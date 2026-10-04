"""People by their node's id, stage 1 (tagpup.store.person_ids; docs/ARCHITECTURE.md, "Identity by id").

`faces.tag_id` and `photo_people.tag_id` sit beside `name` and hold the id of the one person node the
name is -- NULL for a name no person node is called, or two are. Migration 21 fills them, one lookup per
distinct name; every write that sets a name, every edit of the tree and every undo keeps them, in its
own transaction. Each case here is a way that goes wrong: the migration interrupted, a name recorded in
the journal before the column existed, a snapshot from before it, the tree renamed or edited between two
writes, a tag the tree lacks, a node deleted while faces name it, two libraries naming one person with
different ids.

Rows are made as the code that makes them makes them: a face by tagpup.store.faces, a photo by the
indexer's record (tests/photo_rows.py), the tree by tagpup.store.taxonomy; a library at version 20 by
the migrations themselves (test_migrations.at_version), its faces named as version 20 stored them.
"""
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from test_migrations import at_version  # noqa: E402

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

    def test_clustering_and_a_guess_and_clearing_them(self):
        write(self.path, lambda conn: faces.set_names(conn, {self.faces[0]: ODA, self.faces[2]: WREN}))
        write(self.path, lambda conn: faces.name_if_unnamed(conn, self.faces[1], WREN))
        self.assertEqual([node_id(self.path, "Family/Coast/" + ODA), node_id(self.path, "People/" + WREN),
                          node_id(self.path, "People/" + WREN)], [self.face_id(f) for f in self.faces])
        write(self.path, lambda conn: faces.clear_automatic_names(conn))
        self.assertEqual([None, None, None], [self.face_id(f) for f in self.faces])
        self.assertEqual([0, 0], in_step(self.path))

    def test_a_face_detected_with_a_name_has_its_node(self):
        made = write(self.path, lambda conn: faces.insert(conn, self.other, [5, 5, 9, 9], b"\x03" * 8, name=WREN))
        self.assertEqual(node_id(self.path, "People/" + WREN), self.face_id(made))

    def test_a_name_filed_twice_or_nowhere_has_no_id_and_is_reported(self):
        self.name([self.faces[0]], ASH)
        self.name([self.faces[2]], NOBODY)
        self.assertEqual([None, None], [self.face_id(self.faces[0]), self.face_id(self.faces[2])])
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            found = person_ids.unresolved(conn)
            results = {check.name: check.count for check in checks.run(conn)}
        finally:
            conn.close()
        self.assertEqual({ASH: 2}, found.several, "a face and the photo's list")
        self.assertEqual({NOBODY: 2}, found.none)
        self.assertEqual(0, results["faces whose person id is not their name's"])
        self.assertEqual(0, results["people listed whose person id is not their name's"])

    def test_renaming_a_person_follows_through_the_tree_and_the_faces(self):
        """TagTuner's rename: the tree's node moves (one transaction), the files are rewritten, then the
        faces are renamed (another). Between the two the faces' old name names no node: no id, never the
        renamed node's under a name it no longer has."""
        self.name([self.faces[0], self.faces[2]], ODA)
        oda = node_id(self.path, "Family/Coast/" + ODA)
        write(self.path, lambda conn: taxonomy.move_branch(conn, "Family/Coast/" + ODA, "Family/Coast/Oda Vance"))
        self.assertEqual(oda, node_id(self.path, "Family/Coast/Oda Vance"), "the node keeps its id")
        self.assertEqual({ODA: {None}}, {k: v for k, v in ids_of(self.path).items()})
        self.assertEqual([0, 0], in_step(self.path))
        write(self.path, lambda conn: people.rename(conn, ODA, "Oda Vance"))
        self.assertEqual({"Oda Vance": {oda}}, ids_of(self.path))
        self.assertEqual({oda}, ids_of(self.path, "photo_people")["Oda Vance"])
        self.assertEqual([0, 0], in_step(self.path))

    def test_a_node_deleted_while_faces_name_it_leaves_no_id_naming_it(self):
        self.name([self.faces[0]], ODA)
        gone = node_id(self.path, "Family/Coast/" + ODA)
        write(self.path, lambda conn: taxonomy.remove_node(conn, "Family/Coast/" + ODA))
        self.assertIsNone(self.face_id(self.faces[0]))
        self.assertEqual([], look(self.path, "SELECT 1 FROM faces WHERE tag_id = ? UNION ALL"
                                             " SELECT 1 FROM photo_people WHERE tag_id = ?", (gone, gone)))

    def test_a_second_node_of_the_same_name_takes_the_id_away_and_removing_it_gives_it_back(self):
        self.name([self.faces[0]], ODA)
        oda = node_id(self.path, "Family/Coast/" + ODA)
        write(self.path, lambda conn: taxonomy.add_node(conn, "People/" + ODA))
        self.assertIsNone(self.face_id(self.faces[0]), "which of the two is a guess")
        write(self.path, lambda conn: taxonomy.remove_node(conn, "People/" + ODA))
        self.assertEqual(oda, self.face_id(self.faces[0]))

    def test_a_person_given_a_tag_under_them_is_a_branch_and_loses_the_id(self):
        """The leaf rule (#660): a person is a node with nothing under it. A node made under a person
        makes them a branch -- no id, by the tree's edit -- and taking it away gives the id back."""
        self.name([self.faces[0]], ODA)
        oda = node_id(self.path, "Family/Coast/" + ODA)
        write(self.path, lambda conn: taxonomy.add_node(conn, "Family/Coast/%s/Swim Team" % ODA))
        self.assertIsNone(self.face_id(self.faces[0]))
        self.assertEqual({None}, ids_of(self.path, "photo_people")[ODA])
        write(self.path, lambda conn: taxonomy.remove_node(conn, "Family/Coast/%s/Swim Team" % ODA))
        self.assertEqual(oda, self.face_id(self.faces[0]))
        self.assertEqual([0, 0], in_step(self.path))

    def test_a_leaf_and_a_branch_of_one_name_is_the_leaf(self):
        write(self.path, lambda conn: taxonomy.add_node(conn, "Friends/%s/Sailing" % ODA))
        self.name([self.faces[0]], ODA)
        self.assertEqual(node_id(self.path, "Family/Coast/" + ODA), self.face_id(self.faces[0]))

    def test_a_branch_no_longer_holding_faces_names_nobody(self):
        self.name([self.faces[0]], ODA)
        write(self.path, lambda conn: taxonomy.set_branch_flags(conn, "Family", has_face=0))
        self.assertIsNone(self.face_id(self.faces[0]))
        self.assertEqual([0, 0], in_step(self.path))

    def test_a_tag_in_a_file_the_tree_lacks_gets_a_new_node_never_an_old_id(self):
        """The indexer adds the people a file names to the tree (TagTaxonomy.add_people, save). The node
        made is new: an id SQLite has given before -- even the highest, deleted -- is never given again."""
        highest = look(self.path, "SELECT MAX(id) FROM tag_taxonomy")[0][0]
        write(self.path, lambda conn: taxonomy.remove_node(conn, "Trips/Coast"))
        self.name([self.faces[2]], NOBODY)
        self.assertIsNone(self.face_id(self.faces[2]))
        tree = taxonomy.TagTaxonomy(self.path)
        tree.load()
        tree.add_people([NOBODY])
        tree.save()
        made = look(self.path, "SELECT id FROM tag_taxonomy WHERE name = ?", (NOBODY,))[0][0]
        self.assertGreater(made, highest)
        self.assertEqual(made, self.face_id(self.faces[2]))
        self.assertEqual([0, 0], in_step(self.path))

    def test_the_tree_edited_between_two_writes_of_a_bulk_edit(self):
        """A bulk tag write is chunks, each a transaction (phase 9d); TagTuner may rename a person
        between two. Each chunk reads the tree inside its own transaction: nothing is left behind."""
        from tagpup.store import photos
        photos.record_tags(self.path, self.photo, ["People/" + ODA])
        write(self.path, lambda conn: taxonomy.move_branch(conn, "People/" + WREN, "People/Wren Ashby"))
        photos.record_tags(self.path, self.other, ["People/Wren Ashby"])
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


# ---- The migration -----------------------------------------------------------------------------

class TheMigration(unittest.TestCase):
    def library_at_20(self):
        """A library as version 20 left it: its tree, faces named by version 20's own writes (no id),
        and its people rebuilt."""
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        at_version(path, 20)
        photo = os.path.join(os.path.dirname(path), "Pictures", "dunes.jpg")
        conn = db.connect(path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            seed_tree(conn)
            photo_rows.add_read(conn, photo, {"XMP:Subject": ["People/" + WREN]})
            made = [faces.insert(conn, photo, [n, 0, n + 5, 5], bytes([n])) for n in range(5)]
            for face, who in zip(made, (ODA, WREN.lower(), ASH, NOBODY, "People")):
                faces.name(conn, [face], who)
            conn.commit()
        finally:
            conn.close()
        self.assertNotIn("tag_id", [row[1] for row in look(path, "PRAGMA table_info(faces)")])
        return home, path, made

    def test_fills_each_rows_id_from_its_name_and_changes_nothing_else(self):
        _home, path, made = self.library_at_20()
        before = look(path, "SELECT id, photo_id, box, embedding, name, prob, name_source, excluded FROM faces ORDER BY id")
        listed = look(path, "SELECT photo_id, position, name, source FROM photo_people ORDER BY 1, 2")
        schema._current.clear()
        self.assertEqual(["people by their node's id", "photos by file name"], schema.ensure(path))
        self.assertEqual(before, look(path, "SELECT id, photo_id, box, embedding, name, prob, name_source, excluded"
                                            " FROM faces ORDER BY id"))
        self.assertEqual(listed, look(path, "SELECT photo_id, position, name, source FROM photo_people ORDER BY 1, 2"))
        held = dict(look(path, "SELECT id, tag_id FROM faces"))
        self.assertEqual([node_id(path, "Family/Coast/" + ODA), node_id(path, "People/" + WREN), None, None, None],
                         [held[face] for face in made], "a root is a category, not a person")
        self.assertEqual({node_id(path, "People/" + WREN)}, ids_of(path, "photo_people")[WREN])
        self.assertEqual([0, 0], in_step(path))
        # migration 22 (an index) is recorded after it
        operation, summary = look(path, "SELECT operation, summary FROM changes WHERE operation != "
                                        "'migration 22: photos by file name' ORDER BY id DESC LIMIT 1")[0]
        self.assertEqual("migration 21: people by their node's id", operation)
        self.assertIn('"kind": "additive"', summary)

    def test_reads_one_pair_per_name_never_a_row_or_a_vector(self):
        """The backfill on photo_index's 225,000 faces: one lookup per distinct name (the pairs of name
        and id, from idx_faces_person), one write per pair that is wrong, and no read of the vectors."""
        _home, path, _made = self.library_at_20()
        statements = []
        real = schema._person_ids

        def traced(conn):
            conn.set_trace_callback(statements.append)
            try:
                real(conn)
            finally:
                conn.set_trace_callback(None)

        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS[:20] + (
                schema.MIGRATIONS[20]._replace(apply=traced),)):
            schema._current.clear()
            schema.ensure(path)
        # The trace names a statement again for each row a trigger fires on (the runner's watch): count
        # the statements, not their mentions.
        writes = {s for s in statements if s.lstrip().upper().startswith("UPDATE")}
        names = look(path, "SELECT COUNT(DISTINCT name) FROM faces")[0][0] + look(
            path, "SELECT COUNT(DISTINCT name) FROM photo_people")[0][0]
        self.assertLessEqual(len(writes), names + 1)
        self.assertEqual([], [s for s in statements if "embedding" in s or "crop" in s.lower() or "jpeg" in s])
        conn = db.connect(db.readonly_uri(path), uri=True)
        try:
            plan = " ".join(row[3] for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT name, tag_id, COUNT(*) FROM faces WHERE name IS NOT NULL GROUP BY name, tag_id"))
            update = " ".join(row[3] for row in conn.execute(
                "EXPLAIN QUERY PLAN UPDATE faces SET tag_id = ? WHERE name = ? AND tag_id IS ?", (1, "x", None)))
        finally:
            conn.close()
        self.assertIn("COVERING INDEX idx_faces_person", plan)
        self.assertIn("idx_faces_person", update)

    def test_interrupted_part_way_leaves_the_library_at_20_and_a_second_run_does_it(self):
        _home, path, made = self.library_at_20()
        schema._current.clear()
        with mock.patch.object(person_ids, "sync", side_effect=RuntimeError("the power went")):
            with self.assertRaises(RuntimeError):
                schema.ensure(path)
        self.assertEqual([(20,)], look(path, "SELECT MAX(version) FROM schema_version"))
        self.assertNotIn("tag_id", [row[1] for row in look(path, "PRAGMA table_info(faces)")], "all or nothing")
        self.assertEqual([], look(path, "SELECT name FROM sqlite_master WHERE name = 'idx_faces_person'"))
        schema._current.clear()
        self.assertEqual(["people by their node's id", "photos by file name"], schema.ensure(path))
        self.assertEqual(node_id(path, "Family/Coast/" + ODA),
                         look(path, "SELECT tag_id FROM faces WHERE id = ?", (made[0],))[0][0])

    def test_a_snapshot_from_before_it_comes_back_and_is_migrated_again(self):
        """snapshots.restore copies an older library over the file and brings it to the current schema."""
        from tagpup.store import snapshots
        home, path, made = self.library_at_20()
        conn = db.connect(path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        snapshot = os.path.join(home.root, "before-21.db")
        shutil.copyfile(path, snapshot)
        schema._current.clear()
        schema.ensure(path)
        write(path, lambda conn: faces.unname(conn, [made[0]]))
        snapshots.restore(path, snapshot)
        self.assertEqual([(schema.LATEST,)], look(path, "SELECT MAX(version) FROM schema_version"))
        self.assertEqual(node_id(path, "Family/Coast/" + ODA),
                         look(path, "SELECT tag_id FROM faces WHERE id = ?", (made[0],))[0][0])
        self.assertEqual([0, 0], in_step(path))

    def test_a_change_recorded_before_it_is_undone_after_it_and_the_face_gets_its_id(self):
        """A face deleted by a journaled change at version 20 (dedupe_faces) was recorded without the
        column. The migration blocks no undo of it (schema.ADDS_DERIVED_COLUMNS); the undo puts the row
        back as recorded and gives it its person's id."""
        _home, path, made = self.library_at_20()
        schema._current.clear()
        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS[:20]), mock.patch.object(schema, "LATEST", 20):
            applied = journal.apply(path, "dedupe faces", [journal.delete("faces", (made[0],), {"name": ODA})])
        self.assertEqual(20, look(path, "SELECT schema_version FROM changes WHERE id = ?", (applied.change_id,))[0][0])
        schema._current.clear()
        schema.ensure(path)
        conn = db.connect(db.readonly_uri(path), uri=True)
        try:
            self.assertEqual([], journal.refusal(conn, applied.change_id))
        finally:
            conn.close()
        self.assertTrue(journal.rehearse_undo(path, applied.change_id).exact)
        journal.undo(path, applied.change_id)
        self.assertEqual([(ODA, node_id(path, "Family/Coast/" + ODA))],
                         look(path, "SELECT name, tag_id FROM faces WHERE id = ?", (made[0],)))
        self.assertEqual([0, 0], in_step(path))

    def test_the_columns_it_says_are_derived_are_the_ones_it_adds(self):
        _home, path, _made = self.library_at_20()
        before = {table: [row[1] for row in look(path, "PRAGMA table_info(%s)" % table)] for table in journal.KEYS}
        schema._current.clear()
        schema.ensure(path)
        added = {table: tuple(row[1] for row in look(path, "PRAGMA table_info(%s)" % table) if row[1] not in before[table])
                 for table in journal.KEYS}
        self.assertEqual(schema.ADDS_DERIVED_COLUMNS[21], {t: c for t, c in added.items() if c})
        self.assertIsNone(journal.schema_gap_blocker(20, 21))


class TheJournal(Library):
    def test_an_undone_rename_of_a_node_gives_the_faces_back_their_id(self):
        self.name([self.faces[0]], ODA)
        oda = node_id(self.path, "Family/Coast/" + ODA)
        applied = journal.apply(self.path, "merge", [journal.delete("tag_taxonomy", (oda,), {"tag": "Family/Coast/" + ODA})])
        self.assertIsNone(self.face_id(self.faces[0]))
        journal.undo(self.path, applied.change_id)
        self.assertEqual(oda, self.face_id(self.faces[0]))
        self.assertEqual([0, 0], in_step(self.path))

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
            found = {check.name: check for check in checks.run(conn)}["faces whose person id is not their name's"]
        finally:
            conn.close()
        self.assertEqual((1, [self.faces[0]]), (found.count, found.examples))
        self.assertEqual({"faces": 1, "photo_people": 0}, person_ids.repair(self.path))
        self.assertEqual([0, 0], in_step(self.path))
        self.assertEqual(node_id(self.path, "People/" + WREN), self.face_id(self.faces[0]))

    def test_the_tool_reports_and_with_apply_repairs(self):
        import io
        from contextlib import redirect_stdout
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import doctor
        self.name([self.faces[0]], ODA)
        self.name([self.faces[2]], ASH)
        conn = db.connect(self.path)
        try:
            conn.execute("UPDATE faces SET tag_id = NULL WHERE id = ?", (self.faces[0],))
            conn.commit()
        finally:
            conn.close()
        said = io.StringIO()
        with redirect_stdout(said):
            self.assertEqual(1, doctor.report(self.path))
        text = said.getvalue()
        self.assertIn("faces whose person id is not their name's", text)
        self.assertIn("names with several person nodes: 1", text)
        self.assertNotIn(ASH, text, "names only with --show")
        said = io.StringIO()
        with redirect_stdout(said):
            self.assertEqual(1, doctor.rebuild_derived(self.path))
        self.assertEqual([1, 0], in_step(self.path), "a dry run writes nothing")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, doctor.rebuild_derived(self.path, apply=True))
        self.assertEqual([0, 0], in_step(self.path))


    def test_a_name_on_a_branch_has_no_id_and_is_reported_with_the_node(self):
        """docs/findings.md, #660, the owner's rule (2026-10-04): a branch tag cannot be a person. A name
        whose only node has nodes under it -- a group such as Family/Coast -- has no id; the name itself is
        left as it is (stage 1 renames and unnames nothing), and the doctor lists it with the node's id,
        the name only with --show."""
        self.name([self.faces[0]], "Coast")
        coast = node_id(self.path, "Family/Coast")
        self.assertIsNone(self.face_id(self.faces[0]))
        self.assertEqual("Coast", look(self.path, "SELECT name FROM faces WHERE id = ?", (self.faces[0],))[0][0])
        self.assertEqual([0, 0], in_step(self.path))
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            found = person_ids.unresolved(conn)
        finally:
            conn.close()
        self.assertEqual({"Coast": ([coast], 2)}, found.branch, "a face and the photo's list")
        import io
        from contextlib import redirect_stdout
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import doctor
        for show, named in ((0, False), (5, True)):
            said = io.StringIO()
            with redirect_stdout(said):
                doctor.report(self.path, show=show)
            text = said.getvalue()
            self.assertIn("names on a branch: 1, on 2 row(s)", text)
            self.assertIn("node %d" % coast, text)
            self.assertEqual(named, "Coast" in text.replace("Family/Coast", ""), "names only with --show")


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
