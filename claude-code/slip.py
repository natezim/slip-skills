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

def cmd_list(app_dir: Path, include_all: bool = False) -> None:
    if not app_dir.exists():
        fail(f"app folder not found: {app_dir}")

    reports = []
    for md in sorted(app_dir.rglob("*.md")):
        if md.name in RESERVED_FILES or is_reserved(md, app_dir) or is_noise(md.name):
            continue
        reports.append(load_report(md, app_dir))

    annotate_with_receipts(reports, app_dir)

    # Exact-duplicate hint: notes whose normalized text matches across the batch.
    # A cheap signal for Claude's clustering — semantic dedupe stays Claude's job.
    by_text: dict[str, list[str]] = {}
    for rep in reports:
        for note in rep["notes"]:
            key = normalize(note["text"])
            if key:
                by_text.setdefault(key, []).append(note["id"] or f'{rep["report"]}#{note["index"]}')
    dup_hints = [ids for ids in by_text.values() if len(ids) > 1]

    if include_all:
        visible = reports
    else:
        closed = resolved_index(reports, app_dir)
        visible = [pending_view(with_repeat_hints(r, closed)) for r in reports]

    out = {
        "appDir": str(app_dir),
        "filter": "all" if include_all else "pending",
        "reportCount": len(reports),
        "pendingReportCount": sum(1 for r in reports if not r["fullyHandled"]),
        "noteCount": sum(len(r["notes"]) for r in reports),
        "pendingNoteCount": sum(r["pendingCount"] for r in reports),
        "duplicateHints": dup_hints,
        "reports": visible,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


def pending_view(report: dict) -> dict:
    """Drop work the receipts already closed out, so a re-run doesn't pay context
    for it. A fully handled report collapses to a stub — enough to count and name,
    with no note text or image paths; a partial one keeps only its open notes."""
    if report["fullyHandled"]:
        return {
            "report": report["report"],
            "noteCount": len(report["notes"]),
            "pendingCount": 0,
            "fullyHandled": True,
        }
    open_notes = [n for n in report["notes"] if n["priorStatus"] not in TERMINAL_STATUSES]
    return {**report, "notes": open_notes, "noteCount": len(report["notes"])}


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
    results = {}
    for i, r in enumerate(data.get("results", [])):
        key = r.get("noteId")
        results[key if key is not None else i] = r
    return results


def annotate_with_receipts(reports: list[dict], app_dir: Path) -> None:
    """Tag each note with its last receipt `priorStatus`, and mark reports already
    fully handled — so a re-run skips finished work instead of re-analyzing it."""
    for rep in reports:
        results = receipt_results(rep["report"], app_dir)
        handled = 0
        for i, note in enumerate(rep["notes"]):
            key = note["id"] if note["id"] is not None else i
            note["priorStatus"] = (results.get(key) or {}).get("status")
            if note["priorStatus"] in TERMINAL_STATUSES:
                handled += 1
        rep["pendingCount"] = len(rep["notes"]) - handled
        rep["fullyHandled"] = len(rep["notes"]) > 0 and handled == len(rep["notes"])


def cmd_receipt(app_dir: Path, report: str, results_path: str, agent: str) -> None:
    md = (app_dir / report).resolve()
    if not md.exists():
        fail(f"report not found: {report}")
    results_data = json.loads(Path(results_path).read_text())
    results = results_data.get("results", results_data)  # accept bare list too
    if not isinstance(results, list):
        fail("results file must be a list, or an object with a 'results' list")

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
        "processedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "agent": agent,
        "results": [clean_result(r) for r in results],
    }
    results_dir = app_dir / "_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    dest = results_dir / receipt_filename(report)
    dest.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    print(str(dest))


def clean_result(r: dict) -> dict:
    return {
        "noteId": r.get("noteId"),
        "status": r.get("status"),
        "commit": r.get("commit"),
        "filesTouched": r.get("filesTouched", []),
        "summary": r.get("summary", ""),
        "duplicateOf": r.get("duplicateOf"),
        "question": r.get("question"),
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

    pr = sub.add_parser("receipt", help="Write a resolution receipt")
    pr.add_argument("--report", required=True, help="Report path relative to app dir")
    pr.add_argument("--results", required=True, help="JSON file with the results array")
    pr.add_argument("--agent", default="claude-code:slip", help="Resolver identifier")

    args = p.parse_args()
    app_dir = resolve_app_dir(args.app_dir, args.config)

    if args.cmd == "list":
        cmd_list(app_dir, include_all=args.all)
    elif args.cmd == "receipt":
        cmd_receipt(app_dir, args.report, args.results, args.agent)


if __name__ == "__main__":
    main()
