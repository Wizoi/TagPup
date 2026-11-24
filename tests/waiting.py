"""Waiting in a test for something to happen, never for a fixed time.

A fixed sleep is the slowest wait there is and still races: it waits its whole length when the thing happened at once,
and not long enough on a loaded machine. `wait_until` returns as soon as `condition()` holds and fails loudly, naming
what it waited for, when it never does. A test that must show that nothing happens waits for something that comes after
it instead (a barrier: the notification of a file written after the ones under test, a look counted), and only where
there is no such thing for the least the product's own interval allows, with a reason on the line
(tests/test_no_test_waits_a_fixed_time.py reads it).
"""
import time


class NeverHappened(AssertionError):
    """What a test waited for did not happen in its time."""


def wait_until(condition, timeout=20.0, interval=0.01, what=None):
    """`condition()`'s first truthy value, as soon as it has one; NeverHappened after `timeout` seconds, naming `what`
    (or the condition) and the last value it gave. An exception the condition raises is the test's, not retried."""
    deadline = time.monotonic() + timeout
    while True:
        found = condition()
        if found:
            return found
        if time.monotonic() >= deadline:
            raise NeverHappened("waited %.1f s for %s; the last look gave %r"
                                % (timeout, what or getattr(condition, "__name__", repr(condition)), found))
        time.sleep(interval)
