"""tagpup.services.tags: the tag tree's edits.

Rewriting the photo files is tagpup.services.tagging.replace_tag's, tested with real
files in test_service_replace_tag.py and, through the routes, test_taxonomy_lifecycle.py.
It is stood in for here: what is checked is what each edit does to the tree, the faces
and the people lists, and what it asks to have rewritten.
"""
import contextlib
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402

from tagpup.core.result import NotFound, Result  # noqa: E402
from tagpup.services import tags  # noqa: E402
from tagpup.store import taxonomy  # noqa: E402


class TreeCase(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.rewrites = []
        self.unwritable = set()

        def replace_tag(library, photo_paths, old, new, exiftool_path):
            self.rewrites.append((sorted(photo_paths), old, new))
            result = Result(attempted=len(photo_paths),
                            changed=len([p for p in photo_paths if p not in self.unwritable]))
            for path in photo_paths:
                if path in self.unwritable:
                    result.fail(path, "the file is open elsewhere")
            return result

        patcher = mock.patch("tagpup.services.tagging.replace_tag", side_effect=replace_tag)
        patcher.start()
        self.addCleanup(patcher.stop)

    def node(self, path, has_face=0):
        """Add `path` to the tree, and return the id of `path`'s node."""
        tags.create(self.lib.library, path, has_face=has_face)
        return self.id_of(path)

    def id_of(self, path):
        return taxonomy.find(self.lib.library.path, path)["id"]

    def photo(self, name, tags_=(), people=()):
        path = self.lib.photo(name)
        self.lib.add_row(path, tags=tags_)
        self.lib.execute("UPDATE photos SET people = ? WHERE path = ?", (json.dumps(list(people)), path))
        return path

    def tree(self):
        return {row[0]: row[1:] for row in self.lib.rows(
            "SELECT tag, name, has_face, hidden_from_autocomplete FROM tag_taxonomy")}


class Creating(TreeCase):
    def test_each_level_is_a_node_and_the_json_file_follows(self):
        result = tags.create(self.lib.library, "Crew/Divers")
        self.assertEqual((result.ok, result.changed, result.details["tag"]), (True, 1, "Crew/Divers"))
        self.assertEqual(sorted(self.tree()), ["Crew", "Crew/Divers"])
        with open(taxonomy.json_file(self.lib.library.path), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["paths"], ["Crew", "Crew/Divers"])

    def test_below_a_parent_typed_whole_or_not(self):
        crew = self.node("Crew")
        tags.create(self.lib.library, "Divers", parent_id=crew)
        again = tags.create(self.lib.library, "Crew/Divers", parent_id=crew)
        self.assertEqual((again.changed, sorted(self.tree())), (0, ["Crew", "Crew/Divers"]))

    def test_a_tag_that_may_not_be_set_or_an_empty_one_is_refused(self):
        for name in ("", "Crew|Divers"):
            self.assertTrue(tags.create(self.lib.library, name).refused)
        self.assertEqual(self.tree(), {})

    def test_below_a_parent_that_is_not_there_is_not_found(self):
        with self.assertRaises(NotFound):
            tags.create(self.lib.library, "Divers", parent_id=999)


class Marking(TreeCase):
    def test_a_branch_and_nothing_beside_it(self):
        self.node("Crew/Divers/Jane Olsen")
        self.node("Crewmates/Rowers")
        result = tags.set_flags(self.lib.library, self.id_of("Crew"), has_face=1, hidden=1)
        self.assertEqual(result.changed, 3)
        self.assertEqual({tag: row[1:] for tag, row in self.tree().items()}, {
            "Crew": (1, 1), "Crew/Divers": (1, 1), "Crew/Divers/Jane Olsen": (1, 1),
            "Crewmates": (0, 0), "Crewmates/Rowers": (0, 0)})

    def test_a_node_that_is_not_there_is_not_found(self):
        with self.assertRaises(NotFound):
            tags.set_flags(self.lib.library, 999, has_face=1)


class Deleting(TreeCase):
    def setUp(self):
        super().setUp()
        self.node("Activity/Rowing/Juniors")
        self.rowing = self.id_of("Activity/Rowing")
        self.on_it = self.photo("a.jpg", ["Activity/Rowing"])
        self.under_it = self.photo("b.jpg", ["Activity / Rowing / Juniors"])
        self.beside_it = self.photo("c.jpg", ["Activity/Rowingclub"])

    def test_what_it_would_touch(self):
        usage = tags.usage(self.lib.library, self.rowing)
        self.assertEqual((usage["tag"], usage["used"], usage["count"]), ("Activity/Rowing", True, 2))
        self.assertEqual(sorted(usage["affected_photos"]), sorted([self.on_it, self.under_it]))

    def test_removed_from_its_photos_and_from_the_tree(self):
        result = tags.delete(self.lib.library, self.rowing, "remove", None, "exiftool")
        self.assertTrue(result.ok, result.message())
        self.assertEqual(self.rewrites, [(sorted([self.on_it, self.under_it]), "Activity/Rowing", None)])
        self.assertEqual(sorted(self.tree()), ["Activity"])

    def test_moved_to_a_tag_under_which_its_branch_goes_on(self):
        """The photos carrying Activity/Rowing/Juniors now carry Sport/Rowing/Juniors, and
        so does the tree (docs/findings.md, #38)."""
        tags.delete(self.lib.library, self.rowing, "move", " Sport / Rowing ", "exiftool")
        self.assertEqual(self.rewrites[0][1:], ("Activity/Rowing", "Sport/Rowing"))
        self.assertEqual(sorted(self.tree()), ["Activity", "Sport", "Sport/Rowing", "Sport/Rowing/Juniors"])

    def test_it_cannot_be_moved_under_itself(self):
        result = tags.delete(self.lib.library, self.rowing, "move", "Activity/Rowing/Masters", "exiftool")
        self.assertTrue(result.refused)
        self.assertEqual(self.rewrites, [])

    def test_moving_needs_somewhere_to_move_to(self):
        self.assertTrue(tags.delete(self.lib.library, self.rowing, "move", "", "exiftool").refused)
        self.assertIn("Activity/Rowing", self.tree())

    def test_a_tag_a_photo_still_carries_stays(self):
        self.unwritable.add(self.under_it)
        result = tags.delete(self.lib.library, self.rowing, "remove", None, "exiftool")
        self.assertFalse(result.ok)
        self.assertEqual((result.details["photos_affected"], result.details["photos_rewritten"]), (2, 1))
        self.assertIn("Activity/Rowing/Juniors", self.tree())


class Renaming(TreeCase):
    def test_the_branch_and_its_photos(self):
        self.node("Crew/Divers/Juniors")
        crew = self.id_of("Crew/Divers")
        photo = self.photo("a.jpg", ["Crew/Divers/Juniors"])
        result = tags.rename(self.lib.library, crew, "Swimmers", "exiftool")
        self.assertTrue(result.ok, result.message())
        self.assertEqual(sorted(self.tree()), ["Crew", "Crew/Swimmers", "Crew/Swimmers/Juniors"])
        self.assertEqual(self.tree()["Crew/Swimmers"][0], "Swimmers")
        self.assertEqual(self.rewrites, [([photo], "Crew/Divers", "Crew/Swimmers")])

    def test_a_person_is_renamed_on_their_faces_and_in_each_photos_people(self):
        person = self.node("People/Rowan Thackeray")
        photo = self.photo("a.jpg", ["People/Rowan Thackeray"],
                           people=["Rowan Thackeray", "Ada Pembrook", "Rowan Thackeray-Vale"])
        self.lib.add_face(photo, [0, 0, 10, 10], name="Rowan Thackeray")
        result = tags.rename(self.lib.library, person, "Rowan Thackeray-Vale", "exiftool")
        self.assertEqual(result.details["faces_renamed"], 1)
        self.assertEqual(self.lib.rows("SELECT name FROM faces"), [("Rowan Thackeray-Vale",)])
        self.assertEqual(json.loads(self.lib.rows("SELECT people FROM photos")[0][0]),
                         ["Rowan Thackeray-Vale", "Ada Pembrook"])

    def test_a_keyword_that_holds_no_faces_leaves_faces_alone(self):
        rowing = self.node("Activity/Rowan")
        photo = self.photo("a.jpg", ["Activity/Rowan"], people=["Rowan"])
        self.lib.add_face(photo, [0, 0, 10, 10], name="Rowan")
        tags.rename(self.lib.library, rowing, "Rowing", "exiftool")
        self.assertEqual(self.lib.rows("SELECT name FROM faces"), [("Rowan",)])

    def test_onto_a_node_that_is_there_is_refused(self):
        crew = self.node("Crew/Divers")
        self.node("Crew/Swimmers")
        self.assertTrue(tags.rename(self.lib.library, crew, "Swimmers", "exiftool").refused)
        self.assertEqual(self.rewrites, [])

    def test_to_the_name_it_has_changes_nothing(self):
        crew = self.node("Crew")
        result = tags.rename(self.lib.library, crew, "Crew", "exiftool")
        self.assertEqual((result.ok, result.changed, self.rewrites), (True, 0, []))

    def test_a_name_holding_a_separator_is_refused(self):
        crew = self.node("Crew")
        self.assertTrue(tags.rename(self.lib.library, crew, "Crew/Divers", "exiftool").refused)


class Merging(TreeCase):
    """TagTuner's Rename, Merge and Retire (docs/findings.md, #38)."""

    def setUp(self):
        super().setUp()
        for path in ("Crew/Divers/Jane Olsen", "Crew/Divers/Ann Kerr", "Crew/Swimmers/Ann Kerr"):
            self.node(path)
        self.jane = self.id_of("Crew/Divers/Jane Olsen")

    def merge(self, source, target, **options):
        return tags.merge(self.lib.library, source, target, "exiftool", **options)

    def test_the_plan_counts_the_photos_carrying_a_tag_under_it_and_writes_nothing(self):
        on_it = self.photo("a.jpg", ["Crew/Divers"])
        under_it = self.photo("b.jpg", ["Crew/Divers/Jane Olsen", "Crew/Swimmers"])
        plan = self.merge("Crew/Divers", "Crew/Swimmers").details
        self.assertEqual((plan["photos"], plan["photos_already_carrying_the_target"],
                          plan["taxonomy_rows_to_drop"], plan["applied"]), (2, 1, 3, False))
        self.assertEqual(sorted(plan["examples"]), sorted(os.path.basename(p) for p in (on_it, under_it)))
        self.assertEqual(self.rewrites, [])
        self.assertIn("Crew/Divers", self.tree())

    def test_the_branch_joins_the_one_there_and_what_is_free_moves(self):
        result = self.merge("Crew/Divers", "Crew/Swimmers", apply=True)
        self.assertTrue(result.details["applied"], result.message())
        self.assertEqual(sorted(self.tree()), ["Crew", "Crew/Swimmers", "Crew/Swimmers/Ann Kerr",
                                               "Crew/Swimmers/Jane Olsen"])
        self.assertEqual(self.id_of("Crew/Swimmers/Jane Olsen"), self.jane, "a moved node keeps its id")

    def test_renaming_a_tag_no_photo_carries_keeps_it_under_its_new_name(self):
        self.merge("Crew/Divers", "Crew/Rowers", apply=True)
        self.assertIn("Crew/Rowers/Jane Olsen", self.tree())

    def test_retiring_takes_the_branch_out_and_its_cached_embedding(self):
        self.lib.execute("INSERT INTO tag_embeddings (tag, prompt, model_name, pretrained, embedding)"
                         " VALUES ('Crew/Divers', 'p', 'm', 'x', x'00')")
        self.assertEqual(self.merge("Crew/Divers", None, retire=True).details["embeddings_to_drop"], 1)
        self.merge("Crew/Divers", None, retire=True, apply=True)
        self.assertEqual(sorted(self.tree()), ["Crew", "Crew/Swimmers", "Crew/Swimmers/Ann Kerr"])
        self.assertEqual(self.lib.rows("SELECT COUNT(*) FROM tag_embeddings"), [(0,)])

    def test_a_photo_not_rewritten_keeps_the_tag_in_the_tree(self):
        self.unwritable.add(self.photo("a.jpg", ["Crew/Divers/Jane Olsen"]))
        result = self.merge("Crew/Divers", "Crew/Swimmers", apply=True)
        self.assertFalse(result.ok)
        self.assertFalse(result.details["applied"])
        self.assertIn("Crew/Divers/Jane Olsen", self.tree())

    def test_what_cannot_be_asked(self):
        for source, target, options in (("", "X", {}), ("Crew", "", {}), ("Crew", "Crew", {}),
                                        ("Crew", "Crew|X", {}), ("Crew", "Crew/Divers", {})):
            self.assertTrue(self.merge(source, target, **options).refused, (source, target))


class RenamingAPerson(TreeCase):
    def setUp(self):
        super().setUp()
        self.node("People/Rowan Thackeray")
        self.node("Family/Rowan Thackeray")

    def rename(self, old, new):
        return tags.rename_person(self.lib.library, old, new, "exiftool")

    def test_every_node_filed_under_the_name_and_the_photos_carrying_them(self):
        photo = self.photo("a.jpg", ["People/Rowan Thackeray"])
        result = self.rename("Rowan Thackeray", "Rowan Vale")
        self.assertTrue(result.ok, result.message())
        self.assertEqual(sorted(self.tree()), ["Family", "Family/Rowan Vale", "People", "People/Rowan Vale"])
        self.assertEqual(self.rewrites, [([photo], "People/Rowan Thackeray", "People/Rowan Vale")])
        self.assertEqual(result.details["photos_affected"], 1)

    def test_faces_whatever_their_case_and_each_photos_people(self):
        photo = self.photo("a.jpg", people=["rowan thackeray", "Ada Pembrook"])
        self.lib.add_face(photo, [0, 0, 10, 10], name="Rowan Thackeray")
        self.lib.add_face(photo, [10, 10, 20, 20], name="rowan thackeray")
        result = self.rename("Rowan Thackeray", "Rowan Vale")
        self.assertEqual(result.details["faces_renamed"], 2)
        self.assertEqual(self.lib.rows("SELECT DISTINCT name FROM faces"), [("Rowan Vale",)])
        self.assertEqual(json.loads(self.lib.rows("SELECT people FROM photos")[0][0]),
                         ["Rowan Vale", "Ada Pembrook"])

    def test_into_a_name_filed_already_the_two_become_one(self):
        self.node("People/Rowan Vale")
        photo = self.photo("a.jpg", ["People/Rowan Thackeray"])
        self.rename("Rowan Thackeray", "Rowan Vale")
        self.assertEqual(sorted(self.tree()), ["Family", "Family/Rowan Vale", "People", "People/Rowan Vale"])
        self.assertIn(([photo], "People/Rowan Thackeray", "People/Rowan Vale"), self.rewrites)

    def test_what_cannot_be_asked(self):
        for old, new in (("", "X"), ("Rowan Thackeray", tags.UNMATCHED), ("Rowan Thackeray", "A/B")):
            self.assertTrue(self.rename(old, new).refused, (old, new))
        self.assertEqual(self.rename("Rowan Thackeray", "Rowan Thackeray").changed, 0)


class AnIndexBehindItsFiles(unittest.TestCase):
    """docs/findings.md, #44. The photos to change come from the index; the rewrite
    reads each file and skips one whose file no longer carries the tag. That photo was
    counted as one that could not be rewritten. Here the rewrite is the real one, and
    only the files are stood in for."""

    def setUp(self):
        self.lib = TempLibrary(self)
        self.carries = self.lib.photo("a.jpg")
        self.stale = self.lib.photo("b.jpg")
        for path in (self.carries, self.stale):
            self.lib.add_row(path, tags=["Activity/Rowing"])
        tags.create(self.lib.library, "Activity/Rowing")
        self.node = taxonomy.find(self.lib.library.path, "Activity/Rowing")["id"]
        # b's file lost the tag to another program; its row still says it has it.
        files = {self.carries: ["Activity/Rowing"], self.stale: []}

        @contextlib.contextmanager
        def session(**_options):
            yield object()

        for patcher in (mock.patch("tagpup.files.exiftool_session.ExifToolSession", session),
                        mock.patch("tagpup.files.keywords.tags_in_file", lambda et, p: files[p]),
                        mock.patch("tagpup.files.keywords.write_keywords",
                                   lambda et, p, t: files.__setitem__(p, list(t)) or (t, t))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_delete_takes_the_tag_out_of_the_tree(self):
        result = tags.delete(self.lib.library, self.node, "remove", None, "exiftool")
        self.assertTrue(result.ok, result.message())
        self.assertIsNone(taxonomy.find(self.lib.library.path, "Activity/Rowing"))
        self.assertEqual(self.lib.rows("SELECT tags FROM photos WHERE path = ?", (self.stale,)), [("[]",)])

    def test_a_merge_is_applied(self):
        result = tags.merge(self.lib.library, "Activity/Rowing", "Activity/Sculling", "exiftool", apply=True)
        self.assertTrue(result.details["applied"], result.message())
        self.assertNotIn("error", result.details)

    def test_a_rename_warns_of_nothing(self):
        result = tags.rename(self.lib.library, self.node, "Sculling", "exiftool")
        self.assertTrue(result.ok, result.message())


class TheTreeView(TreeCase):
    def test_counts_a_photo_toward_each_level_above_its_tags(self):
        self.node("Activity/Hiking")
        self.photo("a.jpg", ["Activity/Hiking"])
        counts = {node["tag"]: node["usage_count"] for node in tags.tree(self.lib.library)}
        self.assertEqual(counts, {"Activity": 1, "Activity/Hiking": 1})

    def test_a_photo_counts_once_toward_a_node_however_many_of_its_tags_are_under_it(self):
        """docs/findings.md, #41: a photo counted once for each of its tags under a node."""
        self.node("Family/Immediate/Ada Pembrook")
        self.node("Family/Immediate/Wren Halloway")
        self.photo("a.jpg", ["Family/Immediate/Ada Pembrook", "Family/Immediate/Wren Halloway"])
        counts = {node["tag"]: node["usage_count"] for node in tags.tree(self.lib.library)}
        self.assertEqual(counts, {"Family": 1, "Family/Immediate": 1,
                                  "Family/Immediate/Ada Pembrook": 1, "Family/Immediate/Wren Halloway": 1})

    def test_a_node_whose_parent_is_missing_is_put_right(self):
        self.lib.execute("INSERT INTO tag_taxonomy (tag, name, parent_id, has_face)"
                         " VALUES ('Trips/Boston MA', 'Boston MA', NULL, 0)")
        nodes = {node["tag"]: node for node in tags.tree(self.lib.library)}
        self.assertEqual(nodes["Trips/Boston MA"]["parent_id"], nodes["Trips"]["id"])


if __name__ == "__main__":
    unittest.main()
