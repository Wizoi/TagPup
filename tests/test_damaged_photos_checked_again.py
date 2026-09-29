"""A damaged photo is read again when its file is written to, whatever its stamp says, and
when the owner asks (docs/findings.md, #407).

The reviewer's case: a zero-filled copy is recorded; a good copy is laid over it keeping
the modified time -- robocopy and Explorer do -- and a zero-filled file has the good one's
size, so the stamp is the one recorded. Compared by stamp alone, the record never cleared
and the photo kept the vector and faces made from half a picture. Now the folder watcher
hands the file it was told was written to damaged_photos.check_again, as Check again
does: it reads whole, and it is indexed again for real.

Real files, real watchdog and ExifTool; the CLIP model and the face detector stood in for.
"""
import io
import itertools
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import damaged_photos  # noqa: E402
import own_home  # noqa: E402
from click.testing import CliRunner  # noqa: E402
from test_folder_watcher import Base as WatcherBase  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.ml.clip import output_dim  # noqa: E402
from tagpup.services import damaged_photos as damaged  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.services import sync  # noqa: E402
from tagpup.store import db  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
THEN = 1_700_000_000
DIM = output_dim(library_settings.DEFAULTS["model.name"]) or 512


class Detector:
    box = [1, 1, 20, 20]

    def detect_and_embed_faces(self, path):
        return [{"box": list(Detector.box), "embedding": [0.5] * 512, "prob": 0.99, "crop_image": None}]


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class TheReviewersCase(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="damaged_check_")
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.library = Library(self.db_path)
        self.folder = os.path.join(self.home.root, "Harbour")
        self.half = damaged_photos.second_half_zeros(os.path.join(self.folder, "copy stopped.jpg"))
        os.utime(self.half, (THEN, THEN))
        self.calls = itertools.count(1)
        Detector.box = [1, 1, 20, 20]
        self.index([self.folder])
        self.first = self.vector()
        # The good copy laid over it, its modified time kept: the same size, the same time.
        stamp = os.stat(self.half)
        damaged_photos.whole_jpeg(self.half, seed=13, size=(480, 360))
        os.utime(self.half, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertEqual((stamp.st_mtime, stamp.st_size), (os.stat(self.half).st_mtime, os.stat(self.half).st_size))
        Detector.box = [30, 30, 60, 60]

    def index(self, folders):
        with mock.patch("tagpup.ml.clip.ClipModel._init_model"), \
                mock.patch("tagpup.ml.clip.ClipModel.embed_picture",
                           side_effect=lambda img: [0.01 * next(self.calls)] * DIM), \
                mock.patch("tagpup.ml.faces.FaceModel", lambda **settings: Detector()):
            for folder in folders:
                result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", folder, "--no-subfolders"])
                self.assertEqual(0, result.exit_code, result.output)

    def query(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def vector(self):
        where, params = paths.sql_equals("p.path", self.half)
        return [bytes(v)[:4].hex() for (v,) in self.query(
            "SELECT e.vector FROM embeddings e JOIN photos p ON p.id = e.photo_id WHERE " + where, params)]

    def test_the_stamp_alone_never_clears_it(self):
        sync.sync(self.library, apply=True, exiftool_path=EXIFTOOL, queue=lambda folders: self.index(folders))
        self.assertEqual(["copy stopped.jpg"], [each["name"] for each in damaged.listed(self.library)])

    def test_read_again_it_is_forgotten_and_indexed_for_real(self):
        done = damaged.check_again(self.library, [self.half])
        self.assertEqual(([self.half], []), (done["whole"], done["still"]))
        self.index(done["folders"])
        self.assertEqual([], damaged.listed(self.library))
        self.assertEqual([], damaged.records(self.library))
        self.assertNotEqual(self.first, self.vector(), "the vector made from half a picture was kept")
        self.assertEqual([("[30, 30, 60, 60]",)], self.query("SELECT box FROM faces"))

    def test_a_file_still_damaged_stays_listed(self):
        damaged_photos.second_half_zeros(self.half)
        done = damaged.check_again(self.library)
        self.assertEqual(([], [self.half]), (done["whole"], done["still"]))
        self.assertEqual(1, len(damaged.listed(self.library)))


class FakeIndexer:
    def __init__(self, *args, **kwargs):
        self.stdout = io.StringIO("")
        self.returncode = 0

    def wait(self):
        return 0


class CheckAgainQueuesTheIndex(unittest.TestCase):
    """runtime.check_damaged, what Check again and the watcher call: the folder of a photo
    that reads whole goes on this process's index queue."""

    def test_one_indexer_for_what_reads_whole(self):
        home = own_home.for_test(self, prefix="damaged_queue_")
        library = Library(home.library("harbour.db"))
        library_actions.create(library.path)
        photo = damaged_photos.truncated(os.path.join(home.root, "Harbour", "cut short.jpg"))
        stat = os.stat(photo)
        damaged.remember(library, [(photo, (stat.st_mtime, stat.st_size), "truncated", "found", 0)])
        damaged_photos.whole_jpeg(photo, seed=11)
        self.addCleanup(indexing_jobs.forget, library)
        with mock.patch("tagpup.core.processes.start", side_effect=FakeIndexer) as start:
            done = runtimes.check_damaged(library)
            indexing_jobs.queue_for(library).wait()
        self.assertEqual((1, 1), (done["queued"], start.call_count))
        self.assertEqual([], damaged.records(library))


class TheWatcherHandsOverWhatWasWritten(WatcherBase):
    def test_a_file_written_over_is_handed_to_be_read_again_before_its_folders_sync(self):
        regatta = os.path.join(self.root, "Regatta")
        photo = damaged_photos.second_half_zeros(os.path.join(regatta, "copy stopped.jpg"))
        stamp = os.stat(photo)
        written = []
        watcher = watching_with(self, written)
        self.started(watcher)
        del self.synced_at[:]    # the catch-up's
        damaged_photos.whole_jpeg(photo, seed=13, size=(480, 360))
        os.utime(photo, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))    # the stamp restored
        self.wait_until(lambda: self.synced)
        self.assertIn(paths.stored(photo), [path for _name, found, _at in written for path in found])
        self.assertLessEqual(written[0][2], self.synced_at[0], "handed over only after its folder's sync")


def watching_with(case, written):
    from tagpup.files import images
    from tagpup.jobs import watching
    case.synced_at = []
    sync_first = case.sync

    def sync(library, folder):
        case.synced_at.append(time.monotonic())
        sync_first(library, folder)

    watcher = watching.Watcher(lambda: [case.library], lambda library: [case.root], sync, images.is_photo,
                               debounce=0.5, recheck=0.2, tick=0.05,
                               written=lambda library, found: written.append((library.name, found, time.monotonic())))
    case.addCleanup(watcher.stop, 10)
    return watcher


if __name__ == "__main__":
    unittest.main()
