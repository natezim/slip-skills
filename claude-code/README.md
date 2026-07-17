# Slip × Claude Code

A full skill: `/slip` reads a fresh batch of field reports, dedupes, fixes what's fixable, verifies,
writes a resolution receipt, and archives the sources.

## Install

Copy this folder's files into your project:

```
your-project/
└── .claude/
    ├── slip.json                 # ← from slip.json below
    └── skills/
        └── slip/
            ├── SKILL.md          # ← this folder's SKILL.md
            └── slip.py           # ← this folder's slip.py
```

Then create `.claude/slip.json` pointing at your Slip export folder:

```json
{
  "dropRoot": "~/Dropbox/Slip",
  "app": "YourAppName"
}
```

- `dropRoot` is the folder Slip exports into (the parent that contains one subfolder per app).
- `app` is the subfolder for this project — Slip names it after your project.

## Use

In Claude Code, run:

```
/slip
```

It will:

1. List the new reports (`slip.py list`) — prefers the JSON sidecar, falls back to parsing markdown.
2. Read the notes and screenshots, dedupe, and map each to your code.
3. Fix what's clear, verify, and stop-and-ask on anything risky.
4. Write a receipt (`slip.py receipt`) and archive resolved sources (`slip.py archive`) — which is
   what lights up the **Fixes** tab back in the app.

## The helper (`slip.py`)

Stdlib-only, three commands:

- `list --app-dir DIR` → structured JSON of pending reports.
- `receipt --app-dir DIR --report R --results F` → writes `_results/R.result.json`.
- `archive --app-dir DIR --report R` → moves a resolved report to `_archive/<date>/` (never deletes).

`--app-dir` overrides the folder from `slip.json`.
