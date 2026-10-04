"""Automatch's write is one write (docs/findings.md, #659).

It named its chosen faces one by one (faces.name_if_unnamed), and each rebuilt its photo's people and
read the tag tree for the faces' person ids, twice: a folder of 9,400 unnamed faces cost 9.8 s inside
the write lock where the trunk took 4.7 s. Now the faces are named in one statement per name and chunk
(faces.name_unnamed), with name_if_unnamed's guard, and their photos are rebuilt once, reading the tree
once.
"""
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.test_service_faces import FacesCase, vector  # noqa: E402

from tagpup.services import faces  # noqa: E402
from tagpup.store import db, person_ids, taxonomy  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402

ROWAN = "Rowan Thackeray"


class AutomatchNamesItsFacesInOneWrite(FacesCase):
    def setUp(self):
        super().setUp()
        db.write_with_connection(self.lib.library.path, lambda conn: taxonomy.add_node(conn, "People/" + ROWAN, 1))
        self.rowan = vector(1)
        self.known = self.face(self.photo("known.jpg"), name=ROWAN, embedding=self.rowan)
        self.lookalikes = [self.face(self.photo("p%d.jpg" % n), embedding=self.rowan) for n in range(4)]

    def matrix(self):
        return [self.known], [ROWAN], np.stack([self.rowan])

    def test_one_rebuild_and_one_read_of_the_tree_for_the_whole_folder(self):
        with mock.patch.object(store_faces, "_rebuilt", wraps=store_faces._rebuilt) as rebuilt, \
                mock.patch.object(person_ids.People, "read", wraps=person_ids.People.read) as reads:
            result = faces.automatch_folder(self.lib.library, self.folder, self.matrix)
        self.assertEqual(4, result.changed)
        self.assertEqual(1, rebuilt.call_count)
        self.assertEqual(1, reads.call_count)
        node = self.lib.rows("SELECT id FROM tag_taxonomy WHERE tag = ?", ("People/" + ROWAN,))[0][0]
        self.assertEqual({(ROWAN, node)}, set(self.lib.rows(
            "SELECT name, tag_id FROM faces WHERE id IN (%s)" % ",".join("?" * 4), self.lookalikes)))


class NamingUnnamedFacesTogether(FacesCase):
    def test_only_faces_still_unnamed_in_play_and_not_unmatched_by_hand_are_named(self):
        photo = self.photo("a.jpg")
        free, named, excluded, nobody = (self.face(photo) for _ in range(4))
        self.lib.execute("UPDATE faces SET name = 'Oda Castellane' WHERE id = ?", (named,))
        self.lib.execute("UPDATE faces SET excluded = 1 WHERE id = ?", (excluded,))
        self.lib.execute("UPDATE faces SET name_source = 'manual' WHERE id = ?", (nobody,))
        done = db.write_with_connection(self.lib.library.path, lambda conn: store_faces.name_unnamed(
            conn, {free: ROWAN, named: ROWAN, excluded: ROWAN, nobody: ROWAN}))
        self.assertEqual([free], done)
        self.assertEqual([(free, ROWAN), (named, "Oda Castellane"), (excluded, None), (nobody, None)],
                         self.lib.rows("SELECT id, name FROM faces WHERE id IN (?, ?, ?, ?) ORDER BY id",
                                       (free, named, excluded, nobody)))

    def test_without_an_outer_transaction_it_reports_only_what_it_wrote(self):
        """docs/findings.md, #665: a face named by another process just before the write is not counted
        as named by it. Without the caller's IMMEDIATE transaction, nothing holds the faces still between a
        read and the write; only what the UPDATE itself changed is reported."""
        photo = self.photo("c.jpg")
        mine, theirs = self.face(photo), self.face(photo)
        path = self.lib.library.path

        class Interleaved:
            """The caller's connection; just before the first write to faces' names, another process
            names `theirs`."""

            def __init__(self, conn):
                self.conn, self.done = conn, False

            def execute(self, sql, params=()):
                if not self.done and sql.lstrip().upper().startswith("UPDATE FACES SET NAME"):
                    self.done = True
                    other = db.connect(path)
                    try:
                        other.execute("UPDATE faces SET name = 'Oda Castellane' WHERE id = ?", (theirs,))
                        other.commit()
                    finally:
                        other.close()
                return self.conn.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self.conn, name)

        conn = db.connect(path)
        try:
            conn.execute("SELECT 1").fetchall()
            done = store_faces.name_unnamed(Interleaved(conn), {mine: ROWAN, theirs: ROWAN})
            conn.commit()
        finally:
            conn.close()
        self.assertEqual([mine], done)
        self.assertEqual([(mine, ROWAN), (theirs, "Oda Castellane")],
                         self.lib.rows("SELECT id, name FROM faces WHERE id IN (?, ?) ORDER BY id", (mine, theirs)))

    def test_a_write_rebuilds_its_photos_reading_the_tree_once(self):
        face = self.face(self.photo("b.jpg"))
        with mock.patch.object(person_ids.People, "read", wraps=person_ids.People.read) as reads:
            db.write_with_connection(self.lib.library.path, lambda conn: store_faces.name(conn, [face], ROWAN))
        self.assertEqual(1, reads.call_count)


if __name__ == "__main__":
    unittest.main()
