"""Waiting in a test for something to happen, never for a fixed time.

A fixed sleep is the slowest wait there is and still races: it waits its whole length when the thing happened at once,
and not long enough on a loaded machine. `wait_until` returns as soon as `condition()` holds and fails loudly, naming
what it waited for, when it never does. A test that must show that nothing happens waits for something that comes after
it instead (a barrier: the notification of a file written after the ones under test, a look counted), and only where
there is no such thing for the least the product's own interval allows, with a reason on the line
(tests/test_no_test_waits_a_fixed_time.py reads it); `holds_for` is that window when the product names one.

These are the one wait loop of the tests: a loop that sleeps anywhere else is refused by the same guard unless its line
says why it is not a wait for something (a scenario's pacing, a retry of a delete off every test's time).
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


def comes_true(condition, timeout=20.0, interval=0.01):
    """Whether `condition()` came true within `timeout` seconds, as soon as it does: for a test that asserts it with its
    own message (`self.assertTrue(comes_true(...), status)`) or goes on either way."""
    try:
        return bool(wait_until(condition, timeout, interval))
    except NeverHappened:
        return False


def holds_for(condition, seconds, interval=0.02):
    """Whether `condition()` held at every look for `seconds`: False at the first look where it does not. For a test
    that must show something does NOT happen inside a window the product names (a process that must outlive
    RECORD_WITHIN, a line a quiet look must not write) and has nothing after it to wait for instead."""
    deadline = time.monotonic() + seconds
    while True:
        if not condition():
            return False
        if time.monotonic() >= deadline:
            return True
        time.sleep(interval)
