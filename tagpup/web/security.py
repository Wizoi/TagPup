"""Who a request may come from: a page on this machine, served by this server.

The apps hold a private photo library and bind a port on the machine, so the Host
check is what keeps a page on the internet from talking to them through the browser,
and the Origin check what keeps another local page from doing the same. Both were
scripts/localserver.py's, run at the top of every handler; here they are one
`before_request` hook, so no route can forget.
"""
import urllib.parse

from flask import abort, request

#: The only hosts the apps answer to.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


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


def refusal(host, origin=None):
    """Why a request with these Host and Origin headers is refused, or None."""
    if hostname_of(host) not in LOCAL_HOSTS:
        return "Forbidden: Invalid Host Header"
    if origin and hostname_of(urllib.parse.urlparse(origin).netloc) not in LOCAL_HOSTS:
        return "Forbidden: Cross-Origin Requests Denied"
    return None


def guard():
    """Refuse the request, 403, unless it is local. Registered as a before_request hook
    on every app (tagpup.web.app)."""
    problem = refusal(request.headers.get("Host", ""), request.headers.get("Origin"))
    if problem:
        abort(403, description=problem)
