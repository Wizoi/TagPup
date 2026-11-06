"""A tag-tree rename, merge or retire of a tag a clip holds (DECISIONS 2026-11-05, M7; #2503): the tree's rewrite of
its photos patches each clip's XMP as a tag save does (file_changes.leave_out_clips lets a keyword write of a clip through
while writing clips is on), and the tree moves once every file is rewritten.

What the owner lives through: a tag is renamed in the tag tree, merged into another in TagTuner, or retired, and a clip
of the library holds it. With writing clips on, the clip is rewritten with the photos, its row follows and the tree
moves. A retire that would take the last keyword off a clip whose keywords Windows keeps in its QuickTime Subject (14
live .mp4 clips) is refused for that clip with its sentence, and the tree keeps the node, as for any photo not
rewritten. With writing clips off, the photos beside the clip are rewritten, the clip is named in the reply with the
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

from tagpup.files import images, segment_edits  # noqa: E402
from tagpup.services import tags  # noqa: E402
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

    def test_a_retire_that_would_empty_a_clip_whose_keywords_are_in_quicktime_keeps_the_node_and_says_why(self):
        tagged.with_quicktime_subject(self.clip_path, ["Trips/Coast"])
        clip, = self.index(self.clip_path)
        before = self.read_bytes(self.clip_path)
        reply = self.post("taxonomy/delete-confirm", {"tag_id": self.node("Trips/Coast"), "action": "delete"})
        body = reply.get_json()
        self.assertEqual((False, 0), (body["success"], body["photos_rewritten"]), body)
        self.assertIn("jetty.mp4: " + segment_edits.KEPT_IN_QUICKTIME[:150], body["error"], "the alert says which and why")
        self.assertTrue(self.node("Trips/Coast"), "the clip still carries it, so the tree keeps it")
        self.assertEqual(before, self.read_bytes(self.clip_path), "the clip is as it was")
        self.assertEqual(["Trips/Coast"], self.tags_of(clip))
        result = tags.merge(self.vl.library, "Trips/Coast", "", EXIFTOOL, retire=True, apply=True)
        self.assertEqual([segment_edits.KEPT_IN_QUICKTIME], [each["why"] for each in result.details["failed_photos"]],
                         "TagTuner's reply names it and why")
        self.assertTrue(self.node("Trips/Coast"))

    def test_its_only_tag_taken_off_in_a_bulk_edit_is_refused_with_the_sentence_never_called_held_already(self):
        tagged.with_quicktime_subject(self.clip_path, ["Trips/Coast"])
        clip, = self.index(self.clip_path)
        reply = self.post("photos/bulk-tags", {"paths": [self.clip_path], "remove_tags": ["Trips/Coast"]})
        self.assertIn(segment_edits.KEPT_IN_QUICKTIME, reply.get_json()["error"])
        self.assertEqual([], self.change_states(clip), "nothing journaled")
        self.assertEqual(["Trips/Coast"], self.tags_of(clip))

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
