#!/usr/bin/env python3
"""Tests for slip.py — stdlib only, no deps. Run: python3 claude-code/test_slip.py

Weighted toward the things that lose the user's work rather than toward coverage:
a receipt that reopens closed notes, a status that quietly parks one forever, a
gist trusted in place of a note nobody read, a narrowed pull that understates the
backlog behind it. Each of those has been a real bug in this file.
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import slip  # noqa: E402

NOTE_A = "2AB1F73C-EE11-4273-8653-F2C3C322C6BD"
NOTE_B = "9F3A0000-0000-0000-0000-000000000002"

REPORT = """---
schema: 2
project: Slip
---

# Slip — Field Report

## 1. 5:30 PM · bug
<!-- id: {a} -->
Weird space in the export.

![screenshot](images/1731-001.png)

## 2. 5:31 PM · idea
<!-- id: {b} -->
Add a compact mode.
""".format(a=NOTE_A, b=NOTE_B)

RELPATH = "2026-07-17/1731-weird-space.md"


class SlipTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = self.root / "app"
        (self.app / "2026-07-17" / "images").mkdir(parents=True)
        (self.app / RELPATH).write_text(REPORT)
        (self.app / "2026-07-17" / "images" / "1731-001.png").write_bytes(b"")
        self.triage = self.root / "triage.json"

    # -- helpers ---------------------------------------------------------
    def listing(self, **kwargs):
        buf = io.StringIO()
        with redirect_stdout(buf):
            slip.cmd_list(self.app, self.triage, **kwargs)
        return json.loads(buf.getvalue())

    def receipt(self, results, report=RELPATH):
        path = self.root / "results.json"
        path.write_text(json.dumps({"project": "Slip", "results": results}))
        with redirect_stdout(io.StringIO()):
            slip.cmd_receipt(self.app, report, str(path), "test")

    def triage_notes(self, notes, report=RELPATH):
        path = self.root / "triage-in.json"
        path.write_text(json.dumps({"notes": notes}))
        with redirect_stdout(io.StringIO()):
            slip.cmd_triage(self.app, self.triage, report, str(path))

    def notes(self, **kwargs):
        return [n for r in self.listing(**kwargs)["reports"] for n in r["notes"]]

    def park(self, note_ids, theme="ideas round", report=RELPATH):
        path = self.root / "park-in.json"
        path.write_text(json.dumps(
            {"notes": [{"report": report, "noteId": n} for n in note_ids]}))
        with redirect_stdout(io.StringIO()):
            slip.cmd_park(self.app, self.triage, theme, str(path), None)

    def unpark(self, theme=None, note_ids=None, report=RELPATH):
        path = None
        if note_ids is not None:
            path = self.root / "unpark-in.json"
            path.write_text(json.dumps(
                {"notes": [{"report": report, "noteId": n} for n in note_ids]}))
        with redirect_stdout(io.StringIO()):
            slip.cmd_unpark(self.app, self.triage, theme,
                            str(path) if path else None, None)

    def add_report(self, rel, body):
        path = self.app / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)

    # -- parsing ---------------------------------------------------------
    def test_reads_embedded_ids_and_absolute_images(self):
        notes = self.notes()
        self.assertEqual([n["id"] for n in notes], [NOTE_A, NOTE_B])
        self.assertTrue(Path(notes[0]["images"][0]).is_absolute())
        self.assertTrue(Path(notes[0]["images"][0]).exists())

    def test_reserved_dirs_are_never_reports(self):
        (self.app / "_results").mkdir(exist_ok=True)
        (self.app / "_results" / "notes.md").write_text("# not a report")
        (self.app / "README.md").write_text("# not a report")
        self.assertEqual(self.listing()["reportCount"], 1)

    # -- receipts: the state that stops re-work ---------------------------
    def test_terminal_status_closes_a_note_out(self):
        self.receipt([{"noteId": NOTE_A, "status": "fixed", "commit": "abc1234"}])
        listing = self.listing()
        self.assertEqual(listing["pendingNoteCount"], 1)
        self.assertEqual([n["id"] for n in self.notes()], [NOTE_B])

    def test_fully_handled_report_drops_out_entirely(self):
        self.receipt([{"noteId": NOTE_A, "status": "fixed"},
                      {"noteId": NOTE_B, "status": "wont_fix"}])
        listing = self.listing()
        self.assertEqual(listing["reports"], [])
        self.assertEqual(listing["reportCount"], 1)  # still counted, just not paid for

    def test_partial_receipt_does_not_reopen_closed_notes(self):
        # The regression this was written for: receipts used to be a full
        # overwrite, so a later run receipting only its own note silently
        # reopened everything an earlier run had closed.
        self.receipt([{"noteId": NOTE_A, "status": "fixed", "commit": "abc1234"}])
        self.receipt([{"noteId": NOTE_B, "status": "deferred", "summary": "user cut it"}])
        self.assertEqual(self.listing()["pendingNoteCount"], 1)
        stored = {r["noteId"]: r for r in self.stored_receipt()["results"]}
        self.assertEqual(stored[NOTE_A]["status"], "fixed")
        self.assertEqual(stored[NOTE_A]["commit"], "abc1234")

    def stored_receipt(self):
        path = self.app / "_results" / slip.receipt_filename(RELPATH)
        return json.loads(path.read_text())

    # -- aging -----------------------------------------------------------
    def test_leaving_a_note_open_ages_it(self):
        self.receipt([{"noteId": NOTE_B, "status": "deferred", "summary": "user cut it"}])
        first = self.notes()[-1]
        self.assertEqual(first["deferredRuns"], 1)
        self.receipt([{"noteId": NOTE_B, "status": "deferred", "summary": "cut again"}])
        second = self.notes()[-1]
        self.assertEqual(second["deferredRuns"], 2)
        self.assertEqual(second["deferredSince"], first["deferredSince"])  # pinned to the first
        self.assertEqual(self.listing()["agedNoteCount"], 1)

    def test_closing_a_note_clears_its_age(self):
        self.receipt([{"noteId": NOTE_B, "status": "deferred", "summary": "user cut it"}])
        self.receipt([{"noteId": NOTE_B, "status": "fixed", "commit": "abc1234"}])
        stored = {r["noteId"]: r for r in self.stored_receipt()["results"]}
        self.assertEqual(stored[NOTE_B]["deferredRuns"], 0)
        self.assertIsNone(stored[NOTE_B]["deferredSince"])

    def test_untouched_notes_do_not_age(self):
        # A note the run never reached is not deferred, and must not accrue age.
        self.receipt([{"noteId": NOTE_A, "status": "fixed"}])
        self.assertEqual(self.listing()["agedNoteCount"], 0)
        self.assertNotIn("deferredRuns", self.notes()[0])

    # -- validation: the cheap statuses have to cost something ------------
    def assert_refused(self, results, *, because: str):
        """The write is rejected, and the message says which field is missing —
        an error nobody can act on just gets retried verbatim."""
        err = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(err):
            self.receipt(results)
        self.assertIn(because, err.getvalue())

    def test_unknown_status_is_refused(self):
        self.assert_refused([{"noteId": NOTE_A, "status": "Fixed"}], because="unknown status")

    def test_deferred_needs_a_reason(self):
        self.assert_refused([{"noteId": NOTE_A, "status": "deferred"}], because="summary")
        self.receipt([{"noteId": NOTE_A, "status": "deferred", "summary": "user cut it"}])

    def test_needs_info_needs_a_question(self):
        self.assert_refused([{"noteId": NOTE_A, "status": "needs_info", "summary": "blocked"}],
                            because="question")
        self.receipt([{"noteId": NOTE_A, "status": "needs_info", "question": "which screen?"}])

    def test_duplicate_needs_a_target(self):
        # Terminal *and* pointerless would close a note into nothing.
        self.assert_refused([{"noteId": NOTE_B, "status": "duplicate"}], because="duplicateOf")
        self.receipt([{"noteId": NOTE_B, "status": "duplicate", "duplicateOf": NOTE_A}])

    def test_every_problem_is_reported_at_once(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(err):
            self.receipt([{"noteId": NOTE_A, "status": "deferred"},
                          {"noteId": NOTE_B, "status": "duplicate"}])
        self.assertIn("summary", err.getvalue())
        self.assertIn("duplicateOf", err.getvalue())

    def test_fixed_needs_nothing_extra(self):
        self.receipt([{"noteId": NOTE_A, "status": "fixed"}])  # non-code outcomes are legitimate

    def test_commit_must_be_a_bare_sha(self):
        # The phone renders the commit verbatim; "sha — prose" duplicated the
        # summary on half of all real resolved rows before this was enforced.
        self.assert_refused(
            [{"noteId": NOTE_A, "status": "fixed",
              "commit": "39d10c4 — fix: the tag box stops eating the note"}],
            because="bare sha")
        self.receipt([{"noteId": NOTE_A, "status": "fixed", "commit": "39d10c4"}])
        self.receipt([{"noteId": NOTE_B, "status": "fixed", "commit": None}])  # pre-commit is fine

    # -- triage memory ---------------------------------------------------
    def test_triage_is_remembered_and_new_excludes_it(self):
        self.triage_notes([{"noteId": NOTE_A, "gist": "trailing space on export",
                            "cluster": "export-space", "sawScreenshot": True}])
        listing = self.listing()
        self.assertEqual(listing["triagedNoteCount"], 1)
        self.assertEqual(listing["reports"][0]["notes"][0]["triaged"]["gist"],
                         "trailing space on export")
        self.assertEqual([n["id"] for n in self.notes(new_only=True)], [NOTE_B])

    def test_triage_merges_across_runs(self):
        self.triage_notes([{"noteId": NOTE_A, "gist": "one"}])
        self.triage_notes([{"noteId": NOTE_B, "gist": "two"}])
        self.assertEqual(self.listing()["triagedNoteCount"], 2)

    def test_gist_is_provisional_when_the_screenshot_was_not_read(self):
        self.triage_notes([{"noteId": NOTE_A, "gist": "guessed", "sawScreenshot": False},
                           {"noteId": NOTE_B, "gist": "no image on this note"}])
        by_id = {n["id"]: n for n in self.notes()}
        self.assertTrue(by_id[NOTE_A]["triaged"]["gistProvisional"])   # has an image, unread
        self.assertFalse(by_id[NOTE_B]["triaged"]["gistProvisional"])  # nothing to have missed

    def test_triage_without_a_gist_is_refused(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(err):
            self.triage_notes([{"noteId": NOTE_A, "gist": "   "}])
        self.assertIn("gist", err.getvalue())

    def test_triage_never_closes_a_note(self):
        self.triage_notes([{"noteId": NOTE_A, "gist": "read it"}])
        self.assertEqual(self.listing()["pendingNoteCount"], 2)

    # -- parking: held work must not read as resolved work ----------------
    def test_parked_notes_leave_the_pull_but_not_the_count(self):
        # The whole point. A parked note is held, not resolved — if parking made
        # the backlog number shrink it would be a nicer-looking way to lose work.
        self.park([NOTE_B])
        listing = self.listing()
        self.assertEqual([n["id"] for n in self.notes()], [NOTE_A])
        self.assertEqual(listing["pendingNoteCount"], 2)
        self.assertEqual(listing["returnedNoteCount"], 1)
        self.assertEqual(listing["parkedNoteCount"], 1)
        self.assertEqual(listing["parkedThemes"], {"ideas round": 1})

    def test_theme_opens_exactly_that_park(self):
        self.park([NOTE_A], theme="design session")
        self.park([NOTE_B], theme="ideas round")
        self.assertEqual([n["id"] for n in self.notes(theme="ideas round")], [NOTE_B])
        self.assertEqual([n["id"] for n in self.notes(theme="IDEAS ROUND")], [NOTE_B])
        self.assertEqual(self.notes(theme="nobody-parked-here"), [])

    def test_parking_freezes_the_age_instead_of_ratcheting_it(self):
        # The bug parking exists for: six real notes sat at deferredRuns 2–4 purely
        # because each run re-read them and re-deferred them with the same summary.
        self.receipt([{"noteId": NOTE_B, "status": "deferred", "summary": "user cut it"}])
        self.park([NOTE_B])
        for _ in range(3):  # three runs that never see it, so never receipt it
            self.assertNotIn(NOTE_B, [n["id"] for n in self.notes()])
        self.unpark(theme="ideas round")
        self.assertEqual(self.notes()[-1]["deferredRuns"], 1)  # not 4

    def test_unparking_returns_it_as_ordinary_open_work(self):
        self.park([NOTE_B])
        self.unpark(theme="ideas round")
        self.assertEqual([n["id"] for n in self.notes()], [NOTE_A, NOTE_B])
        self.assertEqual(self.listing()["parkedNoteCount"], 0)
        self.assertNotIn("parked", self.notes()[-1])

    def test_unparking_a_single_note_leaves_the_rest_held(self):
        self.park([NOTE_A, NOTE_B])
        self.unpark(note_ids=[NOTE_A])
        self.assertEqual(self.listing()["parkedNoteCount"], 1)
        self.assertEqual([n["id"] for n in self.notes()], [NOTE_A])

    def test_park_and_triage_do_not_overwrite_each_other(self):
        # One memory file, two independent facts. Parking used to be tempting to
        # store as a triage field, which would have made a held note read as read.
        self.triage_notes([{"noteId": NOTE_A, "gist": "trailing space"}])
        self.park([NOTE_B])
        self.assertEqual(self.listing()["triagedNoteCount"], 1)
        self.assertEqual(self.listing()["parkedNoteCount"], 1)
        self.triage_notes([{"noteId": NOTE_B, "gist": "compact mode"}])
        self.assertEqual(self.listing()["parkedNoteCount"], 1)  # survived the triage write

    def test_park_refuses_a_note_that_is_not_in_the_report(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(err):
            self.park(["00000000-0000-0000-0000-00000000DEAD"])
        self.assertIn("not in", err.getvalue())

    def test_park_requires_a_real_theme_name(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(err):
            self.park([NOTE_B], theme="  ")
        self.assertIn("theme", err.getvalue())

    def test_unpark_that_matches_nothing_says_so(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(err):
            self.unpark(theme="never-used")
        self.assertIn("nothing matched", err.getvalue())

    def test_all_still_shows_parked_notes(self):
        self.park([NOTE_B])
        ids = [n["id"] for r in self.listing(include_all=True)["reports"] for n in r["notes"]]
        self.assertIn(NOTE_B, ids)

    # -- receipts aimed at the wrong report -------------------------------
    def test_receipt_refuses_a_noteid_from_another_report(self):
        # Found in a real folder: a 3-note report carrying 4 records, the extra one
        # a note from elsewhere. It joins to nothing, so the outcome is unreadable
        # and the note it was meant for stays pending forever. Silent until now.
        self.assert_refused(
            [{"noteId": "00000000-0000-0000-0000-00000000DEAD", "status": "fixed"}],
            because="not in this report")

    def test_legacy_reports_still_receipt_positionally(self):
        self.add_report("2026-07-18/0900-legacy.md", "# R\n\n## 1. 9:00 AM\nflat old note\n")
        self.receipt([{"status": "fixed", "commit": "abc1234"}],
                     report="2026-07-18/0900-legacy.md")

    # -- slicing a big backlog -------------------------------------------
    def test_counts_stay_global_when_the_pull_is_narrowed(self):
        listing = self.listing(limit=1)
        self.assertEqual(listing["pendingNoteCount"], 2)   # the backlog, not the slice
        self.assertEqual(listing["returnedNoteCount"], 1)
        self.assertTrue(listing["truncated"])

    def test_limit_is_not_marked_truncated_when_it_fits(self):
        self.assertFalse(self.listing(limit=5)["truncated"])

    def test_tag_filter_and_untagged_selector(self):
        self.assertEqual([n["id"] for n in self.notes(tags=["bug"])], [NOTE_A])
        self.assertEqual([n["id"] for n in self.notes(tags=["BUG"])], [NOTE_A])  # case-insensitive
        self.add_report("2026-07-18/0900-plain.md", "# R\n\n## 1. 9:00 AM\nno tag here\n")
        self.assertEqual([n["text"] for n in self.notes(tags=["untagged"])], ["no tag here"])

    def test_pull_order_follows_capture_date_not_export_folder(self):
        # Day-folders are keyed to the *export*; a note can sit on the phone for
        # days first, so a newer folder can hold the oldest open thought.
        self.add_report("2026-07-20/0900-sent-late.md",
                        "# R\n\n## 1. 9:00 AM\n"
                        "<!-- id: 33330000-0000-0000-0000-000000000003 "
                        "captured: 2026-07-10T08:00:00Z -->\ncaptured long ago\n")
        # Fixture report's notes carry no captured: stamp, so it falls back to its
        # folder date (2026-07-17) — later than the 07-10 capture above.
        self.assertEqual(self.listing()["reports"][0]["report"], "2026-07-20/0900-sent-late.md")

    def test_aged_reports_are_pulled_ahead_of_merely_older_ones(self):
        self.add_report("2026-07-10/0900-older.md", "# R\n\n## 1. 9:00 AM\nolder note\n")
        self.assertEqual(self.listing()["reports"][0]["report"], "2026-07-10/0900-older.md")
        self.receipt([{"noteId": NOTE_B, "status": "deferred", "summary": "user cut it"}])
        self.assertEqual(self.listing()["reports"][0]["report"], RELPATH)  # aged jumps the queue

    def test_all_includes_the_closed_backlog(self):
        self.receipt([{"noteId": NOTE_A, "status": "fixed"},
                      {"noteId": NOTE_B, "status": "fixed"}])
        self.assertEqual(len(self.listing(include_all=True)["reports"]), 1)

    # -- legacy exports ---------------------------------------------------
    def test_legacy_report_has_no_stable_ids(self):
        self.add_report("2026-07-18/0900-legacy.md", "# R\n\n## 1. 9:00 AM\nflat old note\n")
        legacy = [r for r in self.listing()["reports"]
                  if r["report"] == "2026-07-18/0900-legacy.md"][0]
        self.assertFalse(legacy["hasStableIds"])
        self.assertIsNone(legacy["notes"][0]["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
