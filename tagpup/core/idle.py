"""What a process keeps in memory only while it is used, let go after an idle period.

The always-on process runs all day, and what it builds to answer quickly -- the models,
each library's vectors, New Person's pool of faces, the Identify Faces grids, the folder
scans -- was held until it ended: on photo_index some 700 MB for a library opened once
(owner, 2026-09-26: "can the in-memory cache be minimized when not in use?"). Each such
cache registers here how to let it go, and says when it is used; one timer (the web
server's background task, tagpup.runtime) lets go of each unused for `idle_after`
seconds and not in use now. The next use builds it again.

    idle = IdleCaches(30 * 60)
    idle.register("New Person pool", release=drop_the_pools, in_use=lambda: False)
    idle.used("New Person pool")          # at every use
    idle.release_idle()                   # on the timer: the names let go

In core: the runtime and the web both register theirs, and neither may import the other.
"""
import threading
import time


class IdleCaches:
    """The registered caches of one process. `idle_after` in seconds; None keeps all."""

    def __init__(self, idle_after=None, clock=None):
        self.idle_after = idle_after
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        #: name -> {"release", "in_use", "used": time of last use, or None when let go}.
        self._caches = {}

    def register(self, name, release, in_use=None):
        """`release()` lets the cache go -- returning 0 when it let go of nothing, being
        held -- and `in_use()`, true while something holds it (a run, a build), keeps it.
        Registering a name again replaces it, and keeps when it was last used."""
        with self._lock:
            used = self._caches.get(name, {}).get("used")
            self._caches[name] = {"release": release, "in_use": in_use or (lambda: False), "used": used}

    def names(self):
        with self._lock:
            return list(self._caches)

    def used(self, name):
        """`name` was used now: it is kept for another idle period."""
        now = self._clock()
        with self._lock:
            cache = self._caches.get(name)
            if cache is not None:
                cache["used"] = now
            else:
                self._caches[name] = {"release": lambda: None, "in_use": lambda: False, "used": now}

    def idle_for(self, name):
        """Seconds since `name` was last used, or None when it was let go (or never used)."""
        with self._lock:
            cache = self._caches.get(name)
            used = cache["used"] if cache is not None else None
        return None if used is None else max(0.0, self._clock() - used)

    def release_now(self, name):
        """Let `name` go now, whatever its idle time -- the owner's button. False, and nothing
        done, while it is in use; True once its release has run."""
        with self._lock:
            cache = self._caches.get(name)
        if cache is None or cache["in_use"]():
            return False
        cache["release"]()
        with self._lock:
            cache["used"] = None
        return True

    def release_idle(self):
        """Let go of each cache unused for `idle_after` and not in use; the names let go.
        One let go is not let go again until it is used again."""
        if not self.idle_after:
            return []
        now = self._clock()
        with self._lock:
            due = [(name, cache) for name, cache in self._caches.items()
                   if cache["used"] is not None and now - cache["used"] >= self.idle_after]
        released = []
        for name, cache in due:
            if cache["in_use"]():
                continue
            if cache["release"]() == 0:
                continue   # held: asked again at the next look
            with self._lock:
                # Used again while it was let go: kept as used.
                if cache["used"] is not None and cache["used"] <= now:
                    cache["used"] = None
            released.append(name)
        return released
