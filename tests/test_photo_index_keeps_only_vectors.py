"""The in-memory photo index keeps only what a search needs: each photo's vector, by its
id. What a photo holds is read from the library for a search's results alone
(tagpup.services.search.PhotoIndex; owner, 2026-09-26: "if everything is going to be a
database call anyway, can the in-memory cache be minimized when not in use?").

On photo_index it held every row's record too -- 161 MB of raw_metadata and 71 MB of
paths, tags, people and captions beside the vectors -- for as long as the library was
open. The answers stay what they were on the same rows; the records are read as the
library holds them now.
"""
import os
import sys
import tracemalloc
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import db, schema  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402

MODEL = "ViT-B-32|openai|cropped|2.0|native"


def unit(*values):
    vector = np.zeros(8, dtype=np.float32)
    vector[:len(values)] = values
    return vector


class Base(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="index_vectors_")
        self.db_path = self.home.library("harbour.db")
        schema.ensure(self.db_path)
        self.folder = os.path.join(self.home.root, "Photos")
        os.makedirs(self.folder)

    def add(self, name, vector, tags=(), taken=None, raw_padding=0):
        """A photo as the indexer records it after reading `tags`, with its vector."""
        path = os.path.join(self.folder, name)
        fields = {"XMP:Subject": list(tags)}
        if taken:
            fields["EXIF:DateTimeOriginal"] = taken
        if raw_padding:
            fields["XMP:Notes"] = "x" * raw_padding
        conn = db.connect(self.db_path)
        try:
            photo_rows.add_read(conn, path, fields)
            if vector is not None:
                store_embeddings.put(conn, path, MODEL, 1.0, 1, np.asarray(vector, dtype=np.float32).tobytes())
            conn.commit()
        finally:
            conn.close()
        return path

    def index(self):
        index = PhotoIndex(self.db_path, MODEL)
        self.assertTrue(index.load())
        self.addCleanup(index.close)
        return index


class ASearch(Base):
    def test_answers_the_nearest_photos_with_what_each_holds(self):
        near = self.add("Regatta - 1.jpg", unit(1, 0), tags=["Activity/Rowing"], taken="2019:06:15 10:00:00")
        far = self.add("Harbour - 1.jpg", unit(0, 1), tags=["Places/Harbour"])
        self.add("Unread.jpg", None)
        found = self.index().search(unit(1, 0.1).tolist(), k=5)
        self.assertEqual([near, far], [record["path"] for _sim, record in found])
        self.assertGreater(found[0][0], found[1][0])
        record = found[0][1]
        self.assertEqual(["Activity/Rowing"], record["tags"])
        self.assertEqual(2019, record["year"])
        self.assertIn("Activity/Rowing", record["raw_metadata"]["XMP:Subject"])
        self.assertTrue(record["has_embedding"])
        for key in ("mtime", "size", "people", "captions"):
            self.assertIn(key, record)

    def test_reads_what_a_photo_holds_now_not_when_the_index_was_loaded(self):
        path = self.add("Regatta - 1.jpg", unit(1, 0), tags=["Activity/Rowing"])
        index = self.index()
        store_photos.record_tags(self.db_path, path, ["Activity/Sailing"])
        self.assertEqual(["Activity/Sailing"], index.search(unit(1, 0).tolist(), k=1)[0][1]["tags"])

    def test_leaves_out_a_photo_removed_since(self):
        gone = self.add("Gone.jpg", unit(1, 0))
        kept = self.add("Kept.jpg", unit(0.9, 0.1))
        index = self.index()
        conn = db.connect(self.db_path)
        try:
            store_photos.remove(conn, [gone])
            conn.commit()
        finally:
            conn.close()
        self.assertEqual([kept], [record["path"] for _sim, record in index.search(unit(1, 0).tolist(), k=2)])


class WhatItKeeps(Base):
    def test_only_the_vectors_by_photo_id(self):
        self.add("Regatta - 1.jpg", unit(1, 0), tags=["Activity/Rowing"])
        self.add("Unread.jpg", None)
        index = self.index()
        self.assertTrue(all(isinstance(item, int) for item in index.index.items), "it keeps records again")
        self.assertEqual((2, 1), (index.count, index.indexed))

    def test_far_less_than_the_records_of_its_photos(self):
        for n in range(300):
            self.add("Regatta - %d.jpg" % n, unit(1, n / 300), tags=["Activity/Rowing"], raw_padding=5000)
        tracemalloc.start()
        try:
            before = tracemalloc.get_traced_memory()[0]
            index = self.index()
            held = tracemalloc.get_traced_memory()[0] - before
        finally:
            tracemalloc.stop()
        self.assertEqual(300, index.indexed)
        # 300 records of 5 KB each were 1.5 MB and more; ids and a list are a few KB.
        self.assertLess(held, 300 * 1024, "the index holds %d bytes for 300 photos" % held)

    def test_every_photos_record_is_read_when_asked_and_not_kept(self):
        first = self.add("Regatta - 1.jpg", unit(1, 0), tags=["Activity/Rowing"])
        second = self.add("Unread.jpg", None)
        index = self.index()
        records = index.records()
        self.assertEqual([first, second], [record["path"] for record in records])
        self.assertEqual([True, False], [record["has_embedding"] for record in records])
        self.assertFalse(hasattr(index, "metadata"))


class ByIdInChunks(Base):
    def test_more_ids_than_one_statement_takes(self):
        paths = [self.add("Regatta - %d.jpg" % n, unit(1, n)) for n in range(7)]
        index = self.index()
        ids = [record["id"] for record in index.records()]
        with mock.patch.object(store_photos, "CHUNK", 3):
            found = index.records(list(reversed(ids)) + [10 ** 6])
        self.assertEqual(list(reversed(paths)), [record["path"] for record in found])


if __name__ == "__main__":
    unittest.main()
