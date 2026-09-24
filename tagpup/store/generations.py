"""Counters that move when a table changes, whoever changes it, and the cache kept by them.

Triggers made by tagpup.store.schema bump `photos`, `faces` or `taxonomy` in the
`generations` table on every change to those tables, from any process: TagPup,
TagTuner, the CLI or a script. A cache that stores the generations it was built at
is current exactly while they have not moved, which one small query can tell.
"""
import sqlite3
import threading

NAMES = ("photos", "faces", "taxonomy")


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
    return tuple(found.get(name, 0) for name in names)


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

    def forget(self, key=None):
        """Drop one library's value, or every value."""
        with self._lock:
            if key is None:
                self._entries.clear()
            else:
                self._entries.pop(key, None)
