"""People by id, stage 2, fix round 3: a name that became exactly one person's because a same-named person left stays an unresolved
name (nothing links it by chance), and is therefore in no person's list. It is REPORTED (person_ids.unresolved `one`, the doctor) and
LINKED only by the owner: `people link-name NAME [--apply]` (tagpup.services.people.link_name), a dry run by default, one journaled
change of the faces, undoable.

Two Max (a pet and a friend), one renamed away: the face decided by hand, a guess and the photo's list stay unresolved Max.
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from click.testing import CliRunner  # noqa: E402
from people_by_id import MAX_FRIEND, MAX_PET, TwoSams, look, write  # noqa: E402

from tagpup.core.result import Refused  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import people as people_service  # noqa: E402
from tagpup.store import db, faces, journal, people, person_ids, taxonomy  # noqa: E402
from tagpup_cli import cli  # noqa: E402


class LinkingByTheOwner(TwoSams, unittest.TestCase):
    def setUp(self):
        self.make_library()
        self.pet = self.node(MAX_PET)

        def unresolved(conn):
            self.manual = faces.insert(conn, self.other, [40, 0, 50, 10], b"\x03" * 8)
            self.guess = faces.insert(conn, self.other, [60, 0, 70, 10], b"\x04" * 8)
            conn.execute("UPDATE faces SET name = 'Max', tag_id = NULL, name_source = 'manual' WHERE id = ?", (self.manual,))
            conn.execute("UPDATE faces SET name = 'Max', tag_id = NULL, name_source = NULL WHERE id = ?", (self.guess,))
            people.rebuild_photos(conn, [self.other])

        write(self.path, unresolved)
        write(self.path, lambda conn: taxonomy.move_branch(conn, MAX_FRIEND, "Friends/Rowan Thackeray"))   # Pets/Max is the only Max

    def unresolved(self):
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            return person_ids.unresolved(conn)
        finally:
            conn.close()

    def test_the_name_is_reported_with_the_rows_it_would_link(self):
        found = self.unresolved()
        self.assertEqual({"Max": (self.pet, 1, 1, 1)}, found.one)
        self.assertEqual({}, found.several)
        self.assertEqual({}, found.none)

    def test_the_doctor_says_so(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import doctor
        said = io.StringIO()
        with redirect_stdout(said):
            doctor.report(self.path)
        self.assertIn("names one person is called whose rows are linked to nobody: 1, on 1 face(s) decided by hand, 1 other "
                      "face(s) and 1 listed person(s)", " ".join(said.getvalue().split()))
        self.assertNotIn("Max", said.getvalue(), "names only with --show")

    def test_the_repair_links_nothing(self):
        person_ids.repair(self.path)
        self.assertEqual((None, "Max", "manual"), self.face(self.manual))

    def test_a_dry_run_counts_and_writes_nothing(self):
        result = people_service.link_name(self.library, "max")
        self.assertEqual((1, 1, 1, False), (result.details["faces_by_hand"], result.details["faces_by_guess"],
                                            result.details["listed"], result.details["applied"]))
        self.assertEqual((None, "Max", "manual"), self.face(self.manual))
        self.assertEqual([], look(self.path, "SELECT 1 FROM changes WHERE operation = ?", (journal.PERSON_LINKED,)))

    def test_apply_links_the_faces_and_the_list_as_one_journaled_change_and_the_undo_unlinks_them(self):
        result = people_service.link_name(self.library, "Max", apply=True)
        self.assertTrue(result.ok, result.message())
        self.assertEqual((self.pet, "Max", "manual"), self.face(self.manual), "the owner said who it is: the hand decision too")
        self.assertEqual((self.pet, "Max", None), self.face(self.guess))
        self.assertIn((self.pet, "Max", "face"), self.listed(self.other))
        self.assertEqual({}, self.unresolved().one)
        changes = look(self.path, "SELECT id FROM changes WHERE operation = ?", (journal.PERSON_LINKED,))
        self.assertEqual(1, len(changes))
        undone = journal_service.undo(self.library, changes[0][0], apply=True, exiftool_path="exiftool")
        self.assertEqual([], undone.errors)
        self.assertEqual((None, "Max", "manual"), self.face(self.manual))
        self.assertEqual(1, len(self.unresolved().one), "unresolved again, and not linked by the undo")

    def test_a_name_two_people_have_or_nobody_is_refused(self):
        with self.assertRaises(Refused) as why:
            people_service.link_name(self.library, "Sam")
        self.assertIn("Family/Thackeray/Sam", str(why.exception))
        refused = people_service.link_name(self.library, "Nobody Atall")
        self.assertIn("No person is called", refused.refused)

    def test_the_command_is_a_dry_run_unless_apply(self):
        runner = CliRunner()
        dry = runner.invoke(cli, ["--db", self.path, "people", "link-name", "Max"])
        self.assertEqual(0, dry.exit_code, dry.output)
        self.assertIn("1 face(s) decided by hand, 1 other face(s) and 1 listed person(s)", " ".join(dry.output.split()))
        self.assertIn("A dry run", dry.output)
        self.assertEqual((None, "Max", "manual"), self.face(self.manual))
        applied = runner.invoke(cli, ["--db", self.path, "people", "link-name", "Max", "--apply"])
        self.assertEqual(0, applied.exit_code, applied.output)
        self.assertEqual((self.pet, "Max", "manual"), self.face(self.manual))
        refused = runner.invoke(cli, ["--db", self.path, "people", "link-name", "Sam"])
        self.assertEqual(1, refused.exit_code)
        self.assertIn("Family/Ingersoll/Sam", refused.output)


if __name__ == "__main__":
    unittest.main()
