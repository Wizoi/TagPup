"""The check of a library's roots: `roots check` and the doctor's account of them.

What is wrong with the library's rooted rows, [] when nothing: every rooted row converts back
(its root is the library's, and this machine places it), and none is held under one root though
the file it names lies under another's place (a lookup under that root would miss it). Reads
only; counts, never paths.

The conversion that put a library's paths under a root (`roots adopt`) is gone: all three live
libraries had it done (DECISIONS, 2026-10-11). What it left is read by tagpup.store.roots and
tagpup.core.paths, and this module says whether the rows still agree with the roots.
"""
from tagpup.core import paths

#: The tables with a column of paths a root holds as `@name/rel`, and the columns.
TABLES = (
    ("photos", ("path",)),
    ("change_files", ("path", "new_path")),
    ("added_folders", ("path",)),
)


def _has(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def verify(conn, roots):
    """The problems of the library on `conn` under `roots`, as sentences; [] when none."""
    problems = []
    unknown = unplaced = malformed = shadowed = native = 0
    why_unplaced = ""
    for table, columns in TABLES:
        if not _has(conn, table):
            continue
        for column in columns:
            for (value,) in conn.execute("SELECT %s FROM %s WHERE %s IS NOT NULL" % (column, table, column)):
                if not value.startswith(paths.ROOT_MARK):
                    # A row that kept its native path where Roots.locate says it is a root's -- by one of
                    # its places or by its share's address: every lookup of that path is by the row form,
                    # and misses it.
                    if paths.is_native_absolute(value) and roots.locate(value) is not None:
                        native += 1
                    continue
                try:
                    native_path = paths.from_row(value, roots)
                except paths.UnknownRoot:
                    unknown += 1
                    continue
                except paths.UnmappedRoot as problem:
                    unplaced += 1
                    why_unplaced = why_unplaced or str(problem)
                    continue
                except paths.RootsError:
                    malformed += 1
                    continue
                found = roots.locate(native_path)
                if found is not None and found[0] != value[1:].partition(paths.ROW_SEP)[0].lower():
                    shadowed += 1
    if unknown:
        problems.append("%d row(s) name a root the library does not have" % unknown)
    if unplaced:
        problems.append("%d row(s) name a root this machine does not place: %s" % (unplaced, why_unplaced))
    if malformed:
        problems.append("%d row(s) are not a path under a root" % malformed)
    if native:
        problems.append("%d row(s) kept their native path where this library's roots resolve it to a root (its place "
                        "or its share's address): a lookup by the row form misses them" % native)
    if shadowed:
        problems.append("%d row(s) are held under one root but lie under another root's place, so a lookup under "
                        "that root misses them" % shadowed)
    return problems
