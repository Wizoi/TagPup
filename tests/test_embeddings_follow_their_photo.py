"""A photo's CLIP vectors are its own: kept by id, one per model, and stamped with the
file they were computed from (tagpup.store.embeddings; docs/findings.md, #62, #65).

Before, the same vector was kept twice. The cache Suggest read was keyed by path and
the file's mtime, so every keyword write made the next Suggest embed the photo again;
a rotation left the vector describing the picture before the turn; and removing a
folder left its photos' vectors behind.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from service_fixture import TempLibrary  # noqa: E402

from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.store import db, embeddings, photos  # noqa: E402

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

    def keep(self, model, mtime, size, photo=None):
        conn = db.connect(self.lib.library.path)
        try:
            embeddings.put(conn, photo or self.photo, model, mtime, size, VECTOR)
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
        before = embeddings.stamp_of(self.photo)
        return before, self.change_the_file()

    def test_a_keyword_write_keeps_the_vector_for_the_file_it_leaves(self):
        before, stat = self.written_by_the_app()
        photos.record_tags(self.lib.library.path, self.photo, ["Places/Beach"], before=before)
        self.assertEqual((stat.st_mtime, stat.st_size), self.stored()[:2])

    def test_a_caption_or_a_time_shift_keeps_it_too(self):
        before, stat = self.written_by_the_app()
        photos.record_file_stat(self.lib.library.path, self.photo, before=before)
        self.assertEqual((stat.st_mtime, stat.st_size), self.stored()[:2])
        before, stat = self.written_by_the_app()
        photos.record_reads(self.lib.library.path, [{"path": self.photo, "raw_metadata": {"a": 1},
                                                     "mtime": stat.st_mtime, "size": stat.st_size}],
                            before={self.photo: before})
        self.assertEqual((stat.st_mtime, stat.st_size), self.stored()[:2])

    def test_a_file_changed_elsewhere_and_read_back_is_embedded_again(self):
        stat = self.change_the_file()
        before = self.stored()
        photos.record_reads(self.lib.library.path, [{"path": self.photo, "raw_metadata": {"a": 1},
                                                     "mtime": stat.st_mtime, "size": stat.st_size}])
        self.assertEqual(before, self.stored())

    def test_a_file_changed_elsewhere_is_not_made_current_by_recording_its_tags(self):
        # Turned in Explorer, then found by a tag rename that writes nothing and records
        # what the file holds: the row's stamp still matched the vector's, and the old
        # vector was carried onto the new file (#84).
        stored = self.stored()
        self.change_the_file()
        photos.record_tags(self.lib.library.path, self.photo, ["Places/Beach"])
        self.assertEqual(stored, self.stored())

    def test_a_file_changed_elsewhere_and_then_tagged_in_the_app_is_embedded_again(self):
        stored = self.stored()
        self.change_the_file()
        before, _stat = self.written_by_the_app()
        photos.record_tags(self.lib.library.path, self.photo, ["Places/Beach"], before=before)
        self.assertEqual(stored, self.stored())

    def test_a_vector_already_stale_is_not_made_current_by_a_write(self):
        self.keep(MODEL, 1.0, 1)
        before, _stat = self.written_by_the_app()
        photos.record_tags(self.lib.library.path, self.photo, ["Places/Beach"], before=before)
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
        with mock.patch("tagpup.files.images.shown_size", return_value=(40, 30, False)), \
                mock.patch("tagpup.files.metadata.rotate_image_file", return_value=6):
            photo_actions.rotate(self.lib.library, self.photo, "right", "exiftool")
        self.assertIsNone(self.stored())

    def test_a_rename_keeps_them(self):
        renamed = os.path.join(os.path.dirname(self.photo), "renamed.jpg")
        photos.move_rows(self.lib.library.path, {self.photo: renamed})
        self.assertEqual(VECTOR, self.stored(photo=renamed).vector)

    def test_deleting_the_photo_takes_them(self):
        self.keep(OTHER, 1.0, 1)
        photos.forget_photo(self.lib.library.path, self.photo)
        self.assertEqual([(0,)], self.lib.rows("SELECT COUNT(*) FROM embeddings"))

    def test_removing_the_folder_takes_them(self):
        conn = db.connect(self.lib.library.path)
        try:
            photos.remove_under(conn, os.path.dirname(self.photo))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual([(0,)], self.lib.rows("SELECT COUNT(*) FROM embeddings"))

    def test_each_model_keeps_its_own(self):
        self.keep(OTHER, 2.0, 2)
        self.assertEqual((2.0, 2), self.stored(OTHER)[:2])
        self.assertNotEqual((2.0, 2), self.stored(MODEL)[:2])

    def test_search_reads_only_the_model_it_is_for(self):
        conn = db.connect(self.lib.library.path)
        try:
            other_photo = self.lib.photo("dunes.jpg")
            embeddings.put(conn, other_photo, OTHER, 1.0, 1, VECTOR)
            conn.commit()
            vectors = {path: vector for path, *_rest, vector in photos.index_rows(conn, MODEL)}
        finally:
            conn.close()
        self.assertEqual(VECTOR, vectors[self.photo])
        self.assertIsNone(vectors[other_photo])


class AnEmbedding(unittest.TestCase):
    def test_is_stamped_with_the_file_as_it_was_opened(self):
        # Rotated while Suggest embedded it: the rotation took the vector away, and the
        # vector from before the turn came back stamped as the file after it (#86).
        import torch
        from embedder import ClipEmbedder
        from PIL import Image

        lib = TempLibrary(self)
        photo = os.path.join(lib.photos, "turned.jpg")
        Image.new("RGB", (8, 8)).save(photo)
        opened = embeddings.stamp_of(photo)
        index = mock.Mock(conn=True, write_on_own_connection=lambda op, label: db.write_with_connection(
            lib.library.path, op, label=label))
        embedder = ClipEmbedder(model_name="ViT-T", pretrained="tiny", photo_index=index)
        embedder.device = "cpu"

        def turned_while_embedding(image):
            with open(photo, "ab") as f:
                f.write(b"turned")
            return torch.zeros((3, 2, 2))

        def init_model():
            embedder.model = mock.Mock(encode_image=lambda batch: torch.ones((1, 4)))
            embedder.preprocess = turned_while_embedding

        embedder._init_model = init_model
        embedder.embed_image(photo, force_recompute=True)
        conn = db.connect(db.readonly_uri(lib.library.path), uri=True)
        try:
            stored = embeddings.get(conn, photo, embedder.model_key)
        finally:
            conn.close()
        self.assertEqual(opened, stored[:2])
        self.assertNotEqual(embeddings.stamp_of(photo), stored[:2])


if __name__ == "__main__":
    unittest.main()
