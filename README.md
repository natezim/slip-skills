# Slip Skills

Ready-made integrations for closing the **Slip** field-report loop with your AI coding tool.

[Slip](https://github.com/natezim/Slip) captures bugs and ideas on your phone and exports them as
plain files — Markdown reports, a JSON sidecar per export, and screenshots. Any tool (or a person)
can read them, fix the issues, and write a small **receipt** back so Slip marks them ✅ resolved in a
"Fixes" tab.

No backend, no account, no API key — just files in a folder you already sync (Dropbox, iCloud,
Drive…).

## Pick your tool

| Tool | Setup |
|------|-------|
| **[Claude Code](claude-code/)** | A full `/slip` skill: triage, dedupe, fix, verify, and close the loop. |
| **[Gemini CLI](gemini-cli/)** | A prompt + the receipt format. |
| **[Codex](codex/)** | An `AGENTS.md` recipe. |
| **[Cursor](cursor/)** | A project rule + the receipt format. |
| **Anything else** | Every Slip export includes a `README.md` that explains the format — just point your tool at the folder. |

## How the loop works

1. **Capture** on your phone with Slip → export to a folder.
2. **Point your AI tool at the folder.** Each export self-describes via its `README.md`.
3. Your tool **fixes** issues and **writes a receipt** to `_results/<report>.result.json`.
4. Slip **reads the receipt** and marks those notes resolved (with your summary).

Full format: [docs/field-report-loop.md](docs/field-report-loop.md).

## The receipt (the one thing to get right)

After fixing a note, write `_results/<report-name>.result.json`:

```json
{
  "schema": 1,
  "project": "<project>",
  "results": [
    {
      "noteId": "<id from the note's .json sidecar>",
      "status": "fixed",
      "commit": "<commit sha, optional>",
      "summary": "<one line, optional>"
    }
  ]
}
```

`status` is one of `fixed`, `deferred`, `needs_info`, `wont_fix`, `duplicate`. Match notes by
`noteId` — never by order. Slip only ever reads this file; it never leaves your folder.

## Contributing

Using Slip with a tool that isn't here yet? PRs welcome — add a folder with a short recipe and the
receipt format above.
