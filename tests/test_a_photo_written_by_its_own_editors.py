"""A photo's fields saved by TagPup's own writer (field_values.PATCH_IN_PLACE: tagpup.files.segment_edits.new_header into
in_place.patch_jpeg, the one patch writer since 4d), in the flows the owner lives in (scenarios F1 and D3; docs/PLAN_OWN_METADATA.md, M4 and its hazards
a, b and c).

Three keywords added one at a time are three patches of the XMP alone, IPTC and EXIF left as they were and recorded so
(fields.as_patched); a save killed once its record is saved, or after its last byte, is settled at the next start as done
against what the patch leaves, never a conflict (#2434); a photo another process is patching is not written by this
one, and one this process is patching is not opened by another; a share that stops answering at the write leaves the
photo as it was and its record, and the next start writes it; a packet the XMP editor refuses (dc:subject written as an
attribute) and a photo holding two APP13 segments are ExifTool's rewrite; a person added to a photo whose keywords are in
IPTC alone gets every keyword in XMP; the last keyword taken off writes every copy (#2433); an undo puts back the bytes
the photo held; a rotation of a photo whose packet holds tiff:Orientation and whose EXIF holds an IFD1 Orientation reads
as ExifTool's write of the same reads (hazard c); a save repeated, or a tag the photo holds added again, is holding it
already as the patch leaves it (fields.as_patched of field_values.left_as_they_were): nothing written, nothing journaled,
`changed` 0, and the first save's undo puts back the bytes; the writer itself, given it, writes no byte and answers the
copies it left (hazard a). A photo holding a second XMP packet inside its EXIF (IFD0 tag 0x02BC, as Windows Live Photo Gallery wrote it;
tests/fixtures/xmp_in_ifd0.json, captured) has a tag added, its Date Taken moved, a turn and every keyword taken off, each
ExifTool's rewrite, which writes both packets, and read back as asked (#3108); the same photo with that entry under
another tag is patched in place.

Real JPEGs written by the real ExifTool in a home of the test's own (test_file_journal.FilesCase); nothing of the
owner's. Breaks that fail these, each tried on a copy of the code: segment_edits.stale_of answering () fails
test_three_keywords_added_are_three_patches_of_the_xmp_alone; dropping `row.stale = in_place.left_stale(saved[0])` in
file_changes._by_bytes fails test_killed_after_the_last_byte_it_is_done_as_the_patch_left_it; in_place._alone opening
with sharing fails test_two_saves_at_once_never_write_one_photo_together; field_values.write taking in_place.Away for a
rewrite fails test_a_share_that_stops_answering_at_the_write_is_written_at_the_next_start; xmp_packet reading a property
written as an attribute fails test_a_packet_the_editor_refuses_is_rewritten; tagging._tags_held reading XMP alone fails
test_a_person_added_to_a_photo_whose_keywords_are_in_iptc_alone; segment_edits.stale_of leaving a group whose XMP value
is empty fails test_the_last_keyword_taken_off_is_taken_from_every_copy; file_changes._carry keeping the target as the
journal's after (the read back then corrects it, warning) fails test_an_undo_puts_back_the_bytes_the_photo_held; in_place._header_of letting two APP13 segments
through fails test_a_photo_with_two_app13_segments_is_rewritten; fields.EDITED_AS without tiff:Orientation fails
test_a_turn_reads_as_exiftools_write_of_it_reads; file_changes._holds_already comparing against the plan's after
(the planner before the fix), or in_place.patch_jpeg answering () when no byte changes, fails
test_a_save_repeated_or_a_tag_held_added_again_writes_and_journals_nothing;
segment_edits.new_header patching a photo whose IFD0 holds an XMP packet (the code before #3108) fails
test_a_photo_holding_an_xmp_packet_in_its_exif_is_rewritten_and_reads_back_as_asked.
"""
import io
import json
import os
import shutil
import struct
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_rows  # noqa: E402
from test_a_crash_part_way_is_settled_at_the_next_start import start_again  # noqa: E402
from test_a_photo_patched_in_place import PRINT_FLAGS, away_at, open_elsewhere, read, saves_counted  # noqa: E402
from test_file_journal import (EXIFTOOL, Crash, FilesCase, crash_at, cut_patch, field_of, keywords_of,  # noqa: E402
                               requires_exiftool, write_outside)

import page_writes  # noqa: E402
from tagpup.core import fields, paths  # noqa: E402
from tagpup.files import containers, field_values, in_place, segment_edits, xmp_packet  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import batches, bulk_edit, file_changes, metadata_check, photos as photo_actions, tagging  # noqa: E402
from tagpup.store import db  # noqa: E402

#: The copies a keyword save patching the XMP alone leaves as they were, in the order segment_edits answers them.
KEYWORD_COPIES_LEFT = ("IPTC:Keywords", "EXIF:XPKeywords")
#: The header of a photo_index JPEG whose EXIF's IFD0 holds an XMP packet (0x02BC), captured, its values fictional.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "xmp_in_ifd0.json"),
          encoding="utf-8") as _f:
    XMP_IN_IFD0 = json.load(_f)
#: Its keywords, in both packets and EXIF XPKeywords.
IFD0_KEYWORDS = sorted(["People/Classmate/Robin Lark", "Holidays/Summer", "People/Classmate/Ellis Moor",
                        "People/Wendy Harlow", "Activities/Summer Camps", "People/Classmate/Juniper Cole"])


#: The Date Taken a camera photo of the tests holds.
TAKEN = "2019:06:01 10:00:00"


def whole_read(path):
    """Every tag ExifTool reads of the photo by its group, but the file system's (its stamp)."""
    with ExifToolSession(executable=EXIFTOOL) as et:
        found = et.get_metadata([path])[0]
    return {tag: value for tag, value in found.items() if not tag.startswith(("File:", "SourceFile"))}


def kinds_written(before, after):
    """The kinds of the segments of `before` whose bytes `after` (the same length) changed."""
    layout = containers.read(io.BytesIO(before))
    differ = [at for at in range(len(before)) if before[at] != after[at]]
    return sorted({next((each.kind for each in layout.segments if each.offset <= at < each.end), "outside")
                   for at in differ})


def photo_with_xmp_in_ifd0(path, tag=0x02BC):
    """A JPEG holding the captured header's EXIF, its IFD0's packet entry under `tag`, then its APP1 packet, as the photo
    held them (its ICC profile between the two left out), before a picture PIL makes."""
    from PIL import Image

    tiff = bytearray(XMP_IN_IFD0["tiff_size"])
    for at, hexed in XMP_IN_IFD0["tiff"]:
        tiff[at:at + len(hexed) // 2] = bytes.fromhex(hexed)
    packet, at = XMP_IN_IFD0["ifd0_packet"].encode("utf-8"), XMP_IN_IFD0["ifd0_packet_at"]
    tiff[at:at + len(packet)] = packet
    walked = containers.exif_tiff(bytes(tiff))
    ifd0 = walked.named["ifd0"]
    at = ifd0.offset + 2 + 12 * [entry[0] for entry in ifd0.entries].index(0x02BC)
    tiff[at:at + 2] = struct.pack(walked.order + "H", tag)
    xmp = XMP_IN_IFD0["xmp"].replace("<?xpacket end", " " * XMP_IN_IFD0["xmp_padding"] + "<?xpacket end", 1)
    picture = io.BytesIO()
    Image.new("RGB", (16, 12), (90, 110, 130)).save(picture, "JPEG")
    picture = picture.getvalue()
    jfif = 4 + int.from_bytes(picture[4:6], "big")

    def app1(body):
        return b"\xff\xe1" + (len(body) + 2).to_bytes(2, "big") + body
    with open(path, "wb") as handle:
        handle.write(picture[:jfif] + app1(containers.EXIF_HEAD + bytes(tiff))
                     + app1(containers.XMP_HEAD + xmp.encode("utf-8")) + picture[jfif:])
    return path


@requires_exiftool
class APhotoWrittenByItsOwnEditors(FilesCase):
    def seed(self, path, values):
        """The row of a photo the test made by hand, as the indexer leaves it."""
        conn = db.connect(self.library.path)
        try:
            photo_rows.add_read(conn, path, values)
            conn.commit()
        finally:
            conn.close()

    def raw(self, path, field):
        """The row's raw_metadata value of `field`, as the journal keeps a value (a list of texts; [] when none)."""
        raw = json.loads(self.rows("SELECT raw_metadata FROM photos WHERE path = ?", (path,))[0][0])
        return fields.field_values(raw.get(field))

    def after(self, change, field):
        return json.loads(self.rows("SELECT fields_after FROM change_files WHERE change_id = ?", (change,))[0][0])[field]

    def assert_patched_xmp_alone(self, path, keywords, change):
        """`path` holds `keywords` in XMP and in its row, its IPTC and EXIF copies 'Beach' as before, recorded so."""
        self.assertEqual((sorted(keywords), sorted(keywords)), (keywords_of(path), self.indexed(path)))
        self.assertEqual(("Beach", "Beach"), (field_of(path, "IPTC:Keywords"), field_of(path, "EXIF:XPKeywords")))
        self.assertEqual((["Beach"], ["Beach"]), (self.after(change, "IPTC:Keywords"), self.raw(path, "IPTC:Keywords")),
                         "the row or the journal says IPTC holds what the patch did not write")
        self.assertEqual(sorted(keywords), sorted(self.after(change, "XMP:Subject")), "the journal lacks what XMP holds")

    def test_three_keywords_added_are_three_patches_of_the_xmp_alone(self):
        a = self.make("a.jpg")
        original = read(a)
        with saves_counted() as saved:
            for tag in ("Harbour", "Regatta", "Pier"):
                result = self.add([a], (tag,))
                self.assertEqual((1, []), (result.changed, result.errors), result.message())
        self.assertEqual(3, saved.call_count, "a save was not a patch")
        self.assertEqual(["xmp"], kinds_written(original, read(a)), "a segment other than the XMP was written")
        self.assertEqual(["done"], self.states(result.details["change"]))
        self.assert_patched_xmp_alone(a, ["Beach", "Harbour", "Regatta", "Pier"], result.details["change"])

    def test_killed_once_its_record_is_saved_it_is_written_at_the_next_start(self):
        a = self.make("a.jpg")
        original = read(a)
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at(file_changes.PATCH_SAVED)):
            with self.assertRaises(Crash):
                self.add([a])
        change = self.last_change()
        self.assertEqual((["writing"], original), (self.states(change), read(a)))
        start_again(self.library)
        self.assertEqual(("applied", ["done"]), (self.status(change), self.states(change)))
        self.assert_patched_xmp_alone(a, ["Beach", "Harbour"], change)
        self.assertEqual([], self.rows("SELECT id FROM change_files WHERE patch IS NOT NULL"))

    def test_killed_after_the_last_byte_it_is_done_as_the_patch_left_it(self):
        # #2434: the file holds every range as written, so XMP holds the new keywords and IPTC the old: neither the
        # plan's before nor its after. The record's `stale` says which copies the patch left; read against
        # fields.as_patched the file is done, not a conflict, and is not written again.
        a = self.make("a.jpg")
        with cut_patch(), self.assertRaises(Crash):
            self.add([a])
        change = self.last_change()
        patched = read(a)
        self.assertEqual(["writing"], self.states(change))
        start_again(self.library)
        self.assertEqual(("applied", ["done"]), (self.status(change), self.states(change)))
        self.assertEqual(patched, read(a), "a file holding what the patch was to leave was written again")
        self.assert_patched_xmp_alone(a, ["Beach", "Harbour"], change)

    def test_two_saves_at_once_never_write_one_photo_together(self):
        # Two processes on one folder (the server and a CLI run, two libraries holding it): another's patch holds the
        # photo with no sharing, so this one writes nothing; and while this one patches, another cannot open it.
        a = self.make("a.jpg")
        original = read(a)
        with open_elsewhere(a, 0), saves_counted() as saved:
            result = self.add([a])
        self.assertEqual((0, 0, original), (result.changed, saved.call_count, read(a)))
        self.assertEqual(1, len(result.errors), result.message())
        opened = []

        def elsewhere(step):
            if step == file_changes.PATCH_SAVED:
                try:
                    with open_elsewhere(a, 0x7):
                        opened.append(True)
                except OSError:
                    opened.append(False)
        with mock.patch.object(file_changes, "_reached", side_effect=elsewhere):
            result = self.add([a])
        self.assertEqual(([False], 1), (opened, result.changed), "another program opened the photo mid-patch")
        self.assert_patched_xmp_alone(a, ["Beach", "Harbour"], result.details["change"])

    def test_a_share_that_stops_answering_at_the_write_is_written_at_the_next_start(self):
        a = self.make("a.jpg")
        original = read(a)
        with away_at(4), saves_counted() as saved:
            result = self.add([a])
        change = result.details["change"]
        self.assertEqual((0, 1, original), (result.changed, saved.call_count, read(a)))
        self.assertEqual(["writing"], self.states(change), "the record, the only way back, was let go")
        start_again(self.library)
        self.assertEqual(["done"], self.states(change))
        self.assert_patched_xmp_alone(a, ["Beach", "Harbour"], change)

    def test_a_packet_the_editor_refuses_is_rewritten(self):
        # dc:subject written as an attribute of rdf:Description: ExifTool reads it, the XMP editor refuses the packet
        # (docs/PLAN_OWN_METADATA.md, "XMP"), and the save is ExifTool's rewrite, every copy written.
        a = self.make("a.jpg", row=False)
        held = read(a)
        xmp = containers.read(io.BytesIO(held)).of_kind("xmp")[0]
        segment = held[xmp.offset:xmp.end]
        element = (b"'>\n  <dc:subject>\n   <rdf:Bag>\n    <rdf:li>Beach</rdf:li>\n   </rdf:Bag>\n  </dc:subject>\n"
                   b" </rdf:Description>")
        attribute = b"'\n  dc:subject='Beach'/>"
        self.assertIn(element, segment, "ExifTool lays the packet out otherwise now")
        segment = segment.replace(element, attribute)
        end = segment.rindex(b"<?xpacket end=")
        segment = segment[:end] + b" " * (len(element) - len(attribute)) + segment[end:]
        with open(a, "wb") as handle:
            handle.write(held[:xmp.offset] + segment + held[xmp.end:])
        self.assertIsInstance(xmp_packet.read(segment[4 + len(containers.XMP_HEAD):]), containers.Refusal)
        self.assertEqual("Beach", field_of(a, "XMP:Subject"))
        self.seed(a, {"XMP:Subject": ["Beach"], "IPTC:Keywords": ["Beach"], "EXIF:XPKeywords": "Beach"})
        answers = []
        real = segment_edits.new_header

        def new_header(*args):
            answers.append(real(*args))
            return answers[-1]
        with mock.patch.object(segment_edits, "new_header", new_header), saves_counted() as saved:
            result = self.add([a])
        self.assertEqual((1, [], 0, [None]), (result.changed, result.errors, saved.call_count, answers), result.message())
        self.assertEqual((["Beach", "Harbour"], ["Beach", "Harbour"], ["Beach", "Harbour"]),
                         (keywords_of(a), field_of(a, "IPTC:Keywords"), self.indexed(a)))

    def test_a_person_added_to_a_photo_whose_keywords_are_in_iptc_alone(self):
        # 151 photo_index rows hold IPTC keywords and no XMP:Subject (counted read-only, 2026-11-05). The planner
        # reads both copies, so XMP is given every keyword, not the person alone; IPTC keeps what it held.
        from PIL import Image

        a = os.path.join(self.folder, "a.jpg")
        Image.new("RGB", (16, 12), (90, 110, 130)).save(a, "JPEG")
        values = {"IPTC:Keywords": ["Beach"], "EXIF:XPKeywords": "Beach", "XMP-xmpMM:PreservedFileName": "a.jpg"}
        write_outside(a, values)
        self.seed(a, values)
        with saves_counted() as saved:
            result = tagging.change_tags(self.library, [a], ["People/Wren Calloway"], [], EXIFTOOL)
        self.assertEqual((1, [], 1), (result.changed, result.errors, saved.call_count), result.message())
        self.assert_patched_xmp_alone(a, ["Beach", "People/Wren Calloway"], result.details["change"])

    def test_the_last_keyword_taken_off_is_taken_from_every_copy(self):
        # #2433: XMP holding none, the reader would take IPTC's copy and the keyword would come back.
        for tag in ("Beach", "Harbour"):     # IPTC shrinks by 10 bytes (no pad fits: a rewrite), by 12 (a patch)
            with self.subTest(tag=tag):
                a = self.make("%s.jpg" % tag, tags=(tag,))
                with saves_counted() as saved:
                    result = tagging.change_tags(self.library, [a], [], [tag], EXIFTOOL)
                self.assertEqual((1, []), (result.changed, result.errors), result.message())
                self.assertEqual(([], None, None, []), (keywords_of(a), field_of(a, "IPTC:Keywords"),
                                                        field_of(a, "EXIF:XPKeywords"), self.indexed(a)))
                self.assertEqual(([], []), (self.raw(a, "IPTC:Keywords"),
                                              self.after(result.details["change"], "IPTC:Keywords")))
                self.assertEqual(tag == "Harbour", saved.call_count == 1, "patched or not, not as the fit says")

    def test_an_undo_puts_back_the_bytes_the_photo_held(self):
        # The write records the journal's after as the patch left the file (IPTC as it was) when it marks it done; the
        # read back finds nothing to correct (it warns when it does), and the undo starts from what the file holds.
        a = self.make("a.jpg")
        original = read(a)
        with self.assertNoLogs(file_changes.logger, "WARNING"):
            change = self.add([a]).details["change"]
        with saves_counted() as saved:
            undone = self.undo(change)
        self.assertEqual((1, [], 1), (undone.changed, undone.errors, saved.call_count), "the undo was not a patch")
        self.assertEqual((["undone"], ["Beach"]), (self.states(change), self.indexed(a)))
        self.assertEqual(original, read(a), "the undo did not put back the bytes the photo held")

    def upright(self, names, caption=None):
        """Photos as a camera leaves them, holding Beach, a Date Taken and an Orientation (1) (with `caption`, a
        caption), each row as the indexer leaves it: ExifTool's one command gives every one its Orientation."""
        made = [self.make(name, taken=TAKEN, caption=caption, row=False) for name in names]
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.execute("-IFD0:Orientation#=1", "-overwrite_original", *made)
        values = {"XMP:Subject": ["Beach"], "IPTC:Keywords": ["Beach"], "EXIF:XPKeywords": "Beach",
                  "EXIF:DateTimeOriginal": TAKEN}
        if caption:
            values["XMP:Description"] = caption
        for path in made:
            self.seed(path, values)
        return made

    def test_an_undo_of_a_caption_a_date_taken_shift_and_a_turn_puts_back_what_the_photo_held(self):
        # Undo writes the journal's before values through the same writer: a patch of each, every tag ExifTool reads
        # as it was and every byte outside the EXIF, IPTC and XMP segments the photo's own (the XMP editor may lay a
        # property it put back elsewhere in the packet).
        saves = {
            "caption": lambda a: tagging.save_photo(self.library, a, "Harbour at dusk", ["Beach"], None, EXIFTOOL, "",
                                                    base=page_writes.base(self.library, a)),
            "date taken": lambda a: photo_actions.shift_date_taken(self.library, [a], 90, EXIFTOOL),
            "turn": lambda a: photo_actions.rotate_photos(self.library, [a], "right", EXIFTOOL),
        }
        for label, save in saves.items():
            with self.subTest(label):
                a = self.upright([label.replace(" ", "_") + ".jpg"], caption="Harbour")[0]
                original, tags = read(a), whole_read(a)
                with saves_counted() as saved:
                    result = save(a)
                self.assertEqual((1, [], 1), (result.changed, result.errors, saved.call_count), result.message())
                self.assertNotEqual(tags, whole_read(a))
                change = self.last_change()
                with saves_counted() as saved:
                    undone = self.undo(change)
                self.assertEqual((1, [], 1), (undone.changed, undone.errors, saved.call_count), "the undo was not a patch")
                self.assertEqual(["undone"], self.states(change))
                self.assertEqual(tags, whole_read(a), "the undo did not put back what the photo held")
                self.assertEqual([], [kind for kind in kinds_written(original, read(a))
                                      if kind not in in_place.PATCHED_KINDS], "a byte outside them was written")

    def test_a_bulk_tag_add_a_time_shift_and_a_turn_over_more_than_a_chunk_are_patches_its_rows_record(self):
        # The selection's bulk edit (bulk_edit: one change, written a chunk of batches.CHUNK at a time), Time Shift and a
        # turn, each over more photos than a chunk: every photo the own writer's patch, given the journal's `saving`,
        # and its row recording what the file holds (photos.follow_fields, in the transaction that marks it done).
        made = self.upright(["p%02d.jpg" % n for n in range(batches.CHUNK + 1)])
        ids = [self.photo_id(path) for path in made]
        operations = (
            ("bulk tags", lambda: bulk_edit.run(self.library, bulk_edit.prepare(self.library, "tags",
                                                                                {"add": ["Trips/Coast"]}), ids, EXIFTOOL)),
            ("time shift", lambda: photo_actions.shift_date_taken(self.library, made, 90, EXIFTOOL)),
            ("rotate", lambda: photo_actions.rotate_photos(self.library, made, "right", EXIFTOOL)),
        )
        for label, operation in operations:
            with self.subTest(label), saves_counted() as saved, \
                    mock.patch.object(field_values, "write", wraps=field_values.write) as wrote:
                operation()
                change = self.last_change()
                self.assertEqual((len(made), len(made)), (saved.call_count, wrote.call_count), "not every photo a patch")
                self.assertTrue(all(call.kwargs.get("saving") for call in wrote.call_args_list))
                self.assertEqual(["done"] * len(made), self.states(change))
                recorded = self.rows("SELECT path, fields_after FROM change_files WHERE change_id = ?", (change,))
                with ExifToolSession(executable=EXIFTOOL) as et:
                    for path, after in recorded:
                        after = json.loads(after)
                        held = field_values.read_one(et, path, list(after))
                        raw, mtime, size = self.rows("SELECT raw_metadata, mtime, size FROM photos WHERE path = ?",
                                                     (path,))[0]
                        # The row keeps the fields the scan reads (fields.scan_reads), and the file's stamp.
                        scanned = {field: value for field, value in after.items()
                                   if fields.scan_reads(fields.read_key(field))}
                        stat = os.stat(path)
                        self.assertTrue(fields.reads_same(held, after), "the journal's after is not what the file holds")
                        self.assertTrue(fields.reads_same(json.loads(raw), scanned), "the row is not what the file holds")
                        self.assertEqual((stat.st_size, True), (size, abs(stat.st_mtime - mtime) < 0.001),
                                         "the row is not stamped from the file written")
        self.assertEqual(["Beach", "Trips/Coast"], keywords_of(made[-1]))
        self.assertEqual("Beach", field_of(made[-1], "IPTC:Keywords"), "a tag added patches the XMP alone")

    def test_a_photo_with_two_app13_segments_is_rewritten(self):
        # Photoshop splits its resources over two APP13 segments (0 / 2 / 3 photos in the census, docs/domains/files.md):
        # in_place._header_of refuses the photo before any writer is asked.
        a = self.make("a.jpg", row=False)
        held = read(a)
        iptc = containers.read(io.BytesIO(held)).of_kind("iptc")[0]
        body = b"Photoshop 3.0\0" + PRINT_FLAGS
        second = b"\xff\xed" + (len(body) + 2).to_bytes(2, "big") + body
        with open(a, "wb") as handle:
            handle.write(held[:iptc.end] + second + held[iptc.end:])
        self.assertEqual(2, len(containers.read(io.BytesIO(read(a))).of_kind("iptc")))
        self.seed(a, {"XMP:Subject": ["Beach"], "IPTC:Keywords": ["Beach"], "EXIF:XPKeywords": "Beach"})
        with mock.patch.object(segment_edits, "new_header", wraps=segment_edits.new_header) as asked, \
                saves_counted() as saved:
            result = self.add([a])
        self.assertEqual((1, [], 0, 0), (result.changed, result.errors, saved.call_count, asked.call_count),
                         result.message())
        self.assertEqual((["Beach", "Harbour"], ["Beach", "Harbour"], ["Beach", "Harbour"]),
                         (keywords_of(a), field_of(a, "IPTC:Keywords"), self.indexed(a)))

    def test_a_turn_reads_as_exiftools_write_of_it_reads(self):
        # Hazard c: a packet holding tiff:Orientation and an EXIF whose thumbnail (IFD1) has an Orientation of its own.
        # The own writer's turn and ExifTool's write of "Orientation" to a copy of the same photo read alike.
        from PIL import Image

        a = self.make("a.jpg", row=False)
        thumb = os.path.join(self.home.root, "thumb.jpg")
        Image.new("RGB", (8, 6), (20, 40, 60)).save(thumb, "JPEG")
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.execute("-ThumbnailImage<=" + thumb, "-IFD0:Orientation#=1", "-XMP-tiff:Orientation#=1",
                       "-overwrite_original", a)
            et.execute("-IFD1:Orientation#=1", "-overwrite_original", a)
        self.seed(a, {"XMP:Subject": ["Beach"], "IPTC:Keywords": ["Beach"], "EXIF:XPKeywords": "Beach"})
        theirs = os.path.join(self.home.root, "ExifTool's", "a.jpg")
        os.makedirs(os.path.dirname(theirs))
        shutil.copyfile(a, theirs)

        def orientations(path):
            # By the group each copy is in (-G1: IFD0, IFD1, XMP-tiff); the session's own -G would name two EXIF.
            with ExifToolSession(executable=EXIFTOOL, common_args=["-G1", "-n"]) as et:
                found = json.loads(et.execute("-j", "-a", "-Orientation", path))[0]
            return {key: value for key, value in found.items() if key != "SourceFile"}
        before = orientations(a)
        self.assertEqual({"IFD0:Orientation", "IFD1:Orientation", "XMP-tiff:Orientation"}, set(before))
        with saves_counted() as saved:
            result = photo_actions.rotate_photos(self.library, [a], "right", EXIFTOOL)
        self.assertEqual((1, [], 1), (result.changed, result.errors, saved.call_count), result.message())
        turned = json.loads(self.rows("SELECT fields_after FROM change_files")[0][0])[fields.ORIENTATION]
        with ExifToolSession(executable=EXIFTOOL) as et:
            field_values.write(et, theirs, {fields.ORIENTATION: turned})
        self.assertEqual(orientations(theirs), orientations(a))
        # Both turn the picture's copies and leave the thumbnail's own (ExifTool 13.59).
        self.assertEqual({"IFD0:Orientation": 6, "IFD1:Orientation": 1, "XMP-tiff:Orientation": 6}, orientations(a))

    def test_a_save_repeated_or_a_tag_held_added_again_writes_and_journals_nothing(self):
        # The photo panel's Save of the same keywords again, and Add to all selected of a tag the photo has. The file
        # holds them in XMP, IPTC the old set, as the first save's patch left it: holding it already, as a write would
        # leave it. It was planned, journaled and reported as 1 changed with no byte written, a History entry that
        # changed nothing.
        a = self.make("a.jpg")
        original = read(a)
        first = tagging.save_photo(self.library, a, "", ["Beach", "Harbour"], None, EXIFTOOL, "",
                                   base=page_writes.base(self.library, a))
        self.assertEqual((1, []), (first.changed, first.errors), first.message())
        change, held = first.details["change"], read(a)
        with mock.patch.object(field_values, "write", wraps=field_values.write) as wrote, saves_counted() as saved:
            again = tagging.save_photo(self.library, a, "", ["Beach", "Harbour"], None, EXIFTOOL, "",
                                       base=first.details["base"])   # the page's next base: the first save's reply
            tagged = self.add([a])
        self.assertEqual(((0, [], None), (0, [], None)),
                         ((again.changed, again.errors, again.details["change"]),
                          (tagged.changed, tagged.errors, tagged.details["change"])), again.message())
        self.assertEqual((0, 0, held), (wrote.call_count, saved.call_count, read(a)), "a photo holding it was written")
        self.assertEqual(change, self.last_change(), "a save that changed nothing is in History")
        self.assert_patched_xmp_alone(a, ["Beach", "Harbour"], change)
        # Hazard a, at the writer: given the same values, it has no byte to write and answers the copies it left.
        flat, hierarchical = fields.expand_tag_fields(["Beach", "Harbour"])
        with field_values.opened(EXIFTOOL) as et:
            left = et.write(a, fields.keyword_fields(flat, hierarchical),
                            saving=lambda *_: self.fail("a patch with no byte to write saved a record"))
        self.assertEqual((KEYWORD_COPIES_LEFT, held), (tuple(left), read(a)))
        # Undo after the saves that did nothing: the first save's, the bytes the photo held.
        undone = self.undo(change)
        self.assertEqual((1, []), (undone.changed, undone.errors))
        self.assertEqual((original, ["Beach"]), (read(a), self.indexed(a)))

    def written_four_times(self, tag):
        """A photo of the captured header (its packet entry under `tag`) given a tag, its Date Taken an hour later, a turn
        right and every keyword taken off, one write at a time, each read back through ExifTool as the planners read it:
        (the patches each saved, the reads)."""
        a = photo_with_xmp_in_ifd0(os.path.join(self.folder, "a.jpg"), tag)
        with ExifToolSession(executable=EXIFTOOL) as et:
            self.seed(a, field_values.read(et, [a], metadata_check.READ)[paths.key(a)])
        writes = (lambda: self.add([a]),
                  lambda: photo_actions.shift_date_taken(self.library, [a], 60, EXIFTOOL),
                  lambda: photo_actions.rotate_photos(self.library, [a], "right", EXIFTOOL),
                  lambda: tagging.change_tags(self.library, [a], [], IFD0_KEYWORDS + ["Harbour"], EXIFTOOL))
        patches, reads = [], []
        for write in writes:
            with saves_counted() as saved:
                result = write()
            self.assertEqual((1, []), (result.changed, result.errors), result.message())
            patches.append(saved.call_count)
            reads.append((keywords_of(a), field_of(a, "EXIF:DateTimeOriginal"), field_of(a, "XMP:DateTimeOriginal"),
                          field_of(a, "EXIF:Orientation"), self.indexed(a)))
        return patches, reads

    def test_a_photo_holding_an_xmp_packet_in_its_exif_is_rewritten_and_reads_back_as_asked(self):
        # 3 of 3,000 sampled photo_index JPEGs (#3108). ExifTool's read answers the IFD0 packet's copy of a value the
        # APP1 packet's patch changed: the date read back unshifted, the keywords taken off read back as held.
        patches, reads = self.written_four_times(0x02BC)
        self.assertEqual([0, 0, 0, 0], patches, "a write patched a photo whose IFD0 holds an XMP packet")
        self.assertEqual(self.as_asked(), reads)

    def test_the_same_photo_without_the_ifd0_packet_is_patched_in_place(self):
        # The one difference, its IFD0 entry under a tag no reader takes for XMP: the own writer patches it.
        patches, reads = self.written_four_times(0x02BD)
        self.assertEqual([1, 1, 1, 1], patches, "a write of a photo with no XMP packet in its IFD0 was not a patch")
        self.assertEqual(self.as_asked(), reads)

    def as_asked(self):
        added = sorted(IFD0_KEYWORDS + ["Harbour"])
        return [(added, "2006:08:19 15:42:07", "2006:08:19 15:42:07Z", 1, added),
                (added, "2006:08:19 16:42:07", "2006:08:19 16:42:07Z", 1, added),
                (added, "2006:08:19 16:42:07", "2006:08:19 16:42:07Z", 6, added),
                ([], "2006:08:19 16:42:07", "2006:08:19 16:42:07Z", 6, [])]


if __name__ == "__main__":
    unittest.main()
