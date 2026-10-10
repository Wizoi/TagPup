"""The stage 3 re-review's findings #478, #483, #486, #488 (docs/findings.md); the ingress's (#480-#482, #484,
#485) are tests/test_roots_ingress.py's.
"""
import json
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import photo_rows  # noqa: E402
import roots_library as rl  # noqa: E402
from click.testing import CliRunner  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.services import file_changes, tagging  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.store import checks, db, file_journal  # noqa: E402
from tagpup.web import app as web  # noqa: E402

WINDOWS = os.name == "nt"
SHARE = "\\\\idziserver\\Pictures\\Pictures"


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class ShareSpelledRows(unittest.TestCase):
    """photo_index's shape: rooted photos at the local copy, and rows spelled by the share's address."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_share_")
        self.side = rl.Side(self.home, "photo_index", real=2, bulk=0, outside=0)
        conn = db.connect(self.side.db_path)
        try:
            for n in range(3):
                path = SHARE + "\\2026\\IMG_%d.jpg" % n
                photo_rows.add_read(conn, path, rl.read(path))
            conn.commit()
        finally:
            conn.close()
        self.machine = rl.machine()

    def adopt(self, address, apply=False):
        return roots_service.adopt(self.side.library, rl.NAME, address, self.side.pictures, self.machine, apply=apply)

    def test_with_an_address_they_are_share_spelled_in_the_dry_run_and_the_apply_refuses(self):     # #478
        dry = self.adopt(SHARE)
        report = dry.details["rehearsal"]
        self.assertEqual(3, report["tables"]["photos"]["share_spelled"])
        self.assertEqual(3, report["share_spelled"]["rows"])
        self.assertEqual([{"group": SHARE, "count": 3}], report["share_spelled"]["folders"])
        self.assertEqual([], [g for g in report["outside"] if g["group"].startswith("\\\\")], "counted outside")
        self.assertIn("spelled by the share's address", dry.refused)
        before = json.dumps(self.side.dump(), default=repr, sort_keys=True)
        applied = self.adopt(SHARE, apply=True)
        self.assertIn("spelled by the share's address", applied.refused)
        self.assertEqual(before, json.dumps(self.side.dump(), default=repr, sort_keys=True), "something was written")

    def test_without_an_address_nothing_changes(self):                                              # #478
        dry = self.adopt("")
        self.assertIsNone(dry.refused, dry.refused)
        self.assertEqual(0, dry.details["rehearsal"]["share_spelled"]["rows"])
        self.assertEqual(3, sum(g["count"] for g in dry.details["rehearsal"]["outside"] if g["group"].startswith("\\\\")))
        self.assertIsNone(self.adopt("", apply=True).refused)

    def test_the_check_reports_native_rows_the_roots_resolve_to_a_root(self):                      # #478
        self.assertIsNone(self.adopt("", apply=True).refused)
        self.assertEqual([], roots_service.check(self.side.library))
        # The address is given afterwards (an edit, an older adoption): Roots.locate now resolves them.
        db.write_with_connection(self.side.db_path, lambda conn: conn.execute(
            "UPDATE roots SET address = ? WHERE name = ?", (SHARE, rl.NAME)))
        found = roots_service.check(self.side.library)
        self.assertEqual(1, len(found), found)
        self.assertIn("3 row(s) kept their native path", found[0])
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        self.addCleanup(conn.close)
        self.assertEqual(3, checks.native_rows_under_a_root(conn).count)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheCliIndex(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_cli_")
        self.side = rl.Side(self.home, "photo_index", real=1, bulk=0, outside=0)
        self.assertIsNone(self.side.adopt().refused)
        self.old = self.side.pictures
        self.first = os.path.join(self.home.root, "Copy", "Pictures")
        shutil.copytree(self.old, self.first, copy_function=shutil.copy2)
        config.set_location("pictures", self.first)

    def run_index(self, directories):
        import click

        import tagpup_cli
        seen = []

        @click.command()
        @click.argument("directories", nargs=-1)
        @click.option("--reset", is_flag=True)
        @click.pass_context
        @tagpup_cli._holds_the_roots
        def fake(ctx, directories, reset):
            seen.append(list(directories))

        return CliRunner().invoke(fake, directories, obj={"db": self.side.db_path, "test": False}), seen

    def test_a_folder_that_exists_only_at_the_previous_place_is_refused_naming_both(self):        # #483
        later = os.path.join(self.old, "Only here")
        os.makedirs(later)
        result, seen = self.run_index([later])
        self.assertEqual(1, result.exit_code)
        self.assertEqual([], seen, "the command went on")
        self.assertIn("previous place of root pictures", result.output)
        self.assertIn(later, result.output.replace("\n", ""))
        self.assertIn(self.first, result.output.replace("\n", ""))

    def test_one_that_exists_at_both_is_indexed_at_the_first_place(self):                          # #483
        both = os.path.join(self.old, "Both")
        os.makedirs(both)
        os.makedirs(os.path.join(self.first, "Both"))
        result, seen = self.run_index([both, self.side.outside_folder])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual([[os.path.join(self.first, "Both"), self.side.outside_folder]], seen)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class AWriteStoppedByARootChange(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_stop_")
        self.side = rl.Side(self.home, "photo_index", real=2, bulk=0, outside=0)
        self.assertIsNone(self.side.adopt().refused)
        self.library = self.side.library

    def stopped(self, call):
        real = file_changes._carry
        calls = []

        def stop_after_the_first(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise roots_service.RootsChanged("changed")
            return real(*args, **kwargs)

        with mock.patch.object(file_changes, "_carry", stop_after_the_first):
            return call()

    def test_the_change_is_finished_at_once_so_what_was_written_can_be_undone(self):               # #486
        result = self.stopped(lambda: tagging.change_tags(self.library, self.side.real[:3], ["Harbour"], [],
                                                          rl.EXIFTOOL))
        change = result.details["change"]
        self.assertEqual("applied", file_journal.change(self.side.db_path, change).status)
        states = [row.state for row in file_journal.files_of(self.side.db_path, change)]
        self.assertEqual(["done"], states, "files never written were left planned")
        self.assertEqual([], file_journal.unfinished(self.side.db_path))

    def test_the_route_answers_refused_with_written_and_the_change(self):                          # #486
        app = web.create_app("tagpup", startup=self.library)
        app.testing = True
        reply = self.stopped(lambda: app.test_client().post(
            "/photo_index/api/photos/bulk-tags", data=json.dumps({"paths": self.side.real[:3], "add_tags": ["Harbour"],
                                                                  "remove_tags": []}), content_type="application/json"))
        body = reply.get_json()
        self.assertEqual(400, reply.status_code)
        self.assertIn(roots_service.STOPPED, body["error"])
        self.assertEqual(1, len(body["written"]))
        self.assertIsNotNone(body["change"])
        self.assertEqual([self.side.real[0].lower()], [p.lower() for p in body["written"]])


if __name__ == "__main__":
    unittest.main()
