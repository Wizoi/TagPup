"""The waits for a share's look (tagpup.files.shares.bounded joins its ShareLook thread), counted.

What a share gone away costs a page is how many times it is waited for, and for how long each time.
Timed in seconds instead, a test measured the machine: on a busy one the work around a wait that never
happened took longer than the limit (#721).

    waits, counting = share_waits.counted()
    with counting:
        ...
    self.assertEqual([0.3], waits)     # one wait, of 0.3 s
"""
import threading
from unittest import mock


def counted():
    """(waits, patcher): within the patcher, the timeout of each join of a ShareLook thread is appended to waits."""
    waits = []
    real = threading.Thread.join

    def join(thread, timeout=None):
        if thread.name == "ShareLook":
            waits.append(timeout)
        return real(thread, timeout)
    return waits, mock.patch.object(threading.Thread, "join", join)
