"""Every index the queue makes is one run of the library's `job_runs`, under the sync that queued it, written only by the
process that claimed it (F11, D9; DECISIONS 2026-10-28, "ACTIVITY REWORK"; docs/PLAN_ACTIVITY.md, B2;
tagpup.jobs.indexing, tagpup.services.indexing, tagpup.services.job_runs.recording and tell).

The server's queue claims the run as it takes the job -- under the run of the library that queued it -- writes how far it
got from the indexer's lines (a phase and counts, never a path, at most every 10 s), and ends it done, failed with its
sentence, or done and counted cancelled. The indexer it starts (the CLI, faked here: it prints what the real one prints)
claims nothing. A library behind this version's schema, a library being written by the bake, a row pruned mid-run: the
index runs as it would have; another process writing the library as an index starts leaves it unrecorded, started at
once, and as an index ends lets the next start at once, the end written once the library is free; an end that cannot be
written is written as the next index begins. Two processes indexing one library (the app and
a CLI sync) each record their own. The daily sync's walk says how far it got in its own run. A server killed mid-index
leaves its run to the sweep at the next start, under its sync still.

The Activity page reads the index's runs from these rows alone (tagpup.services.activity; the queue keeps no history):
the timeline lists each once, nested under its sync with how far it got, cancelled or abandoned as it ended, with its
log; Now lists another process's index among the runs, and this process's once, as its queue's. Libraries are made in
homes of their own; the names are fictional.
"""
import json
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from waiting import holds_for, wait_until  # noqa: E402
import migration_names  # noqa: E402

from PIL import Image  # noqa: E402

from tagpup import logs  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import processes  # noqa: E402
from tagpup.core import progress_line  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.jobs import recurring  # noqa: E402
from tagpup.services import activity, bake_after_index, indexing  # noqa: E402
from tagpup.services import job_runs as runs_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import roots_location  # noqa: E402
from tagpup.services import sync as sync_service  # noqa: E402
from tagpup.store import db, job_runs, schema  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web.lifecycle import Lifecycle  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The indexer the queue starts, as far as its lines go: it prints its plan's lines, holds the library's write lock
#: between HOLD and LET_GO as a bake's write does, waits at "WAIT <file>" for the file, and exits with the plan's code.
FAKE_CLI = '''
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "plan.json"), encoding="utf-8") as handle:
    plan = json.load(handle)
sys.path.insert(0, plan["root"])
held = None
for line in plan["lines"]:
    if line == "HOLD":
        from tagpup.store import db
        held = db.connect(os.environ["TAGPUP_DB_PATH"])
        db.begin(held, immediate=True)
    elif line == "LET_GO":
        held.rollback()
        held.close()
    elif line.startswith("WAIT "):
        while not os.path.exists(line[5:]):
            time.sleep(0.02)
    else:
        print(line, flush=True)
sys.exit(plan.get("exit", 0))
'''


def bars(photos):
    """The facts an index of `photos` says before its bake, made as the CLI makes them."""
    return [progress_line.say(fact) for fact in (
        indexing.phase_fact("metadata", photos, photos), indexing.phase_fact("embeddings", photos, photos),
        indexing.phase_fact("faces"), progress_line.fact("new_faces", sentence="No new face is waiting for a name."))]


def bake(folders, of=None):
    of = of or len(folders)
    return [progress_line.say(bake_after_index.folder_told(number, of, bake_after_index.Baked(written=number - 1), each))
            for number, each in enumerate(folders, 1)]


def baked(**counts):
    """The fact of what a bake wrote, as the CLI says it."""
    return progress_line.say(bake_after_index.Baked(**counts).fact())


def rows(db_path, sql, params=()):
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


class _Indexing(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        #: The process's runtime, as the server's: its index queues.
        self.runtime = runtimes.Runtime()
        self.library = self.made("harbour")

    def made(self, name, at=None):
        library = Library(self.home.library(name + ".db"))
        if at is None:
            schema.ensure(library.path)
        else:
            with migration_names.up_to(at):
                schema.ensure(library.path)
            schema.forget()
        return library

    def folder(self, name):
        folder = os.path.join(self.home.root, "Pictures", name)
        os.makedirs(folder, exist_ok=True)
        return folder

    def code(self, lines, exit_code=0, name="code"):
        """A code folder whose tagpup_cli.py prints `lines` and exits `exit_code`."""
        code = os.path.join(self.home.root, name)
        os.makedirs(code, exist_ok=True)
        with open(os.path.join(code, "tagpup_cli.py"), "w", encoding="utf-8") as handle:
            handle.write(FAKE_CLI)
        with open(os.path.join(code, "plan.json"), "w", encoding="utf-8") as handle:
            json.dump({"root": ROOT, "lines": lines, "exit": exit_code}, handle)
        return code

    def index_with(self, library, code, report=None):
        def index(folders, told):
            def heard(message=None, percent=None):
                if report is not None:
                    report(message, percent)
                told(message, percent)
            return indexing.index_folder(library, folders, code, heard)
        return index

    def queued(self, folders, code, library=None, report=None):
        """`folders` queued on `library`'s queue (this process's, as the server's) and indexed by the fake CLI: the
        queue's status of the first, once it has ended."""
        library = library or self.library
        queue = self.runtime.index_queues.of(library)
        self.assertTrue(queue.start(folders, self.index_with(library, code, report), together=True).ok)
        queue.wait()
        return queue.status(folders[0])

    def runs(self, library=None):
        return runs_service.tree(library or self.library)

    def indexing_in_another_process(self):
        """Another process -- the server, a CLI's sync -- whose daily sync has queued an index of the library, which
        hangs once it has begun until SERVER_GO is made in the home: the process, its index under way. It makes
        SERVER_ENDED once its queue is done, and then ends."""
        ready = os.path.join(self.home.root, "SERVER_READY")
        script = os.path.join(self.home.root, "server.py")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(SERVER % {"root": ROOT, "library": self.library.path, "ready": ready,
                                   "go": os.path.join(self.home.root, "SERVER_GO"),
                                   "ended": os.path.join(self.home.root, "SERVER_ENDED"),
                                   "folder": self.folder("Regatta")})
        server = processes.start([sys.executable, script], cwd=self.home.root)
        try:
            wait_until(lambda: self.assertIsNone(server.poll(), "the server ended before its index began")
                       or os.path.exists(ready), timeout=60, what="the server's index to begin")
        except BaseException:
            processes.kill_tree(server.pid)
            server.wait(30)
            raise
        return server

    def index_run(self, library=None):
        [run] = [each for each in self.runs(library) if each.job == runs_service.INDEX]
        return run

    def page(self, url):
        """What the Activity page is answered at `url`, by an app of this home's libraries (Flask's test client)."""
        app = web.create_app("tagpup", runtime=self.runtime,
                             lifecycle=Lifecycle(background=runtimes.Background([]), runtime=self.runtime))
        app.testing = True
        reply = app.test_client().get(url)
        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
        return reply.get_json()

    def timeline(self, kind):
        return [entry for entry in self.page("/api/activity/timeline")["entries"] if entry["kind"] == kind]

    def own_log(self, run):
        """The log an index's own indexer writes, which the page links to."""
        return logs.run_log(indexing.INDEXER_LOG, run.changed["run"])


class AnIndexIsOneRunUnderTheSyncThatQueuedIt(_Indexing):
    def test_the_daily_sync_s_index_is_its_child_says_how_far_it_got_and_ends_with_its_counts_and_message(self):
        folders = [self.folder("Regatta"), self.folder("Lighthouse")]
        code = self.code(bars(3) + bake(folders) + [baked(written=3, conflicts=1)])
        queue = self.runtime.index_queues.of(self.library)
        index = self.index_with(self.library, code)
        synced = recurring.run_claimed(recurring.JOBS.get(recurring.SYNC), self.library, self.library.name,
                                       lambda now: queue.start(folders, index, together=True))
        self.assertTrue(synced.ran)
        queue.wait()
        status = queue.status(folders[0])
        self.assertEqual("completed", status["status"], status)
        found = self.runs()
        self.assertEqual([("index", synced.run_id), ("sync", None)], [(run.job, run.parent) for run in found])
        run = found[0]
        self.assertEqual((job_runs.DONE, status["message"]), (run.outcome, run.note))
        # The queue's bar is not a count of what the index did (#2193).
        self.assertNotIn("percent", run.changed)
        self.assertEqual((3, 3, 0, 1, 2), tuple(run.changed[name] for name in (
            "photos_read", "baked_written", "baked_current", "baked_conflicts", "folders")))
        # The last phase it said, at once as the phase changed: the bake's first folder, counted, its path not written.
        self.assertEqual({"phase": "bake", "counts": {"folders_done": 0, "folders": 2, "written": 0, "current": 0,
                                                      "conflicts": 0, "percent": 0}}, run.progress)
        text = rows(self.library.path, "SELECT COALESCE(progress, '') || changed || COALESCE(note, '') FROM job_runs")
        self.assertFalse(any("Regatta" in each[0] or "Pictures" in each[0] for each in text), text)
        # Its tag, which its indexer's log is named by, is in its row: what the page finds the run's lines by.
        self.assertTrue(run.changed["run"].startswith("index:harbour:"), run.changed)
        [entry] = self.timeline("index")
        self.assertEqual((synced.run_id, "bake", "index of 2 folder(s)"),
                         (entry["parent"], entry["progress"]["phase"], entry["what"]))
        self.assertEqual(run.heartbeat, entry["progress"]["progressed"])
        [sync] = [each for each in self.timeline("job") if each["id"] == synced.run_id]
        self.assertEqual([logs.SERVER_LOG, self.own_log(run)], sync["logs"])

    def test_the_timeline_lists_an_index_once_from_its_row_with_its_log(self):
        """#2188: the index's row was listed as a job beside the queue's own entry of it."""
        self.queued([self.folder("Regatta")], self.code(bars(1)))
        run = self.index_run()
        found = [entry for entry in self.page("/api/activity/timeline")["entries"] if entry["kind"] in ("index", "job")]
        self.assertEqual([("index", run.id, "index of 1 folder(s)", "completed", None, run.changed["run"])],
                         [(each["kind"], each["id"], each["what"], each["outcome"], each["error"], each["run"])
                          for each in found])
        self.assertEqual([self.own_log(run), logs.SERVER_LOG], found[0]["logs"])
        self.assertEqual([("index", run.id)], [(each["kind"], each["id"]) for each in activity.timeline(self.library)
                                               if each["kind"] != "change"])

    def test_an_index_queued_by_no_run_is_a_top_run_and_the_indexer_it_starts_claims_none(self):
        folder = self.folder("Regatta")
        self.assertEqual("completed", self.queued([folder], self.code(bars(1)))["status"])
        self.assertEqual([("index", None, job_runs.DONE)], [(run.job, run.parent, run.outcome) for run in self.runs()])

    def test_two_libraries_indexing_at_once_each_record_their_own_run(self):
        other = self.made("lighthouse")
        release = os.path.join(self.home.root, "RELEASE")
        mine = [self.folder("Regatta")]
        theirs = [self.folder("Quay %d" % number) for number in range(3)]
        queues = [self.runtime.index_queues.of(self.library), self.runtime.index_queues.of(other)]
        for queue, library, folders, name in ((queues[0], self.library, mine, "code-harbour"),
                                              (queues[1], other, theirs, "code-lighthouse")):
            code = self.code(["WAIT " + release] + bake(folders), name=name)
            self.assertTrue(queue.start(folders, self.index_with(library, code), together=True).ok)
        try:
            # Both under way at once, each its own run in its own library.
            wait_until(lambda: all(rows(each.path, "SELECT outcome FROM job_runs") == [("running",)]
                                   for each in (self.library, other)), timeout=30, what="both runs under way")
            now = {entry["name"]: entry for entry in self.page("/api/activity/now")["libraries"]}
            for library in (self.library, other):
                self.assertEqual((self.index_run(library).changed["run"], []),
                                 (now[library.name]["indexing"]["running"]["run"], now[library.name]["jobs"]))
        finally:
            open(release, "w").close()
            for queue in queues:
                queue.wait()
        self.assertEqual((1, 3), (self.index_run().progress["counts"]["folders"], self.index_run(other).progress["counts"]["folders"]))
        self.assertEqual([1, 3], [self.index_run(each).changed["folders"] for each in (self.library, other)])


class AnIndexThatDoesNotFinishIsRecordedSo(_Indexing):
    def test_a_cancelled_run_is_done_counted_cancelled_with_its_sentence(self):
        status = self.queued([self.folder("Regatta")], self.code([], exit_code=indexing.EXIT_CANCELLED))
        self.assertEqual("cancelled", status["status"])
        run = self.index_run()
        self.assertEqual((job_runs.DONE, indexing.CANCELLED_WAITING, 1), (run.outcome, run.note, run.changed["cancelled"]))
        self.assertEqual([("cancelled", None)], [(entry["outcome"], entry["error"]) for entry in self.timeline("index")])

    def test_an_index_that_does_not_run_to_its_end_ends_failed_with_a_sentence_however_it_stopped(self):
        """Three queued runs: the indexer never started (WinError 206: a command line past 32,767 characters, #2149),
        the indexer exited badly, and the thread stopped by something that is not an Exception. Each row says why, and
        none is done (a run whose thread stopped read done with one error), and the folder of the stopped one reads
        failed with the same sentence, not indexing for ever."""
        folder = [self.folder("Regatta")]
        with mock.patch.object(processes, "start", side_effect=OSError(206, "The filename or extension is too long")):
            status = self.queued(folder, self.code(bars(1)))
        self.assertEqual("failed", status["status"])
        run = self.index_run()
        self.assertEqual((job_runs.FAILED, status["message"], 1), (run.outcome, run.note, run.changed["errors"]))
        self.assertEqual(("OSError", runs_service.ERROR), (run.note.split(":")[0], runs_service.kind_of(run)))
        self.assertIn("too long", run.note)

        status = self.queued(folder, self.code(bars(1), exit_code=1))
        exited = [each for each in self.runs() if each.job == runs_service.INDEX and each.id != run.id][0]
        self.assertEqual((job_runs.FAILED, "failed"), (exited.outcome, status["status"]))
        self.assertEqual((status["message"], runs_service.ERROR), (exited.note, runs_service.kind_of(exited)))
        self.assertIn("exit code 1", exited.note)
        (group,) = activity.needs_you([self.library])
        self.assertEqual([(runs_service.INDEX, 2)], [(each["job"], each["count"]) for each in group["items"]])
        (item,) = group["items"]
        self.assertIn("run a sync of the library", item["next_step"])
        self.assertEqual((False, exited.changed["run"]), (item["run_now"], item["run"]))

        class Stopped(BaseException):
            pass

        def stops(folders, told):
            raise Stopped()
        queue = self.runtime.index_queues.of(self.library)
        with mock.patch.object(threading, "excepthook", lambda args: None):
            self.assertTrue(queue.start(folder, stops, together=True).ok)
            queue.wait()
        known = {run.id, exited.id}
        (stopped,) = [each for each in self.runs() if each.job == runs_service.INDEX and each.id not in known]
        self.assertEqual((job_runs.FAILED, indexing_jobs.STOPPED, runs_service.ERROR),
                         (stopped.outcome, stopped.note, runs_service.kind_of(stopped)))
        said = queue.status(folder[0])
        self.assertEqual(("failed", indexing_jobs.STOPPED), (said["status"], said["message"]))
        self.assertEqual([], queue.active()["active"])


class AnIndexWhoseRowCannotBeWrittenRunsAsItWould(_Indexing):
    def test_an_end_that_cannot_be_written_is_written_as_the_next_index_of_the_library_begins(self):
        """#2189: the row stayed `running`, this live server's, for good: every later index unrecorded and a root move
        refused until a restart."""
        real_end, said = runs_service.end, []

        def end_once_unwritable(*args, **kwargs):
            if not said:
                said.append(args[1])
                raise OSError("the library cannot be written just now")
            return real_end(*args, **kwargs)
        with mock.patch.object(runs_service, "end", side_effect=end_once_unwritable):
            first = self.queued([self.folder("Regatta")], self.code(bars(1)))
            self.assertEqual([job_runs.RUNNING], [run.outcome for run in self.runs()])
            self.assertEqual(["the index job is running"], roots_location._running_here(self.library))
            self.queued([self.folder("Lighthouse")], self.code(bars(1)))
        found = self.runs()
        self.assertEqual([(job_runs.DONE, None), (job_runs.DONE, first["message"])],
                         [(run.outcome, run.note if run.id == said[0] else None) for run in found])
        self.assertEqual([], roots_location._running_here(self.library))

    def test_an_end_that_fails_twice_is_still_written_when_later_runs_are_claimed(self):
        """#2199: one slot held the pending end, and a later run's end cleared it: the first run stayed `running`, this live
        server's, until a restart, and a root move was refused."""
        real_end, tried = runs_service.end, []

        def end_unwritable_twice(*args, **kwargs):
            tried.append(args[1])
            if len(tried) <= 2:
                raise OSError("the library cannot be written just now")
            return real_end(*args, **kwargs)
        with mock.patch.object(runs_service, "end", side_effect=end_unwritable_twice):
            first = self.queued([self.folder("Regatta")], self.code(bars(1)))
            self.queued([self.folder("Lighthouse")], self.code(bars(1)))
            self.queued([self.folder("Quay")], self.code(bars(1)))
        found = self.runs()
        self.assertEqual([job_runs.DONE] * 3, [run.outcome for run in found])
        self.assertEqual(first["message"], [run.note for run in found if run.id == tried[0]][0])
        self.assertEqual([], roots_location._running_here(self.library))

    def test_a_library_behind_the_schema_is_indexed_and_never_written_a_run(self):
        behind = self.made("breakwater", at=35)
        status = self.queued([self.folder("Regatta")], self.code(bars(1) + bake([self.folder("Regatta")])),
                             library=behind)
        self.assertEqual("completed", status["status"], status)
        self.assertEqual([(35, 0)], rows(behind.path, "SELECT (SELECT MAX(version) FROM schema_version),"
                                                      " (SELECT COUNT(*) FROM job_runs)"))
        self.queued([self.folder("Lighthouse")], self.code(bars(1)))
        self.assertEqual({"breakwater": [], "harbour": []},
                         {entry["name"]: entry["jobs"] for entry in self.page("/api/activity/now")["libraries"]})
        self.assertEqual([("harbour", "completed")], [(entry["library"], entry["outcome"]) for entry in self.timeline("index")])

    def test_while_the_bake_holds_the_library_progress_is_skipped_and_the_index_is_not_slowed(self):
        folders = [self.folder("Quay %d" % number) for number in range(5)]
        heard = os.path.join(self.home.root, "HEARD")
        code = self.code(bars(5) + ["HOLD"] + bake(folders) + ["WAIT " + heard, "LET_GO",
                                                               baked(written=4)])

        def report(message, percent):
            # The last bake line has been told (job_runs.tell runs before the report): the bake may let go.
            if message and message.startswith(bake_after_index.FOLDER_SAID + "5 of 5"):
                open(heard, "w").close()
        began = time.monotonic()
        status = self.queued(folders, code, report=report)
        self.assertLess(time.monotonic() - began, 15, "a busy library is skipped, never waited for (30 s)")
        self.assertEqual("completed", status["status"], status)
        run = self.index_run()
        self.assertEqual((job_runs.DONE, 4), (run.outcome, run.changed["baked_written"]))
        self.assertNotEqual("bake", (run.progress or {}).get("phase"), "every bake line came while the library was held")

    def test_a_row_removed_mid_run_is_not_written_again_and_the_run_goes_on(self):
        """A restore or a prune can take the row while the index runs."""
        folders = [self.folder("Quay %d" % number) for number in range(3)]
        taken = []

        def report(message, percent):
            if message and message.startswith(bake_after_index.FOLDER_SAID) and not taken:
                taken.append(self.index_run().id)
                conn = db.connect(self.library.path)
                try:
                    conn.execute("DELETE FROM job_runs")
                    conn.commit()
                finally:
                    conn.close()
        told = []
        real_tell = runs_service.tell
        with mock.patch.object(runs_service, "tell", side_effect=lambda *a: told.append(real_tell(*a)) or told[-1]):
            status = self.queued(folders, self.code(bars(3) + bake(folders)), report=report)
        self.assertEqual("completed", status["status"], status)
        self.assertTrue(taken)
        self.assertEqual([(0,)], rows(self.library.path, "SELECT COUNT(*) FROM job_runs"))
        self.assertIn(False, told)


#: Another process writing the library -- a CLI's change, a restore -- until RELEASE is made: it says READY once it holds it.
WRITER = '''
import os, sys, time
sys.path.insert(0, %(root)r)
from tagpup.store import db
held = db.connect(%(library)r)
db.begin(held, immediate=True)
open(%(ready)r, "w").close()
while not os.path.exists(%(release)r):
    time.sleep(0.02)
held.rollback()
held.close()
'''


class AnIndexStartsAtOnceBesideAnotherWriter(_Indexing):
    def another_writer(self):
        """Another process holding the library's write lock until the file it returns is made (at the latest when the
        test ends)."""
        ready, release = os.path.join(self.home.root, "READY"), os.path.join(self.home.root, "RELEASE")
        script = os.path.join(self.home.root, "writer.py")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(WRITER % {"root": ROOT, "library": self.library.path, "ready": ready, "release": release})
        writer = processes.start([sys.executable, script], cwd=self.home.root)
        self.addCleanup(writer.wait, 30)
        self.addCleanup(lambda: open(release, "w").close())
        wait_until(lambda: self.assertIsNone(writer.poll(), "the writer ended before it held the library")
                   or os.path.exists(ready), timeout=60, what="the writer to hold the library")
        return release

    def test_a_library_another_process_holds_is_indexed_at_once_and_unrecorded(self):
        """#2196: the claim waited out the other's write, up to four times 30 s, before the indexer started."""
        release = self.another_writer()
        began, first = time.monotonic(), []

        def report(message, percent):
            if not first:
                first.append(time.monotonic() - began)
                open(release, "w").close()
        status = self.queued([self.folder("Regatta")], self.code(bars(1)), report=report)
        self.assertEqual("completed", status["status"], status)
        self.assertLess(first[0], 15, "the indexer started only once the other's write was waited out")
        self.assertEqual([], [run for run in self.runs() if run.job == "index"])

    def test_an_index_ending_while_another_process_writes_lets_the_next_start_and_is_ended_once_it_is_free(self):
        """#2200: the end waited the other's write out, up to four times 30 s, on the queue's worker: the next queued
        index started only then."""
        go = os.path.join(self.home.root, "GO")
        queue = self.runtime.index_queues.of(self.library)
        held, seen = [], {}

        def first_said(message, percent):
            # Its last line: the other process writes from here to after the next index has begun.
            if not held and message and message.startswith("No new face"):
                held.append(self.another_writer())
                # Let go of it at the latest 20 s on, so the old code fails in time rather than in two minutes.
                timer = threading.Timer(20, lambda: open(held[0], "w").close())
                timer.start()
                self.addCleanup(timer.cancel)
                seen["first_ends"] = time.monotonic()
                open(go, "w").close()

        def second_said(message, percent):
            if "second_begins" not in seen:
                seen["second_begins"] = time.monotonic()
                [first] = [run for run in self.runs() if run.job == runs_service.INDEX]
                seen["first"] = first
                seen["now"] = activity.runs_now([self.library], activity.Here())["runs"]
                open(held[0], "w").close()
        first_code = self.code(bars(1) + ["WAIT " + go], name="first")
        self.assertTrue(queue.start([self.folder("Regatta")], self.index_with(self.library, first_code, first_said),
                                    together=True).ok)
        self.assertTrue(queue.start([self.folder("Lighthouse")],
                                    self.index_with(self.library, self.code(bars(1), name="second"), second_said),
                                    together=True).ok)
        queue.wait()
        waited = seen["second_begins"] - seen["first_ends"]
        self.assertLess(waited, 2 * job_runs.RECORD_WITHIN + 8, "the next index waited for the first's end")
        # While its end waited, the first read as running, under way on the Activity page, not stuck.
        self.assertEqual(job_runs.RUNNING, seen["first"].outcome)
        self.assertEqual([(seen["first"].id, activity.RUNNING)],
                         [(run["run_id"], run["state"]) for run in seen["now"]])
        [first] = wait_until(lambda: [run for run in self.runs() if run.id == seen["first"].id
                                      and run.outcome != job_runs.RUNNING], timeout=60, what="the first run to end")
        self.assertEqual((job_runs.DONE, 1, 1), (first.outcome, first.changed["folders"], first.changed["attempted"]),
                         first.changed)
        self.assertEqual([], roots_location._running_here(self.library))

    def test_a_cli_index_ending_while_another_process_writes_is_ended_before_its_process_ends(self):
        """#2200: the thread that waits out the other's write took its daemon flag from the queue's worker, a daemon, so a
        CLI's sync ending meanwhile ended with the index's row still running, swept abandoned by the next start with its
        counts lost."""
        cli = self.indexing_in_another_process()
        self.addCleanup(lambda: cli.poll() is None and processes.kill_tree(cli.pid))
        release = self.another_writer()
        open(os.path.join(self.home.root, "SERVER_GO"), "w").close()
        ended = os.path.join(self.home.root, "SERVER_ENDED")
        wait_until(lambda: os.path.exists(ended), timeout=60, what="the CLI's queue to end")
        # Its queue is done and its last line run: a process with nothing left to wait for is gone within a few
        # seconds. The other process still writes, longer than an end is tried for (RECORD_WITHIN).
        alive = holds_for(lambda: cli.poll() is None, 3 + job_runs.RECORD_WITHIN)
        open(release, "w").close()
        cli.wait(60)
        run = self.index_run()
        self.assertEqual((job_runs.DONE, 1, 1), (run.outcome, run.changed.get("attempted"), run.changed.get("changed")),
                         run.changed)
        self.assertTrue(alive, "the CLI ended while its index's end waited for the library")


class TwoProcessesIndexingOneLibrary(_Indexing):
    def test_the_server_and_a_cli_sync_each_record_their_index_and_a_root_move_waits_for_both(self):
        """#2190: the second process's claim answered `running` and its index ran unrecorded (a CLI `sync --apply`
        beside the app). Each is its own row, its own owner; a root move is refused while either runs, and the one whose
        process is killed is swept at the next start, the other kept."""
        cli = self.indexing_in_another_process()
        try:
            self.assertEqual("completed", self.queued([self.folder("Lighthouse")], self.code(bars(1)))["status"])
            found = [run for run in self.runs() if run.job == "index"]
            self.assertEqual([job_runs.DONE, job_runs.RUNNING], [run.outcome for run in found])
            self.assertNotEqual(found[0].owner, found[1].owner)
            # The newest index has ended; the other process's still runs, and Now lists it under its sync.
            self.assertEqual(["the index job is running"], roots_location._running_here(self.library))
            [theirs] = [entry for entry in self.page("/api/activity/now")["libraries"] if entry["name"] == "harbour"]
            self.assertEqual([("index", found[1].id, found[1].changed["run"], found[1].parent,
                               [self.own_log(found[1]), logs.SERVER_LOG])],
                             [(each["job"], each["run_id"], each["run"], each["parent"], each["logs"])
                              for each in theirs["jobs"]])
            self.assertIsNotNone(found[1].parent)
        finally:
            processes.kill_tree(cli.pid)
            cli.wait(30)
        self.assertEqual(1, runs_service.sweep_abandoned(self.library))
        self.assertEqual([job_runs.DONE, job_runs.ABANDONED],
                         [run.outcome for run in self.runs() if run.job == "index"])
        self.assertEqual([], roots_location._running_here(self.library))


class ALongIndexWritesItsProgressAboutEvery10Seconds(_Indexing):
    def test_388_folders_written_once_in_10_s_of_the_clock(self):
        """The bake of 388 folders, a line each 2.5 s of a fake clock: the run's row is written 97 times (the first
        line and one in four of the 387 after it: 1 + 387 // 4), not 388."""
        folders = ["F%03d" % number for number in range(388)]
        clock = iter(1790000000.0 + 2.5 * step for step in range(10000))
        written = []
        real = job_runs.progress

        def counted(*args):
            done = real(*args)
            written.append(done)
            return done
        with mock.patch.object(runs_service, "time", mock.Mock(time=lambda: next(clock))), \
                mock.patch.object(job_runs, "progress", side_effect=counted):
            status = self.queued([self.folder("Regatta")], self.code(bake(folders)))
        self.assertEqual("completed", status["status"], status)
        self.assertEqual(97, written.count(True), (written.count(True), len(written)))
        self.assertEqual(388, self.index_run().progress["counts"]["folders"])

    def test_a_library_busy_the_whole_bake_is_tried_once_in_10_s_not_at_each_line(self):
        """#2196: a write the library was busy for was tried again at the very next line, a connection each."""
        folders = ["F%03d" % number for number in range(388)]
        clock = iter(1790000000.0 + 2.5 * step for step in range(10000))
        with mock.patch.object(runs_service, "time", mock.Mock(time=lambda: next(clock))), \
                mock.patch.object(job_runs, "progress", return_value=db.BUSY) as progress:
            status = self.queued([self.folder("Regatta")], self.code(bake(folders)))
        self.assertEqual("completed", status["status"], status)
        self.assertTrue(90 <= progress.call_count <= 98, progress.call_count)


class TheDailySyncSaysHowFarItsWalkGot(_Indexing):
    def test_the_sync_job_s_walk_writes_its_phase_folders_and_files_into_its_run(self):
        """#2171: the daily job's walk showed nothing; the watcher's alone was said, in memory."""
        pictures = os.path.join(self.home.root, "Pictures")
        for name in ("Regatta", "Lighthouse", "Quay"):
            Image.new("RGB", (16, 16), "navy").save(os.path.join(self.folder(name), "one.jpg"), "JPEG")
        phases = []
        real = job_runs.progress

        def heard(db_path, run_id, phase, counts, now, *within):
            phases.append(phase)
            return real(db_path, run_id, phase, counts, now, *within)
        with mock.patch.object(job_runs, "progress", side_effect=heard):
            synced = recurring.run_claimed(recurring.JOBS.get(recurring.SYNC), self.library, self.library.name,
                                           lambda now: sync_service.sync(self.library, roots=[pictures]))
        self.assertTrue(synced.result.ok, synced.result.message())
        self.assertEqual(["walking", "after_the_walk"], phases)
        [run] = self.runs()
        self.assertEqual({"phase": "after_the_walk", "counts": {"folders": 4, "files": 3}}, run.progress)

    def test_a_sync_whose_end_cannot_be_written_is_written_as_the_next_one_is_claimed(self):
        """The recurring runner's run is the same one-place lifecycle as the index's: an end that raised once left the sync
        `running`, this live server's, so no later sync was recorded."""
        sync = recurring.JOBS.get(recurring.SYNC)
        real_end, said = runs_service.end, []

        def end_once_unwritable(*args, **kwargs):
            if not said:
                said.append(args[1])
                raise OSError("the library cannot be written just now")
            return real_end(*args, **kwargs)
        with mock.patch.object(runs_service, "end", side_effect=end_once_unwritable):
            first = recurring.run_claimed(sync, self.library, self.library.name, lambda now: Result(attempted=1))
            self.assertEqual([job_runs.RUNNING], [run.outcome for run in self.runs()])
            second = recurring.run_claimed(sync, self.library, self.library.name, lambda now: Result(attempted=2))
        self.assertTrue(first.ran and second.ran, (first.why, second.why))
        self.assertEqual([job_runs.DONE, job_runs.DONE], [run.outcome for run in self.runs()])
        self.assertEqual([first.run_id], said)

    def test_a_recurring_body_stopped_by_more_than_an_exception_ends_its_run_failed(self):
        class Stopped(BaseException):
            pass

        def stops(now):
            raise Stopped()
        with self.assertRaises(Stopped):
            recurring.run_claimed(recurring.JOBS.get(recurring.SYNC), self.library, self.library.name, stops)
        [run] = self.runs()
        self.assertEqual((job_runs.FAILED, runs_service.ERROR), (run.outcome, runs_service.kind_of(run)))

    def test_a_sync_not_run_as_a_job_writes_no_run(self):
        """The watcher's sync of a folder claims none while it runs (its record is written as it ends)."""
        pictures = os.path.join(self.home.root, "Pictures")
        self.folder("Regatta")
        with mock.patch.object(job_runs, "progress") as progress:
            self.assertTrue(sync_service.sync(self.library, roots=[pictures]).ok)
        progress.assert_not_called()


#: The server or a CLI's sync, as far as its index goes: the daily sync queues an index, which hangs once it has begun
#: until GO is made, then has indexed its folders; ENDED is made once the queue is done, and the process ends.
SERVER = '''
import os, sys, time
sys.path.insert(0, %(root)r)
from tagpup.core.library import Library
from tagpup.core.result import Result
from tagpup.jobs import indexing, recurring
library = Library(%(library)r)
queue = indexing.IndexQueue(library.name, library)

def index(folders, report):
    open(%(ready)r, "w").close()
    while not os.path.exists(%(go)r):
        time.sleep(0.02)
    return Result(attempted=len(folders), changed=len(folders))

recurring.run_claimed(recurring.JOBS.get(recurring.SYNC), library, library.name,
                      lambda now: queue.start([%(folder)r], index, together=True))
queue.wait()
open(%(ended)r, "w").close()
'''


class AServerKilledMidIndex(_Indexing):
    def test_the_next_start_marks_the_index_abandoned_under_its_sync(self):
        server = self.indexing_in_another_process()
        processes.kill_tree(server.pid)
        server.wait(30)
        self.assertEqual([("index", job_runs.RUNNING), ("sync", job_runs.DONE)],
                         [(run.job, run.outcome) for run in self.runs()])
        library_actions.bring_up_to_date_in_background([self.library]).join(60)
        found = self.runs()
        self.assertEqual([("index", found[1].id, job_runs.ABANDONED, job_runs.STOPPED), ("sync", None, job_runs.DONE, None)],
                         [(run.job, run.parent, run.outcome, run.note if run.job == "index" else None) for run in found])
        [entry] = self.timeline("index")
        self.assertEqual(("abandoned", job_runs.STOPPED, found[1].id, [self.own_log(found[0]), logs.SERVER_LOG]),
                         (entry["outcome"], entry["error"], entry["parent"], entry["logs"]))
        [sync] = self.timeline("job")
        self.assertEqual([logs.SERVER_LOG, self.own_log(found[0])], sync["logs"])


if __name__ == "__main__":
    unittest.main()
