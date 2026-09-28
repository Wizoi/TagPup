"""The library's folders: the one answer to "is this folder the library's".

A folder is the library's when it holds one of its photos directly, or when it was added
(tagpup.store.added_folders) -- it, or a folder above it added with its subfolders --
unless it is one of the library's ignored folders (`library.ignored`) or under one: an
ignored folder is never the library's, even under a folder added with its subfolders.

There were two answers: the rows (store.photos, sync's walk and its sorting of new files,
the watcher, Remove Folder's list) and the rows or an add (Suggest, the writes). A folder
added whose index never ran was the library's to Suggest in and nobody's to sync, watch or
remove. Every consumer asks here: holds() for one folder (a point query, for ensure_row and
the services), of() for all of them at once (sync, the watcher, the lists).
"""
import os

from tagpup.core import paths, validation
from tagpup.store import added_folders

#: The setting naming the library's ignored folders (tagpup.core.validation.SETTINGS),
#: read here as the store reads any setting: changed only through the journal.
IGNORED = "library.ignored"


def ignored(conn):
    """The library's ignored folders, as its settings hold them; none for a library that
    holds no settings (yet)."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'settings'").fetchone() is None:
        return []
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (IGNORED,)).fetchone()
    return validation.folder_list(row[0]) if row and row[0] else []


def is_ignored(folder, ignored_folders):
    """Is `folder` one of `ignored_folders`, or under one?"""
    return any(paths.same(folder, other) or paths.is_under(folder, other) for other in ignored_folders)


def holds(conn, folder, ignored_folders=None):
    """Is `folder` the library's? `ignored_folders` are the library's ignored folders,
    read from its settings unless given. Two point queries and the added folders, a
    handful: what ensure_row asks for every row it would make."""
    if is_ignored(folder, ignored(conn) if ignored_folders is None else ignored_folders):
        return False
    where, params = paths.sql_in("path", folder)
    if conn.execute("SELECT 1 FROM photos WHERE " + where + " LIMIT 1", params).fetchone() is not None:
        return True
    return added_folders.covers(conn, folder)


def with_rows(conn):
    """[(folder, photos the library holds directly in it)], the folder spelled as the
    library stores it, so it can be sent back to name the folder. Spellings differing only
    in case are one folder, under the first seen. Every row's path is read: for a
    whole-library answer (of), not a question about one folder."""
    held, spelling = {}, {}
    for (photo_path,) in conn.execute("SELECT path FROM photos"):
        folder = os.path.dirname(photo_path)
        key = paths.key(folder)
        spelling.setdefault(key, folder)
        held[key] = held.get(key, 0) + 1
    return [(spelling[key], count) for key, count in held.items()]


class Folders:
    """Every folder of a library at once (of): `rows` [(folder, photos directly in it)],
    `added` [(folder, with its subfolders)], `ignored` [folder]."""

    def __init__(self, rows, added, ignored_folders):
        self.rows = {paths.key(folder): (folder, count) for folder, count in rows}
        self.added = [(paths.stored(folder), bool(subfolders)) for folder, subfolders in added]
        self.ignored = [paths.stored(folder) for folder in ignored_folders]

    def holds(self, folder):
        """Is `folder` the library's (the module's holds, from what was read)?"""
        if is_ignored(folder, self.ignored):
            return False
        if paths.key(folder) in self.rows:
            return True
        return any(paths.same(folder, path) or (subfolders and paths.is_under(folder, path))
                   for path, subfolders in self.added)

    def _added_kept(self):
        return [path for path, _subfolders in self.added if not is_ignored(path, self.ignored)]

    def walked(self):
        """The folders sync walks and the watcher watches, besides the roots: each holding
        rows -- an ignored one too, so its rows are kept in step and a missing file told --
        and each added, but an ignored one. Not reduced to the topmost: the caller does."""
        return [folder for folder, _count in self.rows.values()] + self._added_kept()

    def listed(self):
        """[(folder, photos directly in it)] of the folders Remove Folder offers and the
        picker counts: those holding rows and those added (0 when nothing is read yet),
        but an ignored one added."""
        found = dict(self.rows)
        for path in self._added_kept():
            found.setdefault(paths.key(path), (path, 0))
        return list(found.values())


def of(conn, ignored_folders=None, rows=None):
    """Every folder of the library (Folders). `ignored_folders` are read from its settings
    unless given; `rows`, with_rows(conn), unless given (the watcher keeps them between
    changes of the photos)."""
    return Folders(with_rows(conn) if rows is None else rows, added_folders.every(conn),
                   ignored(conn) if ignored_folders is None else ignored_folders)
