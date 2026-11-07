"""A clip is matched on its best frame (M2; DECISIONS 2026-11-06, migration 38): the photo index holds a clip's frame 0
(its vector) and frames 1 to 4 (embedding_frames) as vectors of their own under the clip's id, and a search -- Suggest's
neighbours (services.suggester reads PhotoIndex.search) -- answers each photo once, at its nearest vector
(tagpup.core.suggesting.best_frame), in a total order: most alike first, ties by photo id.

The scenarios: a clip whose middle frame is the one like the query; frames crowding the top still giving k photos; a
clip cut short (fewer frames) and one with frame 0 alone (best of one: photo_index holds one, counted 2026-11-07); ties
across photos whatever order the vectors were read in; k larger than the index; a photo removed or its vector retired
since the index was read; two libraries, each its own clips; a library with no clip searching as before. The rows are
made as the indexer makes them (photo_rows.add_read, store.embeddings.put), in a home of their own. Fictional names only.
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.services import search  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import db, schema  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402

MODEL = "ViT-SO400M-14-SigLIP2|webli|full-frame|1.4|native"


def unit(*values):
    vector = np.zeros(8, dtype=np.float32)
    vector[:len(values)] = values
    return vector


def blob(vector):
    return np.asarray(vector, dtype=np.float32).tobytes()


class Library:
    """A library in the test's own home, its photos and clips recorded as the indexer records them."""

    def __init__(self, test, home, name):
        self.test = test
        self.path = home.library(name)
        schema.ensure(self.path)
        self.folder = os.path.join(home.root, os.path.splitext(name)[0])
        os.makedirs(self.folder)

    def add(self, name, vector, frames=(), tags=()):
        """A photo (or, named .mp4, a clip with its `frames` 1 to n) with its vector; returns its id."""
        path = os.path.join(self.folder, name)
        conn = db.connect(self.path)
        try:
            photo_id = photo_rows.add_read(conn, path, {"XMP:Subject": list(tags)})
            store_embeddings.put(conn, path, MODEL, 1.0, 1, blob(vector), None, frames=[blob(f) for f in frames])
            conn.commit()
        finally:
            conn.close()
        return photo_id

    def run(self, statement, values=()):
        conn = db.connect(self.path)
        try:
            conn.execute(statement, values)
            conn.commit()
        finally:
            conn.close()

    def index(self):
        index = PhotoIndex(self.path, MODEL)
        self.test.assertTrue(index.load())
        self.test.addCleanup(index.close)
        return index


def found(index, query, k):
    return [(round(sim, 4), record["id"]) for sim, record in index.search(unit(*query).tolist(), k=k)]


class Base(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="best_frame_")
        self.lib = Library(self, self.home, "harbour.db")


class AClipIsMatchedOnItsBestFrame(Base):
    def test_a_clip_whose_middle_frame_is_like_the_query_is_found_at_that_frame_once(self):
        photo = self.lib.add("Regatta - 1.jpg", unit(0.6, 0.8))
        clip = self.lib.add("Regatta - 2.mp4", unit(0, 1), frames=[unit(0, 1, 1), unit(1, 0), unit(0, 0, 1), unit(0, 1)])
        index = self.lib.index()
        self.assertEqual(search.CLIP_PICTURES, 5)
        self.assertEqual((2, 6), (index.indexed, index.index.ntotal))   # two photos, six vectors
        # Frame 0 alone (0, 1) is less like (1, 0) than the photo; its frame 2 is the query itself.
        self.assertEqual([(1.0, clip), (0.6, photo)], found(index, (1, 0), k=5))

    def test_a_clip_cut_short_and_one_of_frame_0_alone_are_each_one_photo(self):
        short = self.lib.add("Jetty - 1.mp4", unit(0, 1), frames=[unit(1, 0)])
        alone = self.lib.add("Jetty - 2.mp4", unit(0.8, 0.6))
        index = self.lib.index()
        self.assertEqual((2, 3), (index.indexed, index.index.ntotal))
        self.assertEqual([(1.0, short), (0.8, alone)], found(index, (1, 0), k=2))

    def test_frames_crowding_the_top_still_give_k_photos(self):
        clips = [self.lib.add("Cove - %d.mp4" % n, unit(1, 0.01 * n), frames=[unit(1, 0.01 * n + 0.001 * f)
                                                                              for f in range(1, 5)])
                 for n in range(1, 4)]
        photo = self.lib.add("Cove - 9.jpg", unit(1, 0.5))
        index = self.lib.index()
        self.assertEqual(clips, [photo_id for _sim, photo_id in found(index, (1, 0), k=3)])
        self.assertEqual(clips + [photo], [photo_id for _sim, photo_id in found(index, (1, 0), k=4)])

    def test_k_larger_than_the_index_answers_every_photo_once(self):
        ids = [self.lib.add("Dunes - 1.mp4", unit(1, 0), frames=[unit(1, 0.1)] * 4),
               self.lib.add("Dunes - 2.jpg", unit(0, 1))]
        self.assertEqual(ids, [photo_id for _sim, photo_id in found(self.lib.index(), (1, 0), k=50)])


class TiesAreOrderedByPhotoId(Base):
    def test_a_tie_between_a_clips_late_frame_and_a_photo_puts_the_lower_id_first(self):
        # The clip's id is lower, but its tying vector is a frame, read after every photo's vector: in the order the
        # vectors were read the photo came first.
        clip = self.lib.add("Pier - 1.mp4", unit(0, 1), frames=[unit(0, 0, 1), unit(0, 0, 1), unit(1, 0)])
        photo = self.lib.add("Pier - 2.jpg", unit(1, 0))
        self.assertLess(clip, photo)
        index = self.lib.index()
        self.assertEqual([(1.0, clip), (1.0, photo)], found(index, (1, 0), k=2))
        self.assertEqual([(1.0, clip)], found(index, (1, 0), k=1))


class WhatChangedSinceTheIndexWasRead(Base):
    def test_a_photo_removed_since_is_left_out_and_a_vector_retired_since_is_gone_at_the_next_load(self):
        gone = self.lib.add("Quay - 1.mp4", unit(1, 0), frames=[unit(1, 0.1)] * 4)
        retired = self.lib.add("Quay - 2.mp4", unit(1, 0.2), frames=[unit(1, 0.3)] * 4)
        kept = self.lib.add("Quay - 3.jpg", unit(1, 0.4))
        index = self.lib.index()
        self.lib.run("DELETE FROM photos WHERE id = ?", (gone,))
        self.lib.run("DELETE FROM embeddings WHERE photo_id = ?", (retired,))   # its frames go with it (the trigger)
        # Searched from the vectors read: the removed photo has no record to answer; the retired one's photo is still
        # the library's, answered until the index is read again.
        self.assertEqual([retired, kept], [photo_id for _sim, photo_id in found(index, (1, 0), k=3)])
        self.assertTrue(index.load())
        self.assertEqual((1, 1), (index.indexed, index.index.ntotal))
        self.assertEqual([kept], [photo_id for _sim, photo_id in found(index, (1, 0), k=3)])


class TwoLibraries(Base):
    def test_each_library_searches_its_own_clips_frames(self):
        other = Library(self, self.home, "cliffs.db")
        here = self.lib.add("Bay - 1.mp4", unit(0, 1), frames=[unit(1, 0)] * 4)
        there = other.add("Bay - 1.mp4", unit(0, 1), frames=[unit(0, 0, 1)] * 2)
        self.assertEqual([(1.0, here)], found(self.lib.index(), (1, 0), k=1))
        index = other.index()
        self.assertEqual((1, 3), (index.indexed, index.index.ntotal))
        self.assertEqual([(0.0, there)], found(index, (1, 0), k=1))


class ALibraryWithNoClip(Base):
    def test_searches_its_photos_as_before_one_vector_each(self):
        ids = [self.lib.add("Lake - %d.jpg" % n, unit(1, 0.2 * n)) for n in range(1, 4)]
        index = self.lib.index()
        self.assertEqual((3, 3), (index.indexed, index.index.ntotal))
        self.assertEqual(ids[:2], [photo_id for _sim, photo_id in found(index, (1, 0), k=2)])


class TheFramesRead(Base):
    def test_scans_the_frames_table_and_seeks_each_photo_by_its_key(self):
        """The frames are read in a statement of their own, a SCAN of embedding_frames (4 rows a clip: 8,708 on
        photo_index, 2026-11-07), each photo found by its primary key; never a scan of photos, nor the frame-0 vector read
        again beside each frame."""
        self.lib.add("Reef - 1.mp4", unit(1, 0), frames=[unit(1, 0.1)] * 4)
        conn = db.connect(db.readonly_uri(self.lib.path), uri=True)
        statements = []
        conn.set_trace_callback(statements.append)
        try:
            self.assertEqual(4, len(store_photos.frame_vectors(conn, MODEL)))
            conn.set_trace_callback(None)
            self.assertEqual(1, len(statements))
            self.assertNotIn("embeddings e", statements[0])
            plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + statements[0])]
        finally:
            conn.close()
        self.assertEqual(["SCAN f", "SEARCH p USING INTEGER PRIMARY KEY (rowid=?)"], plan)


if __name__ == "__main__":
    unittest.main()
