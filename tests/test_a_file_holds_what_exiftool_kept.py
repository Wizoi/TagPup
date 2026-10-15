"""A file keeps what ExifTool keeps of a value, and the journal, the row and a settling after a crash say what the FILE holds
(scenario F1, tagpup.services.file_changes, tagpup.core.fields.as_read).

ExifTool does not always keep a value as written: IPTC:Keywords is cut at 64 bytes, a letter outside Latin-1 comes back as "?",
a caption "1.50" is answered as the number 1.5 and "true" as a boolean. A journal that compared the value asked for with the
file's would call a file written before a crash a conflict, record it done holding what it was never to hold (so that undo
refused it), plan the same caption again on every edit, and count a write that changed nothing as a change.

Real ExifTool, on JPEGs made here, in a home of the test's own; rows as the indexer stores them (photo_rows.add_read); a crash
is a BaseException at a step of the write (file_changes.STEPS).

Lifted: test_a_number_compares_as_read (#264), test_a_boolean_compares_as_read (#264 #278), test_settling_compares_as_read
(#277), test_a_write_records_what_the_file_holds (#273), test_a_crash_reads_back_what_it_wrote (#273 #286),
test_changed_counts_what_changed (#276). Mutations, each in a scratch copy of the code, each failing the test named:
core/fields.py `_as_read` without the IPTC cut, or with a letter dropped and not "?" -> test_a_crash_after_the_write_settles_done;
without the number arm (`if _JSON_NUMBER.fullmatch(text)` -> `if False`) -> the same test and
test_a_file_already_holding_it_is_not_written_again; without the boolean arm -> the latter; services/file_changes.py `_carry`
settling by `now == target` rather than `fields.reads_same(now, target)` -> test_a_crash_after_the_write_settles_done;
`_read_back` left out of the `except BaseException` arm of write_fields -> test_a_change_stopped_part_way; `result.changed -= 1`
-> both test_a_write_that_changed_nothing tests; `file_journal.set_after(conn, row.id, kept)` -> test_a_write_records_what;
the guard that leaves a file another program changed since its write -> test_a_file_changed_outside.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_file_journal import EXIFTOOL, Crash, FilesCase, crash_at, keywords_of, write_outside  # noqa: E402

import photo_rows  # noqa: E402
from tagpup.files import field_values  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import file_changes, tagging  # noqa: E402
from tagpup.store import db  # noqa: E402

#: 98 bytes, longer than the 64 IPTC:Keywords keeps.
LONG = "Places/" + "Harbour" * 13
#: A letter IPTC's default character set (Latin) does not hold.
L_STROKE = chr(0x141)


class ReadFilesCase(FilesCase):
    """Rows as the indexer makes them (tests/photo_rows.py); the case of every test of a write to a photo that was read."""

    def make_read(self, name, tags=("Beach",)):
        """A photo holding `tags`, its row as the indexer leaves it after reading them."""
        path = self.make(name, tags, row=False)
        conn = db.connect(self.library.path)
        try:
            photo_rows.add_read(conn, path, {"XMP:Subject": list(tags), "IPTC:Keywords": list(tags)})
            conn.commit()
        finally:
            conn.close()
        return path

    def caption(self, photo_paths, caption):
        # The page's save of one photo: its caption, the tags it holds already.
        for path in photo_paths:
            result = tagging.save_photo(self.library, path, caption, ["Beach"], None, EXIFTOOL, "")
        return result


class KeptCase(ReadFilesCase):
    def after_of(self, change, path=None):
        sql, params = "SELECT fields_after FROM change_files WHERE change_id = ?", (change,)
        if path is not None:
            sql, params = sql + " AND path = ?", params + (path,)
        return json.loads(self.rows(sql, params)[0][0])

    def iptc_of(self, path):
        with ExifToolSession(executable=EXIFTOOL) as et:
            found = et.get_tags([path], tags=["IPTC:Keywords"])[0].get("IPTC:Keywords")
        return sorted(found if isinstance(found, list) else [found])


class WhatTheFileKept(KeptCase):
    def test_a_write_records_what_the_file_holds_and_an_undo_finds_it(self):
        photos = [self.make_read(name) for name in ("a.jpg", "b.jpg", "c.jpg")]
        with mock.patch.object(field_values, "read", wraps=field_values.read) as read:
            result = self.add(photos, [LONG])
        change = result.details["change"]
        self.assertEqual(3, result.changed)
        # Read for the plan and read back, each once for every file; the check before each write is one file at a time.
        self.assertEqual(2, sum(1 for call in read.call_args_list if len(call.args[1]) == 3))
        cut = [t for t in self.iptc_of(photos[0]) if t != "Beach"][0]
        self.assertEqual(64, len(cut.encode("utf-8")))
        self.assertEqual(sorted(["Beach", cut]), sorted(self.after_of(change, photos[0])["IPTC:Keywords"]))
        raw = json.loads(self.rows("SELECT raw_metadata FROM photos WHERE path = ?", (photos[0],))[0][0])
        self.assertEqual(sorted(["Beach", cut]), sorted(raw["IPTC:Keywords"]))
        self.assertEqual(["Beach", LONG], keywords_of(photos[0]))

        undone = self.undo(change)
        self.assertEqual((3, []), (undone.changed, undone.errors))
        for path in photos:
            self.assertEqual((["Beach"], ["Beach"], ["Beach"]), (keywords_of(path), self.iptc_of(path), self.indexed(path)))

    def test_a_file_changed_outside_since_its_write_is_not_taken_as_the_writes(self):
        photos = [self.make_read(name) for name in ("a.jpg", "b.jpg", "c.jpg")]
        first, seen = photos[0], []

        def elsewhere(step):
            if step == "file recorded" and not seen:
                seen.append(step)
                write_outside(first, {"XMP:Subject": ["Written Elsewhere"]})

        with mock.patch.object(file_changes, "_reached", side_effect=elsewhere):
            change = self.add(photos, [LONG]).details["change"]
        after = self.after_of(change, first)
        self.assertEqual(["Beach", LONG], sorted(after["XMP:Subject"]))
        self.assertEqual(["Beach", LONG], sorted(after["IPTC:Keywords"]))
        undone = self.undo(change)
        self.assertEqual(2, undone.changed)
        self.assertIn("photo %d" % self.photo_id(first), [what for what, _why in undone.errors])
        self.assertEqual(["Written Elsewhere"], keywords_of(first))

    def test_a_crash_after_the_write_settles_done_whatever_the_file_kept(self):
        # (what is written, what a read of the file gives for it in the journal, and in which field)
        cases = {
            "a long keyword": (lambda a: self.add([a], [LONG]), "IPTC:Keywords", LONG[:64]),
            "a letter IPTC cannot hold": (lambda a: self.add([a], ["Places/" + L_STROKE + "odz"]), "IPTC:Keywords", "Places/?odz"),
            "a number": (lambda a: self.caption([a], "1.50"), "XMP:Description", "1.5"),
            "a boolean": (lambda a: self.caption([a], "true"), "XMP:Description", "True"),
        }
        for label, (write, field, kept) in cases.items():
            with self.subTest(label):
                a = self.make_read(label.replace(" ", "_") + ".jpg")
                with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written")):
                    with self.assertRaises(Crash):
                        write(a)
                change = self.last_change()
                self.assertEqual(1, self.settle())
                self.assertEqual(("applied", ["done"]), (self.status(change), self.states(change)))
                # Recorded as the file holds it, so undoing the change finds what it left.
                self.assertIn(kept, self.after_of(change)[field])
                undone = self.undo(change)
                self.assertEqual((1, []), (undone.changed, undone.errors))

    def test_a_file_already_holding_it_is_not_written_again(self):
        for text in ("1.50", "true"):
            with self.subTest(text):
                a = self.make_read("again_%s.jpg" % text)
                self.assertEqual(1, self.caption([a], text).changed)
                written = os.stat(a).st_mtime_ns
                again = self.caption([a], text)
                self.assertEqual((0, None), (again.changed, again.details["change"]))
                self.assertEqual(written, os.stat(a).st_mtime_ns)

    def test_a_change_stopped_part_way_reads_back_the_files_it_recorded(self):
        photos = [self.make_read(name) for name in ("a.jpg", "b.jpg", "c.jpg")]
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file recorded", nth=2)):
            with self.assertRaises(Crash):
                self.add(photos, [LONG])
        change = self.last_change()
        found = self.rows("SELECT path, state, fields_after FROM change_files WHERE change_id = ? ORDER BY id", (change,))
        self.assertEqual(["done", "done", "planned"], [state for _p, state, _a in found])
        for path, _state, after in found[:2]:
            self.assertEqual(sorted(["Beach", LONG[:64]]), sorted(json.loads(after)["IPTC:Keywords"]), path)
            raw = json.loads(self.rows("SELECT raw_metadata FROM photos WHERE path = ?", (path,))[0][0])
            self.assertEqual(sorted(["Beach", LONG[:64]]), sorted(raw["IPTC:Keywords"]))


class AWriteThatChangedNothing(KeptCase):
    """A write ExifTool reported done that left the file holding what it held is an error, taken out of the change (#276)."""

    def test_is_not_counted_changed(self):
        a, b = self.make_read("a.jpg"), self.make_read("b.jpg")
        real = field_values.write

        def write(et, path, values):
            if path != a:
                real(et, path, values)

        with mock.patch.object(field_values, "write", side_effect=write):
            result = self.add([a, b])
        change = result.details["change"]
        self.assertEqual(1, result.changed)
        self.assertEqual([a], [what for what, _why in result.errors])
        self.assertNotIn(a, result.details["written"])
        self.assertEqual(["Beach"], keywords_of(a))
        self.assertEqual(["done"], self.states(change), "the file that did not change is still in the change")
        self.assertEqual("applied", self.status(change))

    def test_nothing_changed_is_a_failed_change(self):
        a = self.make_read("a.jpg")
        with mock.patch.object(field_values, "write"):
            result = self.add([a])
        self.assertEqual((0, 1), (result.changed, len(result.errors)))
        self.assertEqual(("failed", []), (self.status(result.details["change"]), self.states(result.details["change"])))


if __name__ == "__main__":
    unittest.main()
