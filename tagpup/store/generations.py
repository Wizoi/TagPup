"""Counters that move when a table changes, whoever changes it, and the cache kept by them.

Triggers made by tagpup.store.schema bump `photos`, `faces` or `taxonomy` in the
`generations` table on every change to those tables, from any process: TagPup,
TagTuner, the CLI or a script. A cache that stores the generations it was built at
is current exactly while they have not moved, which one small query can tell.

A cache of photos or faces holds the paths of this machine, native, and where a library holds a
root a path is where the machine's map puts it (tagpup.store.roots): move the root in the map
and the paths a cache holds are the old place's, though no row moved. So the generations of
photos and faces carry a salt made of the library's roots and where this machine keeps them --
nothing, 0, for a library with no roots -- and every cache keyed by them is built again when the
map moves, the Identify Faces grids, the folders the watcher watches and the index's.
"""
import sqlite3
import threading
import zlib

from tagpup.store import roots as store_roots

NAMES = ("photos", "faces", "taxonomy")

#: The generations whose caches hold paths.
WITH_PATHS = ("photos", "faces")

#: One more than any salt, for a library whose roots this machine cannot place.
_UNPLACED = 1 << 40


def _salt(conn):
    """A number that is 0 for a library with no roots and otherwise says where this machine
    keeps them, to be added to a generation. Never raises: a map that cannot be read is a
    salt of its own, and what converts a path says why."""
    try:
        roots = store_roots.roots_for(conn)
    except ValueError:
        return _UNPLACED
    if roots.identity:
        return 0
    text = repr(sorted((name, roots.logical[name], roots.locations.get(name, ())) for name in roots.logical))
    return (zlib.crc32(text.encode("utf-8")) + 1) << 41


def value(conn, name):
    """One generation, or 0 on a library that does not count it yet."""
    return values(conn, (name,))[0]


def values(conn, names=NAMES):
    """The generations `names`, in that order; 0 for any not counted yet."""
    try:
        found = dict(conn.execute(
            "SELECT name, value FROM generations WHERE name IN (%s)" % ",".join("?" * len(names)),
            tuple(names)).fetchall())
    except sqlite3.OperationalError:
        found = {}
    salt = _salt(conn) if any(name in WITH_PATHS for name in names) else 0
    return tuple(found.get(name, 0) + (salt if name in WITH_PATHS else 0) for name in names)


class Cache:
    """A value per library, built again once the generations it depends on move.

    `build(conn)` makes the value from a connection to the library. `get(conn, key)`
    returns it, rebuilding only if one of `names` has moved since it was built; `key`
    says which library (a path, or a paths.key of it). The generations are read before
    the value is built, so a write landing in between costs one more build later,
    never a stale value kept.
    """

    def __init__(self, names, build):
        self.names = tuple(names)
        self.build = build
        self._entries = {}
        self._lock = threading.Lock()

    def get(self, conn, key):
        stamp = values(conn, self.names)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry[0] == stamp:
                return entry[1]
        built = self.build(conn)
        with self._lock:
            self._entries[key] = (stamp, built)
        return built

    def last(self, key, default=None):
        """The value last built for `key`, whatever has moved since: for a library that
        cannot be read just now, where what was read stands."""
        with self._lock:
            entry = self._entries.get(key)
        return entry[1] if entry is not None else default

    def forget(self, key=None):
        """Drop one library's value, or every value."""
        with self._lock:
            if key is None:
                self._entries.clear()
            else:
                self._entries.pop(key, None)
