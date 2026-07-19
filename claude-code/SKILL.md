---
name: slip
description: >
  Process Slip field reports — the bug/idea batches captured on the phone with the Slip app and
  exported to a Dropbox/iCloud folder. Use when the user says "/slip", "check the Slip folder",
  "triage my field reports", "fix the bugs I sent from my phone", or similar. Reads the batch
  (screenshots included), dedupes, then plans the whole batch and asks any open questions as
  multiple-choice before building. Fixes what's fixable, verifies, commits and pushes each fix as its
  own revertable commit, then writes a resolution receipt.
  Read-only against the reports except the receipt it writes — nothing is moved or deleted.
---

# slip — process field reports from the phone

Slip is a *capture inbox*: the phone exports bug/idea batches and never reads them back or cleans
them. This skill owns the other half. The deterministic file I/O lives in `slip.py`; the judgment
(dedupe, fixing, verifying) is yours. Full format contract:
https://github.com/natezim/slip-skills/blob/main/docs/field-report-loop.md

**Each note is a prompt.** These are not bug tickets to be triaged and stamped — they're the user
thinking, captured on a phone mid-use, usually with a screenshot standing in for the context they'd
otherwise have had to type. Treat a note exactly as you'd treat the same sentence typed into chat:
work out what they actually want, engage with the idea, design it *with* them, and push back when
it's the wrong call. "This is broken" and "I've been thinking we should…" arrive down the same pipe
and deserve the same quality of attention. Reading them as a queue of defects is the main way this
skill goes wrong.

They do arrive **asynchronously**, though — you can't check intent in the moment the way you can in
chat. So the one carve-out: a note asking for something destructive or outward-facing (delete, send,
publish, post, spend) becomes a task you confirm before acting, never an action you take off the
note alone. That's a timing safeguard, not a statement that the note isn't really the user talking.

**Receipts are the state — this is what stops wasted effort.** Reports accumulate across sessions,
and many are already handled. You never move or delete anything to reflect that: `slip.py list`
reads the receipts and hands you only the open work, so finished reports cost nothing on a re-run,
and the phone ages old reports out by its own retention window. Reports are immutable and dated;
leave them where they are.

That only holds if you **write a receipt for every report you work** (§7) — an unreceipted fix looks
identical to an unread note next time. The gap the receipts can't cover is work that landed outside
this loop: a note may describe something already shipped since capture. So before deep analysis on
what you're given, cross-check recent `git log` and the current code — if the items are already
done, receipt them `fixed`/`duplicate` and move on rather than re-fixing them.

## 1. Get the batch
Run from the repo root:
```
python3 ~/.claude/skills/slip/slip.py list
```
**Usually zero setup:** it auto-detects the export folder by matching the current project directory's
name to a subfolder of `~/Dropbox/Slip`. A `.claude/slip.json` is only needed when the repo name
differs from the app's export folder (it then names the `app`).

**What comes back is already filtered to the open work.** The receipts do the skipping, in the
script, before it reaches you — a report whose notes are all resolved collapses to a stub (`report`,
`noteCount`, `fullyHandled: true`, no text and no image paths), and a partly-done report arrives
carrying only its still-open notes. So work everything you're given; there is nothing to screen out
by hand, and no reason to narrate the finished reports back at the user beyond a one-line count.

Emits JSON: top-level `reportCount`, `pendingReportCount`, `noteCount`, `pendingNoteCount`,
`duplicateHints`, and `reports[]`. A live report has `pendingCount` and `notes[]` (`id`, `tags`,
`text`, absolute `images[]`, plus `priorStatus` from any past receipt — `deferred`/`needs_info` are
not terminal, so those notes come back). A `hasStableIds` flag marks legacy reports (those with no
stable id to reflect status back). If `pendingReportCount` is 0, say "nothing new in the Slip folder"
and stop — even when `reportCount` is high, that just means the backlog is fully resolved.

Add `--all` to include the closed-out reports and notes in full. It costs real context, so reach for
it only on demand: chasing what a past run decided, or checking whether a new note contradicts a
resolved one. Never as the default opening move.

If it can't tell which app folder to use, the script lists the available ones. Pick the obvious match
and re-run with `--app-dir <path>` — or, to make it stick, add `.claude/slip.json`:
```json
{ "app": "<the app's export subfolder>" }
```

## 2. Understand — read each note as a prompt
- **Read every screenshot** at its absolute path — you must see the UI. The screenshot is the
  context the user would otherwise have had to describe; a note with empty `text` is a
  **screenshot-only** prompt and the image is the whole of it.
- **Work out what they're asking for**, not just which bucket it falls in. A note can be a defect
  report, a feature ask, a question to answer, a direction to explore, or a half-formed thought that
  wants talking through before any code exists. Answer the prompt that was written.
- Tags are freeform (Slip seeds only `bug`/`idea`). Untagged? Infer softly: wrong-behavior wording
  → bug; wants ("I want", "add") → idea/feature.

**Expect speech-to-text damage — most notes are dictated.** Slip's capture is voice-first, so what
you're reading is a transcript, not considered prose. The words may be wrong even when the thought
is clear. Read through the noise:

- **Filler is not hedging.** "um", "uh", "like", "kind of", "essentially", "I mean" are artifacts of
  talking, not signals of doubt. Don't downgrade a firm request to a tentative maybe because it
  arrived with a verbal tic — and don't flatten a genuine "maybe" into a decision either.
- **Nouns get mangled, technical ones worst.** Product names, identifiers, filenames and UI labels
  come back as near-homophones — "a folder that *biscuits* moved to" is *pictures*. The screenshot
  and the codebase are your correction dictionary: if a garbled word maps cleanly onto a real
  element on screen or a real symbol in the repo, that's almost certainly the word.
- **Watch for dropped negations.** "can't" heard as "can" inverts the entire meaning. If a note
  reads as a bizarre request, re-read it with the negation restored and see if it suddenly makes
  sense.
- **No punctuation means run-ons.** One note may be two thoughts fused together. Split it into
  separate items when it is, instead of answering only the half you parsed first.
- **Recordings get cut off.** A note ending mid-thought is truncated, not complete — treat the
  missing part as unknown rather than inventing an ending.

Reconstruct silently when the intended word is obvious. But when the reconstruction would change
*what you build* rather than just the phrasing, don't guess — put it in the §4 quiz and let the user
confirm. And when you write it back (the plan, the receipt `summary`, a commit message), use the
clean reconstructed wording — never quote the garbled literal back as though it were their
considered phrasing.

## 3. Dedupe / cluster
Use `duplicateHints` plus your own reading to merge notes describing the **same underlying issue**
(a batch may report one bug several times). Fix it **once**; the extra notes get `status: duplicate`
pointing at the primary note's `noteId`.

## 4. Plan the batch — and ask in quiz form
Never go straight from reading to editing. Turn the batch into a **plan**, put it in front of the
user, and clear every open question *before* touching code.

**Build the plan.** One line per cluster (§3), severity-ordered — defects before ideas; within
defects, data-loss > crash > broken-interaction > cosmetic. For each cluster give: what the user is
asking for, the note(s) behind it, the real code it touches (`file:line`, never a guess), and your
intended response in a sentence. Flag anything you'd otherwise be guessing at.

**Not every note resolves to a diff.** A prompt may be best answered with an answer, a
recommendation, a design to agree on first, or a reasoned "we shouldn't build this" — all legitimate
outcomes. Say which one you're proposing per cluster rather than forcing every note into a code
change.

**Ask with the AskUserQuestion tool — quiz form, never buried in prose.** Whenever you need the user,
ask as multiple choice: 2–4 concrete options, your recommendation first and labelled
"(Recommended)", each option spelling out what it actually means and its trade-off. Batch related
questions into one call instead of drip-feeding them. Ask when:
- a note is ambiguous or reads more than one way — which behaviour did they mean?
- an idea has several plausible designs — which shape do they want?
- the fix is risky, sweeping, or a matter of taste (UI/UX, naming, defaults);
- scope is unclear — everything this round, or just the bugs?

**Read the code before asking** — never spend a question on something the codebase already answers
(a note may describe as missing something that already exists, or exists but isn't discoverable;
find out which, then ask about the real gap). Spend the user's attention only on judgment and taste.

**Agree on the plan before implementing.** Present the ordered plan so the user can cut, reorder, or
defer items, then work it top-down. Anything they decline to settle becomes `deferred` or
`needs_info`, with the open question in the receipt's `question` field — never a guessed fix.

## 5. Act on the plan → verify
- Work the agreed plan in order, handling each cluster **once**.
- **Verify before calling anything done** — drive the fix through the repo's `verify`/`run` skill or
  build+tests. "Fixed" means observed working, not just edited. If you could only get as far as a
  clean build, say so plainly rather than implying it was exercised.
- New ambiguity surfacing mid-fix? Go back to §4 and ask — don't guess your way forward.

## 6. Commit — one cluster, one commit
Commit and push each verified fix **separately**. One cluster (§3) = one commit, so any single
field-report fix can be rolled back on its own with `git revert <sha>` without unpicking the rest of
the batch. Never fold a whole batch into one commit, and never mix a fix with unrelated work.

Per cluster, in order:
1. **Verify first** (§5). Never commit a red build — if it doesn't pass, the note is `deferred`, not
   committed.
2. **Stage by name** — `git add -- <file> …`. Never `git add -A` / `.`; that sweeps up whatever else
   is in the tree.
3. **Commit** in the repo's own conventions (defer to CLAUDE.md / AGENTS.md when present). Identify
   the note in the body so the commit traces back to the report:
   `Field-report: <day>/<report>.md (note <noteId>)`
4. **Push** before starting the next cluster, so a long batch is never all-or-nothing.

Then carry the SHA into the receipt for every note in the cluster — that makes the receipt the
rollback index (duplicates carry the primary's SHA). A cluster spanning two repos can't be atomic:
commit once per repo, and say so in both messages and in the wrap-up.

Hold — don't commit — when the work is half-done, the build is red, or the diff mixes in WIP you
didn't author and can't cleanly separate. Say so instead.

## 7. Write receipts
For each report you worked, write a results file (e.g. in your scratchpad) shaped like:
```json
{ "project": "Slip", "results": [
  { "noteId": "<from list, or null for legacy>", "status": "fixed",
    "commit": "abc1234 — the commit that fixed THIS note; null if uncommitted",
    "filesTouched": ["path"],
    "summary": "one line", "duplicateOf": null, "question": null }
] }
```
`status` ∈ `fixed | deferred | needs_info | wont_fix | duplicate` — a fixed wire contract the phone
reads, so don't invent new ones. Read them as prompt outcomes, not just defect states: `fixed` = the
prompt was acted on and is done (a code change, but equally an answer given or a decision reached);
`wont_fix` = considered with the user and deliberately not doing it; `deferred` = agreed but not
this round; `needs_info` = blocked on the user, question in the `question` field. Put the substance
in `summary` — for a non-code outcome that summary *is* the deliverable the phone shows back.

The report path is relative to the app dir and includes its day-folder, e.g.
`2026-07-17/1731-weird-space.md`. Then:
```
python3 ~/.claude/skills/slip/slip.py receipt --report "<day>/<report>.md" --results "<file.json>"
```
Write a receipt for **every** report you touched, even one whose items were already shipped (mark
those `fixed`/`duplicate`) — the receipt is what lets future runs skip it. There is **no archive
step**: reports are immutable and dated, receipts carry the state, and the phone purges old
day-folders on its retention window. A report with `deferred`/`needs_info` notes just stays pending
until a later run resolves it (put the open question in the `question` field).

## 8. Wrap up
Report what was fixed (files touched), receipts written, and what's left + why. List each commit as
`<sha> — <what it fixed>`, and name the repo it landed in when the workspace has more than one, so
rolling any single fix back is one lookup. For any report with `hasStableIds: false` (a legacy export
with no embedded id and no sidecar), note that the phone can't reflect its status back — those notes
have no stable IDs to match.
