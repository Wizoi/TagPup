"""Who a request may come from: a page on this machine, served by this server.

The apps hold a private photo library and bind a port on the machine, so the Host
check is what keeps a page on the internet from talking to them through the browser,
and the Origin check what keeps another local page from doing the same. Both were
scripts/localserver.py's, run at the top of every handler; here they are one
`before_request` hook, so no route can forget.

The same hook answers what is this PC's alone (THIS_PC_ONLY) to this PC's addresses only, whatever address the server
listens on (`tagpup_web.py --listen lan`, phase 10's): a second guard beside the loopback sockets (DECISIONS
2026-09-26). Every route is in it or listed with why it is not (tests/test_every_route_answers_this_pc_or_says_why.py).
"""
import os
import urllib.parse

from flask import abort, request

from tagpup.web import responses

#: The only hosts the apps answer to.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")

#: The addresses of this PC, as a request's remote address: what answers this PC only (THIS_PC_ONLY)
#: takes a request from one of these and refuses any other.
LOOPBACK = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})

_VIEWS = "The library's views answer this PC only"
_ACTIVITY = "The Activity page answers this PC only"

#: What answers this PC only, each a path and every path under it, with the sentence a request from any other address
#: is refused with (403, the pages' JSON error, tagpup.web.responses.error): the library's views carry photo paths and
#: names and the pictures are of people; Name faces from tags works on the whole library; the Activity page covers
#: every library of the home; the roots are this machine's map; a drain is a launch's or an install's on this machine
#: (tagpup.web.lifecycle, which also asks for the launcher's token). Paths as the app sees them, under the library the URL
#: names (tagpup.web.libraries.LibraryFromUrl).
THIS_PC_ONLY = (
    ("/api/library", _VIEWS),
    ("/api/photo-thumb", _VIEWS),
    ("/api/video-file", _VIEWS),
    ("/api/clip", _VIEWS),
    ("/api/name-faces", "Naming faces works on the whole library and answers this PC only"),
    ("/activity", _ACTIVITY),
    ("/api/activity", _ACTIVITY),
    ("/api/roots", "Roots are shown and changed from this PC only"),
    ("/api/server/drain", "Only a launch or an install on this machine may drain this server"),
)


def from_this_pc(remote):
    """Is `remote`, a request's address, this PC?"""
    return remote in LOOPBACK


def this_pc_only(path):
    """The sentence a request for `path` from another address is refused with, or None when `path` is not this PC's
    alone. `path` is a request's path or a route's rule: every entry of THIS_PC_ONLY is spelled out, so a rule's
    variable parts come after it."""
    for prefix, sentence in THIS_PC_ONLY:
        if path == prefix or path.startswith(prefix + "/"):
            return sentence
    return None


def hostname_of(authority):
    """The host out of a `host[:port]` authority, brackets and all handled.

    `[::1]:8000` -> `::1`, `localhost:8000` -> `localhost`. Returns it lowercased,
    since hostnames are not case-sensitive and the allow-list is written in lower case.
    """
    authority = (authority or "").strip()
    if authority.startswith("["):
        end = authority.find("]")
        if end != -1:
            return authority[1:end].lower()
    return authority.split(":")[0].lower()


def allowed_hosts():
    """The hosts a request may be for: this PC's, and the names in TAGPUP_ALLOWED_HOSTS (comma-separated, exact): the
    one name the owner's phone reaches this PC by through `tailscale serve` (DECISIONS 2026-11-14). It is the server's
    environment, so a changed name counts from the server's next start."""
    named = (name.strip().lower() for name in os.environ.get("TAGPUP_ALLOWED_HOSTS", "").split(","))
    return LOCAL_HOSTS + tuple(name for name in named if name)


def refusal(host, origin=None):
    """Why a request with these Host and Origin headers is refused, or None."""
    allowed = allowed_hosts()
    if hostname_of(host) not in allowed:
        return "Forbidden: Invalid Host Header"
    if origin and hostname_of(urllib.parse.urlparse(origin).netloc) not in allowed:
        return "Forbidden: Cross-Origin Requests Denied"
    return None


def guard():
    """Refuse the request, 403, unless it is local, and one for what is this PC's alone (this_pc_only) from any other
    address, whatever its method (OPTIONS and HEAD too). Registered as the first before_request hook on every app
    (tagpup.web.app), so a refused request reaches no other hook and no route."""
    problem = refusal(request.headers.get("Host", ""), request.headers.get("Origin"))
    if problem:
        abort(403, description=problem)
    sentence = this_pc_only(request.path)
    if sentence is not None and not from_this_pc(request.remote_addr):
        return responses.error(403, sentence)
    return None
