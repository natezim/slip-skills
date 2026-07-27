#!/usr/bin/env python3
"""slip.py — deterministic file I/O for the Slip field-report loop.

The judgment (dedupe, fixing, verifying) is Claude's job. This script owns only
the parts that must be exact: discovering pending reports, emitting them as clean
structured JSON, and writing schema-correct resolution receipts. Stdlib only.
See docs/field-report-loop.md for the contract.

Reports are immutable, per-day-organized markdown (schema 2): each note's stable
`id` is embedded as an HTML comment after its heading — no `.json` sidecar. Old
exports (flat `.md` + `.json` sidecars) still parse; the sidecar reader is kept
as a fallback. Nothing is moved or archived: receipts are the state, and the
phone ages reports out by retention.

Subcommands:
  list      Emit JSON of pending reports (reads embedded id comments; falls back
            to the .json sidecar for legacy exports, then to plain markdown).
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
RESERVED_DIRS = {"_results", "images"}
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
# A pending note this similar to an already-closed one is probably the same
# complaint arriving twice — which usually means the first fix didn't take.
REPEAT_SIMILARITY = 0.75
MIN_REPEAT_MATCH_CHARS = 25  # below this, short notes match each other on noise
HEADING_RE = re.compile(r"^##\s+\d+\.\s*(.*)$")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
# The per-note machine tag Slip embeds after each heading (schema 2), e.g.
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
             new_only: bool = False) -> None:
    if not app_dir.exists():
        fail(f"app folder not found: {app_dir}")

    reports = []
    for md in sorted(app_dir.rglob("*.md")):
        if md.name in RESERVED_FILES or is_reserved(md, app_dir) or is_noise(md.name):
            continue
        reports.append(load_report(md, app_dir))

    annotate_with_receipts(reports, app_dir)
    annotate_with_triage(reports, load_triage(triage_path))

    if include_all:
        visible, truncated = reports, False
    else:
        closed = resolved_index(reports, app_dir)
        visible = [pending_view(with_repeat_hints(r, closed))
                   for r in reports if not r["fullyHandled"]]
        if new_only:
            visible = select_notes(visible, lambda n: "triaged" not in n)
        if tags:
            visible = select_notes(visible, tag_matcher(tags))
        visible = by_priority(visible)
        visible, truncated = apply_limit(visible, limit)

    out = {
        "appDir": str(app_dir),
        "filter": "all" if include_all else "pending",
        "reportCount": len(reports),
        "pendingReportCount": sum(1 for r in reports if not r["fullyHandled"]),
        "noteCount": sum(len(r["notes"]) for r in reports),
        # The counts stay global on purpose: a narrowed pull must never make the
        # backlog behind it look smaller than it is.
        "pendingNoteCount": sum(r["pendingCount"] for r in reports),
        "agedNoteCount": aged_note_count(reports),
        "triagedNoteCount": triaged_note_count(reports),
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


def by_priority(reports: list[dict]) -> list[dict]:
    """Reports carrying a note some run already passed over, first; then oldest.

    Both orderings guard against losing work, at different timescales: age says a
    note is stuck and getting quietly re-skipped, and date matters because the phone
    deletes whole day-folders on its retention window — an old note not worked is an
    old note that eventually disappears unresolved. Ordering here rather than only
    under `--limit` keeps a truncated pull a prefix of the full one.
    """
    return sorted(reports,  # default= for a report file that parsed to no notes at all
                  key=lambda r: (-max((n.get("deferredRuns") or 0 for n in r["notes"]), default=0),
                                 r["report"]))


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
    """Prefer embedded id comments in the markdown (schema 2). Fall back to a
    legacy `.json` sidecar if present, then to plain markdown with no ids."""
    rel = str(md.relative_to(app_dir))
    report = report_from_markdown(md, app_dir, rel)
    if report["hasStableIds"]:
        return report  # schema 2 — ids embedded in the markdown

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
        # schema 2 embeds a stable id per note; a legacy export (no id comments,
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
    """Flat, collision-free receipt name from a report's relative path — so
    per-day reports (e.g. `2026-07-17/1731-slug.md`) map to a unique, date-led
    `2026-07-17-1731-slug.result.json` that never clobbers another day's."""
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


def cmd_triage(app_dir: Path, triage_path: Path, report: str, notes_path: str) -> None:
    md = (app_dir / report).resolve()
    if not md.exists():
        fail(f"report not found: {report}")
    data = json.loads(Path(notes_path).read_text())
    entries = data.get("notes", data) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        fail("triage file must be a list, or an object with a 'notes' list")

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

    triage_path.parent.mkdir(parents=True, exist_ok=True)
    triage_path.write_text(
        json.dumps({"schema": SCHEMA_VERSION, "app": APP_NAME, "notes": records},
                   indent=2, ensure_ascii=False) + "\n")
    print(str(triage_path))


def cmd_receipt(app_dir: Path, report: str, results_path: str, agent: str) -> None:
    md = (app_dir / report).resolve()
    if not md.exists():
        fail(f"report not found: {report}")
    results_data = json.loads(Path(results_path).read_text())
    results = results_data.get("results", results_data)  # accept bare list too
    if not isinstance(results, list):
        fail("results file must be a list, or an object with a 'results' list")

    problems = [p for i, r in enumerate(results) for p in result_problems(r, i)]
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


def result_problems(r: dict, index: int) -> list[str]:
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

    required = {
        "needs_info": ("question", "the question the user has to answer"),
        "deferred": ("summary", "who cut it from this round and why — your own sense that it "
                                "was too large is not a reason, it is a routing decision"),
        "duplicate": ("duplicateOf", "the noteId this folds into"),
    }.get(status)
    if required and not str(r.get(required[0]) or "").strip():
        return [f"{where}: status {status!r} needs a non-empty {required[0]} — {required[1]}"]
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

    pt = sub.add_parser("triage", help="Record what a run read, so a re-run needn't re-read it")
    pt.add_argument("--report", required=True, help="Report path relative to app dir")
    pt.add_argument("--notes", required=True, help="JSON file with the triaged notes")

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
                 limit=args.limit, new_only=args.new)
    elif args.cmd == "triage":
        cmd_triage(app_dir, triage_path, args.report, args.notes)
    elif args.cmd == "receipt":
        cmd_receipt(app_dir, args.report, args.results, args.agent)


if __name__ == "__main__":
    main()
