"""The XMP census (tagpup.services.inspect.xmp_census, scripts/census_xmp.py): before phase 6 bakes regions and vectors
into the photo files, how many already hold MWG or Microsoft Photo regions, Extended XMP, two XMP blocks, how big their
XMP is and the Orientation they say, by extension (docs/PHASE6_DESIGN.md, 3.5). The rows cannot answer: the indexer keeps
no region field.

How it fails: a file that cannot be read is counted, never dropped; a share that stops answering ends the read and says
so, and the files not read are not counted as zero; an empty library reads nothing; a library whose root this machine
does not place reads nothing and says why; one whose root's folder is gone counts every file unreadable. Counts only:
the answer holds no path, no name, no value. With a stand-in ExifTool, and the real one on hand-made files. Fictional
names only.
"""
import contextlib
import importlib.util
import io
import json
import os
import struct
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import roots_library as rl  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Refused  # noqa: E402
from tagpup.files import exiftool_session, field_values  # noqa: E402
from tagpup.services import inspect  # noqa: E402
from tagpup.store import db, schema  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
WINDOWS = os.name == "nt"
HARBOUR = r"D:\Pictures\Harbour Regatta"
COAST = r"D:\Pictures\Coast Trip"
NAME = "Rowan Thackeray"


def census_program():
    """scripts/census_xmp.py, loaded by its path: a program, not a module of the package."""
    spec = importlib.util.spec_from_file_location("census_xmp", os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "census_xmp.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def binary(size):
    return "(Binary data %d bytes, use -b option to extract)" % size


def stand_in(answers, refused=(), stall_at=None):
    """An ExifTool session that answers each photo from `answers` ({path: what ExifTool answers}); a command holding a
    path of `refused` fails as one naming a missing file does; command number `stall_at` (from 1) waits out its
    deadline, as one on a share gone to sleep does. `commands` lists each command's (photos, params)."""
    refused = {paths.key(p) for p in refused}
    by_key = {paths.key(p): answer for p, answer in answers.items()}

    class StandIn:
        commands = []
        timeout = 300

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_tags(self, batch, tags=None, params=None):
            StandIn.commands.append((list(batch), list(params or [])))
            if stall_at is not None and len(StandIn.commands) >= stall_at:
                raise exiftool_session.ExifToolTimeout("ExifTool did not answer within 300s (a read).")
            if any(paths.key(p) in refused for p in batch):
                raise RuntimeError("Error: File not found - <file>")
            return [dict({"File:MIMEType": "image/jpeg", "File:FileType": "JPEG"}, **by_key.get(paths.key(p), {}),
                         SourceFile=p.replace(os.sep, "/")) for p in batch]

    return StandIn


class ACensusOfALibrary(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self, prefix="xmp_census_")
        self.db_path = home.library("library.db")
        schema.ensure(self.db_path)
        self.library = Library(self.db_path)

    def add(self, *photo_paths):
        conn = db.connect(self.db_path)
        try:
            for path in photo_paths:
                photo_rows.add_read(conn, path, {"XMP:Subject": ["Activity/Sailing"]})
            conn.commit()
        finally:
            conn.close()

    def census(self, session, **kwargs):
        with mock.patch.object(exiftool_session, "ExifToolSession", session):
            return inspect.xmp_census(self.library, **kwargs)

    def assertNothingNamed(self, answer, *photo_paths):
        said = json.dumps(answer)
        for text in [NAME, "Harbour", "Coast", "IMG_"] + [os.path.basename(p) for p in photo_paths]:
            self.assertNotIn(text, said)

    def test_each_question_is_counted_by_extension_one_command_a_batch(self):
        held = {
            os.path.join(HARBOUR, "IMG_0001.jpg"): {"XMP:RegionType": "Face", "XMP:XMP": binary(16 * 1024 - 1),
                                                     "EXIF:Orientation": 6},
            os.path.join(HARBOUR, "IMG_0002.jpg"): {"XMP:RegionRectangle": "0.1, 0.1, 0.2, 0.2",
                                                     "XMP:RegionPersonDisplayName": NAME, "XMP:XMP": binary(16 * 1024),
                                                     "EXIF:Orientation": 1},
            os.path.join(HARBOUR, "IMG_0003.jpg"): {"XMP:HasExtendedXMP": "DE81626638B70122E23F580608DB5AFD",
                                                     "XMP:XMP": binary(48 * 1024)},
            os.path.join(COAST, "IMG_0004.jpg"): {"ExifTool:Warning": "Duplicate XMP at JPEG-APP1-XMP",
                                                   "XMP:XMP": binary(64 * 1024 - 1), "EXIF:Orientation": 6},
            os.path.join(COAST, "IMG_0005.jpg"): {"XMP:XMP": binary(64 * 1024)},
            os.path.join(COAST, "Scan_0006.tif"): {"File:MIMEType": "image/tiff", "File:FileType": "TIFF"},
        }
        self.add(*held)
        session = stand_in(held)
        with mock.patch.object(field_values, "READ_BATCH", 4):
            answer = self.census(session)
        self.assertEqual((6, 6, 6, 0, 0, False), (answer["photos"], answer["sampled"], answer["read"],
                                                  answer["unreadable"], answer["not_answered"], answer["stopped"]))
        self.assertEqual([2, 4], sorted(len(batch) for batch, _params in session.commands), "one command a batch")
        self.assertTrue(all(params == list(inspect.CENSUS_PARAMS) for _batch, params in session.commands))
        jpg, tif = answer["by_extension"][".jpg"], answer["by_extension"][".tif"]
        self.assertEqual((5, 5, 1, 1, 1, 1), (jpg["n"], jpg["read"], jpg["mwg_regions"], jpg["mp_regions"],
                                              jpg["extended_xmp"], jpg["duplicate_xmp"]))
        self.assertEqual({"<16K": 1, "<48K": 1, "<64K": 2, "over": 1, "none": 0}, jpg["xmp_size"])
        self.assertEqual({"6": 2, "1": 1, "none": 2}, jpg["orientation"])
        self.assertEqual((1, 1, 0), (tif["n"], tif["xmp_size"]["none"], tif["mwg_regions"]))
        self.assertNothingNamed(answer, *held)

    def test_a_file_that_cannot_be_read_is_counted_not_dropped(self):
        good, gone, text = (os.path.join(HARBOUR, "IMG_%04d.jpg" % n) for n in (1, 2, 3))
        self.add(good, gone, text)
        session = stand_in({good: {"XMP:RegionType": "Face"}, text: {"File:MIMEType": "text/plain"}}, refused=[gone])
        answer = self.census(session)
        jpg = answer["by_extension"][".jpg"]
        self.assertEqual((3, 1, 2, 1), (jpg["n"], jpg["read"], jpg["unreadable"], jpg["mwg_regions"]))
        self.assertEqual((1, 2), (answer["read"], answer["unreadable"]))

    def test_a_share_that_stops_answering_ends_the_read_and_the_rest_is_not_counted_as_nothing(self):
        photos = [os.path.join(HARBOUR, "IMG_%04d.jpg" % n) for n in range(1, 6)]
        self.add(*photos)
        session = stand_in({p: {"XMP:RegionType": "Face"} for p in photos}, stall_at=2)
        with mock.patch.object(field_values, "READ_BATCH", 2):
            answer = self.census(session)
        self.assertEqual(2, len(session.commands), "the read ends at the command not answered")
        self.assertTrue(answer["stopped"])
        jpg = answer["by_extension"][".jpg"]
        self.assertEqual((5, 2, 0, 3, 2), (jpg["n"], jpg["read"], jpg["unreadable"], jpg["not_answered"],
                                           jpg["mwg_regions"]))
        said = "\n".join(census_program().lines(answer))
        self.assertIn("ExifTool stopped answering", said)
        self.assertIn("the 3 not answered are not counted", said)

    def test_an_empty_library_starts_no_exiftool(self):
        def no_session(*args, **kwargs):
            raise AssertionError("an ExifTool was started for no photo")

        answer = self.census(no_session)
        self.assertEqual((0, 0, {}), (answer["photos"], answer["sampled"], answer["by_extension"]))
        self.assertIn("holds no photos", "\n".join(census_program().lines(answer)))

    def test_a_sample_is_spread_over_the_folders_by_their_size(self):
        folders = {HARBOUR: 10, COAST: 30}
        for folder, count in folders.items():
            self.add(*[os.path.join(folder, "IMG_%04d.jpg" % n) for n in range(count)])
        session = stand_in({})
        answer = self.census(session, sample=8)
        asked = [p for batch, _params in session.commands for p in batch]
        self.assertEqual((40, 8, 8), (answer["photos"], answer["sampled"], len(set(asked))))
        self.assertEqual([2, 6], [sum(1 for p in asked if paths.is_under(p, folder)) for folder in (HARBOUR, COAST)])
        whole = self.census(stand_in({}), sample=100)
        self.assertEqual(40, whole["sampled"], "a library smaller than the sample is read whole")
        with self.assertRaises(Refused):
            self.census(stand_in({}), sample=0)


@unittest.skipUnless(WINDOWS, "spellings of roots_library are Windows paths")
class ALibraryWhoseRootsAreNotThere(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="xmp_census_roots_")
        self.side = rl.Side(self.home, "photo_index", real=1, bulk=2, outside=0)
        self.assertTrue(self.side.adopt().ok)

    def test_a_root_this_machine_does_not_place_reads_nothing_and_says_why(self):
        os.remove(config.machine_roots_path())

        def no_session(*args, **kwargs):
            raise AssertionError("an ExifTool was started for a library whose photos cannot be named")

        with mock.patch.object(exiftool_session, "ExifToolSession", no_session):
            answer = inspect.xmp_census(self.side.library)
        self.assertEqual(["roots_problem"], list(answer))
        self.assertIn("machine_roots.json", answer["roots_problem"])

    @unittest.skipUnless(EXIFTOOL, "needs ExifTool")
    def test_a_root_whose_folder_is_gone_counts_every_file_unreadable(self):
        os.rename(self.side.pictures, self.side.pictures + " moved away")
        answer = inspect.xmp_census(self.side.library, exiftool_path=EXIFTOOL)
        self.assertEqual((9, 9, 0, 9), (answer["photos"], answer["sampled"], answer["read"], answer["unreadable"]))
        self.assertFalse(answer["stopped"])

    @unittest.skipUnless(EXIFTOOL, "needs ExifTool")
    def test_the_program_prints_the_counts_and_no_file_it_could_not_read(self):
        os.rename(self.side.pictures, self.side.pictures + " moved away")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = census_program().main(["--db", self.side.db_path])
        self.assertEqual(0, code)
        self.assertIn("Read: 0; unreadable: 9; not answered: 0.", out.getvalue())
        for text in ("Pictures", "Earlier_", "IMG_", self.home.root):
            self.assertNotIn(text, out.getvalue() + err.getvalue())


@unittest.skipUnless(EXIFTOOL, "needs ExifTool")
class TheRealExifToolOnHandMadeFiles(unittest.TestCase):
    """What the census asks is what ExifTool answers: a struct asked by its flattened field, the XMP block's size without
    its bytes, two XMP blocks only under -validate."""

    def setUp(self):
        from PIL import Image
        self.home = own_home.for_test(self, prefix="xmp_census_real_")
        folder = os.path.join(self.home.root, "Pictures", "Harbour Regatta")
        os.makedirs(folder)
        self.files = {name: os.path.join(folder, name) for name in (
            "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.jpg", "IMG_0004.jpg", "IMG_0005.jpg", "Scan_0006.png",
            "Note_0007.jpg")}
        for name, path in self.files.items():
            if name.startswith("Note"):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("not a photo")
            else:
                Image.new("RGB", (32, 24), (90, 120, 30)).save(path)
        f = self.files
        with exiftool_session.ExifToolSession(executable=EXIFTOOL) as et:
            et.execute("-XMP-mwg-rs:RegionType=Face", "-EXIF:Orientation#=6", "-overwrite_original", f["IMG_0001.jpg"])
            et.execute("-XMP-MP:RegionInfoMP={Regions=[{PersonDisplayName=%s}]}" % NAME, "-overwrite_original",
                       f["IMG_0002.jpg"])
            et.execute("-XMP-dc:Description=" + "x" * 70000, "-overwrite_original", f["IMG_0003.jpg"])
            et.execute("-XMP-dc:Subject=Activity/Sailing", "-IFD0:Rating=3", "-overwrite_original", f["IMG_0004.jpg"])
            et.execute("-XMP-dc:Subject=Activity/Sailing", "-overwrite_original", f["Scan_0006.png"])
        self.second_xmp_block(f["IMG_0004.jpg"])
        db_path = self.home.library("library.db")
        schema.ensure(db_path)
        self.library = Library(db_path)
        conn = db.connect(db_path)
        try:
            for path in self.files.values():
                photo_rows.add_read(conn, path, {"XMP:Subject": ["Activity/Sailing"]})
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def second_xmp_block(path):
        """The JPEG's XMP segment copied once more after itself, as some programs leave a photo."""
        with open(path, "rb") as handle:
            data = handle.read()
        start = data.find(b"http://ns.adobe.com/xap/1.0/\x00") - 4
        end = start + 2 + struct.unpack(">H", data[start + 2:start + 4])[0]
        with open(path, "wb") as handle:
            handle.write(data[:end] + data[start:end] + data[end:])

    def test_the_census_counts_what_each_file_holds(self):
        answer = inspect.xmp_census(self.library, exiftool_path=EXIFTOOL)
        jpg, png = answer["by_extension"][".jpg"], answer["by_extension"][".png"]
        self.assertEqual((7, 6, 1, False), (answer["sampled"], answer["read"], answer["unreadable"], answer["stopped"]))
        self.assertEqual((6, 5, 1), (jpg["n"], jpg["read"], jpg["unreadable"]))
        self.assertEqual((1, 1, 1, 1), (jpg["mwg_regions"], jpg["mp_regions"], jpg["extended_xmp"],
                                       jpg["duplicate_xmp"]))
        self.assertEqual({"<16K": 4, "<48K": 0, "<64K": 0, "over": 0, "none": 1}, jpg["xmp_size"])
        self.assertEqual({"6": 1, "none": 4}, jpg["orientation"])
        self.assertEqual((1, 1, {"<16K": 1, "<48K": 0, "<64K": 0, "over": 0, "none": 0}),
                         (png["n"], png["read"], png["xmp_size"]))
        said = json.dumps(answer) + "\n".join(census_program().lines(answer))
        for text in (NAME, "Harbour", "IMG_", "Note_", self.home.root):
            self.assertNotIn(text, said)


if __name__ == "__main__":
    unittest.main()
