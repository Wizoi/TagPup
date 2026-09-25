"""The one place a photo path's spelling is decided.

A path reaches this app from os.walk, from a folder someone typed, from a URL, from
a row in the index and from the browser, and each of those spelled it differently:
forward slashes, backslashes, both at once, any case. Every component then converted
it by hand, each its own way. The index stores native absolute paths; one helper
looked them up with forward slashes and matched nothing, so tag writes, renames and
deletes all reported success while leaving the index as it was, and the indexer,
handed a folder typed with forward slashes, stored rows with both separators in one
path.

So there are exactly two spellings, and both come from here:

* stored(path) -- what is written to the database and looked up in it. Absolute,
  normalised, native separators: what os.path.abspath gives, which is what the
  indexer has always written.
* key(path) -- what two paths are compared by, in memory. Same file, same key, on
  whichever operating system: case-insensitive where the filesystem is.

And the database compares paths the way the filesystem does, through sql_equals()
and sql_under(): case-insensitively on Windows, exactly elsewhere, and in a form the
path indexes can answer. A folder typed in lower case walks to lower-case paths, and
those name the same files as the rows the indexer wrote.

Nothing outside this module converts separators or case on a path. The test
tests/test_paths_single_owner.py fails the build on anything that does.
"""
import os

__all__ = ["stored", "key", "same", "is_under", "sql_equals", "sql_under", "COLLATE"]

#: Does this filesystem ignore case? normcase says so on Windows and not elsewhere.
CASE_INSENSITIVE = os.path.normcase("A") == "a"

#: How path columns compare in SQL. The path indexes are declared with the same
#: collation (index.py), or an equality on them could not use the index.
COLLATE = "NOCASE" if CASE_INSENSITIVE else "BINARY"


def stored(path):
    """A path the way the database holds it. "" for nothing.

    os.path.abspath normalises too: on Windows it turns every "/" into "\\" and
    collapses "a\\.\\b" and "a\\x\\..\\b", so a folder typed as D:/Pictures/x and one
    picked in a dialog end up the same string.
    """
    if not path:
        return ""
    path = os.fspath(path)
    # A bare drive ("C:") is that drive's current directory to abspath, which in a
    # server is wherever it was started from. Nobody typing "C:" as a folder means
    # that; they mean the drive.
    if CASE_INSENSITIVE and len(path) == 2 and path[1] == ":" and path[0].isalpha():
        path += os.sep
    return os.path.abspath(path)


def _as_folder(spelling):
    """A folder spelling that ends in exactly one separator.

    os.path.join(p, "") does not add one after a UNC share root ("\\\\nas\\photos"),
    so "under \\\\nas\\photos" also matched "\\\\nas\\photos2\\...".
    """
    return spelling if spelling.endswith(os.sep) else spelling + os.sep


def key(path):
    """A path for comparing, never for storing or opening. "" for nothing."""
    if not path:
        return ""
    return os.path.normcase(stored(path))


def same(a, b):
    """Do these two spellings name the same file?"""
    return bool(a) and bool(b) and key(a) == key(b)


def spelled_as(path):
    """Is there a file named exactly `path` -- its name in this case -- in its folder? Two
    spellings that differ only in case name one file on Windows (same), so only the
    folder's listing says which one a rename left (docs/findings.md, #283)."""
    folder, name = os.path.split(stored(path))
    try:
        return name in os.listdir(folder)
    except OSError:
        return False


def is_under(path, folder):
    """Is `path` inside `folder` (at any depth)? A folder is not under itself."""
    if not path or not folder:
        return False
    return key(path).startswith(_as_folder(key(folder)))


def sql_equals(column, path):
    """(clause, params) matching rows whose `column` holds this file.

        clause, params = paths.sql_equals("path", photo)
        cursor.execute("SELECT id FROM photos WHERE " + clause, params)

    Not LIKE, which the tuner used for case-insensitivity: LIKE reads "_" -- in
    most camera filenames -- as any character, and cannot use the index.
    """
    return "%s = ? COLLATE %s" % (column, COLLATE), (stored(path),)


def sql_under(column, folder):
    """(clause, params) matching rows whose `column` is inside `folder`, any depth.

    A range rather than LIKE, so "%" and "_" in folder names are plain characters,
    and a range the path index can seek: every path that starts with "D:\\Run\\"
    sorts at or after it and before "D:\\Run]", the same prefix with its separator
    raised by one. It was `substr(path, 1, n) = ?`, a function on the column, and
    every read of a folder scanned the whole index (docs/findings.md, #168).

    The bound is right case-insensitively too: NOCASE folds only A-Z, and neither
    separator nor the character after it is a letter, so no folded spelling of a path
    in the folder sorts past it.
    """
    prefix = _as_folder(stored(folder))
    upper = prefix[:-1] + chr(ord(prefix[-1]) + 1)
    return ("%s >= ? COLLATE %s AND %s < ? COLLATE %s" % (column, COLLATE, column, COLLATE),
            (prefix, upper))
