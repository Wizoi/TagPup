"""A photo's fields saved by a patch in place, in the flows the owner lives in (scenario F1; tagpup.files.in_place.patch_jpeg,
field_values.write's in-place path).

A keyword renamed writes only the bytes of the EXIF, IPTC and XMP segments that differ, the picture never touched, and
non-ASCII keywords (UTF-16 XPKeywords, UTF-8 XMP) the same; three saves in a row are three patches; what does not fit
is ExifTool's rewrite, as before the patch (a photo with no XMP yet, an EXIF segment that would move); a photo another
program has open is not patched; a photo another process is patching (held with no sharing) is not written by this
one; a share that stops answering mid-patch stops the write, and the next start settles the photo by its bytes. The
fit itself (in_place.jpeg_ranges) against ExifTool's own output: an IPTC segment that shrank by 2 to 10 bytes is
rewritten (no 8BIM resource is that small), by 12 or more it is padded with a dummy resource that ExifTool reads past.

Real JPEGs written by the real ExifTool in a home of the test's own (test_file_journal.FilesCase); the crash cuts of a
patch are D3's (test_a_crash_part_way_is_settled_at_the_next_start.AnInPlacePatchCutShort). Break that fails these:
field_values.PATCH_IN_PLACE False (every write the rewrite) fails the three tests that count a patch saved; in_place
._fitted padding IPTC with zero bytes instead of the dummy resource fails test_an_iptc_segment_that_shrank_is_padded_
only_by_a_resource_exiftool_reads_past; dropping its `new.offset != old.offset` refusal fails test_an_exif_segment_
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
