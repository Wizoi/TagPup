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

A folder's Smart Rename of a folder the library does not hold writes the files only and moves no counter, so nothing
lets a kept scan go: the subfolder's scan and its parent's must follow every photo it renamed, and the file it moved
aside, or opening the parent shows names no file has any more (#2892). Numbered names shuffled among themselves move
each record once. A rename that stops part-way stopped in its second part, after the photos of the folders the library
holds were renamed with their rows: the library moved, every kept scan is let go, and the answer is the folder scanned
again.

The services stand in (each renames the file on disk as the real one does and answers its Result); what is under test is
the routes' follow-up. Written against the trunk before the follow-up was folded into one function, and passing there.
"""
import os
import sys
import unittest
from unittest import mock

import flask

from tests import page_writes
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
    """POST /api/photos/write op `set`: a title saved, the photo renamed after its caption."""

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
                                  dates={}, taken=None, base={"tags": list(tags), "title": title})
            return result
        with mock.patch.object(tagpup_routes.tagging_actions, "save_photo", side_effect=saving):
            found = self.lib.folders().peek(self.folder)[paths.key(self.old)]   # the panel's record of the photo
            status, reply = self.lib.post(page_writes.ROUTE, page_writes.set_from(
                found, title="Harbour Day", tags=["Trips/Coast", "Boats"]))
        self.assertEqual(200, status, reply)
        return page_writes.saved(reply)


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


class AFolderRenamedThatTheLibraryDoesNotHold(unittest.TestCase):
    """POST /api/folder/rename-photos in a subfolder the library does not hold, its scan kept and its parent's: the
    files renamed and written (their names kept first), no row, no counter moved. The two photos asked for take the
    numbers the order gives them, the first the second's old name and the second a name a file not asked for held,
    which is moved aside first, as the service does."""

    def setUp(self):
        self.lib = Library(self)
        self.folder = os.path.join(self.lib.photos, "Day 1")
        os.makedirs(self.folder)
        self.first, self.second, self.other = (paths.stored(os.path.join(self.folder, name)) for name in (
            "Harbour - 1.jpg", "Harbour - 2.jpg", "Harbour - 3.jpg"))
        records = {}
        for path, title in ((self.first, "First"), (self.second, "Second"), (self.other, "Other")):
            with open(path, "wb") as handle:
                handle.write(title.encode())
            record = photo_actions.page_record(path, {"tags": [], "raw_metadata": {}}, *OLD_STAMP)
            record["title"] = title
            records[paths.key(path)] = record
        self.lib.folders().put(self.folder, records)
        self.lib.folders().put(self.lib.photos, {key: dict(record) for key, record in records.items()})
        self.lib.suggestion_runs().looks[paths.key(self.folder)] = {
            path: {"raw_suggestions": {"path": path}} for path in (self.first, self.second, self.other)}
        self.aside = paths.stored(os.path.join(self.folder, "Harbour - 3_conflict_1.jpg"))

    def rename(self, stops_after_first=False):
        """The two photos renamed, numbered first-taken first: the second (taken first) to 1, the first to 3. Stopped
        part-way, the first alone, after a photo of a held folder was renamed with its row (a row written: the photos
        counter moves)."""
        wanted = {self.second: self.first, self.first: self.other}

        def renaming(_library, photo_paths, _grouping, _format, _exiftool):
            result = Result(attempted=len(photo_paths))
            moves = {self.first: self.other} if stops_after_first else wanted
            os.replace(self.other, self.aside)
            for old in moves:
                os.replace(old, old + ".tmp")
            for old, new in moves.items():
                os.replace(old + ".tmp", new)
                with open(new, "ab") as handle:
                    handle.write(b" its name kept")
            result.changed = len(moves)
            result.details.update(updated_paths=dict(moves), renamed=dict(moves), moved_aside={self.other: self.aside},
                                  index_rows_moved=0, index_skipped=[], file_only=len(moves), with_rows=0)
            if stops_after_first:
                self.lib.hold(os.path.join(self.lib.photos, "Day 2"))
                result.fail("smart rename", "Could not rename: the share went away.")
            return result
        with mock.patch.object(tagpup_routes.photo_actions, "smart_rename", side_effect=renaming):
            return self.lib.post("/api/folder/rename-photos", {
                "folder_path": self.folder, "photo_paths": [self.first, self.second], "grouping": "Harbour"})

    def held(self, folder):
        """{name: (title, path, filename, mtime, size)} of a kept scan, as it is handed back (get: the stamp checked)."""
        kept = self.lib.folders().get(folder)
        self.assertIsNotNone(kept, "the kept scan was let go: nothing moved the library")
        return {os.path.basename(key): (r["title"], r["path"], r["filename"], r["mtime"], r["size"])
                for key, r in kept.items()}

    def expected(self, titles):
        """{name: the record held of the file of that name now}, for {path: the title of the photo it is}."""
        found = {}
        for path, title in titles.items():
            stamp = os.stat(path)
            found[os.path.basename(paths.key(path))] = (title, path, os.path.basename(path), stamp.st_mtime, stamp.st_size)
        return found

    def test_every_scan_holding_the_photos_follows_them_under_their_new_names_and_stamps(self):
        status, reply = self.rename()
        self.assertEqual(200, status, reply)
        now = self.expected({self.first: "Second", self.other: "First", self.aside: "Other"})
        self.assertEqual(now, self.held(self.lib.photos), "the parent's scan, whose walk took the photos in")
        self.assertEqual(now, self.held(self.folder), "the subfolder's scan")
        self.assertEqual({self.second: self.first, self.first: self.other}, reply["updated_paths"])
        self.assertEqual(sorted([self.first, self.other, self.aside]), sorted(r["path"] for r in reply["updated_photos"]))

    def test_suggests_looks_follow_every_photo_and_the_file_moved_aside(self):
        # Each photo's looks under its new name, the file moved aside's too: left under its old name, the name the first
        # photo took, one of the two overwrote the other and the first photo showed the other file's suggestions.
        self.lib.suggestion_runs().looks[paths.key(self.folder)] = {
            path: {"raw_suggestions": {"path": path, "tags": [title]}}
            for path, title in ((self.first, "Trips/First"), (self.second, "Trips/Second"), (self.other, "Trips/Other"))}
        status, reply = self.rename()
        self.assertEqual(200, status, reply)
        looks = self.lib.suggestion_runs().looks[paths.key(self.folder)]
        self.assertEqual({self.first: (self.first, ["Trips/Second"]), self.other: (self.other, ["Trips/First"]),
                          self.aside: (self.aside, ["Trips/Other"])},
                         {path: (entry["raw_suggestions"]["path"], entry["raw_suggestions"]["tags"])
                          for path, entry in looks.items()})

    def test_a_rename_that_stops_part_way_answers_the_folder_scanned_again(self):
        status, reply = self.rename(stops_after_first=True)
        self.assertEqual(500, status, reply)
        self.assertEqual({self.first: self.other}, reply["updated_paths"])
        now = sorted([self.second, self.other, self.aside])
        self.assertEqual(now, sorted(r["path"] for r in reply["updated_photos"]), "the folder as it is now")
        self.assertIsNone(self.lib.folders().get(self.lib.photos), "the parent's scan, kept past a move of the library")
        self.assertEqual(now, sorted(r["path"] for r in self.lib.folders().get(self.folder).values()),
                         "the folder's scan is the one answered")


class AFolderRenamedWhileAnAncestorIsScanned(unittest.TestCase):
    """A folder of many photos renamed (the keys of the kept maps move) while the owner's other tab opens the ancestor,
    whose scan reads the very map the rename re-keys: every scan answers every photo, never a map the rename has taken
    the photos out of and not yet put back (#2907)."""

    COUNT = 3000

    def setUp(self):
        self.lib = Library(self)
        self.folder = os.path.join(self.lib.photos, "Day 1")
        os.makedirs(self.folder)
        self.names = [paths.stored(os.path.join(self.folder, "Harbour - %04d.jpg" % number)) for number in range(self.COUNT)]
        self.records = {}
        for path in self.names:
            self.records[paths.key(path)] = photo_actions.page_record(path, {"tags": [], "raw_metadata": {}}, *OLD_STAMP)
        self.lib.folders().put(self.lib.photos, dict(self.records))

    def renamed(self, to_new):
        """Every photo's name changed (or changed back), the follow-up the routes make."""
        moves = [(old.replace("Harbour", "Quay") if not to_new else old, old if not to_new else old.replace("Harbour", "Quay"),
                  True) for old in self.names]
        tagpup_routes._followed_rename(self.lib.library, moves)

    def test_scans_of_the_ancestor_always_hold_every_photo_while_the_folder_is_renamed_back_and_forth(self):
        import threading
        failed, stop = [], threading.Event()

        def scanning():
            while not stop.is_set():
                status, reply = self.lib.get("/api/folder/scan", {"path": self.lib.photos})
                if status != 200 or len(reply) != self.COUNT:
                    failed.append((status, reply if status != 200 else len(reply)))
        scanner = threading.Thread(target=scanning)
        scanner.start()
        try:
            for round_ in range(20):
                self.renamed(round_ % 2 == 0)
        finally:
            stop.set()
            scanner.join()
        self.assertEqual([], failed[:3])
        self.assertEqual(self.COUNT, len(self.lib.folders().peek(self.lib.photos)), "every record kept")

    def test_what_a_scan_put_hands_back_is_a_copy_a_rename_does_not_change(self):
        """The path taken after any write (the kept map is gone, the folder is scanned and put): the caller walks what put
        gave back while the rename re-keys the map it stored (#2910, the review's second row)."""
        cache = self.lib.folders()
        cache.clear()
        scanned = {paths.key(os.path.join(self.folder, "Harbour - %04d.jpg" % number)): {"n": number}
                   for number in range(5)}
        walked = cache.put(self.lib.photos, scanned)
        cache.move_all([(paths.stored(os.path.join(self.folder, "Harbour - 0000.jpg")),
                         paths.stored(os.path.join(self.folder, "Quay - 0000.jpg")), None)])
        self.assertIn(paths.key(os.path.join(self.folder, "Harbour - 0000.jpg")), walked,
                      "what put gave back changed with the rename: it is the stored map, not a copy")
        self.assertNotIn(paths.key(os.path.join(self.folder, "Harbour - 0000.jpg")), cache.peek(self.lib.photos))

    def test_the_readers_of_a_scan_walk_a_copy_while_the_folder_is_renamed_back_and_forth(self):
        """What Suggest's photos, a time shift's targets and the suggest-status answer each walk: the records of the
        folder's map, one after another, while the rename re-keys the map (#2910)."""
        import threading
        failed, stop = [], threading.Event()
        library = self.lib.library

        def walking(reader):
            while not stop.is_set():
                try:
                    reader()
                except Exception as e:
                    failed.append(repr(e))
                    return

        def suggest_photos():
            held = tagpup_routes._folder_photos(library, self.lib.photos)
            {key: meta for key, meta in held.items() if meta["media"] == "photo"}

        def time_shift_targets():
            held = tagpup_routes._folder_photos(library, self.lib.photos)
            [paths.stored(record["path"]) for record in held.values() if record["media"] == "photo"]

        def suggest_status():
            status, reply = self.lib.get("/api/folder/suggest-status", {"path": self.lib.photos})
            if status != 200:
                failed.append((status, reply))
        readers = [threading.Thread(target=walking, args=(reader,))
                   for reader in (suggest_photos, time_shift_targets, suggest_status)]
        for reader in readers:
            reader.start()
        # The map is re-keyed through its one owner, over and over, with the threads switched often: a rename of a
        # folder this size is a moment, and the race is a reader caught inside it.
        cache = self.lib.folders()
        there = [paths.stored(os.path.join(self.folder, "Harbour - %04d.jpg" % number)) for number in range(self.COUNT)]
        away = [path.replace("Harbour", "Quay") for path in there]
        switch = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            for round_ in range(40):
                before, after = (there, away) if round_ % 2 == 0 else (away, there)
                cache.move_all([(old, new, None) for old, new in zip(before, after)])
                if failed:
                    break
        finally:
            sys.setswitchinterval(switch)
            stop.set()
            for reader in readers:
                reader.join()
        self.assertEqual([], failed[:3])


if __name__ == "__main__":
    unittest.main()
