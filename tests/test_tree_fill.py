"""`tagpup_cli.py tree-fill` (tagpup.services.tree_fill): the one-time command that gives a library's names and keywords their
nodes (owner, 2026-10-10, "every tag has an id").

The library is made as the old indexer left one: photos read with keywords no node of the tree is (a read made the node, then the
node is taken out), faces and listed people holding a name and no id (written by SQL as the old writers wrote them). Nothing is
seeded through the code under test.
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
from tagpup.store import db, people, taxonomy  # noqa: E402
from tagpup.services import tree_fill  # noqa: E402

PIER = "Trips/Harbour Walk/Pier"
ZED = "Zed Quill"
YARA = "Yara Quill"
ACCENT = chr(233)


class Legacy(unittest.TestCase, people_by_id.TwoSams):
    def setUp(self):
        self.make_library()
        self.third = os.path.join(os.path.dirname(self.photo), "regatta_003.jpg")
        self.fourth = os.path.join(os.path.dirname(self.photo), "regatta_004.jpg")
        self.keywords = [PIER, PIER.lower(), PIER.replace("/", chr(92)), PIER + "/", "Places/Caf" + ACCENT,
                         unicodedata.normalize("NFD", "Places/Caf" + ACCENT), "Family/Thackeray", "pets/max", "Wren Halloway",
                         "People/Kit Ardent"]
        people_by_id.write(self.path, self.old_library)

    def old_library(self, conn):
        photo_rows.add_read(conn, self.third, {"XMP:Subject": self.keywords})
        photo_rows.add_read(conn, self.fourth, {"XMP:PersonInImage": [YARA, "Sam", "Thackeray"]})
        # As the old indexer left them: no node for these keywords, nor for the person field's name, and the faces named by
        # SQL with a name and no id.
        for tag in (PIER, "Places/Caf" + ACCENT, "People/Kit Ardent", YARA):
            node = conn.execute("SELECT id FROM tag_taxonomy WHERE tag = ?", (tag,)).fetchone()
            while node is not None:
                parent = conn.execute("SELECT parent_id FROM tag_taxonomy WHERE id = ?", node).fetchone()[0]
                conn.execute("DELETE FROM tag_taxonomy WHERE id = ?", node)
                node = (parent,) if parent is not None and not conn.execute(
                    "SELECT 1 FROM tag_taxonomy WHERE parent_id = ?", (parent,)).fetchone() and parent not in self.roots(conn) else None
        conn.execute("UPDATE faces SET name = ?, tag_id = NULL WHERE id = ?", (ZED, self.faces[0]))
        conn.execute("UPDATE faces SET name = ?, tag_id = NULL WHERE id = ?", (ZED.lower(), self.faces[1]))
        conn.execute("UPDATE faces SET name = 'Sam', tag_id = NULL WHERE id = ?", (self.faces[2],))
        people.rebuild(conn)
        conn.execute("UPDATE photo_people SET tag_id = NULL WHERE tag_id NOT IN (SELECT id FROM tag_taxonomy)")

    @staticmethod
    def roots(conn):
        return {node for (node,) in conn.execute("SELECT id FROM tag_taxonomy WHERE tag IN ('Family', 'Trips', 'People', 'Pets', 'Places')")}

    def numbers(self):
        return {
            "nodes": people_by_id.look(self.path, "SELECT COUNT(*) FROM tag_taxonomy")[0][0],
            "faces_with_id": people_by_id.look(self.path, "SELECT COUNT(*) FROM faces WHERE tag_id IS NOT NULL")[0][0],
            "rows": people_by_id.look(self.path, "SELECT COUNT(*) FROM photo_tags")[0][0],
        }

    def count(self, tag):
        """How many nodes are `tag` without case (SQLite's lower is for ASCII only: compared here)."""
        return sum(1 for (each,) in people_by_id.look(self.path, "SELECT tag FROM tag_taxonomy") if each.lower() == tag.lower())

    def called(self, name):
        return people_by_id.look(self.path, "SELECT COUNT(*) FROM tag_taxonomy WHERE name = ?", (name,))[0][0]

    # ---- the rehearsal

    def test_a_rehearsal_reports_what_it_would_make_and_changes_nothing(self):
        before = self.numbers()
        report = tree_fill.run(self.library)
        self.assertEqual(before, self.numbers())
        self.assertEqual(before["nodes"] + report["made"]["nodes"] - report["made"]["nodes"], self.numbers()["nodes"])
        self.assertEqual({"without_a_node": 3, "left_a_persons_name": 1}, {key: report["keywords"][key] for key in ("without_a_node", "left_a_persons_name")})
        self.assertEqual(3, report["names"]["without_a_node"], "Zed Quill (in two spellings), Yara Quill and Kit Ardent")
        self.assertEqual({"several": 1, "branch": 1}, {"several": report["names"]["left_several_people"], "branch": report["names"]["left_a_group"]})
        self.assertGreater(report["made"]["nodes"], 3)
        self.assertEqual(2, report["linked"]["faces"])

    # ---- the apply

    def test_apply_makes_the_nodes_links_the_rows_and_says_what_changed(self):
        before = self.numbers()
        report = tree_fill.run(self.library, apply=True)
        after = self.numbers()
        self.assertEqual(after["nodes"] - before["nodes"], report["made"]["nodes"])
        self.assertEqual(after["faces_with_id"] - before["faces_with_id"], report["linked"]["faces"])
        self.assertEqual(after["rows"] - before["rows"], report["keyword_rows_added"])
        for tag in (PIER, "Trips/Harbour Walk", "Places/Caf" + ACCENT, "People/Kit Ardent"):
            self.assertEqual(1, self.count(tag), tag)
        self.assertEqual(1, self.count(ZED))
        self.assertEqual(1, self.count(YARA))
        self.assertEqual(1, self.called("Kit Ardent"), "the keyword's node, and no second for the name")
        zed = self.node(ZED)
        self.assertEqual([zed, zed], [self.face(self.faces[0])[0], self.face(self.faces[1])[0]])
        self.assertEqual([(zed, ZED, "face")], [row for row in self.listed(self.photo) if row[1] == ZED])
        self.assertEqual([(self.node(YARA), YARA, "keyword")], self.listed(self.fourth)[:1])
        self.assertEqual(0, people_by_id.look(self.path, "SELECT COUNT(*) FROM photo_tags pt LEFT JOIN tag_taxonomy t ON t.id = pt.tag_id WHERE t.id IS NULL")[0][0])
        self.assertEqual(self.node("People/Kit Ardent"), people_by_id.look(
            self.path, "SELECT t.id FROM photo_tags pt JOIN photos p ON p.id = pt.photo_id JOIN tag_taxonomy t ON t.id = pt.tag_id"
                       " WHERE t.tag = 'People/Kit Ardent'")[0][0])

    def test_a_name_that_is_a_keyword_made_person_is_that_node_and_no_second(self):
        def name_kit(conn):
            conn.execute("UPDATE faces SET name = 'Kit Ardent', tag_id = NULL WHERE id = ?", (self.faces[0],))
            people.rebuild(conn)
        people_by_id.write(self.path, name_kit)
        tree_fill.run(self.library, apply=True)
        self.assertEqual(1, self.called("Kit Ardent"), "the keyword's People/Kit Ardent is the node, not a second at the top")
        self.assertEqual(self.node("People/Kit Ardent"), self.face(self.faces[0])[0])

    def test_the_names_left_stay_as_they_were_and_get_no_node(self):
        tree_fill.run(self.library, apply=True)
        self.assertEqual(0, self.count("Sam"), "two people have the name")
        self.assertEqual(0, self.count("Thackeray"), "a group has it")
        self.assertEqual(None, self.face(self.faces[2])[0])
        self.assertEqual(0, self.count("Wren Halloway"), "a bare keyword that is a person's name is their keyword")

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
        self.assertEqual((0, 0, 0), (again["made"]["nodes"], again["linked"]["faces"], again["keyword_rows_added"]))
        self.assertEqual(0, again["keywords"]["without_a_node"])
        self.assertEqual(0, again["names"]["without_a_node"])

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
        self.assertEqual(None, self.face(self.faces[0])[0])

    def test_two_libraries_do_not_share_it(self):
        other = self.home.library("other.db")
        from tagpup.store import schema
        schema.ensure(other)
        tree_fill.run(self.library, apply=True)
        self.assertEqual([], people_by_id.look(other, "SELECT tag FROM tag_taxonomy WHERE lower(tag) = lower(?)", (PIER,)))

    # ---- the command

    def cli(self, *args):
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.path, "tree-fill", *args])
        self.assertEqual(0, result.exit_code, result.output)
        return result.output

    def test_the_command_is_a_dry_run_by_default_and_prints_no_name(self):
        before = self.numbers()
        said = self.cli()
        self.assertEqual(before, self.numbers())
        self.assertIn("Would make", said)
        self.assertIn("Nothing changed", said)
        self.assertNotIn(ZED, said)
        self.assertNotIn("Harbour", said)
        self.assertIn(ZED, self.cli("--reveal"))

    def test_apply_backs_up_first_then_makes_and_says_it_is_not_journaled(self):
        library = self.library
        said = self.cli("--apply")
        self.assertIn("Backed up to", said)
        self.assertIn("not journaled", said)
        self.assertIn("Made", said)
        backups = [name for name in os.listdir(library.backups) if "before-tree-fill" in name]
        self.assertEqual(1, len(backups), backups)
        self.assertEqual(1, self.count(ZED))
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
        self.assertEqual((0, 0), (report["made"]["nodes"], report["names"]["without_a_node"]))
        self.assertEqual([], db.connect(db.readonly_uri(path), uri=True).execute("SELECT 1 FROM tag_taxonomy").fetchall())


if __name__ == "__main__":
    unittest.main()
