"""Suggest reads each library's index once, and again only when it has changed.

The library TagPup started on kept the index it loaded at startup for as long as the
server ran: photos indexed afterwards were never neighbours, and tags saved afterwards
never counted, until a restart. Every other library went the other way and loaded its
whole index again for every Suggest -- 1.3s on a 68,000-photo library, each click.

Now each library has one embedder and one index, and a Suggest run reloads the index
only when the photos table has changed since it was read.
"""
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db as tagpup_db  # noqa: E402
from index import PhotoIndex  # noqa: E402
from tagpup_server import TagPupHTTPRequestHandler, set_active_db_path  # noqa: E402


def add_photo(db_path, name):
    conn = tagpup_db.connect(db_path)
    try:
        conn.execute("INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata, embedding)"
                     " VALUES (?, 1.0, 1, '[\"Activity/Rowing\"]', '[]', '[]', '{}', ?)",
                     (os.path.join(os.path.dirname(db_path), name),
                      np.ones(512, dtype=np.float32).tobytes()))
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


class OneEmbedderPerLibrary(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="library_embedder_")
        self.db_path = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db_path)
        index.load()
        index.close()
        add_photo(self.db_path, "a.jpg")
        self.kwargs = {"model_name": "ViT-B-32", "pretrained": "laion2b_s34b_b79k",
                       "cache_dir": os.path.join(self.dir, "cache")}
        set_active_db_path(self.db_path)

    def tearDown(self):
        set_active_db_path(self.db_path)
        embedder = TagPupHTTPRequestHandler.shared_embedder
        if embedder is not None:
            embedder.photo_index.close()
        TagPupHTTPRequestHandler.shared_embedder = None
        set_active_db_path(None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_second_run_reuses_the_first_runs_embedder(self):
        first = TagPupHTTPRequestHandler.library_embedder(self.db_path, self.kwargs)
        second = TagPupHTTPRequestHandler.library_embedder(self.db_path, self.kwargs)
        self.assertIs(first, second)

    def test_a_run_after_indexing_sees_the_new_photos(self):
        first = TagPupHTTPRequestHandler.library_embedder(self.db_path, self.kwargs)
        self.assertEqual(1, len(first.photo_index.metadata))
        add_photo(self.db_path, "b.jpg")
        again = TagPupHTTPRequestHandler.library_embedder(self.db_path, self.kwargs)
        self.assertEqual(2, len(again.photo_index.metadata))


if __name__ == "__main__":
    unittest.main()
