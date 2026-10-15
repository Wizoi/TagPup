"""A caller's paths are resolved through the first place of a root once, at the ingress
(tagpup.web.roots_ingress; findings #480-#482, #484, #485).

A page that still holds the PREVIOUS place's spelling after a move -- TagPup's folder cache, a bookmark, a
second tab -- must read and write the first place's files, see one cache entry per folder, serve and open the
first place's file, and work once the previous place is gone. Real ExifTool, real files, Flask's test client.

Lifted: test_roots_share_spelling_and_cli_index (the stage 3 re-review's #478, #483, #486, #488): the rows a share's
address spells, the CLI's index of a folder at the previous place, a write stopped by a change of roots. They are the
same flow from other doors: a path spelled as the previous place, the share or the root before the change.
"""
import hashlib
import json
import os
import shutil
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import photo_rows  # noqa: E402
import roots_library as rl  # noqa: E402
from click.testing import CliRunner  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.services import file_changes, tagging  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.store import checks, db, file_journal  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web import roots_gate, roots_ingress, tagpup_routes  # noqa: E402

WINDOWS = os.name == "nt"
LIBRARY = "photo_index"
SHARE = "\\\\idziserver\\Pictures\\Pictures"


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
        roots_ingress.forget(self.library)

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
    def test_the_old_and_the_first_spelling_of_a_folder_are_one_cache_entry(self):          # #480, #493
        self.move()
        self.scan(self.folder_old)
        self.scan(self.folder_first)
        self.scan(self.folder_old.upper().replace("\\", "/"))
        cache = tagpup_routes.folders.of(self.library)
        either = (paths.key(self.first), paths.key(self.old))
        entries = [key for key in cache._maps if key.startswith(either)]
        self.assertEqual(1, len(entries), "two entries for one folder, under both spellings: %s" % entries)

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


class TheCostOfTheIngress(AfterAMove):
    def timed(self, url, runs=300):
        """Microseconds per call of the guard alone, in a request context naming the library."""
        from flask import g
        with self.app.test_request_context("/api/server" if url is None else url):
            g.library = self.library
            roots_ingress.guard()
            started = time.perf_counter()
            for _ in range(runs):
                roots_ingress.guard()
            return (time.perf_counter() - started) / runs * 1e6

    def test_a_request_without_a_path_costs_nothing_and_one_with_a_path_under_a_millisecond(self):   # #489
        nothing = self.timed(None)
        photo = self.timed("/api/photo-file?path=" + self.held[0].replace("\\", "%5C"))
        sys.stderr.write("[ingress] guard per request: no path %.1f us, a photo-file path %.1f us\n" % (nothing, photo))
        self.assertLess(nothing, 50, "a request with no path opened something")
        self.assertLess(photo, 1000, "a request with a path costs more than a millisecond")

    def test_a_request_without_a_path_opens_no_connection(self):                              # #489
        from tagpup.services import roots as roots_service
        with mock.patch.object(roots_service, "canonicaliser", side_effect=AssertionError("looked")):
            self.assertEqual(200, self.get("/api/server").status_code)
            self.assertEqual(200, self.get("/api/databases").status_code)

    def test_one_look_serves_a_second_of_requests_and_a_changed_map_is_seen_at_once(self):   # #489
        from tagpup.services import roots as roots_service
        looks = []
        real = roots_service.canonicaliser
        with mock.patch.object(roots_service, "canonicaliser", side_effect=lambda lib: looks.append(1) or real(lib)):
            for _ in range(5):
                self.get("/api/photo-file", query_string={"path": self.held[0]})
            self.assertEqual(1, len(looks))
            self.move()
            self.get("/api/photo-file", query_string={"path": self.held[0]})
        self.assertEqual(2, len(looks), "a changed map was not looked at again")


class TheFolderRoutesNameTheTwoPlaces(AfterAMove):
    def only_at_the_old_place(self):
        self.move()
        later = os.path.join(self.old, "Only here")
        os.makedirs(later)
        return later

    def assert_two_places(self, reply, later):
        self.assertEqual(400, reply.status_code, reply.get_data(as_text=True))
        said = reply.get_json()["error"]
        self.assertIn("previous place of root pictures", said)
        self.assertIn("does not exist at its current place", said)
        self.assertIn(later, said)

    def test_scan_membership_and_subfolders_say_it(self):                                    # #490
        later = self.only_at_the_old_place()
        self.assert_two_places(self.get("/api/folder/scan", query_string={"path": later}), later)
        self.assert_two_places(self.get("/api/folder/membership", query_string={"path": later}), later)
        self.assert_two_places(self.get("/api/folder/subfolders", query_string={"path": later}), later)

    def test_the_tuner_says_it_too(self):                                                    # #490
        self.app = web.create_app("tuner", startup=self.library)
        self.app.testing = True
        self.client = self.app.test_client()
        later = self.only_at_the_old_place()
        self.assert_two_places(self.get("/api/folder/subfolders", query_string={"path": later}), later)
        self.assert_two_places(self.post("/api/folder/add", {"folder_path": later}), later)

    def test_the_autocomplete_is_left_alone(self):                                           # #491
        from flask import g, request
        self.move()
        typed = self.old + "\\"
        with self.app.test_request_context("/api/autocomplete-folder", query_string={"path": typed}):
            g.library = self.library
            roots_ingress.guard()
            self.assertEqual(typed, request.args["path"], "half-typed text was rewritten")
        with self.app.test_request_context("/api/folder/scan", query_string={"path": self.folder_old}):
            g.library = self.library
            roots_ingress.guard()
            self.assertEqual(self.folder_first, request.args["path"])


class ThePageIsToldItsPathsAreOld(AfterAMove):
    def test_a_response_to_old_paths_carries_the_roots_name(self):                           # #492
        self.move()
        old = self.post("/api/photos/bulk-tags", {"paths": [self.held[0]], "add_tags": ["Harbour"], "remove_tags": []})
        self.assertEqual("pictures", old.headers.get("X-TagPup-Roots-Moved"))
        new = self.post("/api/photos/bulk-tags", {"paths": [self.moved[0]], "add_tags": ["Quay"], "remove_tags": []})
        self.assertIsNone(new.headers.get("X-TagPup-Roots-Moved"))
        self.assertIsNone(self.get("/api/server").headers.get("X-TagPup-Roots-Moved"))
        self.assertEqual("pictures", self.get("/api/photo-file", query_string={"path": self.held[1]}).headers.get(
            "X-TagPup-Roots-Moved"))


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
        # A row the adoption did not convert: kept native, under a place the library no longer lives at.
        db.write_with_connection(side.db_path, lambda conn: conn.execute(
            "UPDATE change_files SET path = ? WHERE change_id = ? AND path = ?",
            (side.real[0], change, "@pictures/" + os.path.relpath(side.real[0], side.pictures).replace(os.sep, "/"))))
        files = file_journal.files_of(side.db_path, change)
        self.assertTrue(all(row.path.lower().startswith(first.lower()) for row in files),
                        "an undo would write the old copy: " + files[0].path)
        moved = first + side.real[0][len(side.pictures):]
        self.assertIn("Harbour", keywords(moved))
        undone = journal_service.undo(side.library, change, apply=True, exiftool_path=rl.EXIFTOOL)
        self.assertEqual((True, 2), (undone.ok, undone.changed), undone.message())
        self.assertNotIn("Harbour", keywords(moved))
        self.assertIn("Harbour", keywords(side.real[0]), "the old copy was written")


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class ShareSpelledRows(unittest.TestCase):
    """photo_index's shape: rooted photos at the local copy, and rows spelled by the share's address."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_share_")
        self.side = rl.Side(self.home, "photo_index", real=2, bulk=0, outside=0)
        conn = db.connect(self.side.db_path)
        try:
            for n in range(3):
                path = SHARE + "\\2026\\IMG_%d.jpg" % n
                photo_rows.add_read(conn, path, rl.read(path))
            conn.commit()
        finally:
            conn.close()

    def test_the_check_reports_native_rows_the_roots_resolve_to_a_root(self):                      # #478
        self.assertIsNone(self.side.adopt(address="").refused)
        self.assertEqual([], roots_service.check(self.side.library))
        # The address is given afterwards (an edit): Roots.locate now resolves them.
        db.write_with_connection(self.side.db_path, lambda conn: conn.execute(
            "UPDATE roots SET address = ? WHERE name = ?", (SHARE, rl.NAME)))
        found = roots_service.check(self.side.library)
        self.assertEqual(1, len(found), found)
        self.assertIn("3 row(s) kept their native path", found[0])
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        self.addCleanup(conn.close)
        self.assertEqual(3, checks.native_rows_under_a_root(conn).count)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheCliIndex(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_cli_")
        self.side = rl.Side(self.home, "photo_index", real=1, bulk=0, outside=0)
        self.assertIsNone(self.side.adopt().refused)
        self.old = self.side.pictures
        self.first = os.path.join(self.home.root, "Copy", "Pictures")
        shutil.copytree(self.old, self.first, copy_function=shutil.copy2)
        config.set_location("pictures", self.first)

    def run_index(self, directories):
        import click

        import tagpup_cli
        seen = []

        @click.command()
        @click.argument("directories", nargs=-1)
        @click.option("--reset", is_flag=True)
        @click.pass_context
        @tagpup_cli._holds_the_roots
        def fake(ctx, directories, reset):
            seen.append(list(directories))

        return CliRunner().invoke(fake, directories, obj={"db": self.side.db_path, "test": False}), seen

    def test_a_folder_that_exists_only_at_the_previous_place_is_refused_naming_both(self):        # #483
        later = os.path.join(self.old, "Only here")
        os.makedirs(later)
        result, seen = self.run_index([later])
        self.assertEqual(1, result.exit_code)
        self.assertEqual([], seen, "the command went on")
        self.assertIn("previous place of root pictures", result.output)
        self.assertIn(later, result.output.replace("\n", ""))
        self.assertIn(self.first, result.output.replace("\n", ""))

    def test_one_that_exists_at_both_is_indexed_at_the_first_place(self):                          # #483
        both = os.path.join(self.old, "Both")
        os.makedirs(both)
        os.makedirs(os.path.join(self.first, "Both"))
        result, seen = self.run_index([both, self.side.outside_folder])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual([[os.path.join(self.first, "Both"), self.side.outside_folder]], seen)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class AWriteStoppedByARootChange(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_stop_")
        self.side = rl.Side(self.home, "photo_index", real=2, bulk=0, outside=0)
        self.assertIsNone(self.side.adopt().refused)
        self.library = self.side.library

    def stopped(self, call):
        real = file_changes._carry
        calls = []

        def stop_after_the_first(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise roots_service.RootsChanged("changed")
            return real(*args, **kwargs)

        with mock.patch.object(file_changes, "_carry", stop_after_the_first):
            return call()

    def test_the_change_is_finished_at_once_so_what_was_written_can_be_undone(self):               # #486
        result = self.stopped(lambda: tagging.change_tags(self.library, self.side.real[:3], ["Harbour"], [],
                                                          rl.EXIFTOOL))
        change = result.details["change"]
        self.assertEqual("applied", file_journal.change(self.side.db_path, change).status)
        states = [row.state for row in file_journal.files_of(self.side.db_path, change)]
        self.assertEqual(["done"], states, "files never written were left planned")
        self.assertEqual([], file_journal.unfinished(self.side.db_path))

    def test_the_route_answers_refused_with_written_and_the_change(self):                          # #486
        app = web.create_app("tagpup", startup=self.library)
        app.testing = True
        reply = self.stopped(lambda: app.test_client().post(
            "/photo_index/api/photos/bulk-tags", data=json.dumps({"paths": self.side.real[:3], "add_tags": ["Harbour"],
                                                                  "remove_tags": []}), content_type="application/json"))
        body = reply.get_json()
        self.assertEqual(400, reply.status_code)
        self.assertIn(roots_service.STOPPED, body["error"])
        self.assertEqual(1, len(body["written"]))
        self.assertIsNotNone(body["change"])
        self.assertEqual([self.side.real[0].lower()], [p.lower() for p in body["written"]])


if __name__ == "__main__":
    unittest.main()
