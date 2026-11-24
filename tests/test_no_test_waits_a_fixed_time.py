"""A test waits for what it waits for, not for a fixed time.

Fixed sleeps cost the suite minutes of every run: the folder watcher's file slept 1-2 s after each sync to show no
second one came, 23 times. A test now waits through tests/waiting.py (`wait_until`: returns as soon as the thing has
happened, fails naming it when it never does), and shows that nothing happens by waiting for something that comes after
it (a barrier). A sleep of half a second or more in a test, or one whose length is worked out, carries the reason on its
line (`# wait: ...`): the few that have no event to wait for (a stopped watcher hears nothing; a child process must be
seen blocked) are listed by the reason they give.
"""
import ast
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import waiting  # noqa: E402

TESTS = os.path.dirname(os.path.abspath(__file__))
#: Seconds from which a fixed sleep needs its reason.
LONG = 0.5
REASON = "# wait:"


def _sleeps(source):
    """[(line number, the argument's value or None when it is worked out)] of each time.sleep in `source`; a sleep in a
    child process's script (a string) is not a call here."""
    tree = ast.parse(source)
    bare = any(isinstance(node, ast.ImportFrom) and node.module == "time" and any(a.name == "sleep" for a in node.names)
               for node in ast.walk(tree))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        called = node.func
        if (isinstance(called, ast.Attribute) and called.attr == "sleep" and isinstance(called.value, ast.Name)
                and called.value.id == "time") or (bare and isinstance(called, ast.Name) and called.id == "sleep"):
            try:
                seconds = float(ast.literal_eval(node.args[0]))
            except (ValueError, TypeError, SyntaxError):
                seconds = None
            found.append((node.lineno, seconds))
    return found


def unexplained(source):
    """The lines of `source` that sleep LONG or more, or for a time worked out, with no reason on the line."""
    lines = source.splitlines()
    return [number for number, seconds in _sleeps(source)
            if (seconds is None or seconds >= LONG) and REASON not in lines[number - 1]]


class NoTestWaitsAFixedTime(unittest.TestCase):
    def test_a_sleep_of_half_a_second_or_one_worked_out_says_why_on_its_line(self):
        bad = []
        for name in sorted(os.listdir(TESTS)):
            if not name.endswith(".py") or name == "waiting.py":
                continue
            with open(os.path.join(TESTS, name), encoding="utf-8") as handle:
                bad += ["tests/%s:%d" % (name, number) for number in unexplained(handle.read())]
        self.assertEqual([], bad, "a fixed sleep with no reason: wait with tests/waiting.py wait_until for what it "
                                  "waits for, or say on its line why nothing can be waited for (%s ...)" % REASON)

    def test_the_guard_reads_a_literal_a_sum_a_worked_out_time_and_the_reason(self):
        source = "\n".join([
            "import time",
            "time.sleep(0.02)",
            "time.sleep(1.5)",
            "time.sleep(0.25 + 0.25)",
            "time.sleep(watcher.debounce)",
            "time.sleep(2)   # wait: a child must be seen blocked",
            "'''time.sleep(60) in a child's script'''",
        ])
        self.assertEqual([3, 4, 5], unexplained(source))
        self.assertEqual([2], unexplained("from time import sleep\nsleep(0.9)\n"))


class WaitingUntilSomethingHappens(unittest.TestCase):
    def test_it_returns_as_soon_as_the_thing_has_happened_with_what_the_condition_gave(self):
        ready_at = time.monotonic() + 0.1
        started = time.monotonic()
        found = waiting.wait_until(lambda: time.monotonic() >= ready_at and "ready", timeout=10, interval=0.01)
        self.assertEqual("ready", found)
        self.assertLess(time.monotonic() - started, 2.0, "it waited out its time")

    def test_a_thing_that_never_happens_fails_loudly_naming_it_after_its_time(self):
        started = time.monotonic()
        with self.assertRaises(waiting.NeverHappened) as failed:
            waiting.wait_until(lambda: [], timeout=0.2, interval=0.01, what="the watcher's sync of Regatta")
        self.assertGreaterEqual(time.monotonic() - started, 0.2)
        self.assertIn("the watcher's sync of Regatta", str(failed.exception))
        self.assertIn("[]", str(failed.exception), "the last look it got")
        self.assertIsInstance(failed.exception, AssertionError, "a failure of the test, not an error in it")


if __name__ == "__main__":
    unittest.main()
