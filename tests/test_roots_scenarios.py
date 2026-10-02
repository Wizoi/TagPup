"""What the owner does with a library that holds a root, against one that does not
(docs/ARCHITECTURE.md, "Roots and machines").

Two libraries are built alike in folders of their own (tests/roots_library.py): thousands of
rows' worth of shape -- photos under a root-shaped folder, a few beside it, faces,
suggestions, added folders, damaged files, a journal of changes made before anything was
adopted -- and one is adopted by the root. Then the same flows run on both, with real
ExifTool on real JPEGs, and what each answers must be the same once its own folder is taken
out of it: the folder listing, a tag save, bulk tags, a rename and its undo, a delete, a sync that
finds a file moved, new and gone, the indexer recording a photo, Suggest keeping a suggestion,
a damaged file recorded, a folder added, a search, and the doctor. Then the machine's map is
changed to another place and nothing in the rows changes, and the same flows work there.
"""
import json
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import photo_rows  # noqa: E402
import roots_library as rl  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.services import sync, tagging  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import added_folders, damaged_files, db, folders, journal, suggestions  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402

WINDOWS = os.name == "nt"
EXIFTOOL = rl.EXIFTOOL
FORMAT = "{grouping} - {index} - {caption}"

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
        """The library as its owner sees it: every photo and its tags, the damaged files, the
        folders added and ignored, and the journal's names and states."""
        conn = db.connect(db.readonly_uri(side.db_path), uri=True)
        try:
            listing = sorted((path, tags) for path, _m, _s, tags, _p, _c, _r in store_photos.rows_under(
                conn, side.base))
            held = sorted(folders.of(conn).listed())
            damaged = [r.path for r in damaged_files.every(conn)]
            added = added_folders.every(conn)
            ignored = folders.ignored(conn)
        finally:
            conn.close()
        changes = [(e["operation"], e["status"], e["files"]) for e in journal.history(side.db_path, limit=50)
                   if not e["operation"].startswith(("migration", "roots adopt"))]
        return {"photos": listing, "folders": held, "damaged": damaged, "added": added, "ignored": ignored,
                "journal": changes}

    def same_seen(self):
        self.assertEqual(self.plain.norm(self.seen(self.plain)), self.rooted.norm(self.seen(self.rooted)))

    def rooted_rows_are_rows(self):
        """Whatever a flow did, the library holds no native row under the root."""
        native = [p for p in self.rooted.raw_paths() if not p.startswith("@")
                  and p.lower().startswith(self.rooted.pictures.lower() + os.sep)]
        self.assertEqual([], native)
        for table, column in (("damaged_files", "path"), ("added_folders", "path"), ("change_files", "path"),
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

    def test_a_damaged_file_is_recorded_listed_and_forgotten(self):
        def flow(side):
            path = os.path.join(side.pictures, "Trips", "2025 Coast", "Cut short too.jpg")
            seen = []

            def write(conn):
                seen.append(damaged_files.record(conn, path, (2.0, 50), "truncated", "ends early"))
                seen.append(damaged_files.record(conn, path, (2.0, 50), "truncated", "ends early"))
                seen.append([r.path for r in damaged_files.under(conn, side.pictures)])
                seen.append(damaged_files.forget(conn, [path]))
            db.write_with_connection(side.db_path, write)
            conn = db.connect(db.readonly_uri(side.db_path), uri=True)
            try:
                seen.append([r.path for r in damaged_files.every(conn)])
            finally:
                conn.close()
            return seen
        found = self.both(flow)
        self.assertEqual([True, False], found[:2])

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


if __name__ == "__main__":
    unittest.main()
