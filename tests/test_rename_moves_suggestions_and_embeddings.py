"""Smart Rename takes a photo's saved suggestions and cached embedding with it.

Renaming moved the photo's index row and its faces, and left two things under the old
name: the suggestions saved for it, which the page looks up by path, and its cached
CLIP embedding. The renamed photos showed no suggestions, the next Suggest ran them
again from scratch, and the old entries stayed in the cache for ever.

The vectors now point at the photo's row by id (migration 5), so a rename has nothing
of theirs to move; these check they are still the photo's under its new name.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_rows import VECTORS_WITH_PATHS  # noqa: E402
from test_tagpup_server_finds_native_rows import (  # noqa: E402
    HandlerCase, fake_exiftool, fake_extractor, forward, key_of)

import db as tagpup_db  # noqa: E402
from tagpup_server import set_active_db_path  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402


class RenameMovesSuggestionsAndEmbeddings(HandlerCase):
    def setUp(self):
        super().setUp()
        self.files = [self.make_file(n) for n in ("a.jpg", "b.jpg")]
        for path in self.files:
            self.seed(path)  # with a vector, stamped with the file as it is on disk
        set_active_db_path(self.db_path)
        suggestion_jobs.runs_for(Library(self.db_path)).statuses[key_of(self.folder)] = {
            "status": "completed", "total": 2, "completed": 2,
            "suggestions": {
                os.path.abspath(p): {"tags": ["Activity/Rowing"], "people": [], "title": "",
                                     "raw_suggestions": {"path": os.path.abspath(p)}}
                for p in self.files},
        }

    def rename(self):
        with patch("exiftool_session.ExifToolSession", fake_exiftool([{}])), \
                patch("metadata.MetadataExtractor", fake_extractor()):
            return self.call("handle_post_folder_rename_photos", {
                "folder_path": forward(self.folder),
                "photo_paths": [forward(p) for p in self.files],
                "grouping": "Regatta",
            })

    def test_the_saved_suggestions_follow_the_photos(self):
        renamed = self.rename()["updated_paths"]
        set_active_db_path(self.db_path)
        saved = suggestion_jobs.runs_for(Library(self.db_path)).statuses[key_of(self.folder)]["suggestions"]
        self.assertEqual(sorted(os.path.abspath(p) for p in renamed.values()), sorted(saved),
                         "suggestions are still filed under the old names")
        for path, entry in saved.items():
            self.assertEqual(path, entry["raw_suggestions"]["path"])

    def test_the_cached_embeddings_follow_the_photos(self):
        renamed = self.rename()["updated_paths"]
        for new in renamed.values():
            self.assertEqual(1, self.count("SELECT COUNT(*) FROM " + VECTORS_WITH_PATHS + " WHERE p.path = ?",
                                           os.path.abspath(new)), new)
        self.assertEqual(2, self.count("SELECT COUNT(*) FROM embeddings"))

    def test_two_photos_swapping_names_swap_their_embeddings(self):
        # path is the photos table's unique key, so a swap has to pass through placeholders.
        import tagpup_server
        a, b = (os.path.abspath(p) for p in self.files)
        conn = tagpup_db.connect(self.db_path)
        conn.execute("UPDATE embeddings SET vector = x'aa'"
                     " WHERE photo_id = (SELECT id FROM photos WHERE path = ?)", (a,))
        conn.commit()
        conn.close()

        tagpup_server.move_photo_rows(self.db_path, {a: b, b: a})

        conn = tagpup_db.connect(self.db_path)
        try:
            vector = conn.execute("SELECT e.vector FROM " + VECTORS_WITH_PATHS + " WHERE p.path = ?",
                                  (b,)).fetchone()
        finally:
            conn.close()
        self.assertEqual((b"\xaa",), vector)


if __name__ == "__main__":
    unittest.main()
