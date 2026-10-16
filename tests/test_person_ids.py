"""People by their node's id (tagpup.store.person_ids; docs/ARCHITECTURE.md, "Identity by id").

`faces.tag_id` and `photo_people.tag_id` sit beside `name` and hold the id of the person node a row means -- NULL for a name
no person node is called, or that two are and nothing said which. Stage 1: they are filled, one lookup per distinct
name. Stage 2: the id is the PERSON and the name beside it a cache of the node's leaf (a rename of the node changes it, the
id never); a writer takes the person by id (or by a name that is one person's), a tree edit that renames, moves or merges a
person keeps their faces by id, and a node that faces name is not deleted. Each case here is a way that goes wrong: the
tree renamed or edited between two writes, a tag the tree lacks, a node deleted while faces name it, two libraries naming one person with
different ids.

Rows are made as the code that makes them makes them: a face by tagpup.store.faces, a photo by the
indexer's record (tests/photo_rows.py), the tree by tagpup.store.taxonomy. (The migration that added the columns, 21, is in
the baseline; its tests of a library at 20 went with it.)
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.store import checks, db, faces, journal, people, person_ids, schema, taxonomy  # noqa: E402

WREN = "Wren Halloway"
ODA = "Oda Castellane"
ASH = "Ash Corrin"          # filed twice: the ambiguous person path (docs/findings.md, #27)
NOBODY = "Tamsin Vey"       # on a face, filed nowhere


def look(path, sql, params=()):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def write(path, operation):
    return db.write_with_connection(path, operation)


def node_id(path, tag):
    found = look(path, "SELECT id FROM tag_taxonomy WHERE tag = ?", (tag,))
    return found[0][0] if found else None


def ids_of(path, table="faces"):
    """{name: {tag_id}} of the named rows of `table`."""
    found = {}
    for name, tag_id in look(path, "SELECT name, tag_id FROM %s WHERE name IS NOT NULL" % table):
        found.setdefault(name, set()).add(tag_id)
    return found


def in_step(path):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return [person_ids.out_of_step(conn, table).rows for table in person_ids.TABLES]
    finally:
        conn.close()


TREE = ("People/" + WREN, "Family/Coast/" + ODA, "People/" + ASH, "Friends/" + ASH, "Trips/Coast")


def seed_tree(conn):
    """The tree as TagTuner leaves it: People and Family hold faces, Friends too, Trips does not."""
    with people.tree_edit(conn):
        for root, has_face in (("People", 1), ("Family", 1), ("Friends", 1), ("Trips", 0)):
            taxonomy.add_path(conn, root, root_has_face=has_face)
        for tag in TREE:
            taxonomy.add_path(conn, tag)


class Library(unittest.TestCase):
    """A library at the latest schema with a tree, a photo whose keywords name someone, and faces."""

    def setUp(self):
        self.home = own_home.for_test(self)
        self.path = self.home.library("harbour.db")
        schema.ensure(self.path)
        root = os.path.dirname(self.path)
        self.photo = os.path.join(root, "Pictures", "regatta_001.jpg")
        self.other = os.path.join(root, "Pictures", "regatta_002.jpg")

        def seed(conn):
            seed_tree(conn)
            photo_rows.add_read(conn, self.photo, {"XMP:Subject": ["People/" + WREN, "Trips/Coast"]})
            photo_rows.add_read(conn, self.other, {"XMP:Subject": []})
            return [faces.insert(conn, self.photo, [0, 0, 10, 10], b"\x00" * 8),
                    faces.insert(conn, self.photo, [20, 0, 30, 10], b"\x01" * 8),
                    faces.insert(conn, self.other, [0, 0, 10, 10], b"\x02" * 8)]

        self.faces = write(self.path, seed)

    def name(self, face_ids, who):
        return write(self.path, lambda conn: faces.name(conn, face_ids, who))

    def face_id(self, face):
        return look(self.path, "SELECT tag_id FROM faces WHERE id = ?", (face,))[0][0]


class WritesKeepTheId(Library):
    def test_a_keyword_lists_the_person_with_their_node(self):
        self.assertEqual({WREN: {node_id(self.path, "People/" + WREN)}}, ids_of(self.path, "photo_people"))

    def test_naming_a_face_gives_it_the_node_and_unnaming_takes_it_off(self):
        self.name([self.faces[0]], ODA)
        self.assertEqual(node_id(self.path, "Family/Coast/" + ODA), self.face_id(self.faces[0]))
        self.assertEqual({node_id(self.path, "Family/Coast/" + ODA)}, ids_of(self.path, "photo_people")[ODA])
        write(self.path, lambda conn: faces.unname(conn, [self.faces[0]]))
        self.assertIsNone(self.face_id(self.faces[0]))
        self.assertEqual([0, 0], in_step(self.path))

    def test_a_name_in_another_case_is_the_same_node(self):
        self.name([self.faces[0]], ODA.upper())
        self.assertEqual(node_id(self.path, "Family/Coast/" + ODA), self.face_id(self.faces[0]))

    def test_excluding_takes_the_id_with_the_name(self):
        self.name([self.faces[0]], ODA)
        write(self.path, lambda conn: faces.exclude(conn, [self.faces[0]], "not a face"))
        self.assertIsNone(self.face_id(self.faces[0]))

    def test_clustering_and_a_guess(self):
        oda, wren = node_id(self.path, "Family/Coast/" + ODA), node_id(self.path, "People/" + WREN)
        write(self.path, lambda conn: faces.set_names(conn, {self.faces[0]: oda, self.faces[2]: wren}, {self.faces[0]: None, self.faces[2]: None}))
        write(self.path, lambda conn: faces.name_if_unnamed(conn, self.faces[1], WREN))
        self.assertEqual([node_id(self.path, "Family/Coast/" + ODA), node_id(self.path, "People/" + WREN),
                          node_id(self.path, "People/" + WREN)], [self.face_id(f) for f in self.faces])
        self.assertEqual([0, 0], in_step(self.path))

    def test_a_face_detected_with_a_name_has_its_node(self):
        made = write(self.path, lambda conn: faces.insert(conn, self.other, [5, 5, 9, 9], b"\x03" * 8, name=WREN))
        self.assertEqual(node_id(self.path, "People/" + WREN), self.face_id(made))

    def test_a_name_filed_twice_is_refused_and_one_filed_nowhere_gets_its_node(self):
        """A name two people have is refused when a face is named by it (nobody is guessed). A name no node has is given
        one when a face is named by it (2026-10-10), at the top of the tree."""
        with self.assertRaises(person_ids.AmbiguousPerson):
            self.name([self.faces[0]], ASH)
        self.name([self.faces[2]], NOBODY)
        self.assertEqual([None, node_id(self.path, NOBODY)], [self.face_id(self.faces[0]), self.face_id(self.faces[2])])
        self.assertIsNotNone(node_id(self.path, NOBODY))
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            results = {check.name: check.count for check in checks.run(conn)}
        finally:
            conn.close()
        self.assertEqual(0, results["faces whose name is not their person's"])
        self.assertEqual(0, results["people listed whose name is not their person's"])

    def test_renaming_a_person_is_one_transaction_and_the_id_stays(self):
        """TagTuner's rename moves the node, and the faces take its new name in the same transaction (the cache of its
        leaf): no moment has a face with a name that no node has -- stage 1 left the faces' id NULL until a second
        transaction renamed them."""
        self.name([self.faces[0], self.faces[2]], ODA)
        oda = node_id(self.path, "Family/Coast/" + ODA)
        write(self.path, lambda conn: taxonomy.move_branch(conn, "Family/Coast/" + ODA, "Family/Coast/Oda Vance"))
        self.assertEqual(oda, node_id(self.path, "Family/Coast/Oda Vance"), "the node keeps its id")
        self.assertEqual({"Oda Vance": {oda}}, ids_of(self.path))
        self.assertEqual({oda}, ids_of(self.path, "photo_people")["Oda Vance"])
        self.assertEqual([0, 0], in_step(self.path))

    def test_a_node_a_face_names_is_not_deleted_without_force(self):
        self.name([self.faces[0]], ODA)
        oda = node_id(self.path, "Family/Coast/" + ODA)
        with self.assertRaises(person_ids.PersonInUse) as refused:
            write(self.path, lambda conn: taxonomy.remove_node(conn, "Family/Coast/" + ODA))
        self.assertIn("1 face(s)", str(refused.exception))
        self.assertEqual(oda, self.face_id(self.faces[0]), "nothing was written")
        write(self.path, lambda conn: taxonomy.remove_node(conn, "Family/Coast/" + ODA, force=True))
        self.assertIsNone(node_id(self.path, "Family/Coast/" + ODA))
        self.assertEqual((None, None), look(self.path, "SELECT tag_id, name FROM faces WHERE id = ?", (self.faces[0],))[0])
        self.assertEqual([0, 0], in_step(self.path))

    def test_a_second_node_of_the_same_name_does_not_take_the_id_away(self):
        self.name([self.faces[0]], ODA)
        oda = node_id(self.path, "Family/Coast/" + ODA)
        write(self.path, lambda conn: taxonomy.add_node(conn, "People/" + ODA))
        self.assertEqual(oda, self.face_id(self.faces[0]), "which of the two is the person is the id's to say")
        write(self.path, lambda conn: taxonomy.remove_node(conn, "People/" + ODA))
        self.assertEqual(oda, self.face_id(self.faces[0]))

    def test_a_person_given_a_tag_under_them_keeps_the_id_but_is_a_group_now(self):
        """Existing violations are not converted (rule a is a refusal of the owner's actions): the face still names the
        node; it is not a person any more (#660), so a request for them as a person is refused."""
        self.name([self.faces[0]], ODA)
        oda = node_id(self.path, "Family/Coast/" + ODA)
        write(self.path, lambda conn: taxonomy.add_node(conn, "Family/Coast/%s/Swim Team" % ODA))
        self.assertEqual(oda, self.face_id(self.faces[0]))
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            with self.assertRaises(person_ids.GroupNotPerson):
                person_ids.resolve(conn, oda)
        finally:
            conn.close()
        write(self.path, lambda conn: taxonomy.remove_node(conn, "Family/Coast/%s/Swim Team" % ODA))
        self.assertEqual(oda, self.face_id(self.faces[0]))
        self.assertEqual([0, 0], in_step(self.path))

    def test_a_leaf_and_a_branch_of_one_name_is_the_leaf(self):
        write(self.path, lambda conn: taxonomy.add_node(conn, "Friends/%s/Sailing" % ODA))
        self.name([self.faces[0]], ODA)
        self.assertEqual(node_id(self.path, "Family/Coast/" + ODA), self.face_id(self.faces[0]))

    def test_a_branch_no_longer_holding_faces_keeps_the_faces_it_has(self):
        self.name([self.faces[0]], ODA)
        write(self.path, lambda conn: taxonomy.set_branch_flags(conn, "Family", has_face=0))
        self.assertEqual(node_id(self.path, "Family/Coast/" + ODA), self.face_id(self.faces[0]))
        self.assertEqual([0, 0], in_step(self.path))

    def test_a_tag_in_a_file_the_tree_lacks_gets_a_new_node_never_an_old_id(self):
        """A face named by a name the tree lacks makes the node (taxonomy.ensure_node). The node made is new: an id SQLite has
        given before -- even the highest, deleted -- is never given again."""
        highest = look(self.path, "SELECT MAX(id) FROM tag_taxonomy")[0][0]
        write(self.path, lambda conn: taxonomy.remove_node(conn, "Trips/Coast"))
        self.name([self.faces[2]], NOBODY)
        self.assertEqual(NOBODY, look(self.path, "SELECT name FROM faces WHERE id = ?", (self.faces[2],))[0][0])
        made = look(self.path, "SELECT id FROM tag_taxonomy WHERE name = ?", (NOBODY,))[0][0]
        self.assertGreater(made, highest)
        self.assertEqual(made, self.face_id(self.faces[2]))
        self.assertEqual([0, 0], in_step(self.path))

    def test_the_tree_edited_between_two_writes_of_a_bulk_edit(self):
        """A bulk tag write is chunks, each a transaction (phase 9d); TagTuner may rename a person
        between two. Each chunk reads the tree inside its own transaction: nothing is left behind."""
        photo_rows.tag(self.path, self.photo, ["People/" + ODA])
        write(self.path, lambda conn: taxonomy.move_branch(conn, "People/" + WREN, "People/Wren Ashby"))
        photo_rows.tag(self.path, self.other, ["People/Wren Ashby"])
        self.assertEqual([0, 0], in_step(self.path))
        self.assertEqual({node_id(self.path, "People/Wren Ashby")}, ids_of(self.path, "photo_people")["Wren Ashby"])

    def test_a_write_started_with_the_tree_before_a_rename_resolves_it_after(self):
        """An index run, or Suggest, holds the tree it read; its write reads the tree again inside its
        transaction, so a rename another process committed meanwhile is what the id follows."""
        conn = db.connect(self.path)
        try:
            known = person_ids.read(conn)   # what the run read at its start
            write(self.path, lambda other: taxonomy.move_branch(other, "Family/Coast/" + ODA, "Family/Coast/Oda Vance"))
            conn.execute("BEGIN IMMEDIATE")
            faces.name(conn, [self.faces[0]], "Oda Vance")
            conn.commit()
        finally:
            conn.close()
        self.assertIsNotNone(known.id_of(ODA))
        self.assertEqual(node_id(self.path, "Family/Coast/Oda Vance"), self.face_id(self.faces[0]))


class TwoLibraries(unittest.TestCase):
    def test_one_name_is_each_librarys_own_node(self):
        home = own_home.for_test(self)
        made = {}
        for name, extra in (("north.db", ()), ("south.db", ("Activity", "Activity/Sailing", "Pets", "Pets/Rook"))):
            path = home.library(name)
            schema.ensure(path)
            photo = os.path.join(os.path.dirname(path), name + ".jpg")

            def seed(conn, extra=extra, photo=photo):
                for tag in extra:
                    taxonomy.add_path(conn, tag)
                seed_tree(conn)
                photo_rows.add_read(conn, photo, {"XMP:Subject": []})
                face = faces.insert(conn, photo, [0, 0, 1, 1], b"\x00")
                faces.name(conn, [face], WREN)
                return face

            face = write(path, seed)
            made[name] = (look(path, "SELECT tag_id FROM faces WHERE id = ?", (face,))[0][0],
                          node_id(path, "People/" + WREN))
        self.assertEqual(made["north.db"][0], made["north.db"][1])
        self.assertEqual(made["south.db"][0], made["south.db"][1])
        self.assertNotEqual(made["north.db"][0], made["south.db"][0], "the same person, two ids")


# ---- The reads of names and ids ----------------------------------------------------------------

class TheReadsOfNamesAndIds(Library):
    def test_a_pair_of_name_and_id_comes_from_the_covering_index_and_a_write_by_name_uses_it(self):
        """The sync on photo_index's 225,000 faces: one lookup per distinct name (the pairs of name and id, from
        idx_faces_person), one write per pair that is wrong, and no read of the vectors."""
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            plan = " ".join(row[3] for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT name, tag_id, COUNT(*) FROM faces WHERE name IS NOT NULL GROUP BY name, tag_id"))
            update = " ".join(row[3] for row in conn.execute(
                "EXPLAIN QUERY PLAN UPDATE faces SET tag_id = ? WHERE name = ? AND tag_id IS ?", (1, "x", None)))
        finally:
            conn.close()
        self.assertIn("COVERING INDEX idx_faces_person", plan)
        self.assertIn("idx_faces_person", update)


class TheJournal(Library):
    def test_a_node_the_faces_name_cannot_be_deleted_by_a_change(self):
        self.name([self.faces[0]], ODA)
        oda = node_id(self.path, "Family/Coast/" + ODA)
        with self.assertRaises(journal.Refusal) as refused:
            journal.apply(self.path, "merge", [journal.delete("tag_taxonomy", (oda,), {"tag": "Family/Coast/" + ODA})])
        self.assertIn("faces", str(refused.exception))
        self.assertEqual(oda, self.face_id(self.faces[0]))

    def test_the_undo_of_a_nodes_insert_takes_the_node_away_unless_a_person_named_a_face_by_it(self):
        """Undoing a node's insert takes the node away -- unless a person named a face by it, which is theirs."""
        applied = journal.apply(self.path, "add a person", [journal.insert("tag_taxonomy", {
            "tag": "Friends/Maren Oakhollow", "parent_id": node_id(self.path, "Friends"), "name": "Maren Oakhollow",
            "has_face": 1})])
        self.assertTrue(journal.rehearse_undo(self.path, applied.change_id).exact)
        journal.undo(self.path, applied.change_id)
        self.assertIsNone(node_id(self.path, "Friends/Maren Oakhollow"))
        again = journal.apply(self.path, "add a person", [journal.insert("tag_taxonomy", {
            "tag": "Friends/Maren Oakhollow", "parent_id": node_id(self.path, "Friends"), "name": "Maren Oakhollow",
            "has_face": 1})])
        write(self.path, lambda conn: faces.name(conn, [self.faces[0]], node_id(self.path, "Friends/Maren Oakhollow")))
        with self.assertRaises(journal.Refusal) as refused:
            journal.undo(self.path, again.change_id)
        self.assertIn("unname them first", str(refused.exception))

    def test_the_undo_of_a_nodes_insert_is_refused_for_a_guessed_face_too(self):
        """A face only guessed (clustering's name_source None) is refused the same, before anything is written: the old undo
        took the node away and left the face holding a name and no id (the review of 2026-10-15)."""
        applied = journal.apply(self.path, "add a person", [journal.insert("tag_taxonomy", {
            "tag": "Friends/Maren Oakhollow", "parent_id": node_id(self.path, "Friends"), "name": "Maren Oakhollow",
            "has_face": 1})])
        maren = node_id(self.path, "Friends/Maren Oakhollow")
        write(self.path, lambda conn: faces.name_if_unnamed(conn, self.faces[1], maren))
        self.assertEqual(
            maren, look(self.path, "SELECT tag_id FROM faces WHERE id = ?", (self.faces[1],))[0][0])
        with self.assertRaises(journal.Refusal) as refused:
            journal.undo(self.path, applied.change_id)
        self.assertIn("unname them first", str(refused.exception))
        self.assertEqual(maren, node_id(self.path, "Friends/Maren Oakhollow"))
        self.assertEqual(
            (maren, "Maren Oakhollow"),
            look(self.path, "SELECT tag_id, name FROM faces WHERE id = ?", (self.faces[1],))[0])

    def test_a_rename_between_a_naming_and_its_undo_is_not_a_conflict(self):
        """The change records the person's id and, beside it, the name as a cache: the rename of the node changes the cache
        and not the face, so the undo finds the face as the change left it."""
        oda = node_id(self.path, "Family/Coast/" + ODA)
        applied = journal.apply(self.path, "name a face", [journal.update(
            "faces", (self.faces[0],), {"name": None, "tag_id": None}, {"name": ODA, "tag_id": oda}, kind="named")])
        write(self.path, lambda conn: taxonomy.move_branch(conn, "Family/Coast/" + ODA, "Family/Coast/Oda Vance"))
        self.assertEqual((oda, "Oda Vance"), look(self.path, "SELECT tag_id, name FROM faces WHERE id = ?", (self.faces[0],))[0])
        self.assertTrue(journal.rehearse_undo(self.path, applied.change_id).exact)
        journal.undo(self.path, applied.change_id)
        self.assertEqual((None, None), look(self.path, "SELECT tag_id, name FROM faces WHERE id = ?", (self.faces[0],))[0])

    def test_a_face_deleted_and_put_back_rehearses_exactly(self):
        self.name([self.faces[0]], ODA)
        edits = [journal.delete("faces", (self.faces[0],), {"name": ODA})]
        rehearsal = journal.rehearse(self.path, "dedupe faces", edits)
        self.assertTrue(rehearsal.exact, rehearsal.differences)
        applied = journal.apply(self.path, "dedupe faces", edits)
        journal.undo(self.path, applied.change_id)
        self.assertEqual(node_id(self.path, "Family/Coast/" + ODA), self.face_id(self.faces[0]))


# ---- What the doctor says --------------------------------------------------------------------

class TheDoctor(Library):
    def test_a_row_an_older_version_wrote_is_reported_and_repaired(self):
        """A version of the app from before migration 21 names a face and knows nothing of the id."""
        self.name([self.faces[0]], ODA)
        conn = db.connect(self.path)
        try:
            conn.execute("UPDATE faces SET name = ? WHERE id = ?", (WREN, self.faces[0]))   # as version 20 wrote it
            conn.commit()
        finally:
            conn.close()
        self.assertEqual([1, 0], in_step(self.path))
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            found = {check.name: check for check in checks.run(conn)}["faces whose name is not their person's"]
        finally:
            conn.close()
        self.assertEqual((1, [self.faces[0]]), (found.count, found.examples))
        self.assertEqual({"faces": 1, "photo_people": 0}, person_ids.repair(self.path))
        self.assertEqual([0, 0], in_step(self.path))
        # The id is the person; the name was a cache and is put right, not the id.
        self.assertEqual((node_id(self.path, "Family/Coast/" + ODA), ODA),
                         look(self.path, "SELECT tag_id, name FROM faces WHERE id = ?", (self.faces[0],))[0])

    def test_repairing_only_the_people_does_not_rebuild_every_photos_derived_rows(self):
        """Fix round 3: after the ids were put right the doctor compared the number of lines with whether any list was left, and so
        rebuilt the keyword, folder and metadata rows of every photo though only the people were wrong."""
        import io
        from contextlib import redirect_stdout
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import doctor
        self.name([self.faces[0]], ODA)
        write(self.path, lambda conn: conn.execute("UPDATE faces SET name = ? WHERE id = ?", (ODA.lower(), self.faces[0])))
        with mock.patch.object(doctor.derived, "repair", side_effect=AssertionError("the photos were rebuilt")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(0, doctor.rebuild_derived(self.path, apply=True))
        self.assertEqual([0, 0], in_step(self.path))

    def test_the_tool_reports_and_with_apply_repairs(self):
        import io
        from contextlib import redirect_stdout
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import doctor
        self.name([self.faces[0]], ODA)
        self.name([self.faces[1]], WREN)
        conn = db.connect(self.path)
        try:
            conn.execute("UPDATE faces SET tag_id = NULL, name_source = 'manual' WHERE id = ?", (self.faces[1],))   # no id written
            people.rebuild(conn)
            conn.execute("UPDATE faces SET name = ? WHERE id = ?", (ODA.lower(), self.faces[0]))   # a cache the node does not spell so
            conn.commit()
        finally:
            conn.close()
        said = io.StringIO()
        with redirect_stdout(said):
            self.assertEqual(2, doctor.report(self.path), "the faces, and the photo whose list now differs from the face's")
        text = said.getvalue()
        self.assertRegex(text, "faces whose name is not their person's +2", "a cache misspelled; a name with no id")
        self.assertNotIn(WREN, text, "names only with --show")
        said = io.StringIO()
        with redirect_stdout(said):
            self.assertEqual(1, doctor.rebuild_derived(self.path))
        self.assertEqual([2, 0], in_step(self.path), "a dry run writes nothing")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, doctor.rebuild_derived(self.path, apply=True))
        self.assertEqual([0, 0], in_step(self.path))
        self.assertEqual((None, None, None), look(self.path, "SELECT tag_id, name, name_source FROM faces WHERE id = ?",
                                                  (self.faces[1],))[0],
                         "the repair links no name: a name alone is nobody (owner, 2026-10-15, superseding #985's names to"
                         " review), and the face is unnamed, not decided")
        self.assertEqual(ODA, look(self.path, "SELECT name FROM faces WHERE id = ?", (self.faces[0],))[0][0])


class AListedPersonWithNoId(Library):
    def test_the_repair_takes_a_listed_person_with_no_id_or_a_gone_nodes_id_away(self):
        """A photo_people row holding a name and no id, or the id of a node gone (hand-written: nothing makes one), is nobody:
        the repair, and the sync every tree-touching change runs, take the row away -- photo_people.name is NOT NULL."""
        photo = look(self.path, "SELECT id FROM photos ORDER BY id LIMIT 1")[0][0]
        write(self.path, lambda conn: conn.executemany(
            "INSERT INTO photo_people (photo_id, position, name, source, tag_id) VALUES (?, ?, ?, 'keyword', ?)",
            [(photo, 90, "Somebody Else", None), (photo, 91, "Long Gone", 999999)]))
        self.assertEqual({"faces": 0, "photo_people": 2}, person_ids.repair(self.path))
        self.assertEqual([], look(self.path, "SELECT position FROM photo_people WHERE position >= 90"))
        self.assertEqual([0, 0], in_step(self.path))


class AStrayIdAnOlderAppLeaves(Library):
    """docs/findings.md, #662: a version from before migration 21 unnames a face (name NULL) and knows
    nothing of the id, which stays. The doctor counts it, and the repair clears it."""

    def test_is_found_and_cleared(self):
        self.name([self.faces[0], self.faces[1]], ODA)
        conn = db.connect(self.path)
        try:
            conn.execute("UPDATE faces SET name = NULL, name_source = 'manual' WHERE id = ?", (self.faces[0],))
            conn.commit()
        finally:
            conn.close()
        self.assertIsNotNone(self.face_id(self.faces[0]))
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            found = person_ids.out_of_step(conn, "faces")
        finally:
            conn.close()
        self.assertEqual((1, [self.faces[0]]), (found.rows, found.examples))
        self.assertEqual({"faces": 1, "photo_people": 0}, person_ids.repair(self.path))
        self.assertIsNone(self.face_id(self.faces[0]))
        self.assertEqual(node_id(self.path, "Family/Coast/" + ODA), self.face_id(self.faces[1]))
        self.assertEqual([0, 0], in_step(self.path))


if __name__ == "__main__":
    unittest.main()
