"""`tagpup_cli.py tree-fill` (tagpup.services.tree_fill): the one-time command that gives a library's file keywords their nodes
(owner, 2026-10-10, "every tag has an id"; 2026-10-11, a keyword is its path).

The library is made as the old indexer left one: photos read with keywords no node of the tree is (a read made the node, then the
node is taken out). Nothing is seeded through the code under test.
"""
import os
import sys
import unicodedata
import unittest
from unittest import mock

from click.testing import CliRunner

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import people_by_id  # noqa: E402
import photo_rows  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.store import db, taxonomy  # noqa: E402
from tagpup.services import tree_fill  # noqa: E402

PIER = "Trips/Harbour Walk/Pier"
ACCENT = chr(233)


class Legacy(unittest.TestCase, people_by_id.TwoSams):
    def setUp(self):
        self.make_library()
        self.third = os.path.join(os.path.dirname(self.photo), "regatta_003.jpg")
        self.keywords = [PIER, PIER.lower(), PIER.replace("/", chr(92)), PIER + "/", "Places/Caf" + ACCENT,
                         unicodedata.normalize("NFD", "Places/Caf" + ACCENT), "Family/Thackeray", "pets/max", "Wren Halloway",
                         "People/Kit Ardent"]
        people_by_id.write(self.path, self.old_library)

    def old_library(self, conn):
        photo_rows.add_read(conn, self.third, {"XMP:Subject": self.keywords})
        # As the old indexer left them: no node for these keywords.
        for tag in (PIER, "Places/Caf" + ACCENT, "People/Kit Ardent", "Wren Halloway"):
            node = conn.execute("SELECT id FROM tag_taxonomy WHERE tag = ?", (tag,)).fetchone()
            while node is not None:
                parent = conn.execute("SELECT parent_id FROM tag_taxonomy WHERE id = ?", node).fetchone()[0]
                conn.execute("DELETE FROM tag_taxonomy WHERE id = ?", node)
                node = (parent,) if parent is not None and not conn.execute(
                    "SELECT 1 FROM tag_taxonomy WHERE parent_id = ?", (parent,)).fetchone() and parent not in self.roots(conn) else None

    @staticmethod
    def roots(conn):
        return {node for (node,) in conn.execute("SELECT id FROM tag_taxonomy WHERE tag IN ('Family', 'Trips', 'People', 'Pets', 'Places')")}

    def numbers(self):
        return {
            "nodes": people_by_id.look(self.path, "SELECT COUNT(*) FROM tag_taxonomy")[0][0],
            "rows": people_by_id.look(self.path, "SELECT COUNT(*) FROM photo_tags")[0][0],
        }

    def count(self, tag):
        """How many nodes are `tag` without case (SQLite's lower is for ASCII only: compared here)."""
        return sum(1 for (each,) in people_by_id.look(self.path, "SELECT tag FROM tag_taxonomy") if each.lower() == tag.lower())

    # ---- the rehearsal

    def test_a_rehearsal_reports_what_it_would_make_and_changes_nothing(self):
        before = self.numbers()
        report = tree_fill.run(self.library)
        self.assertEqual(before, self.numbers())
        self.assertEqual(4, report["keywords"]["without_a_node"], "the pier, the cafe, Kit Ardent and the bare name")
        self.assertGreater(report["made"]["nodes"], 4)

    # ---- the apply

    def test_apply_makes_the_nodes_and_says_what_changed(self):
        before = self.numbers()
        report = tree_fill.run(self.library, apply=True)
        after = self.numbers()
        self.assertEqual(after["nodes"] - before["nodes"], report["made"]["nodes"])
        self.assertEqual(after["rows"] - before["rows"], report["keyword_rows_added"])
        for tag in (PIER, "Trips/Harbour Walk", "Places/Caf" + ACCENT, "People/Kit Ardent"):
            self.assertEqual(1, self.count(tag), tag)
        self.assertEqual(0, people_by_id.look(self.path, "SELECT COUNT(*) FROM photo_tags pt LEFT JOIN tag_taxonomy t ON t.id = pt.tag_id WHERE t.id IS NULL")[0][0])

    def test_a_bare_keyword_that_is_a_persons_name_is_a_node_of_its_own_and_a_person_nobody(self):
        tree_fill.run(self.library, apply=True)
        self.assertEqual(1, self.count("Wren Halloway"), "one at the top, beside the person filed under People")
        self.assertEqual([(0,)], people_by_id.look(self.path, "SELECT has_face FROM tag_taxonomy WHERE tag = 'Wren Halloway'"))
        self.assertEqual([], [row for row in self.listed(self.third) if row[1] == "Wren Halloway"])

    def test_a_keyword_spelled_otherwise_is_one_node(self):
        tree_fill.run(self.library, apply=True)
        self.assertEqual(1, self.count(PIER))
        cafes = [tag for (tag,) in people_by_id.look(self.path, "SELECT tag FROM tag_taxonomy") if tag.lower().startswith("places/caf")]
        self.assertEqual(1, len(cafes), cafes)
        self.assertEqual(1, self.count("Family/Thackeray"), "a group's name is the group")
        self.assertEqual(1, self.count("Pets/Max"))

    def test_run_twice_the_second_makes_nothing(self):
        tree_fill.run(self.library, apply=True)
        held = self.numbers()
        again = tree_fill.run(self.library, apply=True)
        self.assertEqual(held, self.numbers())
        self.assertEqual((0, 0), (again["made"]["nodes"], again["keyword_rows_added"]))
        self.assertEqual(0, again["keywords"]["without_a_node"])

    def test_a_failure_part_way_leaves_nothing_made(self):
        before = self.numbers()
        real = taxonomy.ensure_node
        calls = []

        def fails_on_the_third(*args, **kwargs):
            calls.append(args[1])
            if len(calls) == 3:
                raise RuntimeError("the disk went")
            return real(*args, **kwargs)
        with mock.patch.object(taxonomy, "ensure_node", fails_on_the_third), self.assertRaises(RuntimeError):
            tree_fill.run(self.library, apply=True)
        self.assertEqual(3, len(calls))
        self.assertEqual(before, self.numbers())
        self.assertEqual(0, self.count(PIER))

    def test_two_libraries_do_not_share_it(self):
        other = self.home.library("other.db")
        from tagpup.store import schema
        schema.ensure(other)
        tree_fill.run(self.library, apply=True)
        self.assertEqual([], people_by_id.look(other, "SELECT tag FROM tag_taxonomy WHERE lower(tag) = lower(?)", (PIER,)))

    # ---- a root the tree lacks, and a person a file's keyword would cover

    def test_skip_root_leaves_that_roots_keywords_out(self):
        report = tree_fill.run(self.library, apply=True, skip_roots=("TRIPS",))
        self.assertEqual(1, report["keywords"]["skipped_by_root"])
        self.assertEqual(0, self.count(PIER))
        self.assertEqual(1, self.count("Places/Caf" + ACCENT))
        again = tree_fill.run(self.library)
        self.assertEqual(1, again["keywords"]["without_a_node"], "the skipped keyword is still to be made, when asked")

    def many_under_one_root(self):
        def read(conn):
            photo_rows.add_read(conn, self.third.replace("003", "009"), {"XMP:Subject": ["Gone/Kw%d" % n for n in range(12)]})
            for n in range(12):
                conn.execute("DELETE FROM tag_taxonomy WHERE tag = ?", ("Gone/Kw%d" % n,))
            conn.execute("DELETE FROM tag_taxonomy WHERE tag = 'Gone'")
        people_by_id.write(self.path, read)

    def test_a_root_most_keywords_share_that_the_tree_lacks_is_said_prominently_and_named_only_with_reveal(self):
        self.many_under_one_root()
        said = self.cli()
        self.assertIn("12 keyword(s) share one root that is not in the tag tree", said)
        self.assertIn("--skip-root", said)
        self.assertNotIn("Gone", said)
        self.assertIn("root not in the tree: Gone (12 keyword(s))", self.cli("--reveal"))
        skipped = self.cli("--skip-root", "gone")
        self.assertNotIn("share one root", skipped)
        self.assertIn("12 keyword(s) under a --skip-root", skipped)

    def test_a_keyword_under_a_person_that_is_used_is_left_and_counted(self):
        node = self.node(people_by_id.SAM_T)
        people_by_id.write(self.path, lambda conn: photo_rows.add_read(
            conn, self.third.replace("003", "008"), {"XMP:Subject": [people_by_id.SAM_T + "/Swim Team"]}))
        report = tree_fill.run(self.library, apply=True)
        self.assertEqual(1, report["keywords"]["left_under_a_person"])
        self.assertEqual(0, self.count(people_by_id.SAM_T + "/Swim Team"))
        self.assertIsNotNone(node)
        self.assertIn("under a person", self.cli())

    # ---- the command

    def cli(self, *args):
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.path, "tree-fill", *args])
        self.assertEqual(0, result.exit_code, result.output)
        return result.output

    def test_the_command_is_a_dry_run_by_default_and_prints_no_keyword(self):
        before = self.numbers()
        said = self.cli()
        self.assertEqual(before, self.numbers())
        self.assertIn("Would make", said)
        self.assertIn("Nothing changed", said)
        self.assertNotIn("Kit Ardent", said)
        self.assertNotIn("Harbour", said)
        self.assertIn("Kit Ardent", self.cli("--reveal"))

    def test_apply_backs_up_first_then_makes_and_says_it_is_not_journaled(self):
        library = self.library
        said = self.cli("--apply")
        self.assertIn("Backed up to", said)
        self.assertIn("not journaled", said)
        self.assertIn("Made", said)
        backups = [name for name in os.listdir(library.backups) if "before-tree-fill" in name]
        self.assertEqual(1, len(backups), backups)
        self.assertEqual(1, self.count("People/Kit Ardent"))
        self.assertIn("Nothing to make", self.cli("--apply"))
        self.assertEqual(1, len([name for name in os.listdir(library.backups) if "before-tree-fill" in name]), "none for nothing")


class TheCountsAreTheLibrarys(unittest.TestCase):
    """A library with nothing to make says so and writes nothing."""

    def test_an_empty_library(self):
        import own_home
        from tagpup.core.library import Library
        from tagpup.store import schema
        home = own_home.for_test(self)
        path = home.library("empty.db")
        schema.ensure(path)
        report = tree_fill.run(Library(path), apply=True)
        self.assertEqual((0, 0), (report["made"]["nodes"], report["keywords"]["without_a_node"]))
        self.assertEqual([], db.connect(db.readonly_uri(path), uri=True).execute("SELECT 1 FROM tag_taxonomy").fetchall())


if __name__ == "__main__":
    unittest.main()
