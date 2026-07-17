# Slip × Gemini CLI

There's no packaged skill here yet — but Slip exports are self-describing, so a prompt is enough.
Point Gemini CLI at your project with the export folder reachable, and paste:

```
Read every report in <path-to>/Slip/<App>/ (dated *.md under <yyyy-MM-dd>/ day-folders; skip
_results/ and images/). Each note's stable `id` is embedded as an <!-- id: … --> HTML comment after
its heading. For each note: understand it (open referenced screenshots), find the relevant code, and
fix what's clearly fixable. Ask before anything risky.

When you finish a note, append to _results/<report-name>.result.json in this shape:
{ "schema": 1, "project": "<App>", "results": [
  { "noteId": "<id from the note's comment>", "status": "fixed", "commit": "<sha>", "summary": "<one line>" }
] }
status ∈ fixed | deferred | needs_info | wont_fix | duplicate. Match by noteId, never by order.
Leave the reports in place; only write the receipt.
```

Slip watches the folder and marks the matching notes ✅ resolved.

See [the full contract](../docs/field-report-loop.md). PRs to turn this into a packaged Gemini
extension are welcome.
