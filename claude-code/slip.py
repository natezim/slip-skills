#!/usr/bin/env python3
"""slip.py — deterministic file I/O for the Slip field-report loop.

The judgment (dedupe, fixing, verifying) is Claude's job. This script owns only
the parts that must be exact: discovering pending reports, emitting them as clean
structured JSON, writing schema-correct resolution receipts, and archiving
resolved sources. Stdlib only. See docs/field-report-loop.md for the contract.

Subcommands:
  list      Emit JSON of pending reports (prefers the .json sidecar; parses .md
            for legacy exports that predate sidecars).
  receipt   Write _results/<report>.result.json from a results file.
  archive   Move a resolved report (.md + .json + referenced images) into
            _archive/<YYYY-MM-DD>/, preserving structure. Never deletes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
APP_NAME = "Slip"
RESERVED_DIRS = {"_archive", "_results", "images"}
RESERVED_FILES = {"README.md"}  # the self-describing export README, not a report
DEFAULT_DROP_ROOT = "~/Dropbox/Slip"  # where Slip exports; one subfolder per app
TIMESTAMP_RE = re.compile(r"(\d{4}-\d{2}-\d{2})-\d{4}")
HEADING_RE = re.compile(r"^##\s+\d+\.\s*(.*)$")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


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


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def cmd_list(app_dir: Path) -> None:
    if not app_dir.exists():
        fail(f"app folder not found: {app_dir}")

    reports = []
    for md in sorted(app_dir.rglob("*.md")):
        if md.name in RESERVED_FILES or is_reserved(md, app_dir):
            continue
        reports.append(load_report(md, app_dir))

    # Exact-duplicate hint: notes whose normalized text matches across the batch.
    # A cheap signal for Claude's clustering — semantic dedupe stays Claude's job.
    by_text: dict[str, list[str]] = {}
    for rep in reports:
        for note in rep["notes"]:
            key = normalize(note["text"])
            if key:
                by_text.setdefault(key, []).append(note["id"] or f'{rep["report"]}#{note["index"]}')
    dup_hints = [ids for ids in by_text.values() if len(ids) > 1]

    out = {
        "appDir": str(app_dir),
        "reportCount": len(reports),
        "noteCount": sum(len(r["notes"]) for r in reports),
        "duplicateHints": dup_hints,
        "reports": reports,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


def load_report(md: Path, app_dir: Path) -> dict:
    sidecar = md.with_suffix(".json")
    rel = str(md.relative_to(app_dir))
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text())
            return report_from_sidecar(data, md, sidecar, app_dir, rel)
        except (json.JSONDecodeError, KeyError):
            pass  # fall through to markdown parse
    return report_from_markdown(md, app_dir, rel)


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
    return {
        "report": rel,
        "sidecar": None,
        "schema": None,
        "hasStableIds": False,  # legacy export — phone can't match these by id
        "project": front.get("project"),
        "exportedAt": front.get("exported") or front.get("captured"),
        "device": None,
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
    images = [str((md.parent / m).resolve()) for m in IMAGE_RE.findall(chunk)]
    text = IMAGE_RE.sub("", chunk).strip()
    if text == "_(no note)_":
        text = ""
    # A heading like "11:35 AM · bug" carries tags after the "·".
    tags = []
    if "·" in tag_hint:
        tags = [t.strip() for t in tag_hint.split("·", 1)[1].split(",") if t.strip()]
    return {"index": index, "id": None, "capturedAt": None, "tags": tags,
            "text": text, "images": images}


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


# ---------------------------------------------------------------------------
# receipt
# ---------------------------------------------------------------------------

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
    dest = results_dir / (Path(report).stem + ".result.json")
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
# archive
# ---------------------------------------------------------------------------

def cmd_archive(app_dir: Path, report: str, dry_run: bool) -> None:
    md = (app_dir / report).resolve()
    if not md.exists():
        fail(f"report not found: {report}")

    date = archive_date(md)
    dest_root = app_dir / "_archive" / date

    # The file set: the .md, its sidecar, and every referenced image.
    members = [md]
    sidecar = md.with_suffix(".json")
    if sidecar.exists():
        members.append(sidecar)
    for img in referenced_images(md):
        if img.exists():
            members.append(img)

    moved = []
    for src in members:
        rel = src.resolve().relative_to(app_dir)
        dest = dest_root / rel
        moved.append((str(rel), str(dest.relative_to(app_dir))))
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
    print(json.dumps({"report": report, "date": date, "dryRun": dry_run,
                      "moved": moved}, indent=2, ensure_ascii=False))


def archive_date(md: Path) -> str:
    m = TIMESTAMP_RE.match(md.name)
    if m:
        return m.group(1)
    return datetime.now().strftime("%Y-%m-%d")


def referenced_images(md: Path) -> list[Path]:
    sidecar = md.with_suffix(".json")
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text())
            refs = [img for n in data.get("notes", []) for img in n.get("images", [])]
            return [(md.parent / r) for r in refs]
        except json.JSONDecodeError:
            pass
    text = md.read_text(encoding="utf-8", errors="replace")
    return [(md.parent / r) for r in IMAGE_RE.findall(text)]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Slip field-report loop file I/O")
    p.add_argument("--app-dir", help="Override the report folder (else read config)")
    p.add_argument("--config", default=".claude/slip.json", help="Path to slip.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="Emit pending reports as JSON")

    pr = sub.add_parser("receipt", help="Write a resolution receipt")
    pr.add_argument("--report", required=True, help="Report path relative to app dir")
    pr.add_argument("--results", required=True, help="JSON file with the results array")
    pr.add_argument("--agent", default="claude-code:slip", help="Resolver identifier")

    pa = sub.add_parser("archive", help="Move a resolved report to _archive/<date>/")
    pa.add_argument("--report", required=True, help="Report path relative to app dir")
    pa.add_argument("--dry-run", action="store_true", help="Show moves without doing them")

    args = p.parse_args()
    app_dir = resolve_app_dir(args.app_dir, args.config)

    if args.cmd == "list":
        cmd_list(app_dir)
    elif args.cmd == "receipt":
        cmd_receipt(app_dir, args.report, args.results, args.agent)
    elif args.cmd == "archive":
        cmd_archive(app_dir, args.report, args.dry_run)


if __name__ == "__main__":
    main()
