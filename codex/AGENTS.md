# Slip field reports — agent instructions

Drop this file at your repo root (Codex reads `AGENTS.md`). Set the export path below.

## Field reports

Bug/idea reports captured with the Slip app live in `~/Dropbox/Slip/<App>/` (edit to your path).
Layout: dated `*.md` reports, a same-named `*.json` sidecar with a stable `id` per note, a shared
`images/` folder, and reserved `_results/` and `_archive/` dirs.

When asked to handle Slip reports:

1. Read the pending reports — everything in the folder except `_archive/`, `_results/`, and
   `README.md`. Prefer the `.json` sidecars (easiest to parse); open referenced screenshots.
2. Dedupe notes describing the same issue, then fix what's clearly fixable. Ask before anything
   ambiguous or risky.
3. For each report, write a receipt to `_results/<report-name>.result.json`:

   ```json
   {
     "schema": 1,
     "project": "<App>",
     "results": [
       { "noteId": "<id from the sidecar>", "status": "fixed",
         "commit": "<sha>", "summary": "<one line>" }
     ]
   }
   ```

   `status` ∈ `fixed | deferred | needs_info | wont_fix | duplicate`. Match by `noteId`, never by
   order. Only write the receipt — never move or delete the reports.

Slip watches the folder and reflects resolved notes back into its Fixes tab.
See https://github.com/natezim/slip-skills/blob/main/docs/field-report-loop.md.
