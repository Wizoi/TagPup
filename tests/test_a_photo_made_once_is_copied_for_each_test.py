"""A photo, a clip or a library a test asks for is made once by the real writer and copied for each test
(tests/corpus.py): the copies are private, the item is never written, and the run's eight processes, a run killed
part-way and a corpus folder deleted mid-run each still give every test a whole copy. What ExifTool and ffmpeg make is
kept across runs, made again when either is upgraded, and the least recently used deleted past the cap. Every item of
the conformance corpus (corpus.ITEMS), made twice into two empty corpus folders, holds the same bytes, and no two hold
the same: a recipe is its bytes, and each makes the layout it names.

Each test names corpus folders of its own (corpus.ENV and corpus.CACHE_ENV, in a home of its own), never the run's or
the machine's: it kills makers, deletes folders and fakes installed writers. The photo is ExifTool's, as FilesCase.make's is; the race and the kill are timed by a maker of the
test's own that writes half a file, waits, and writes the rest, so that the moment of the cut is the test's.
"""
import os
import sys
import time
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corpus  # noqa: E402
import own_home  # noqa: E402
from waiting import wait_until  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core import processes  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.store import db, schema  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
TOOLS = config.locate_ffmpeg()
BEACH = {"XMP:Subject": ["Beach"], "IPTC:Keywords": ["Beach"]}
WHOLE = b"made " * 2000

#: A test process asking for one item, made slowly: it writes half, says so (a file named for its process), waits
#: `pause` seconds and writes the rest, then copies it to `dest`. argv: dest, pause.
ASKER = """
import os, sys, time
sys.path.insert(0, %r)
import corpus
dest, pause = sys.argv[1], float(sys.argv[2])   # made as a kept item: two runs in two worktrees share it

def build(target):
    with open(target, "wb") as handle:
        handle.write(%r[:5000])
        handle.flush()
        with open(os.path.join(os.path.dirname(dest), "making-%%d" %% os.getpid()), "w"):
            pass
        time.sleep(pause)
        handle.write(%r[5000:])

corpus.made(dest, ["slow"], build, keep=True)
""" % (os.path.join(WORKSPACE_DIR, "tests"), WHOLE, WHOLE)


def making(made):
    """A maker of the whole item at once, listing each target in `made`."""
    def build(target):
        made.append(target)
        with open(target, "wb") as handle:
            handle.write(WHOLE)
    return build


def bytes_of(path):
    with open(path, "rb") as handle:
        return handle.read()


class ACorpusOfItsOwn(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="corpus_")
        self.corpus = os.path.join(self.home.root, "corpus")
        self.cache = os.path.join(self.home.root, "cache")
        patcher = mock.patch.dict(os.environ, {corpus.ENV: self.corpus, corpus.CACHE_ENV: self.cache})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(corpus.remove, self.corpus)
        self.addCleanup(corpus.remove, self.cache)
        self.folder = os.path.join(self.home.root, "Photos")
        os.makedirs(self.folder)
        with open(os.path.join(self.home.root, "asker.py"), "w", encoding="utf-8") as handle:
            handle.write(ASKER)

    def ask(self, name, pause=0.0):
        """A process of its own asking for the slow item at `name` in the folder."""
        return processes.start([sys.executable, os.path.join(self.home.root, "asker.py"),
                                os.path.join(self.folder, name), str(pause)])

    def made_by(self):
        return sorted(n for n in os.listdir(self.folder) if n.startswith("making-"))

    def items(self):
        """The run's items."""
        return sorted(n for n in os.listdir(self.corpus) if not n.endswith(".lock"))

    def kept_items(self):
        """The items kept across runs, of this generation."""
        return sorted(n for n in os.listdir(corpus.kept()) if not n.endswith(".lock"))


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class APhotoCopiedForEachTest(ACorpusOfItsOwn):

    def test_two_tests_copies_of_one_photo_are_their_own_and_the_photo_made_once_is_never_written(self):
        before = time.time()
        a = corpus.jpeg(os.path.join(self.folder, "a.jpg"), BEACH, EXIFTOOL)
        b = corpus.jpeg(os.path.join(self.folder, "b.jpg"), BEACH, EXIFTOOL)
        relay = corpus.jpeg(os.path.join(self.folder, "relay.jpg"), {"XMP:Subject": ["Relay"]}, EXIFTOOL)
        self.assertEqual(bytes_of(a), bytes_of(b), "one recipe, one photo: as two made by it were")
        self.assertNotEqual(bytes_of(a), bytes_of(relay), "a tag of its own makes a photo of its own")
        self.assertEqual(2, len(self.kept_items()), "made once for each recipe, and kept")
        # A copy is a file made now: its own time, never the item's.
        self.assertGreaterEqual(os.stat(b).st_mtime, before - 2)
        made = corpus.item(["jpeg", [16, 12], [90, 110, 130], BEACH], EXIFTOOL, ".jpg", keep=True)
        kept = bytes_of(made)
        with self.assertRaises(PermissionError, msg="the item is read-only"):
            open(made, "r+b").close()
        # The write under test is the real ExifTool's, on the copy.
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([a], tags={"XMP:Subject": ["Harbour"]}, params=["-overwrite_original"])
        self.assertNotEqual(kept, bytes_of(a))
        self.assertEqual(kept, bytes_of(made), "the corpus is as it was made")
        self.assertEqual(kept, bytes_of(b), "another test's copy is as it was made")


class AnItemWhoseBuildMakesAnother(ACorpusOfItsOwn):

    def test_the_inner_item_on_the_outer_ones_stripe_is_made_not_waited_for(self):
        # An _over or clip item makes another item inside its build; their keys meet on one of 64 stripes once in 64.
        outer = os.path.join(corpus.kept(), "00000000-outer.bin")
        inner = os.path.join(corpus.kept(), "%08x-inner.bin" % corpus.STRIPES)

        def build_inner(part):
            with open(part, "wb") as handle:
                handle.write(b"inner")

        def build_outer(part):
            corpus._make(inner, build_inner, False)
            with open(part, "wb") as handle:
                handle.write(b"outer")

        with mock.patch.object(corpus, "WAIT", 1.0):
            corpus._make(outer, build_outer, False)
        self.assertEqual([b"outer", b"inner"], [bytes_of(outer), bytes_of(inner)])


class ATestRunsEightProcesses(ACorpusOfItsOwn):

    def test_a_writer_installed_again_meanwhile_makes_its_items_again(self):
        tool = os.path.join(self.home.root, "writer.exe")   # stands for ExifTool's or ffmpeg's executable
        with open(tool, "wb") as handle:
            handle.write(b"version 1")
        made = []
        build = making(made)
        corpus.made(os.path.join(self.folder, "one.bin"), ["item"], build, tool)
        corpus.made(os.path.join(self.folder, "two.bin"), ["item"], build, tool)
        os.utime(tool, (time.time() + 60, time.time() + 60))
        corpus.made(os.path.join(self.folder, "three.bin"), ["item"], build, tool)
        self.assertEqual(2, len(made), "made once, then once more by the new writer")

    def test_two_processes_asking_for_one_item_at_once_make_it_once_and_each_copies_it_whole(self):
        first, second = self.ask("first.bin", pause=1.5), self.ask("second.bin", pause=1.5)
        for asker in (first, second):
            self.assertEqual(0, asker.wait(timeout=60))
        self.assertEqual(1, len(self.made_by()), "one made it; the other waited its turn and copied it")
        for name in ("first.bin", "second.bin"):
            self.assertEqual(WHOLE, bytes_of(os.path.join(self.folder, name)))
        self.assertEqual(1, len(self.kept_items()), "no part left beside it")

    def test_a_maker_killed_part_way_leaves_no_item_and_the_next_test_makes_it_whole_without_waiting(self):
        killed = self.ask("killed.bin", pause=60)
        wait_until(self.made_by, timeout=30, interval=0.05, what="the maker to start")
        processes.kill_tree(killed.pid)
        killed.wait(timeout=30)
        parts = self.kept_items()
        self.assertEqual(1, len(parts), "the half-made part, under a name of its own")
        self.assertIn("-part-", parts[0])
        started = time.monotonic()
        path = corpus.made(os.path.join(self.folder, "next.bin"), ["slow"], making([]), keep=True)
        self.assertLess(time.monotonic() - started, 10, "the killed maker's turn went with it")
        self.assertEqual(WHOLE, bytes_of(path))

    def test_a_corpus_folder_deleted_mid_run_is_made_again_by_the_next_test_that_asks(self):
        made = []
        build = making(made)
        corpus.made(os.path.join(self.folder, "one.bin"), ["item"], build)
        self.assertTrue(corpus.remove(self.corpus))
        self.assertEqual(WHOLE, bytes_of(corpus.made(os.path.join(self.folder, "two.bin"), ["item"], build)))
        self.assertEqual(2, len(made))

    def test_a_maker_that_fails_leaves_nothing_and_says_why(self):
        def broken(target):
            with open(target, "wb") as handle:
                handle.write(b"half")
            raise RuntimeError("the writer failed")
        with self.assertRaisesRegex(RuntimeError, "the writer failed"):
            corpus.made(os.path.join(self.folder, "x.bin"), ["broken"], broken)
        self.assertEqual([], self.items())
        self.assertFalse(os.path.exists(os.path.join(self.folder, "x.bin")))


class TheRunsOfMonthsShareOneCorpus(ACorpusOfItsOwn):
    """What ExifTool and ffmpeg make is kept from one run to the next, in a folder of the machine's shared by every
    worktree; a writer upgraded makes its items again; the folder never grows past its cap."""

    def test_the_next_run_copies_a_kept_item_without_making_it_and_makes_its_own_library_again(self):
        made, ours = [], []
        corpus.made(os.path.join(self.folder, "one.bin"), ["clip"], making(made), keep=True)
        corpus.made(os.path.join(self.folder, "one.db"), ["library"], making(ours))
        next_run = os.path.join(self.home.root, "next_run")
        with mock.patch.dict(os.environ, {corpus.ENV: next_run}):
            self.addCleanup(corpus.remove, next_run)
            two = corpus.made(os.path.join(self.folder, "two.bin"), ["clip"], making(made), keep=True)
            corpus.made(os.path.join(self.folder, "two.db"), ["library"], making(ours))
        self.assertEqual((1, 2), (len(made), len(ours)), "kept: made once; the run's own: made for each run")
        self.assertEqual(WHOLE, bytes_of(two))

    def installed(self, folder, files):
        """Programs installed at `folder`: {relative path: bytes}. Returns the folder."""
        for name, body in files.items():
            path = os.path.join(folder, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(body)
        return folder

    def test_exiftool_is_known_by_its_library_not_its_launcher_and_an_upgrade_makes_its_items_again(self):
        library = os.path.join("exiftool_files", "lib", "Image", "ExifTool.pm")
        folder = self.installed(os.path.join(self.home.root, "ExifTool"), {
            "exiftool.exe": b"launcher", library: b"package Image::ExifTool;\n$VERSION = '13.59';\n"})
        tool, made = os.path.join(folder, "exiftool.exe"), []
        corpus.made(os.path.join(self.folder, "one.jpg"), ["photo"], making(made), tool, keep=True)
        later = time.time() + 60
        os.utime(tool, (later, later))   # the launcher copied again by the installer: the same program
        corpus.made(os.path.join(self.folder, "two.jpg"), ["photo"], making(made), tool, keep=True)
        self.assertEqual(1, len(made))
        self.installed(folder, {library: b"package Image::ExifTool;\n$VERSION = '13.60';\n"})
        corpus.made(os.path.join(self.folder, "three.jpg"), ["photo"], making(made), tool, keep=True)
        self.assertEqual(2, len(made), "a new ExifTool, its launcher as it was: made again")

    @unittest.skipUnless(os.name == "nt", "the fake ffmpeg is a Windows command file")
    def test_ffmpeg_is_known_by_its_version_answer_and_an_upgrade_makes_its_items_again(self):
        folder = self.installed(os.path.join(self.home.root, "FFmpeg"), {"ffmpeg.cmd": b"@echo ffmpeg version 9.0.2\r\n"})
        tool, made = os.path.join(folder, "ffmpeg.cmd"), []
        corpus.made(os.path.join(self.folder, "one.mp4"), ["clip"], making(made), tool, keep=True)
        later = time.time() + 60
        os.utime(tool, (later, later))   # installed again, the same version
        corpus.made(os.path.join(self.folder, "two.mp4"), ["clip"], making(made), tool, keep=True)
        self.assertEqual(1, len(made))
        self.installed(folder, {"ffmpeg.cmd": b"@echo ffmpeg version 9.1.0\r\n"})
        corpus.made(os.path.join(self.folder, "three.mp4"), ["clip"], making(made), tool, keep=True)
        self.assertEqual(2, len(made), "a new ffmpeg: made again")

    def test_past_its_cap_the_items_used_least_recently_go_and_a_part_a_killed_maker_left_with_them(self):
        made = []
        paths = {name: corpus.made(os.path.join(self.folder, name + ".bin"), [name], making(made), keep=True)
                 for name in ("harbour", "jetty", "slipway")}
        items = {name: corpus.item([name], ext=".bin", keep=True) for name in paths}
        hour = 3600
        for name, age in (("harbour", 3 * hour), ("jetty", 2 * hour), ("slipway", 0)):
            os.utime(items[name], (time.time() - age, time.time() - age))
        stale, fresh = (os.path.join(corpus.kept(), "abc-part-%d.bin" % n) for n in (1, 2))
        for part, age in ((stale, hour), (fresh, 0)):
            with open(part, "wb") as handle:
                handle.write(b"half")
            os.utime(part, (time.time() - age, time.time() - age))
        corpus.made(os.path.join(self.folder, "again.bin"), ["harbour"], making(made), keep=True)   # harbour used
        with mock.patch.object(corpus, "CAP", int(3.8 * len(WHOLE))):
            corpus.made(os.path.join(self.folder, "pier.bin"), ["pier"], making(made), keep=True)
        self.assertEqual(4, len(made))
        self.assertEqual([False, True, True], [os.path.exists(items[n]) for n in ("jetty", "harbour", "slipway")],
                         "the one used least recently went, until the rest are under 80% of the cap")
        self.assertEqual((False, True), (os.path.exists(stale), os.path.exists(fresh)), "a maker's part, until STALE")
        self.assertEqual(WHOLE, bytes_of(corpus.made(os.path.join(self.folder, "back.bin"), ["jetty"], making(made),
                                                     keep=True)))
        self.assertEqual(5, len(made), "asked for again, made again")


@unittest.skipIf(EXIFTOOL is None or TOOLS.problem, "ExifTool or ffmpeg not installed")
class EveryRecipeIsItsBytes(unittest.TestCase):

    def test_every_recipe_made_twice_into_two_empty_corpus_folders_holds_the_same_bytes(self):
        home = own_home.for_test(self, prefix="corpus_twice_")
        made = []
        for n in range(2):
            where = os.path.join(home.root, str(n))
            run, kept = os.path.join(where, "run"), os.path.join(where, "kept")
            with mock.patch.dict(os.environ, {corpus.ENV: run, corpus.CACHE_ENV: kept}):
                made.append({name: bytes_of(corpus.of(name, os.path.join(where, "copies"), EXIFTOOL, TOOLS.ffmpeg))
                             for name in corpus.ITEMS})
            for folder in (run, kept):
                self.assertTrue(corpus.remove(folder))
        self.assertEqual([], [name for name in corpus.ITEMS if made[0][name] != made[1][name]],
                         "a recipe made twice made two files")
        self.assertEqual(len(corpus.ITEMS), len(set(made[0].values())), "two recipes made one file")


class ALibraryCopiedForEachTest(ACorpusOfItsOwn):

    def test_a_library_copied_is_current_holds_nothing_beside_it_and_is_its_own(self):
        one = corpus.library(self.home.library("one.db"))
        two = corpus.library(self.home.library("two.db"))
        self.assertEqual(1, len(self.items()), "one library made, nothing beside it: no -wal, no -shm")
        self.assertEqual([], schema.ensure(one), "a copy is a current library")
        db.write_with_connection(one, lambda conn: conn.execute("INSERT INTO settings (key, value) VALUES ('k', 'v')"))
        with db.connect(db.readonly_uri(two), uri=True) as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0], "a write to one is its own")


if __name__ == "__main__":
    unittest.main()
