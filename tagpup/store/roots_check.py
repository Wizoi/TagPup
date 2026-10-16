"""The one check of a library's rooted rows: `roots check` (tagpup.services.roots.check, `verify`) and the doctor's two
rules of them (tagpup.store.checks: rooted_rows_convert, native_rows_under_a_root) both read `broken`.

What is wrong with the rows of store.roots.PATH_COLUMNS: a rooted row that does not convert back (its root is not the
library's, this machine does not place it, or it is not what paths.to_row writes), one held under one root though the file
it names lies under another's place (a lookup under that root would miss it), and a row that kept its native path where a
root of the library is. Reads only; counts and row keys, never paths.

The conversion that put a library's paths under a root (`roots adopt`) is gone: all three live libraries had it done
(DECISIONS, 2026-10-11). What it left is read by tagpup.store.roots and tagpup.core.paths, and this module says whether the
rows still agree with the roots.
"""
import collections

from tagpup.core import paths
from tagpup.store import roots as store_roots

#: What is wrong with a row. The first four are rooted rows that do not convert back; NATIVE is a native row under a root.
UNKNOWN, UNPLACED, MALFORMED, SHADOWED, NATIVE = "unknown", "unplaced", "malformed", "shadowed", "native"
DO_NOT_CONVERT = (UNKNOWN, UNPLACED, MALFORMED, SHADOWED)


def broken(conn, roots):
    """({kind: [the row: a photo by its id, any other row as "table key"]}, why a root is not placed, or "") of the rows of
    PATH_COLUMNS that disagree with `roots`, the library's roots as this machine places them. `roots` is None when this
    machine's map cannot place them (store.roots.roots_for raised): every rooted row is then UNPLACED, and no native row can
    be said to lie under a root."""
    found = collections.defaultdict(list)
    why = ""
    for table, key, column in store_roots.PATH_COLUMNS:
        for row_id, value in conn.execute("SELECT %s, %s FROM %s WHERE %s IS NOT NULL" % (key, column, table, column)):
            row = row_id if table == "photos" else "%s %s" % (table, row_id)
            if not value.startswith(paths.ROOT_MARK):
                # Kept native where Roots.locate says it is a root's -- by one of its places or by its share's address:
                # every lookup of that path is by the row form, and misses it.
                if roots is not None and paths.is_native_absolute(value) and roots.locate(value) is not None:
                    found[NATIVE].append(row)
                continue
            if roots is None:
                found[UNPLACED].append(row)
                continue
            try:
                native_path = paths.from_row(value, roots)
            except paths.UnknownRoot:
                found[UNKNOWN].append(row)
                continue
            except paths.UnmappedRoot as problem:
                found[UNPLACED].append(row)
                why = why or str(problem)
                continue
            except paths.RootsError:
                found[MALFORMED].append(row)
                continue
            located = roots.locate(native_path)
            if located is not None and located[0] != value[1:].partition(paths.ROW_SEP)[0].lower():
                found[SHADOWED].append(row)
    return found, why


def verify(conn, roots):
    """The problems of the library on `conn` under `roots`, as sentences; [] when none."""
    found, why_unplaced = broken(conn, roots)
    counts = {kind: len(found[kind]) for kind in DO_NOT_CONVERT + (NATIVE,)}
    problems = []
    if counts[UNKNOWN]:
        problems.append("%d row(s) name a root the library does not have" % counts[UNKNOWN])
    if counts[UNPLACED]:
        problems.append("%d row(s) name a root this machine does not place: %s" % (counts[UNPLACED], why_unplaced))
    if counts[MALFORMED]:
        problems.append("%d row(s) are not a path under a root" % counts[MALFORMED])
    if counts[NATIVE]:
        problems.append("%d row(s) kept their native path where this library's roots resolve it to a root (its place "
                        "or its share's address): a lookup by the row form misses them" % counts[NATIVE])
    if counts[SHADOWED]:
        problems.append("%d row(s) are held under one root but lie under another root's place, so a lookup under "
                        "that root misses them" % counts[SHADOWED])
    return problems
