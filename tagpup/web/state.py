"""What a server keeps in memory for each library it serves: the folder cache, the
Identify grids, the CLIP model warmed for it.

Each old server kept these in dicts keyed by a thread-local "active library", set at
the top of every request and again by hand on every background thread; a thread that
forgot was served another library's cache (docs/findings.md, #44). Here a value is
looked up by the library it belongs to, which a route has from the request (`current`)
and a background thread is handed when it starts.
"""
import threading

from flask import g


class PerLibrary:
    """One value per library, made by `make(library)` the first time that library asks.
    Thread-safe: the servers answer requests on a pool of threads."""

    def __init__(self, make):
        self._make = make
        self._held = {}
        self._lock = threading.Lock()

    def of(self, library):
        with self._lock:
            value = self._held.get(library.key)
            if value is None:
                value = self._held[library.key] = self._make(library)
            return value

    def forget(self, library):
        """Drop a library's value: a library removed, or a cache to be built again."""
        with self._lock:
            self._held.pop(library.key, None)

    def libraries(self):
        with self._lock:
            return list(self._held)


def current():
    """The Library the request names (tagpup.web.libraries), or None when the URL names
    none -- the picker, the page before a library is chosen."""
    return getattr(g, "library", None)


def require():
    """The request's Library, or a 404 for a request that names none but needs one."""
    from flask import abort
    library = current()
    if library is None:
        abort(404, description="No library: choose one first")
    return library
