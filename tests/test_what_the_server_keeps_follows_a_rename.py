"""What the server keeps of a photo follows it when one of its writes renames it (scenario I8; #2852).

The owner Smart Renames the open photo, or saves a title that renames it after the caption, or converts an old clip into
an MP4 of a new name. The page goes on from what the server keeps: its cached scan of every folder holding the photo (the
folder opened, and an ancestor whose recursive walk took it in) and what Suggest only looked at in a folder the library
does not hold. Each write has to leave all three following the photo: the records under its new name (path, filename)
and carrying the file's stamp now -- the stamp the next save names, which is refused when it is not the file's (I8) --
and Suggest's looks kept under the new name (#547, #2415). A write that kept the name still takes the stamp: it wrote the
file. A save after a title also tells the records the tags and title it wrote; the other two leave them as they were.

The conversion's follow-up runs on the conversion's thread when it ends, the request long answered and no request
context there: it acts on the library the conversion was started in.

The services stand in (each renames the file on disk as the real one does and answers its Result); what is under test is
the routes' follow-up. Written against the trunk before the follow-up was folded into one function, and passing there.
"""
import os
import unittest
from unittest import mock

import flask

from tests.handler_harness import Library

from tagpup.core import paths
from tagpup.core.result import Result
from tagpup.services import photos as photo_actions
from tagpup.web import tagpup_routes

#: The stamp the page's records start with: not the file's.
OLD_STAMP = (1000000000.0, 7)


class Follows:
    """A photo of a subfolder, held by the subfolder's scan and its parent's, and by Suggest's looks at the subfolder.
    A subclass says how its write is asked (`write`), what it renames the photo to, and whether it tells the records
    what it wrote."""

    #: The name the write gives the photo, and the name the photo has before.
    BEFORE = "Regatta - 07.jpg"
    AFTER = "Harbour Day - 07.jpg"
    #: The write tells the records the tags and title it wrote.
    TELLS_TAGS = False

    def setUp(self):
        self.lib = Library(self)
        self.folder = os.path.join(self.lib.photos, "Day 1")
        os.makedirs(self.folder)
        self.old = paths.stored(os.path.join(self.folder, self.BEFORE))
        with open(self.old, "wb") as handle:
            handle.write(b"a photo's bytes")
        record = photo_actions.page_record(self.old, {"tags": ["Trips/Coast"], "raw_metadata": {}}, *OLD_STAMP)
        record["title"] = "Start"
        self.lib.folders().put(self.folder, {paths.key(self.old): record})
        self.lib.folders().put(self.lib.photos, {paths.key(self.old): dict(record)})
        self.lib.suggestion_runs().looks[paths.key(self.folder)] = {
            self.old: {"raw_suggestions": {"path": self.old, "tags": ["Trips/Coast"]}}}

    def renamed_on_disk(self, new):
        """What the stood-in service does to the file: renames it, and writes it (its stamp moves)."""
        os.replace(self.old, new)
        with open(new, "ab") as handle:
            handle.write(b" written")
        return new

    def records(self):
        """{folder key: {photo key: (path, filename, mtime, size)}} of every cached scan."""
        cache = self.lib.folders()
        return {paths.key(folder): {key: (r["path"], r["filename"], r["mtime"], r["size"])
                                    for key, r in cache.peek(folder).items()}
                for folder in (self.folder, self.lib.photos)}

    def assert_followed(self, new, reply):
        stamp = os.stat(new)
        held = (new, os.path.basename(new), stamp.st_mtime, stamp.st_size)
        self.assertEqual({paths.key(self.folder): {paths.key(new): held}, paths.key(self.lib.photos): {paths.key(new): held}},
                         self.records(), "every scan holding the photo holds it under its name now, with its stamp")
        self.assertEqual((stamp.st_mtime, stamp.st_size), (reply["mtime"], reply["size"]), "the reply's stamp")
        self.assertTrue(paths.same(new, reply["new_path"]))

    def test_a_rename_moves_the_records_and_the_looks_and_stamps_the_records(self):
        new = paths.stored(os.path.join(self.folder, self.AFTER))
        reply = self.write(new)
        self.assertTrue(reply["success"], reply)
        self.assert_followed(new, reply)
        looks = self.lib.suggestion_runs().looks[paths.key(self.folder)]
        self.assertEqual([new], list(looks), "Suggest's looks kept under the new name")
        self.assertEqual(new, looks[new]["raw_suggestions"]["path"])
        self.assert_records_told()

    def test_a_write_that_kept_the_name_stamps_the_records_and_moves_nothing(self):
        reply = self.write(self.old)
        self.assertTrue(reply["success"], reply)
        self.assert_followed(self.old, reply)
        self.assertEqual([self.old], list(self.lib.suggestion_runs().looks[paths.key(self.folder)]))
        self.assert_records_told()

    def assert_records_told(self):
        cache = self.lib.folders()
        told = [(r["tags"], r["title"]) for folder in (self.folder, self.lib.photos) for r in cache.peek(folder).values()]
        self.assertEqual([(["Trips/Coast", "Boats"], "Harbour Day") if self.TELLS_TAGS else (["Trips/Coast"], "Start")] * 2,
                         told, "the tags and title the write wrote" if self.TELLS_TAGS else "left as they were")


class ASmartRenameOfTheOpenPhoto(Follows, unittest.TestCase):
    """POST /api/photo/smart-rename: the box beside the photo's name."""

    def write(self, new):
        def renaming(_library, photo_path, _grouping, _format, _exiftool):
            result = Result(attempted=1)
            moved = not paths.same(photo_path, new)
            if moved:
                self.renamed_on_disk(new)
            else:
                with open(photo_path, "ab") as handle:
                    handle.write(b" its name kept: preserve_names wrote it")
            result.changed = 1 if moved else 0
            result.details.update(new_path=new, renamed=[new] if moved else [],
                                  updated_paths={photo_path: new} if moved else {})
            return result
        with mock.patch.object(tagpup_routes.photo_actions, "smart_rename_one", side_effect=renaming):
            status, reply = self.lib.post("/api/photo/smart-rename", {"path": self.old, "grouping": "Harbour Day"})
        self.assertEqual(200, status, reply)
        return reply


class ASaveAfterATitle(Follows, unittest.TestCase):
    """POST /api/photo/save-metadata: a title saved, the photo renamed after its caption."""

    TELLS_TAGS = True

    def write(self, new):
        def saving(_library, photo_path, title, tags, _date, _exiftool, _format, _stamp, _base):
            moved = not paths.same(photo_path, new)
            if moved:
                self.renamed_on_disk(new)
            else:
                with open(photo_path, "ab") as handle:
                    handle.write(b" its title written")
            result = Result(attempted=1, changed=1)
            result.details.update(new_path=new, renamed=moved, tags=list(tags), flat=list(tags), hierarchical=list(tags),
                                  dates={}, taken=None, index_warning=None, base={"tags": list(tags), "title": title})
            return result
        with mock.patch.object(tagpup_routes.tagging_actions, "save_photo", side_effect=saving):
            status, reply = self.lib.post("/api/photo/save-metadata", {
                "path": self.old, "title": "Harbour Day", "tags": ["Trips/Coast", "Boats"]})
        self.assertEqual(200, status, reply)
        return reply


class AClipConverted(Follows, unittest.TestCase):
    """POST /api/clip/convert: the conversion ends on its own thread, after the request was answered; what it made of the
    end is what /api/clip/converted hands the panel."""

    BEFORE = "quay.wmv"
    AFTER = "quay.mp4"

    def write(self, new):
        ended = {}

        def starting(library, _exiftool, _ffmpeg, _photo_id, _mode, _stamp, done=None):
            ended["done"] = done
            result = Result(attempted=1)
            result.details.update(ticket=1, path=self.old)
            return result
        with mock.patch.object(tagpup_routes.clip_conversion, "start", side_effect=starting):
            status, reply = self.lib.post("/api/clip/convert", {"id": 1, "mode": "convert",
                                                                 "stamp": {"mtime": OLD_STAMP[0], "size": OLD_STAMP[1]}})
        self.assertEqual(200, status, reply)
        # The request is long answered: the conversion's thread has no request, and no page's library to read.
        self.assertFalse(flask.has_request_context())
        moved = not paths.same(self.old, new)
        if moved:
            self.renamed_on_disk(new)
        else:
            with open(self.old, "ab") as handle:
                handle.write(b" made smaller")
        how = Result(attempted=1, changed=1)
        how.details.update(path=new, size_before=len(b"a photo's bytes"), size_after=os.path.getsize(new))
        said = ended["done"](self.old, how)
        self.assertEqual(moved, said["renamed"])
        return said


if __name__ == "__main__":
    unittest.main()
