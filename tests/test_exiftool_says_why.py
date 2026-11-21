"""A command ExifTool fails says what ExifTool said.

Two bulk writes of the same photos at once failed on the server as "Error in bulk tags
write: execute returned a non-zero exit status: 1", and the page said the same: all
pyexiftool's error carries. ExifTool had said "Error creating file:
<file>_exiftool_tmp", which names the cause. Now the error, and so the route's reply,
carries ExifTool's first lines, each photo named "<file>" (a file's name can hold a
caption, and so a name); the log has all of it.

Real ExifTool: a file named as ExifTool names its temporary file, in the way of the
write, is what makes it refuse. AFailedWrite's is written as each test begins: it may be another
write's under way, and stays (file_changes.removed_scratch, rule 3; DECISIONS 2026-10-29), the
owner told to try again later; one an interrupted write left long ago is removed and the photo
written (#2180). The scratch file is ExifTool's rewrite's, and no patch in place is made while one is there
(field_values.write): the save falls to the rewrite, which settles it.
"""
import logging
import os
import time
import unittest
from unittest import mock

from tests.handler_harness import Library
from tests.test_bulk_writes_start_from_the_file import make_photo, tags_in
from tests.test_taxonomy_lifecycle import EXIFTOOL, requires_exiftool

from exiftool.exceptions import ExifToolExecuteError

from tagpup.files import exiftool_session
from tagpup.files.exiftool_session import ExifToolSession


def in_the_way(photo):
    """What ExifTool writes a photo through, left there: it refuses to write it."""
    with open(photo + "_exiftool_tmp", "wb") as handle:
        handle.write(b"left by another write")


@requires_exiftool
class AFailedWrite(unittest.TestCase):
    def setUp(self):
        self.lib = Library(self)
        self.lib.hold(self.lib.photos)
        self.photo = make_photo(os.path.join(self.lib.photos, "Rowan at the quay.jpg"))
        in_the_way(self.photo)

    def test_the_error_says_what_exiftool_said_and_names_no_photo(self):
        with self.assertLogs(exiftool_session.logger, logging.WARNING) as logged:
            with ExifToolSession(executable=EXIFTOOL) as et:
                with self.assertRaises(ExifToolExecuteError) as raised:
                    et.set_tags([self.photo], tags={"XMP:Subject": ["Quay"]},
                                params=["-o", exiftool_session.scratch_of(self.photo)])
        said = str(raised.exception)
        self.assertIn("' already exists", said)
        self.assertNotIn("Rowan", said)
        self.assertIn("<file>", said)
        # What is in the way, and what to do, said without the photo's name (#1411).
        self.assertIn("_exiftool_tmp after it", said)
        self.assertIn("rename it back", said)
        self.assertEqual(1, raised.exception.returncode)
        # The log has all of it, the photo named.
        self.assertTrue(any("Rowan at the quay.jpg_exiftool_tmp" in line for line in logged.output), logged.output)

    def test_a_scratch_file_an_interrupted_write_left_long_ago_is_removed_and_the_photo_written(self):
        """The live case (#2180): a share that dropped mid-write left it days before, beside the photo as it was."""
        long_ago = time.time() - 3 * 86400
        os.utime(self.photo, (long_ago, long_ago))
        os.utime(self.photo + "_exiftool_tmp", (long_ago + 60, long_ago + 60))
        status, reply = self.lib.post("/api/photos/write", {"op": "tags", "changes": {self.photo: {"add": ["Sunset"]}}})
        self.assertEqual(200, status, reply)
        self.assertFalse(os.path.exists(self.photo + "_exiftool_tmp"))
        self.assertIn("Sunset", tags_in(self.photo))

    def test_the_bulk_write_route_says_it(self):
        status, reply = self.lib.post("/api/photos/write", {"op": "tags", "changes": {self.photo: {"add": ["Sunset"]}}})
        self.assertEqual(200, status, reply)
        self.assertFalse(reply["success"], reply)
        (error,) = reply["errors"]
        self.assertIn("' already exists", error["why"])
        self.assertNotIn("non-zero exit status", error["why"])
        # Written this moment, it may be another write's under way: not one to delete (rule 3).
        self.assertIn("try again after 5 minutes", error["why"])
        self.assertNotIn("delete that file", error["why"])


@requires_exiftool
class ACommandThatTimesOut(unittest.TestCase):
    """A timeout's message named the command's files, and it reaches the journal's
    conflict text ("could not be read: ..."), which History and the MCP server's history
    show without revealing paths. "-" makes ExifTool wait on its command pipe for an
    image that never comes (test_exiftool_session.py)."""

    def test_it_says_how_many_files_and_names_none(self):
        photo = os.path.join(own_folder(self), "Rowan Thackeray at the quay.jpg")
        et = ExifToolSession(executable=EXIFTOOL, timeout=3)
        self.addCleanup(lambda: et.terminate() if et.running else None)
        with self.assertLogs(exiftool_session.logger, logging.WARNING) as logged:
            with self.assertRaises(exiftool_session.ExifToolTimeout) as raised:
                et.execute("-j", photo, "-")
        said = str(raised.exception)
        self.assertIn("1 file", said)
        self.assertNotIn("Rowan", said)
        self.assertTrue(any("Rowan Thackeray at the quay.jpg" in line for line in logged.output), logged.output)


def own_folder(testcase):
    import tempfile
    import shutil

    folder = tempfile.mkdtemp(prefix="says_why_")
    testcase.addCleanup(shutil.rmtree, folder, True)
    return folder


class SaidBriefly(unittest.TestCase):
    def test_a_file_named_by_its_name_alone_is_named_the_same(self):
        said = exiftool_session.unnamed("Warning: [minor] Rowan.jpg has a bad XMP", ["D:\\Photos\\Rowan.jpg"])
        self.assertEqual("Warning: [minor] <file> has a bad XMP", said)

    def test_the_first_errors_before_warnings_each_file_named_the_same(self):
        stderr = ("Warning: IPTCDigest is not current - D:/Photos/Rowan.jpg\r\n"
                  "Error: Error creating file: D:/Photos/Rowan.jpg_exiftool_tmp - D:/Photos/Rowan.jpg\r\n"
                  "Error: File not found - d:/photos/ROWAN.JPG\r\n")
        said = exiftool_session.said_briefly(stderr, ["-overwrite_original", "D:\\Photos\\Rowan.jpg"])
        self.assertEqual("Error: Error creating file: <file>_exiftool_tmp - <file>; Error: File not found - <file>",
                         said)

    def test_the_log_says_it_on_one_line_the_error_first(self):
        # A failed bake's log line showed the warning, and the error that failed it two lines down (#2015).
        stderr = ("Warning: [minor] Fixed incorrect URI for xmlns:MicrosoftPhoto - D:/Photos/Rowan.jpg\r\n"
                  "Error: [minor] Different 'rdf:about' attributes not handled - D:/Photos/Rowan.jpg\r\n")
        refused = ExifToolExecuteError(1, "", stderr, ["-overwrite_original", "D:/Photos/Rowan.jpg"])
        with self.assertLogs(exiftool_session.logger, logging.WARNING) as logged, \
                mock.patch("exiftool.ExifToolHelper.execute", side_effect=refused):
            with self.assertRaises(ExifToolExecuteError):
                ExifToolSession(executable="exiftool").execute("-overwrite_original", "D:/Photos/Rowan.jpg")
        self.assertEqual(1, len(logged.output))
        line = logged.output[0]
        self.assertNotIn("\n", line)
        self.assertLess(line.index("Error: [minor] Different"), line.index("Warning: [minor] Fixed"), line)

    def test_nothing_said_is_nothing(self):
        self.assertEqual("", exiftool_session.said_briefly(b"", []))


if __name__ == "__main__":
    unittest.main()
