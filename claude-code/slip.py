#!/usr/bin/env python3
"""slip.py — deterministic file I/O for the Slip field-report loop.

The judgment (dedupe, fixing, verifying) is Claude's job. This script owns only
the parts that must be exact: discovering pending reports, emitting them as clean
structured JSON, and writing schema-correct resolution receipts. Stdlib only.
See docs/field-report-loop.md for the contract.

Reports are immutable markdown (schema 3): flat in `<Project>/`, the date leading
each filename (`<yyyy-MM-dd>-<HHmm>-<slug>.md`), each note's stable `id` embedded
as an HTML comment after its heading — no `.json` sidecar. Older exports still
parse too: schema-2 `<day>/…` day-folders (a `*.md` glob finds them either way),
and legacy flat `.md` + `.json` sidecars (the sidecar reader is kept as a
fallback). Nothing is moved or archived: receipts are the state, and the phone
ages reports out by retention.

Subcommands:
  list      Emit JSON of pending reports (reads embedded id comments; falls back
            to the .json sidecar for legacy exports, then to plain markdown).
  triage    Record what a run read, so a re-run needn't re-read it.
  park      Hold notes under a named theme so they leave the default pull.
  unpark    Release a theme (or single notes) back into the default pull.
  receipt   Write _results/<report>.result.json from a results file.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1  # receipt schema (independent of the export/report schema)
APP_NAME = "Slip"
RESERVED_DIRS = {"_results", "images"}  # "images" only exists in legacy schema-2 day-folders
RESERVED_FILES = {"README.md"}  # the self-describing export README, not a report
DEFAULT_DROP_ROOT = "~/Dropbox/Slip"  # where Slip exports; one subfolder per app
# Receipt statuses that close a note out for good. `deferred`/`needs_info` do not —
# those stay pending until a later run resolves them.
TERMINAL_STATUSES = {"fixed", "wont_fix", "duplicate"}
VALID_STATUSES = TERMINAL_STATUSES | {"deferred", "needs_info"}
# Receipts record what was *resolved*. Triage records what was *read* — the far
# bigger cost on a large batch, since re-reading means re-opening every screenshot
# and re-deriving every cluster. Kept beside slip.json in the repo, never in the
# synced export folder: this is the resolver's own memory, not part of the loop.
TRIAGE_FILENAME = "slip-triage.json"
# A *parked* note is one the user deliberately held for a named future session —
# a design round, an ideas round. It is still open work, and it still counts in
# `pendingNoteCount`; it simply stops arriving in the default pull. That is the
# whole mechanism: a note nobody pulls is a note nobody receipts, and aging is
# driven by receipts, so parking freezes `deferredRuns` instead of ratcheting it.
#
# The problem this solves: `deferred` was doing two jobs — "cut from this round,
# still live" and "waiting on a session that has no date". The second kind came
# back every run, got re-read, got re-deferred with a near-identical summary, and
# aged. The age counter exists to surface notes that are genuinely stuck, and
# counting rounds of a decision already made is precisely what destroys it.
PARK_THEME_RE = re.compile(r"^[\w][\w .-]{0,59}$")
# A pending note this similar to an already-closed one is probably the same
# complaint arriving twice — which usually means the first fix didn't take.
REPEAT_SIMILARITY = 0.75
MIN_REPEAT_MATCH_CHARS = 25  # below this, short notes match each other on noise
HEADING_RE = re.compile(r"^##\s+\d+\.\s*(.*)$")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
# The per-note machine tag Slip embeds after each heading (schema 2+), e.g.
#   <!-- id: 2AB1F73C-EE11-4273-8653-F2C3C322C6BD captured: 2026-07-17T17:30:00Z -->
ID_COMMENT_RE = re.compile(
    r"<!--\s*id:\s*([0-9A-Fa-f-]{36})(?:\s+captured:\s*(\S+))?\s*-->"
)


# ---------------------------------------------------------------------------
# Config / paths
# ---------------------------------------------------------------------------

def resolve_app_dir(explicit: str | None, config_path: str) -> Path:
    """The <dropRoot>/<app> folder to operate on.

    Resolution order: an explicit --app-dir wins; then `.claude/slip.json` (an
    override for when the repo name doesn't match the export folder); otherwise
    zero-config auto-detect — look for a drop-root subfolder named after the
    current project directory. So a repo named after its app "just works" with no
    setup, and only mismatched names need a config.
    """
    if explicit:
        return Path(os.path.expanduser(explicit)).resolve()

    # Config is optional now — it overrides auto-detect.
    drop_root = DEFAULT_DROP_ROOT
    app = None
    cfg = Path(config_path)
    if cfg.exists():
        data = json.loads(cfg.read_text())
        drop_root = data.get("dropRoot") or DEFAULT_DROP_ROOT
        app = (data.get("app") or "").strip() or None

    root = Path(os.path.expanduser(drop_root))
    if app:
        return (root / app).resolve()

    # Auto-detect: a drop folder named after the project we're working in?
    cwd_name = Path.cwd().name
    match = match_app_folder(root, cwd_name)
    if match:
        return match.resolve()

    available = app_folders(root)
    hint = f" Available: {', '.join(available)}." if available else ""
    fail(
        f"Couldn't tell which Slip app folder to use for '{cwd_name}'. No folder in "
        f"{root} matches it, and no 'app' set in {config_path}.{hint} "
        "Add .claude/slip.json with an \"app\", or pass --app-dir."
    )


def app_folders(root: Path) -> list[str]:
    """User-facing app subfolders under the drop root (skips reserved/hidden)."""
    if not root.exists():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and not p.name.startswith((".", "_"))
    )


def match_app_folder(root: Path, name: str) -> Path | None:
    """Case-insensitive exact match of `name` against a drop-root subfolder."""
    if not root.exists():
        return None
    for p in sorted(root.iterdir()):
        if p.is_dir() and p.name.lower() == name.lower() and not p.name.startswith((".", "_")):
            return p
    return None


def fail(msg: str) -> "None":
    print(f"slip.py: {msg}", file=sys.stderr)
    sys.exit(1)


def is_reserved(path: Path, app_dir: Path) -> bool:
    rel_parts = path.relative_to(app_dir).parts
    return any(part in RESERVED_DIRS for part in rel_parts[:-1])


def is_noise(name: str) -> bool:
    """Non-report markdown to skip: README variants (including Dropbox's
    "README (… conflicted copy).md" duplicates) and other cloud sync artifacts —
    which otherwise leak in as bogus, id-less "reports"."""
    low = name.lower()
    return (
        low.startswith("readme")
        or "conflicted copy" in low        # Dropbox
        or ".sync-conflict" in low         # Syncthing
    )


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def cmd_list(app_dir: Path, triage_path: Path, include_all: bool = False,
             tags: list[str] | None = None, limit: int | None = None,
             new_only: bool = False, theme: str | None = None) -> None:
    if not app_dir.exists():
        fail(f"app folder not found: {app_dir}")

    reports = []
    for md in sorted(app_dir.rglob("*.md")):
        if md.name in RESERVED_FILES or is_reserved(md, app_dir) or is_noise(md.name):
            continue
        reports.append(load_report(md, app_dir))

    annotate_with_receipts(reports, app_dir)
    annotate_with_triage(reports, load_triage(triage_path))
    annotate_with_parked(reports, load_parked(triage_path))

    if include_all:
        visible, truncated = reports, False
    else:
        closed = resolved_index(reports, app_dir)
        visible = [pending_view(with_repeat_hints(r, closed))
                   for r in reports if not r["fullyHandled"]]
        # `--theme` opens one park; the default pull closes all of them. Either
        # way the note is selected on its park, never on the absence of a receipt.
        if theme:
            visible = select_notes(visible, theme_matcher(theme))
        else:
            visible = select_notes(visible, lambda n: "parked" not in n)
        if new_only:
            visible = select_notes(visible, lambda n: "triaged" not in n)
        if tags:
            visible = select_notes(visible, tag_matcher(tags))
        visible = by_priority(visible)
        visible, truncated = apply_limit(visible, limit)

    out = {
        "appDir": str(app_dir),
        "filter": f"theme:{theme}" if theme else ("all" if include_all else "pending"),
        "reportCount": len(reports),
        "pendingReportCount": sum(1 for r in reports if not r["fullyHandled"]),
        "noteCount": sum(len(r["notes"]) for r in reports),
        # The counts stay global on purpose: a narrowed pull must never make the
        # backlog behind it look smaller than it is. Parked notes are pending —
        # they are held, not resolved — so they stay inside `pendingNoteCount`
        # and only drop out of `returnedNoteCount`.
        "pendingNoteCount": sum(r["pendingCount"] for r in reports),
        "agedNoteCount": aged_note_count(reports),
        "triagedNoteCount": triaged_note_count(reports),
        "parkedNoteCount": parked_note_count(reports),
        "parkedThemes": parked_themes(reports),
        "returnedNoteCount": sum(len(r["notes"]) for r in visible),
        "truncated": truncated,
        "duplicateHints": duplicate_hints(visible),
        "reports": visible,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


def select_notes(reports: list[dict], keep) -> list[dict]:
    """Reports narrowed to the notes matching `keep`, dropping any left with none."""
    out = []
    for rep in reports:
        notes = [n for n in rep["notes"] if keep(n)]
        if notes:
            out.append({**rep, "notes": notes})
    return out


def tag_matcher(tags: list[str]):
    """Case-insensitive tag test. `untagged` selects the notes carrying no tag at
    all — Slip only seeds `bug`/`idea`, so without it a tag-narrowed pass would
    silently skip everything dictated without one."""
    wanted = {t.strip().lower() for t in tags if t.strip()}
    def keep(note: dict) -> bool:
        have = {str(t).lower() for t in note.get("tags") or []}
        return bool(wanted & have) or ("untagged" in wanted and not have)
    return keep


def theme_matcher(theme: str):
    """Case-insensitive match on a note's park theme."""
    wanted = theme.strip().lower()
    return lambda note: str((note.get("parked") or {}).get("theme", "")).lower() == wanted


def by_priority(reports: list[dict]) -> list[dict]:
    """Reports carrying a note some run already passed over, first; then oldest.

    Both orderings guard against losing work, at different timescales: age says a
    note is stuck and getting quietly re-skipped, and date matters because the phone
    deletes old reports on its retention window — an old note not worked is an old
    note that eventually disappears unresolved. Ordering here rather than only under
    `--limit` keeps a truncated pull a prefix of the full one.

    "Oldest" means oldest *capture*, not oldest export: a note can sit on the phone
    for days before being sent, so a newer report can hold the oldest open thought
    in the whole batch.
    """
    def earliest_capture(rep: dict) -> str:
        stamps = [n["capturedAt"] for n in rep["notes"] if n.get("capturedAt")]
        # Legacy notes carry no timestamp; fall back to the report's leading date
        # (`report[:10]` — the same `yyyy-MM-dd` whether the report is flat schema-3
        # or a schema-2 `<day>/…` path), pinned to midnight so it sorts with (and
        # ahead of) that day's captures.
        return min(stamps) if stamps else rep["report"][:10] + "T00:00:00Z"

    return sorted(reports,  # default= for a report file that parsed to no notes at all
                  key=lambda r: (-max((n.get("deferredRuns") or 0 for n in r["notes"]), default=0),
                                 earliest_capture(r), r["report"]))


def apply_limit(reports: list[dict], limit: int | None) -> tuple[list[dict], bool]:
    """The first `limit` open notes, oldest report first — a working set small
    enough to hold at once. What's left over is simply not returned: those notes
    are still pending and still untriaged, and a note nobody looked at gets no
    receipt and no age. Truncating is not deferring."""
    if not limit or limit <= 0:
        return reports, False
    if sum(len(r["notes"]) for r in reports) <= limit:
        return reports, False
    out, taken = [], 0
    for rep in reports:
        if taken >= limit:
            break
        notes = rep["notes"][:limit - taken]
        taken += len(notes)
        out.append({**rep, "notes": notes})
    return out, True


def pending_view(report: dict) -> dict:
    """Drop the notes the receipts already closed out, so a re-run doesn't pay
    context for them. Fully handled reports don't reach here at all — they're
    filtered out whole, and the counts alone carry that they existed."""
    open_notes = [n for n in report["notes"] if n["priorStatus"] not in TERMINAL_STATUSES]
    return {**report, "notes": open_notes, "noteCount": len(report["notes"])}


def aged_note_count(reports: list[dict]) -> int:
    """Open notes a past run already left open at least once. Surfaced on its own
    so a re-run can't miss that some of this batch is not, in fact, new."""
    return sum(1 for rep in reports for note in rep["notes"]
               if note["priorStatus"] not in TERMINAL_STATUSES and note.get("deferredRuns"))


def triaged_note_count(reports: list[dict]) -> int:
    """Open notes a past run already read and summarized — the ones a re-run can
    take from their gist instead of reopening their screenshots."""
    return sum(1 for rep in reports for note in rep["notes"]
               if note["priorStatus"] not in TERMINAL_STATUSES and note.get("triaged"))


def open_notes(reports: list[dict]):
    """Every still-open note across the reports, ignoring the ones receipts closed."""
    return (note for rep in reports for note in rep["notes"]
            if note["priorStatus"] not in TERMINAL_STATUSES)


def parked_note_count(reports: list[dict]) -> int:
    """Open notes currently held under a theme. Reported on every pull, including
    the pulls that hide them — a held note that stops being counted is a lost one."""
    return sum(1 for note in open_notes(reports) if note.get("parked"))


def parked_themes(reports: list[dict]) -> dict:
    """theme -> how many open notes are held under it, so a run can say what is
    waiting and on what, rather than reporting a bare total nobody can act on."""
    counts: dict[str, int] = {}
    for note in open_notes(reports):
        theme = (note.get("parked") or {}).get("theme")
        if theme:
            counts[theme] = counts.get(theme, 0) + 1
    return dict(sorted(counts.items()))


def duplicate_hints(reports: list[dict]) -> list[list[str]]:
    """Notes in the visible batch whose normalized text matches. A cheap signal for
    Claude's clustering — semantic dedupe stays Claude's job. One note re-exported
    into two files carries the same id twice and is not a duplicate of itself."""
    by_text: dict[str, list[str]] = {}
    for rep in reports:
        for note in rep["notes"]:
            key = normalize(note["text"])
            if not key:
                continue
            ident = note["id"] or f'{rep["report"]}#{note["index"]}'
            group = by_text.setdefault(key, [])
            if ident not in group:
                group.append(ident)
    return [ids for ids in by_text.values() if len(ids) > 1]


def resolved_index(reports: list[dict], app_dir: Path) -> list[dict]:
    """Every already-closed note, with the outcome its receipt recorded. Built from
    the unfiltered reports so it still sees what `pending_view` is about to drop."""
    index = []
    for rep in reports:
        results = receipt_results(rep["report"], app_dir)
        for i, note in enumerate(rep["notes"]):
            if note["priorStatus"] not in TERMINAL_STATUSES:
                continue
            record = results.get(note["id"] if note["id"] is not None else i) or {}
            index.append({
                "noteId": note["id"],
                "report": rep["report"],
                "status": note["priorStatus"],
                "summary": record.get("summary", ""),
                "commit": record.get("commit"),
                "normalized": normalize(note["text"]),
            })
    return index


def repeat_hint(text: str, index: list[dict]) -> dict | None:
    """The closest already-closed note that this pending one looks like a re-report
    of, or None. Cheap ratios prefilter before the real (quadratic) comparison."""
    normalized = normalize(text)
    if len(normalized) < MIN_REPEAT_MATCH_CHARS:
        return None  # too short to match on anything but noise
    best, best_score = None, 0.0
    for candidate in index:
        other = candidate["normalized"]
        if len(other) < MIN_REPEAT_MATCH_CHARS:
            continue
        matcher = difflib.SequenceMatcher(None, normalized, other)
        if (matcher.real_quick_ratio() < REPEAT_SIMILARITY
                or matcher.quick_ratio() < REPEAT_SIMILARITY):
            continue
        score = matcher.ratio()
        if score >= REPEAT_SIMILARITY and score > best_score:
            best, best_score = candidate, score
    if best is None:
        return None
    return {
        "noteId": best["noteId"],
        "report": best["report"],
        "status": best["status"],
        "summary": best["summary"],
        "commit": best["commit"],
        "similarity": round(best_score, 2),
    }


def with_repeat_hints(report: dict, index: list[dict]) -> dict:
    """Flag pending notes that echo something already closed out. The hint is a
    prompt to check whether that earlier fix actually held — never a licence to
    close the new note on the strength of the old receipt."""
    if report["fullyHandled"]:
        return report
    notes = []
    for note in report["notes"]:
        hint = None
        if note["priorStatus"] not in TERMINAL_STATUSES:
            hint = repeat_hint(note["text"], index)
        notes.append({**note, "possibleRepeatOf": hint} if hint else note)
    return {**report, "notes": notes}


def load_report(md: Path, app_dir: Path) -> dict:
    """Prefer embedded id comments in the markdown (schema 2+). Fall back to a
    legacy `.json` sidecar if present, then to plain markdown with no ids."""
    rel = str(md.relative_to(app_dir))
    report = report_from_markdown(md, app_dir, rel)
    if report["hasStableIds"]:
        return report  # schema 2+ — ids embedded in the markdown

    sidecar = md.with_suffix(".json")
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text())
            return report_from_sidecar(data, md, sidecar, app_dir, rel)
        except (json.JSONDecodeError, KeyError):
            pass  # fall through to the (id-less) markdown parse
    return report


def report_from_sidecar(data: dict, md: Path, sidecar: Path, app_dir: Path, rel: str) -> dict:
    notes = []
    for i, n in enumerate(data.get("notes", [])):
        notes.append({
            "index": i,
            "id": n.get("id"),
            "capturedAt": n.get("capturedAt"),
            "tags": n.get("tags", []),
            "text": n.get("text", ""),
            "images": [str((md.parent / img).resolve()) for img in n.get("images", [])],
        })
    return {
        "report": rel,
        "sidecar": str(sidecar.relative_to(app_dir)),
        "schema": data.get("schema"),
        "hasStableIds": True,
        "project": data.get("project"),
        "exportedAt": data.get("exportedAt"),
        "device": data.get("device"),
        "notes": notes,
    }


def report_from_markdown(md: Path, app_dir: Path, rel: str) -> dict:
    text = md.read_text(encoding="utf-8", errors="replace")
    front, body = split_frontmatter(text)
    notes = parse_markdown_notes(body, md)
    has_ids = any(n["id"] for n in notes)
    schema = front.get("schema")
    try:
        schema = int(schema) if schema is not None else None
    except (TypeError, ValueError):
        pass
    return {
        "report": rel,
        "sidecar": None,
        "schema": schema,
        # schema 2+ embeds a stable id per note; a legacy export (no id comments,
        # no sidecar) can't be matched back by the phone.
        "hasStableIds": has_ids,
        "project": front.get("project"),
        "exportedAt": front.get("exported") or front.get("captured"),
        "device": front.get("device"),
        "notes": notes,
    }


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end].strip()
    body = text[end + 4:]
    front: dict = {}
    for line in block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            front[k.strip()] = v.strip()
    return front, body


def parse_markdown_notes(body: str, md: Path) -> list[dict]:
    lines = body.splitlines()
    heading_idx = [i for i, ln in enumerate(lines) if HEADING_RE.match(ln)]
    notes: list[dict] = []
    if heading_idx:  # batch field report
        for n, start in enumerate(heading_idx):
            end = heading_idx[n + 1] if n + 1 < len(heading_idx) else len(lines)
            heading = HEADING_RE.match(lines[start]).group(1)
            chunk = "\n".join(lines[start + 1:end])
            notes.append(markdown_note(n, chunk, md, tag_hint=heading))
    else:  # single-note file — whole body is one note
        notes.append(markdown_note(0, body, md, tag_hint=""))
    return notes


def markdown_note(index: int, chunk: str, md: Path, tag_hint: str) -> dict:
    # Pull the stable id (and capture time) out of the embedded comment, then
    # strip the comment so it never leaks into the note text.
    note_id, captured = parse_id_comment(chunk)
    chunk = ID_COMMENT_RE.sub("", chunk)
    images = [str((md.parent / m).resolve()) for m in IMAGE_RE.findall(chunk)]
    text = IMAGE_RE.sub("", chunk).strip()
    if text == "_(no note)_":
        text = ""
    # A heading like "11:35 AM · bug" carries tags after the "·".
    tags = []
    if "·" in tag_hint:
        tags = [t.strip() for t in tag_hint.split("·", 1)[1].split(",") if t.strip()]
    return {"index": index, "id": note_id, "capturedAt": captured, "tags": tags,
            "text": text, "images": images}


def parse_id_comment(chunk: str) -> tuple[str | None, str | None]:
    """(noteId, capturedAt) from the first `<!-- id: … captured: … -->` in a
    chunk, or (None, None) for a legacy report without one."""
    m = ID_COMMENT_RE.search(chunk)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


# ---------------------------------------------------------------------------
# receipt
# ---------------------------------------------------------------------------

def receipt_filename(report: str) -> str:
    """Flat, collision-free receipt name from a report's relative path. A flat
    schema-3 report (`2026-07-17-1731-slug.md`) maps to
    `2026-07-17-1731-slug.result.json`; a legacy schema-2 path
    (`2026-07-17/1731-slug.md`) folds the slash to the same name — so both land on a
    unique, date-led receipt that never clobbers another day's."""
    stem = report[:-3] if report.endswith(".md") else report
    return stem.replace("/", "-").replace("\\", "-") + ".result.json"


def receipt_results(report_rel: str, app_dir: Path) -> dict:
    """noteId (or note index for legacy reports) -> that note's last receipt record,
    from this report's receipt if one exists."""
    receipt = app_dir / "_results" / receipt_filename(report_rel)
    if not receipt.exists():
        return {}
    try:
        data = json.loads(receipt.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return {result_key(r, i): r for i, r in enumerate(data.get("results", []))}


def result_key(record: dict, index: int) -> str | int:
    """How a result joins back to its note: the stable id, or — for a legacy report
    that never had one — its position. Positional joins only line up when the whole
    report is receipted at once; id-joined results merge a note at a time."""
    key = record.get("noteId")
    return key if key is not None else index


def prior_aging(record: dict) -> tuple[int, str | None]:
    """The `(runs, since)` a past receipt recorded for a note, defensively parsed —
    any tool that speaks the format may have written it, including by hand."""
    runs, since = record.get("deferredRuns"), record.get("deferredSince")
    return (runs if isinstance(runs, int) and runs > 0 else 0,
            since if isinstance(since, str) and since else None)


def annotate_with_receipts(reports: list[dict], app_dir: Path) -> None:
    """Tag each note with its last receipt `priorStatus` and how long it has been
    left open, and mark reports already fully handled — so a re-run skips finished
    work instead of re-analyzing it."""
    for rep in reports:
        results = receipt_results(rep["report"], app_dir)
        handled = 0
        for i, note in enumerate(rep["notes"]):
            key = note["id"] if note["id"] is not None else i
            record = results.get(key) or {}
            note["priorStatus"] = record.get("status")
            runs, since = prior_aging(record)
            if runs:  # only on the notes that have one — a `0` on every note is dead context
                note["deferredRuns"], note["deferredSince"] = runs, since
            if note["priorStatus"] in TERMINAL_STATUSES:
                handled += 1
        rep["pendingCount"] = len(rep["notes"]) - handled
        rep["fullyHandled"] = len(rep["notes"]) > 0 and handled == len(rep["notes"])


# ---------------------------------------------------------------------------
# Triage memory (repo-local; never written into the synced export folder)
# ---------------------------------------------------------------------------

def load_triage(path: Path) -> dict:
    """What past runs recorded about notes they read, keyed `<report>#<id|index>`.
    Missing or unreadable is not an error — it just means nothing has been read
    yet, and the batch gets triaged from scratch."""
    if not path.exists():
        return {}
    try:
        notes = json.loads(path.read_text()).get("notes")
    except (json.JSONDecodeError, OSError, AttributeError):
        return {}
    return notes if isinstance(notes, dict) else {}


def triage_key(report_rel: str, note_id: str | None, index: int) -> str:
    """Scoped by report so legacy notes, which have only a position, stay distinct."""
    return f"{report_rel}#{note_id if note_id is not None else index}"


def annotate_with_triage(reports: list[dict], triage: dict) -> None:
    """Hang each note's past triage record on it, so `list` can say 'already read,
    here's the gist' instead of handing back a note to be worked out again.

    A gist is trusted in place of the note, which makes a wrong one stickier than no
    gist at all — so the one case that can't be trusted is marked: a note that has a
    screenshot summarized by a run that never opened it. `gistProvisional` is the
    invalidation path; without it, a guess made once is believed forever.
    """
    for rep in reports:
        for i, note in enumerate(rep["notes"]):
            record = triage.get(triage_key(rep["report"], note["id"], i))
            if record:
                note["triaged"] = {
                    **record,
                    "gistProvisional": bool(note["images"]) and not record.get("sawScreenshot"),
                }


def load_parked(path: Path) -> dict:
    """Which notes are held, keyed the same way triage is. Kept in its own block
    rather than as a field on the triage record: parking and reading are separate
    facts, and a note can be parked before anyone has read it."""
    if not path.exists():
        return {}
    try:
        parked = json.loads(path.read_text()).get("parked")
    except (json.JSONDecodeError, OSError, AttributeError):
        return {}
    return parked if isinstance(parked, dict) else {}


def write_memory(path: Path, triage: dict, parked: dict) -> None:
    """The resolver's own memory file, rewritten whole. Both blocks are always
    written, so parking never silently drops what triage had recorded."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"schema": SCHEMA_VERSION, "app": APP_NAME, "notes": triage, "parked": parked},
        indent=2, ensure_ascii=False) + "\n")


def annotate_with_parked(reports: list[dict], parked: dict) -> None:
    """Hang each note's park on it, so `list` can hold it back from the default
    pull and still account for it in the counts."""
    for rep in reports:
        for i, note in enumerate(rep["notes"]):
            record = parked.get(triage_key(rep["report"], note["id"], i))
            if record:
                note["parked"] = record


def report_note_ids(app_dir: Path, report: str) -> set:
    """The stable ids a report actually contains — empty for a legacy export that
    has none. Joined unresolved on purpose: `load_report` takes the path apart
    with `relative_to(app_dir)`, and resolving one side and not the other breaks
    that wherever the temp or home dir is itself a symlink (macOS `/var`)."""
    return {n["id"] for n in load_report(app_dir / report, app_dir)["notes"] if n["id"]}


def note_refs(entries, app_dir: Path, default_report: str | None) -> list[tuple[str, str]]:
    """`(report, key)` pairs from a park/unpark file, validated against the reports
    on disk. A park aimed at a note that doesn't exist would hold nothing while
    reading as though it had — the same silent no-op a stray receipt noteId is."""
    refs = []
    for i, e in enumerate(entries):
        report = str(e.get("report") or default_report or "").strip()
        if not report:
            fail(f"entry {i}: no report — give one per entry or pass --report")
        md = (app_dir / report).resolve()
        if not md.exists():
            fail(f"entry {i}: report not found: {report}")
        note_id = e.get("noteId")
        known = report_note_ids(app_dir, report)
        if note_id is not None and known and note_id not in known:
            fail(f"entry {i}: note {note_id} is not in {report}")
        refs.append((report, triage_key(report, note_id, e.get("index", 0))))
    return refs


def read_entries(notes_path: str) -> list:
    data = json.loads(Path(notes_path).read_text())
    entries = data.get("notes", data) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        fail("file must be a list, or an object with a 'notes' list")
    return entries


def cmd_park(app_dir: Path, triage_path: Path, theme: str, notes_path: str,
             report: str | None) -> None:
    """Hold notes under a named theme. The theme is required and has to be a real
    name: 'later' and 'backlog' are where work goes to die, and the point of the
    park is that the user can ask for it back by name."""
    theme = theme.strip()
    if not PARK_THEME_RE.match(theme):
        fail(f"bad theme {theme!r} — 1–60 chars, letters/digits/space/._- only")

    refs = note_refs(read_entries(notes_path), app_dir, report)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    parked = dict(load_parked(triage_path))
    for _, key in refs:
        parked[key] = {"theme": theme, "parkedAt": now}
    write_memory(triage_path, load_triage(triage_path), parked)
    print(f"{triage_path}: {len(refs)} note(s) parked under {theme!r}")


def cmd_unpark(app_dir: Path, triage_path: Path, theme: str | None,
               notes_path: str | None, report: str | None) -> None:
    """Release a whole theme, or named notes, back into the default pull. This is
    how an ideas round actually starts: the work comes back as ordinary open
    work, and from here on it ages again like anything else."""
    parked = dict(load_parked(triage_path))
    if notes_path:
        keys = {key for _, key in note_refs(read_entries(notes_path), app_dir, report)}
    elif theme:
        wanted = theme.strip().lower()
        keys = {k for k, v in parked.items()
                if str(v.get("theme", "")).lower() == wanted}
    else:
        fail("give --theme or --notes")
    if not keys:
        fail("nothing matched — no notes released")
    for key in keys:
        parked.pop(key, None)
    write_memory(triage_path, load_triage(triage_path), parked)
    print(f"{triage_path}: {len(keys)} note(s) released")


def cmd_triage(app_dir: Path, triage_path: Path, report: str, notes_path: str) -> None:
    md = (app_dir / report).resolve()
    if not md.exists():
        fail(f"report not found: {report}")
    entries = read_entries(notes_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = dict(load_triage(triage_path))
    for i, e in enumerate(entries):
        gist = str(e.get("gist") or "").strip()
        if not gist:
            fail(f"triage entry {i} has no gist — a note recorded as read with "
                 "nothing recorded about it is worse than one left unread")
        records[triage_key(report, e.get("noteId"), i)] = {
            "gist": gist,
            "cluster": e.get("cluster"),
            "kind": e.get("kind"),
            "sawScreenshot": bool(e.get("sawScreenshot")),
            "triagedAt": now,
        }

    write_memory(triage_path, records, load_parked(triage_path))
    print(str(triage_path))


def cmd_receipt(app_dir: Path, report: str, results_path: str, agent: str) -> None:
    md = (app_dir / report).resolve()
    if not md.exists():
        fail(f"report not found: {report}")
    results_data = json.loads(Path(results_path).read_text())
    results = results_data.get("results", results_data)  # accept bare list too
    if not isinstance(results, list):
        fail("results file must be a list, or an object with a 'results' list")

    known = report_note_ids(app_dir, report)
    problems = [p for i, r in enumerate(results) for p in result_problems(r, i, known)]
    if problems:
        fail("bad results file:\n  - " + "\n  - ".join(problems))

    # Aging is the script's to keep, not the caller's: a note left open again comes
    # back older, so nothing can be quietly re-deferred run after run.
    prior = receipt_results(report, app_dir)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    project = results_data.get("project") if isinstance(results_data, dict) else None
    if not project:
        # Prefer the report's own frontmatter; fall back to a legacy sidecar.
        front, _ = split_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
        project = front.get("project")
        if not project:
            sidecar = md.with_suffix(".json")
            if sidecar.exists():
                project = json.loads(sidecar.read_text()).get("project")

    receipt = {
        "schema": SCHEMA_VERSION,
        "app": APP_NAME,
        "project": project,
        "sourceReport": report,
        "processedAt": now,
        "agent": agent,
        "results": merge_results(prior, results, now),
    }
    results_dir = app_dir / "_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    dest = results_dir / receipt_filename(report)
    dest.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    print(str(dest))


def result_problems(r: dict, index: int, known_ids: set) -> list[str]:
    """Everything wrong with one result, so a bad file reports in full instead of
    one error per re-run.

    The rule behind the checks: the statuses that take a note off the list have to
    cost something to write. `fixed` already pays — it means a verified change. The
    cheap ones were the dangerous ones, because a status with no required field is
    the path of least resistance for a run that has run out of room: `deferred` with
    nothing said, or a terminal `duplicate` pointing nowhere, both close a note into
    silence. Now each has to name what it is.
    """
    where = f"result {index}" + (f" ({r.get('noteId')})" if r.get("noteId") else "")
    status = r.get("status")
    if status not in VALID_STATUSES:
        return [f"{where}: unknown status {status!r} — use one of {', '.join(sorted(VALID_STATUSES))}"]

    # A noteId that isn't in this report writes an outcome nowhere anything reads:
    # `annotate_with_receipts` joins on the report's own ids, so the record is inert
    # and the note it was meant for stays pending forever. It fails silently and it
    # has happened for real — hence a hard refusal rather than a warning. Legacy
    # reports carry no ids at all (`known_ids` empty) and still join positionally.
    note_id = r.get("noteId")
    if known_ids and note_id is not None and note_id not in known_ids:
        return [f"{where}: noteId is not in this report — receipt it against the "
                "report that actually contains it, or the outcome is written where "
                "nothing will read it"]

    required = {
        "needs_info": ("question", "the question the user has to answer"),
        "deferred": ("summary", "who cut it from this round and why — your own sense that it "
                                "was too large is not a reason, it is a routing decision"),
        "duplicate": ("duplicateOf", "the noteId this folds into"),
    }.get(status)
    if required and not str(r.get(required[0]) or "").strip():
        return [f"{where}: status {status!r} needs a non-empty {required[0]} — {required[1]}"]

    # The phone renders `commit` verbatim next to `summary`, so a sha with prose
    # appended shows as duplicated caption text on every resolved row.
    commit = r.get("commit")
    if commit is not None and not re.fullmatch(r"[0-9a-f]{7,40}", str(commit).strip()):
        return [f"{where}: commit must be a bare sha (got {str(commit)[:40]!r}) — "
                "what the commit did belongs in summary"]
    return []


def merge_results(prior: dict, results: list, now: str) -> list[dict]:
    """This run's outcomes laid over the report's last receipt, not swapped for it.

    A run only receipts the notes it worked, and the notes it didn't are usually
    the ones an earlier run already closed — overwriting would reopen them and
    hand the whole report back as unfinished. Untouched records pass through
    verbatim, in place; genuinely new ones append.
    """
    incoming = {}
    for i, r in enumerate(results):
        key = result_key(r, i)
        incoming[key] = clean_result(r, prior.get(key) or {}, now)
    return list({**prior, **incoming}.values())


def clean_result(r: dict, prior: dict, now: str) -> dict:
    """One result in wire shape, with the note's age carried forward from its last
    receipt: `deferredRuns` counts the runs that ended without closing it, and
    `deferredSince` is when that started. Closing the note resets both."""
    still_open = r.get("status") not in TERMINAL_STATUSES
    runs, since = prior_aging(prior)
    return {
        "noteId": r.get("noteId"),
        "status": r.get("status"),
        "commit": r.get("commit"),
        "filesTouched": r.get("filesTouched", []),
        "summary": r.get("summary", ""),
        "duplicateOf": r.get("duplicateOf"),
        "question": r.get("question"),
        "deferredRuns": runs + 1 if still_open else 0,
        "deferredSince": (since or now) if still_open else None,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Slip field-report loop file I/O")
    p.add_argument("--app-dir", help="Override the report folder (else read config)")
    p.add_argument("--config", default=".claude/slip.json", help="Path to slip.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="Emit pending reports as JSON")
    pl.add_argument("--all", action="store_true",
                    help="Include reports and notes the receipts already closed out")
    pl.add_argument("--new", action="store_true",
                    help="Only notes no past run has triaged")
    pl.add_argument("--tag", action="append", metavar="TAG",
                    help="Only notes with this tag; repeatable. `untagged` selects those with none")
    pl.add_argument("--limit", type=int, metavar="N",
                    help="Cap the notes returned, oldest report first")
    pl.add_argument("--theme", metavar="NAME",
                    help="Open a parked theme: only the notes held under it")

    pt = sub.add_parser("triage", help="Record what a run read, so a re-run needn't re-read it")
    pt.add_argument("--report", required=True, help="Report path relative to app dir")
    pt.add_argument("--notes", required=True, help="JSON file with the triaged notes")

    pp = sub.add_parser("park", help="Hold notes under a named theme, out of the default pull")
    pp.add_argument("--theme", required=True, metavar="NAME", help="What they're waiting on")
    pp.add_argument("--notes", required=True, help="JSON file of {report, noteId} entries")
    pp.add_argument("--report", help="Default report for entries that omit one")

    pu = sub.add_parser("unpark", help="Release a theme (or named notes) back into the pull")
    pu.add_argument("--theme", metavar="NAME", help="Release everything held under it")
    pu.add_argument("--notes", help="JSON file of {report, noteId} entries to release")
    pu.add_argument("--report", help="Default report for entries that omit one")

    pr = sub.add_parser("receipt", help="Write a resolution receipt")
    pr.add_argument("--report", required=True, help="Report path relative to app dir")
    pr.add_argument("--results", required=True, help="JSON file with the results array")
    pr.add_argument("--agent", default="claude-code:slip", help="Resolver identifier")

    args = p.parse_args()
    app_dir = resolve_app_dir(args.app_dir, args.config)
    # Triage memory sits beside slip.json, so it follows --config into the same repo.
    triage_path = Path(args.config).expanduser().parent / TRIAGE_FILENAME

    if args.cmd == "list":
        cmd_list(app_dir, triage_path, include_all=args.all, tags=args.tag,
                 limit=args.limit, new_only=args.new, theme=args.theme)
    elif args.cmd == "triage":
        cmd_triage(app_dir, triage_path, args.report, args.notes)
    elif args.cmd == "park":
        cmd_park(app_dir, triage_path, args.theme, args.notes, args.report)
    elif args.cmd == "unpark":
        cmd_unpark(app_dir, triage_path, args.theme, args.notes, args.report)
    elif args.cmd == "receipt":
        cmd_receipt(app_dir, args.report, args.results, args.agent)


if __name__ == "__main__":
    main()
