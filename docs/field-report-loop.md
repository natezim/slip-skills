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
format is `schema: 3`**; the **receipt format is its own `schema: 1`** (they version independently).

## Folder layout (per project, in the user's chosen folder)

Exports are **immutable, flat, date-led markdown**. One layout for every destination (custom folder,
the app's iCloud container, and share):

```
Slip/<Project>/
├── 2026-07-17-1731-weird-space.md    # one report per send; <yyyy-MM-dd>-<HHmm>-<slug>.md
├── 2026-07-17-1731-001.png           # screenshots, date+HHmm-prefixed, beside their report
├── 2026-07-17-1731-002.jpg           # …in whichever format encoded smaller; see below
├── 2026-07-18-0904-action-section.md
├── _results/                         # receipts written by a resolver, read by the phone
│   └── 2026-07-17-1731-weird-space.result.json
└── README.md                         # self-describing; written once (refreshed only if changed)
```

- Flat, **not** nested: everything lives directly in `<Project>/`, the date leading every filename so
  reports and their screenshots sort and group by the day (then time) they were sent. There is no
  per-day folder — a File Provider (Dropbox) forks a freshly-created day-folder to `<day> (1)` on
  every send, so the only folder the app makes is the stable, reused `<Project>/`. (Older exports may
  still use schema-2 `<yyyy-MM-dd>/` day-folders with an `images/` subdir; a `*.md` glob finds reports
  in either layout, and image links are always relative to the report, so both resolve.)
- One report file per **send**; its screenshots sit beside it as `<yyyy-MM-dd>-<HHmm>-NNN.<ext>`.
- **Bounded, if the capture side is sweeping:** the app purges reports (and their screenshots) whose
  leading date is past its retention window — that's the only cleanup; nothing is ever moved or
  archived. Note that in Slip this sweep is **opt-in for a custom export folder** (`cleanExportFolder`),
  because the folder belongs to the user, not the app. With it off the loop still works and the folder
  grows without limit, so a resolver must not assume the batch it's handed is bounded, recent, or complete.
- `_results/` is a reserved name; discovery ignores it as a report, as is any file without a leading
  `<yyyy-MM-dd>` (e.g. `README.md`) and the legacy `images/` subdir.

## Report — `<yyyy-MM-dd>-<HHmm>-<slug>.md` (written by Slip)

Human-readable **and** machine-parseable. Machine metadata rides in the YAML frontmatter, and each
note's stable `id` is embedded as an HTML comment right after its heading — invisible when the
markdown is read, trivial to parse. **There is no `.json` sidecar.**

```markdown
---
schema: 3
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

![screenshot](2026-07-17-1731-001.png)

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
- Image paths are relative to the report `.md` (a bare `<yyyy-MM-dd>-<HHmm>-NNN.<ext>` beside it), so
  they resolve the same for a person or a tool.
- **Never infer an image's extension — follow the link.** A project folder may hold both `.png` and
  `.jpg`: Slip encodes each capture whichever way comes out smaller, which is PNG for flat UI with
  text and JPEG for photographs. Everything is `.png` in older sends. Match by the filename prefix or
  by reading the markdown link; matching on `.png` silently drops half the screenshots, and writing a
  copy under a hardcoded `.png` name mislabels the bytes inside it.

**Back-compat:** older exports were a flat `<timestamp>-<slug>.md` plus a `<timestamp>-<slug>.json`
sidecar (report `schema: 1`). Resolvers should still read that sidecar when a report has no embedded
id comments. Those legacy reports age out via retention; no migration is performed.

## Receipt — `_results/<report-name>.result.json` (written by the resolver, read by Slip)

One receipt per report processed. The name is the report's relative path flattened with `-`, so a
flat report maps to a same-named receipt (`2026-07-17-1731-weird-space.md` →
`2026-07-17-1731-weird-space.result.json`) and a legacy `<day>/…` path folds the slash to the same
result. Joins to notes by `noteId`.

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
      "commit": "abc1234",                    // BARE sha only, when status == fixed (null pre-commit).
                                              // The phone shows it verbatim — no message, no prose.
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

`deferredRuns` / `deferredSince` are **resolver-owned aging**, and optional — written by the resolver,
and the phone may surface them (Slip shows "asked 3 times, still open • since Jul 12"). They exist so
a note can't be quietly re-deferred forever: each run that leaves a note open increments the count,
closing it resets it to 0, and both sides are expected to show the age rather than let an item roll
silently from round to round. Being optional, a reader must tolerate their absence — older receipts
predate them, and any tool may write a receipt without them.

A resolver should require a status to carry its own justification — `deferred` a summary saying who
cut the work, `needs_info` its question, `duplicate` the `duplicateOf` it folds into. `fixed` needs
nothing extra. This isn't a format rule (the phone reads whatever arrives); it's that the statuses
which remove a note from the list are otherwise the cheapest ones to write, which is backwards.

`status` semantics for the phone:
- `fixed` → mark note Resolved, show `commit` + `summary`, move into the **Fixes** box.
- `duplicate` → the note is closed; `duplicateOf` names the primary. Annotating the row as a
  duplicate is enough — folding it into the primary's outcome is optional (the primary may even
  live in a different report), and Slip doesn't do it.
- `deferred` / `wont_fix` → annotate, keep visible (not resolved).
- `needs_info` → surface `question` on the note as a prompt back to the user.

Resolution on the phone is **reversible** (received feedback, not a hard state) — reopening a note
clears its resolved status and pulls it back out of the Fixes box.

## Loop, end to end

1. **Capture** (phone) — note created; optional on-device tidy/auto-tag runs at finalize.
2. **Export** (phone) — writes `<yyyy-MM-dd>-<HHmm>-<slug>.md` (ids embedded) + images, flat in `Slip/<Project>/`.
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

A resolver may also keep **triage memory** — a private record of which notes it has already read and
what it understood them to mean, so a large backlog isn't re-read (and its screenshots re-opened)
every run. That state is deliberately *outside* this contract: the reference implementation keeps it
in the repo it fixes, not in the shared folder, because it's the resolver's working memory and means
nothing to the phone. Nothing here should ever require it.

The same store may hold a **park** — notes the user has deliberately held for a named future session
("the ideas round"), which the resolver keeps out of its own default pull so they aren't re-read and
re-deferred every run. Aging is driven by receipts, so a note nobody pulls is a note nobody receipts,
and its `deferredRuns` stops climbing; that's the whole mechanism, and it needs no new status. On the
wire a parked note is simply `deferred` with a summary saying who held it and why, which is what the
phone shows. A resolver that keeps no park is still fully conformant.

Because a closed note's text is no longer shown, `list` carries one signal back across that edge: a
pending note that closely echoes a closed one gets `possibleRepeatOf`, naming the earlier note and
the outcome its receipt recorded. It is a lexical match — a re-report in near-identical words fires
it, a genuine rephrasing doesn't — so it flags work worth re-checking and never certifies that
anything is new.

## Invariants

- Files are the only channel. Either side may be offline; state reconciles on next read.
- `noteId` is the single join key across report and receipt. Never reuse or rewrite it.
- Reports are **immutable and dated**, flat in `<Project>/`. `_results/` (and any file without a
  leading `<yyyy-MM-dd>`, like `README.md`, plus the legacy `images/` subdir) is never a report.
- **Receipts are the state; nothing is archived.** Old reports disappear only by the app's retention
  (it deletes dated report files + their screenshots, and ages out receipts), never by a resolver
  moving or deleting them.
