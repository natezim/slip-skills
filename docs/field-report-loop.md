# Slip Field-Report Loop — shared contract

A closed loop between the **Slip iOS app** (capture) and **any external resolver** (fix), over a
plain folder in Dropbox/iCloud/Drive. No backend, no accounts, no provider APIs — just files,
matching Slip's existing export ethos.

**Slip is a standalone product; this loop is an optional integration.** The app is fully functional
with nothing on the other end: you capture, export clean AI-ready reports, and mark notes resolved
by hand. Resolution is a first-class app concept. A *resolution receipt* (below) is merely one
optional way to set it — written by any tool that speaks this format: a CI job, a script, a
different agent, or the Claude Code `slip` skill (the reference implementation, used by the app's
author). The app never assumes Claude Code, or any resolver, exists.

Both the Swift app and any resolver code against the formats defined here. The **report/export
format is `schema: 2`**; the **receipt format is its own `schema: 1`** (they version independently).

## Folder layout (per project, in the user's chosen folder)

Exports are **immutable, per-day-organized markdown**. One layout for every destination (custom
folder, the app's iCloud container, and share):

```
Slip/<Project>/
├── 2026-07-17/
│   ├── 1731-weird-space.md        # one report per send; <HHmm>-<slug>.md
│   └── images/
│       └── 1731-001.png           # screenshots, HHmm-prefixed, co-located in the day
├── 2026-07-18/
│   └── 0904-action-section.md
├── _results/                      # receipts written by a resolver, read by the phone
│   └── 2026-07-17-1731-weird-space.result.json
└── README.md                      # self-describing; regenerated on every export
```

- One report file per **send**, inside a `<yyyy-MM-dd>/` day-folder; images live in that day's
  `images/`, prefixed with the send's `HHmm` so several sends in a day never collide.
- **Bounded & self-organizing:** ~1 folder/day. The app purges whole day-folders older than its
  retention window — that's the only cleanup; nothing is ever moved or archived.
- `_results/` and `images/` are reserved names; discovery on both sides ignores them as reports.

## Report — `<day>/<HHmm>-<slug>.md` (written by Slip)

Human-readable **and** machine-parseable. Machine metadata rides in the YAML frontmatter, and each
note's stable `id` is embedded as an HTML comment right after its heading — invisible when the
markdown is read, trivial to parse. **There is no `.json` sidecar.**

```markdown
---
schema: 2
project: Slip
exported: 2026-07-17T21:31:08Z
device: { model: iPhone16,2, os: iOS 26.5, app: "1.0 (148)" }
source: Slip (iOS capture inbox)
tags: [bug, idea]
notes: 2
---

# Slip — Field Report
Captured 2026-07-17 • 2 notes • tags: bug (1), idea (1)

---

## 1. 5:30 PM · bug
<!-- id: 2AB1F73C-EE11-4273-8653-F2C3C322C6BD captured: 2026-07-17T17:30:00Z -->
This has a lot of weird space in it…

![screenshot](images/1731-001.png)

## 2. 5:31 PM · idea
<!-- id: 9F3A0000-0000-0000-0000-000000000002 captured: 2026-07-17T17:31:00Z -->
Add a compact mode.
```

The per-note id comment:

```
<!-- id: <UUID> captured: <ISO-8601 UTC> -->
```

Rules:
- `id` is the originating `BugNote.id` (UUID) — stable across re-export, the key a receipt joins on.
- The comment sits between a note's `## N.` heading and its body; strip it before treating the rest
  as note text.
- An empty note body (`_(no note)_`) means a **screenshot-only** capture; the image is the content.
- `images` paths are relative to the report `.md`, so they resolve the same for a person or a tool.

**Back-compat:** older exports were a flat `<timestamp>-<slug>.md` plus a `<timestamp>-<slug>.json`
sidecar (report `schema: 1`). Resolvers should still read that sidecar when a report has no embedded
id comments. Those legacy reports age out via retention; no migration is performed.

## Receipt — `_results/<report-name>.result.json` (written by the resolver, read by Slip)

One receipt per report processed. The name is the report's relative path flattened with `-`, so a
per-day report maps to a unique, date-led receipt (`2026-07-17/1731-weird-space.md` →
`2026-07-17-1731-weird-space.result.json`). Joins to notes by `noteId`.

```json
{
  "schema": 1,
  "app": "Slip",
  "project": "Slip",
  "sourceReport": "2026-07-17/1731-weird-space.md",
  "processedAt": "2026-07-17T15:02:00Z",
  "agent": "claude-code:slip",
  "results": [
    {
      "noteId": "2AB1F73C-EE11-4273-8653-F2C3C322C6BD",
      "status": "fixed",                      // fixed | deferred | needs_info | wont_fix | duplicate
      "commit": "abc1234",                    // present when status == fixed (may be null pre-commit)
      "filesTouched": ["DevThought/Slip/Export/BundleBuilder.swift"],
      "summary": "Whitespace is now trimmed on export.",
      "duplicateOf": null,                    // noteId, when status == duplicate
      "question": null,                       // string, when status == needs_info
      "deferredRuns": 0,                      // runs that ended without closing this note
      "deferredSince": null                   // ISO-8601, when it first stayed open
    }
  ]
}
```

**A receipt is merged, not replaced.** A resolver rewrites the file each time, but carries forward
every record it didn't touch this run — a run that receipts only the notes it worked must not reopen
the ones an earlier run closed. Records join by `noteId`; a legacy report with no ids joins
positionally and so has to be receipted in full.

`deferredRuns` / `deferredSince` are **resolver-owned aging**, and optional — the phone ignores them.
They exist so a note can't be quietly re-deferred forever: each run that leaves a note open
increments the count, closing it resets it to 0, and the resolver is expected to surface the age
rather than let an item roll silently from round to round.

`status` semantics for the phone:
- `fixed` → mark note Resolved, show `commit` + `summary`, move into the **Fixes** box.
- `duplicate` → fold into `duplicateOf`'s outcome (don't double-count).
- `deferred` / `wont_fix` → annotate, keep visible (not resolved).
- `needs_info` → surface `question` on the note as a prompt back to the user.

Resolution on the phone is **reversible** (received feedback, not a hard state) — reopening a note
clears its resolved status and pulls it back out of the Fixes box.

## Loop, end to end

1. **Capture** (phone) — note created; optional on-device tidy/auto-tag runs at finalize.
2. **Export** (phone) — writes `<day>/<HHmm>-<slug>.md` (ids embedded) + images to `Slip/<Project>/`.
3. **Fix** (resolver, e.g. the `slip` skill on the Mac) — parse reports, dedupe/cluster, fix,
   **verify**, commit.
4. **Receipt** (resolver) — write `_results/…result.json`. Nothing is moved: the receipt is the state.
5. **Confirm** (phone) — watch the folder, join receipts by `noteId`, mark Resolved, populate Fixes box.

`slip.py list` reads receipts and emits only the open work — a fully-resolved report collapses to a
counts-only stub, a partly-done one drops its closed notes — so a re-run skips finished work without
moving anything, and without spending the resolver's context on it. `--all` opts back in to the
whole history. `fixed`/`wont_fix`/`duplicate` close a note out; `deferred`/`needs_info` leave it
pending for a later run, and come back carrying `deferredRuns`/`deferredSince` plus a top-level
`agedNoteCount`, so a re-run can tell a genuinely new note from one it has already passed over.

Because a closed note's text is no longer shown, `list` carries one signal back across that edge: a
pending note that closely echoes a closed one gets `possibleRepeatOf`, naming the earlier note and
the outcome its receipt recorded. It is a lexical match — a re-report in near-identical words fires
it, a genuine rephrasing doesn't — so it flags work worth re-checking and never certifies that
anything is new.

## Invariants

- Files are the only channel. Either side may be offline; state reconciles on next read.
- `noteId` is the single join key across report and receipt. Never reuse or rewrite it.
- Reports are **immutable and dated**. Reserved names (`_results/`, `images/`) are never reports.
- **Receipts are the state; nothing is archived.** Old reports disappear only by the app's retention
  (it deletes whole day-folders, and ages out receipts), never by a resolver moving or deleting them.
