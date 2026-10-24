"""tools/retro_metrics.py counts a tiny git repository and a fake transcript folder made here, and keeps no text of a
transcript: a canary in a message's text, a tool's input, a user's record, a compaction's record and a subagent's meta
file (its description, its request, a branch name that is not a worktree-agent one) is in none of its outputs. Before a
merge the main session runs its --branch guard: a branch adding over the budget fails unless allowed."""
import contextlib
import datetime
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
import retro_metrics  # noqa: E402
from tagpup.core import processes  # noqa: E402

CANARY = "CANARY-Wrenna-Pellishaw-7731"
DAY = datetime.date
IN_THE_WINDOW = DAY(2026, 10, 2)


def _remove(path):
    def writable(func, name, _exc):
        os.chmod(name, stat.S_IWRITE)
        func(name)
    shutil.rmtree(path, onerror=writable)


class ARepository:
    def __init__(self, folder):
        self.folder = folder
        self.git("init", "-q", "-b", "main")

    def git(self, *args, when=None):
        env = dict(os.environ)
        if when:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when + "T12:00:00"
        done = processes.run(["git", "-C", self.folder, "-c", "user.name=Tess Tarrow", "-c",
                              "user.email=tess@example.invalid", "-c", "commit.gpgsign=false", *args],
                             capture_output=True, text=True, env=env)
        assert done.returncode == 0, done.stderr
        return done.stdout

    def write(self, path, text):
        full = os.path.join(self.folder, *path.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def commit(self, when, subject, *changes, remove=()):
        for path, text in changes:
            self.write(path, text)
            self.git("add", path)
        for path in remove:
            self.git("rm", "-q", path)
        self.git("commit", "-q", "-m", subject, when=when)


HEAD = "| # | Found | Finding | Status |\n|---|---|---|---|\n"


def build_repository(folder):
    repo = ARepository(folder)
    repo.commit("2026-10-01", "before the window", ("tagpup/a.py", "x = 1\n" * 10),
                ("tests/test_a.py", "def test_one():\n    pass\ndef test_two():\n    pass\n"),
                ("docs/findings.md", HEAD + "| 1 | 2026-10-01 | wrong: a thing | open |\n"))
    repo.git("branch", "worktree-agent-cafe")   # a worker that committed nothing on its own branch
    repo.commit("2026-10-02", "code and a lifted test\n\nCo-Authored-By: Fable Model 9 <f@example.invalid>",
                ("tagpup/a.py", "x = 1\n" * 15),
                ("tests/test_b.py", '"""' + "Lift" + 'ed: test_one"""\ndef test_three():\n    pass\n'))
    repo.git("checkout", "-q", "-b", "worktree-agent-abc1")
    repo.commit("2026-10-02", "a worker's change", ("web/x.js", "a;\nb;\nc;\n"))
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--no-ff", "worktree-agent-abc1", "-m", "Merge worktree-agent-abc1: a thing",
             when="2026-10-03")
    repo.commit("2026-10-03", "findings only",
                ("docs/findings.md", HEAD + "| 1 | 2026-10-01 | wrong: a thing | fixed: abc |\n"
                 "| 2 | 2026-10-03 | wrong (once a library is switched): the reviewer saw it | open |\n"),
                ("docs/history/findings_closed_0001-0250.md", HEAD + "| 3 | 2026-10-02 | low: x | obsolete: gone |\n"))
    repo.commit("2026-10-03", "Revert the first test", remove=["tests/test_a.py"])
    repo.commit("2026-10-05", "after the window", ("tagpup/b.py", "y = 2\n"))
    repo.git("branch", "worktree-agent-f00d")   # its tip is the trunk's, committed after its run began
    repo.git("checkout", "-q", "-b", "worktree-agent-dead")   # a worker's branch, never merged: +4 -10
    repo.commit("2026-10-05", "a big change", ("tagpup/a.py", "x = 1\n" * 5), ("web/y.js", "a;\n" * 4))
    repo.git("checkout", "-q", "main")
    return repo


def at_noon(day):
    return datetime.datetime(day.year, day.month, day.day, 12).astimezone().isoformat()


def build_transcripts(folder):
    def record(kind, model, out, msg_id, day=IN_THE_WINDOW, sidechain=False, **usage):
        return {"type": kind, "timestamp": at_noon(day), "sessionId": "s-1", "isSidechain": sidechain,
                "message": {"id": msg_id, "model": model, "role": kind,
                            "content": [{"type": "text", "text": CANARY},
                                        {"type": "tool_use", "input": {"file_path": CANARY}}],
                            "usage": dict({"input_tokens": 10, "output_tokens": out, "cache_creation_input_tokens": 100,
                                           "cache_read_input_tokens": 1000}, **usage)}}
    main = [record("assistant", "claude-opus-test", 5, "m1"), record("assistant", "claude-opus-test", 20, "m1"),
            {"type": "user", "timestamp": at_noon(DAY(2026, 10, 2)), "message": {"content": CANARY}},
            record("assistant", "claude-opus-test", 99, "m2", day=DAY(2026, 9, 30))]
    main.append(compaction("auto", 170000))
    os.makedirs(os.path.join(folder, "s-1", "subagents"))
    files = [("s-1.jsonl", main)]
    for agent, kind, branch, stopped, calls in SUBAGENTS:
        records = [record("assistant", "fable-test-1", 2, "%s-%d" % (agent, n), sidechain=True, input_tokens=1,
                          cache_creation_input_tokens=0, cache_read_input_tokens=0) for n in range(calls)]
        if agent == "a1":
            records.append(compaction("manual", 90000))
        if agent in CWDS:   # a record's cwd, as the harness writes it: where the agent ran
            records.insert(0, {"type": "user", "timestamp": at_noon(IN_THE_WINDOW), "cwd": CWDS[agent],
                               "message": {"content": CANARY}})
        files.append(("s-1/subagents/agent-%s.jsonl" % agent, records))
        meta = {"agentType": kind, "description": CANARY, "requestShape": CANARY, "toolUseId": "toolu_1",
                "spawnDepth": 1, "requestNonInteractive": True}
        if branch:
            meta.update(worktreeBranch=branch, spawnedWithWorktree=True, worktreePath="C:/wt/" + CANARY)
        if stopped:
            meta["stoppedByUser"] = True
        with open(os.path.join(folder, "s-1", "subagents", "agent-%s.meta.json" % agent), "w", encoding="utf-8") as h:
            json.dump(meta, h)
    for path, records in files:
        with open(os.path.join(folder, *path.split("/")), "w", encoding="utf-8") as handle:
            handle.write("\n".join(json.dumps(r) for r in records) + "\nnot json " + CANARY + "\n")


def compaction(trigger, before):
    return {"type": "system", "subtype": "compact_boundary", "timestamp": at_noon(IN_THE_WINDOW), "sessionId": "s-1",
            "content": CANARY, "compactMetadata": {"trigger": trigger, "preTokens": before, "postTokens": 9,
                                                   "preservedSegment": {"note": CANARY}}}


#: (agent, agentType, worktreeBranch, stoppedByUser, calls): one subagent of the fake session each, 3 tokens a call.
SUBAGENTS = (("a1", "tagpup-reviewer", None, False, 1), ("a2", "tagpup-worker", "worktree-agent-abc1", False, 2),
             ("a3", "tagpup-worker", "worktree-agent-beef", True, 1), ("a4", "general-purpose", "f-" + CANARY, False, 1),
             ("a5", "tagpup-worker-small", "worktree-agent-dead", False, 1),
             ("a6", "tagpup-worker-small", "worktree-agent-cafe", False, 1),
             ("a7", "tagpup-worker", "worktree-agent-f00d", False, 1),
             ("a8", "tagpup-quick-reviewer", None, False, 1), ("a9", "tagpup-owner-data", None, False, 1),
             ("a10", "tagpup-worker-small", None, False, 1))


#: The folder a subagent ran in, where its meta file names no branch: a worktree, the main checkout, a folder that
#: is not an agent's worktree.
CWDS = {"a1": "C:\\repos\\app\\.claude\\worktrees\\agent-abc1", "a8": "C:\\repos\\app",
        "a9": "C:\\repos\\app\\.claude\\worktrees\\trial-" + CANARY}


class TheCountersOfAKnownWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="retro_metrics_")
        cls.repo = os.path.join(cls.home, "repo")
        cls.transcripts = os.path.join(cls.home, "transcripts")
        os.makedirs(cls.repo)
        build_repository(cls.repo)
        build_transcripts(cls.transcripts)
        cls.out, cls.json = os.path.join(cls.home, "r.md"), os.path.join(cls.home, "r.json")
        retro_metrics.main(["--repo", cls.repo, "--rev", "main", "--transcripts", cls.transcripts, "--since",
                            "2026-10-02", "--until", "2026-10-03", "--out", cls.out, "--json", cls.json])
        with open(cls.json, encoding="utf-8") as handle:
            cls.report = json.load(handle)

    @classmethod
    def tearDownClass(cls):
        _remove(cls.home)

    def test_git(self):
        g = self.report["git"]
        self.assertEqual((5, 1), (g["commits"], g["merges"]))
        self.assertEqual({"code": [8, 0], "tests": [3, 4]}, {k: g["lines"][k] for k in ("code", "tests")})
        self.assertEqual(["2026-10-02", "2026-10-03"], list(g["per_day"]))
        self.assertEqual({"code": 10, "tests": 4}, {k: g["lines_in_tree"]["start"][k] for k in ("code", "tests")})
        self.assertEqual({"code": 18, "tests": 3}, {k: g["lines_in_tree"]["end"][k] for k in ("code", "tests")})
        self.assertEqual((1, 1, 1, 4), (g["worktree_merges"], g["reverts"], g["findings_only_commits"],
                                        g["files_changed_outside_docs"]))
        self.assertEqual({"Fable Model 9": 1, "(no trailer)": 4}, g["trailers"])

    def test_findings(self):
        f = self.report["findings"]
        self.assertEqual((2, {"2026-10-02": 1, "2026-10-03": 1}, {"wrong": 1, "low": 1}),
                         (f["opened"], f["opened_per_day"], f["opened_per_severity"]))
        self.assertEqual((2, {"fixed": 1, "obsolete": 1}), (f["closed"], f["closed_per_kind"]))
        self.assertEqual((1, 3, 1, 1), (f["open_now"], f["highest"], f["opened_naming_review_or_owner"],
                                        f["closed_obsolete_or_stale"]))
        self.assertEqual({"lines": 4, "open_rows": 1}, f["findings_md_per_day"]["2026-10-03"])

    def test_tests(self):
        t = self.report["tests"]
        self.assertEqual(({"files": 1, "test_files": 1, "def_test": 2}, {"files": 1, "test_files": 1, "def_test": 1}),
                         (t["start"], t["end"]))
        self.assertEqual((1, 1, 1), (t["files_added"], t["files_deleted"], t["lifted_lines"]))

    def test_usage(self):
        u = self.report["usage"]
        opus, fable = u["per_model"]["claude-opus-test"], u["per_model"]["fable-test-1"]
        self.assertEqual((1, 10, 20, 100, 1000), (opus["turns"], opus["input_tokens"], opus["output_tokens"],
                                                  opus["cache_creation_input_tokens"], opus["cache_read_input_tokens"]))
        self.assertEqual(0, opus["large_context"])
        self.assertEqual((11, 11, 22, False), (fable["turns"], fable["input_tokens"], fable["output_tokens"],
                                             fable["priced"]))
        self.assertAlmostEqual(10 * 5e-6 + 20 * 25e-6 + 100 * 5e-6 * 1.25 + 1000 * 5e-6 * 0.1, u["cost_low"])
        self.assertAlmostEqual(3 * (10 * 5e-6 + 20 * 25e-6 + 100 * 5e-6 * 1.25 + 1000 * 5e-6 * 0.1), u["cost_high"])
        self.assertEqual(({"2026-10-02": 1}, {"2026-10-02": 10}, {"main": 1130, "subagent": 33}, ["fable-test-1"]),
                         (u["sessions_per_day"], u["subagent_files_per_day"], u["tokens_main_vs_subagent"],
                          u["unpriced_models"]))

    def test_agent_types_branches_and_the_runs_the_owner_stopped(self):
        a = self.report["usage"]["agents"]
        kinds = {t: (v["runs"], v["calls"], v["tokens"]) for t, v in a["by_agent_type"].items() if v["calls"]}
        self.assertEqual({"main session": (1, 1, 1130), "worker": (3, 4, 12), "small worker": (3, 3, 9),
                          "reviewer": (1, 1, 3), "quick reviewer": (1, 1, 3), "owner: data": (1, 1, 3),
                          "general-purpose": (1, 1, 3)}, kinds)
        self.assertEqual((9, 21), (a["review"]["tokens"], a["work"]["tokens"]))
        self.assertEqual({"worktree-agent-abc1": ("merged", 9), "worktree-agent-beef": ("no merge found", 3),
                          "worktree-agent-dead": ("open", 3), "worktree-agent-cafe": ("nothing to merge", 3),
                          "worktree-agent-f00d": ("fast-forward", 3),
                          retro_metrics.OTHER_BRANCH: ("not a worktree branch", 3)},
                         {b: (v["state"], v["tokens"]) for b, v in a["by_branch"].items()})
        self.assertEqual((1, 9), (a["branches_per_state"]["merged"]["branches"],
                                  a["branches_per_state"]["merged"]["tokens"]))
        loose = a["branches_per_state"][retro_metrics.NO_BRANCH]
        self.assertEqual((1, 3), (loose["runs"], loose["tokens"]), "a worker tied to no branch is a state of its own")
        self.assertEqual({"worktree-agent-abc1": 1}, {b: v["review_runs"] for b, v in a["by_branch"].items()
                                                      if v["review_runs"]},
                         "the reviewer that ran in a branch's worktree is that branch's review run; the one that ran "
                         "in the main checkout and the one in a folder that is no agent's worktree are tied to none")
        self.assertEqual((1, 1, 3), tuple(a["stopped_by_the_owner"][k] for k in ("runs", "calls", "tokens")))

    def test_a_subagent_resumed_in_a_later_session_has_a_transcript_and_no_meta_file(self):
        # Seen 2026-10-17: a worker resumed after /clear left agent-<id>.jsonl with no agent-<id>.meta.json beside it.
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "agent-abc1.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"type": "user", "cwd": "C:/wt/.claude/worktrees/agent-abc1"}) + "\n")
            facts = retro_metrics.agent_meta(path)
        self.assertEqual("other", facts["agent_type"], "its type is not known, and it is not guessed")
        self.assertEqual("worktree-agent-abc1", facts["branch"], "the branch is still tied by the cwd")
        self.assertFalse(facts["stopped"])

    def test_workers_that_passed_the_hook_and_the_relaunches_that_finished_them(self):
        def call(file, kind, branch, at, context):
            return {"agent_type": kind, "agent_file": file, "branch": branch, "at": at, "model": "fable-test-1",
                    "input_tokens": context, "output_tokens": 1, "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0}
        big = retro_metrics.LARGE_CONTEXT + 1
        records = [call("a.jsonl", "worker", "worktree-agent-aa", 100, big),
                   call("b.jsonl", "worker", "worktree-agent-aa", 200, 10),    # the relaunch of aa
                   call("c.jsonl", "worker", "worktree-agent-bb", 300, 10),    # a branch of one run: no relaunch
                   call("d.jsonl", "reviewer", None, 400, 10),                 # reviewed in the main checkout
                   call("e.jsonl", "reviewer", "worktree-agent-aa", 500, 10)]  # tied to its branch
        got = retro_metrics.worker_restarts(records)
        self.assertEqual((1, big + 1), (got["at_the_hook"]["runs"], got["at_the_hook"]["tokens"]))
        self.assertEqual((1, 11), (got["relaunches"]["runs"], got["relaunches"]["tokens"]))
        self.assertEqual((1, 11), (got["unattached_reviews"]["runs"], got["unattached_reviews"]["tokens"]))

    def test_main_sessions_and_their_compactions(self):
        a = self.report["usage"]["agents"]
        self.assertEqual({"s-1": {"calls": 1, "average_context": 1110, "max_context": 1110, "compactions": 1,
                                  "compactions_auto": 1, "tokens_before_compaction": [170000]}}, a["main_sessions"])
        self.assertEqual((1110, 1), (a["main_average_context"], a["subagent_compactions"]))

    def test_workers(self):
        self.assertEqual({"count": 1, "median": 3, "p90": 3, "max": 3, "over_budget": 0, "removed": 0, "old_median": 3,
                          "old_over_budget": 0}, self.report["workers"]["worktree_branches"])
        self.assertEqual([{"day": "2026-10-03", "added": 3, "removed": 0, "subject": "", "reason": None}],
                         [{k: v for k, v in m.items() if k != "sha"} for m in self.report["workers"]["trunk_merges"]])

    def test_before_a_merge_a_branch_adding_over_the_budget_fails_unless_allowed(self):
        def run(*extra):
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                code = retro_metrics.main(["--repo", self.repo, "--rev", "main", "--branch", "worktree-agent-dead",
                                           *extra])
            return code, printed.getvalue()
        code, said = run("--budget", "3")
        self.assertEqual(1, code)
        self.assertIn("+4 -10", said)
        self.assertIn("OVER", said)
        code, said = run("--budget", "3", "--allow-over", "one migration, cannot be cut")
        self.assertEqual(0, code)
        self.assertIn("Over budget: one migration, cannot be cut", said, "the line for the merge message")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run("--budget", "3", "--allow-over", " ")
        self.assertEqual(0, run("--budget", "4")[0], "the budget counts added lines: 10 removed are not over 4")
        self.assertEqual(0, run()[0])

    def test_no_transcript_text_reaches_any_output(self):
        records, compactions = retro_metrics.transcript_facts(self.transcripts)
        self.assertEqual((13, 2), (len(records), len(compactions)))
        self.assertNotIn(CANARY, json.dumps([records, compactions]))
        for path in (self.out, self.json):
            with open(path, encoding="utf-8") as handle:
                self.assertNotIn(CANARY, handle.read())


class AWorkerMergedUnderItsOwnBranchName(unittest.TestCase):
    """A worker that renamed its branch merges as `Merge perf/thing`: the run is tied to the merge by a `Worktree:`
    line in the merge's message, which --branch prints while the worker's worktree still exists."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="retro-worktree-")
        self.addCleanup(_remove, self.folder)
        os.makedirs(os.path.join(self.folder, "main"))
        self.repo = ARepository(os.path.join(self.folder, "main"))
        self.repo.commit("2026-10-01", "start", ("a.txt", "a\n"))
        self.tree = os.path.join(self.folder, "main", ".claude", "worktrees", "agent-beef")
        self.repo.git("worktree", "add", "-q", "-b", "perf/thing", self.tree)

    def test_the_guard_prints_the_line_and_the_merge_it_leads_to_ties_the_run(self):
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            retro_metrics.main(["--repo", self.repo.folder, "--rev", "main", "--branch", "perf/thing"])
        self.assertIn("Worktree: agent-beef", printed.getvalue())
        started = {"worktree-agent-beef": 0}
        self.assertEqual({"worktree-agent-beef": "no merge found"},
                         retro_metrics.branch_states(self.repo.folder, "main", started))
        self.repo.git("-C", self.tree, "commit", "-q", "--allow-empty", "-m", "work")
        self.repo.git("merge", "-q", "--no-ff", "perf/thing", "-m", "Merge perf/thing: a thing",
                      "-m", "Worktree: agent-beef")
        self.repo.git("worktree", "remove", "--force", self.tree)
        self.repo.git("branch", "-D", "perf/thing")
        self.assertEqual({"worktree-agent-beef": "merged"},
                         retro_metrics.branch_states(self.repo.folder, "main", started))

    def test_a_branch_checked_out_in_no_agent_worktree_gets_no_line(self):
        self.repo.git("branch", "elsewhere")
        self.assertEqual("", retro_metrics.worktree_line(self.repo.folder, "elsewhere"))


if __name__ == "__main__":
    unittest.main()
