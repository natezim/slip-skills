# Slip × Cursor

Add a project rule so Cursor knows how to handle Slip field reports. Create
`.cursor/rules/slip.mdc`:

```md
---
description: How to handle Slip field reports
---

Bug/idea reports from the Slip app live in `~/Dropbox/Slip/<App>/` (set your path). Reports are flat,
date-led `*.md` files (`<yyyy-MM-dd>-<HHmm>-<slug>.md`) directly in that folder; each note's stable
`id` is embedded as an `<!-- id: … -->` HTML comment after its heading. Screenshots sit beside each
report (`<yyyy-MM-dd>-<HHmm>-NNN.<ext>`); receipts go in a reserved `_results/` dir. (Older exports
may still use `<yyyy-MM-dd>/` day-folders with an `images/` subdir — read both.)

When asked to handle Slip reports:
- Read the pending reports (skip `_results/`, `README.md`, and any legacy `images/` dir); open referenced screenshots.
- Dedupe, fix what's clear, ask before anything risky.
- Write a receipt to `_results/<report-name>.result.json`:
  { "schema": 1, "project": "<App>", "results": [
    { "noteId": "<id from the note's comment>", "status": "fixed", "commit": "<sha>", "summary": "<one line>" } ] }
  status ∈ fixed | deferred | needs_info | wont_fix | duplicate. Match by noteId, never by order.
  Only write the receipt; never move or delete reports.
```

Slip watches the folder and marks resolved notes ✅ in its Fixes tab.
Full format: [../docs/field-report-loop.md](../docs/field-report-loop.md).
