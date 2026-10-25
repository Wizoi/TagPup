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

A bake (tagpup.files.bake) is kept as ExifTool keeps it too: its regions are one structure, journaled as ONE text, the
canonical serialised form ExifTool writes as given, so the text a read gives is the text written (4000 and 4000.0 alike, a
float of 17 digits exactly); a dict in a plan is refused (it was planned as "clear the regions"), and so is a session that
does not know TagPup's namespace (ABakeIsWritten).
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_file_journal import EXIFTOOL, Crash, FilesCase, crash_at, keywords_of, write_outside  # noqa: E402

import baked_files  # noqa: E402
import photo_rows  # noqa: E402
from tagpup.core import fields, orientation, paths  # noqa: E402
from tagpup.files import bake, field_values  # noqa: E402
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


#: Another program's MWG region (a name holding the serialised form's special characters) and its Microsoft Photo one.
FOREIGN_MWG = ("-XMP-mwg-rs:RegionInfo={AppliedToDimensions={W=16,H=12,Unit=pixel},RegionList=[{Type=Face,"
               "Name=Ash|, Corrin |{x|}||y,Area={X=0.5,Y=0.5,W=0.25,H=0.25,Unit=normalized}}]}")
FOREIGN_MP = "-XMP-MP:RegionInfoMP={Regions=[{PersonDisplayName=Ash Corrin,Rectangle=0.1|, 0.2|, 0.3|, 0.4}]}"


class ABakeIsWritten(KeptCase):
    """A bake written through the one writer, journaled, undone, settled after a crash (PHASE6_DESIGN, section 9)."""

    def values(self, faces, seed=1):
        # A face n of a 16 x 12 picture in a cell of an 8 x 8 grid: 60 of them span Extended XMP.
        boxes = [[n % 8 * 2, n // 8 * 1.5, n % 8 * 2 + 1.5, n // 8 * 1.5 + 1.25] for n in range(faces)]
        return bake.values(baked_files.unit(bake.PHOTO_DIM, seed),
                           [(box, baked_files.unit(bake.FACE_DIM, 10 * seed + n)) for n, box in enumerate(boxes)],
                           (16, 12), baked_files.DIGEST, baked_files.PHOTO_MODEL, baked_files.FACE_MODEL)

    def bake(self, photo_paths, values):
        return file_changes.write_fields(self.library, "bake", EXIFTOOL, photo_paths, list(values),
                                         lambda path, held: file_changes.Plan(after=values))

    def crash(self, step, photo_paths, values):
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at(step)):
            with self.assertRaises(Crash):
                self.bake(photo_paths, values)
        return self.last_change()

    def bake_of(self, path):
        records = {}
        with ExifToolSession(executable=EXIFTOOL, common_args=list(bake.READ_ARGS)) as et:
            field_values.read(et, [path], [], also=bake.FIELDS, records=records)
        return bake.parse(records[paths.key(path)])

    def regions_of(self, path):
        with ExifToolSession(executable=EXIFTOOL) as et:
            found = et.get_tags([path], tags=["XMP:RegionInfo", "XMP:RegionInfoMP"], params=["-struct"])[0]
        return {key: value for key, value in found.items() if key != "SourceFile"}

    def outside(self, path, *args):
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.execute(*args, "-overwrite_original", path)

    def assert_baked(self, path, values, faces):
        held = self.bake_of(path)
        self.assertIsInstance(held, bake.Bake, getattr(held, "reason", ""))
        self.assertEqual((baked_files.PHOTO_MODEL, baked_files.DIGEST, faces),
                         (held.photo_model, held.fingerprint, len(held.faces)))
        if faces:
            self.assertEqual([values[fields.REGION_INFO][0]], [bake.region_text(self.regions_of(path)["XMP:RegionInfo"])])

    def test_a_bake_reads_back_as_made_is_journaled_as_its_texts_and_an_undo_clears_it_in_one_command(self):
        a = self.make_read("a.jpg")
        values = self.values(2)
        result = self.bake([a], values)
        self.assertEqual((1, []), (result.changed, result.errors))
        change = result.details["change"]
        self.assertEqual(values, self.after_of(change), "the journal's after is what bake.values gave, text for text")
        self.assert_baked(a, values, 2)
        held = self.bake_of(a)
        for n, face in enumerate(held.faces):
            box = [n % 8 * 2, n // 8 * 1.5, n % 8 * 2 + 1.5, n // 8 * 1.5 + 1.25]
            self.assertEqual(orientation.area_of(box, 16, 12), face.area, "a region read back is the float written")
            self.assertGreater(float(face.vector @ baked_files.unit(bake.FACE_DIM, 10 + n)), 0.999)

        with mock.patch.object(field_values, "write", wraps=field_values.write) as write:
            undone = self.undo(change)
        self.assertEqual((1, []), (undone.changed, undone.errors))
        self.assertEqual([{field: [] for field in values}], [call.args[2] for call in write.call_args_list],
                         "an undo of a bake on a photo holding none is one clearing command")
        self.assertEqual(bake.NOT_BAKED, self.bake_of(a).reason)
        self.assertEqual({}, self.regions_of(a))
        self.assertEqual((["Beach"], ["Beach"]), (keywords_of(a), self.indexed(a)))

    def test_another_programs_regions_are_replaced_and_put_back_and_its_microsoft_regions_kept(self):
        for faces in (2, 0):
            with self.subTest(faces=faces):
                a = self.make_read("regions_%d.jpg" % faces)
                self.outside(a, FOREIGN_MWG, FOREIGN_MP)
                before = self.regions_of(a)
                self.assertEqual("Ash, Corrin {x}|y", before["XMP:RegionInfo"]["RegionList"][0]["Name"])
                values = self.values(faces)
                change = self.bake([a], values).details["change"]
                self.assert_baked(a, values, faces)
                after = self.regions_of(a)
                self.assertEqual(before["XMP:RegionInfoMP"], after["XMP:RegionInfoMP"])
                self.assertEqual(faces > 0, "XMP:RegionInfo" in after, "no faces leaves no regions")
                undone = self.undo(change)
                self.assertEqual((1, []), (undone.changed, undone.errors))
                self.assertEqual(before, self.regions_of(a))

    def test_a_bake_spanning_extended_xmp_settles_forward_after_a_crash(self):
        values = self.values(60)
        for step in ("file writing", "file written"):
            with self.subTest(step):
                a = self.make_read(step.replace(" ", "_") + ".jpg")
                change = self.crash(step, [a], values)
                self.assertEqual(["writing"], self.states(change))
                written = os.stat(a).st_mtime_ns
                self.assertEqual(1, self.settle())
                self.assertEqual(("applied", ["done"]), (self.status(change), self.states(change)))
                self.assert_baked(a, values, 60)
                self.assertEqual(values, self.after_of(change))
                if step == "file written":
                    self.assertEqual(written, os.stat(a).st_mtime_ns, "a file holding the bake was written again")
                with ExifToolSession(executable=EXIFTOOL) as et:
                    self.assertTrue(et.get_tags([a], tags=["XMP:HasExtendedXMP"])[0].get("XMP:HasExtendedXMP"))
                self.assertGreater(os.path.getsize(a), 80 * 1024)

    def test_regions_another_program_wrote_before_settling_are_a_conflict_not_overwritten(self):
        a = self.make_read("a.jpg")
        change = self.crash("file writing", [a], self.values(2))
        self.outside(a, FOREIGN_MWG)
        self.settle()
        self.assertEqual(["conflict"], self.states(change))
        self.assertEqual(bake.NOT_BAKED, self.bake_of(a).reason)
        self.assertEqual("Ash, Corrin {x}|y", self.regions_of(a)["XMP:RegionInfo"]["RegionList"][0]["Name"])

    def test_a_structure_as_a_dict_and_a_session_that_does_not_know_the_namespace_are_refused(self):
        a = self.make_read("a.jpg")
        written = os.stat(a).st_mtime_ns
        regions = {"AppliedToDimensions": {"W": 16, "H": 12, "Unit": "pixel"}, "RegionList": []}
        with self.assertRaises(TypeError):
            self.bake([a], {fields.REGION_INFO: regions})
        with ExifToolSession(executable=EXIFTOOL) as et:
            with self.assertRaises(ValueError):
                field_values.write(et, a, self.values(1))
        self.assertEqual((None, written), (self.last_change(), os.stat(a).st_mtime_ns))

    def test_a_structure_reads_back_as_the_text_written(self):
        a = self.make_read("a.jpg")
        written = {"AppliedToDimensions": {"W": 4000.0, "H": 3000, "Unit": "pixel"},
                   "RegionList": [{"Type": "Face", "Name": "Ash, Corrin {x}|y",
                                   "Area": {"X": 0.20833333333333334, "Y": 1 / 3, "W": 1e-05, "H": 0.5, "Unit": "normalized"}}]}
        text = bake.region_text(written)
        self.assertEqual(text, bake.region_text(dict(written, AppliedToDimensions={"W": 4000, "H": 3000.0, "Unit": "pixel"})))
        with field_values.session(EXIFTOOL) as et:
            field_values.write(et, a, {fields.REGION_INFO: [text]})
            self.assertEqual({fields.REGION_INFO: [text]}, field_values.read_one(et, a, [fields.REGION_INFO]))
        area = self.regions_of(a)["XMP:RegionInfo"]["RegionList"][0]["Area"]
        self.assertEqual((0.20833333333333334, 1 / 3, 1e-05), tuple(bake._number(area[k], k) for k in "XYW"))

    def test_a_text_with_blanks_at_its_edges_reads_back_as_written(self):
        # ExifTool trims the blanks at a value's edges unless each is escaped (#1880): another program's name, put back.
        a = self.make_read("a.jpg")
        for name in ("  Lead Space  ", "\tTabbed\t", "   "):
            with self.subTest(name=name):
                text = bake.region_text({"RegionList": [{"Type": "Face", "Name": name}]})
                with field_values.session(EXIFTOOL) as et:
                    field_values.write(et, a, {fields.REGION_INFO: [text]})
                    self.assertEqual({fields.REGION_INFO: [text]}, field_values.read_one(et, a, [fields.REGION_INFO]))
                self.assertEqual(name, self.regions_of(a)["XMP:RegionInfo"]["RegionList"][0]["Name"])


if __name__ == "__main__":
    unittest.main()
