# Slip × Claude Code

A full skill: `/slip` reads a fresh batch of field reports, dedupes, fixes what's fixable, verifies,
writes a resolution receipt, and archives the sources.

## Install (once, globally)

Install the skill at the user level so `/slip` works in **every** project — no per-repo setup:

```
~/.claude/
└── skills/
    └── slip/
        ├── SKILL.md          # ← this folder's SKILL.md
        └── slip.py           # ← this folder's slip.py
```

```sh
mkdir -p ~/.claude/skills/slip
cp claude-code/SKILL.md claude-code/slip.py ~/.claude/skills/slip/
```

## Config: usually none

The skill **auto-detects** your export folder by matching the current project directory's name to a
subfolder of `~/Dropbox/Slip`. So a repo named after its app just works.

You only need a config when the repo name **differs** from the app's export folder (e.g. repo
`DevThought`, app folder `Slip`). Then add `.claude/slip.json` in that repo:

```json
{ "app": "Slip" }
```

(Add `"dropRoot": "~/somewhere/else"` too if you don't export to `~/Dropbox/Slip`.)

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

`--app-dir` overrides both auto-detect and `slip.json`.
