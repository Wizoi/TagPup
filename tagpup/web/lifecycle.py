"""Which version answers, whether the server takes new work, and letting the work under
way finish before the always-on process moves it onto a new version (docs/ARCHITECTURE.md,
phase 8, "Updating itself").

The supervisor (tagpup.supervisor) runs the web server as its child. When a newer commit
has been installed it asks the server to drain -- POST /api/server/drain, with the token
it gave the server (tagpup.supervisor.TOKEN, which tagpup_web hands the Lifecycle) -- and
moves it onto the new version only once the drain says it is done:

- A drain is refused at once, taking nothing away, while work runs beside the requests:
  a Suggest run, an index, a recurring job (the background tasks' busy(), tagpup.runtime).
  The supervisor asks again later. An update never interrupts a write.
- Otherwise the server stops taking new work: each new request is answered 503 with
  Retry-After, which the pages wait out and send again (web/common/api.js). The
  background tasks are stopped, and the requests already in flight -- a journaled write
  among them -- are let finish.
- When nothing is in flight and nothing runs, it is drained, and the supervisor stops it.
  When the deadline passes first, it takes work again, and says what it waited for.

A server drained and never stopped -- its supervisor gone -- takes work again after
LEFT_DRAINED. GET /api/server says which version answers, which the gear shows.
"""
import hmac
import logging
import threading
import time

from flask import Blueprint, current_app, jsonify, request

from tagpup.jobs import indexing as indexing_jobs
from tagpup.jobs import suggestions as suggestion_jobs
from tagpup.supervisor import TOKEN_HEADER
from tagpup.web import responses

logger = logging.getLogger(__name__)

#: The header on a refusal while draining, which tells a page to wait and send again.
UPDATING_HEADER = "X-TagPup-Updating"
RETRY_AFTER_SECONDS = 2

#: How long a drain waits by default, and at most, for what is in flight.
DRAIN_SECONDS = 120
MAX_DRAIN_SECONDS = 1800

#: A drained server not stopped in this long takes work again: its supervisor is gone.
LEFT_DRAINED = 180

#: The paths answered while draining, and not counted as work: asking how the server
#: is, and the drain itself.
STATUS, DRAIN, RESUME = "/api/server", "/api/server/drain", "/api/server/resume"
EXEMPT = frozenset({STATUS, DRAIN, RESUME})

UPDATING_MESSAGE = "TagPup is moving to a new version; this is sent again in a moment."


def long_work():
    """The work under way in this process beside its requests, by what it is: what a drain
    will not interrupt."""
    found = []
    runs = suggestion_jobs.running()
    if runs:
        found.append("%d Suggest run(s)" % runs)
    indexing = indexing_jobs.running()
    if indexing:
        found.append("indexing in %d library(ies)" % indexing)
    return found


class Lifecycle:
    """One server process's: `version` (the installed version's name, or None from a
    checkout), `token` (the supervisor's, or None), `background` (tagpup.runtime.
    Background, stopped by a drain and started again by a resume), `work()` (long_work,
    or a test's) and `clock`."""

    def __init__(self, version=None, token=None, background=None, work=long_work, clock=time.monotonic):
        self.version = version
        self._token = token or None
        self._background = background
        self._work = work
        self._clock = clock
        self._changed = threading.Condition()
        self._in_flight = 0
        self._closed = False
        self._drained_at = None

    # ---- What it says ---------------------------------------------------------------

    def busy(self):
        """What runs beside the requests now: [what, ...]."""
        return list(self._work()) + (self._background.busy() if self._background is not None else [])

    def status(self):
        with self._changed:
            closed, in_flight = self._closed, self._in_flight
        return {"version": self.version, "supervised": self._token is not None, "taking_work": not closed,
                "requests": in_flight, "busy": self.busy()}

    def allows(self, token):
        """Is `token` the supervisor's?"""
        return self._token is not None and isinstance(token, str) and hmac.compare_digest(token, self._token)

    # ---- The gate every request passes ------------------------------------------------

    def wrap(self, app):
        """The WSGI app `app` behind this gate: counted while in flight, refused 503 while
        draining."""
        return _Gate(self, app)

    def _enter(self):
        """Count a request in; False when it is to be refused."""
        with self._changed:
            left = self._closed and self._drained_at is not None and self._clock() - self._drained_at > LEFT_DRAINED
            if not left:
                if self._closed:
                    return False
                self._in_flight += 1
                return True
        logger.warning("Drained %ds ago and not stopped: taking work again.", LEFT_DRAINED)
        self.resume()
        return self._enter()

    def _leave(self):
        with self._changed:
            self._in_flight -= 1
            self._changed.notify_all()

    # ---- Draining ---------------------------------------------------------------------

    def drain(self, seconds=DRAIN_SECONDS):
        """Stop taking new work and wait up to `seconds` for what is under way to finish.
        {"drained": True}, or {"drained": False, "waiting_for": [...]}: refused at once,
        nothing turned away, while work runs beside the requests; or the deadline passed,
        and the server takes work again."""
        deadline = self._clock() + seconds
        with self._changed:
            if not self._closed:
                busy = self.busy()
                if busy:
                    logger.info("An update waits: %s under way.", ", ".join(busy))
                    return {"drained": False, "waiting_for": busy}
                self._closed = True
                self._drained_at = None
            in_flight = self._in_flight
        logger.info("Draining for an update: taking no new work; %d request(s) in flight.", in_flight)
        if self._background is not None:
            self._background.stop(timeout=max(0.0, deadline - self._clock()))
        while True:
            with self._changed:
                waiting = (["%d request(s)" % self._in_flight] if self._in_flight else []) + self.busy()
                if not waiting:
                    self._drained_at = self._clock()
                    logger.info("Drained: nothing in flight, nothing running.")
                    return {"drained": True}
                left = deadline - self._clock()
                if left <= 0:
                    break
                self._changed.wait(min(0.2, left))
        logger.warning("Could not drain in %ds (waiting for %s); taking work again.", seconds, ", ".join(waiting))
        self.resume()
        return {"drained": False, "waiting_for": waiting}

    def resume(self):
        """Take work again, and start the background tasks a drain stopped."""
        with self._changed:
            was_closed = self._closed
            self._reopen()
        if was_closed and self._background is not None:
            self._background.start()
        return was_closed

    def _reopen(self):
        self._closed = False
        self._drained_at = None
        self._changed.notify_all()


class _Gate:
    """WSGI middleware: each request counted while in flight -- until its body has been
    sent or closed -- and, while the server drains, refused."""

    def __init__(self, lifecycle, app):
        self.lifecycle = lifecycle
        self.app = app

    def __call__(self, environ, start_response):
        if (environ.get("PATH_INFO") or "/") in EXEMPT:
            return self.app(environ, start_response)
        if not self.lifecycle._enter():
            body = ('{"success": false, "updating": true, "error": "%s"}' % UPDATING_MESSAGE).encode("utf-8")
            start_response("503 SERVICE UNAVAILABLE", [
                ("Content-Type", "application/json"), ("Content-Length", str(len(body))),
                ("Retry-After", str(RETRY_AFTER_SECONDS)), (UPDATING_HEADER, "1"),
                ("Cache-Control", "no-store")])
            return [body]
        try:
            body = self.app(environ, start_response)
        except BaseException:
            self.lifecycle._leave()
            raise
        return _Counted(body, self.lifecycle._leave)


class _Counted:
    """A response body that calls `done` once, when it has been sent or closed."""

    def __init__(self, body, done):
        self._body = body
        self._done = done
        self._finished = False
        self._lock = threading.Lock()

    def _finish(self):
        with self._lock:
            if self._finished:
                return
            self._finished = True
        self._done()

    def __iter__(self):
        try:
            yield from self._body
        finally:
            self._finish()

    def close(self):
        try:
            close = getattr(self._body, "close", None)
            if callable(close):
                close()
        finally:
            self._finish()


# ---- The routes, which both apps serve alike -----------------------------------------------

routes = Blueprint("server", __name__)


def _lifecycle():
    return current_app.config["LIFECYCLE"]


@routes.get(STATUS)
def server_status():
    """Which version answers, whether it takes work, and what runs now."""
    return jsonify(_lifecycle().status())


@routes.post(DRAIN)
def drain():
    """Stop taking new work and let what is under way finish (Lifecycle.drain): the
    supervisor's, before it moves the server onto a new version."""
    lifecycle = _lifecycle()
    if not lifecycle.allows(request.headers.get(TOKEN_HEADER)):
        return responses.error(403, "Only the process that started this server may drain it")
    body = request.get_json(silent=True) or {}
    seconds = body.get("seconds", DRAIN_SECONDS)
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds <= MAX_DRAIN_SECONDS:
        return responses.error(400, "seconds must be a number of seconds, more than 0 and at most %d"
                               % MAX_DRAIN_SECONDS)
    return jsonify(dict(lifecycle.drain(seconds), success=True))


@routes.post(RESUME)
def resume():
    """Take work again after a drain (the supervisor's, when it could not move the server)."""
    lifecycle = _lifecycle()
    if not lifecycle.allows(request.headers.get(TOKEN_HEADER)):
        return responses.error(403, "Only the process that started this server may resume it")
    return jsonify({"success": True, "resumed": lifecycle.resume()})
