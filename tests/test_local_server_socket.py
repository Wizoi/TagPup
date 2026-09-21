"""The socket both servers listen on, and the two seconds it used to cost per click.

`http://localhost:8080/` is the address in the browser's bar. On Windows `localhost`
resolves to `::1` before `127.0.0.1`, and both servers bound an IPv4 socket only -- so
every request the browser made went first to an address nothing was listening on and
waited for Windows to give up. Measured against the running server: 2.05s per request,
before any work at all. A grid of 24,000 face crops pays it on every new connection.

That is the whole finding. It is not a database problem and no amount of indexing
touches it; it is the listening socket.
"""
import http.client
import os
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import localserver


class Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        if not localserver.is_local_request(self):
            return
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


class ServerOnAPort:
    """A ThreadedHTTPServer on a free port, stopped on the way out."""

    def __enter__(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        self.port = probe.getsockname()[1]
        probe.close()

        self.server = localserver.ThreadedHTTPServer(("", self.port), Echo)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.2)
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


class TestTheServerAnswersOnBothStacks(unittest.TestCase):
    """Whichever address `localhost` resolves to first has to be a live one."""

    def test_it_answers_on_ipv4(self):
        with ServerOnAPort() as s:
            conn = http.client.HTTPConnection("127.0.0.1", s.port, timeout=5)
            conn.request("GET", "/")
            self.assertEqual(200, conn.getresponse().status)
            conn.close()

    @unittest.skipUnless(socket.has_ipv6, "no IPv6 on this machine")
    def test_it_answers_on_ipv6(self):
        with ServerOnAPort() as s:
            if s.server.address_family != socket.AF_INET6:
                self.skipTest("dual-stack socket unavailable; fell back to IPv4")
            conn = http.client.HTTPConnection("::1", s.port, timeout=5)
            try:
                conn.request("GET", "/")
            except OSError as e:
                self.fail("nothing was listening on ::1, which is where `localhost`"
                          " goes first on Windows: %s" % e)
            self.assertEqual(200, conn.getresponse().status)
            conn.close()

    @unittest.skipUnless(socket.has_ipv6, "no IPv6 on this machine")
    def test_connecting_by_name_does_not_stall(self):
        """The symptom as it was actually felt: a wait before any work is done.

        A failed IPv6 attempt costs about two seconds on Windows before the fall back
        to IPv4. A generous ceiling here, because this is a timing test on a shared
        machine and the thing it is catching is not subtle.
        """
        with ServerOnAPort() as s:
            start = time.time()
            conn = http.client.HTTPConnection("localhost", s.port, timeout=10)
            conn.request("GET", "/")
            conn.getresponse().read()
            conn.close()
            elapsed = time.time() - start

        self.assertLess(
            elapsed, 1.0,
            "a request to localhost took %.2fs before doing anything; the server is"
            " not listening on the address localhost resolves to first" % elapsed,
        )


class TestHostParsing(unittest.TestCase):
    """`[::1]:8080` is a host and a port, not a host called `[`."""

    def test_a_bracketed_ipv6_host_is_recognised(self):
        self.assertEqual("::1", localserver.hostname_of("[::1]:8080"))
        self.assertEqual("::1", localserver.hostname_of("[::1]"))

    def test_ordinary_hosts_still_parse(self):
        self.assertEqual("localhost", localserver.hostname_of("localhost:8080"))
        self.assertEqual("127.0.0.1", localserver.hostname_of("127.0.0.1:8080"))
        self.assertEqual("localhost", localserver.hostname_of("LocalHost"))
        self.assertEqual("", localserver.hostname_of(""))

    def test_the_ipv6_spelling_is_actually_in_the_allow_list(self):
        """It was listed as `[::1]` and compared against `[`, so it never matched."""
        self.assertIn(localserver.hostname_of("[::1]:8080"), localserver.LOCAL_HOSTS)

    def test_somewhere_else_is_still_refused(self):
        for authority in ("example.com:8080", "[2001:db8::1]:8080", "192.168.1.9:8080"):
            self.assertNotIn(
                localserver.hostname_of(authority), localserver.LOCAL_HOSTS,
                "%s was treated as local" % authority,
            )


if __name__ == "__main__":
    unittest.main()
