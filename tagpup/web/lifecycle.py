"""Which version answers, whether the server takes new work, and letting the work under
way finish before the always-on process moves it onto a new version (docs/ARCHITECTURE.md,
phase 8, "Updating itself").

The supervisor (tagpup.supervisor) runs the web server as its child. When a newer commit
has been installed it asks the server to drain -- POST /api/server/drain, with the token
it gave the server (tagpup.supervisor.TOKEN, which tagpup_web hands the Lifecycle) -- and
moves it onto the new version only once the drain says it is done:

- A drain is refused at once, taking nothing away, while work runs beside the requests:
  a Suggest run, an index, a recurring job (the background tasks' busy(), tagpup.runtime);
  and, asked for a quiet moment (`quiet`), while a request came in that many seconds
  ago -- someone is using the app. The supervisor asks again later. An update never
  interrupts a write.
- Otherwise the server stops taking new work: each new request is answered 503 with
  Retry-After, which the pages wait out and send again (web/common/api.js). The
  background tasks are stopped, and the requests already in flight -- a journaled write
  among them -- are let finish.
- When nothing is in flight and nothing runs, it is drained, and the supervisor stops it.
  When the deadline passes first, it takes work again, and says what it waited for.

Without the always-on process, a launcher of another version drains it the same way
(tagpup.launcher), with the token this server wrote in its record (`launcher_token`), and
only from this machine; drained, the launcher ends it and serves in its place.

A server drained and never stopped -- its supervisor gone -- takes work again after
LEFT_DRAINED. GET /api/server says which version answers, which the gear shows, and every
response says it in X-TagPup-Version, by which a page left open learns that the server
was replaced (web/common/api.js).
"""
import hmac
import logging
import threading
import time

from flask import Blueprint, current_app, jsonify, request

from tagpup.jobs import bulk_edits as bulk_edit_jobs
from tagpup.jobs import indexing as indexing_jobs
from tagpup.jobs import suggestions as suggestion_jobs
from tagpup import launcher
from tagpup.services import libraries as library_actions
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
#: is, and the drain itself -- as the gate sees them, before the library's middleware
#: takes /<library> off the path.
#:
#: The bare status (/api/server) is the supervisor's and startup.py's. The pages ask it
#: under their library's address (/<library>/api/server: the gear's version, and
#: api.js's probe after an /api/ image failed), and that one is NOT exempt, on purpose:
#: while the server drains it is turned away 503 with X-TagPup-Updating, which is how the
#: probe learns the server is away. The drain and the resume are exempt under any
#: library's address too: counted, a drain would wait for itself.
STATUS, DRAIN, RESUME = "/api/server", "/api/server/drain", "/api/server/resume"
EXEMPT = frozenset({STATUS, DRAIN, RESUME})
CONTROL = (DRAIN, RESUME)


#: What only watches the server: the Activity page and its reads (tagpup.web.activity_routes),
#: which it asks every few seconds while open. Counted in flight like any request, but not
#: as somebody using the app: an open Activity page would otherwise never leave the
#: update its quiet moment (the drain's `quiet`), and hold it back for an hour.
WATCHING = ("/activity/", "/api/activity/")


def only_watches(method, path):
    """Is a request of `method` for `path`, as the gate sees it, a read that only watches?"""
    return method in ("GET", "HEAD") and path.startswith(WATCHING)


def exempt(path):
    """Is `path`, as the gate sees it, answered while draining and not counted as work?"""
    if path in EXEMPT:
        return True
    # A URL's path, not a tag: what follows its first part, "/<library>".
    second = path.find("/", 1)
    return second > 0 and path[second:] in CONTROL

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
    edits = bulk_edit_jobs.running()
    if edits:
        found.append("%d bulk edit(s)" % edits)
    bringing = library_actions.bringing_up_to_date()
    if bringing:
        found.append("bringing %d library(ies) up to date" % bringing)   # the startup migrations (#664)
    return found


#: Where a launcher's drain may come from: this machine.
LOOPBACK = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


class Lifecycle:
    """One server process's: `version` (the installed version's name, or None from a
    checkout), `token` (the supervisor's, or None), `launcher_token` (the one in this
    server's record, tagpup.launcher, or None), `background` (tagpup.runtime.Background,
    stopped by a drain and started again by a resume), `work()` (long_work, or a test's)
    and `clock`."""

    def __init__(self, version=None, token=None, background=None, work=long_work, clock=time.monotonic,
                 launcher_token=None):
        self.version = version
        #: What X-TagPup-Version says: the version, or that it runs from a checkout.
        self.version_label = version or launcher.FROM_A_CHECKOUT
        #: When this server started (seconds since the epoch): the Activity page's "running since".
        self.started = time.time()
        self._token = token or None
        self._launcher_token = launcher_token or None
        self._background = background
        self._work = work
        self._clock = clock
        self._changed = threading.Condition()
        self._in_flight = 0
        self._closed = False
        self._drained_at = None
        #: When a request last came in (not the status or the drain): a quiet moment is
        #: some time after it.
        self._last_request = None
        #: A drain is under way: a second joins it.
        self._draining = False

    # ---- What it says ---------------------------------------------------------------

    @property
    def background(self):
        """The background tasks this process runs (tagpup.runtime.Background), or None."""
        return self._background

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

    def allows_launcher(self, token, remote):
        """Is `token` the one in this server's record, sent from this machine (`remote`, the
        request's address)? Another machine is refused whatever it sends."""
        return (self._launcher_token is not None and remote in LOOPBACK and isinstance(token, str)
                and hmac.compare_digest(token, self._launcher_token))

    def authorizes(self, headers, remote):
        """May a request with `headers` from `remote` drain or resume this server: the
        supervisor's token, or a launcher's from this machine?"""
        return self.allows(headers.get(TOKEN_HEADER)) or self.allows_launcher(headers.get(launcher.HEADER), remote)

    # ---- The gate every request passes ------------------------------------------------

    def wrap(self, app):
        """The WSGI app `app` behind this gate: counted while in flight, refused 503 while
        draining."""
        return _Gate(self, app)

    def _enter(self, watching=False):
        """Count a request in; False when it is to be refused. One that only `watching`
        (only_watches) is not a request somebody made: the quiet moment is not reset."""
        with self._changed:
            left = self._closed and self._drained_at is not None and self._clock() - self._drained_at > LEFT_DRAINED
            if not left:
                if self._closed:
                    return False
                self._in_flight += 1
                if not watching:
                    self._last_request = self._clock()
                return True
        logger.warning("Drained %ds ago and not stopped: taking work again.", LEFT_DRAINED)
        self.resume()
        return self._enter(watching)

    def _leave(self):
        with self._changed:
            self._in_flight -= 1
            self._changed.notify_all()

    # ---- Draining ---------------------------------------------------------------------

    def drain(self, seconds=DRAIN_SECONDS, quiet=0):
        """Stop taking new work and wait up to `seconds` for what is under way to finish.
        {"drained": True}, or {"drained": False, "waiting_for": [...]}: refused at once,
        nothing turned away, while work runs beside the requests or a request came in
        the last `quiet` seconds (someone is using the app); or the deadline passed, and
        the server takes work again."""
        deadline = self._clock() + seconds
        with self._changed:
            if self._draining:
                # One at a time: this one waits for that one, and says how it ended.
                while self._draining and self._clock() < deadline:
                    self._changed.wait(min(0.2, max(0.0, deadline - self._clock())))
                if self._closed and self._drained_at is not None:
                    return {"drained": True}
                return {"drained": False, "waiting_for": ["another drain"]}
            if not self._closed:
                since = None if self._last_request is None else self._clock() - self._last_request
                if quiet and since is not None and since < quiet:
                    return {"drained": False, "waiting_for": ["a request %ds ago" % since]}
                busy = self.busy()
                if busy:
                    logger.info("An update waits: %s under way.", ", ".join(busy))
                    return {"drained": False, "waiting_for": busy}
                self._closed = True
                self._drained_at = None
            in_flight = self._in_flight
            self._draining = True
        try:
            return self._drain_until(deadline, seconds, in_flight)
        finally:
            with self._changed:
                self._draining = False
                self._changed.notify_all()

    def _drain_until(self, deadline, seconds, in_flight):
        logger.info("Draining for an update: taking no new work; %d request(s) in flight.", in_flight)
        if self._background is not None:
            self._background.stop(timeout=max(0.0, deadline - self._clock()))
        while True:
            with self._changed:
                if not self._closed:
                    # Taken back while it waited (a resume, or left drained too long).
                    return {"drained": False, "waiting_for": ["taken back"]}
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
        path = environ.get("PATH_INFO") or "/"
        label = (launcher.VERSION_HEADER, self.lifecycle.version_label)

        def start_response_saying_the_version(status, headers, exc_info=None):
            headers = [h for h in headers if h[0].lower() != label[0].lower()] + [label]
            if exc_info is None:
                return start_response(status, headers)
            return start_response(status, headers, exc_info)
        return self._call(environ, start_response_saying_the_version, path)

    def _call(self, environ, start_response, path):
        if exempt(path):
            return self.app(environ, start_response)
        if not self.lifecycle._enter(only_watches(environ.get("REQUEST_METHOD", "GET"), path)):
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
    supervisor's, before it moves the server onto a new version; or a launcher's on this
    machine, before it serves another version in its place (tagpup.launcher)."""
    lifecycle = _lifecycle()
    if not lifecycle.authorizes(request.headers, request.remote_addr):
        return responses.error(403, "Only the process that started this server, or a launch on this machine, "
                                    "may drain it")
    body = request.get_json(silent=True) or {}
    seconds = body.get("seconds", DRAIN_SECONDS)
    quiet = body.get("quiet", 0)
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds <= MAX_DRAIN_SECONDS:
        return responses.error(400, "seconds must be a number of seconds, more than 0 and at most %d"
                               % MAX_DRAIN_SECONDS)
    if isinstance(quiet, bool) or not isinstance(quiet, (int, float)) or quiet < 0:
        return responses.error(400, "quiet must be a number of seconds, 0 or more")
    return jsonify(dict(lifecycle.drain(seconds, quiet), success=True))


@routes.post(RESUME)
def resume():
    """Take work again after a drain (the supervisor's, when it could not move the server)."""
    lifecycle = _lifecycle()
    if not lifecycle.authorizes(request.headers, request.remote_addr):
        return responses.error(403, "Only the process that started this server may resume it")
    return jsonify({"success": True, "resumed": lifecycle.resume()})
