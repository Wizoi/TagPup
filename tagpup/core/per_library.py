"""One value per library, made the first time that library asks: what a server, a job
runner or the runtime keeps for each library it serves.

Each old server kept these in dicts keyed by a thread-local "active library", set at the
top of every request and again by hand on every background thread; a thread that forgot
was served another library's cache (docs/findings.md, #44). Here a value is looked up by
the Library it belongs to, which a route has from its request and a background thread
is handed when it starts. It lives in core because web, jobs and the runtime all keep
values this way, and none of them may import another's.
"""
import threading


class PerLibrary:
    """One value per library, made by `make(library)` the first time that library asks.
    Thread-safe: the servers answer requests on a pool of threads."""

    def __init__(self, make):
        self._make = make
        self._held = {}
        self._lock = threading.Lock()
        #: Called at each ask: how a cache in an idle registry says it was used
        #: (tagpup.core.idle.IdleCaches.used), set when it is registered.
        self.on_use = None

    def of(self, library):
        if self.on_use is not None:
            self.on_use()
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

    def held(self, library_key):
        """The value kept for the library with that key, or None; makes nothing."""
        with self._lock:
            return self._held.get(library_key)

    def release(self):
        """Let go of every library's value: each is made again at its next ask. How many."""
        with self._lock:
            count = len(self._held)
            self._held.clear()
            return count
