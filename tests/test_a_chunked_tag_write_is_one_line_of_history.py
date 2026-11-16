"""The writes of one action sent in chunks are one line of History, and its Undo puts every file back (plan
one-queue-for-tag-changes, cut 2; tagpup.services.journal.lines, tagpup.store.journal.GROUP).

A page's bulk tag write will be sent in chunks of 25 photos, each chunk its own journaled change (cut 3): History must not
list a 5,000-photo action as 200 lines, nor make the owner undo it 200 times. Each chunk carries the action's token in its
change's summary (POST /api/photos/bulk-tags `group`); History lists the changes of one token as one line, and the
dialog's Undo posts them newest first through the one per-change undo route, as this test does. The changes are made
through the route the page posts to, on JPEGs made here whose rows are as the indexer makes them, with the real ExifTool.
"""
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import web_client  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import tagging  # noqa: E402
from tagpup.store import db, journal  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
BEACH = {"XMP:Subject": ["Beach"], "IPTC:Keywords": ["Beach"]}


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class AChunkedTagWrite(unittest.TestCase):
    def setUp(self):
        from PIL import Image

        app, self.home = web_client.app_for(self, "tagpup")
        self.client = app.test_client()
        self.library = Library(self.home.library("library.db"))
        self.folder = os.path.join(self.home.root, "Regatta Day")
        os.makedirs(self.folder)
        self.photos = [os.path.join(self.folder, "p%d.jpg" % n) for n in range(5)]
        Image.new("RGB", (16, 12), (90, 110, 130)).save(self.photos[0], "JPEG")
        for photo in self.photos[1:]:
            shutil.copyfile(self.photos[0], photo)
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags(self.photos, tags=BEACH, params=["-overwrite_original"])
        conn = db.connect(self.library.path)
        try:
            for photo in self.photos:
                photo_rows.add_read(conn, photo, BEACH)
            conn.commit()
        finally:
            conn.close()

    def chunk(self, paths, tag, group, status=200):
        """A chunk as the page posts it; the id of the change it made (None when it was refused)."""
        before = self.newest()
        reply = self.client.post("/library/api/photos/bulk-tags",
                                 json={"paths": paths, "add_tags": [tag], "remove_tags": [], "group": group})
        self.assertEqual(status, reply.status_code, reply.data)
        made = self.newest()
        return None if made == before else made

    def newest(self):
        found = journal.history(self.library.path, limit=1)
        return found[0]["id"] if found else None

    def lines(self, limit=20):
        reply = self.client.get("/library/api/history?limit=%d" % limit)
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()["changes"]

    def subjects(self):
        with ExifToolSession(executable=EXIFTOOL) as et:
            return [sorted(found.get("XMP:Subject") if isinstance(found.get("XMP:Subject"), list)
                           else [found.get("XMP:Subject")]) for found in et.get_tags(self.photos, tags=["XMP:Subject"])]

    def test_the_chunks_of_one_action_are_one_line_and_two_actions_written_at_once_two(self):
        coast = self.chunk(self.photos[0:2], "Trips/Coast", "drain-1")
        pier = self.chunk(self.photos[2:3], "Trips/Pier", "drain-2")
        # A change with no token, as every change made before this cut: its own line, as it was.
        plain = tagging.change_tags(self.library, self.photos[3:4], ["Trips/Coast"], [], EXIFTOOL).details["change"]
        coast_again = self.chunk(self.photos[4:5], "Trips/Coast", "drain-1")
        alone = self.chunk(self.photos[0:1], "Trips/Pier", "drain-3")
        pier_again = self.chunk(self.photos[1:2], "Trips/Pier", "drain-2")
        listed = self.lines(limit=4)
        self.assertEqual([pier_again, alone, coast_again, plain], [line["id"] for line in listed])
        two, one, coast_line, old = listed
        self.assertEqual(("drain-2", 2, [pier_again, pier], {"group": "drain-2", "photos": 2}),
                         (two["group"], two["writes"], [m["id"] for m in two["members"]], two["summary"]))
        self.assertEqual((2, [coast_again, coast], {"done": 3}, {"group": "drain-1", "photos": 3}, True),
                         (coast_line["writes"], [m["id"] for m in coast_line["members"]], coast_line["files"],
                          coast_line["summary"], coast_line["undoable"]))
        self.assertEqual("add to all selected", coast_line["operation"])
        # A token on one change alone, and no token, are ordinary lines.
        self.assertNotIn("members", one)
        self.assertNotIn("members", old)
        self.assertEqual(({"done": 1}, 1, False), (old["files"], old["summary"]["photos"], "group" in old["summary"]))
        # A group whose older changes are past what History read at once is completed, not cut short.
        with mock.patch.object(journal_service, "READ_AT_ONCE", 2):
            far = self.lines(limit=3)
        self.assertEqual([[pier_again, pier], None, [coast_again, coast]],
                         [[m["id"] for m in line["members"]] if "members" in line else None for line in far])

    def test_a_group_half_undone_then_the_rest_puts_every_file_back(self):
        made = [self.chunk(self.photos[0:2], "Trips/Coast", "drain-1"),
                self.chunk(self.photos[2:3], "Trips/Coast", "drain-1"),
                self.chunk(self.photos[3:5], "Trips/Coast", "drain-1")]
        line = self.lines()[0]
        self.assertEqual((made[2], 3, list(reversed(made)), 5),
                         (line["id"], line["writes"], [m["id"] for m in line["members"]], line["summary"]["photos"]))
        # The newest undone alone (by hand, or an Undo stopped after its first post): the line is what is left.
        reply = self.client.post("/library/api/history/%d/undo" % made[2], json={"apply": True})
        self.assertEqual((200, 2), (reply.status_code, reply.get_json()["changed"]), reply.data)
        line = self.lines()[0]
        self.assertEqual((made[1], 2, [made[1], made[0]], 3, True),
                         (line["id"], line["writes"], [m["id"] for m in line["members"]], line["summary"]["photos"],
                          line["undoable"]))
        # Undo of the rest, as the dialog posts it: each newest first through the one per-change route.
        for member in line["members"]:
            reply = self.client.post("/library/api/history/%d/undo" % member["id"], json={"apply": True})
            self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual([["Beach"]] * 5, self.subjects())
        line = self.lines()[0]
        self.assertEqual(("undone", 3, [], 5, False), (line["status"], line["writes"], line["members"],
                                                        line["summary"]["photos"], line["undoable"]))
        self.assertIn("undone", line["why_not"])

    def test_the_headline_counts_the_photos_written_not_the_photos_planned(self):
        # The second chunk names a photo the first gave the tag already: planned, and nothing to write to it.
        first = self.chunk(self.photos[0:2], "Trips/Coast", "drain-1")
        second = self.chunk(self.photos[1:3], "Trips/Coast", "drain-1")
        line = self.lines()[0]
        self.assertEqual((second, 2, [second, first]), (line["id"], line["writes"], [m["id"] for m in line["members"]]))
        self.assertEqual(3, sum(line["files"].values()), "the chunks wrote three photos")
        self.assertEqual(3, line["summary"]["photos"], "four were planned, three were written")

    def test_a_token_that_is_not_one_or_a_batch_in_a_group_is_refused_and_nothing_written(self):
        before = self.newest()
        for group in ("People/Wren Ashby", 7, ""):
            self.assertIsNone(self.chunk(self.photos[0:1], "Trips/Coast", group, status=400), group)
        # More than one chunk's photos: a batch, which the batch strip finds in History by its own id.
        many = [os.path.join(self.folder, "m%02d.jpg" % n) for n in range(26)]
        for photo in many:
            shutil.copyfile(self.photos[0], photo)
        self.assertIsNone(self.chunk(many, "Trips/Coast", "drain-1", status=400))
        self.assertEqual(before, self.newest())
        self.assertEqual([["Beach"]] * 5, self.subjects())


if __name__ == "__main__":
    unittest.main()
