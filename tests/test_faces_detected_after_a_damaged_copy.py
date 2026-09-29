"""A photo indexed from a damaged copy has its faces detected again once its file is whole,
even when Suggest made its vector first (docs/findings.md, #407).

forget_to_reindex takes the photo's vectors and undecided faces away and queues its
folder. The indexer passes over a photo whose row describes its file and that has a
vector -- and Suggest makes a vector of any photo it looks at: if it did so before the
queued index ran, the index skipped the photo and its faces were never detected. Now the
photo is marked (store.faces_pending) and the indexer detects its faces whatever vector
it has; a sync that queues nothing -- the MCP server's -- leaves the mark, counted by the
doctor and the Activity page.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_damaged_photos_reindexed import DIM, EXIFTOOL, Case  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.services import sync  # noqa: E402
from tagpup.store import checks, db  # noqa: E402
from tagpup.web import app as web  # noqa: E402


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class SuggestFirst(Case):
    def synced_without_indexing(self):
        queued = []
        sync.sync(self.library, apply=True, exiftool_path=EXIFTOOL,
                  queue=lambda folders: (queued.append(list(folders)), Result(attempted=1, changed=1))[1])
        return queued

    def suggest_embeds_it(self):
        """What Suggest does for a photo it looks at with no vector: makes one, and keeps it."""
        with mock.patch("tagpup.ml.clip.ClipModel._init_model"), \
                mock.patch("tagpup.ml.clip.ClipModel.embed_picture", side_effect=lambda img: [0.5] * DIM):
            runtime = tagpup_cli.get_runtime()
            index = tagpup_cli.library_index(runtime, self.db_path)
            index.load()
            try:
                runtime.embeddings(Library(self.db_path), index).of(self.half)
            finally:
                index.close()

    def to_detect(self):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return checks.faces_to_detect(conn)
        finally:
            conn.close()

    def test_the_queued_index_detects_its_faces_though_suggest_made_its_vector(self):
        self.replace_with_the_whole_photo()
        self.assertEqual([[self.folder]], self.synced_without_indexing())
        self.assertEqual([], self.faces())
        self.suggest_embeds_it()
        self.assertEqual(1, len(self.vector()))
        self.index()
        self.assertEqual([("[30, 30, 60, 60]", None)], self.faces(), "its faces were never detected")
        self.assertEqual(0, self.to_detect())

    def test_a_sync_that_queues_nothing_leaves_it_marked_and_seen(self):
        self.replace_with_the_whole_photo()
        sync.sync(self.library, apply=True, exiftool_path=EXIFTOOL, queue=None)   # the MCP server's
        self.assertEqual(1, self.to_detect())
        app = web.create_app("tagpup")
        app.testing = True
        harbour = next(each for each in app.test_client().get("/api/activity/attention").get_json()["libraries"]
                       if each["name"] == Library(self.db_path).name)
        self.assertEqual(1, harbour["faces_to_detect"])
        self.index()
        self.assertEqual(0, self.to_detect())


if __name__ == "__main__":
    unittest.main()
