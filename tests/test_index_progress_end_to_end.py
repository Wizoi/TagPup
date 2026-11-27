"""The server's progress bar, end to end: the real `tagpup_cli.py index` in a process, read by
tagpup.services.indexing.index_folder as the server reads it.

The progress is the CLI's facts, one a line on its stdout (tagpup.core.progress_line), and every
test of the reader feeds it lines made by the builders the CLI uses; a change to what the CLI says
would leave a bar stuck at 0% with all of them green. Here the CLI is run for three small generated photos in a home of its own, its models stood in for by a
sitecustomize the child imports (the weights refuse under test; the protocol -- arguments,
environment, exit code, stdout -- is the real one). The library is a new one, so on the new face model (SCRFD and
AdaFace, validation.SCRFD_ADAFACE): the stand-in is that model's. FaceNet's stood in here until the new models became
every library's baseline, and the refused load then failed each photo, logged, and the run said nothing was added.

The index recorded photos, so the CLI matches the faces it made after it (the stand-in finds none), saying so on the
fact whose sentence the server ends its message with (owner, DECISIONS 2026-10-23), and no fact's text and no log line
reaches the bar. Then the same folder is added again: nothing changed on disk, so the CLI reads no file, says the index
is current, and says nothing of faces. Run by hand, the same CLI prints no fact: the person's words, and each bake folder
line once (#2169: the log's copy and the console's).

Lifted: test_functional_cli (which built exiftool.ExifToolHelper and called sqlite3.connect itself, against the rules, and
tested the same two runs): every photo is indexed, tagged or not (three untagged photos), and an index again reads nothing.
Mutation proof: tagpup_cli.py's skip rule made `images_to_process = all_images` fails the second half.

A sync that finds many folders queues them as one job, one indexer for them all (#2149: 388 folders on photo_index). On the
command line they passed Windows's 32,767 characters and the indexer never started; the server hands them over in a file
(`index --folders-file`), and 400 folders of 120 characters each, some spelled beyond ASCII, reach the indexer whole.
Failed on the old code: `FileNotFoundError: [WinError 206]` from processes.start.

The owner watches a sync's new folders being indexed and baked (#1915: a 3 h bake showed nothing but the indexer's last
line, at 100%): the bake says each folder as it begins (bake_after_index.folder_told), and the Activity page's Now reads
"Bake folder 2 of 3", the folder's path whole and the percent of the folders done, while the run is at its second folder.
The indexer is a stand-in child that prints the real facts -- and a fact cut short and one of a kind no reader knows
among them, passed over -- and holds at the second folder until the test lets it go. Failed on the old code: the line was cut at 90
characters and the bar stayed where the last indexer bar left it.

A folder of many photos is baked a chunk at a time, and the queue's `progressed` moves as each chunk ends, not only as the
folder begins (photo_index, 2026-10-28: one folder of 759 files baked over 57 minutes with no line, 152 of 2,564
folders over the page's 5 minutes): the real CLI over five photos, its chunks two files (batches.CHUNK patched in the
child), each line the queue hears stamped by a clock that moves. Failed on the old code: one line for the folder, one
`progressed`, and no `files_done` told to the run's row.

The server goes away mid-index (a repo-run server restarted on a .py save, #1564): the real CLI is held at its first
photo, the read end of its pipe is closed after its first fact, and it goes on to its end -- its photos recorded, the
faces it made matched, its folder baked, exit status 0 -- saying nothing more; the next index finds the folder current.
Failed on the old code: the first person's line after the closed pipe ("Took the graphics card.") raised OSError 22 and
the run ended (status 120: its traceback could not be written either) before the matching and the bake (the
fact's own print alone was guarded).
"""
import itertools
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from waiting import wait_until  # noqa: E402

from click.testing import CliRunner  # noqa: E402
from PIL import Image  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core import processes  # noqa: E402
from tagpup.core import progress_line  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.services import bake_after_index, indexing, sync  # noqa: E402
from tagpup.services import job_runs as runs_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db, job_runs  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web.lifecycle import Lifecycle  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXIFTOOL = own_home.installed_exiftool()

#: What the child runs before the CLI does: the models answer, nothing loads.
SITECUSTOMIZE = '''
import sys
sys.path.insert(0, %(root)r)
from unittest import mock
from tagpup.ml import clip, scrfd_adaface
mock.patch.object(clip.ClipModel, "_init_model").start()
mock.patch.object(clip.ClipModel, "picture_input").start()
mock.patch.object(clip.ClipModel, "embed_input", return_value=[0.1] * 8).start()
mock.patch.object(scrfd_adaface.ScrfdAdaFace, "_init_models").start()
mock.patch.object(scrfd_adaface.ScrfdAdaFace, "missing", return_value=None).start()
mock.patch.object(scrfd_adaface.ScrfdAdaFace, "detect_and_embed", return_value=[]).start()
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
        # Each bar the CLI draws is read as "label: N% (done/total)", at its own percent.
        self.assertEqual(sorted(percents), percents, heard)
        self.assertGreaterEqual(max(percents), 50, heard)
        self.assertTrue(any(indexing.GENERATING_EMBEDDINGS + ": 100% (3/3)" == message for message, _p in heard), heard)
        # Neither a fact's text nor a log line reaches the bar: the log is the run's file alone.
        self.assertFalse([message for message, _p in heard
                          if progress_line.PREFIX.strip() in message or "[INFO]" in message or "%|" in message], heard)
        self.assertEqual(100, result.details["percent"])
        self.assertEqual("Folder indexed. No new face is waiting for a name. Bake: 3 file(s) written, 0 already held it.",
                         result.details["message"])
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
        self.assertFalse(any(indexing.GENERATING_EMBEDDINGS in (message or "") for message, _p in heard_again), heard_again)
        self.assertEqual("Folder indexed: no photo was added or read again.", again.details["message"])

    def test_the_phases_the_cli_draws_reach_the_run_s_row_through_the_queue(self):
        """#2191: the bars' labels were copied into the reader; the real CLI's, through the server's queue, reach the
        row of the run the queue claimed: its metadata and embeddings phases are told with their counts, and the
        photos it read are in the row's counts."""
        home = own_home.for_test(self, "index_phase_")
        shim = os.path.join(home.root, "shim")
        os.makedirs(shim)
        with open(os.path.join(shim, "sitecustomize.py"), "w", encoding="utf-8") as handle:
            handle.write(SITECUSTOMIZE % {"root": ROOT})
        folder = os.path.join(home.root, "Regatta")
        os.makedirs(folder)
        for name in ("one.jpg", "two.jpg", "three.jpg"):
            Image.new("RGB", (64, 64), "navy").save(os.path.join(folder, name), "JPEG")
        library_actions.create(home.library("harbour.db"))
        library = Library(home.library("harbour.db"))
        queue = indexing_jobs.IndexQueue(library.name, library)
        told = []
        real = runs_service.progress

        def spy(lib, run_id, counts, phase, now=None):
            told.append((phase, dict(counts)))
            return real(lib, run_id, counts, phase, now)
        saved = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = shim
        try:
            with mock.patch.object(runs_service, "progress", side_effect=spy):
                self.assertTrue(queue.start([folder], lambda folders, report: indexing.index_folder(
                    library, folders, ROOT, report), together=True).ok)
                queue.wait()
        finally:
            if saved is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = saved
        self.assertEqual("completed", queue.status(folder)["status"], queue.status(folder))
        phases = [phase for phase, _counts in told]
        self.assertEqual(["metadata", "embeddings", "faces", "bake"],
                         [phase for phase, _each in itertools.groupby(phases)], told)
        last = max(index for index, phase in enumerate(phases) if phase == "embeddings")
        self.assertEqual({"done": 3, "of": 3, "percent": 100}, dict(told[last][1]))
        [run] = [each for each in runs_service.tree(library) if each.job == runs_service.INDEX]
        self.assertEqual(job_runs.DONE, run.outcome)
        self.assertEqual(3, run.changed["photos_read"])
        self.assertIsNotNone(run.progress, "the run's row says how far it got")

    def test_an_index_whose_every_photo_failed_says_so_and_not_that_the_folder_is_current(self):
        """#1995: the CLI's per-photo loop logs a failure and goes on, exits 0 having recorded nothing; the message told
        the owner no photo was added or read again, as for a folder that is current."""
        home = own_home.for_test(self, "index_failed_")
        shim = os.path.join(home.root, "shim")
        os.makedirs(shim)
        with open(os.path.join(shim, "sitecustomize.py"), "w", encoding="utf-8") as handle:
            handle.write(SITECUSTOMIZE % {"root": ROOT}
                         + "mock.patch.object(clip.ClipModel, 'embed_input', side_effect=RuntimeError('refused')).start()\n")
        folder = os.path.join(home.root, "Regatta")
        os.makedirs(folder)
        for name in ("one.jpg", "two.jpg", "three.jpg"):
            Image.new("RGB", (64, 64), "navy").save(os.path.join(folder, name), "JPEG")
        library = Library(home.library("harbour.db"))
        saved = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = shim
        try:
            result = indexing.index_folder(library, folder, ROOT, report=lambda message=None, percent=None: None)
        finally:
            if saved is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = saved
        self.assertTrue(result.ok, result.errors)
        self.assertTrue(result.details["message"].startswith("3 photo(s) failed and were not indexed"),
                        result.details["message"])
        self.assertEqual(3, result.details["failed_photos"])

    def test_run_by_hand_it_prints_words_and_each_bake_folder_once(self):
        """The owner runs `TagPup CLI.cmd` by hand: no fact reaches the console, the person's lines do, and each bake
        folder's line is printed once (#2169: the log's copy and the console's, twice)."""
        home = own_home.for_test(self, "index_by_hand_")
        shim = os.path.join(home.root, "shim")
        os.makedirs(shim)
        with open(os.path.join(shim, "sitecustomize.py"), "w", encoding="utf-8") as handle:
            handle.write(SITECUSTOMIZE % {"root": ROOT})
        folder = os.path.join(home.root, "Regatta")
        os.makedirs(folder)
        for name in ("one.jpg", "two.jpg", "three.jpg"):
            Image.new("RGB", (64, 64), "navy").save(os.path.join(folder, name), "JPEG")
        env = {name: value for name, value in os.environ.items() if name != progress_line.ASKED}
        env.update(PYTHONPATH=shim, NO_COLOR="1", TERM="dumb", PYTHONIOENCODING="utf-8")
        ran = processes.run([sys.executable, "tagpup_cli.py", "--db", home.library("harbour.db"), "index", folder],
                            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=300)
        self.assertEqual(0, ran.returncode, ran.stdout + ran.stderr)
        printed = ran.stdout.splitlines()
        self.assertFalse([line for line in printed if progress_line.PREFIX.strip() in line], ran.stdout)
        self.assertEqual(1, len([line for line in printed if bake_after_index.FOLDER_SAID + "1 of 1" in line]), ran.stdout)
        self.assertIn(indexing.NEW_FACES + "No new face is waiting for a name.", printed)
        self.assertIn("Bake: 3 file(s) written, 0 already held it.", printed)


#: Added to SITECUSTOMIZE: the photo's embedding waits for the file %(release)s, and the matching of new faces leaves a
#: file beside it when it runs.
HELD_AT_THE_FIRST_PHOTO = '''
import os, time
from tagpup.services import faces as _faces
def _embed(*args, **kwargs):
    deadline = time.time() + 60
    while not os.path.exists(%(release)r) and time.time() < deadline:
        time.sleep(0.05)
    return [0.1] * 8
mock.patch.object(clip.ClipModel, "embed_input", side_effect=_embed).start()
_automatch_new = _faces.automatch_new
def _matching(*args, **kwargs):
    open(%(release)r + ".matched", "w").close()
    return _automatch_new(*args, **kwargs)
mock.patch.object(_faces, "automatch_new", side_effect=_matching).start()
'''


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class TheServerGoesMidIndex(unittest.TestCase):
    def test_a_pipe_whose_reader_is_gone_leaves_the_index_to_its_end_and_the_next_one_finds_it_current(self):
        home = own_home.for_test(self, "index_server_gone_")
        release = os.path.join(home.root, "RELEASE")
        shim = os.path.join(home.root, "shim")
        os.makedirs(shim)
        with open(os.path.join(shim, "sitecustomize.py"), "w", encoding="utf-8") as handle:
            handle.write(SITECUSTOMIZE % {"root": ROOT} + HELD_AT_THE_FIRST_PHOTO % {"release": release})
        folder = os.path.join(home.root, "Regatta")
        os.makedirs(folder)
        for name in ("one.jpg", "two.jpg", "three.jpg"):
            Image.new("RGB", (64, 64), "navy").save(os.path.join(folder, name), "JPEG")
        before = {}
        for name in os.listdir(folder):
            with open(os.path.join(folder, name), "rb") as handle:
                before[name] = handle.read()
        library = Library(home.library("harbour.db"))
        env = dict(os.environ, PYTHONPATH=shim, NO_COLOR="1", TERM="dumb")
        progress_line.ask(env)
        child = processes.start([sys.executable, "tagpup_cli.py", "--db", library.path, "index", folder], cwd=ROOT,
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace")
        try:
            heard = []
            for line in iter(child.stdout.readline, ""):
                heard.append(line)
                if progress_line.heard(line):
                    break
            self.assertTrue(heard and progress_line.heard(heard[-1]), heard)
            child.stdout.close()   # the server is gone: the pipe has no reader
            open(release, "w").close()
            ended = child.wait(timeout=300)
        finally:
            if child.poll() is None:
                processes.kill_tree(child.pid)
        self.assertEqual(0, ended, "the index went on to its end")
        self.assertTrue(os.path.exists(release + ".matched"), "the faces it made were matched")
        for name, held in before.items():
            with open(os.path.join(folder, name), "rb") as handle:
                self.assertNotEqual(held, handle.read(), "%s was baked" % name)
        conn = db.connect(library.path)
        try:
            self.assertEqual(3, conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0])
        finally:
            conn.close()
        saved = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = shim
        try:
            again = indexing.index_folder(library, folder, ROOT, report=lambda message=None, percent=None: None)
        finally:
            if saved is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = saved
        self.assertTrue(again.ok, again.errors)
        self.assertEqual("Folder indexed: no photo was added or read again.", again.details["message"])


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class TheOwnerWatchesALargeFolderBaked(unittest.TestCase):
    def test_progressed_moves_as_each_chunk_of_the_folders_files_is_written(self):
        home = own_home.for_test(self, "bake_chunks_")
        shim = os.path.join(home.root, "shim")
        os.makedirs(shim)
        with open(os.path.join(shim, "sitecustomize.py"), "w", encoding="utf-8") as handle:
            handle.write(SITECUSTOMIZE % {"root": ROOT}
                         + "from tagpup.services import batches\nbatches.CHUNK = 2\n")
        folder = os.path.join(home.root, "Regatta")
        os.makedirs(folder)
        for number in range(5):
            Image.new("RGB", (64, 64), (40 * number, 90, 160)).save(os.path.join(folder, "heat %d.jpg" % number), "JPEG")
        library_actions.create(home.library("harbour.db"))
        library = Library(home.library("harbour.db"))
        queue = indexing_jobs.IndexQueue(library.name, library)
        # The queue's clock moves a minute at each look: each report it hears is a `progressed` of its own.
        ticks = itertools.count()
        clock = mock.Mock(time=lambda: time.time() + 60 * next(ticks))
        heard, told = [], []
        real = runs_service.progress

        def spy(lib, run_id, counts, phase, now=None):
            told.append((phase, dict(counts)))
            return real(lib, run_id, counts, phase, now)

        def index(folders, report):
            def hearing(message=None, percent=None):
                report(message, percent)
                heard.append((message, percent, queue.status(folder).get("progressed")))
            return indexing.index_folder(library, folders, ROOT, hearing)
        saved = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = shim
        try:
            with mock.patch.object(indexing_jobs, "time", clock), \
                    mock.patch.object(runs_service, "progress", side_effect=spy):
                self.assertTrue(queue.start([folder], index, together=True).ok)
                queue.wait()
        finally:
            if saved is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = saved
        self.assertEqual("completed", queue.status(folder)["status"], queue.status(folder))
        in_the_folder = [(message, percent, progressed) for message, percent, progressed in heard
                         if (message or "").startswith(bake_after_index.FOLDER_SAID + "1 of 1")]
        self.assertEqual(3, len({progressed for _m, _p, progressed in in_the_folder}), ("progressed moved within the folder",
                                                                                         heard))
        self.assertEqual([False, True, True], [", %d of 5 of its files reached: " % (2 * n) in message
                                               for n, (message, _p, _at) in enumerate(in_the_folder)], heard)
        self.assertEqual([50, 70, 90], [percent for _m, percent, _at in in_the_folder])
        self.assertEqual([(2, 5), (4, 5)], [(counts["files_done"], counts["files"]) for phase, counts in told
                                            if phase == "bake" and "files_done" in counts])


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class ASyncOfManyFolders(unittest.TestCase):
    """#2149: a sync's catch-up of a few hundred folders is one job, one indexer, the models loaded once."""

    def test_four_hundred_long_folders_reach_one_indexer_whole_and_its_list_is_gone_after(self):
        home = own_home.for_test(self, "index_many_")
        shim = os.path.join(home.root, "shim")
        os.makedirs(shim)
        with open(os.path.join(shim, "sitecustomize.py"), "w", encoding="utf-8") as handle:
            handle.write(SITECUSTOMIZE % {"root": ROOT})
        width = 120 - len(home.root) - 1
        folders = []
        for number in range(400):
            # Spelled beyond ASCII in some: the list is UTF-8 both ways. (Within the console's code page: the indexer
            # prints each folder it scans, and one outside it stops the child before the list is reached; no live folder
            # is spelled so.)
            name = ("Regatta heat %03d %s " % (number, "Côte d'Azur" if number % 3 == 0 else "harbour")).ljust(width, "x")
            folders.append(os.path.join(home.root, name))
            os.makedirs(folders[-1])
        self.assertGreater(sum(len(folder) + 3 for folder in folders), 32767, "the command line would have held them")
        # A photo in the first and the last: the index records its folders as added only when it found a photo.
        for folder in (folders[0], folders[-1]):
            Image.new("RGB", (64, 64), "navy").save(os.path.join(folder, "one.jpg"), "JPEG")
        library = Library(home.library("harbour.db"))
        lists_before = {name for name in os.listdir(tempfile.gettempdir()) if name.startswith("tagpup-folders-")}
        saved = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = shim
        try:
            result = indexing.index_folder(library, folders, ROOT, report=lambda message=None, percent=None: None,
                                           subfolders=False)
        finally:
            if saved is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = saved
        self.assertTrue(result.ok, result.errors)
        conn = db.connect(library.path)
        try:
            added = {row[0]: row[1] for row in conn.execute("SELECT path, subfolders FROM added_folders")}
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0])
        finally:
            conn.close()
        # Every folder, spelled as stored, and the options after the list still heard.
        self.assertEqual({paths.stored(folder): 0 for folder in folders}, added)
        lists_after = {name for name in os.listdir(tempfile.gettempdir()) if name.startswith("tagpup-folders-")}
        self.assertEqual(set(), lists_after - lists_before, "the list of folders was left behind")

    def test_a_list_that_cannot_be_read_is_a_sentence_and_no_index(self):
        home = own_home.for_test(self, "index_no_list_")
        library = home.library("harbour.db")
        missing = os.path.join(home.root, "folders.txt")
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", library, "index", "--folders-file", missing])
        self.assertEqual(2, result.exit_code, result.output)
        self.assertIsInstance(result.exception, SystemExit, result.output)
        self.assertIn("The list of folders could not be read", result.output)
        self.assertFalse(os.path.exists(library), "an index ran")
        # Nor a list that is not UTF-8.
        with open(missing, "wb") as handle:
            handle.write(b"C:\\Pictures\\R\xe9gate\n")
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", library, "index", "--folders-file", missing])
        self.assertEqual(2, result.exit_code, result.output)
        self.assertIn("The list of folders could not be read", result.output)
        # And no folder at all is refused as before.
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", library, "index"])
        self.assertEqual(2, result.exit_code, result.output)
        self.assertIn("Missing argument", result.output)


#: The indexer a sync's new folders are handed to, stood in for: the lines the real one prints, from the code that prints
#: them, its folders read from the list the server hands over, held at the second folder until RELEASE appears beside it.
STUB_CLI = '''
import os, sys, time
sys.path.insert(0, %(root)r)
from tagpup.core import progress_line
from tagpup.services import bake_after_index, indexing
args = sys.argv[1:]
with open(args[args.index("--folders-file") + 1], encoding="utf-8") as handle:
    folders = handle.read().splitlines()
print(progress_line.say(indexing.phase_fact("metadata", 3, 3)), flush=True)
print(progress_line.say(bake_after_index.folder_told(2, 3, bake_after_index.Baked(), "nowhere"))[:40], flush=True)
print(progress_line.PREFIX + '{"kind":"dance","sentence":"Bake folder 1 of 0"}', flush=True)
baked = bake_after_index.Baked()
for number, folder in enumerate(folders, 1):
    print(progress_line.say(bake_after_index.folder_told(number, len(folders), baked, folder)), flush=True)
    if number == 2:
        release = os.path.join(os.path.dirname(os.path.abspath(__file__)), "RELEASE")
        deadline = time.time() + 60
        while not os.path.exists(release) and time.time() < deadline:
            time.sleep(0.05)
    baked.written += 1
print(progress_line.say(baked.fact()), flush=True)
'''


class TheOwnerWatchesASyncsNewFoldersIndexedAndBaked(unittest.TestCase):
    def test_now_reads_the_bakes_folder_its_path_and_how_far_while_it_runs(self):
        home = own_home.for_test(self, "bake_seen_")
        stub = os.path.join(home.root, "stub")
        os.makedirs(stub)
        with open(os.path.join(stub, "tagpup_cli.py"), "w", encoding="utf-8") as handle:
            handle.write(STUB_CLI % {"root": ROOT})
        pictures = os.path.join(home.root, "Pictures")
        folders = [os.path.join(pictures, name) for name in ("Regatta", "Harbour Walk", "Lighthouse")]
        library_actions.create(home.library("harbour.db"))
        library = Library(home.library("harbour.db"))
        conn = db.connect(library.path)
        try:
            for folder in folders:
                os.makedirs(folder)
                Image.new("RGB", (32, 32), "navy").save(os.path.join(folder, "held.jpg"), "JPEG")
                photo_rows.add_indexed(conn, os.path.join(folder, "held.jpg"), {})
            conn.commit()
        finally:
            conn.close()
        for folder in folders:
            Image.new("RGB", (32, 32), "teal").save(os.path.join(folder, "new.jpg"), "JPEG")

        runtime = runtimes.Runtime()      # the server's: the page reads its queues
        queue = runtime.index_queues.of(library)
        heard = []

        def index(each, report):
            def told(message=None, percent=None):
                heard.append((message, percent))
                report(message, percent)
            return indexing.index_folder(library, each, stub, told, subfolders=False)

        synced = sync.sync(library, apply=True, roots=[pictures],
                           queue=lambda new: queue.start(new, index, together=True))
        self.assertTrue(synced.ok, synced.message())
        self.assertEqual(3, synced.details["queued"])
        self.addCleanup(queue.wait)
        self.addCleanup(lambda: open(os.path.join(stub, "RELEASE"), "w").close())

        app = web.create_app("tagpup", runtime=runtime,
                             lifecycle=Lifecycle(background=runtimes.Background([]), runtime=runtime))
        app.testing = True
        client = app.test_client()
        def baking():
            reply = client.get("/api/activity/now").get_json()
            found = next(each for each in reply["libraries"] if each["name"] == "harbour")["indexing"]["running"]
            return found if found and found["message"].startswith("Bake folder 2 of 3") else None
        running = wait_until(baking, timeout=60, interval=0.05, what="the bake of folder 2 of 3 on the page")
        said = bake_after_index.FOLDER_SAID + "2 of 3, 1 written, 0 current, 0 conflicts: "
        self.assertIn(running["message"], [said + paths.stored(folder) for folder in folders], "the folder's path, whole")
        self.assertEqual(66, running["percent"], "one folder of three is done, in the bar's second half")
        self.assertLessEqual(running["started"], running["progressed"], "when it last moved, for 'last progress'")

        open(os.path.join(stub, "RELEASE"), "w").close()
        queue.wait()
        self.assertEqual("completed", queue.status(folders[0])["status"])
        # The fact cut short and the one of no kind a reader knows were passed over; nothing raised.
        self.assertFalse([message for message, _p in heard if progress_line.PREFIX.strip() in message
                          or "1 of 0" in message], heard)
        self.assertEqual([50, 66, 83], [percent for message, percent in heard if message.startswith("Bake folder")])


if __name__ == "__main__":
    unittest.main()
