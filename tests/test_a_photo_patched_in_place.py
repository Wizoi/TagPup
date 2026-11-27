"""A photo's fields saved by a patch in place, in the flows the owner lives in (scenario F1; tagpup.files.in_place.patch_jpeg,
field_values.write's in-place path).

A keyword renamed writes only the bytes of the EXIF, IPTC and XMP segments that differ, the picture never touched;
a non-ASCII keyword renamed is patched into the XMP alone (UTF-8), the copies outside it left as they were (the own
writer, segment_edits; fields.XMP_ONLY); three saves in a row are three patches; what the own writer cannot patch is
ExifTool's rewrite (a photo with no XMP yet); a photo another program has open is not patched; a photo another process
is patching (held with no sharing) is not written by this one; a share that stops answering mid-patch stops the write,
and the next start settles the photo by its bytes; a photo holding Photoshop's Print Flags Information (8BIM 0x2710)
keeps it as it was, wherever it sits, when its last keyword is taken off (every copy written, #2433) and the own
writer pads the IPTC segment that shrank with a dummy resource of its own.

Real JPEGs written by the real ExifTool in a home of the test's own (test_file_journal.FilesCase); the crash cuts of a
patch are D3's (test_a_crash_part_way_is_settled_at_the_next_start.AnInPlacePatchCutShort). Break that fails these:
field_values.PATCH_IN_PLACE False (every write the rewrite) fails the tests that count a patch saved;
containers.PAD_RESOURCE 0x2710 (Print Flags' id) fails test_photoshop_print_flags_in_the_iptc_segment_are_kept_as_they_were.
A save that patches the XMP alone is the scenarios of test_a_photo_written_by_its_own_editors.
"""
import contextlib
import io
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exiftool_fields  # noqa: E402
from corpus import PRINT_FLAGS  # noqa: E402
import photo_rows  # noqa: E402
from test_a_crash_part_way_is_settled_at_the_next_start import start_again  # noqa: E402
from test_file_journal import EXIFTOOL, FilesCase, field_of, keywords_of, requires_exiftool, write_outside  # noqa: E402

from tagpup.files import containers, in_place, shares  # noqa: E402
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
        return tagging.replace_tag(self.library, photo_paths, old, new, exiftool_fields.opened(self, EXIFTOOL))

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

    def test_keywords_beyond_ascii_are_patched_into_the_xmp_alone(self):
        # The own writer writes a keyword save into the XMP copy alone (UTF-8), leaving XPKeywords (UTF-16) and IPTC as
        # they were (fields.XMP_ONLY); the reader takes XMP's.
        a = self.make("a.jpg", tags=("Café",))
        with saves_counted() as saved:
            result = self.replace([a], "Café", "Cafè")
        self.assertEqual((1, [], 1), (result.changed, result.errors, saved.call_count))
        self.assertEqual((["Cafè"], "Café", "Café"), (keywords_of(a), field_of(a, "EXIF:XPKeywords"),
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

    def test_photoshop_print_flags_in_the_iptc_segment_are_kept_as_they_were(self):
        # A photo Photoshop saved holds its Print Flags Information (0x2710) among its 8BIM resources. Its last keyword
        # taken off is taken from every copy (#2433): the IPTC segment shrinks and the own writer pads it with a
        # resource of its own: never over Print Flags when they are last, never a second resource of that id when they
        # are not.
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
                    result = tagging.change_tags(self.library, [a], [], list(tags), exiftool_fields.opened(self, EXIFTOOL))
                self.assertEqual((1, [], 1), (result.changed, result.errors, saved.call_count), "not a patch")
                held = read(a)
                flags = [each for each in containers.read(io.BytesIO(held)).of_kind("iptc")[0].resources
                         if each.id == 0x2710]
                self.assertEqual(1, len(flags), "a second resource of Print Flags' id was written")
                self.assertEqual(PRINT_FLAGS, held[flags[0].offset:flags[0].offset + flags[0].size],
                                 "Print Flags were written over")
                self.assertEqual(([], None), (keywords_of(a), field_of(a, "IPTC:Keywords")))


if __name__ == "__main__":
    unittest.main()
