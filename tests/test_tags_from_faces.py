"""docs/findings.md, #861: `tagpup_cli.py tags-from-faces` -- the faces named before a photo's tag followed them, mended.

A face named by TagTuner's strip, Match or AutoMatch All, by Identify Faces or by clustering left the photo listing the person
from the face alone (`photo_people` source 'face'): a green box and "No people tags". The command counts them (a dry run,
never a name) and with --apply writes the keyword into each photo's FILE, 25 photos to a journaled change. The doctor counts
the same.

ExifTool is a table of files (tests/fake_exiftool.py); the photos are real small JPEGs and rows as the indexer records them.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_organize_faces import Case, ODA, WREN  # noqa: E402

from click.testing import CliRunner  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.services import face_people, tags_from_faces  # noqa: E402
from tagpup.store import checks, db, people, taxonomy  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import doctor  # noqa: E402

PEOPLE = "People/"


class Mending(Case):
    def drifted(self, name, who=WREN, **picture):
        """A photo whose face was named and whose keywords do not say so."""
        photo = self.photo(name, **picture)
        self.files.keep_tags(photo, [])
        self.face(photo, name=who, name_source="manual")
        return photo

    def run_cli(self, *arguments):
        with mock.patch.object(tagpup_cli, "get_exiftool_path", return_value="exiftool"):
            return CliRunner().invoke(tagpup_cli.cli, ["--db", self.path, "tags-from-faces", *arguments])

    def operations(self):
        return [row[0] for row in self.look("SELECT operation FROM changes ORDER BY id") if row[0] == tags_from_faces.OPERATION]


class TheDryRun(Mending):
    def test_it_counts_and_writes_nothing(self):
        for n in range(3):
            self.drifted("drift_%d.jpg" % n)
        self.photo("fine.jpg", [PEOPLE + ODA])
        said = self.run_cli()
        self.assertEqual(0, said.exit_code, said.output)
        self.assertIn("3 photo(s) list a person from a face alone (3 people)", said.output)
        self.assertIn("3 photo file(s) would be written", said.output)
        self.assertIn("Nothing changed", said.output)
        self.assertEqual(0, self.files.writes)
        self.assertEqual([], self.operations())

    def test_it_never_names_anyone(self):
        self.drifted("drift_x.jpg")
        said = self.run_cli()
        self.assertNotIn(WREN, said.output)
        self.assertNotIn("drift_x", said.output)

    def test_a_person_the_tree_files_in_two_places_is_left_and_counted(self):
        def two_places(conn):
            with people.tree_edit(conn):
                taxonomy.add_path(conn, "People/Pairs/" + ODA)
                taxonomy.add_path(conn, "People/Others/" + ODA)
        db.write_with_connection(self.path, two_places)
        self.drifted("drift_a.jpg")
        self.drifted("drift_b.jpg", who=ODA)
        planned = tags_from_faces.plan(self.library)
        self.assertEqual({"photos_with_a_person_on_a_face_alone": 2, "people": 2, "photos_to_write": 1, "people_to_write": 1,
                          "people_the_tree_files_in_two_places": 1, "people_the_file_names_under_another_root": 0,
                          "people_from_a_guess_only": 0, "guesses_included": False}, planned.details["counts"])

    def test_a_library_in_step_has_nothing_to_do(self):
        self.photo("fine.jpg", [PEOPLE + WREN])
        said = self.run_cli("--apply")
        self.assertEqual(0, said.exit_code)
        self.assertIn("Nothing to write", said.output)


class OneRuleForThePersonAPhotoAlreadyNames(Mending):
    def under_another_root(self, name="other_root.jpg"):
        # The photo's keyword is the person's leaf under a root the tree does not file people under: the index lists the person
        # from the face alone, and the keyword writer would drop the add (the file names them by their leaf).
        photo = self.photo(name, ["Parkrunner/" + WREN])
        self.files.keep_tags(photo, ["Parkrunner/" + WREN])
        self.face(photo, name=WREN, name_source="manual")
        return photo

    def test_the_dry_run_skips_and_counts_it_and_never_names_the_person(self):
        self.under_another_root()
        self.drifted("drift_a.jpg", who=ODA)
        said = self.run_cli()
        self.assertIn("2 photo(s) list a person from a face alone", said.output)
        self.assertIn("1 of the people: the photo's keywords already name them under a root", said.output)
        self.assertIn("tree question", said.output)
        self.assertIn("1 photo file(s) would be written", said.output)
        self.assertNotIn(WREN, said.output)
        self.assertNotIn("Parkrunner", said.output)

    def test_the_count_to_write_is_what_apply_writes_and_a_second_run_finds_none(self):
        photo = self.under_another_root()
        self.drifted("drift_b.jpg", who=ODA)
        dry = tags_from_faces.plan(self.library)
        wrote = self.run_cli("--apply")
        self.assertEqual(0, wrote.exit_code, wrote.output)
        self.assertIn("Wrote %d photo file(s)" % dry.details["counts"]["photos_to_write"], wrote.output)
        self.assertEqual(["Parkrunner/" + WREN], self.files.tags_of(photo), "the file that names the person was written")
        again = self.run_cli()
        self.assertIn("0 photo file(s) would be written", again.output)
        self.assertEqual(1, tags_from_faces.plan(self.library).details["counts"]["people_the_file_names_under_another_root"])


class GuessesAreLeftUnlessAsked(Mending):
    def guessed(self, name="guess.jpg"):
        photo = self.photo(name)
        self.files.keep_tags(photo, [])
        self.face(photo, name=WREN)             # no name_source: a guess of clustering or automatch
        return photo

    def test_a_person_only_a_guess_backs_is_skipped_and_counted(self):
        guess = self.guessed()
        self.drifted("drift_c.jpg", who=ODA)
        said = self.run_cli()
        self.assertIn("1 of the people: from a guess only", said.output)
        self.assertIn("1 photo file(s) would be written", said.output)
        applied = self.run_cli("--apply")
        self.assertEqual(0, applied.exit_code, applied.output)
        self.assertEqual([], self.files.tags_of(guess), "a guess was written into a photo file")

    def test_a_face_named_by_hand_beside_a_guess_of_the_same_person_counts_as_decided(self):
        photo = self.photo("both.jpg")
        self.files.keep_tags(photo, [])
        self.face(photo, name=WREN, name_source="manual")
        self.face(photo, box=(70, 10, 110, 60), name=WREN)
        self.assertEqual(1, tags_from_faces.plan(self.library).details["counts"]["people_to_write"])

    def test_guesses_asks_for_them_with_a_warning(self):
        guess = self.guessed("guess_b.jpg")
        said = self.run_cli("--guesses")
        self.assertIn("WARNING: --guesses", said.output)
        self.assertIn("decided reference", said.output)
        self.assertIn("1 photo file(s) would be written", said.output)
        applied = self.run_cli("--apply", "--guesses")
        self.assertEqual(0, applied.exit_code, applied.output)
        self.assertEqual([PEOPLE + WREN], self.files.tags_of(guess))


class TheApply(Mending):
    def test_it_writes_the_keyword_into_the_files_and_the_library_follows(self):
        photos = [self.drifted("drift_%d.jpg" % n) for n in range(3)]
        said = self.run_cli("--apply")
        self.assertEqual(0, said.exit_code, said.output)
        self.assertIn("Wrote 3 photo file(s) in 1 change(s)", said.output)
        for photo in photos:
            self.assertEqual([PEOPLE + WREN], self.files.tags_of(photo))
        self.assertEqual([(WREN, "keyword")] * 3, self.look("SELECT name, source FROM photo_people ORDER BY photo_id"))
        self.assertEqual((0, 0), checks.people_on_faces_alone(self.conn()))

    def test_a_chunk_is_one_change_of_the_journal(self):
        for n in range(5):
            self.drifted("drift_%d.jpg" % n)
        with mock.patch.object(face_people, "CHUNK", 2):
            said = self.run_cli("--apply")
        self.assertIn("5 photo file(s) in 3 change(s)", said.output)
        self.assertEqual([tags_from_faces.OPERATION] * 3, self.operations())
        self.assertIn("2 of 5 photo(s) done", said.output)
        self.assertIn("5 of 5 photo(s) done", said.output)

    def test_it_can_be_run_again_and_finds_nothing_more(self):
        self.drifted("drift_a.jpg")
        self.run_cli("--apply")
        writes = self.files.writes
        said = self.run_cli("--apply")
        self.assertIn("Nothing to write", said.output)
        self.assertEqual(writes, self.files.writes)

    def test_a_photo_that_cannot_be_written_is_counted_and_the_others_are_written(self):
        bad = self.drifted("drift_bad.jpg")
        good = self.drifted("drift_good.jpg", who=ODA)
        self.files.unreadable.add(paths.key(bad))
        said = self.run_cli("--apply")
        self.assertEqual(1, said.exit_code)
        self.assertIn("1 photo(s) could not be written", said.output)
        self.assertEqual([PEOPLE + ODA], self.files.tags_of(good))
        self.assertEqual(1, self.run_cli().output.count("1 photo file(s) would be written"), "the one left is still to do")
        self.assertNotIn("drift_bad", said.output)

    def test_a_file_changed_meanwhile_is_not_overwritten(self):
        photo = self.drifted("drift_race.jpg")
        self.files.on_read = lambda path, n: self.files.keep_tags(photo, ["Regatta"]) if n == 2 else None
        said = self.run_cli("--apply")
        self.assertEqual(["Regatta"], self.files.tags_of(photo), "the other program's change was overwritten")
        self.assertNotEqual(0, said.exit_code)

    def test_a_photo_with_another_person_by_keyword_keeps_it(self):
        photo = self.photo("two.jpg", [PEOPLE + ODA, "Regatta"])
        self.files.keep_tags(photo, [PEOPLE + ODA, "Regatta"])
        self.face(photo, name=WREN, name_source="manual")
        self.run_cli("--apply")
        self.assertEqual(sorted([PEOPLE + ODA, PEOPLE + WREN, "Regatta"]), sorted(self.files.tags_of(photo)))

    def conn(self):
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        self.addCleanup(conn.close)
        return conn


class TheDoctor(Mending):
    def test_it_counts_the_drift_and_does_not_call_it_broken(self):
        for n in range(2):
            self.drifted("drift_%d.jpg" % n)
        lines = []
        doctor.report(self.path, out=lines.append)
        said = "\n".join(lines)
        self.assertIn("photos listing a person from a face alone: 2, 2 people", said)
        self.assertIn("tags-from-faces", said)
        self.assertNotIn(WREN, said)
        # Reported, not a rule: mending it writes the owner's photo files, which only --apply does.
        self.assertNotIn("people_on_faces_alone", [rule.__name__ for rule in checks.RULES])

    def test_it_says_nothing_when_the_library_is_in_step(self):
        self.photo("fine.jpg", [PEOPLE + WREN])
        lines = []
        doctor.report(self.path, out=lines.append)
        self.assertNotIn("from a face alone", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
