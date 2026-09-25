"""The socket the server listens on, and the two seconds it used to cost per click.

`http://localhost:8080/` is the address in the browser's bar. On Windows `localhost`
resolves to `::1` before `127.0.0.1`, and both old servers bound an IPv4 socket only --
so every request the browser made went first to an address nothing was listening on
and waited for Windows to give up. Measured against the running server: 2.05s per
request, before any work at all. A grid of 24,000 face crops pays it on every new
connection. That is the whole finding. It is not a database problem and no amount of
indexing touches it; it is the listening socket, which tagpup.web.app.bind now makes.
"""
import os
import socket
import sys
import time
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

from tagpup.web import app as web  # noqa: E402
from tagpup.web import security  # noqa: E402


class Bound:
    """A listening socket on a free port, closed on the way out. A listening socket
    accepts a connection at the TCP level without anyone calling accept()."""

    def __enter__(self):
        self.sock = web.bind(0)
        self.port = self.sock.getsockname()[1]
        return self

    def __exit__(self, *exc):
        self.sock.close()


class TestTheServerAnswersOnBothStacks(unittest.TestCase):
    """Whichever address `localhost` resolves to first has to be a live one."""

    def test_it_answers_on_ipv4(self):
        with Bound() as s:
            socket.create_connection(("127.0.0.1", s.port), timeout=5).close()

    @unittest.skipUnless(socket.has_ipv6, "no IPv6 on this machine")
    def test_it_answers_on_ipv6(self):
        with Bound() as s:
            if s.sock.family != socket.AF_INET6:
                self.skipTest("dual-stack socket unavailable; fell back to IPv4")
            try:
                socket.create_connection(("::1", s.port), timeout=5).close()
            except OSError as e:
                self.fail("nothing was listening on ::1, which is where `localhost`"
                          " goes first on Windows: %s" % e)

    @unittest.skipUnless(socket.has_ipv6, "no IPv6 on this machine")
    def test_connecting_by_name_does_not_stall(self):
        """The symptom as it was actually felt: a wait before any work is done.

        A failed IPv6 attempt costs about two seconds on Windows before the fall back
        to IPv4. A generous ceiling here, because this is a timing test on a shared
        machine and the thing it is catching is not subtle.
        """
        with Bound() as s:
            start = time.time()
            socket.create_connection(("localhost", s.port), timeout=10).close()
            elapsed = time.time() - start
        self.assertLess(
            elapsed, 1.0,
            "a connection to localhost took %.2fs before doing anything; the server is"
            " not listening on the address localhost resolves to first" % elapsed,
        )


class TestAPortInUseIsRefused(unittest.TestCase):
    """Two servers must never share a port.

    On Windows SO_REUSEADDR let a second server bind a port a live one held, and the
    two then took each other's connections. Two test runs did that at once: one run's
    request reached the other run's server, where the folder picker was not mocked,
    and it opened on the desktop of the person using the app.
    """

    def test_a_second_server_on_the_same_port_fails_to_start(self):
        with Bound() as s:
            try:
                second = web.bind(s.port)
            except OSError:
                return
            second.close()
            self.fail("a second server bound port %d while the first was listening"
                      " on it; requests would go to either" % s.port)


class TestHostParsing(unittest.TestCase):
    """`[::1]:8080` is a host and a port, not a host called `[`."""

    def test_a_bracketed_ipv6_host_is_recognised(self):
        self.assertEqual("::1", security.hostname_of("[::1]:8080"))
        self.assertEqual("::1", security.hostname_of("[::1]"))

    def test_ordinary_hosts_still_parse(self):
        self.assertEqual("localhost", security.hostname_of("localhost:8080"))
        self.assertEqual("127.0.0.1", security.hostname_of("127.0.0.1:8080"))
        self.assertEqual("localhost", security.hostname_of("LocalHost"))
        self.assertEqual("", security.hostname_of(""))


if __name__ == "__main__":
    unittest.main()
