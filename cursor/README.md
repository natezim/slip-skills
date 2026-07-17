# Slip × Cursor

Add a project rule so Cursor knows how to handle Slip field reports. Create
`.cursor/rules/slip.mdc`:

```md
---
description: How to handle Slip field reports
---

Bug/idea reports from the Slip app live in `~/Dropbox/Slip/<App>/` (set your path). Each export has
dated `*.md` reports, a same-named `*.json` sidecar with a stable `id` per note, a shared `images/`
folder, and reserved `_results/` and `_archive/` dirs.

When asked to handle Slip reports:
- Read the pending reports (skip `_archive/`, `_results/`, `README.md`); prefer the `.json` sidecars
  and open referenced screenshots.
- Dedupe, fix what's clear, ask before anything risky.
- Write a receipt to `_results/<report-name>.result.json`:
  { "schema": 1, "project": "<App>", "results": [
    { "noteId": "<id from sidecar>", "status": "fixed", "commit": "<sha>", "summary": "<one line>" } ] }
  status ∈ fixed | deferred | needs_info | wont_fix | duplicate. Match by noteId, never by order.
  Only write the receipt; don't move or delete reports.
```

Slip watches the folder and marks resolved notes ✅ in its Fixes tab.
Full format: [../docs/field-report-loop.md](../docs/field-report-loop.md).
