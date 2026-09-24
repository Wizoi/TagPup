"""The HTTP socket both servers listen on, who they will answer, and what they log.

Both TagPup and TagTuner had their own copy of a two-line `ThreadedHTTPServer` and a
word-for-word copy of the Host and Origin check. They also had the same two bugs, and
the first one cost two seconds on every click.

**Listen on IPv6 as well as IPv4.** `http://localhost:8080/` is the address in the
browser's bar, and on Windows `localhost` resolves to `::1` before `127.0.0.1`. The
servers bound an IPv4 socket only, so the browser's first connection attempt went to an
address nothing was listening on and sat there until Windows gave up -- measured at
2.05s, on every single request, before a byte of work was done. Loading a grid of
24,000 face crops pays that on every new connection.

A dual-stack socket answers both, so whichever address `localhost` resolves to first is
the right one.

**Parse a bracketed host properly.** The check did `host.split(":")[0]`, which turns
`[::1]:8080` into `[`. So the IPv6 spelling was in the allow-list and could never match
it -- harmless while nothing listened on IPv6, and a locked door the moment something
did.
"""
import logging
import os
import socket
import sys
import time
import urllib.parse
from socketserver import ThreadingTCPServer

import _root  # noqa: F401
from tagpup.logs import REQUESTS

requests_log = logging.getLogger(REQUESTS)

#: A request slower than this is logged with how long it took.
SLOW_REQUEST_SECONDS = 1.0

#: The only hosts these servers will answer to. They hold a private photo library and
#: bind a port on the machine, so the Host check is what keeps a page on the internet
#: from driving them through the browser (DNS rebinding), and the Origin check is what
#: keeps one from reading the answers (CSRF).
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class ThreadedHTTPServer(ThreadingTCPServer):
    """A threaded server that answers on IPv4 and IPv6 alike.

    Falls back to IPv4 where a dual-stack socket cannot be had -- a machine with IPv6
    switched off, or a platform that refuses to clear IPV6_V6ONLY. Losing IPv6 costs
    two seconds a request; failing to start costs everything.
    """

    # SO_REUSEADDR means different things on the two platforms. On POSIX it only
    # skips TIME_WAIT, which a restarting server wants. On Windows it lets a second
    # socket bind a port a live server already holds -- and the two then take each
    # other's connections. Two test runs did exactly that: one run's requests reached
    # the other run's server, where a mocked folder picker was real, and opened it on
    # the desktop of the person using the app. Windows gets SO_EXCLUSIVEADDRUSE instead,
    # so a port in use is refused, loudly.
    allow_reuse_address = os.name != "nt"
    address_family = socket.AF_INET6

    def __init__(self, server_address, handler, bind_and_activate=True):
        host, port = server_address[0], server_address[1]
        try:
            super().__init__((host or "::", port), handler, bind_and_activate)
        except OSError:
            self.address_family = socket.AF_INET
            super().__init__((host or "", port), handler, bind_and_activate)

    def server_bind(self):
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        if self.address_family == socket.AF_INET6:
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except (AttributeError, OSError):
                # Some platforms are v6-only and will not be talked out of it. The
                # server still works; `localhost` just pays the fallback again.
                pass
        super().server_bind()

    def handle_error(self, request, client_address):
        """A request that raised, logged with its traceback.

        socketserver printed it to stderr, which is a console nobody may be watching
        and which is gone when the window closes. A browser dropping a request it no
        longer wants -- thumbnails scrolled past -- is not a failure of this server.
        """
        error = sys.exc_info()[1]
        who = client_address[0] if client_address else "?"
        if who.startswith("::ffff:"):
            who = who[len("::ffff:"):]   # an IPv4 client, as the dual-stack socket spells it
        if isinstance(error, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            requests_log.debug("%s closed the connection before the answer was sent", who)
            return
        requests_log.exception("a request from %s failed", who)


class RequestLog:
    """Mixed into a request handler: every request slower than a second is logged.

    Timed from the moment the request line is parsed, so time spent waiting for a
    request to arrive is never counted as time spent answering it.
    """

    slow_request_seconds = SLOW_REQUEST_SECONDS

    def parse_request(self):
        self._request_started = time.perf_counter()
        return super().parse_request()

    def handle_one_request(self):
        self._request_started = None
        try:
            super().handle_one_request()
        finally:
            started = self._request_started
            if started is not None:
                took = time.perf_counter() - started
                if took >= self.slow_request_seconds:
                    requests_log.warning("slow: %s took %.2fs", self.requestline, took)


def hostname_of(authority):
    """The host out of a `host[:port]` authority, brackets and all handled.

    `[::1]:8080` -> `::1`, `localhost:8080` -> `localhost`. Returns it lowercased,
    since hostnames are not case-sensitive and the allow-list is written in lower case.
    """
    authority = (authority or "").strip()
    if authority.startswith("["):
        end = authority.find("]")
        if end != -1:
            return authority[1:end].lower()
    return authority.split(":")[0].lower()


def is_local_request(handler):
    """Is this request from a page on this machine, served by this server?

    Returns True to proceed. On refusal it sends the error itself and returns False,
    so a caller reads as `if not is_local_request(self): return`.
    """
    if hostname_of(handler.headers.get("Host", "")) not in LOCAL_HOSTS:
        handler.send_error(403, "Forbidden: Invalid Host Header")
        return False

    origin = handler.headers.get("Origin")
    if origin:
        netloc = urllib.parse.urlparse(origin).netloc
        if hostname_of(netloc) not in LOCAL_HOSTS:
            handler.send_error(403, "Forbidden: Cross-Origin Requests Denied")
            return False
    return True
