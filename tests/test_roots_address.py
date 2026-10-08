"""A share's address keeps its two leading backslashes (#914).

Git Bash turns a leading double backslash in an argument into one before the program sees it, so
`roots adopt --address '\\\\server\\share\\Pictures'` arrived as `\\server\\share\\Pictures` and was
stored so: an address that names no place, ignored by every comparison. The adoption now refuses
such an address, and `roots repair-address` gives a root already stored so its two backslashes back,
as one journaled change that `undo` reverses.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import roots_library as rl  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.store import db, journal  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402

WINDOWS = os.name == "nt"

#: The address as the shell handed it over, and as it should be.
LOST = "\\idziserver\\Pictures\\Pictures\\Clients or Groups\\Parkrun"
KEPT = "\\" + LOST
DRIVE = "D:\\Pictures\\Pictures"


@unittest.skipUnless(WINDOWS, "a share's address is a Windows spelling")
class TheAddressKeepsItsBackslashes(unittest.TestCase):
    def test_only_a_single_leading_separator_is_a_lost_one(self):
        self.assertTrue(paths.lost_unc_slash(LOST))
        self.assertTrue(paths.lost_unc_slash("/idziserver/Pictures"))
        self.assertFalse(paths.lost_unc_slash(KEPT))
        self.assertFalse(paths.lost_unc_slash("//idziserver/Pictures"))
        self.assertFalse(paths.lost_unc_slash(DRIVE))
        self.assertFalse(paths.lost_unc_slash(""))

    def test_a_restored_address_compares_as_a_share_and_a_lost_one_does_not(self):
        self.assertEqual(KEPT, paths.restore_unc_slash(LOST))
        self.assertEqual(KEPT, paths.restore_unc_slash(KEPT))
        self.assertEqual(DRIVE, paths.restore_unc_slash(DRIVE))
        self.assertTrue(paths.is_native_absolute(KEPT))
        self.assertFalse(paths.is_native_absolute(LOST))
        self.assertTrue(paths.key(KEPT).startswith("\\\\"))
        self.assertTrue(paths.key(paths.restore_unc_slash(LOST)).startswith("\\\\"))


@unittest.skipUnless(WINDOWS, "a share's address is a Windows spelling")
class Adopting(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_address_")
        self.side = rl.Side(self.home, "photo_index", real=1, bulk=2, outside=1)

    def addresses(self):
        return {entry["name"]: entry["address"] for entry in store_roots.listing(self.side.db_path)}

    def test_an_address_that_lost_a_backslash_is_refused_and_nothing_is_written(self):
        for apply in (False, True):
            result = roots_service.adopt(self.side.library, "pictures", LOST, self.side.pictures, rl.machine(),
                                         apply=apply)
            self.assertIn("starts with one separator", result.refused)
        self.assertEqual({}, self.addresses())

    def test_a_share_address_is_stored_with_its_two_backslashes(self):
        result = roots_service.adopt(self.side.library, "pictures", KEPT, self.side.pictures, rl.machine(), apply=True)
        self.assertIsNone(result.refused)
        self.assertEqual({"pictures": KEPT}, self.addresses())

    def test_a_drive_address_is_stored_as_given(self):
        result = roots_service.adopt(self.side.library, "pictures", DRIVE, self.side.pictures, rl.machine(), apply=True)
        self.assertIsNone(result.refused, result.refused)
        self.assertEqual({"pictures": DRIVE}, self.addresses())


@unittest.skipUnless(WINDOWS, "a share's address is a Windows spelling")
class Repairing(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_repair_")
        self.side = rl.Side(self.home, "photo_index", real=1, bulk=1, outside=0)
        # The rows an adoption through the shell wrote: what adoption.adopt writes with the address
        # it was handed.
        conn = db.connect(self.side.db_path)
        try:
            store_roots.insert(conn, "parkrun", LOST)
            store_roots.insert(conn, "pictures", KEPT.replace("Clients or Groups\\Parkrun", "Other"))
            store_roots.insert(conn, "local", DRIVE)
            conn.commit()
        finally:
            conn.close()

    def addresses(self):
        return {entry["name"]: entry["address"] for entry in store_roots.listing(self.side.db_path)}

    def test_a_dry_run_says_what_it_would_change_and_changes_nothing(self):
        before = self.addresses()
        result = roots_service.repair_addresses(self.side.library)
        self.assertEqual([{"name": "parkrun", "was": LOST, "now": KEPT}], result.details["repairs"])
        self.assertEqual(0, result.changed)
        self.assertEqual(before, self.addresses())
        self.assertEqual([], [each for each in journal.history(self.side.db_path, limit=50)
                              if each["operation"].startswith(journal.ADDRESS_REPAIR)])

    def test_apply_repairs_only_the_lost_one_as_one_change(self):
        result = roots_service.repair_addresses(self.side.library, apply=True)
        self.assertEqual(1, result.changed)
        found = self.addresses()
        self.assertEqual(KEPT, found["parkrun"])
        self.assertEqual(DRIVE, found["local"])
        self.assertTrue(found["pictures"].endswith("Other"))
        changes = [each for each in journal.history(self.side.db_path, limit=50)
                   if each["operation"].startswith(journal.ADDRESS_REPAIR)]
        self.assertEqual(1, len(changes))
        self.assertNotIn("idziserver", str(changes[0]), "a summary holds no address")

    def test_it_is_run_twice_without_a_second_change(self):
        roots_service.repair_addresses(self.side.library, apply=True)
        again = roots_service.repair_addresses(self.side.library, apply=True)
        self.assertEqual((0, []), (again.changed, again.details["repairs"]))
        self.assertEqual(1, len([each for each in journal.history(self.side.db_path, limit=50)
                                 if each["operation"].startswith(journal.ADDRESS_REPAIR)]))

    def test_undo_puts_the_stored_address_back(self):
        roots_service.repair_addresses(self.side.library, apply=True)
        change = [each for each in journal.history(self.side.db_path, limit=50)
                  if each["operation"].startswith(journal.ADDRESS_REPAIR)][0]["id"]
        undone = journal_service.undo(self.side.library, change, apply=True)
        self.assertIsNone(undone.refused, undone.refused)
        self.assertEqual(LOST, self.addresses()["parkrun"])

    def test_a_library_with_no_such_root_has_nothing_to_repair(self):
        other = rl.Side(self.home, "other", real=1, bulk=1, outside=0)
        result = roots_service.repair_addresses(other.library, apply=True)
        self.assertEqual((0, []), (result.changed, result.details["repairs"]))

    def test_the_command_line(self):
        from click.testing import CliRunner
        from tagpup_cli import cli

        def run(*args):
            return CliRunner().invoke(cli, ["--db", self.side.db_path, "roots", "repair-address", *args])
        said = run()
        self.assertEqual(0, said.exit_code, said.output)
        self.assertIn("Nothing changed. --apply writes it.", said.output)
        self.assertEqual(LOST, self.addresses()["parkrun"])
        said = run("--apply")
        self.assertIn("Repaired 1 root(s).", said.output)
        self.assertEqual(KEPT, self.addresses()["parkrun"])
        self.assertIn("No root's address is missing", run().output)


if __name__ == "__main__":
    unittest.main()
