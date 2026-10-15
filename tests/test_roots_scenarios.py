"""What the owner does with a library that holds a root, against one that does not
(docs/ARCHITECTURE.md, "Roots and machines").

Two libraries are built alike in folders of their own (tests/roots_library.py): thousands of
rows' worth of shape -- photos under a root-shaped folder, a few beside it, faces,
suggestions, added folders, a journal of changes made before anything was
adopted -- and one is adopted by the root. Then the same flows run on both, with real
ExifTool on real JPEGs, and what each answers must be the same once its own folder is taken
out of it: the folder listing, a tag save, bulk tags, a rename and its undo, a delete, a sync that
finds a file moved, new and gone, the indexer recording a photo, Suggest keeping a suggestion,
a folder added, a search, and the doctor. Then the machine's map is
changed to another place and nothing in the rows changes, and the same flows work there.

Then the owner's Roots dialog and the machine that does not place a root (TheOwnerMovesTheLocationInTheDialog,
TheMachineThatDoesNotPlaceTheRoot, over a library and a second one sharing its root), and a folder the library does not hold
(AFolderTheLibraryDoesNotHold): written to the file only, left alone by Suggest, sync and the watcher until it is added.
"""
import asyncio
import contextlib
import json
import os
import shutil
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import photo_rows  # noqa: E402
import roots_library as rl  # noqa: E402
import web_client  # noqa: E402
from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Conflict, Result  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.mcp import server as mcp_server  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.services import suggestions as saved_suggestions  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.services import sync, tagging  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import added_folders, db, folders, journal, suggestions  # noqa: E402
from tagpup.store import checks, roots_check  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402
from tagpup.store import taxonomy  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web import roots_gate, tagpup_routes  # noqa: E402

WINDOWS = os.name == "nt"
EXIFTOOL = rl.EXIFTOOL
FORMAT = "{grouping} - {index} - {caption}"

LIBRARY = "photo_index"

requires_exiftool = unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")


class Twins(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_twins_")
        self.plain = rl.Side(self.home, "plain", real=2, bulk=15, outside=1)
        self.rooted = rl.Side(self.home, "rooted", real=2, bulk=15, outside=1)
        if EXIFTOOL:
            for side in (self.plain, self.rooted):
                side.tagged_before()
        adopted = self.rooted.adopt()
        self.assertTrue(adopted.ok and not adopted.refused, adopted.message())
        self.assertEqual(1, len(self.rooted.library_roots()))

    # ---- Running a flow on both -----------------------------------------------------------------

    def both(self, flow):
        """What `flow(side)` answers on each side, with the side's own folder taken out."""
        found = [side.norm(flow(side)) for side in (self.plain, self.rooted)]
        self.assertEqual(found[0], found[1])
        return found[0]

    def seen(self, side):
        """The library as its owner sees it: every photo and its tags, the
        folders added and ignored, and the journal's names and states."""
        conn = db.connect(db.readonly_uri(side.db_path), uri=True)
        try:
            listing = sorted((path, tags) for path, _m, _s, tags, _p, _c, _r in store_photos.rows_under(
                conn, side.base))
            held = sorted(folders.of(conn).listed())
            added = added_folders.every(conn)
            ignored = folders.ignored(conn)
        finally:
            conn.close()
        changes = [(e["operation"], e["status"], e["files"]) for e in journal.history(side.db_path, limit=50)
                   if not e["operation"].startswith(("migration", "roots adopt"))]
        return {"photos": listing, "folders": held, "added": added, "ignored": ignored,
                "journal": changes}

    def same_seen(self):
        self.assertEqual(self.plain.norm(self.seen(self.plain)), self.rooted.norm(self.seen(self.rooted)))

    def rooted_rows_are_rows(self):
        """Whatever a flow did, the library holds no native row under the root."""
        native = [p for p in self.rooted.raw_paths() if not p.startswith("@")
                  and p.lower().startswith(self.rooted.pictures.lower() + os.sep)]
        self.assertEqual([], native)
        for table, column in (("added_folders", "path"), ("change_files", "path"),
                              ("change_files", "new_path")):
            held = [p for p in self.rooted.raw_paths(table, column) if p]
            self.assertEqual([], [p for p in held if p.lower().startswith(self.rooted.pictures.lower() + os.sep)],
                             "%s.%s" % (table, column))


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
@requires_exiftool
class TheOwnersFlows(Twins):
    def test_the_state_the_adoption_leaves_is_the_state_it_found(self):
        self.same_seen()
        self.assertEqual(1, len([change for change in self.seen(self.rooted)["journal"] if change[2]]),
                         "the change of files made before the adoption")

    def test_opening_a_folder(self):
        def flow(side):
            found = photo_actions.scan_folder(side.library, side.pictures, EXIFTOOL)
            return sorted((key, record["path"], record["tags"]) for key, record in found.items())
        listed = self.both(flow)
        self.assertEqual(6, len(listed), "the six real photos")

    def test_the_page_lists_the_folders_the_library_holds(self):
        def flow(side):
            return [photo_actions.indexed_folders(side.library), photo_actions.indexed_by_folder(side.library)]
        self.both(flow)

    def test_a_tag_save_with_its_rename(self):
        def flow(side):
            result = tagging.save_photo(side.library, side.real[0], "Start line", ["Activity/Sailing", "Harbour"],
                                        None, EXIFTOOL, FORMAT)
            return [result.attempted, result.changed, result.errors, result.refused, result.details.get("new_path"),
                    result.details.get("renamed"), self.seen(side)]
        found = self.both(flow)
        self.assertEqual(1, found[1], found)
        self.rooted_rows_are_rows()

    def test_bulk_tags_and_their_undo(self):
        def flow(side):
            result = tagging.change_tags(side.library, side.real, ["Bulk"], ["Activity/Sailing"], EXIFTOOL)
            changed = [result.attempted, result.changed, result.errors, result.refused, self.seen(side)]
            undone = journal_service.undo(side.library, result.details["change"], apply=True, exiftool_path=EXIFTOOL)
            return [changed, undone.changed, undone.errors, undone.refused, self.seen(side)]
        found = self.both(flow)
        self.assertEqual(6, found[0][1])
        self.assertEqual(6, found[1])
        self.rooted_rows_are_rows()

    def test_a_change_made_before_the_adoption_is_undone_after_it(self):
        """The journal holds the native paths of a change made before the adoption; its undo
        writes the files and the rows as the rooted library holds them."""
        before = {side.label: [e for e in journal.history(side.db_path) if e["files"]] for side in (self.plain, self.rooted)}
        self.assertEqual(1, len(before["rooted"]))

        def flow(side):
            change = before[side.label][0]["id"]
            refusals = journal_service.refusals(side.library, [{"id": change, "operation": before[side.label][0]["operation"]}])
            undone = journal_service.undo(side.library, change, apply=True, exiftool_path=EXIFTOOL)
            return [refusals, undone.changed, undone.errors, undone.refused, self.seen(side)]
        found = self.both(flow)
        self.assertEqual(2, found[1])
        self.assertEqual([None], list(found[0].values()))
        self.rooted_rows_are_rows()

    def test_smart_rename_and_its_undo(self):
        def flow(side):
            with mock.patch("tagpup.services.photos.preserve_names", return_value=Result()):
                renamed = photo_actions.smart_rename(side.library, side.real[:4], "Regatta", FORMAT, EXIFTOOL)
            names = [renamed.attempted, renamed.changed, renamed.errors, renamed.refused,
                     sorted(renamed.details.get("renamed", {}).items()), renamed.details.get("index_rows_moved"),
                     self.seen(side)]
            undone = journal_service.undo(side.library, renamed.details["change"], apply=True, exiftool_path=EXIFTOOL)
            return [names, undone.changed, undone.errors, undone.refused, self.seen(side)]
        found = self.both(flow)
        self.assertEqual(4, found[0][1])
        self.rooted_rows_are_rows()

    def test_delete(self):
        def flow(side):
            def recycle(path):
                os.remove(path)
                return True
            with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=recycle):
                result = photo_actions.delete(side.library, side.real[0])
            return [result.attempted, result.changed, result.errors, result.details.get("removed"), self.seen(side)]
        found = self.both(flow)
        self.assertEqual({"faces": 1, "photos": 1}, found[3])

    def test_sync_finds_a_file_moved_a_file_new_and_a_file_gone(self):
        def flow(side):
            moved_from = side.real[2]
            moved_to = os.path.join(side.pictures, "2024 Regatta", os.path.basename(moved_from))
            os.rename(moved_from, moved_to)
            os.remove(side.real[3])
            rl.make_jpeg(os.path.join(side.pictures, "2024 Harbour", "Brand new.jpg"), (1, 2, 3))
            asked = []

            def queue(folders_to_index):
                asked.append(sorted(folders_to_index))
                return Result(attempted=len(folders_to_index), changed=len(folders_to_index))

            result = sync.sync(side.library, apply=True, exiftool_path=EXIFTOOL, queue=queue, roots=[side.pictures])
            counts = {k: v for k, v in result.details["counts"].items() if isinstance(v, int)}
            return [counts, result.changed, result.ok, asked, result.details["changed"], self.seen(side)]
        found = self.both(flow)
        self.assertEqual(1, found[0]["moved"])
        self.assertEqual(1, found[0]["new"])
        self.assertEqual(46, found[0]["missing"], "the 45 rows of photos not on disk, and the file taken away")
        self.rooted_rows_are_rows()
        self.assertIn("@pictures/2024 Regatta/IMG_2001.jpg", self.rooted.raw_paths())
        self.assertNotIn("@pictures/2024 Harbour/IMG_2001.jpg", self.rooted.raw_paths())

    def test_a_sync_that_finds_nothing_wrong_reads_nothing(self):
        for side in (self.plain, self.rooted):
            result = sync.sync(side.library, apply=False, exiftool_path=EXIFTOOL, roots=[side.pictures])
            self.assertEqual(0, result.details["counts"]["moved"], side.label)
            self.assertEqual(0, result.details["counts"]["changed"], side.label)
            self.assertEqual(0, result.details["counts"]["new"], side.label)

    def test_the_indexer_records_a_photo_and_records_it_again(self):
        def flow(side):
            new = os.path.join(side.pictures, "2024 Harbour", "Indexed now.jpg")
            rl.make_jpeg(new, (5, 5, 5))
            conn = db.connect(side.db_path)
            try:
                photo_rows.add_read(conn, new, rl.read(new))
                conn.commit()
                photo_rows.add_read(conn, new, dict(rl.read(new), **{"XMP:Subject": ["Changed since"]}))
                conn.commit()
            finally:
                conn.close()
            conn = db.connect(db.readonly_uri(side.db_path), uri=True)
            try:
                return [store_photos.row_as_recorded(conn, new), store_photos.count_under(conn, side.pictures),
                        store_photos.details(conn, new)]
            finally:
                conn.close()
        self.both(flow)
        self.assertIn("@pictures/2024 Harbour/Indexed now.jpg", self.rooted.raw_paths())

    def test_suggest_keeps_a_suggestion_and_reads_it_back(self):
        def flow(side):
            found = {"tags": ["Weather/Rain"], "people": [], "title": "Rain", "raw_before_consensus": False,
                     "raw_suggestions": {"path": side.real[1], "nearest_neighbors": [
                         {"path": side.real[0], "similarity": 0.9}, {"path": side.outside[0], "similarity": 0.2}]}}
            db.write_with_connection(side.db_path, lambda conn: suggestions.put(conn, side.real[1], found))
            conn = db.connect(db.readonly_uri(side.db_path), uri=True)
            try:
                return suggestions.in_folder(conn, side.pictures)
            finally:
                conn.close()
        self.both(flow)

    def test_a_folder_is_added_and_asked_about(self):
        def flow(side):
            folder = os.path.join(side.pictures, "Trips", "New folder")
            db.write_with_connection(side.db_path, lambda conn: added_folders.record(conn, folder, subfolders=False))
            conn = db.connect(db.readonly_uri(side.db_path), uri=True)
            try:
                return [added_folders.covers(conn, folder), added_folders.covers(conn, os.path.join(folder, "Deeper")),
                        folders.holds(conn, folder), added_folders.every(conn)]
            finally:
                conn.close()
        found = self.both(flow)
        self.assertEqual([True, False, True], found[:3])

    def test_a_search_answers_with_the_same_records(self):
        def flow(side):
            index = PhotoIndex(side.db_path)
            try:
                index.load()
                records = index.records()
                return sorted((r["path"], r["tags"], r["people"], r["raw_metadata"].get("SourceFile"), r["year"])
                              for r in records)
            finally:
                index.close()
        found = self.both(flow)
        self.assertEqual(52, len(found))
        self.assertIn("<BASE>/Pictures/2024 Regatta/IMG_1001.jpg", [row[3] for row in found])

    def test_the_doctor_says_the_same_of_both(self):
        doctor = rl.doctor()

        def flow(side):
            said = []
            broken = doctor.report(side.db_path, show=2, out=said.append)
            return [broken, [line for line in said if "under no root" not in line and not line.startswith("    ")
                             and not line.startswith("library:")]]
        self.both(flow)
        rooted_said = []
        doctor.report(self.rooted.db_path, show=2, out=rooted_said.append)
        self.assertTrue([line for line in rooted_said if "photos under no root of the library: 1" in line], rooted_said)
        plain_said = []
        doctor.report(self.plain.db_path, show=2, out=plain_said.append)
        self.assertEqual([], [line for line in plain_said if "under no root" in line])


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class NestedRoots(unittest.TestCase):
    """The model resolves a root nested inside another (the deeper wins); no command makes one any more,
    and the check finds a library edited by hand into holding one."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_nested_")
        self.side = rl.Side(self.home, "nested", real=2, bulk=15, outside=1)
        self.regatta = os.path.join(self.side.pictures, "2024 Regatta")

    def test_the_model_resolves_the_deeper_root(self):
        from tagpup.core import paths
        both = paths.Roots.of({"pictures": rl.ADDRESS, "regatta": ""},
                              {"pictures": [self.side.pictures], "regatta": [self.regatta]})
        self.assertEqual(("regatta", "IMG_1001.jpg"), both.locate(os.path.join(self.regatta, "IMG_1001.jpg")))

    def test_verify_and_the_doctor_still_find_a_row_held_under_the_wrong_root(self):
        """A library edited by hand into holding a nested root with the outer root's rows
        untouched: what the check is for."""
        self.assertTrue(self.side.adopt().ok)
        db.write_with_connection(self.side.db_path, lambda conn: store_roots.insert(conn, "regatta", ""))
        config.add_machine_root("regatta", self.regatta)
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            problems = roots_check.verify(conn, store_roots.roots_for(conn))
            broken = checks.rooted_rows_convert(conn).count
        finally:
            conn.close()
        self.assertEqual(17, broken, "17 photos")
        self.assertTrue([p for p in problems if "held under one root but lie under another" in p], problems)

    def test_verify_is_clean_when_nothing_is_nested(self):
        self.assertTrue(self.side.adopt().ok)
        self.assertTrue(self.side.adopt(location=self.side.outside_folder, name="loose", address="").ok)
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            self.assertEqual([], roots_check.verify(conn, store_roots.roots_for(conn)))
            self.assertEqual(0, checks.rooted_rows_convert(conn).count)
        finally:
            conn.close()


def keywords(path):
    from tagpup.files.exiftool_session import ExifToolSession
    with ExifToolSession(executable=EXIFTOOL) as et:
        found = et.get_tags([path], tags=["XMP:Subject"])[0].get("XMP:Subject", [])
    return sorted(found if isinstance(found, list) else [found])


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class WhenTheMachineMovesTheRoot(Twins):
    """The owner points the same library at the official share: the machine's map changes and
    no row does (docs/ARCHITECTURE.md, "Changing where a root lives")."""

    def setUp(self):
        super().setUp()
        self.moved = os.path.join(self.home.root, "share", "Pictures")
        shutil.copytree(self.rooted.pictures, self.moved)
        self.before = self.rooted.dump()

    def move_the_root(self, keep_old=True):
        places = [self.moved] + ([self.rooted.pictures] if keep_old else [])
        with open(config.machine_roots_path(), "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "roots": {"pictures": places}}, handle)

    def read(self):
        conn = db.connect(db.readonly_uri(self.rooted.db_path), uri=True)
        try:
            return sorted(path for path, *_ in store_photos.rows_under(conn, self.moved))
        finally:
            conn.close()

    def test_no_row_changes_and_every_row_reads_at_the_new_place(self):
        self.assertEqual([], self.read(), "before the move the rows are not at the new place")
        self.move_the_root()
        self.assertEqual(self.before, self.rooted.dump(), "moving the root wrote to the library")
        found = self.read()
        self.assertEqual(51, len(found))
        self.assertTrue(all(path.startswith(self.moved + os.sep) for path in found))
        conn = db.connect(db.readonly_uri(self.rooted.db_path), uri=True)
        try:
            self.assertEqual(sorted(p.replace(self.rooted.pictures, self.moved) for p in
                                    [r[0] for r in store_photos.rows_under(conn, self.rooted.pictures)]), found,
                             "the old place is still recognised: the previous location is kept")
        finally:
            conn.close()
        self.assertEqual(self.before, self.rooted.dump())

    @requires_exiftool
    def test_the_flows_work_against_the_new_place(self):
        self.move_the_root()
        target = os.path.join(self.moved, "2024 Regatta", "IMG_1001.jpg")
        result = tagging.change_tags(self.rooted.library, [target], ["At the share"], [], EXIFTOOL)
        self.assertEqual((1, []), (result.changed, result.errors))
        conn = db.connect(db.readonly_uri(self.rooted.db_path), uri=True)
        try:
            tags = {path: json.loads(t) for path, _m, _s, t, *_ in store_photos.rows_under(conn, self.moved)}
        finally:
            conn.close()
        self.assertIn("At the share", tags[target])
        self.assertIn("@pictures/2024 Regatta/IMG_1001.jpg", self.rooted.raw_paths(), "the row is the same row")
        self.assertEqual(1, len([p for p in self.rooted.raw_paths() if p.endswith("IMG_1001.jpg")]))
        # The file at the old place is another copy: written to, it is written under the same row
        # (the two places are one root's, and the page says which one writes go to).
        undone = journal_service.undo(self.rooted.library, result.details["change"], apply=True, exiftool_path=EXIFTOOL)
        self.assertEqual(1, undone.changed, undone.errors)

    @requires_exiftool
    def test_opening_a_folder_and_a_sync_at_the_new_place_say_what_they_said_at_the_old(self):
        """The same photos, at the new place: the rows describe their files there as they
        described them at the old one, so nothing is read again and nothing is missing."""
        at_the_old_place = photo_actions.scan_folder(self.rooted.library, self.rooted.pictures, EXIFTOOL)
        self.move_the_root(keep_old=False)
        # copytree kept each file's modified time: the rows' stamps match the copies.
        at_the_new_place = photo_actions.scan_folder(self.rooted.library, self.moved, EXIFTOOL)
        self.assertEqual(
            self.rooted.norm(sorted((r["path"], r["tags"], r["size"]) for r in at_the_old_place.values())),
            self.rooted.norm(sorted((r["path"], r["tags"], r["size"]) for r in at_the_new_place.values()),
                             base=os.path.dirname(self.moved)))
        result = sync.sync(self.rooted.library, apply=False, exiftool_path=EXIFTOOL, roots=[self.moved])
        counts = result.details["counts"]
        self.assertEqual((0, 0, 0), (counts["moved"], counts["changed"], counts["new"]))
        self.assertEqual(self.before, self.rooted.dump(), "a dry run at the new place wrote to the library")

    def test_the_old_place_alone_is_not_recognised_once_it_is_dropped_from_the_map(self):
        self.move_the_root(keep_old=False)
        found = self.read()
        self.assertEqual(51, len(found))
        conn = db.connect(db.readonly_uri(self.rooted.db_path), uri=True)
        try:
            self.assertEqual([], store_photos.rows_under(conn, self.rooted.pictures),
                             "a folder under no place of the root holds none of its rows")
            self.assertNotEqual(store_roots.to_row(conn, os.path.join(self.rooted.pictures, "x.jpg")),
                                "@pictures/x.jpg")
        finally:
            conn.close()

    def test_an_open_index_reads_the_new_place_once_it_looks_again(self):
        index = PhotoIndex(self.rooted.db_path)
        try:
            index.load()
            self.assertTrue(all(r["path"].startswith(self.rooted.base) for r in index.records()))
            self.move_the_root()
            with mock.patch.object(store_roots, "RECHECK_SECONDS", 0):
                records = index.records()
            self.assertTrue(all(r["path"].startswith(self.moved + os.sep) or r["path"].startswith(self.rooted.outside_folder)
                                for r in records))
            self.assertEqual(51, len([r for r in records if r["path"].startswith(self.moved + os.sep)]))
            sources = {r["raw_metadata"]["SourceFile"] for r in records if r["path"].startswith(self.moved + os.sep)}
            self.assertTrue(all(s.startswith(self.moved.replace(os.sep, "/")) for s in sources))
        finally:
            index.close()

    def test_two_libraries_on_one_machine_share_the_map(self):
        """The plain library never asks it; the rooted one is placed by it; a root the map lists
        that a library does not have is ignored by that library."""
        self.move_the_root()
        config.add_machine_root("scans", os.path.join(self.home.root, "Scans"))
        conn = db.connect(db.readonly_uri(self.plain.db_path), uri=True)
        try:
            self.assertTrue(store_roots.roots_for(conn).identity)
            self.assertEqual(52, len(store_photos.rows_under(conn, self.plain.base)))
        finally:
            conn.close()
        self.assertEqual(51, len(self.read()))
        self.assertEqual({"pictures", "scans"}, set(config.machine_roots()))


class RootsDialogCase(unittest.TestCase):
    """TagTuner's Roots dialog on a library adopted by a root, a second library sharing it, and a copy of the photos."""

    KIND = "tuner"

    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_dialog_")
        self.side = rl.Side(self.home, LIBRARY, real=6, bulk=0, outside=2)
        self.assertIsNone(self.side.adopt().refused)
        self.second = rl.Side(self.home, "second", real=2, bulk=0, outside=0, share=self.side)
        self.assertIsNone(self.second.adopt().refused)
        self.copy = os.path.join(self.home.root, "Copy", "Pictures")
        shutil.copytree(self.side.pictures, self.copy, copy_function=shutil.copy2)
        app = web.create_app(self.KIND, startup=self.side.library)
        app.testing = True
        self.client = app.test_client()
        roots_gate.forget(self.side.library)

    def get(self, path, **kwargs):
        return self.client.get("/%s%s" % (LIBRARY, path), **kwargs)

    def post(self, path, body=None):
        return self.client.post("/%s%s" % (LIBRARY, path), json=body or {})

    def move(self, location, **more):
        return self.post("/api/roots/change-location", dict({"root": "pictures", "location": location, "dry_run": False}, **more))

    def rows(self):
        return json.dumps(self.side.dump(leave_out=("job_runs",)), default=repr, sort_keys=True)

    def map_file(self):
        with open(config.machine_roots_path(), "rb") as handle:
            return handle.read()

    def places(self):
        return list(config.machine_roots()["pictures"])

    def photos_are_under(self, folder):
        found = self.get("/api/photos").get_json()
        self.assertTrue(found)
        self.assertTrue(all(photo["path"].lower().startswith(folder.lower()) for photo in found), found[:1])
        self.assertEqual(200, self.get("/api/photo-file", query_string={"path": found[0]["path"]}).status_code)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheOwnerMovesTheLocationInTheDialog(RootsDialogCase):
    """The owner looks at a move, makes it with the server running, a second tab looks at the old place, and an index run
    in either library of the root holds it up. No row changes at any step.

    Lifted: test_roots_routes (22 tests: the list, the dry run, confirming, the running server, a poor result and its
    override, a place that is not one, a stale tab, confirming twice, a run queued here or in the other library, this PC only).
    Dropped as no instance: the test that /api/roots/verify and change-back are gone (a route that does not exist), the
    machine map unreadable in the list (the unplaced scenario below says the same sentence). Break that fails the kept
    test: roots_gate.guard answering a library whose map is gone with no 409 fails
    test_a_machine_that_does_not_place_the_root_is_told_the_line_to_add."""

    def test_previewed_then_moved_with_the_server_running_and_a_stale_tab_told(self):
        rows, mapped = self.rows(), self.map_file()
        listed = self.get("/api/roots").get_json()
        self.assertEqual(("pictures", self.side.pictures, True, 18, ["second"]),
                         tuple(listed["roots"][0][key] for key in ("name", "active", "mapped", "rows", "shared_with")))
        self.assertEqual(["photo_index"], self.client.get("/second/api/roots").get_json()["roots"][0]["shared_with"])
        previewed = self.post("/api/roots/change-location", {"root": "pictures", "location": self.copy}).get_json()
        self.assertEqual((True, 0, 18), (previewed["dry_run"], previewed["changed"], previewed["verify"]["matches"]))
        self.assertEqual((rows, mapped), (self.rows(), self.map_file()), "a dry run wrote")
        self.photos_are_under(self.side.pictures)

        moved = self.move(self.copy, **{"from": self.side.pictures}).get_json()
        self.assertEqual((True, 1), (moved["success"], moved["changed"]))
        self.assertEqual([self.copy, self.side.pictures], self.places())
        self.assertEqual(rows, self.rows(), "moving a root changed a row")
        self.photos_are_under(self.copy)      # the same server, its next request
        again = self.move(self.copy, **{"from": self.side.pictures}).get_json()
        self.assertEqual((0, True), (again["changed"], again["unchanged"]), "confirming twice is one change")
        third = os.path.join(self.home.root, "Third", "Pictures")
        shutil.copytree(self.side.pictures, third, copy_function=shutil.copy2)
        stale = self.move(third, **{"from": self.side.pictures})
        self.assertEqual(409, stale.status_code)
        self.assertIn("changed since you looked", stale.get_json()["error"])
        self.assertEqual(self.copy, self.places()[0])
        self.assertEqual(200, self.move(self.side.pictures).status_code)
        self.photos_are_under(self.side.pictures)

    def test_an_index_run_in_either_library_of_the_root_holds_the_move_up(self):
        mapped = self.map_file()
        with mock.patch.object(indexing_jobs.IndexQueue, "busy", lambda queue: True):
            reply, listed = self.move(self.copy), self.get("/api/roots").get_json()
        self.assertEqual(409, reply.status_code)
        self.assertIn("an index run is running or queued", reply.get_json()["error"])
        self.assertIn("an index run is running or queued", listed["busy"])
        queue = indexing_jobs.queue_for(self.second.library)
        self.addCleanup(indexing_jobs.forget, self.second.library)
        with mock.patch.object(type(queue), "busy", lambda q: q is queue):
            reply = self.move(self.copy)
        self.assertEqual(409, reply.status_code)
        self.assertIn("in second, which uses this root too", reply.get_json()["error"])
        self.assertEqual(mapped, self.map_file())

    def test_a_place_that_is_not_the_root_is_a_sentence_and_only_an_override_moves_it(self):
        for location, word in (("Pictures", "absolute"), (os.path.join(self.home.root, "Nowhere"), "cannot be reached"),
                               (None, "absolute")):
            reply = self.move(location)
            self.assertEqual(400, reply.status_code, location)
            self.assertIn(word, reply.get_json()["error"])
        other = os.path.join(self.home.root, "Other", "Pictures")
        rl.make_jpeg(os.path.join(other, rl.FOLDERS[0], "IMG_1001.jpg"))
        poor = self.move(other)
        self.assertEqual(400, poor.status_code)
        self.assertIn("another folder", poor.get_json()["error"])
        self.assertEqual([self.side.pictures], self.places())
        self.assertEqual(200, self.move(other, override=True).status_code)
        self.assertEqual(other, self.places()[0])

    def test_the_dialog_answers_this_pc_only_and_a_library_with_no_roots_cleanly(self):
        for method, path in (("get", "/api/roots"), ("post", "/api/roots/change-location")):
            reply = getattr(self.client, method)("/%s%s" % (LIBRARY, path), environ_overrides={"REMOTE_ADDR": "10.0.0.7"})
            self.assertEqual(403, reply.status_code, path)
        rl.Side(self.home, "plain", real=1, bulk=0, outside=0)
        self.assertEqual([], self.client.get("/plain/api/roots").get_json()["roots"])
        asked = self.client.post("/plain/api/roots/change-location", json={"root": "pictures", "location": self.copy})
        self.assertEqual(404, asked.status_code)
        self.assertIn("no root called pictures", asked.get_json()["error"])
        self.assertEqual(200, self.client.get("/plain/api/photos").status_code, "a library with no roots is never gated")


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheMachineThatDoesNotPlaceTheRoot(RootsDialogCase):
    """The always-on machine's map is gone or unreadable. The web pages and the MCP server say so in one sentence naming the
    file and the line to add, the pages that read no photo still open, and placing the root from the dialog opens the
    library again.

    Lifted: test_roots_routes (the gate, in both apps, with the picker and the placing from the dialog) and
    test_roots_open_check (6: the MCP server's find_library, its tools, the checks, what reads no photo path). Breaks
    that fail the kept tests: roots_gate.guard returning None fails the web test; services.roots.problem returning None
    fails test_the_mcp_server_refuses_the_library_with_the_same_sentence."""

    def test_a_machine_that_does_not_place_the_root_is_told_the_line_to_add(self):
        os.remove(config.machine_roots_path())
        roots_gate.forget(self.side.library)
        refused = self.get("/api/photos")
        self.assertEqual(409, refused.status_code)
        self.assertTrue(refused.get_json()["roots_problem"])
        self.assertEqual("1", refused.headers["X-TagPup-Roots-Problem"])
        for words in ("machine_roots.json", '"pictures"'):
            self.assertIn(words, refused.get_json()["error"])
        tagpup = web.create_app("tagpup", startup=self.side.library).test_client()
        self.assertEqual(409, tagpup.get("/%s/api/taxonomy/tree" % LIBRARY).status_code)
        self.assertEqual(200, self.client.get("/api/databases").status_code)
        self.assertEqual(200, self.get("/api/history").status_code)
        listed = self.get("/api/roots").get_json()["roots"][0]
        self.assertEqual((False, None), (listed["mapped"], listed["active"]))
        with mock.patch("tagpup.web.tuner_routes.desktop.ask_for_folder", return_value=self.copy):
            self.assertEqual(self.copy, self.get("/api/browse-folder").get_json()["path"])    # Browse... names the place
        self.assertEqual(200, self.move(self.copy).status_code)
        self.assertEqual(200, self.get("/api/photos").status_code, "placed from the dialog, the library opens")
        with open(config.machine_roots_path(), "w", encoding="utf-8") as handle:
            handle.write("{ nope")
        roots_gate.forget(self.side.library)
        unreadable = self.get("/api/photos")
        self.assertEqual(409, unreadable.status_code)
        self.assertIn("machine_roots.json", unreadable.get_json()["error"])

    def test_the_mcp_server_refuses_the_library_with_the_same_sentence(self):
        def call(tool, arguments):
            return asyncio.run(mcp_server.build().call_tool(tool, arguments))
        self.assertIsNone(roots_service.problem(mcp_server.find_library(LIBRARY)))
        os.remove(config.machine_roots_path())
        with self.assertRaises(ToolError) as why:
            mcp_server.find_library(LIBRARY)
        self.assertIn("machine_roots.json", str(why.exception))
        self.assertIn('"pictures"', str(why.exception))
        with self.assertRaises(ToolError):
            call("folders", {"library": LIBRARY})
        self.assertIn("machine_roots.json", str(call("checks", {"library": LIBRARY})))
        # What reads no photo's path still answers: the owner can undo what put the library there.
        self.assertIsNotNone(mcp_server.find_library(LIBRARY, photos=False))
        call("history", {"library": LIBRARY})


def make_photo(path):
    from PIL import Image
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (16, 8), color="blue").save(path, "JPEG")
    return path


def stamp(path):
    stat = os.stat(path)
    return (stat.st_mtime_ns, stat.st_size)


class AFolderTheLibraryDoesNotHold(unittest.TestCase):
    """harbour holds Regatta; the share's Lighthouse is another library's. Somebody opens Lighthouse in harbour and
    writes, starts Suggest, syncs. Nothing there becomes harbour's without an Add.

    Lifted: test_writes_only_in_folders_held (11) and test_tree_grows_only_from_folders_added (4), the 2026-09-28 mistake
    (Suggest in another library's folder made rows, so sync queued its photos and the indexer grew the tree from their tags).
    Breaks that fail the kept tests, each applied to a copy of the code: photos.ensure_row making the row (`if not admit`
    read as `if False`) fails test_suggest_sync_and_the_watcher_leave_the_folder_alone; libraries.split putting every photo
    among the held with refuse_writes returning False fails test_a_write_there_touches_the_file_and_makes_no_row;
    libraries._read answering `default` for a library it cannot read fails test_a_library_that_cannot_be_read_fails_a_write."""

    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.library = Library(self.home.library("library.db"))
        self.addCleanup(tagpup_routes.folders.forget, self.library)
        self.addCleanup(indexing_jobs.forget, self.library)
        self.regatta = os.path.join(self.home.root, "Pictures", "Regatta")
        self.held = make_photo(os.path.join(self.regatta, "regatta_01.jpg"))
        conn = db.connect(self.library.path)
        try:
            photo_rows.add_read(conn, self.held, {"XMP:Subject": ["Trips/Regatta"]})
            conn.commit()
        finally:
            conn.close()
        self.lighthouse = os.path.join(self.home.root, "Share", "Lighthouse")
        self.photo = make_photo(os.path.join(self.lighthouse, "IMG_0001.jpg"))
        self.queued = []

        def index_folder(library, subfolders=True):
            def index(folder, cluster, report):
                self.queued.extend([folder] if isinstance(folder, str) else list(folder))
                return Result(attempted=1, changed=1)
            return index
        patcher = mock.patch("tagpup.runtime.index_folder", side_effect=index_folder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, route, body):
        return self.client.post("/library/api" + route, json=body)

    def not_held(self):
        return library_actions.not_held(self.library, [self.photo])

    def test_a_write_there_touches_the_file_and_makes_no_row(self):
        before = stamp(self.photo)
        for route, body in (("/photo/save-metadata", {"path": self.photo, "title": "Lighthouse", "tags": ["Trips/Lighthouse"]}),
                            ("/photos/bulk-tags", {"paths": [self.photo], "add_tags": ["Trips/Harbour"], "remove_tags": []}),
                            ("/photo/rotate", {"path": self.photo, "direction": "left"})):
            reply = self.post(route, body)
            self.assertEqual(200, reply.status_code, (route, reply.data))
            self.assertNotEqual(before, stamp(self.photo), route)
            self.assertEqual([self.lighthouse], self.not_held(), "%s made a row" % route)
            before = stamp(self.photo)
        renamed = self.post("/folder/rename-photos", {"folder_path": self.lighthouse, "photo_paths": [self.photo],
                                                      "grouping": "Lighthouse"})
        self.assertEqual(200, renamed.status_code, renamed.data)
        (name,) = os.listdir(self.lighthouse)
        self.assertTrue(name.startswith("Lighthouse - 1"), name)
        self.photo = os.path.join(self.lighthouse, name)
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=lambda p: os.remove(p) or True):
            self.assertEqual(200, self.post("/photo/delete", {"path": self.photo}).status_code)
        self.assertEqual([self.lighthouse], self.not_held())
        self.assertEqual(200, self.post("/photo/rotate", {"path": self.held, "direction": "left"}).status_code,
                         "the held folder is written as always")

    def test_a_library_that_cannot_be_read_fails_a_write_it_does_not_refuse_it(self):
        """A moment's lock is not "held nowhere": a 409 would send the owner to Add a folder the library holds."""
        before = stamp(self.held)
        with mock.patch("tagpup.store.folders.holds", side_effect=sqlite3.OperationalError("database is locked")):
            written = self.post("/photo/rotate", {"path": self.held, "direction": "left"})
            suggested = self.post("/folder/suggest-start", {"folder_path": self.regatta})
        self.assertEqual(500, written.status_code, written.data)
        self.assertEqual(before, stamp(self.held))
        self.assertEqual(503, suggested.status_code, suggested.data)
        self.assertIn("nothing was started", suggested.get_json()["error"])

    def test_suggest_sync_and_the_watcher_leave_the_folder_alone_until_it_is_added(self):
        with contextlib.suppress(Conflict):      # refused now; kept, and a row made, before
            saved_suggestions.keep(self.library.path, self.photo, {"tags": ["Family/Work"]})
        runtimes.sync(self.library, apply=True)
        indexing_jobs.queue_for(self.library).wait()
        self.assertEqual([], [f for f in self.queued if paths.same(f, self.lighthouse) or paths.is_under(f, self.lighthouse)],
                         "the indexer was queued for a folder never added")
        self.assertFalse(any(paths.same(w, self.lighthouse) or paths.is_under(self.lighthouse, w)
                             for w in runtimes.watch_folders(self.library)), "the watcher watches it")
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            self.assertFalse([tag for tag in taxonomy.tags(conn) if tag.startswith("Family")])
        finally:
            conn.close()
        # Added, it is the library's: sync queues its files for the indexer and reads none of them itself.
        library_actions.record_added(self.library, [self.lighthouse])
        found = runtimes.sync(self.library, apply=True).details["counts"]
        indexing_jobs.queue_for(self.library).wait()
        self.assertEqual((0, 0, 0), (found["changed"], found["never_stamped"], found["to_write"]))
        self.assertTrue(any(paths.same(folder, self.lighthouse) for folder in self.queued))


if __name__ == "__main__":
    unittest.main()
