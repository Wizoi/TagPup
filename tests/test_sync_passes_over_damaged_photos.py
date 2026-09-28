"""Sync does not queue a photo the indexer found does not decode, while its file is
unchanged; replaced, it is queued and indexed at once (docs/findings.md, #407).

Every sync -- the folder watcher's, the catch-up at start, the daily one -- saw a damaged
file as new, since it has no row, and queued the indexer for it: a process that loaded the
photo index and CLIP (10-20 s) to fail on it again, 12 times in 8 minutes one day. Now a
sync passes it over, and when nothing else is new no indexer process is started at all.

Real files: a whole JPEG and one cut short (tests/damaged_photos). The indexer the queue
runs is the CLI's `index`, with real ExifTool and Pillow and only the CLIP model stood in
for after the decode.
"""
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import damaged_photos  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from click.testing import CliRunner  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.ml.clip import output_dim  # noqa: E402
from tagpup.services import damaged_photos as damaged  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.services import sync  # noqa: E402
from tagpup.store import db  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
THEN = 1_700_000_000


def vector():
    return [0.1] * (output_dim(library_settings.DEFAULTS["model.name"]) or 512)


class Case(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="sync_damaged_")
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.library = Library(self.db_path)
        self.folder = os.path.join(self.home.root, "Harbour")
        indexed = os.path.join(self.folder, "jetty.jpg")
        damaged_photos.whole_jpeg(indexed)
        os.utime(indexed, (THEN, THEN))
        conn = db.connect(self.db_path)
        try:
            photo_rows.add_read(conn, indexed, {})
            conn.commit()
        finally:
            conn.close()
        self.cut = damaged_photos.truncated(os.path.join(self.folder, "cut short.jpg"))
        os.utime(self.cut, (THEN, THEN))

    def rows(self):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return sorted(os.path.basename(path) for (path,) in conn.execute("SELECT path FROM photos"))
        finally:
            conn.close()

    def found_damaged(self):
        """What the indexer records of the cut-short file, as it records it."""
        stat = os.stat(self.cut)
        damaged.remember(self.library, [(self.cut, (stat.st_mtime, stat.st_size), "truncated", "found", 0)])


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class QueuedOnce(Case):
    def index(self, folders):
        """The queue: the CLI's `index` of each folder, as the index queue runs it."""
        self.asked.append(list(folders))
        with mock.patch("tagpup.ml.clip.ClipModel._init_model"), \
                mock.patch("tagpup.ml.clip.ClipModel.embed_picture", return_value=vector()):
            for folder in folders:
                result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", folder,
                                                             "--no-subfolders", "--skip-faces"])
                self.assertEqual(0, result.exit_code, result.output)
        return Result(attempted=len(folders), changed=len(folders))

    def sync(self):
        return sync.sync(self.library, apply=True, exiftool_path=EXIFTOOL, queue=self.index)

    def test_a_damaged_file_is_queued_once_not_again_and_indexed_once_replaced(self):
        self.asked = []
        first = self.sync()
        self.assertEqual([[self.folder]], self.asked, "a new file is queued")
        self.assertEqual(["cut short.jpg"], [each["name"] for each in damaged.listed(self.library)])

        again = self.sync()
        self.assertEqual([[self.folder]], self.asked, "queued again, to fail again")
        self.assertEqual(1, again.details["counts"]["unreadable_files"])
        self.assertEqual(0, again.details["counts"]["new"])
        self.assertTrue(again.details["in_step"], "a damaged file is the owner's to restore, not sync's")
        self.assertFalse(first.details["in_step"])

        damaged_photos.whole_jpeg(self.cut, seed=11)       # restored from a backup
        self.sync()
        self.assertEqual([[self.folder]] * 2, self.asked)
        self.assertEqual(["cut short.jpg", "jetty.jpg"], self.rows())
        self.assertEqual([], damaged.listed(self.library))
        self.assertEqual([], damaged.records(self.library), "a photo read whole is forgotten")


class FakeIndexer:
    """What processes.start returns for the indexer: it prints nothing and succeeds."""

    def __init__(self, *args, **kwargs):
        self.stdout = io.StringIO("")
        self.returncode = 0

    def wait(self):
        return 0


class NoIndexerIsStarted(Case):
    """runtime.sync, as the watcher, the catch-up and the daily job call it, with this
    process's index queue."""

    def sync(self):
        self.addCleanup(indexing_jobs.forget, self.library)
        with mock.patch("tagpup.core.processes.start", side_effect=FakeIndexer) as start:
            result = runtimes.sync(self.library, apply=True)
            indexing_jobs.queue_for(self.library).wait()
        return result, start

    def test_when_the_only_new_file_is_known_damaged(self):
        self.found_damaged()
        result, start = self.sync()
        self.assertTrue(result.ok, result.message())
        self.assertEqual(0, result.details["queued"])
        start.assert_not_called()

    def test_nor_when_the_walk_spells_its_time_a_little_differently(self):
        # A record's stamp is held to the file's as every row's is (store.photos.describes,
        # 0.1 s): a walk and a stat -- on a network share above all -- need not agree to
        # the last bit, and an exact comparison queued the indexer again.
        stat = os.stat(self.cut)
        damaged.remember(self.library, [(self.cut, (stat.st_mtime + 0.05, stat.st_size), "truncated", "found", 0)])
        result, start = self.sync()
        self.assertEqual(1, result.details["counts"]["unreadable_files"])
        start.assert_not_called()
        self.assertEqual(["cut short.jpg"], [each["name"] for each in damaged.listed(self.library)])

    def test_but_one_is_when_it_is_not_known(self):
        result, start = self.sync()
        self.assertEqual(1, result.details["queued"])
        self.assertEqual(1, start.call_count)

    def test_nor_is_a_folder_holding_only_damaged_files_offered_to_review(self):
        other = damaged_photos.truncated(os.path.join(self.home.root, "Regatta", "cut short too.jpg"))
        self.found_damaged()
        stat = os.stat(other)
        damaged.remember(self.library, [(other, (stat.st_mtime, stat.st_size), "truncated", "found", 0)])
        self.assertEqual({"folders": [], "photos": 0}, sync.review(self.library, roots=[self.home.root]))


if __name__ == "__main__":
    unittest.main()
