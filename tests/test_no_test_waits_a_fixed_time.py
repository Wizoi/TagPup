"""A test waits for what it waits for, not for a fixed time.

Fixed sleeps cost the suite minutes of every run: the folder watcher's file slept 1-2 s after each sync to show no
second one came, 23 times. A test now waits through tests/waiting.py (`wait_until`: returns as soon as the thing has
happened, fails naming it when it never does), and shows that nothing happens by waiting for something that comes after
it (a barrier). A sleep of half a second or more in a test, or one whose length is worked out, carries the reason on its
line (`# wait: ...`): the few that have no event to wait for (a stopped watcher hears nothing; a child process must be
seen blocked) are listed by the reason they give.

A loop that sleeps is a wait written by hand: fifteen copies of one (`until`, `wait_for`, `wait_until`), each with its own
deadline and its own failure, some returning quietly when time ran out. tests/waiting.py holds the one; a loop that
sleeps anywhere else says on its line, or on its sleep's, why it is not a wait for something (`# wait: ...`).
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


def _sleeps_in(node):
    """The sleeps of a loop's own body (not of a function defined in it): time.sleep, a bare sleep, Event().wait(n)."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(child, ast.Call) and child.args:
            called = child.func
            if ((isinstance(called, ast.Attribute) and called.attr == "sleep") or
                    (isinstance(called, ast.Name) and called.id == "sleep") or
                    (isinstance(called, ast.Attribute) and called.attr == "wait" and isinstance(called.value, ast.Call)
                     and getattr(called.value.func, "attr", getattr(called.value.func, "id", None)) == "Event")):
                yield child
        yield from _sleeps_in(child)


def hand_written_waits(source):
    """The lines of `source` where a loop sleeps, with no reason on the loop's line or on its sleep's."""
    lines = source.splitlines()
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.While, ast.For)):
            continue
        sleeps = list(_sleeps_in(node))
        if sleeps and not any(REASON in lines[at - 1] for at in [node.lineno] + [each.lineno for each in sleeps]):
            found.append(node.lineno)
    return found


def _sources():
    """(name, text) of each test module but the one that owns waiting."""
    for name in sorted(os.listdir(TESTS)):
        if name.endswith(".py") and name != "waiting.py":
            with open(os.path.join(TESTS, name), encoding="utf-8") as handle:
                yield name, handle.read()


def unexplained(source):
    """The lines of `source` that sleep LONG or more, or for a time worked out, with no reason on the line."""
    lines = source.splitlines()
    return [number for number, seconds in _sleeps(source)
            if (seconds is None or seconds >= LONG) and REASON not in lines[number - 1]]


class NoTestWaitsAFixedTime(unittest.TestCase):
    def test_a_sleep_of_half_a_second_or_one_worked_out_says_why_on_its_line(self):
        bad = ["tests/%s:%d" % (name, number) for name, source in _sources() for number in unexplained(source)]
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

    def test_a_loop_that_sleeps_is_tests_waiting_or_says_why_it_is_not_a_wait(self):
        bad = ["tests/%s:%d" % (name, number) for name, source in _sources() for number in hand_written_waits(source)]
        self.assertEqual([], bad, "a wait loop written by hand: use tests/waiting.py (wait_until, holds_for), or say "
                                  "on the loop's line why it is no wait for something (%s ...)" % REASON)

    def test_the_guard_reads_a_deadline_loop_a_retry_an_event_wait_and_the_reason(self):
        source = "\n".join([
            "import threading, time",
            "while time.monotonic() < deadline:",
            "    time.sleep(0.05)",
            "for _ in range(100):",
            "    if done(): break",
            "    threading.Event().wait(0.01)",
            "while True:   # wait: the scenario, a writer that keeps writing",
            "    time.sleep(0.01)",
            "for photo in photos:",
            "    time.sleep(0.01)   # wait: the card's work on it",
            "for photo in photos:",
            "    def later():",
            "        time.sleep(1)",
            "while not stop.wait(0.1):",
            "    look()",
        ])
        self.assertEqual([2, 4], hand_written_waits(source))


class WaitingUntilSomethingHappens(unittest.TestCase):
    def test_a_window_that_holds_is_waited_out_and_one_broken_ends_at_once(self):
        started = time.monotonic()
        self.assertTrue(waiting.holds_for(lambda: True, 0.1))
        self.assertGreaterEqual(time.monotonic() - started, 0.1)
        broken_at = time.monotonic() + 0.05
        started = time.monotonic()
        self.assertFalse(waiting.holds_for(lambda: time.monotonic() < broken_at, 10))
        self.assertLess(time.monotonic() - started, 2.0, "it waited out its window after the condition broke")

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
