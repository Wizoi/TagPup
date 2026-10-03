"""A caller's paths are resolved through the first place of a root once, at the ingress
(tagpup.web.roots_ingress; findings #480-#482, #484, #485).

A page that still holds the PREVIOUS place's spelling after a move -- TagPup's folder cache, a bookmark, a
second tab -- must read and write the first place's files, see one cache entry per folder, serve and open the
first place's file, and work once the previous place is gone. Real ExifTool, real files, Flask's test client.
"""
import hashlib
import json
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import roots_library as rl  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.store import file_journal  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web import roots_gate, tagpup_routes  # noqa: E402

WINDOWS = os.name == "nt"
LIBRARY = "photo_index"


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def keywords(path):
    with ExifToolSession(executable=rl.EXIFTOOL) as et:
        return et.get_tags([path], tags=["XMP:Subject"])[0].get("XMP:Subject", [])


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class AfterAMove(unittest.TestCase):
    KIND = "tagpup"

    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_ingress_")
        self.side = rl.Side(self.home, LIBRARY, real=2, bulk=0, outside=0)
        self.assertIsNone(self.side.adopt().refused)
        self.library = self.side.library
        self.old = self.side.pictures
        self.first = os.path.join(self.home.root, "Copy", "Pictures")
        shutil.copytree(self.old, self.first, copy_function=shutil.copy2)
        self.held = list(self.side.real)
        self.moved = [self.first + p[len(self.old):] for p in self.held]
        self.folder_old = os.path.dirname(self.held[0])
        self.folder_first = os.path.dirname(self.moved[0])
        self.app = web.create_app(self.KIND, startup=self.library)
        self.app.testing = True
        self.client = self.app.test_client()
        tagpup_routes.folders.of(self.library).clear()
        roots_gate.forget(self.library)

    def get(self, path, **kwargs):
        return self.client.get("/%s%s" % (LIBRARY, path), **kwargs)

    def post(self, path, body):
        return self.client.post("/%s%s" % (LIBRARY, path), data=json.dumps(body), content_type="application/json")

    def move(self):
        config.set_location("pictures", self.first)

    def scan(self, folder, force=False):
        reply = self.get("/api/folder/scan", query_string={"path": folder, "force": "true" if force else "false"})
        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
        return reply.get_json()


class TheFolderCache(AfterAMove):
    def test_the_old_and_the_first_spelling_of_a_folder_are_one_cache_entry(self):          # #480
        self.scan(self.folder_old)
        self.move()
        self.scan(self.folder_first)
        self.scan(self.folder_old.upper().replace("\\", "/"))
        cache = tagpup_routes.folders.of(self.library)
        entries = [key for key in cache._maps if key.startswith(paths.key(self.first))]
        self.assertEqual(1, len(entries), "two entries for one folder: %s" % entries)

    def test_tags_written_at_the_first_place_show_on_a_rescan_of_the_old_folder_and_survive_a_save(self):   # #480
        before = self.scan(self.folder_old)                    # cached, spelled by the place before the move
        self.assertTrue(before)
        self.move()                                            # done in the other process: nothing here knows
        reply = self.post("/api/photos/bulk-tags", {"paths": self.held, "add_tags": ["Harbour"], "remove_tags": []})
        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
        for moved in self.moved:
            self.assertIn("Harbour", keywords(moved))
        again = self.scan(self.folder_old)                     # without force
        self.assertTrue(all(record["path"].lower().startswith(self.first.lower()) for record in again), again[0]["path"])
        self.assertTrue(all("Harbour" in record["tags"] for record in again), "the cache still held the old tags")
        record = [r for r in again if r["path"].lower() == self.moved[0].lower()][0]
        saved = self.post("/api/photo/save-metadata", {"path": self.held[0], "title": "A title",
                                                       "tags": record["tags"], "date_taken": None})
        self.assertEqual(200, saved.status_code, saved.get_data(as_text=True))
        self.assertIn("Harbour", keywords(self.moved[0]), "a panel save removed the bulk-added tag")
        self.assertNotIn("Harbour", keywords(self.held[0]), "the old copy was written")


class ThePhotoServedAndOpened(AfterAMove):
    def test_a_rotate_by_the_old_path_is_what_photo_file_serves_for_the_old_path(self):     # #481
        self.move()
        old_bytes = digest(self.held[0])
        reply = self.post("/api/photo/rotate", {"path": self.held[0], "direction": "right"})
        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
        self.assertEqual(old_bytes, digest(self.held[0]), "the old copy was rotated")
        self.assertNotEqual(old_bytes, digest(self.moved[0]))
        served = self.get("/api/photo-file", query_string={"path": self.held[0]})
        self.assertEqual(200, served.status_code)
        with open(self.moved[0], "rb") as handle:
            self.assertEqual(handle.read(), served.data, "photo-file served the old copy")

    def test_open_and_show_in_explorer_use_the_first_places_file(self):                    # #481
        self.move()
        with mock.patch("tagpup.web.tagpup_routes.desktop.open_photo") as opened, \
                mock.patch("tagpup.web.tagpup_routes.desktop.show_in_explorer") as shown:
            self.assertEqual(200, self.post("/api/photo/open", {"path": self.held[0]}).status_code)
            self.assertEqual(200, self.post("/api/photo/open-explorer", {"path": self.held[0]}).status_code)
        opened.assert_called_once_with(self.moved[0])
        shown.assert_called_once_with(self.moved[0])


class WhenThePreviousPlaceIsGone(AfterAMove):
    def test_a_request_holding_old_paths_still_works(self):                                # #482
        self.move()
        shutil.rmtree(self.old)
        saved = self.post("/api/photo/save-metadata", {"path": self.held[0], "title": "", "tags": ["Harbour"],
                                                       "date_taken": None})
        self.assertEqual(200, saved.status_code, saved.get_data(as_text=True))
        self.assertIn("Harbour", keywords(self.moved[0]))
        self.assertEqual(200, self.post("/api/photo/rotate", {"path": self.held[0], "direction": "left"}).status_code)
        self.assertEqual(200, self.post("/api/photos/bulk-tags", {"paths": [self.held[1]], "add_tags": ["Quay"],
                                                                  "remove_tags": []}).status_code)
        self.assertEqual(200, self.get("/api/photo-file", query_string={"path": self.held[0]}).status_code)
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=lambda p: os.remove(p) or True):
            deleted = self.post("/api/photo/delete", {"path": self.held[0]})
        self.assertEqual(200, deleted.status_code, deleted.get_data(as_text=True))
        self.assertFalse(os.path.exists(self.moved[0]))
        self.assertEqual(1, len(self.scan(self.folder_old)), "the first place's folder, by its old name")


class AFolderToAdd(AfterAMove):
    def test_one_that_exists_only_at_the_previous_place_is_refused_naming_both_places_and_the_root(self):  # #485
        self.move()
        later = os.path.join(self.old, "Made later")
        os.makedirs(later)
        reply = self.post("/api/folder/add", {"folder_path": later})
        self.assertEqual(400, reply.status_code)
        said = reply.get_json()["error"]
        self.assertIn(later, said)
        self.assertIn("previous place of root pictures", said)
        self.assertIn(self.first, said)
        self.assertIn("does not exist at its current place", said)

    def test_one_that_exists_at_both_is_queued_at_the_first_place_by_the_real_queue(self):   # #485
        self.move()
        both = os.path.join(self.old, "Both")
        os.makedirs(both)
        os.makedirs(os.path.join(self.first, "Both"))
        queue = indexing_jobs.queue_for(self.library)
        self.addCleanup(indexing_jobs.forget, self.library)
        with mock.patch.object(type(queue), "_ensure_runner"):
            reply = self.post("/api/folder/index-start", {"folder_path": both})
        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
        self.assertEqual([os.path.join(self.first, "Both")], [job["folder"] for job in queue.pending()])


class TheTunerToo(AfterAMove):
    KIND = "tuner"

    def test_its_folder_routes_resolve_the_same_way(self):                                 # #482, #485
        self.move()
        later = os.path.join(self.old, "Made later")
        os.makedirs(later)
        reply = self.post("/api/folder/index-start", {"folder_paths": [later]})
        self.assertEqual(400, reply.status_code)
        self.assertIn("previous place of root pictures", reply.get_json()["error"])
        self.assertEqual(200, self.get("/api/photo-file", query_string={"path": self.held[0]}).status_code)


class AnUndoOfAnEarlierChange(unittest.TestCase):
    @unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
    def test_a_change_recorded_before_the_adoption_is_undone_at_the_first_place(self):       # #484
        home = own_home.for_test(self, prefix="roots_undo_")
        side = rl.Side(home, LIBRARY, real=2, bulk=0, outside=0)
        before = side.tagged_before()
        self.assertEqual(2, before.changed, before.message())
        self.assertIsNone(side.adopt().refused)
        first = os.path.join(home.root, "Copy", "Pictures")
        shutil.copytree(side.pictures, first, copy_function=shutil.copy2)
        config.set_location("pictures", first)
        change = before.details["change"]
        files = file_journal.files_of(side.db_path, change)
        self.assertTrue(all(row.path.lower().startswith(first.lower()) for row in files),
                        "an undo would write the old copy: " + files[0].path)
        moved = first + side.real[0][len(side.pictures):]
        self.assertIn("Harbour", keywords(moved))
        undone = journal_service.undo(side.library, change, apply=True, exiftool_path=rl.EXIFTOOL)
        self.assertEqual((True, 2), (undone.ok, undone.changed), undone.message())
        self.assertNotIn("Harbour", keywords(moved))
        self.assertIn("Harbour", keywords(side.real[0]), "the old copy was written")


if __name__ == "__main__":
    unittest.main()
