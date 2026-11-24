"""A photo, a clip or a library a test asks for is made once per run by the real writer and copied for each test
(tests/corpus.py): the copies are private, the item is never written, and the run's eight processes, a run killed
part-way and a corpus folder deleted mid-run each still give every test a whole copy.

Each test names a corpus folder of its own (corpus.ENV, in a home of its own), never the run's: it kills makers and
deletes the folder. The photo is ExifTool's, as FilesCase.make's is; the race and the kill are timed by a maker of the
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

from tagpup.core import processes  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.store import db, schema  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
BEACH = {"XMP:Subject": ["Beach"], "IPTC:Keywords": ["Beach"]}
WHOLE = b"made " * 2000

#: A test process asking for one item, made slowly: it writes half, says so (a file named for its process), waits
#: `pause` seconds and writes the rest, then copies it to `dest`. argv: dest, pause.
ASKER = """
import os, sys, time
sys.path.insert(0, %r)
import corpus
dest, pause = sys.argv[1], float(sys.argv[2])

def build(target):
    with open(target, "wb") as handle:
        handle.write(%r[:5000])
        handle.flush()
        with open(os.path.join(os.path.dirname(dest), "making-%%d" %% os.getpid()), "w"):
            pass
        time.sleep(pause)
        handle.write(%r[5000:])

corpus.made(dest, ["slow"], build)
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
        patcher = mock.patch.dict(os.environ, {corpus.ENV: self.corpus})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(corpus.remove, self.corpus)
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
        return sorted(n for n in os.listdir(self.corpus) if not n.endswith(".lock"))


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class APhotoCopiedForEachTest(ACorpusOfItsOwn):

    def test_two_tests_copies_of_one_photo_are_their_own_and_the_photo_made_once_is_never_written(self):
        before = time.time()
        a = corpus.jpeg(os.path.join(self.folder, "a.jpg"), BEACH, EXIFTOOL)
        b = corpus.jpeg(os.path.join(self.folder, "b.jpg"), BEACH, EXIFTOOL)
        relay = corpus.jpeg(os.path.join(self.folder, "relay.jpg"), {"XMP:Subject": ["Relay"]}, EXIFTOOL)
        self.assertEqual(bytes_of(a), bytes_of(b), "one recipe, one photo: as two made by it were")
        self.assertNotEqual(bytes_of(a), bytes_of(relay), "a tag of its own makes a photo of its own")
        self.assertEqual(2, len(self.items()), "made once for each recipe")
        # A copy is a file made now: its own time, never the item's.
        self.assertGreaterEqual(os.stat(b).st_mtime, before - 2)
        made = corpus.item(["jpeg", [16, 12], [90, 110, 130], BEACH], EXIFTOOL, ".jpg")
        kept = bytes_of(made)
        with self.assertRaises(PermissionError, msg="the item is read-only"):
            open(made, "r+b").close()
        # The write under test is the real ExifTool's, on the copy.
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([a], tags={"XMP:Subject": ["Harbour"]}, params=["-overwrite_original"])
        self.assertNotEqual(kept, bytes_of(a))
        self.assertEqual(kept, bytes_of(made), "the corpus is as it was made")
        self.assertEqual(kept, bytes_of(b), "another test's copy is as it was made")


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
        self.assertEqual(1, len(self.items()), "no part left beside it")

    def test_a_maker_killed_part_way_leaves_no_item_and_the_next_test_makes_it_whole_without_waiting(self):
        killed = self.ask("killed.bin", pause=60)
        deadline = time.monotonic() + 30
        while not self.made_by() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(self.made_by(), "the maker started")
        processes.kill_tree(killed.pid)
        killed.wait(timeout=30)
        parts = self.items()
        self.assertEqual(1, len(parts), "the half-made part, under a name of its own")
        self.assertIn("-part-", parts[0])
        started = time.monotonic()
        path = corpus.made(os.path.join(self.folder, "next.bin"), ["slow"], making([]))
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
