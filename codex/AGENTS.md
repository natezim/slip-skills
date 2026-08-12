# Slip field reports — agent instructions

Drop this file at your repo root (Codex reads `AGENTS.md`). Set the export path below.

## Field reports

Bug/idea reports captured with the Slip app live in `~/Dropbox/Slip/<App>/` (edit to your path).
Layout: flat, date-led `*.md` reports (`<yyyy-MM-dd>-<HHmm>-<slug>.md`) directly in that folder, each
note's stable `id` embedded as an `<!-- id: … -->` HTML comment after its heading; screenshots beside
each report (`<yyyy-MM-dd>-<HHmm>-NNN.<ext>`); a reserved `_results/` dir for receipts. (Older exports
may still use `<yyyy-MM-dd>/` day-folders with an `images/` subdir — read both.)

When asked to handle Slip reports:

1. Read the pending reports — every `*.md` except `README.md`, skipping the reserved `_results/` (and
   any legacy `images/`) dir. Open referenced screenshots.
2. Dedupe notes describing the same issue, then fix what's clearly fixable. Ask before anything
   ambiguous or risky. Verify by building and by exercising logic on the host — don't boot a
   simulator to go and look. The report's screenshots are the real app on the real device, and the
   next report is the QA result; say plainly when a fix was only compiled. Reports now tend to be
   feature-scoped — the phone groups the inbox by feature area (camera, listings) and sends one at a
   time — and each note's feature rides in as an ordinary tag, a hint for where the code lives.
3. For each report, write a receipt to `_results/<report-name>.result.json`:

   ```json
   {
     "schema": 1,
     "project": "<App>",
     "results": [
       { "noteId": "<id from the note's <!-- id: … --> comment>", "status": "fixed",
         "commit": "<sha>", "summary": "<one line>" }
     ]
   }
   ```

   `status` ∈ `fixed | deferred | needs_info | wont_fix | duplicate`. Match by `noteId`, never by
   order. Only write the receipt — never move or delete the reports.

Slip watches the folder and reflects resolved notes back into its Fixes tab.
See https://github.com/natezim/slip-skills/blob/main/docs/field-report-loop.md.
