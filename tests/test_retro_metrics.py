"""tools/retro_metrics.py counts a tiny git repository and a fake transcript folder made here, and keeps no text of a
transcript: a canary in a message's text, a tool's input and a user's record is in none of its outputs."""
import datetime
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
    repo.commit("2026-10-02", "code and a lifted test\n\nCo-Authored-By: Fable Model 9 <f@example.invalid>",
                ("tagpup/a.py", "x = 1\n" * 15),
                ("tests/test_b.py", '"""' + "Lift" + 'ed: test_one"""\ndef test_three():\n    pass\n'))
    repo.git("checkout", "-q", "-b", "worktree-agent-x")
    repo.commit("2026-10-02", "a worker's change", ("web/x.js", "a;\nb;\nc;\n"))
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--no-ff", "worktree-agent-x", "-m", "Merge worktree-agent-x: a thing", when="2026-10-03")
    repo.commit("2026-10-03", "findings only",
                ("docs/findings.md", HEAD + "| 1 | 2026-10-01 | wrong: a thing | fixed: abc |\n"
                 "| 2 | 2026-10-03 | wrong: the reviewer saw it | open |\n"),
                ("docs/history/findings_closed_0001-0250.md", HEAD + "| 3 | 2026-10-02 | low: x | obsolete: gone |\n"))
    repo.commit("2026-10-03", "Revert the first test", remove=["tests/test_a.py"])
    repo.commit("2026-10-05", "after the window", ("tagpup/b.py", "y = 2\n"))
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
    agent = [record("assistant", "fable-test-1", 2, "a1", sidechain=True, input_tokens=1,
                    cache_creation_input_tokens=0, cache_read_input_tokens=0)]
    os.makedirs(os.path.join(folder, "s-1", "subagents"))
    for path, records in (("s-1.jsonl", main), ("s-1/subagents/agent-a1.jsonl", agent)):
        with open(os.path.join(folder, *path.split("/")), "w", encoding="utf-8") as handle:
            handle.write("\n".join(json.dumps(r) for r in records) + "\nnot json " + CANARY + "\n")


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
        self.assertEqual((1, 1, 2, False), (fable["turns"], fable["input_tokens"], fable["output_tokens"],
                                             fable["priced"]))
        self.assertAlmostEqual(10 * 5e-6 + 20 * 25e-6 + 100 * 5e-6 * 1.25 + 1000 * 5e-6 * 0.1, u["cost_low"])
        self.assertAlmostEqual(3 * (10 * 5e-6 + 20 * 25e-6 + 100 * 5e-6 * 1.25 + 1000 * 5e-6 * 0.1), u["cost_high"])
        self.assertEqual(({"2026-10-02": 1}, {"2026-10-02": 1}, {"main": 1130, "subagent": 3}, ["fable-test-1"]),
                         (u["sessions_per_day"], u["subagent_files_per_day"], u["tokens_main_vs_subagent"],
                          u["unpriced_models"]))

    def test_workers(self):
        self.assertEqual({"count": 1, "median": 3, "p90": 3, "max": 3, "over_budget": 0},
                         self.report["workers"]["worktree_branches"])

    def test_no_transcript_text_reaches_any_output(self):
        records = retro_metrics.usage_records(self.transcripts)
        self.assertEqual(3, len(records))
        self.assertNotIn(CANARY, json.dumps(records))
        for path in (self.out, self.json):
            with open(path, encoding="utf-8") as handle:
                self.assertNotIn(CANARY, handle.read())


if __name__ == "__main__":
    unittest.main()
