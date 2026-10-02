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

Roots (docs/ARCHITECTURE.md, "Roots and machines"): the library may hold a path as a
root's name and the path under it, so that the library does not say where a machine keeps
its photos. Everything above the store keeps handling native paths; only the boundary
with the database converts, with to_row() and from_row(), and sql_equals / sql_under /
sql_in take a Roots to convert their argument. Given none, or a Roots holding nothing,
every function here behaves as it always did.

The row form, a string that sorts the way the path index needs:

* "@name"            the root itself
* "@name/a/b.jpg"    a path under it; "/" always, on every machine
* anything else      a path under no root: this machine's native absolute path as
                     stored() spells it. A native absolute path starts with a drive
                     letter, a separator or "/", never "@", so the two cannot be mixed up.

The name ends where the first "/" does, so "@photos/" is a prefix of nothing in
"@photos2/" (the share "photos" against its sibling "photos2", which _as_folder fixed). The name is
[a-z0-9_-], 1 to 32, folded to lower case; the part after it keeps the case the file has.

Nothing outside this module converts separators or case on a path. The test
tests/test_paths_single_owner.py fails the build on anything that does.
"""
import os
import re

__all__ = ["stored", "key", "same", "is_under", "sql_equals", "sql_under", "sql_in", "COLLATE",
           "Roots", "to_row", "from_row", "root_name", "check_locations", "is_native_absolute",
           "RootsError", "UnmappedRoot", "UnknownRoot"]

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


def sql_equals(column, path, roots=None):
    """(clause, params) matching rows whose `column` holds this file.

        clause, params = paths.sql_equals("path", photo)
        cursor.execute("SELECT id FROM photos WHERE " + clause, params)

    Not LIKE, which the tuner used for case-insensitivity: LIKE reads "_" -- in
    most camera filenames -- as any character, and cannot use the index. Given `roots`, the
    argument is converted to the row form (to_row).
    """
    return "%s = ? COLLATE %s" % (column, COLLATE), (to_row(path, roots),)


def sql_under(column, folder, roots=None):
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
    if roots is not None and not roots.identity:
        return _sql_under_rooted(column, folder, roots)
    prefix = _as_folder(stored(folder))
    upper = prefix[:-1] + chr(ord(prefix[-1]) + 1)
    return ("%s >= ? COLLATE %s AND %s < ? COLLATE %s" % (column, COLLATE, column, COLLATE),
            (prefix, upper))


def _sql_under_rooted(column, folder, roots):
    """sql_under with roots: the range of the folder's rows in row form, and for each root
    wholly inside the folder (a folder above a root's location) the root itself and
    the range of its "@name/" rows -- written as two tests, since a range from "@name" to
    "@name0" would take in the root "@name-2"."""
    prefix, _sep = _row_folder(folder, roots)
    clause, params = _range(column, prefix)
    extra = _under_roots(folder, roots)
    if not extra:
        return clause, params
    parts, values = ["(" + clause + ")"], list(params)
    for name in extra:
        parts.append("%s = ? COLLATE %s" % (column, COLLATE))
        values.append(ROOT_MARK + name)
        sub, sub_params = _range(column, ROOT_MARK + name + ROW_SEP)
        parts.append("(" + sub + ")")
        values.extend(sub_params)
    return "(" + " OR ".join(parts) + ")", tuple(values)


def sql_in(column, folder, roots=None):
    """(clause, params) matching rows whose `column` is directly in `folder`, not in a
    folder under it: sql_under's range, which the path index seeks, and of what it finds
    only the paths holding no separator past the folder's. A folder holds a photo when a
    row is directly in it (tagpup.store.photos.holds_folder)."""
    if roots is not None and not roots.identity:
        prefix, sep = _row_folder(folder, roots)
        clause, params = _range(column, prefix)
        return (clause + " AND instr(substr(%s, ?), ?) = 0" % column, params + (len(prefix) + 1, sep))
    clause, params = sql_under(column, folder)
    prefix = _as_folder(stored(folder))
    return (clause + " AND instr(substr(%s, ?), ?) = 0" % column, params + (len(prefix) + 1, os.sep))


# --- Roots: native in memory, root-relative in the database -------------------------

#: What starts a row that is under a root, and what separates the parts of one.
ROOT_MARK = "@"
ROW_SEP = "/"

_NAME = re.compile(r"[a-z0-9_-]{1,32}\Z")


class RootsError(ValueError):
    """A root, a map or a row that cannot be used. Never answered with a guess."""


class UnmappedRoot(RootsError):
    """A root this machine does not say where it keeps, asked to give a path or take one."""


class UnknownRoot(RootsError):
    """A row names a root the library does not have."""


def root_name(name):
    """A root's name, folded to lower case: [a-z0-9_-], 1 to 32 characters, or RootsError."""
    folded = name.lower() if isinstance(name, str) else name
    if not isinstance(folded, str) or not _NAME.match(folded):
        raise RootsError("a root's name is 1 to 32 characters from a-z, 0-9, _ and -: %r" % (name,))
    return folded


def is_native_absolute(path):
    """Does this name a place by itself on this machine: a drive (and a separator or
    nothing), a UNC share, or a leading "/" elsewhere? Never touches the disk."""
    if not isinstance(path, str) or not path:
        return False
    if not CASE_INSENSITIVE:
        return path.startswith("/")
    drive, rest = os.path.splitdrive(path)
    return bool(drive) and rest[:1] in ("", "\\", "/")


def _folded(path):
    """(stored, key) of an absolute path."""
    spelled = stored(path)
    return spelled, os.path.normcase(spelled)


def check_locations(locations):
    """Validate {name: [native, ...]} and return it as {folded name: (stored, ...)}.

    Refused, each with a message naming it: a bad name, two names that fold to one, a
    root with no location, a location that is not absolute, and the same location twice
    -- under two roots, or twice under one -- since a path under it could then belong to
    either. Nested locations are fine: the deeper one wins.
    """
    checked, seen = {}, {}
    for name, listed in locations.items():
        folded = root_name(name)
        if folded in checked:
            raise RootsError("two roots are named %r once case is folded" % folded)
        if isinstance(listed, str):
            listed = [listed]
        if not listed:
            raise RootsError("root %r lists no location" % folded)
        spelled_all = []
        for native in listed:
            if not is_native_absolute(native):
                raise RootsError("root %r: %r is not an absolute location" % (folded, native))
            spelled, folded_key = _folded(native)
            if folded_key in seen:
                raise RootsError("%r is listed under %r and under %r" % (spelled, seen[folded_key], folded))
            seen[folded_key] = folded
            spelled_all.append(spelled)
        checked[folded] = tuple(spelled_all)
    return checked


class Roots:
    """The library's roots and where this machine keeps each: pure, prepared once.

    `logical` is {name: the share's own address} -- informational, but also a spelling
    that names the root (a path typed as the share's UNC address is under it). `locations`
    is {name: [native location, ...]}; the first is where from_row puts a path, and every
    one is a place to_row recognises. Build with Roots.of(), which keeps the prepared map
    for as long as the same content is asked for.

    The rules, each a refusal and not a guess:
    * a path under a location (or a logical address) of a root is that root's; of several,
      the longest match, so a nested location resolves to the deeper root;
    * a path under none is "unrooted" and keeps its native spelling -- unless some root of
      the library has no location on this machine, when it might belong to that root, and
      to_row raises UnmappedRoot. A machine whose drive is not mounted still maps the
      string (nothing here asks the disk), so an unmounted drive is not this case;
    * from_row of a row under a root that has no location here, or whose name the library
      does not have, raises.
    """

    def __init__(self, logical=None, locations=None):
        logical = {root_name(n): (a or "") for n, a in (logical or {}).items()}
        checked = check_locations(locations or {})
        self.logical = logical
        # Roots the machine maps that this library does not have are another library's.
        self.locations = {n: v for n, v in checked.items() if n in logical}
        self.unmapped = tuple(sorted(n for n in logical if n not in self.locations))
        self.identity = not logical
        entries = {}
        for name, address in logical.items():
            if address and is_native_absolute(address):
                spelled, folded_key = _folded(address)
                if folded_key in entries and entries[folded_key][1] != name:
                    raise RootsError("%r is the address of both %r and %r" % (address, entries[folded_key][1], name))
                entries[folded_key] = (spelled, name)
        for name, listed in self.locations.items():
            for spelled in listed:
                entries[os.path.normcase(spelled)] = (spelled, name)
        # Longest first, so the first match is the deepest. (key, folder form, its length, name)
        self._matchers = tuple(sorted(
            ((k, _as_folder(k), len(_as_folder(k)), name) for k, (_s, name) in entries.items()),
            key=lambda m: -len(m[0])))
        self._first = {n: v[0] for n, v in self.locations.items()}

    _prepared = {}

    @classmethod
    def of(cls, logical=None, locations=None):
        """The Roots for this content, built once however often it is asked for."""
        key = (tuple(sorted((logical or {}).items())),
               tuple(sorted((n, tuple([v] if isinstance(v, str) else v)) for n, v in (locations or {}).items())))
        found = cls._prepared.get(key)
        if found is None:
            found = cls._prepared[key] = cls(logical, locations)
            if len(cls._prepared) > 64:
                cls._prepared.pop(next(iter(cls._prepared)))
        return found

    def locate(self, native):
        """(name, relative part with "/" separators) of a native path, or None when it is
        under no root. Never raises for an unmapped root; to_row does."""
        spelled = stored(native)
        folded_key = os.path.normcase(spelled)
        for exact, folder, size, name in self._matchers:
            if folded_key == exact:
                return name, ""
            if folded_key.startswith(folder):
                rel = spelled[size:]
                return name, (rel if os.sep == ROW_SEP else rel.replace(os.sep, ROW_SEP))
        return None

    def describe(self):
        """One dict per root, for a page to show: name, logical address, this machine's
        locations (empty when it has none) and whether it has any."""
        return [{"name": n, "logical": self.logical[n], "locations": list(self.locations.get(n, ())),
                 "mapped": n in self.locations} for n in sorted(self.logical)]

    def propose_row(self, native):
        """What the row for a native path would be, or None when it is under no root, so a
        page can say "outside the roots" and where to move it. Same answer as to_row, for
        a path to_row can answer."""
        found = self.locate(native)
        return None if found is None else _row(*found)

    def propose_locations(self, native_folders):
        """{root name: [native folders it might live at]} for each root this machine has no
        location for: the folders whose last name is the logical address's last name (a
        share ending in Pictures and a folder ending in Pictures). Only a proposal."""
        proposals = {}
        for name in self.unmapped:
            address = self.logical[name]
            tail = os.path.normcase(os.path.basename(address.rstrip("\\/"))) if address else ""
            if not tail:
                continue
            found = [f for f in native_folders if is_native_absolute(f)
                     and os.path.normcase(os.path.basename(stored(f))) == tail]
            if found:
                proposals[name] = [stored(f) for f in found]
        return proposals


def _row(name, rel):
    return ROOT_MARK + name + (ROW_SEP + rel if rel else "")


def to_row(path, roots=None):
    """A native path as the database holds it: "@name/rel" under a root, else stored().
    With no roots, or none configured, stored() -- as it always was. "" for nothing."""
    if not path:
        return ""
    if roots is None or roots.identity:
        return stored(path)
    found = roots.locate(path)
    if found is not None:
        return _row(*found)
    if roots.unmapped:
        raise UnmappedRoot("%r is under no root this machine knows, and %s %s no location here, "
                           "so it may belong to one" % (stored(path), ", ".join(roots.unmapped),
                                                        "has" if len(roots.unmapped) == 1 else "have"))
    return stored(path)


def from_row(value, roots=None):
    """The native path of what the database holds. A row under no root is already native."""
    if not value:
        return ""
    if not value.startswith(ROOT_MARK):
        return value
    if roots is None or roots.identity:
        raise UnknownRoot("%r is a root-relative row and no roots were given" % value)
    name, _, rel = value[1:].partition(ROW_SEP)
    if name not in roots.logical:
        raise UnknownRoot("%r names root %r, which this library does not have" % (value, name))
    if name not in roots._first:
        raise UnmappedRoot("root %r has no location on this machine" % name)
    location = roots._first[name]
    if not rel:
        return location
    parts = rel.split(ROW_SEP)
    if any(part in ("", ".", "..") for part in parts):
        raise RootsError("%r is not a path under a root" % value)
    return _as_folder(location) + (rel if os.sep == ROW_SEP else rel.replace(ROW_SEP, os.sep))


def _row_folder(folder, roots):
    """A folder in row form, ending in the separator its rows use."""
    row = to_row(folder, roots)
    if row.startswith(ROOT_MARK):
        return row if row.endswith(ROW_SEP) else row + ROW_SEP, ROW_SEP
    return _as_folder(row), os.sep


def _range(column, prefix):
    upper = prefix[:-1] + chr(ord(prefix[-1]) + 1)
    return "%s >= ? COLLATE %s AND %s < ? COLLATE %s" % (column, COLLATE, column, COLLATE), (prefix, upper)


def _under_roots(folder, roots):
    """The roots whose whole extent is inside `folder` though no row says so: a root
    located at a folder inside this one has rows "@name/...", which no range of this folder's
    spelling reaches."""
    inside = _as_folder(os.path.normcase(stored(folder)))
    names = []
    for exact, _folder, _size, name in roots._matchers:
        if exact.startswith(inside) and name not in names:
            names.append(name)
    return names
