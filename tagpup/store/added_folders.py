"""The folders a library was asked to add: `added_folders` (migration 15).

A folder is a library's when it holds one of its photos (tagpup.store.folders, which says
so for every consumer) -- or when the owner asked to add it, before the index has read a photo of it: "Add to
kr-track" in TagPup, TagTuner's Add Folder, the CLI's `index`. Adding made a row for every
photo under the folder at once, the path and nothing read, so Suggest could start before
the index reached it; a large tree was tens of thousands of unstamped rows in one request,
which sync then read again with ExifTool while the indexer read them too. Now the add is
recorded here, and a row is made as a photo is used -- by Suggest, as the indexer reads it.

A record of what was asked, not a change of the library's photos: not journaled, as
indexing is not. Removing a folder from the library forgets what was added at or under it.
"""
import os
import time

from tagpup.core import paths
from tagpup.store import roots as store_roots

TABLE = "added_folders"

#: How a record spells a time (the journal's format).
TIME = "%Y-%m-%d %H:%M:%S"


def _there(conn):
    """Has the library the table? One behind this version's schema, opened read-only for a
    look, has none, and has added nothing."""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE,)).fetchone() is not None


def record(conn, folder, subfolders=True):
    """Record `folder` as added, with the folders under it unless not `subfolders`. Returns
    1 when that added something -- a folder not added before, or its subfolders now -- else
    0. The caller commits."""
    stored = store_roots.to_row(conn, folder)
    where, params = store_roots.sql_equals(conn, "path", folder)
    row = conn.execute("SELECT subfolders FROM added_folders WHERE " + where, params).fetchone()
    if row is not None:
        if subfolders and not row[0]:
            conn.execute("UPDATE added_folders SET subfolders = 1 WHERE " + where, params)
            return 1
        return 0
    conn.execute("INSERT INTO added_folders (path, subfolders, added) VALUES (?, ?, ?)",
                 (stored, 1 if subfolders else 0, time.strftime(TIME)))
    return 1


def every(conn):
    """[(folder, with its subfolders)] of every folder added."""
    if not _there(conn):
        return []
    return [(path, bool(subfolders)) for path, subfolders in store_roots.natives(
        conn, conn.execute("SELECT path, subfolders FROM added_folders").fetchall(), 0)]


def covers(conn, folder):
    """Was `folder` added: it, or a folder above it added with its subfolders? The table
    holds the folders asked for, a handful, so it is read whole."""
    if not _there(conn):
        return False
    for path, subfolders in store_roots.natives(
            conn, conn.execute("SELECT path, subfolders FROM added_folders").fetchall(), 0):
        if paths.same(folder, path) or (subfolders and paths.is_under(folder, path)):
            return True
    return False


def follow(conn, old, new):
    """Point what was added at `old`, or under it, at `new` instead: the folder was renamed
    (tagpup.services.folder_follow). An entry whose new place was added already is left as it
    is; nothing is merged. Returns the records changed. The caller commits."""
    if not _there(conn):
        return 0
    changed = 0
    for path, _subfolders in every(conn):
        if paths.same(path, old):
            target = paths.stored(new)
        elif paths.is_under(path, old):
            target = paths.stored(os.path.join(new, os.path.relpath(paths.stored(path), paths.stored(old))))
        else:
            continue
        where, params = store_roots.sql_equals(conn, "path", path)
        taken, taken_params = store_roots.sql_equals(conn, "path", target)
        if conn.execute("SELECT 1 FROM added_folders WHERE " + taken, taken_params).fetchone() is None:
            changed += conn.execute("UPDATE added_folders SET path = ? WHERE " + where,
                                    (store_roots.to_row(conn, target),) + params).rowcount
    return changed


def forget_under(conn, folder):
    """Forget what was added at `folder` or under it: it is taken out of the library.
    Returns the records removed. The caller commits."""
    if not _there(conn):
        return 0
    at, at_params = store_roots.sql_equals(conn, "path", folder)
    under, under_params = store_roots.sql_under(conn, "path", folder)
    return conn.execute("DELETE FROM added_folders WHERE (" + at + ") OR (" + under + ")",
                        at_params + under_params).rowcount
