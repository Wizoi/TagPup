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
    return os.path.abspath(os.fspath(path))


def key(path):
    """A path for comparing, never for storing or opening. "" for nothing."""
    if not path:
        return ""
    return os.path.normcase(stored(path))


def same(a, b):
    """Do these two spellings name the same file?"""
    return bool(a) and bool(b) and key(a) == key(b)


def is_under(path, folder):
    """Is `path` inside `folder` (at any depth)? A folder is not under itself."""
    if not path or not folder:
        return False
    return key(path).startswith(os.path.join(key(folder), ""))


def sql_equals(column, path):
    """(clause, params) matching rows whose `column` holds this file.

        clause, params = paths.sql_equals("photo_path", photo)
        cursor.execute("SELECT id FROM faces WHERE " + clause, params)

    Not LIKE, which the tuner used for case-insensitivity: LIKE reads "_" -- in
    most camera filenames -- as any character, and cannot use the index.
    """
    return "%s = ? COLLATE %s" % (column, COLLATE), (stored(path),)


def sql_under(column, folder):
    """(clause, params) matching rows whose `column` is inside `folder`, any depth.

    A prefix comparison rather than LIKE, so "%" and "_" in folder names are plain
    characters.
    """
    prefix = os.path.join(stored(folder), "")
    return ("substr(%s, 1, ?) = ? COLLATE %s" % (column, COLLATE),
            (len(prefix), prefix))
