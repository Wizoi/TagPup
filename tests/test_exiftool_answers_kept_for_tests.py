"""The tests keep ExifTool's answers and give them back without running it (scenario I16, tests/exiftool_replay.py).

ExifTool is not TagPup's: once its answer to given arguments and file bytes is known, the suite need not run it again
(owner, 2026-11-24; docs/work/3375.md). tools/run_tests.py gives each test file's process a keeper by default; this file
is the keeper's own scenarios, each with a keeper and a cache folder of its own and the real ExifTool beside it:

* a command kept before is answered with no process started, the same answer and the same bytes as the real run;
* a changed ExifTool (its version, its launcher) or a changed -config file's content is a miss, never an old answer;
* a scratch ExifTool left in the way is a miss, and fails as the real run fails;
* a failed run is never kept: a file held open (a scan, Explorer, a test's own handle) fails that run only, a kept
  answer whose bytes cannot be placed over it leaves no part file and its entry whole, and the next run hits it;
* the release gate (--real-exiftool, record) runs the product's own DocumentID mint and starts ExifTool at `with`;
* two processes asking for one command at once both get the real answer, and one whole entry is kept;
* an entry cut off part-way (a kill) is not read and is replaced; a cache folder deleted mid-run is made again;
* an answer that carries a file's own dates is never kept; the oracle's whole read of a copy is (it sets the file
  system's group aside), and a second copy's is answered from the cache with no ExifTool started;
* the DocumentID minted under a keeper repeats its sequence, so a photo given one hits on the next run;
* the kept answers stay within their cap, the entries used longest ago removed first;
* product code never replays: a fresh TagPup with the variable set has no keeper, and nothing outside tests/ sets one.

The runner never gives this file a keeper (exiftool_replay.STAYS_REAL): each test sets and clears its own.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
from tagpup.core import processes  # noqa: E402
from tagpup.files import exiftool_session, field_values, identity  # noqa: E402
from tagpup.files.exiftool_session import ExifToolFailed, ExifToolSession  # noqa: E402
from tests import exiftool_replay  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()

#: One process asking for one command, given its cache, a go file, the photo and ExifTool; prints its counts.
ASKER = """
import json, os, sys, time
sys.path.insert(0, os.getcwd())
from tests import exiftool_replay
from tagpup.files import exiftool_session
go, photo, executable = sys.argv[1:4]
keeper = exiftool_replay.Keeper(exiftool_replay.REPLAY)
exiftool_session.REPLAY = keeper
while not os.path.exists(go):
    time.sleep(0.01)
with exiftool_session.ExifToolSession(executable=executable) as et:
    et.execute("-overwrite_original", "-XMP:Subject=Trips/Coast", "-XMP:Title=Pier at dusk", photo)
print(json.dumps(dict(keeper.counts)))
"""

#: A TagPup started fresh, with the keeper's variable set: does anything give it a keeper?
FRESH = """
import sys
import tagpup_cli, tagpup_web, tagpup.runtime, tagpup.web.app
from tagpup.files import exiftool_session, identity
print(exiftool_session.REPLAY is None, identity.mint_document_id.__module__, any(
    name == "tests" or name.startswith("tests.") for name in sys.modules))
"""


def picture(path, shade=90):
    """A small JPEG as Pillow writes it: the same bytes each time for the same shade."""
    from PIL import Image
    Image.new("RGB", (48, 32), (shade, 120, 160)).save(path, "JPEG", quality=85)
    return path


@unittest.skipUnless(EXIFTOOL, "needs ExifTool")
class KeptAnswers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="tagpup_kept_answers_")
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.cache = os.path.join(self.temp, "cache")
        environ = mock.patch.dict(os.environ, {exiftool_replay.CACHE_ENV: self.cache})
        environ.start()
        self.addCleanup(environ.stop)
        os.environ.pop(exiftool_replay.ENV, None)
        was = exiftool_session.REPLAY
        self.addCleanup(setattr, exiftool_session, "REPLAY", was)
        exiftool_session.REPLAY = None
        self.pristine = picture(os.path.join(self.temp, "pristine.jpg"))

    def keeper(self, mode=exiftool_replay.REPLAY):
        keeper = exiftool_replay.Keeper(mode)
        exiftool_session.REPLAY = keeper
        return keeper

    def real(self):
        exiftool_session.REPLAY = None

    def copy(self, folder, name="pier.jpg"):
        os.makedirs(os.path.join(self.temp, folder), exist_ok=True)
        path = os.path.join(self.temp, folder, name)
        shutil.copyfile(self.pristine, path)
        return path

    def entries(self):
        return [os.path.join(folder, name) for folder, _dirs, names in os.walk(self.cache)
                for name in names if name.endswith(".entry")]

    def write(self, photo, **session):
        with ExifToolSession(executable=EXIFTOOL, **session) as et:
            et.execute("-overwrite_original", "-XMP:Subject=Trips/Coast", "-XMP:Title=Pier at dusk", photo)
            return et.running

    def bytes_of(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def work(self, folder):
        """A write in place, a write to a new file and a read, in `folder`: what each answered, the bytes left and
        whether an ExifTool process ran, with the folder spelled <F>."""
        photo = self.copy(folder)
        target = os.path.join(os.path.dirname(photo), "pier-rated.jpg")
        answers = []
        with ExifToolSession(executable=EXIFTOOL) as et:
            answers.append(et.execute("-overwrite_original", "-XMP:Subject=Trips/Coast", "-XMP:Title=Pier at dusk",
                                      photo))
            answers.append(et.execute("-m", "-o", target, "-XMP:Rating=4", photo))
            answers.append(json.loads(et.execute("-j", "-G", "-n", "-XMP:Subject", "-XMP:Title", photo)))
            running = et.running
        folder_path = os.path.dirname(photo)
        text = json.dumps(answers)
        for spelling in (folder_path, folder_path.replace("\\", "/"), json.dumps(folder_path)[1:-1]):
            text = text.replace(spelling, "<F>")
        return {"answers": json.loads(text), "photo": self.bytes_of(photo), "target": self.bytes_of(target),
                "running": running}

    def test_a_kept_command_starts_no_process_and_answers_as_exiftool_did(self):
        cold = self.keeper()
        first = self.work("first")
        self.assertEqual(cold.counts["kept"], 3, cold.why)
        self.real()
        real = self.work("real")
        warm = self.keeper()
        again = self.work("again")
        self.assertEqual((warm.counts["hits"], warm.counts["misses"], warm.counts["starts"]), (3, 0, 0), warm.why)
        self.assertFalse(again.pop("running"))
        real.pop("running")
        first.pop("running")
        self.assertEqual(again, real)
        self.assertEqual(first, real)
        self.assertIn("Trips/Coast", json.dumps(real["answers"][2]))

    def test_a_changed_exiftool_is_a_miss(self):
        # What the key holds of an install: its version as its library says it, and its launcher's and library's stamps.
        fake = os.path.join(self.temp, "fake")
        library = os.path.join(fake, "exiftool_files", "lib", "Image", "ExifTool.pm")
        os.makedirs(os.path.dirname(library))
        launcher = os.path.join(fake, "exiftool.exe")
        with open(launcher, "wb") as handle:
            handle.write(b"launcher one")
        with open(library, "w", encoding="latin-1") as handle:
            handle.write("package Image::ExifTool;\n$VERSION = '13.10';\n")
        was = exiftool_replay.tool_of(launcher)
        self.assertEqual(was[0], "13.10")
        with open(library, "w", encoding="latin-1") as handle:
            handle.write("package Image::ExifTool;\n$VERSION = '13.11';\n")
        upgraded = exiftool_replay.tool_of(launcher)
        self.assertEqual(upgraded[0], "13.11")
        with open(launcher, "wb") as handle:
            handle.write(b"launcher two, longer")
        relaunched = exiftool_replay.tool_of(launcher)
        self.assertEqual(len({was, upgraded, relaunched}), 3)
        # An answer kept under one ExifTool is not given under the next.
        keeper = self.keeper()
        for tool, photo in ((was, "a"), (was, "b"), (upgraded, "c"), (relaunched, "d")):
            with mock.patch.object(exiftool_replay, "tool_of", return_value=tool):
                self.write(self.copy(photo))
        self.assertEqual((keeper.counts["hits"], keeper.counts["misses"]), (1, 3), keeper.why)

    def test_a_changed_config_file_is_a_miss(self):
        config = os.path.join(self.temp, "tagpup.config")
        with open(config, "w", encoding="utf-8") as handle:
            handle.write("%Image::ExifTool::UserDefined = ();\n1;\n")
        keeper = self.keeper()
        self.write(self.copy("a"), config_file=config)
        self.write(self.copy("b"), config_file=config)
        self.assertEqual((keeper.counts["hits"], keeper.counts["misses"]), (1, 1), keeper.why)
        with open(config, "w", encoding="utf-8") as handle:
            handle.write("# another namespace\n%Image::ExifTool::UserDefined = ();\n1;\n")
        self.write(self.copy("c"), config_file=config)
        self.assertEqual((keeper.counts["hits"], keeper.counts["misses"]), (1, 2), keeper.why)

    def test_a_scratch_in_the_way_is_a_miss_and_fails_as_exiftool_fails(self):
        self.keeper()
        self.write(self.copy("a"))
        self.real()
        blocked = self.copy("real")
        picture(blocked + "_exiftool_tmp", shade=20)
        with self.assertRaises(ExifToolFailed) as real:
            self.write(blocked)
        keeper = self.keeper()
        blocked = self.copy("kept")
        picture(blocked + "_exiftool_tmp", shade=20)
        with self.assertRaises(ExifToolFailed) as kept:
            self.write(blocked)
        self.assertEqual((keeper.counts["hits"], keeper.counts["misses"]), (0, 1), keeper.why)
        self.assertEqual(str(kept.exception).replace(os.path.dirname(blocked), "<F>"),
                         str(real.exception).replace(os.path.dirname(blocked).replace("kept", "real"), "<F>"))
        self.assertEqual(self.bytes_of(blocked), self.bytes_of(self.pristine))

    def test_a_file_held_open_fails_that_run_only(self):
        # A photo held open by another program fails ExifTool's rename over it and a kept answer's os.replace over it
        # alike. The key cannot hold that, so a failure is never kept: it would be replayed in every later run.
        keeper = self.keeper()
        held = self.copy("held")
        with open(held, "rb"):
            with self.assertRaises(ExifToolFailed) as failed:
                self.write(held)
        self.assertIn("Error renaming temporary file", str(failed.exception))
        self.assertEqual(self.entries(), [])
        self.assertEqual(keeper.why["not kept: ExifTool failed it"], 1, keeper.why)
        good = self.copy("good")
        self.write(good)   # the same command and bytes, the file let go: run, and kept
        [entry] = self.entries()
        whole = self.bytes_of(entry)
        held = self.copy("held-again")
        with open(held, "rb"):
            with self.assertRaises(ExifToolFailed):
                self.write(held)   # a hit whose bytes cannot be placed, then the real run, which fails too
        self.assertEqual(self.bytes_of(held), self.bytes_of(self.pristine))
        self.assertEqual(os.listdir(os.path.dirname(held)), ["pier.jpg"])
        self.assertEqual(self.bytes_of(entry), whole)
        after = self.copy("after")
        self.assertFalse(self.write(after))   # the next run hits the good entry: no ExifTool started
        self.assertEqual(self.bytes_of(after), self.bytes_of(good))
        self.assertEqual((keeper.counts["hits"], keeper.counts["misses"]), (1, 3), keeper.why)
        self.assertEqual(keeper.why["a kept output could not be placed"], 1, keeper.why)

    def test_the_release_gate_runs_the_products_own_mint_and_start(self):
        was = identity.mint_document_id
        self.addCleanup(setattr, identity, "mint_document_id", was)
        with mock.patch.object(exiftool_replay.atexit, "register"):
            recording = exiftool_replay.install(exiftool_replay.RECORD)
        self.assertIs(identity.mint_document_id, was)
        with ExifToolSession(executable=EXIFTOOL) as et:
            self.assertTrue(et.running)   # started at `with`, as in the product
            self.assertEqual(recording.counts["commands"], 0)   # its start's -ver is ExifTool's, never kept
            et.execute("-overwrite_original", "-XMP:Subject=Trips/Coast", "-XMP:Title=Pier at dusk", self.copy("a"))
        self.assertEqual((recording.counts["kept"], recording.counts["starts"]), (1, 0), recording.why)
        with mock.patch.object(exiftool_replay.atexit, "register"):
            replaying = exiftool_replay.install(exiftool_replay.REPLAY)
        self.assertIsNot(identity.mint_document_id, was)
        self.assertFalse(self.write(self.copy("b")))   # replayed: started for the first command it cannot answer
        self.assertEqual(replaying.counts["hits"], 1, replaying.why)

    def test_two_processes_asking_for_one_command_at_once(self):
        go = os.path.join(self.temp, "go")
        photos = [self.copy(name) for name in ("one", "two")]
        env = dict(os.environ, **{exiftool_replay.CACHE_ENV: self.cache})
        env.pop(exiftool_replay.ENV, None)
        askers = [processes.start([sys.executable, "-c", ASKER, go, photo, EXIFTOOL], cwd=ROOT, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
                  for photo in photos]
        open(go, "w").close()
        said = [asker.communicate(timeout=120) for asker in askers]
        self.assertEqual([asker.returncode for asker in askers], [0, 0], said)
        counts = [json.loads(out.strip().splitlines()[-1]) for out, _err in said]
        self.assertEqual([c.get("hits", 0) + c.get("misses", 0) for c in counts], [1, 1], counts)
        self.real()
        real = self.copy("real")
        self.write(real)
        self.assertEqual([self.bytes_of(p) for p in photos], [self.bytes_of(real)] * 2)
        # One whole entry, no part left; the next asker is answered from it.
        self.assertEqual(len(self.entries()), 1)
        self.assertIsNotNone(exiftool_replay.read_entry(self.entries()[0]))
        self.assertFalse([n for _f, _d, names in os.walk(self.cache) for n in names if ".part-" in n])
        keeper = self.keeper()
        third = self.copy("three")
        self.assertFalse(self.write(third))
        self.assertEqual(keeper.counts["hits"], 1, keeper.why)
        self.assertEqual(self.bytes_of(third), self.bytes_of(real))

    def test_an_entry_cut_off_part_way_is_not_read_and_is_replaced(self):
        self.keeper()
        self.write(self.copy("a"))
        [entry] = self.entries()
        whole = self.bytes_of(entry)
        with open(entry, "wb") as handle:   # a kill in the middle of a write that was not os.replace'd
            handle.write(whole[:len(whole) // 2])
        with open(entry + ".part-1-1", "wb") as handle:
            handle.write(whole[:10])
        keeper = self.keeper()
        cut = self.copy("b")
        self.write(cut)
        self.assertEqual((keeper.counts["hits"], keeper.counts["misses"], keeper.counts["kept"]), (0, 1, 1),
                         keeper.why)
        self.assertEqual(self.bytes_of(entry), whole)
        self.write(self.copy("c"))
        self.assertEqual(keeper.counts["hits"], 1)
        self.real()
        real = self.copy("real")
        self.write(real)
        self.assertEqual(self.bytes_of(cut), self.bytes_of(real))

    def test_the_cache_folder_deleted_mid_run_is_made_again(self):
        keeper = self.keeper()
        self.write(self.copy("a"))
        shutil.rmtree(self.cache)
        self.write(self.copy("b"))
        self.write(self.copy("c"))
        self.assertEqual((keeper.counts["misses"], keeper.counts["kept"], keeper.counts["hits"]), (2, 2, 1),
                         keeper.why)
        self.assertEqual(len(self.entries()), 1)

    def test_an_answer_holding_a_files_dates_is_never_kept(self):
        keeper = self.keeper()
        for folder in ("a", "b"):
            photo = self.copy(folder)
            with ExifToolSession(executable=EXIFTOOL) as et:
                answer = json.loads(et.execute("-j", "-File:FileModifyDate", "-XMP:Subject", photo))
            self.assertIn("File:FileModifyDate", answer[0])
        self.assertEqual((keeper.counts["hits"], keeper.counts["misses"], keeper.counts["kept"]), (0, 2, 0))
        self.assertEqual(keeper.why["not kept: its answer holds a file's dates"], 2)
        self.assertEqual(self.entries(), [])

    def test_the_oracles_whole_read_of_a_copy_is_kept_and_answered_with_no_exiftool_started(self):
        # The conformance suite's oracle reads every copy whole: kept, its next run answers it without a start (#3464).
        answers, keepers = [], []
        for folder in ("a", "b"):
            keepers.append(self.keeper())
            photo = self.copy(folder)
            with field_values.oracle(EXIFTOOL) as oracle:
                read = oracle.whole([photo])
            self.assertEqual(1, len(read))
            row = dict(next(iter(read.values())))
            self.assertEqual(os.path.basename(photo), os.path.basename(row.pop("SourceFile")))
            answers.append(row)
        cold, warm = keepers
        self.assertEqual((cold.counts["misses"], cold.counts["kept"]), (2, 2), cold.why)
        self.assertEqual((warm.counts["hits"], warm.counts["misses"], warm.counts["starts"]), (2, 0, 0), warm.why)
        self.assertEqual(answers[0], answers[1])
        self.assertFalse([tag for tag in answers[1] if tag.startswith("File:System:")], answers[1])

    def test_the_minted_identity_repeats_its_sequence(self):
        first, second = exiftool_replay.seeded_mint(), exiftool_replay.seeded_mint()
        ids = [first() for _ in range(3)]
        self.assertEqual(ids, [second() for _ in range(3)])
        self.assertEqual(len(set(ids)), 3)
        for minted in ids:
            self.assertTrue(minted.startswith("xmp.did:"))
            self.assertEqual(uuid.UUID(minted[len("xmp.did:"):]).version, 4)

    def test_the_kept_answers_stay_within_their_cap_used_longest_ago_first(self):
        keeper = self.keeper()
        photos = {}
        for shade in (10, 20, 30):   # three commands, three entries
            photos[shade] = picture(os.path.join(self.temp, "shade%d.jpg" % shade), shade)
            self.write(photos[shade])
        made = sorted(self.entries(), key=os.path.getmtime)
        self.assertEqual(len(made), 3)
        for age, entry in enumerate(made):
            os.utime(entry, (1_000_000 + age, 1_000_000 + age))
        stale = os.path.join(os.path.dirname(made[0]), "x.entry.part-1-1")
        open(stale, "wb").close()
        os.utime(stale, (1_000_000, 1_000_000))
        # The oldest is used again (a hit marks it), so the next oldest goes first.
        os.makedirs(os.path.join(self.temp, "again"))
        again = picture(os.path.join(self.temp, "again", "shade10.jpg"), 10)
        self.write(again)
        self.assertEqual(keeper.counts["hits"], 1, keeper.why)
        sizes = {entry: os.path.getsize(entry) for entry in made}
        cap = sum(sizes.values()) - 1
        (left, size), (gone, _freed) = exiftool_replay.trim(cap)
        self.assertEqual((left, gone), (2, 2))   # one entry and the stale part
        self.assertFalse(os.path.exists(made[1]))
        self.assertTrue(os.path.exists(made[0]) and os.path.exists(made[2]))
        self.assertLessEqual(size, cap)
        self.assertFalse(os.path.exists(stale))


class ProductCodeNeverReplays(unittest.TestCase):
    def test_a_fresh_tagpup_with_the_variable_set_has_no_keeper(self):
        env = dict(os.environ, **{exiftool_replay.ENV: exiftool_replay.REPLAY})
        done = processes.run([sys.executable, "-c", FRESH], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, encoding="utf-8", timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.split()[-3:], ["True", "tagpup.files.identity", "False"], done.stderr)

    def test_nothing_outside_the_tests_sets_a_keeper(self):
        sets = re.compile(r"\bREPLAY\s*=[^=]|mint_document_id\s*=[^=]|exiftool_replay|TAGPUP_TEST_EXIFTOOL")
        found = []
        for top in ("tagpup", "scripts", "tools", "."):
            for folder, dirs, names in os.walk(os.path.join(ROOT, top)):
                dirs[:] = [] if top == "." else [d for d in dirs if d not in ("__pycache__", "node_modules")]
                for name in names:
                    if not name.endswith((".py", ".cmd", ".ps1", ".cjs", ".mjs")):
                        continue
                    path = os.path.join(folder, name)
                    with open(path, encoding="utf-8", errors="replace") as handle:
                        for line in handle:
                            if sets.search(line) and not line.lstrip().startswith("#"):
                                found.append((os.path.relpath(path, ROOT).replace("\\", "/"), line.strip()))
        self.assertEqual(sorted({path for path, _line in found}),
                         ["tagpup/files/exiftool_session.py", "tools/run_tests.py"], found)
        in_session = [line for path, line in found if path == "tagpup/files/exiftool_session.py"]
        self.assertEqual(in_session, ["REPLAY = None"])


if __name__ == "__main__":
    unittest.main()
