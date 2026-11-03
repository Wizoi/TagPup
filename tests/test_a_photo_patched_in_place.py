"""A photo's fields saved by a patch in place, in the flows the owner lives in (scenario F1; tagpup.files.in_place.patch_jpeg,
field_values.write's in-place path).

A keyword renamed writes only the bytes of the EXIF, IPTC and XMP segments that differ, the picture never touched, and
non-ASCII keywords (UTF-16 XPKeywords, UTF-8 XMP) the same; three saves in a row are three patches, and a second save
whose EXIF holds what it held after a first that shortened it writes no byte of the EXIF; what does not fit
is ExifTool's rewrite, as before the patch (a photo with no XMP yet, an EXIF segment that would move); a photo another
program has open is not patched; a photo another process is patching (held with no sharing) is not written by this
one; a share that stops answering mid-patch stops the write, and the next start settles the photo by its bytes; a
photo holding Photoshop's Print Flags Information (8BIM 0x2710) keeps it as it was, wherever it sits. The fit itself (in_place.jpeg_ranges) against ExifTool's own output: an IPTC segment that shrank by 2 to 10 bytes is
rewritten (no 8BIM resource is that small), by 12 or more it is padded with a dummy resource that ExifTool reads past.

Real JPEGs written by the real ExifTool in a home of the test's own (test_file_journal.FilesCase); the crash cuts of a
patch are D3's (test_a_crash_part_way_is_settled_at_the_next_start.AnInPlacePatchCutShort). Break that fails these:
field_values.PATCH_IN_PLACE False (every write the rewrite) fails the three tests that count a patch saved; in_place
._fitted padding IPTC with zero bytes instead of the dummy resource fails test_an_iptc_segment_that_shrank_is_padded_
only_by_a_resource_exiftool_reads_past; containers.PAD_RESOURCE 0x2710 (Print Flags' id) fails test_photoshop_print_flags_
in_the_iptc_segment_are_kept_as_they_were; in_place.jpeg_ranges not passing a segment fitted to what the photo
holds fails test_a_second_save_whose_exif_holds_what_it_held_writes_only_its_xmp; dropping its `new.offset != old.offset` refusal fails test_an_exif_segment_
that_would_move_is_rewritten.
"""
import contextlib
import io
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_rows  # noqa: E402
from test_a_crash_part_way_is_settled_at_the_next_start import start_again  # noqa: E402
from test_file_journal import EXIFTOOL, FilesCase, field_of, keywords_of, requires_exiftool, write_outside  # noqa: E402

from tagpup.files import containers, in_place, shares  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import tagging  # noqa: E402
from tagpup.store import db, file_journal  # noqa: E402


def read(path):
    with open(path, "rb") as handle:
        return handle.read()


def picture_of(held):
    """The bytes of a JPEG from its first scan to its end: what no patch may write."""
    return held[containers.read(io.BytesIO(held)).image_offset:]


@contextlib.contextmanager
def saves_counted():
    """The patches saved meanwhile (file_journal.save_patch, which each patch calls before its first byte)."""
    with mock.patch.object(file_journal, "save_patch", wraps=file_journal.save_patch) as saved:
        yield saved


@contextlib.contextmanager
def open_elsewhere(path, sharing):
    """The photo open in another program: Windows' CreateFileW with `sharing` (FILE_SHARE_* bits; 0, a writer that lets
    no one else in, as a patch in another process holds it)."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                   wintypes.DWORD, wintypes.HANDLE)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.CreateFileW(path, 0x80000000, sharing, None, 3, 0x80, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


#: Photoshop's Print Flags Information resource as 7 of 300 photo_index JPEGs hold one (a reviewer's count, 2026-11-03):
#: 8BIM 0x2710, no name, a body of 22 bytes.
PRINT_FLAGS = b"8BIM" + (0x2710).to_bytes(2, "big") + bytes(2) + (22).to_bytes(4, "big") + bytes(range(1, 23))


def with_print_flags(path, last):
    """The photo at `path` given PRINT_FLAGS in its IPTC segment, after its last 8BIM resource or before its first, as
    Photoshop leaves it in its list."""
    held = read(path)
    iptc = containers.read(io.BytesIO(held)).of_kind("iptc")[0]
    at = iptc.resources[-1].offset + iptc.resources[-1].size if last else iptc.resources[0].offset
    segment = held[iptc.offset:at] + PRINT_FLAGS + held[at:iptc.end]
    segment = segment[:2] + (len(segment) - 2).to_bytes(2, "big") + segment[4:]
    with open(path, "wb") as handle:
        handle.write(held[:iptc.offset] + segment + held[iptc.end:])


def away_at(nth):
    """shares.bounded answering "away" from the `nth` look in_place takes of the photo on, as a share that stops
    answering does: in a patch, 1 is the open, 2 the read of its header, 3 its record's read, 4 its write; then the
    settle's look at its bytes (in_place.held). A look that does not answer never runs."""
    real, looks, lock = shares.bounded, [0], threading.Lock()

    def bounded(location, call, seconds, away_seconds=None):
        if getattr(call, "__module__", None) == in_place.__name__:
            with lock:
                looks[0] += 1
                if looks[0] >= nth:
                    return "away", None
        return real(location, call, seconds, away_seconds)
    return mock.patch.object(in_place.shares, "bounded", bounded)


@requires_exiftool
class APhotoPatchedInPlace(FilesCase):
    def replace(self, photo_paths, old="Beach", new="Shore"):
        return tagging.replace_tag(self.library, photo_paths, old, new, EXIFTOOL)

    def test_a_keyword_renamed_writes_only_its_bytes_and_leaves_no_file_beside_it(self):
        a = self.make("a.jpg")
        original = read(a)
        with saves_counted() as saved:
            result = self.replace([a])
        self.assertEqual((1, [], 1), (result.changed, result.errors, saved.call_count))
        now = read(a)
        self.assertEqual(len(original), len(now))
        self.assertEqual(picture_of(original), picture_of(now), "the picture was written")
        layout = containers.read(io.BytesIO(original))
        patched = [segment for kind in in_place.PATCHED_KINDS for segment in layout.of_kind(kind)]
        differ = [at for at in range(len(now)) if now[at] != original[at]]
        self.assertTrue(differ)
        self.assertEqual([], [at for at in differ if not any(each.offset <= at < each.end for each in patched)],
                         "a byte outside the EXIF, IPTC and XMP segments was written")
        self.assertEqual((["Shore"], ["Shore"], ["a.jpg"]), (keywords_of(a), self.indexed(a), os.listdir(self.folder)))

    def test_keywords_beyond_ascii_are_patched_in_every_field(self):
        a = self.make("a.jpg", tags=("Café",))
        with saves_counted() as saved:
            result = self.replace([a], "Café", "Cafè")
        self.assertEqual((1, [], 1), (result.changed, result.errors, saved.call_count))
        self.assertEqual((["Cafè"], "Cafè", "Cafè"), (keywords_of(a), field_of(a, "EXIF:XPKeywords"),
                                                     field_of(a, "IPTC:Keywords")))

    def test_three_saves_in_a_row_are_three_patches_and_the_last_is_what_it_holds(self):
        a = self.make("a.jpg")
        original = read(a)
        with saves_counted() as saved:
            for old, new in (("Beach", "Shore"), ("Shore", "Beach"), ("Beach", "Shore")):
                self.assertEqual(1, self.replace([a], old, new).changed)
        self.assertEqual((3, ["Shore"], ["Shore"]), (saved.call_count, keywords_of(a), self.indexed(a)))
        self.assertEqual(picture_of(original), picture_of(read(a)))

    def test_a_second_save_whose_exif_holds_what_it_held_writes_only_its_xmp(self):
        # A camera's photo holds its Date Taken in EXIF alone. A keyword shortened is a patch whose EXIF, ExifTool's and
        # shorter, is made up to its old length with zero bytes. The photo panel's Save of the date it shows then adds
        # the date's XMP field; ExifTool writes the EXIF again with the values it holds, without those zeros, and made
        # up again it is the EXIF the photo holds: nothing of it to write, the XMP alone patched.
        tags, taken = ("Beachcombing at low tide",), "2020:01:01 10:00:00"
        a = self.make("a.jpg", tags=tags, taken=taken, row=False)
        write_outside(a, {"EXIF:CreateDate": taken})
        conn = db.connect(self.library.path)
        try:
            photo_rows.add_read(conn, a, {"XMP:Subject": list(tags), "IPTC:Keywords": list(tags),
                                          "EXIF:XPKeywords": ";".join(tags), "EXIF:DateTimeOriginal": taken,
                                          "EXIF:CreateDate": taken})
            conn.commit()
        finally:
            conn.close()
        original = read(a)
        with saves_counted() as saved:
            renamed = self.replace([a], tags[0], "Beach")
            self.assertEqual((1, [], 1), (renamed.changed, renamed.errors, saved.call_count), renamed.message())
            first = read(a)
            self.assertGreater(containers.read(io.BytesIO(first)).of_kind("exif")[0].slack, 0,
                               "the first patch did not make its EXIF up with zero bytes")
            result = tagging.save_photo(self.library, a, "", ["Beach"], "2020-01-01T10:00:00", EXIFTOOL, "")
        self.assertEqual((1, [], 2), (result.changed, result.errors, saved.call_count), result.message())
        now = read(a)
        exif = containers.read(io.BytesIO(first)).of_kind("exif")[0]
        self.assertEqual(first[exif.offset:exif.end], now[exif.offset:exif.end], "the EXIF was written")
        self.assertEqual((taken, ["Beach"]), (field_of(a, "XMP:DateTimeOriginal"), keywords_of(a)))
        self.assertEqual((len(original), picture_of(original)), (len(now), picture_of(now)))

    def test_a_photo_with_no_xmp_yet_is_rewritten(self):
        from PIL import Image

        a = os.path.join(self.folder, "a.jpg")
        Image.new("RGB", (16, 12), (90, 110, 130)).save(a, "JPEG")
        values = {"IPTC:Keywords": ["Beach"], "EXIF:XPKeywords": "Beach"}
        write_outside(a, values)
        conn = db.connect(self.library.path)
        try:
            photo_rows.add_read(conn, a, values)
            conn.commit()
        finally:
            conn.close()
        with saves_counted() as saved:
            result = self.replace([a])
        self.assertEqual((1, [], 0), (result.changed, result.errors, saved.call_count), "a segment made is no patch's")
        self.assertEqual((["Shore"], "Shore"), (keywords_of(a), field_of(a, "IPTC:Keywords")))

    def test_a_photo_a_viewer_has_open_is_not_written_under_it_and_the_error_names_the_holder(self):
        # A viewer (Explorer's preview, the Photos app) opens a photo letting others read, write and delete it. The patch
        # is not taken (it holds its photo alone); the rewrite's move over a photo held open fails as it did before the
        # patch was built, the photo as it was, and the error names who holds it.
        a = self.make("a.jpg")
        original = read(a)
        with open_elsewhere(a, 0x7), saves_counted() as saved:
            result = self.replace([a])
            self.assertEqual(original, read(a))
        self.assertEqual((0, 0, ["Beach"]), (result.changed, saved.call_count, keywords_of(a)),
                         "a photo open in another program was written under it")
        self.assertEqual(1, len(result.errors), result.message())
        self.assertIn("held open by", str(result.errors[0][1]))
        self.assertEqual(["a.jpg"], os.listdir(self.folder), "the rewrite's new file was left beside the photo")
        self.assertEqual((1, ["Shore"]), (self.replace([a]).changed, keywords_of(a)), "the photo let go is written")

    def test_a_photo_another_process_is_patching_is_not_written_by_this_one(self):
        # Two processes on one folder (the server and a CLI run, or two libraries holding it): a patch holds its photo
        # with no sharing from the read of its header to its last byte, so the other's write cannot open it.
        a = self.make("a.jpg")
        original = read(a)
        with open_elsewhere(a, 0), saves_counted() as saved:
            result = self.replace([a])
        self.assertEqual((0, 0, original), (result.changed, saved.call_count, read(a)))
        self.assertEqual(1, len(result.errors), result.message())
        self.assertEqual((1, ["Shore"]), (self.replace([a]).changed, keywords_of(a)), "the photo let go is written")

    def test_a_share_that_stops_answering_at_the_open_writes_nothing_and_says_so(self):
        a = self.make("a.jpg")
        original = read(a)
        with away_at(1), saves_counted() as saved:
            result = self.replace([a])
        self.assertEqual((0, 0, original), (result.changed, saved.call_count, read(a)))
        self.assertTrue(result.errors or result.details.get("stopped"), result.message())

    def test_a_share_that_stops_answering_at_the_write_is_settled_by_its_bytes_at_the_next_start(self):
        a = self.make("a.jpg")
        original = read(a)
        with away_at(4), saves_counted() as saved:
            result = self.replace([a])
        change = result.details["change"]
        self.assertEqual((0, 1, original), (result.changed, saved.call_count, read(a)))
        self.assertEqual(["writing"], self.states(change), "the record, the only way back, was let go")
        start_again(self.library)
        self.assertEqual((["done"], ["Shore"], ["Shore"]), (self.states(change), keywords_of(a), self.indexed(a)))
        self.assertEqual([], self.rows("SELECT id FROM change_files WHERE patch IS NOT NULL"))

    def test_an_exif_segment_that_would_move_is_rewritten(self):
        # A photo whose XMP comes before its EXIF, a long keyword shortened: every segment fits (the XMP by its
        # whitespace, the EXIF by zeros, the IPTC by a dummy resource), but ExifTool's XMP is shorter, so the EXIF it
        # wrote is nearer the start; a maker note may point at its own place in the file, so it is not moved back.
        a = self.make("a.jpg", tags=("Beachcombing at low tide",))
        held = read(a)
        layout = containers.read(io.BytesIO(held))
        (exif,), (xmp,) = layout.of_kind("exif"), layout.of_kind("xmp")
        order = ([each for each in layout.segments if each.offset < exif.offset] + [xmp]
                 + [each for each in layout.segments if each.offset >= exif.offset and each is not xmp])
        reordered = held[:2] + b"".join(held[each.offset:each.end] for each in order) + held[layout.segments[-1].end:]
        self.assertEqual(len(held), len(reordered))
        self.assertLess(*[[each.kind for each in containers.read(io.BytesIO(reordered)).segments].index(kind)
                          for kind in ("xmp", "exif")])
        with open(a, "wb") as handle:
            handle.write(reordered)
        with saves_counted() as saved:
            result = self.replace([a], "Beachcombing at low tide", "Beach")
        self.assertEqual((1, [], 0), (result.changed, result.errors, saved.call_count))
        self.assertEqual((["Beach"], "Beach"), (keywords_of(a), field_of(a, "EXIF:XPKeywords")))


    def test_photoshop_print_flags_in_the_iptc_segment_are_kept_as_they_were(self):
        # A photo Photoshop saved holds its Print Flags Information (0x2710) among its 8BIM resources. A keyword
        # shortened by 12 bytes shrinks the IPTC segment and the patch pads it with a resource of its own: never over
        # Print Flags when they are last, never a second resource of that id when they are not.
        tags = ("Beachcombing at low tide",)
        values = {"XMP:Subject": list(tags), "IPTC:Keywords": list(tags), "EXIF:XPKeywords": ";".join(tags)}
        for last in (True, False):
            with self.subTest(last=last):
                a = self.make("last.jpg" if last else "first.jpg", tags=tags, row=False)
                with_print_flags(a, last)
                conn = db.connect(self.library.path)
                try:
                    photo_rows.add_read(conn, a, values)
                    conn.commit()
                finally:
                    conn.close()
                with saves_counted() as saved:
                    result = self.replace([a], tags[0], "Beachcombing")
                self.assertEqual((1, [], 1), (result.changed, result.errors, saved.call_count), "not a patch")
                held = read(a)
                flags = [each for each in containers.read(io.BytesIO(held)).of_kind("iptc")[0].resources
                         if each.id == 0x2710]
                self.assertEqual(1, len(flags), "a second resource of Print Flags' id was written")
                self.assertEqual(PRINT_FLAGS, held[flags[0].offset:flags[0].offset + flags[0].size],
                                 "Print Flags were written over")
                self.assertEqual((["Beachcombing"], "Beachcombing"), (keywords_of(a), field_of(a, "IPTC:Keywords")))

@requires_exiftool
class TheFitOfAnIptcSegment(unittest.TestCase):
    """in_place.jpeg_ranges against ExifTool's own write of a header holding IPTC keywords alone: the segment that shrank
    is padded to its old length by a dummy 8BIM resource of 12 bytes or more, even (its header and an even body), or
    not at all; a shrink of 2 to 10 bytes or an odd one does not fit and is the rewrite's."""

    def setUp(self):
        from PIL import Image

        self.folder = own_folder(self)
        self.photo = os.path.join(self.folder, "a.jpg")
        Image.new("RGB", (16, 12), (90, 110, 130)).save(self.photo, "JPEG")
        write_outside(self.photo, {"IPTC:Keywords": ["Beachcombing at low tide"]})
        with open(self.photo, "rb") as handle:
            self.header = in_place._header_of(handle)[1]

    def written(self, keyword):
        source, target = os.path.join(self.folder, "header.jpg"), os.path.join(self.folder, "written.jpg")
        for path in (source, target):
            if os.path.exists(path):
                os.remove(path)
        with open(source, "wb") as handle:
            handle.write(self.header + in_place._EOI)
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([source], tags={"IPTC:Keywords": [keyword]}, params=["-m", "-o", target])
        return read(target)

    def test_an_iptc_segment_that_shrank_is_padded_only_by_a_resource_exiftool_reads_past(self):
        old = containers.read(io.BytesIO(self.header + in_place._EOI)).of_kind("iptc")[0]
        seen = set()
        for keyword in ("Beachcombing at low tid", "Beachcombing at low t", "Beachcombing at l", "Beachcombing",
                        "Beach", "Bea"):
            written = self.written(keyword)
            pad = old.size - containers.read(io.BytesIO(written)).of_kind("iptc")[0].size
            ranges = in_place.jpeg_ranges(self.header, written)
            fits = pad == 0 or (pad >= 12 and pad % 2 == 0)
            self.assertEqual(fits, ranges is not None, "a shrink of %d bytes" % pad)
            seen.add(fits)
            if ranges is None:
                continue
            patched = bytearray(self.header)
            for at, new in ranges:
                patched[at:at + len(new)] = new
            self.assertEqual(len(self.header), len(patched))
            if pad:
                last = containers.read(io.BytesIO(bytes(patched) + in_place._EOI)).of_kind("iptc")[0].resources[-1]
                self.assertEqual((containers.PAD_RESOURCE, pad), (last.id, last.size), "the padding is not the dummy's")
            check = os.path.join(self.folder, "patched.jpg")
            with open(check, "wb") as handle:
                handle.write(bytes(patched) + in_place._EOI)
            with ExifToolSession(executable=EXIFTOOL) as et:
                self.assertEqual(keyword, str(et.get_tags([check], tags=["IPTC:Keywords"])[0].get("IPTC:Keywords")))
                # ExifTool's next write of it reads past the dummy resource (zero bytes there break it).
                et.set_tags([check], tags={"IPTC:Keywords": ["Shore"]}, params=["-m", "-overwrite_original"])
                self.assertEqual("Shore", str(et.get_tags([check], tags=["IPTC:Keywords"])[0].get("IPTC:Keywords")))
            os.remove(check)
        self.assertEqual({True, False}, seen, "the keywords tried did not reach both a fit and a shrink too small")


def own_folder(testcase):
    import own_home

    return own_home.for_test(testcase, prefix="in_place_fit_").root


if __name__ == "__main__":
    unittest.main()
