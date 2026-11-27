"""A photo's CLIP vectors are its own: kept by id, one per model, and stamped with the
file they were computed from (tagpup.store.embeddings; docs/findings.md, #62, #65).

Before, the same vector was kept twice. The cache Suggest read was keyed by path and
the file's mtime, so every keyword write made the next Suggest embed the photo again;
a rotation left the vector describing the picture before the turn; and removing a
folder left its photos' vectors behind.

Lifted: test_rename_moves_suggestions_and_embeddings. Its two route tests are the rename in test_suggest_then_apply_all
(mutation: a rename that deletes the suggestions row, or the vectors, fails it); its swap is below (mutation: the
rename's placeholder step removed, so a swap collides on the unique path, fails it). A rename keeping the vectors and a
folder's removal taking them are scenario V9 (test_suggest_then_apply_all, through the routes; mutation: move_rows_in
deleting the photo's vectors, or remove_under deleting no photo, fails it).

A clip's frames 1 to 4 beside its vector (migration 38) follow the vector: replaced with it, taken with it or with the
photo, and put back with the photo by an undo (AClipsFrames).
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exiftool_fields  # noqa: E402
import photo_rows  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fake_exiftool  # noqa: E402
from service_fixture import TempLibrary  # noqa: E402

from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.services import search as search_service  # noqa: E402
from tagpup.services.search import PhotoEmbeddings, PhotoIndex  # noqa: E402
from tagpup.store import db, embeddings, journal, photos  # noqa: E402
from tagpup.files import shares  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402

MODEL = embeddings.model_key("ViT-T", "tiny", True, 1.4, 512)
OTHER = embeddings.model_key("ViT-T", "tiny", True, 1.4, None)
VECTOR = b"\x00\x00\x80\x3f" * 4


class Vectors(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.photo = self.lib.photo("beach.jpg")
        stat = os.stat(self.photo)
        self.lib.add_row(self.photo, mtime=stat.st_mtime, size=stat.st_size)
        self.keep(MODEL, stat.st_mtime, stat.st_size)

    def keep(self, model, mtime, size, photo=None, vector=VECTOR):
        conn = db.connect(self.lib.library.path)
        try:
            embeddings.put(conn, photo or self.photo, model, mtime, size, vector, None)
            conn.commit()
        finally:
            conn.close()

    def stored(self, model=MODEL, photo=None):
        conn = db.connect(db.readonly_uri(self.lib.library.path), uri=True)
        try:
            return embeddings.get(conn, photo or self.photo, model)
        finally:
            conn.close()

    def change_the_file(self):
        with open(self.photo, "ab") as f:
            f.write(b" and its keywords")
        return os.stat(self.photo)

    def written_by_the_app(self):
        """(stamp before, stat after) of a metadata write of the app's own."""
        before = shares.read_stamp(self.photo)
        return before, self.change_the_file()

    def test_a_keyword_write_keeps_the_vector_for_the_file_it_leaves(self):
        before, stat = self.written_by_the_app()
        photo_rows.tag(self.lib.library.path, self.photo, ["Places/Beach"], before=before)
        self.assertEqual((stat.st_mtime, stat.st_size), self.stored()[:2])

    def test_a_caption_or_a_time_shift_keeps_it_too(self):
        before, stat = self.written_by_the_app()
        photo_rows.written(self.lib.library.path, self.photo, {"XMP:Description": "At the beach"}, before=before)
        self.assertEqual((stat.st_mtime, stat.st_size), self.stored()[:2])


    def test_a_file_changed_elsewhere_is_not_made_current_by_recording_its_tags(self):
        # Turned in Explorer, then found by a tag rename that writes nothing and records
        # what the file holds: the row's stamp still matched the vector's, and the old
        # vector was carried onto the new file (#84).
        stored = self.stored()
        self.change_the_file()
        photo_rows.tag(self.lib.library.path, self.photo, ["Places/Beach"])
        self.assertEqual(stored, self.stored())

    def test_a_file_changed_elsewhere_and_then_tagged_in_the_app_is_embedded_again(self):
        stored = self.stored()
        self.change_the_file()
        before, _stat = self.written_by_the_app()
        photo_rows.tag(self.lib.library.path, self.photo, ["Places/Beach"], before=before)
        self.assertEqual(stored, self.stored())

    def test_a_vector_already_stale_is_not_made_current_by_a_write(self):
        self.keep(MODEL, 1.0, 1)
        before, _stat = self.written_by_the_app()
        photo_rows.tag(self.lib.library.path, self.photo, ["Places/Beach"], before=before)
        self.assertEqual((1.0, 1), self.stored()[:2])

    def test_an_embedding_without_its_model_is_refused(self):
        # Dropped without a word, the test environment's vectors never reached it (#85).
        conn = db.connect(self.lib.library.path)
        try:
            with self.assertRaises(ValueError):
                photos.record_indexed(conn, self.photo, {"embedding": VECTOR})
        finally:
            conn.close()

    def test_a_rotation_takes_the_vectors_away(self):
        fake_exiftool.standing_in(self).hold(self.photo, EXIF__Orientation=1)
        self.assertEqual(1, photo_actions.rotate(self.lib.library, self.photo, "right", exiftool_fields.opened(self, "exiftool")).changed)
        self.assertIsNone(self.stored())

    def test_two_photos_swapping_names_swap_their_vectors(self):
        # path is the photos table's unique key, so a swap passes through placeholders (the Smart Rename that
        # shuffles numbered files among themselves). The rename's own suggestions: test_suggest_then_apply_all.
        other, vector = self.lib.photo("dunes.jpg"), b"\x00\x00\x00\x40" * 4
        self.keep(MODEL, 1.0, 1, photo=other, vector=vector)
        photo_rows.move(self.lib.library.path, {self.photo: other, other: self.photo})
        self.assertEqual((VECTOR, vector), (self.stored(photo=other).vector, self.stored(photo=self.photo).vector))

    def test_deleting_the_photo_takes_them(self):
        self.keep(OTHER, 1.0, 1)
        photos.forget_photo(self.lib.library.path, self.photo)
        self.assertEqual([(0,)], self.lib.rows("SELECT COUNT(*) FROM embeddings"))

    def test_keeping_a_vector_says_whether_it_was_kept(self):
        """A pass counts what it kept: keep said nothing, and a failed write looked like one made (#1823)."""
        clip = mock.Mock(settings={"model_name": "ViT-T", "pretrained": "tiny", "preserve_full_frame": True,
                                   "max_aspect_ratio": 1.4, "force_image_size": None})
        index = PhotoIndex(self.lib.library.path)
        index.load()
        self.addCleanup(index.close)
        self.assertIs(True, PhotoEmbeddings(clip, index).keep(self.photo, [0.5] * 4))
        self.assertEqual(OTHER, PhotoEmbeddings(clip, index).model_key)
        self.assertIsNotNone(self.stored(OTHER))
        self.assertIs(False, PhotoEmbeddings(clip, index, remember=False).keep(self.photo, [0.5] * 4))
        with mock.patch.object(embeddings, "put", side_effect=OSError("the disk is full")), \
                self.assertLogs("tagpup_cli.embedder", level="WARNING"):
            self.assertIs(False, PhotoEmbeddings(clip, index).keep(self.photo, [0.5] * 4))

    def test_a_read_that_fails_is_not_a_photo_with_no_vector(self):
        """A locked library or a map missing a root raised, was logged and answered "no vector": the photo was embedded
        again and its vector written over what the library held. Now the read's sentence reaches the caller, before
        anything is made or written (#2732)."""
        clip = mock.Mock(settings={"model_name": "ViT-T", "pretrained": "tiny", "preserve_full_frame": True,
                                   "max_aspect_ratio": 1.4, "force_image_size": None})
        index = PhotoIndex(self.lib.library.path)
        index.load()
        self.addCleanup(index.close)
        vectors = PhotoEmbeddings(clip, index)
        before = self.lib.rows("SELECT COUNT(*) FROM embeddings")
        for failure in (paths.RootsError("a root this machine has no location for"), sqlite3.OperationalError("database is locked")):
            with mock.patch.object(embeddings, "get", side_effect=failure), \
                    mock.patch.object(vectors, "made") as made, mock.patch.object(vectors, "keep") as keep:
                with self.assertRaises(type(failure)) as raised:
                    vectors.of(self.photo)
                self.assertEqual(str(failure), str(raised.exception))
                made.assert_not_called()
                keep.assert_not_called()
        self.assertEqual(before, self.lib.rows("SELECT COUNT(*) FROM embeddings"))

    def test_a_photo_row_that_is_not_json_is_said_not_read_as_empty(self):
        """A damaged row was an empty photo in a Suggest neighbour (#2732)."""
        with self.assertRaises(ValueError) as raised:
            search_service._record((7, "a.jpg", 1.0, 1, "[not json", "[]", "[]", "{}", 2020, 0))
        self.assertIn("photo 7", str(raised.exception))
        self.assertEqual(["Places/Beach"], search_service._record((7, "a.jpg", 1.0, 1, '["Places/Beach"]', "[]", "[]", "{}", 2020, 0))["tags"])

    def test_each_model_keeps_its_own(self):
        self.keep(OTHER, 2.0, 2)
        self.assertEqual((2.0, 2), self.stored(OTHER)[:2])
        self.assertNotEqual((2.0, 2), self.stored(MODEL)[:2])

    def test_search_reads_only_the_model_it_is_for(self):
        conn = db.connect(self.lib.library.path)
        try:
            other_photo = self.lib.photo("dunes.jpg")
            embeddings.put(conn, other_photo, OTHER, 1.0, 1, VECTOR, None)
            conn.commit()
            vectors = dict(photos.vectors(conn, MODEL))
            (photo_id,), (other_id,) = store_roots.photo_ids_at(conn, [self.photo]), store_roots.photo_ids_at(conn, [other_photo])
        finally:
            conn.close()
        self.assertEqual(VECTOR, vectors[photo_id])
        self.assertNotIn(other_id, vectors)


class AClipsFrames(unittest.TestCase):
    """A clip is matched on its best frame: its vector is frame 0 and carries the stamp; frames 1 to 4 are kept beside
    it, by the one writer (embeddings.put, put_by_id), and never outlive it."""

    FRAMES = [bytes([n]) * 16 for n in range(1, 5)]

    def setUp(self):
        self.lib = TempLibrary(self)
        self.clip = self.lib.photo("harbour.mp4")
        self.lib.hold(os.path.dirname(self.clip))
        self.photo_id = self.write(lambda conn: photo_rows.add_read(conn, self.clip, {}))
        self.keep(MODEL, self.FRAMES)

    def write(self, work):
        conn = db.connect(self.lib.library.path)
        try:
            answer = work(conn)
            conn.commit()
            return answer
        finally:
            conn.close()

    def keep(self, model, frames):
        self.write(lambda conn: embeddings.put(conn, self.clip, model, 1.0, 1, VECTOR, None, frames=frames))

    def frames(self, model=MODEL):
        conn = db.connect(db.readonly_uri(self.lib.library.path), uri=True)
        try:
            return embeddings.frames_of(conn, self.photo_id, model)
        finally:
            conn.close()

    def test_deleting_the_clip_takes_its_frames(self):
        self.assertEqual(self.FRAMES, self.frames())
        photos.forget_photo(self.lib.library.path, self.clip)
        self.assertEqual([(0,)], self.lib.rows("SELECT COUNT(*) FROM embedding_frames"))

    def test_embedding_it_again_with_fewer_frames_leaves_exactly_the_new_ones(self):
        again = [b"\x07" * 16, b"\x08" * 16]
        self.keep(MODEL, again)
        self.assertEqual(again, self.frames())
        self.write(lambda conn: embeddings.put_by_id(conn, self.photo_id, MODEL, 2.0, 2, VECTOR, None))
        self.assertEqual([], self.frames(), "a vector written with no frames has none")

    def test_more_than_four_is_refused_and_nothing_changes(self):
        with self.assertRaises(ValueError):
            self.keep(MODEL, self.FRAMES + [b"\x09" * 16])
        self.assertEqual(self.FRAMES, self.frames())

    def test_a_clip_gone_meanwhile_gets_no_frames(self):
        photos.forget_photo(self.lib.library.path, self.clip)
        self.assertIs(False, self.write(lambda conn: embeddings.put_by_id(conn, self.photo_id, MODEL, 1.0, 1, VECTOR, None,
                                                                          frames=self.FRAMES)))
        self.assertEqual([(0,)], self.lib.rows("SELECT COUNT(*) FROM embedding_frames"))

    def test_forgetting_the_vectors_takes_the_frames(self):
        self.assertEqual(1, self.write(lambda conn: embeddings.forget(conn, self.clip)))
        self.assertEqual([(0,)], self.lib.rows("SELECT COUNT(*) FROM embedding_frames"))

    def test_retiring_a_model_takes_its_frames_and_keeps_the_others(self):
        self.keep(OTHER, self.FRAMES[:2])
        (last, _), = self.write(lambda conn: embeddings.next_under(conn, MODEL, 0, 10))
        self.assertEqual(1, self.write(lambda conn: embeddings.delete_between(conn, MODEL, 0, last)))
        self.assertEqual(([], self.FRAMES[:2]), (self.frames(), self.frames(OTHER)))

    def test_a_journaled_delete_of_the_clip_and_its_undo_bring_the_frames_back(self):
        deletion = journal.apply(self.lib.library.path, "delete", [journal.delete("photos", (self.photo_id,), {})])
        self.assertEqual([(0,)], self.lib.rows("SELECT COUNT(*) FROM embedding_frames"))
        journal.undo(self.lib.library.path, deletion.change_id)
        self.assertEqual(self.FRAMES, self.frames())
        self.assertEqual(VECTOR, self.write(lambda conn: embeddings.get(conn, self.clip, MODEL)).vector)


class AnEmbedding(unittest.TestCase):
    def test_is_stamped_with_the_file_as_it_was_opened(self):
        # Rotated while Suggest embedded it: the rotation took the vector away, and the
        # vector from before the turn came back stamped as the file after it (#86).
        import torch
        from PIL import Image

        from tagpup.ml.clip import ClipModel
        from tagpup.services.search import PhotoEmbeddings

        lib = TempLibrary(self)
        photo = os.path.join(lib.photos, "turned.jpg")
        Image.new("RGB", (8, 8)).save(photo)
        lib.add_folder(photo)
        opened = shares.read_stamp(photo)
        index = mock.Mock(conn=True, write_on_own_connection=lambda op, label: db.write_with_connection(
            lib.library.path, op, label=label))
        embedder = ClipModel(model_name="ViT-T", pretrained="tiny", preserve_full_frame=True,
                             max_aspect_ratio=1.4, force_image_size=None, device="cpu")
        vectors = PhotoEmbeddings(embedder, index)

        def turned_while_embedding(image):
            with open(photo, "ab") as f:
                f.write(b"turned")
            return torch.zeros((3, 2, 2))

        def init_model():
            embedder.model = mock.Mock(encode_image=lambda batch: torch.ones((1, 4)))
            embedder.preprocess = turned_while_embedding

        embedder._init_model = init_model
        vectors.of(photo, force_recompute=True)
        conn = db.connect(db.readonly_uri(lib.library.path), uri=True)
        try:
            stored = embeddings.get(conn, photo, vectors.model_key)
        finally:
            conn.close()
        self.assertEqual(opened, stored[:2])
        self.assertNotEqual(shares.read_stamp(photo), stored[:2])


if __name__ == "__main__":
    unittest.main()
