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

Both the Swift app and any resolver code against the formats defined here. Bump `schema` when a
format changes.

## Folder layout (per project, in the user's chosen folder)

```
Slip/<Project>/
├── <timestamp>-<slug>.md          # human-readable field report (existing)
├── <timestamp>-<slug>.json        # NEW machine sidecar — same basename as the .md
├── images/
│   └── <timestamp>-NNN.png        # shared, timestamp-prefixed (existing)
├── _results/                      # receipts written by Claude, read by the phone
│   └── <timestamp>-<slug>.result.json
└── _archive/<YYYY-MM-DD>/         # sources Claude moved here after resolving (folder hygiene)
```

- `<timestamp>` = `yyyy-MM-dd-HHmm` (matches `BundleBuilder.timestamp`).
- The `.md` is unchanged and stays the nice human artifact. The `.json` is authoritative for the agent.
- `_results/` and `_archive/` are reserved dir names; discovery on both sides ignores them as reports.

## Sidecar — `<timestamp>-<slug>.json` (written by Slip)

Authoritative machine form of one export. `id` per note is the keystone: it survives re-export,
enables dedupe, and is the key the receipt joins back on.

```json
{
  "schema": 1,
  "app": "Slip",
  "project": "Slip",
  "exportId": "5D9C…",                       // UUID for this export event
  "exportedAt": "2026-07-17T11:17:00Z",      // ISO-8601 UTC
  "source": "Slip (iOS capture inbox)",
  "device": {
    "model": "iPhone16,2",
    "os": "iOS 26.0",
    "appVersion": "1.2.0",
    "build": "148"
  },
  "notes": [
    {
      "id": "9F3A…",                          // BugNote.id (UUID), stable
      "capturedAt": "2026-07-17T11:15:00Z",
      "tags": ["bug"],                        // freeform; may be []
      "text": "Still showing the weird id instead of the Dropbox folder name",
      "images": ["images/2026-07-17-1117-001.png"]  // paths relative to the .md
    }
  ]
}
```

Rules:
- `text` is the raw note (empty string allowed → screenshot-only note; `images` is then the content).
- `images` paths are relative to the sidecar/`.md` location so they resolve identically for both.
- Everything in the `.md` is derivable from the sidecar; the sidecar never omits a note the `.md` has.

## Receipt — `_results/<timestamp>-<slug>.result.json` (written by Claude, read by Slip)

One receipt per report processed. Joins to notes by `noteId`.

```json
{
  "schema": 1,
  "app": "Slip",
  "project": "Slip",
  "sourceReport": "2026-07-17-1117-start-a-note-from-a-screenshot.md",
  "processedAt": "2026-07-17T15:02:00Z",
  "agent": "claude-code:slip",
  "results": [
    {
      "noteId": "9F3A…",
      "status": "fixed",                      // fixed | deferred | needs_info | wont_fix | duplicate
      "commit": "abc1234",                    // present when status == fixed (may be null pre-commit)
      "filesTouched": ["DevThought/Slip/Export/ExportDestination.swift"],
      "summary": "Opaque Dropbox ID now resolves to the real folder name.",
      "duplicateOf": null,                    // noteId, when status == duplicate
      "question": null                        // string, when status == needs_info
    }
  ]
}
```

`status` semantics for the phone:
- `fixed` → mark note Resolved, show `commit` + `summary`, move into the **Fixes** box.
- `duplicate` → fold into `duplicateOf`'s outcome (don't double-count).
- `deferred` / `wont_fix` → annotate, keep visible (not resolved).
- `needs_info` → surface `question` on the note as a prompt back to the user.

Resolution on the phone is **reversible** (received feedback, not a hard state) — reopening a note
clears its resolved status and pulls it back out of the Fixes box.

## Loop, end to end

1. **Capture** (phone) — note created; optional on-device tidy/auto-tag runs at finalize.
2. **Export** (phone) — writes `.md` + `.json` sidecar + images to `Slip/<Project>/`.
3. **Fix** (`slip` skill, Mac) — parse sidecars, dedupe/cluster, fix, **verify**, commit.
4. **Receipt** (skill) — write `_results/…result.json`; move resolved sources to `_archive/<date>/`.
5. **Confirm** (phone) — watch the folder, join receipts by `noteId`, mark Resolved, populate Fixes box.

## Invariants

- Files are the only channel. Either side may be offline; state reconciles on next read.
- `noteId` is the single join key across all four files. Never reuse or rewrite it.
- Reserved dirs (`_results/`, `_archive/`, `images/`) are never treated as reports.
- Nothing is hard-deleted by either side without explicit user action; "cleanup" always means *move*.
```
