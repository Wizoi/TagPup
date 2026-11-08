"""A tag-tree rename, merge or retire of a tag a clip holds (DECISIONS 2026-11-05, M7; #2503): the tree's rewrite of
its photos patches each clip's XMP as a tag save does (file_changes.leave_out_clips lets a keyword write of a clip through
while writing clips is on), and the tree moves once every file is rewritten.

What the owner lives through: a tag is renamed in the tag tree, merged into another in TagTuner, or retired, and a clip
of the library holds it. With writing clips on, the clip is rewritten with the photos, its row follows and the tree
moves. A node the reader of before made of a clip's QuickTime Subject that is not text (four on photo_index, #2662)
is left with no photo once the rows are re-derived, and deleted from the tree with no file written. With writing clips
off, the photos beside the clip are rewritten, the clip is named in the reply with the
sentence why it was left, and the tree keeps the old name: the clip still carries it.

Real ffmpeg clips and the real ExifTool (tests/test_a_clip_tagged_through_the_journal.ALibraryHoldingAClip). Fictional
names only.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_a_clip_tagged_through_the_journal as tagged  # noqa: E402

from tagpup.files import images  # noqa: E402
from tagpup.services import refresh_rows, tags  # noqa: E402
from tagpup.store import taxonomy  # noqa: E402

EXIFTOOL = tagged.EXIFTOOL


class ATagTreeEditOfATagAClipHolds(tagged.ALibraryHoldingAClip):

    def tagged(self, *photo_paths, tag="Trips/Coast"):
        """Index `photo_paths` and give each the tag through the pages; their ids."""
        ids = self.index(*photo_paths)
        reply = self.post("photos/bulk-tags", {"paths": list(photo_paths), "add_tags": [tag]})
        self.assertEqual(200, reply.status_code, reply.get_json())
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

    def test_a_node_made_of_a_quicktime_subject_that_is_not_text_is_deleted_once_its_clip_is_re_derived(self):
        held = tagged.with_a_quicktime_subject_that_is_not_text(self.clip_path)
        with tagged.read_as_before():
            clip, = self.index(self.clip_path)
        self.vl.tree(held)
        self.assertEqual([held], self.tags_of(clip))
        done = refresh_rows.rederive(self.vl.library, apply=True)
        self.assertEqual((True, 1), (done.ok, done.changed), done.message())
        before = self.read_bytes(self.clip_path)
        reply = self.post("taxonomy/delete-confirm", {"tag_id": self.node(held), "action": "delete"})
        body = reply.get_json()
        self.assertEqual((True, 0), (body["success"], body["photos_rewritten"]), body)
        self.assertIsNone(self.node(held), "the node is gone")
        self.assertEqual(before, self.read_bytes(self.clip_path), "no file is written")
        self.assertEqual([], self.tags_of(clip))

    def test_with_writing_clips_off_a_rename_rewrites_the_photo_names_the_clip_left_and_keeps_the_old_name(self):
        photo, clip = self.tagged(self.photo_path, self.clip_path)
        written = self.read_bytes(self.clip_path)
        with tagged.writing_clips(False):
            reply = self.post("taxonomy/rename", {"tag_id": self.node("Trips/Coast"), "new_name": "Shore"})
        body = reply.get_json()
        self.assertEqual((False, False, 1), (body["success"], body["renamed"], body["photos_rewritten"]), body)
        self.assertEqual([images.CLIP_NOT_EDITED], [each["why"] for each in body["failed"]], "the clip, and why it was left")
        self.assertIn("jetty.mp4: " + images.CLIP_NOT_EDITED, body["error"], "in the sentence the page alerts too")
        self.assert_holds(["Trips/Shore"], photo, self.photo_path)
        self.assertEqual(written, self.read_bytes(self.clip_path), "the clip is as it was")
        self.assertEqual(["Trips/Coast"], self.tags_of(clip))
        self.assertTrue(self.node("Trips/Coast"), "the clip still carries the old name, so the tree keeps it")
        reply = self.post("taxonomy/rename", {"tag_id": self.node("Trips/Coast"), "new_name": "Shore"})
        self.assertTrue(reply.get_json()["success"], "made again with writing clips on, the rename finishes")
        self.assert_holds(["Trips/Shore"], clip, self.clip_path)


if __name__ == "__main__":
    unittest.main()
