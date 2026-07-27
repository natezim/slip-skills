---
name: slip
description: >
  Process Slip field reports — the bug/idea batches captured on the phone with the Slip app and
  exported to a Dropbox/iCloud folder. Use when the user says "/slip", "check the Slip folder",
  "triage my field reports", "fix the bugs I sent from my phone", or similar. Reads the batch
  (screenshots included), dedupes, then plans the whole batch and asks any open questions as
  multiple-choice before building. Works every bug to done before the first idea, hands large
  clusters to their own subagent rather than shelving them, verifies, commits and pushes each fix as
  its own revertable commit, then writes a resolution receipt. Only the user defers work out of a
  round. Read-only against the reports except the receipt it writes — nothing is moved or deleted.
---

# slip — process field reports from the phone

Slip is a *capture inbox*: the phone exports bug/idea batches and never reads them back or cleans
them. This skill owns the other half. The deterministic file I/O lives in `slip.py`; the judgment
(dedupe, fixing, verifying) is yours. Full format contract:
https://github.com/natezim/slip-skills/blob/main/docs/field-report-loop.md

**Each note is a prompt.** These are not bug tickets to be triaged and stamped — they're the user
thinking, captured on a phone mid-use, usually with a screenshot standing in for the context they'd
otherwise have had to type. Treat a note exactly as you'd treat the same sentence typed into chat:
work out what they actually want, engage with the idea, design it *with* them. "This is broken" and
"I've been thinking we should…" arrive down the same pipe and deserve the same quality of attention.
Reading them as a queue of defects is the main way this skill goes wrong.

Engaging is not the same as litigating. If you think a note is the wrong call, say so **once**, in a
sentence or two, and then build it anyway under a stated assumption unless the user tells you
otherwise — the concern belongs in the plan (§4) and the receipt `summary`, not in a negotiation
that costs a round-trip per note.

They do arrive **asynchronously**, though — you can't check intent in the moment the way you can in
chat. So the one carve-out: a note asking for something destructive or outward-facing (delete, send,
publish, post, spend) becomes a task you confirm before acting, never an action you take off the
note alone. That's a timing safeguard, not a statement that the note isn't really the user talking.

**Deferring is the user's call, not yours.** The failure this skill is tuned against: a run reads
twelve notes, fixes three, and files the rest under "backlog" — where, in practice, they are lost.
So nothing leaves this round on your own judgment. "Too big", "deserves its own session", "wants
more thought" are not outcomes — a large note gets a subagent (§5), which is exactly what the
subagent is for. A note can end the run still open in only two ways:

- the **user cut it** from this round in the §4 quiz → `deferred`;
- you asked a **real question** and don't have the answer yet → `needs_info`, question in the receipt.

Both are the user's decision, recorded. Everything else in the batch gets driven to `fixed`,
`wont_fix` (considered together and deliberately dropped) or `duplicate` before the run ends.

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
script, before it reaches you — a report whose notes are all resolved is dropped from `reports[]`
entirely, and a partly-done report arrives carrying only its still-open notes. So work everything
you're given; there is nothing to screen out by hand. The finished backlog survives only as the
difference between `reportCount` and `pendingReportCount`, which is all it's worth: don't narrate
it back at the user beyond a one-line count.

Emits JSON: top-level `reportCount`, `pendingReportCount`, `noteCount`, `pendingNoteCount`,
`agedNoteCount`, `duplicateHints` (over the open notes only), and `reports[]` — which holds only the
reports with open work. A live report has `pendingCount` and `notes[]` (`id`, `tags`,
`text`, absolute `images[]`, plus `priorStatus` from any past receipt — `deferred`/`needs_info` are
not terminal, so those notes come back). A `hasStableIds` flag marks legacy reports (those with no
stable id to reflect status back). If `pendingReportCount` is 0, say "nothing new in the Slip folder"
and stop — even when `reportCount` is high, that just means the backlog is fully resolved.

**A note carrying `deferredRuns` has been round this loop before.** The script counts the runs that
ended without closing it and stamps `deferredSince` with when that started; `agedNoteCount` totals
them. These are the notes the user is most likely to feel they've lost, and they cost nothing to
re-defer — which is exactly why they must not be. Work them **first** within their pass (§5), and if
one is going to stay open again, say so out loud in the wrap-up with its age, rather than letting it
roll silently into a third run. `deferredRuns ≥ 2` means this run is the last honest chance to
either land it or agree with the user to drop it as `wont_fix`.

A pending note may also carry **`possibleRepeatOf`** — see §3.

Add `--all` to include the closed-out reports and notes in full. It costs real context, so reach for
it only on demand: chasing what a past run decided, or checking whether a new note contradicts a
resolved one. Never as the default opening move.

### A big batch is pulled in slices — and read only once, ever
The counts arrive before any note does, so you always know the size before you spend anything on it.
Past ~25 open notes, **do not pull the batch whole and do not read it end to end.** A hundred notes
means a hundred screenshots and a hundred re-derivations, and the run dies of context long before it
commits anything — which is the same disappearing act as a backlog, just with more work burned.

**`--new` is the one that matters: notes no run has ever triaged.** Everything else re-reads what
you already understood. Reach for it first on any re-run.

```
python3 ~/.claude/skills/slip/slip.py list --tag now          # first, always — see §2
python3 ~/.claude/skills/slip/slip.py list --new --tag bug --tag untagged --limit 25
```

- `--new` — drop the notes a past run already read and summarized.
- `--tag <t>` (repeatable) — `--tag bug --tag untagged` is the defect pass; `untagged` is not
  optional there, since Slip only seeds `bug`/`idea` and a dictated note often carries neither.
- `--limit N` — the first N open notes, oldest first; sets `truncated` when it holds some back.

The counts stay global whatever you narrow to: `pendingNoteCount` is the whole backlog,
`returnedNoteCount` is what this pull gave you. Say the real number out loud — "103 open, working
the 25 oldest defects this round" — and never let a narrowed pull get reported as the whole batch.

**Then record what you read, or you'll read it again.** For every note you understood, write a
triage entry — this is the memory that makes a hundred-note backlog survivable:

```json
{ "notes": [ { "noteId": "<from list>", "gist": "one line, in your reconstructed words (§2)",
               "cluster": "short-slug", "kind": "bug|idea|question", "sawScreenshot": true } ] }
```
```
python3 ~/.claude/skills/slip/slip.py triage --report "<day>/<report>.md" --notes "<file.json>"
```

It lands in `.claude/slip-triage.json` — repo-local, never in the synced folder; the phone has no
idea it exists. Next run those notes come back carrying `triaged`, and **that gist is what you work
from: don't reopen the screenshot or re-derive the meaning unless you're about to build that
cluster.** So the gist has to carry the note's actual content, not a filing label — "wants the list
collapsed by default" is memory; "UI feedback" is not, and costs the next run the whole re-read.

**Triage is memory, not an outcome.** A triaged note is still open work: it goes in the plan and it
needs a receipt like anything else. Only receipts close notes, and only §4's scope quiz takes work
out of a round. Recording that you read something is never how it leaves the list.

If it can't tell which app folder to use, the script lists the available ones. Pick the obvious match
and re-run with `--app-dir <path>` — or, to make it stick, add `.claude/slip.json`:
```json
{ "app": "<the app's export subfolder>" }
```

## 2. Understand — read each note as a prompt
- **A note carrying `triaged` you have already read** — start from its `gist` and leave the image
  alone. Reopen it only when you're about to build that cluster and need the detail, or when the
  gist is too thin to act on (and then rewrite the gist so the next run isn't stuck too).
- **`gistProvisional: true` means that gist was written about a screenshot nobody opened** — it's a
  guess, and it's the one kind you must not build on. Open the image, then re-triage it with
  `sawScreenshot: true`. A guess made once is otherwise believed forever.
- **Read every screenshot** of a note you haven't triaged, at its absolute path — you must see the
  UI. The screenshot is the context the user would otherwise have had to describe; a note with empty
  `text` is a **screenshot-only** prompt and the image is the whole of it.
- **Work out what they're asking for**, not just which bucket it falls in. A note can be a defect
  report, a feature ask, a question to answer, a direction to explore, or a half-formed thought that
  wants talking through before any code exists. Answer the prompt that was written.
- Tags are freeform (Slip seeds `bug`/`idea`). Untagged? Infer softly: wrong-behavior wording
  → bug; wants ("I want", "add") → idea/feature.
- **`now` is not a kind — it's the user's own priority call**, tapped on the phone at capture. It
  outranks anything you would infer from wording, and it outranks the bug/idea split: a `now` idea
  goes ahead of a bug nobody flagged. They were looking at the problem when they tapped it; you're
  reading a transcript of it afterwards. Work every `now` note first, whatever else the batch holds.

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

**`possibleRepeatOf` means the opposite of "already handled."** A pending note carrying it closely
echoes one that a past receipt already closed — the hint gives you that note's id, report, status,
`summary` and `commit`. The user is telling you a second time about something the receipts claim is
done, and the most likely explanation is that **the earlier fix didn't hold**. So go look: read the
named commit, exercise the behaviour, and treat the new note as live work. Resolving it `duplicate`
on the strength of the old receipt is the one move that turns a real regression into a silently
closed note — only do it once you've confirmed the current code genuinely does the right thing.

It's a lexical match, so **read it in one direction only**. When it fires, it's worth checking. When
it doesn't, that tells you nothing: it catches re-dictations in near-identical words and misses the
same complaint genuinely rephrased. Never infer "no hint, so this is new."

## 4. Plan the batch — and ask in quiz form
Never go straight from reading to editing. Turn the batch into a **plan**, put it in front of the
user, and clear every open question *before* touching code.

**Build the plan.** One line per cluster (§3), in the order §5 will actually work them: anything
tagged `now` first (§2); then every defect before any idea; aged notes (`deferredRuns`) ahead of
fresh ones inside each group; within defects, data-loss > crash > broken-interaction > cosmetic. For each cluster give: what the user is asking
for, the note(s) behind it, the real code it touches (`file:line`, never a guess), your intended
response in a sentence, and whether you'll do it inline or hand it to a subagent (§5). Flag anything
you'd otherwise be guessing at.

Size is a routing decision, not a filter: a cluster being large is what sends it to a subagent, and
never what keeps it off the plan. Every note **in this pull** appears somewhere in the plan — and on
a sliced batch (§1), say in a line what's behind it: "25 shown, 103 open, 78 not yet read."

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
- the batch is genuinely more than one run — **this is the scope question, and it's the only door
  out of the round.** Don't pre-shrink it on their behalf: give them the real shape (how many open,
  how many defects, how many already aged), say which clusters you'd take first and roughly what
  each costs, and let them choose. Whatever survives gets built this run, however big it is.

  At a hundred notes, say plainly that it's several runs' work — that's a fact about the batch, not
  a verdict on any note in it. **A note you didn't reach is not `deferred`.** `deferred` is written
  only for something the user looked at and cut; a note this round never got to gets *no receipt at
  all*, keeps its place in the queue, and picks up no age. Blanket-deferring the tail would age the
  entire backlog in one go and destroy the one signal that says which notes are genuinely stuck.

**Read the code before asking** — never spend a question on something the codebase already answers
(a note may describe as missing something that already exists, or exists but isn't discoverable;
find out which, then ask about the real gap). Spend the user's attention only on judgment and taste.

**Agree on the plan before implementing.** Present the ordered plan so the user can cut, reorder, or
defer items, then work it top-down. A question they leave unanswered becomes `needs_info`, with the
question in the receipt's `question` field — never a guessed fix. A question they *do* answer is
settled: don't re-open it later in the run.

## 5. Execute — bugs first, then ideas
Work the agreed plan in order, handling each cluster **once**.

**`now` first, then two passes, and don't interleave them.** Anything the user tagged `now` is
worked before the rest of the batch, whatever kind it is. Then every defect cluster reaches
verified-and-committed before the first idea starts.

**Stop at each pass boundary and check in.** After the `now` notes, and again when the defects are
done, come back with what landed, what's next, and one question: carry on, or change the order?
Keep it to a few lines — this is a checkpoint, not a report. A batch of any size is otherwise hours
of silence after a single planning quiz, and a plan agreed at minute two is at its least informed
exactly when it's least revisable. It's also the cheapest moment to be redirected: everything behind
you is committed and receipted, so nothing is lost by changing course here. Ideas are where a batch runs long, so a run that gets cut short should
cost the user ideas, never fixes. Inside each pass, aged notes (§1) go ahead of fresh ones.

**Delegate only the clusters that would otherwise cost you the run.** A subagent starts cold: it
re-reads what you already have loaded, re-derives what you already know, and hands back a summary
you then have to verify. That overhead is real and you pay it per agent — delegate reflexively and
a batch of small fixes takes hours it didn't need to.

So inline is the default. Hand a cluster to a `general-purpose` subagent via the Agent tool when
working it here would genuinely threaten the run — a wide investigation across unfamiliar code, a
sprawling diff, anything you'd expect to spend a long stretch inside. Those are the ones that
otherwise end a twelve-note batch at note five. A fix you can see the shape of already is faster
done here, and every cluster you keep is one you don't have to check afterwards.

The brief is the whole job, because the subagent starts cold and can't see the batch:
- the note text (reconstructed per §2, never the garbled literal), its `noteId`, and the **absolute
  paths of its screenshots** — tell it to open them;
- what §4 settled: the option the user picked, and the ones they turned down;
- the real code you already located (`file:line`), so it isn't re-deriving what you know;
- how to verify in this repo, and §6's commit-and-push rules verbatim, `Field-report:` trailer included;
- what to hand back: `status`, `filesTouched`, the commit SHA, one line on **what it actually
  exercised**, and its open question if it got blocked.

**Run them one at a time.** The win here is context, not wall-clock, and sequential agents already
give you nearly all of it. Two agents committing into one repo race on the index and on each other's
files — a batch half-written by two authors is the exact mess §6 exists to prevent.

That rule is about *writing*. **Reading can fan out**: on a big batch, hand each report to its own
read-only subagent to open the screenshots and come back with §1's triage entries — gist, cluster,
kind — and nothing else. They touch no files, so they're safe in parallel, and they're where the
hundred-screenshot problem actually goes away: the images are read once, in a context that is thrown
away, and what survives is one line per note. Write their entries to the triage store before you
plan, so the round is cheap to resume even if it ends early.

**A subagent's report is a claim, not a result.** Before its cluster counts as done, confirm the SHA
is real (`git log -1 <sha> --stat`) and that what it verified is what the note asked for. No SHA or
no verification line means the cluster isn't finished: read what it did, then either finish it here
or send it back with the gap named. Never copy a subagent's summary into a receipt unchecked.

**Narrate the queue — delegated work is invisible otherwise.** A subagent can't report mid-flight,
so the run has to. Run them **in the foreground**, and around each one print one line before and
one after:

```
▶ 3/7 export-whitespace — notes 4, 9 — delegating
✓ 3/7 export-whitespace — abc1234 — verified: re-exported, trailing space gone
```

Then keep clusters small. A cluster that takes an agent twenty minutes reports once; four clusters
of five report four times, and that difference *is* the visibility — there's no other dial. It also
bounds what a bad brief can cost you. And write each receipt as its cluster lands rather than
batching them at the end, so a run that dies leaves its progress on disk rather than in a context
that's gone. Between the commits and the receipts, `git log --oneline` is the honest progress bar:
it survives the chat, and it's the one record that can't be a claim.

**Verify before calling anything done** — drive the fix through the repo's `verify`/`run` skill or
build+tests. "Fixed" means observed working, not just edited. If you could only get as far as a
clean build, say so plainly rather than implying it was exercised.

**New ambiguity surfacing mid-fix?** Go back to §4 and ask — don't guess your way forward. If it
surfaced inside a subagent, that agent stops and reports the question; you put it to the user and
re-brief it. A subagent never settles a design question on the user's behalf.

## 6. Commit — one cluster, one commit
Commit and push each verified fix **separately**. One cluster (§3) = one commit, so any single
field-report fix can be rolled back on its own with `git revert <sha>` without unpicking the rest of
the batch. Never fold a whole batch into one commit, and never mix a fix with unrelated work.

A delegated cluster (§5) commits inside its own subagent, under these same rules — that's why they
run one at a time, and why the SHA it reports is checked here before it reaches a receipt.

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
didn't author and can't cleanly separate. Say so instead. Holding is not deferring: the cluster is
still this run's work, and the next move is to get it green, not to write it up as backlog.

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
`wont_fix` = considered with the user and deliberately not doing it; `deferred` = **the user** cut it
from this round (§4) — never your own verdict on the size of the job; `needs_info` = blocked on the
user, question in the `question` field. Put the substance in `summary` — for a non-code outcome that
summary *is* the deliverable the phone shows back.

**The statuses that take a note off the list have to say what they are, and the script enforces it:**
`deferred` needs a `summary` naming who cut it and why, `needs_info` needs its `question`, and
`duplicate` needs the `duplicateOf` it folds into. `fixed` is the one that needs nothing extra —
it already paid, in a verified change. If you can't fill the field, you don't have that outcome:
it's unfinished work, and it belongs back in §5.

Only include the notes **this run worked**: the script merges results over the report's last receipt,
so untouched notes keep the outcome they already had. `deferredRuns`/`deferredSince` are the
script's to maintain — it ages a note that stays open and clears it when one closes. Don't write
them yourself.

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

On a sliced batch (§1), close with the state of the queue: how many notes remain open, how many of
those are now triaged and ready to work cheaply next time, and what the next run should pull first.
That standing number is the thing the user is actually tracking — never let a round end reporting
only the slice it happened to take.

**Account for every open note by name.** Anything still `deferred` or `needs_info` gets its own line:
the note, its age (`deferredRuns` + `deferredSince` — "open since 2026-07-17, third run"), the exact
question or the user's own decision to cut it, and what would close it next time. A count is not an
account: "3 items deferred" is precisely the shape the user loses things in. If a note is ending its
second run or later still open, say that plainly and ask whether to drop it as `wont_fix` — an item
nobody intends to build is better closed than carried.
