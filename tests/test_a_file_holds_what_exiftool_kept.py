"""A file keeps what ExifTool keeps of a value, and the journal, the row and a settling after a crash say what the FILE holds
(scenario F1, tagpup.services.file_changes, tagpup.core.fields.as_read).

ExifTool does not always keep a value as written: IPTC:Keywords is cut at 64 bytes, a letter outside Latin-1 comes back as "?",
a caption "1.50" is answered as the number 1.5 and "true" as a boolean. A journal that compared the value asked for with the
file's would call a file written before a crash a conflict, record it done holding what it was never to hold (so that undo
refused it), plan the same caption again on every edit, and count a write that changed nothing as a change.

Real ExifTool, on JPEGs made here, in a home of the test's own; rows as the indexer stores them (photo_rows.add_read); a crash
is a BaseException at a step of the write (file_changes.STEPS). A keyword cut or turned to "?" is IPTC's, a copy only
ExifTool's rewrite writes since the own writer patches a keyword save into XMP alone (fields.XMP_ONLY, 4d): a test of
the cut forces the rewrite (`rewritten`, field_values.PATCH_IN_PLACE off), as a save gets it when the own writer
refuses the photo or its change does not fit; a caption read as a number or a boolean is the patch's as well.

Lifted: test_a_number_compares_as_read (#264), test_a_boolean_compares_as_read (#264 #278), test_settling_compares_as_read
(#277), test_a_write_records_what_the_file_holds (#273), test_a_crash_reads_back_what_it_wrote (#273 #286),
test_changed_counts_what_changed (#276), test_a_cut_keyword_is_not_planned_again (#278; its test of
vocabulary.extract_tags alone is the XMP-first rule, tests/test_tag_writes_keep_the_index_true.py's XmpIsTheCopyRead). Mutations, each in a scratch copy of the code, each failing the test named:
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
import io
import json
import os
import shutil
import stat as file_stat
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_file_journal import EXIFTOOL, Crash, FilesCase, crash_at, keywords_of, write_outside  # noqa: E402

import baked_files  # noqa: E402
import photo_rows  # noqa: E402
import page_writes  # noqa: E402
from tagpup.core import fields, orientation  # noqa: E402
from tagpup.files import bake, containers, exiftool_session, field_values, lock_owners  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import file_changes, tagging  # noqa: E402
from tagpup.store import db, file_journal  # noqa: E402

#: 98 bytes, longer than the 64 IPTC:Keywords keeps.
LONG = "Places/" + "Harbour" * 13
#: A letter IPTC's default character set (Latin) does not hold.
L_STROKE = chr(0x141)


def rewritten():
    """Every write ExifTool's rewrite, as a save is that the own writer refuses (a photo with no XMP yet, an XMP packet in
    its IFD0, #3108) or whose change does not fit its segments: the only writer of IPTC's copy of a keyword added."""
    return mock.patch.object(field_values, "PATCH_IN_PLACE", False)


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
            result = tagging.save_photo(self.library, path, caption, ["Beach"], None, EXIFTOOL, "",
                                        base=page_writes.base(self.library, path))
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
        with rewritten(), mock.patch.object(field_values, "_read", wraps=field_values._read) as read:
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

        # The undo is a save like another: the own writer's patch of the XMP alone, IPTC's cut copy left as it was
        # (fields.XMP_ONLY), which no reader of the tags takes while XMP holds a keyword.
        undone = self.undo(change)
        self.assertEqual((3, []), (undone.changed, undone.errors))
        for path in photos:
            self.assertEqual((["Beach"], ["Beach"], sorted(["Beach", cut])),
                             (keywords_of(path), self.indexed(path), self.iptc_of(path)))

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
        # The own writer's patch of the XMP alone: the journal's after is what it left (fields.as_patched).
        self.assertEqual(["Beach", LONG], sorted(after["XMP:Subject"]))
        self.assertEqual(["Beach"], sorted(after["IPTC:Keywords"]))
        undone = self.undo(change)
        self.assertEqual(2, undone.changed)
        self.assertIn("photo %d" % self.photo_id(first), [what for what, _why in undone.errors])
        self.assertEqual(["Written Elsewhere"], keywords_of(first))

    def test_a_crash_after_the_write_settles_done_whatever_the_file_kept(self):
        # (what is written, what a read of the file gives for it in the journal, and in which field, whether it is the
        # rewrite's: a keyword's IPTC copy is; a caption read as a number or a boolean is the own writer's patch)
        cases = {
            "a long keyword": (lambda a: self.add([a], [LONG]), "IPTC:Keywords", LONG[:64], True),
            "a letter IPTC cannot hold": (lambda a: self.add([a], ["Places/" + L_STROKE + "odz"]), "IPTC:Keywords",
                                          "Places/?odz", True),
            "a number": (lambda a: self.caption([a], "1.50"), "XMP:Title", "1.5", False),
            "a boolean": (lambda a: self.caption([a], "true"), "XMP:Title", "True", False),
        }
        for label, (write, field, kept, rewrite) in cases.items():
            with self.subTest(label):
                a = self.make_read(label.replace(" ", "_") + ".jpg")
                with mock.patch.object(field_values, "PATCH_IN_PLACE", not rewrite), \
                        mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written")):
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

    def test_a_keyword_iptc_cut_is_no_tag_of_its_own_and_no_later_edit_writes_it_again(self):
        # The cut copy was read as a tag of its own, so every later edit planned it into every field and wrote again a
        # file already holding what the edit asked for (#278). The cut is the rewrite's (IPTC's copy).
        a = self.make_read("a.jpg")
        with rewritten():
            self.assertEqual(1, self.add([a], [LONG]).changed)
        self.assertEqual(["Beach", LONG], self.indexed(a))
        written = os.stat(a).st_mtime_ns
        again = self.add([a], ["Beach"])
        self.assertEqual((0, None), (again.changed, again.details["change"]))
        self.assertEqual(written, os.stat(a).st_mtime_ns)
        self.add([a], ["Relay"])
        self.assertEqual((["Beach", LONG, "Relay"],) * 2, (keywords_of(a), self.indexed(a)))

    def test_a_change_stopped_part_way_reads_back_the_files_it_recorded(self):
        photos = [self.make_read(name) for name in ("a.jpg", "b.jpg", "c.jpg")]
        with rewritten(), mock.patch.object(file_changes, "_reached", side_effect=crash_at("file recorded", nth=2)):
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
        real = field_values._write

        def write(et, path, values, **options):
            # field_values.write's answer: the fields it left as they were, none for one that "wrote" all.
            return real(et, path, values, **options) if path != a else ()

        with mock.patch.object(field_values, "_write", side_effect=write):
            result = self.add([a, b])
        change = result.details["change"]
        self.assertEqual(1, result.changed)
        self.assertEqual([a], [what for what, _why in result.errors])
        self.assertNotIn(a, result.details["written"])
        self.assertEqual(["Beach"], keywords_of(a))
        self.assertEqual(["done"], self.states(change), "the file that did not change is still in the change")
        self.assertEqual("applied", self.status(change))

    def test_nothing_changed_leaves_no_change(self):
        a = self.make_read("a.jpg")
        last = self.last_change()
        with mock.patch.object(field_values, "_write"):
            result = self.add([a])
        self.assertEqual((0, 1), (result.changed, len(result.errors)))
        self.assertEqual((None, last), (result.details["change"], self.last_change()), "History lists no change that wrote nothing")


#: Another program's MWG region (a name holding the serialised form's special characters) and its Microsoft Photo one.
FOREIGN_MWG = ("-XMP-mwg-rs:RegionInfo={AppliedToDimensions={W=16,H=12,Unit=pixel},RegionList=[{Type=Face,"
               "Name=Ash|, Corrin |{x|}||y,Area={X=0.5,Y=0.5,W=0.25,H=0.25,Unit=normalized}}]}")
FOREIGN_MP = "-XMP-MP:RegionInfoMP={Regions=[{PersonDisplayName=Ash Corrin,Rectangle=0.1|, 0.2|, 0.3|, 0.4}]}"

#: Apple Photos' regions, the one foreign shape the live libraries hold: no name, an Area's numbers 17 digits long (which
#: ExifTool answers as texts), and Apple's face Extensions.
APPLE = ("-XMP-mwg-rs:RegionInfo={AppliedToDimensions={W=4032,H=3024,Unit=pixel},RegionList=[%s]}" % ",".join(
    "{Type=Face,Area={X=%s,Y=%s,W=%s,H=%s,Unit=normalized},Extensions={XMP-apple-fi:AngleInfoRoll=%d,"
    "XMP-apple-fi:AngleInfoYaw=%d,XMP-apple-fi:ConfidenceLevel=%d,XMP-apple-fi:FaceID=%d,XMP-apple-fi:Timestamp=%d}}"
    % (x, y, w, h, roll, yaw, confidence, face, stamp) for x, y, w, h, roll, yaw, confidence, face, stamp in (
        ("0.38281250000000006", "0.41406249999999994", "0.12890624999999997", "0.17187500000000003", 270, 0, 553, 7,
         2147483647),
        ("0.71093750000000011", "0.30468749999999994", "0.09765625000000001", "0.13020833333333331", 0, 45, 812, 12,
         1617181920))))

#: An XMP block holding only MWG regions: `%s`, the RDF of one region (seeded whole: ExifTool writes no field of a namespace
#: it does not know).
REGIONS_XMP = """<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>
<rdf:Description rdf:about='' xmlns:mwg-rs='http://www.metadataworkinggroup.com/schemas/regions/'
 xmlns:stArea='http://ns.adobe.com/xmp/sType/Area#' xmlns:nw='http://example.org/nowhere/'>
<mwg-rs:Regions rdf:parseType='Resource'><mwg-rs:RegionList><rdf:Bag><rdf:li rdf:parseType='Resource'>
<mwg-rs:Type>Face</mwg-rs:Type>%s
<mwg-rs:Area stArea:x='0.5' stArea:y='0.5' stArea:w='0.25' stArea:h='0.25' stArea:unit='normalized'/>
</rdf:li></rdf:Bag></mwg-rs:RegionList></mwg-rs:Regions></rdf:Description></rdf:RDF></x:xmpmeta>
<?xpacket end='w'?>"""


class BakeCase(KeptCase):
    """A bake written through the one writer, and what a file holds of it; no tests of its own."""

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
        return baked_files.held(path)

    def regions_of(self, path):
        with ExifToolSession(executable=EXIFTOOL) as et:
            found = et.get_tags([path], tags=["XMP:RegionInfo", "XMP:RegionInfoMP"], params=["-struct"])[0]
        return {key: value for key, value in found.items() if key != "SourceFile"}

    def outside(self, path, *args):
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.execute(*args, "-overwrite_original", path)

    def seed_regions(self, path, region):
        # Another program's regions, its XMP block written whole (REGIONS_XMP).
        seed = os.path.join(os.path.dirname(path), "seed.xmp")
        with open(seed, "w", encoding="utf-8", newline="") as handle:
            handle.write(REGIONS_XMP % region)
        self.outside(path, "-XMP<=" + seed)
        os.remove(seed)
        return self.regions_of(path)

    def assert_baked(self, path, values, faces):
        held = self.bake_of(path)
        self.assertIsInstance(held, bake.Bake, getattr(held, "reason", ""))
        self.assertEqual((baked_files.PHOTO_MODEL, baked_files.DIGEST, faces),
                         (held.photo_model, held.fingerprint, len(held.faces)))
        if faces:
            self.assertEqual([values[fields.REGION_INFO][0]], [bake.region_text(self.regions_of(path)["XMP:RegionInfo"])])


class ABakeIsWritten(BakeCase):
    """A bake written through the one writer, journaled, undone, settled after a crash (PHASE6_DESIGN, section 9)."""

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

        with mock.patch.object(field_values, "_write", wraps=field_values._write) as write:
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

    def test_a_tag_added_to_a_photo_whose_bake_spans_extended_xmp_is_rewritten_and_keeps_its_bake(self):
        # A photo holding an extended XMP segment is never the own writer's to patch (in_place._header_of refuses it):
        # a tag added, and its undo, are ExifTool's rewrite, which carries the extended XMP whole.
        a = self.make_read("a.jpg")
        values = self.values(60)
        baked = self.bake([a], values)
        self.assertEqual((1, []), (baked.changed, baked.errors))

        def extended():
            return containers.read(io.BytesIO(bytes_of(a))).of_kind("xmp-extension")
        self.assertTrue(extended(), "60 faces no longer span an extended XMP")
        with mock.patch.object(file_journal, "save_patch", wraps=file_journal.save_patch) as saved:
            added = self.add([a])
            self.assertEqual((1, [], 0), (added.changed, added.errors, saved.call_count), "patched, or not written")
            self.assertEqual(["Beach", "Harbour"], keywords_of(a))
            self.assertTrue(extended())
            self.assert_baked(a, values, 60)
            undone = self.undo(added.details["change"])
            self.assertEqual((1, [], 0), (undone.changed, undone.errors, saved.call_count))
        self.assertEqual(["Beach"], keywords_of(a))
        self.assertTrue(extended())
        self.assert_baked(a, values, 60)

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
                field_values._write(et, a, self.values(1))
        self.assertEqual((None, written), (self.last_change(), os.stat(a).st_mtime_ns))

    def test_a_structure_reads_back_as_the_text_written(self):
        a = self.make_read("a.jpg")
        written = {"AppliedToDimensions": {"W": 4000.0, "H": 3000, "Unit": "pixel"},
                   "RegionList": [{"Type": "Face", "Name": "Ash, Corrin {x}|y",
                                   "Area": {"X": 0.20833333333333334, "Y": 1 / 3, "W": 1e-05, "H": 0.5, "Unit": "normalized"}}]}
        text = bake.region_text(written)
        self.assertEqual(text, bake.region_text(dict(written, AppliedToDimensions={"W": 4000, "H": 3000.0, "Unit": "pixel"})))
        with field_values.opened(EXIFTOOL) as et:
            et.write(a, {fields.REGION_INFO: [text]})
            self.assertEqual({fields.REGION_INFO: [text]}, et.read_one(a, [fields.REGION_INFO]))
        area = self.regions_of(a)["XMP:RegionInfo"]["RegionList"][0]["Area"]
        self.assertEqual((0.20833333333333334, 1 / 3, 1e-05), tuple(bake._number(area[k], k) for k in "XYW"))

    def test_a_text_with_blanks_at_its_edges_reads_back_as_written(self):
        # ExifTool trims the blanks at a value's edges unless each is escaped (#1880): another program's name, put back.
        a = self.make_read("a.jpg")
        for name in ("  Lead Space  ", "\tTabbed\t", "   "):
            with self.subTest(name=name):
                text = bake.region_text({"RegionList": [{"Type": "Face", "Name": name}]})
                with field_values.opened(EXIFTOOL) as et:
                    et.write(a, {fields.REGION_INFO: [text]})
                    self.assertEqual({fields.REGION_INFO: [text]}, et.read_one(a, [fields.REGION_INFO]))
                self.assertEqual(name, self.regions_of(a)["XMP:RegionInfo"]["RegionList"][0]["Name"])

    def test_a_region_field_exiftool_does_not_know_fails_the_write_and_writes_no_byte_of_the_photo(self):
        # ExifTool writes the rest of the structure, warns, and succeeds: a write recorded it as made (#1881). Its warning
        # comes before the photo's first byte, of the header's copy a patch writes or of the new file a rewrite writes
        # beside it: the write is refused there, the photo as it was and the bake withdrawn, whichever writes it.
        text = bake.region_text({"RegionList": [{"Type": "Face", "Bogus": "1"}]})
        for patched in (True, False):
            with self.subTest(patched=patched), mock.patch.object(field_values, "PATCH_IN_PLACE", patched):
                a = self.make_read("patched.jpg" if patched else "rewritten.jpg")
                before = bytes_of(a)
                result = file_changes.write_fields(self.library, "bake", EXIFTOOL, [a], [fields.REGION_INFO],
                                                   lambda path, held: file_changes.Plan(after={fields.REGION_INFO: [text]}))
                self.assertEqual(0, result.changed)
                self.assertIn("is not a field of", str(result.errors))
                self.assertEqual([], self.states(result.details["change"]), "a bake that wrote nothing is kept")
                self.assertEqual(before, bytes_of(a), "the photo was written")
                self.assertNotIn(os.path.basename(exiftool_session.scratch_of(a)), os.listdir(os.path.dirname(a)))

    def test_a_photo_whose_xmp_segment_is_all_zero_bytes_fails_the_write_not_recorded_as_written(self):
        # ExifTool warns "Invalid XMP", exits 0 and leaves the file as it was (#2349).
        a = self.make_read("a.jpg")
        self.outside(a, "-XMP-dc:Subject=seed")
        with open(a, "rb") as handle:
            data = bytearray(handle.read())
        header = b"http://ns.adobe.com/xap/1.0/\x00"
        start = data.index(header) + len(header)
        length = int.from_bytes(data[data.index(header) - 2:data.index(header)], "big") - 2 - len(header)
        data[start:start + length] = bytes(length)
        with open(a, "wb") as handle:
            handle.write(data)
        before = bytes(data)
        text = bake.region_text({"RegionList": [{"Type": "Face", "Name": "Face"}]})
        result = file_changes.write_fields(self.library, "bake", EXIFTOOL, [a], [fields.REGION_INFO],
                                           lambda path, held: file_changes.Plan(after={fields.REGION_INFO: [text]}))
        self.assertEqual(0, result.changed)
        self.assertIn("Invalid XMP", str(result.errors))
        with open(a, "rb") as handle:
            self.assertEqual(before, handle.read())

    def test_an_undo_putting_back_regions_in_a_namespace_exiftool_cannot_write_is_a_conflict_not_undone(self):
        a = self.make_read("a.jpg")
        before = self.seed_regions(a, "<mwg-rs:Extensions rdf:parseType='Resource'><nw:Thing>1</nw:Thing></mwg-rs:Extensions>")
        self.assertEqual({"XMP-nw:Thing": 1}, before["XMP:RegionInfo"]["RegionList"][0]["Extensions"])
        change = self.bake([a], self.values(2)).details["change"]
        undone = self.undo(change)
        self.assertEqual(0, undone.changed)
        self.assertIn("is not a writable XMP tag", str(undone.errors))
        self.assertEqual(["conflict"], self.states(change))

    def test_a_session_whose_configuration_was_gone_when_it_started_again_fails_the_write(self):
        # The temp folder's copy removed (a cleaner) while a session was open, whose process then started again -- as
        # pyexiftool starts it after a command that timed out: ExifTool runs without it, and warns of each of TagPup's
        # fields that it is not defined, writing the rest (#1881).
        a = self.make_read("a.jpg")
        config = os.path.join(os.path.dirname(a), "bake.config")
        shutil.copy(bake.config_path(), config)
        with mock.patch.object(bake, "config_path", return_value=config):
            with ExifToolSession(executable=EXIFTOOL, config_file=bake.config_path()) as et:
                os.remove(config)
                et.terminate()
                field_values._read_one(et, a, [fields.REGION_INFO])
                with self.assertRaisesRegex(RuntimeError, "is not defined"):
                    field_values._write(et, a, self.values(1))

    def test_apple_photos_regions_are_replaced_by_a_bake_and_put_back_equal(self):
        a = self.make_read("a.jpg")
        self.outside(a, APPLE)
        before = self.regions_of(a)
        listed = before["XMP:RegionInfo"]["RegionList"]
        self.assertEqual(("0.38281250000000006", 2147483647),
                         (listed[0]["Area"]["X"], listed[0]["Extensions"]["XMP-apple-fi:Timestamp"]), "not Apple's shape")
        values = self.values(2)
        change = self.bake([a], values).details["change"]
        self.assert_baked(a, values, 2)
        undone = self.undo(change)
        self.assertEqual((1, []), (undone.changed, undone.errors))
        self.assertEqual(["undone"], self.states(change))
        self.assertEqual(before, self.regions_of(a))


def picture_of(data):
    """The bytes of a JPEG (`data`) from its first scan to its end: its picture, which no write may change (#2377)."""
    return data[containers.read(io.BytesIO(data)).image_offset:]


def bytes_of(path):
    with open(path, "rb") as handle:
        return handle.read()


def flip_a_picture_byte(path):
    """What the PC that wrote the owner's photos did to some of them: a byte of the picture changed after a write."""
    with open(path, "r+b") as handle:
        start = containers.read(handle).image_offset
        size = handle.seek(0, 2)
        at = start + (size - start) // 2
        handle.seek(at)
        byte = handle.read(1)[0]
        handle.seek(at)
        handle.write(bytes([byte ^ 0x01]))


class APhotoIsWrittenBesideItselfAndMovedOverIt(KeptCase):
    """ExifTool writes the new file of a photo beside it; the write compares its picture with the photo's and only then
    moves it over the photo (tagpup.files.field_values._landed; #2377, DECISIONS 2026-11-03). A new file whose picture
    changed is removed, the photo untouched, and the write made once more; twice is the photo's error.

    Mutations, each run and each failing the tests named: _landed putting the new file over the photo before comparing
    -> test_a_picture_changed_once, test_a_picture_changed_twice, test_the_new_file_is_moved and test_a_move_that_fails;
    the compare always equal (`same = True`) -> the first two; `-o` dropped from both commands of field_values.write ->
    every test here. On the code before the check (551689f4) every test here fails: four for want of the step "scratch
    written" (no write was ever beside its photo), and test_a_move_that_fails because the photo was written in place.
    A move that fails removes the write's own new file and names no path: on the branch's first cut, which left it and
    raised the OS's error, test_a_move_that_fails and test_a_read_only_photo fail.

    Every write here is ExifTool's rewrite (`rewritten`): a save's whenever the own writer refuses the photo or its change
    does not fit; the own writer's patch never writes a new file beside the photo (test_a_photo_patched_in_place).
    """

    def setUp(self):
        super().setUp()
        forced = rewritten()
        forced.start()
        self.addCleanup(forced.stop)
        self.a = self.make_read("a.jpg")
        self.scratch = exiftool_session.scratch_of(self.a)
        self.before = bytes_of(self.a)
        self.stamped = self.rows("SELECT mtime, size, raw_metadata FROM photos WHERE path = ?", (self.a,))[0]

    def flipping(self, times):
        """A stand-in for file_changes._reached that flips a picture byte of the new file the first `times` times it is
        written, and keeps what the photo held each time it was (to show it was not touched before the compare)."""
        seen = self.seen = []

        def reached(step):
            if step == file_changes.SCRATCH_WRITTEN:
                seen.append(bytes_of(self.a))
                if len(seen) <= times:
                    flip_a_picture_byte(self.scratch)
        return mock.patch.object(file_changes, "_reached", side_effect=reached)

    def row_of_a(self):
        return self.rows("SELECT mtime, size, raw_metadata FROM photos WHERE path = ?", (self.a,))[0]

    def test_a_picture_changed_once_is_written_again(self):
        with self.flipping(1), self.assertLogs(file_changes.logger, "WARNING") as logged:
            result = self.add([self.a])
        self.assertEqual((1, []), (result.changed, result.errors))
        self.assertEqual(1, result.details["pictures_refused"])
        self.assertTrue(any("picture's bytes" in line for line in logged.output), logged.output)
        # The photo was the photo before each compare: the damaged new file never reached it.
        self.assertEqual([self.before, self.before], self.seen)
        self.assertEqual(picture_of(self.before), picture_of(bytes_of(self.a)))
        self.assertEqual(["Beach", "Harbour"], keywords_of(self.a))
        self.assertFalse(os.path.exists(self.scratch))
        self.assertEqual(["done"], self.states(result.details["change"]))
        mtime, size, _raw = self.row_of_a()
        stat = os.stat(self.a)
        self.assertEqual((stat.st_size, True), (size, abs(stat.st_mtime - mtime) < 0.001), "stamped from the replaced file")

    def test_a_picture_changed_twice_is_the_photos_error_and_the_file_is_as_it_was(self):
        with self.flipping(2), self.assertLogs(file_changes.logger, "WARNING"):
            result = self.add([self.a])
        self.assertEqual(0, result.changed)
        self.assertEqual([(self.a, file_journal.PICTURE_CHANGED)],
                         [(what, str(why)) for what, why in result.errors])
        self.assertEqual(self.before, bytes_of(self.a))
        self.assertFalse(os.path.exists(self.scratch))
        self.assertEqual(self.stamped, self.row_of_a(), "the row is not stamped from a write that did not land")
        self.assertEqual([], self.states(result.details["change"]))

    def test_an_identity_minted_into_a_new_file_whose_picture_changed_is_minted_once_more(self):
        """The indexer's mint of a photo's identity (field_values.ensure_document_id) is a write like a save's: a new file
        whose picture changed is removed and the mint made once more (#2476's decision), not swallowed with the photo
        indexed without one this time."""
        real, made = field_values._command, []

        def command(et, source, target, params, clear):
            real(et, source, target, params, clear)
            made.append(target)
            if len(made) == 1:
                flip_a_picture_byte(target)

        with ExifToolSession(executable=EXIFTOOL) as et, \
                mock.patch.object(field_values, "_command", side_effect=command), \
                self.assertLogs(field_values.logger, "WARNING") as logged:
            minted = field_values.ensure_document_id(et, self.a)
            held = fields.read_document_id(et.get_metadata([self.a])[0])
        self.assertIsNotNone(minted)
        self.assertEqual((2, minted), (len(made), held))
        self.assertTrue(any("picture's bytes" in line for line in logged.output), logged.output)
        self.assertEqual(picture_of(self.before), picture_of(bytes_of(self.a)))
        self.assertFalse(os.path.exists(self.scratch))

    def test_a_crash_once_the_new_file_is_written_leaves_the_photo_and_a_settle_writes_it(self):
        # The photo older than the write, as on a share: the scratch file's age, not the photo's, says no write is under way.
        long_ago = os.stat(self.a).st_mtime - 3 * 86400
        os.utime(self.a, (long_ago, long_ago))
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at(file_changes.SCRATCH_WRITTEN)):
            with self.assertRaises(Crash):
                self.add([self.a])
        change = self.last_change()
        self.assertEqual(self.before, bytes_of(self.a))
        self.assertTrue(os.path.exists(self.scratch), "the new file is left beside the photo")
        self.assertEqual(["writing"], self.states(change))
        stale = long_ago + 60
        self.assertLess(stale, os.stat(self.scratch).st_mtime - file_changes.SCRATCH_STALE)
        os.utime(self.scratch, (stale, stale))
        self.assertEqual(1, self.settle())
        self.assertEqual(("applied", ["done"]), (self.status(change), self.states(change)))
        self.assertEqual(["Beach", "Harbour"], keywords_of(self.a))
        self.assertEqual(picture_of(self.before), picture_of(bytes_of(self.a)))
        self.assertFalse(os.path.exists(self.scratch))

    def test_the_new_file_is_moved_over_the_photo_never_copied(self):
        # The owner's requirement (2026-11-03): the photo is replaced by a move of the checked file, never a copy of it.
        moved, ids = [], []
        real_replace = os.replace

        def replace(source, destination):
            moved.append((source, destination))
            real_replace(source, destination)

        def reached(step):
            if step == file_changes.SCRATCH_WRITTEN:
                ids.append(os.stat(self.scratch).st_ino)

        def no_copy(*args, **kwargs):
            raise AssertionError("a photo's write copied a file: %r" % (args,))

        with mock.patch.object(file_changes, "_reached", side_effect=reached), \
                mock.patch.object(os, "replace", side_effect=replace), \
                mock.patch.object(shutil, "copy", side_effect=no_copy), \
                mock.patch.object(shutil, "copyfile", side_effect=no_copy), \
                mock.patch.object(shutil, "copy2", side_effect=no_copy):
            result = self.add([self.a])
        self.assertEqual((1, []), (result.changed, result.errors))
        self.assertEqual(["Beach", "Harbour"], keywords_of(self.a))
        self.assertIn((self.scratch, self.a), moved)
        self.assertEqual(ids, [os.stat(self.a).st_ino], "the photo is the very file ExifTool wrote beside it")

    def test_a_move_that_fails_leaves_the_photo_as_it_was_and_removes_its_new_file(self):
        real_replace = os.replace

        def replace(source, destination):
            if destination == self.a:
                # Words lock_owners.HELD_WORDS does not know: the holder is named only if the error is still an OSError.
                raise PermissionError(13, "Refused by the stand-in", None, 32)
            real_replace(source, destination)

        viewer = [{"name": "Viewer.exe", "pid": 4242}]
        with mock.patch.object(os, "replace", side_effect=replace), \
                mock.patch.object(lock_owners, "holders", return_value=viewer):
            result = self.add([self.a])
        self.assertEqual(0, result.changed)
        self.assertEqual([self.a], [what for what, _why in result.errors])
        self.assertIn("It is held open by Viewer.exe (pid 4242).", str(result.errors[0][1]), "the OS's error was not kept")
        self.assertIn("the new file was removed and the photo is as it was", str(result.errors[0][1]))
        self.assertEqual(self.before, bytes_of(self.a))
        self.assertFalse(os.path.exists(self.scratch), "the write's own new file is left beside the photo")
        self.assertEqual([], self.states(result.details["change"]))

    def test_a_read_only_photo_is_an_error_naming_no_path_and_the_next_write_fails_the_same(self):
        """A photo marked read-only (copied off a disc) in a folder that is not: ExifTool writes its new file beside it,
        and the move over the photo is refused. The error names no path (a photo is named by its id); the new file is
        removed, so the next write is not told a write may be under way, but fails the same way; made writable, it takes
        the tag."""
        os.chmod(self.a, file_stat.S_IREAD)
        self.addCleanup(os.chmod, self.a, file_stat.S_IREAD | file_stat.S_IWRITE)
        for attempt in (1, 2):
            with self.subTest(attempt=attempt):
                result = self.add([self.a])
                self.assertEqual((0, [self.a]), (result.changed, [what for what, _why in result.errors]))
                said = str(result.errors[0][1])
                self.assertIn("the new file was removed and the photo is as it was", said)
                for named in (self.folder, os.path.basename(self.a), "exiftool_tmp"):
                    self.assertNotIn(named, said, "the error names a path")
                self.assertEqual(self.before, bytes_of(self.a))
                self.assertFalse(os.path.exists(self.scratch), "the write's own new file is left beside the photo")
                self.assertEqual(self.stamped, self.row_of_a())
        os.chmod(self.a, file_stat.S_IREAD | file_stat.S_IWRITE)
        done = self.add([self.a])
        self.assertEqual((1, []), (done.changed, done.errors))
        self.assertEqual(["Beach", "Harbour"], keywords_of(self.a))


if __name__ == "__main__":
    unittest.main()
