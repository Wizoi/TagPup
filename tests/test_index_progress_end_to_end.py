"""The server's progress bar, end to end: the real `tagpup_cli.py index` in a process, read by
tagpup.services.indexing.index_folder as the server reads it.

The progress text is scraped from the CLI's stdout (summarize_indexer_line), and every
test of the reader fed it a line a person had typed; a change to what the CLI prints (a tqdm
version, a console line) would leave a bar stuck at 0% with all of them green. Here the CLI is
run for three small generated photos in a home of its own, its models stood in for by a
sitecustomize the child imports (the weights refuse under test; the protocol -- arguments,
environment, exit code, stdout -- is the real one).

The index recorded photos, so the CLI clusters the library's faces after it, saying so on the line the server reads
(owner, DECISIONS 2026-10-16). Then the same folder is added again: nothing changed on disk, so the CLI reads no file,
says the index is current, and clusters nothing.

Lifted: test_functional_cli (which built exiftool.ExifToolHelper and called sqlite3.connect itself, against the rules, and
tested the same two runs): every photo is indexed, tagged or not (three untagged photos), and an index again reads nothing.
Mutation proof: tagpup_cli.py's skip rule made `images_to_process = all_images` fails the second half.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from PIL import Image  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import indexing  # noqa: E402
from tagpup.store import db  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXIFTOOL = own_home.installed_exiftool()

#: What the child runs before the CLI does: the models answer, nothing loads.
SITECUSTOMIZE = '''
import sys
sys.path.insert(0, %(root)r)
from unittest import mock
from tagpup.ml import clip, faces
mock.patch.object(clip.ClipModel, "_init_model").start()
mock.patch.object(clip.ClipModel, "embed_picture", return_value=[0.1] * 8).start()
mock.patch.object(faces.FaceModel, "_init_models").start()
mock.patch.object(faces.FaceModel, "detect_and_embed_faces", return_value=[]).start()
'''


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class TheBarReachesTheEnd(unittest.TestCase):
    def test_the_scraped_percent_reaches_100_the_final_message_is_the_cli_runs_and_an_index_again_reads_nothing(self):
        home = own_home.for_test(self, "index_progress_")
        shim = os.path.join(home.root, "shim")
        os.makedirs(shim)
        with open(os.path.join(shim, "sitecustomize.py"), "w", encoding="utf-8") as handle:
            handle.write(SITECUSTOMIZE % {"root": ROOT})
        folder = os.path.join(home.root, "Regatta")
        os.makedirs(folder)
        for name in ("one.jpg", "two.jpg", "three.jpg"):
            Image.new("RGB", (64, 64), "navy").save(os.path.join(folder, name), "JPEG")
        library = Library(home.library("harbour.db"))

        def index():
            heard = []
            saved = os.environ.get("PYTHONPATH")
            os.environ["PYTHONPATH"] = shim
            try:
                result = indexing.index_folder(library, folder, ROOT,
                                               report=lambda message=None, percent=None: heard.append((message, percent)))
            finally:
                if saved is None:
                    os.environ.pop("PYTHONPATH", None)
                else:
                    os.environ["PYTHONPATH"] = saved
            return result, heard
        result, heard = index()

        self.assertTrue(result.ok, (result.errors, heard))
        percents = [percent for _message, percent in heard if percent is not None]
        # Each bar the CLI draws is read as "label: N% (done/total)", and the last one stands at 100 of 90.
        self.assertIn(90, percents, heard)
        self.assertTrue(any("Generating embeddings: 100% (3/3)" == message for message, _p in heard), heard)
        self.assertEqual(100, result.details["percent"])
        self.assertIn((indexing.CLUSTERING_STARTS, indexing.CLUSTERING_PERCENT), heard)
        self.assertEqual("Folder indexed and face identities resolved.", result.details["message"])
        conn = db.connect(library.path)
        try:
            # Every scanned photo is indexed, tagged or not, and as genuinely untagged (no borrowed metadata).
            self.assertEqual(3, conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0])
            self.assertEqual(3, conn.execute("SELECT COUNT(*) FROM photos WHERE COALESCE(tags, '[]') = '[]'").fetchone()[0])
        finally:
            conn.close()
        # The per-photo write locks are beside the library, not under the working directory: a server's indexer and one
        # started by hand from anywhere else must lock in one place (the child runs from the code's folder, not the home).
        self.assertTrue(os.path.isdir(os.path.join(os.path.dirname(library.path), "locks")))
        again, heard_again = index()
        self.assertTrue(again.ok, (again.errors, heard_again))
        self.assertIn("All images are up to date! Index is current.", [message for message, _p in heard_again], heard_again)
        self.assertFalse(any("Generating embeddings" in (message or "") for message, _p in heard_again), heard_again)
        self.assertFalse(again.details["clustered"], heard_again)


if __name__ == "__main__":
    unittest.main()
