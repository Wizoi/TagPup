"""A tag-tree rename, merge or retire of a tag a clip holds (DECISIONS 2026-11-05, M7; #2503): the tree's rewrite of
its photos patches each clip's XMP as a tag save does (file_changes.leave_out_clips lets a keyword write of a clip
through), and the tree moves once every file is rewritten.

What the owner lives through: a tag is renamed in the tag tree, merged into another in TagTuner, or retired, and a clip
of the library holds it. The clip is rewritten with the photos, its row follows and the tree
moves.

Real ffmpeg clips and the real ExifTool (tests/test_a_clip_tagged_through_the_journal.ALibraryHoldingAClip). Fictional
names only.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_a_clip_tagged_through_the_journal as tagged  # noqa: E402

from tagpup.services import tags  # noqa: E402
from tagpup.store import taxonomy  # noqa: E402

EXIFTOOL = tagged.EXIFTOOL


class ATagTreeEditOfATagAClipHolds(tagged.ALibraryHoldingAClip):

    def tagged(self, *photo_paths, tag="Trips/Coast"):
        """Index `photo_paths` and give each the tag through the pages; their ids."""
        ids = self.index(*photo_paths)
        reply = self.post("photos/write", {"op": "tags", "changes": {photo: {"add": [tag]} for photo in photo_paths}})
        self.assertEqual((200, True), (reply.status_code, reply.get_json()["success"]), reply.get_json())
        return ids

    def node(self, tag):
        found = taxonomy.find(self.vl.path, tag)
        return found and found["id"]

    def assert_holds(self, tag_list, photo_id, path):
        self.assertEqual((tag_list, tag_list), self.read_now(path), "the file holds it")
        self.assertEqual(tag_list, self.tags_of(photo_id), "and its row follows")

    def test_a_rename_in_the_tree_rewrites_the_clip_and_the_photo_and_moves_the_node(self):
        photo, clip = self.tagged(self.photo_path, self.clip_path)
        reply = self.post("taxonomy/rename", {"tag_id": self.node("Trips/Coast"), "new_name": "Shore"})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual((True, 2), (reply.get_json()["success"], reply.get_json()["photos_rewritten"]), reply.get_json())
        self.assert_holds(["Trips/Shore"], clip, self.clip_path)
        self.assert_holds(["Trips/Shore"], photo, self.photo_path)
        self.assertEqual(["done", "done"], self.change_states(clip), "the tag save and the rename, each journaled done")
        self.assertEqual((None, True), (self.node("Trips/Coast"), bool(self.node("Trips/Shore"))))

    def test_a_merge_into_another_tag_rewrites_the_clip(self):
        clip, = self.tagged(self.clip_path)
        result = tags.merge(self.vl.library, "Trips/Coast", "Trips/Harbour", EXIFTOOL, apply=True)
        self.assertEqual((True, []), (result.details["applied"], result.errors), result.message())
        self.assert_holds(["Trips/Harbour"], clip, self.clip_path)
        self.assertIsNone(self.node("Trips/Coast"))

    def test_a_retire_takes_the_tag_off_the_clip(self):
        clip, = self.tagged(self.clip_path)
        self.tagged(self.clip_path, tag="Trips/Pier")
        result = tags.merge(self.vl.library, "Trips/Coast", "", EXIFTOOL, retire=True, apply=True)
        self.assertEqual((True, []), (result.details["applied"], result.errors), result.message())
        self.assert_holds(["Trips/Pier"], clip, self.clip_path)
        self.assertIsNone(self.node("Trips/Coast"))


if __name__ == "__main__":
    unittest.main()
