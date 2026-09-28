"""Two changes of the same photo files at once: the second waits for the first.

Add to all selected clicked three times quickly sent three writes at once. Each planned
from the files as they were before the others wrote, and ExifTool's second write to a
file it was writing failed ("Error creating file: <file>_exiftool_tmp"): one request
wrote its tags, another "wrote 0 file(s)", and a third found a photo "changed since it
was read". Where both writes got through, the second wrote a keyword set planned before
the first's, and the first's tag was gone from the file with nothing said.

Real ExifTool, real JPEGs, the routes through Flask's test client, one client a thread.
"""
import os
import threading
import unittest
from unittest import mock

from tests.handler_harness import Library
from tests.test_bulk_writes_start_from_the_file import EXISTING, make_photo, tags_in
from tests.test_taxonomy_lifecycle import EXIFTOOL, requires_exiftool

from tagpup.services import tagging


def at_once(*calls):
    """Run each call on a thread of its own, started together; their results in order."""
    results = [None] * len(calls)
    start = threading.Barrier(len(calls))

    def run(n, call):
        start.wait()
        try:
            results[n] = call()
        except Exception as e:  # the test reads it
            results[n] = e

    threads = [threading.Thread(target=run, args=(n, call)) for n, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
    return results


@requires_exiftool
class TwoBulkWritesAtOnce(unittest.TestCase):
    def setUp(self):
        self.lib = Library(self)
        self.photos = [make_photo(os.path.join(self.lib.photos, "p%d.jpg" % n)) for n in range(3)]

    def post(self, body):
        client = self.lib.app.test_client()

        def call():
            reply = client.post("/api/photos/bulk-tags", json=body)
            return reply.status_code, reply.get_json()
        return call

    def test_both_succeed_and_every_file_holds_both(self):
        # Overlapping: the first photo only in one, the last two in both.
        for round_ in range(2):
            quay, harbour = "Places/Quay %d" % round_, "Places/Harbour %d" % round_
            first, second = at_once(
                self.post({"paths": self.photos, "add_tags": [quay], "remove_tags": []}),
                self.post({"paths": self.photos[1:], "add_tags": [harbour], "remove_tags": []}))
            self.assertEqual(200, first[0], first)
            self.assertEqual(200, second[0], second)
            held = tags_in(self.photos[0])
            self.assertIn(quay, held)
            self.assertNotIn(harbour, held)
            for photo in self.photos[1:]:
                held = tags_in(photo)
                self.assertIn(quay, held)
                self.assertIn(harbour, held)


@requires_exiftool
class ASaveAndABulkWriteAtOnce(unittest.TestCase):
    """A save reads the photo, then writes: a bulk write between the two was overwritten,
    and the save's journal said the photo held what it held before the bulk write."""

    def setUp(self):
        self.lib = Library(self)
        self.photo = make_photo(os.path.join(self.lib.photos, "a.jpg"))

    def test_the_bulk_write_waits_for_the_save(self):
        save_read, bulk_done = threading.Event(), threading.Event()
        caption_problem = tagging._caption_problem

        def after_the_read(*args):
            # The save has read the photo. Give a bulk write the time to get in.
            save_read.set()
            bulk_done.wait(1.5)
            return caption_problem(*args)

        def save():
            with mock.patch.object(tagging, "_caption_problem", side_effect=after_the_read):
                return tagging.save_photo(self.lib.library, self.photo, "", EXISTING + ["Places/Harbour"], None,
                                          EXIFTOOL, "")

        def bulk():
            save_read.wait(30)
            try:
                return tagging.change_tags(self.lib.library, [self.photo], ["Sunset"], [], EXIFTOOL)
            finally:
                bulk_done.set()

        saved, added = at_once(save, bulk)
        self.assertNotIsInstance(saved, Exception)
        self.assertNotIsInstance(added, Exception)
        self.assertTrue(saved.ok, saved.errors)
        self.assertTrue(added.ok, added.errors)
        self.assertEqual(sorted(EXISTING + ["Places/Harbour", "Sunset"]), sorted(tags_in(self.photo)))


if __name__ == "__main__":
    unittest.main()
