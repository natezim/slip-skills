# Slip × Claude Code

A full skill: `/slip` reads a fresh batch of field reports, dedupes, fixes what's fixable, verifies,
and writes a resolution receipt. Reports stay put — receipts carry the state.

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

1. List the open reports (`slip.py list`) — reads the embedded id comments (falls back to a legacy
   `.json` sidecar, then plain markdown), and uses past receipts to hand back only what's still
   unresolved, flagging anything an earlier run left open. `--all` includes the finished work too.
2. Read the notes and screenshots, dedupe, and map each to your code.
3. Put the whole batch in front of you as a plan and ask the open questions as multiple choice —
   including what to cut if it's more than one run's work. That scope call is yours: the skill
   doesn't get to shelve work on its own.
4. Build it — every bug to verified-and-committed before the first idea, one commit per fix. Big
   clusters go to their own subagent so a long batch can't run the context out halfway through.
5. Write a receipt (`slip.py receipt`) — which is what lights up the **Fixes** tab back in the app.
   Receipts merge, so a later run can receipt just the notes it worked. Reports are immutable and
   dated; the phone ages old ones out by its retention window.

## The helper (`slip.py`)

Stdlib-only, three commands:

- `list [--all] [--new] [--tag T] [--limit N]` → structured JSON of the pending reports. `--all`
  adds the ones the receipts already closed out; `--new`, `--tag` and `--limit` narrow a big backlog
  to a working set without ever understating how much is really open.
- `triage --report R --notes F` → records what this run read, in `.claude/slip-triage.json`.
- `receipt --report R --results F` → writes `_results/<flattened-R>.result.json`.

`--app-dir` overrides both auto-detect and `slip.json`.

## Big backlogs

A hundred open notes is a hundred screenshots, and re-reading them every run is what kills the
context before anything gets committed. So the skill remembers what it has read: a one-line gist per
note in `.claude/slip-triage.json` (repo-local — it never goes near your synced folder, and Slip
doesn't know it exists). A later run pulls `--new` for the notes nobody has looked at yet and works
the rest from their gists.

That memory is reading, not deciding: a triaged note is still open work, and only a receipt closes
it. Delete the file and you lose some speed, nothing else.
