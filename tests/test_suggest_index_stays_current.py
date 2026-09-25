"""Suggest reads each library's index once, and again only when it has changed.

The library TagPup started on kept the index it loaded at startup for as long as the
server ran: photos indexed afterwards were never neighbours, and tags saved afterwards
never counted, until a restart. Every other library went the other way and loaded its
whole index again for every Suggest -- 1.3s on a 68,000-photo library, each click.

Now each library has one index, kept by the process's runtime (tagpup.runtime.Runtime.
photo_index, a PerLibrary), and a Suggest run reloads the index only when the photos
table has changed since it was read.
"""
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_rows import add_face, add_vector  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import db as tagpup_db  # noqa: E402


def add_photo(db_path, name):
    conn = tagpup_db.connect(db_path)
    try:
        path = os.path.join(os.path.dirname(db_path), name)
        conn.execute("INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)"
                     " VALUES (?, 1.0, 1, '[\"Activity/Rowing\"]', '[]', '{}')", (path,))
        add_vector(conn, path, np.ones(512, dtype=np.float32).tobytes())
        conn.commit()
    finally:
        conn.close()


class IndexReloadsWhenChanged(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="index_current_")
        self.db_path = os.path.join(self.dir, "lib.db")
        self.index = PhotoIndex(self.db_path)
        self.index.load()
        add_photo(self.db_path, "a.jpg")
        self.index.load()

    def tearDown(self):
        self.index.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_an_unchanged_library_is_not_read_again(self):
        self.assertFalse(self.index.reload_if_changed())

    def test_a_photo_indexed_since_is_picked_up(self):
        add_photo(self.db_path, "b.jpg")  # by another connection: the CLI indexer
        self.assertTrue(self.index.reload_if_changed())
        self.assertEqual(2, len(self.index.metadata))
        self.assertFalse(self.index.reload_if_changed())

    def test_people_named_since_are_picked_up(self):
        # Naming a face changes who is in a photo and leaves the file, and so the
        # row's mtime, alone. The index watched mtimes (docs/findings.md, #52).
        from tagpup.store import faces
        conn = tagpup_db.connect(self.db_path)
        try:
            face_id = add_face(conn, os.path.join(self.dir, "a.jpg"))
            conn.commit()
        finally:
            conn.close()
        self.index.reload_if_changed()
        conn = tagpup_db.connect(self.db_path)
        try:
            faces.name(conn, [face_id], "Wren Halloway")
            conn.commit()
        finally:
            conn.close()
        self.assertTrue(self.index.reload_if_changed())
        self.assertEqual(["Wren Halloway"], self.index.metadata[0]["people"])


class OneIndexPerLibrary(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="library_index_")
        self.db_path = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db_path)
        index.load()
        index.close()
        add_photo(self.db_path, "a.jpg")
        self.library = Library(self.db_path)
        # No model is built: the index is all this asks for.
        self.runtime = Runtime(clip=object(), faces=object())

    def tearDown(self):
        self.runtime.photo_index(self.library).close()
        self.runtime.forget(self.library)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_second_run_reuses_the_first_runs_index(self):
        first = self.runtime.photo_index(self.library)
        second = self.runtime.photo_index(self.library)
        self.assertIs(first, second)

    def test_a_run_after_indexing_sees_the_new_photos(self):
        first = self.runtime.photo_index(self.library)
        self.assertEqual(1, len(first.metadata))
        add_photo(self.db_path, "b.jpg")
        again = self.runtime.photo_index(self.library)
        self.assertEqual(2, len(again.metadata))

    def test_its_vectors_are_the_runtimes_models(self):
        self.assertEqual(self.runtime.model_key(self.library), self.runtime.photo_index(self.library).model)


if __name__ == "__main__":
    unittest.main()
