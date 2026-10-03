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
import contextlib
import os
import re
import threading

__all__ = ["stored", "key", "same", "is_under", "sql_equals", "sql_under", "sql_in", "COLLATE",
           "Roots", "to_row", "from_row", "root_name", "check_locations", "is_native_absolute",
           "RootsError", "UnmappedRoot", "UnknownRoot", "outside_roots", "exiftool_spelling", "ROOT_MARK"]

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


def name_key(name):
    """A file's or folder's NAME, without its folder, for comparing within one folder: the case
    the file system ignores folded. key() makes a path absolute, which a bare name must not be."""
    return os.path.normcase(name)


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
    """(stored, folder-form key) of an absolute path. The key ends in a separator whether or
    not the spelling does, so a UNC share root with and without its trailing separator,
    and a drive root, are each one location; two places nest when one key starts with the
    other, which a sibling ("photos2" against "photos") never does."""
    spelled = stored(path)
    drive = os.path.splitdrive(spelled)[0]
    if spelled.endswith(os.sep) and not (spelled == drive + os.sep and (not drive or drive.endswith(":"))):
        spelled = spelled[:-1]     # a UNC share root, with or without its separator, is one place
    return spelled, _as_folder(os.path.normcase(spelled))


#: A colon in the part under a root is refused where it can only be an NTFS stream, in to_row
#: and in from_row alike; elsewhere it is an ordinary character in both.
_COLON_REFUSED = os.name == "nt"


def _plain(address, what):
    if address.startswith(("\\\\?\\", "//?/", "\\\\.\\", "//./")):
        raise RootsError("%s %r uses the long-path or device prefix; write the plain spelling "
                         "(D:\\folder or \\\\server\\share\\folder)" % (what, address))


def _nested(name, spelled_keys):
    """Refuse two places of one root, one inside the other: a path under both would have
    two relative parts. [(spelling, folder-form key)], in the order given."""
    for i, (first, a) in enumerate(spelled_keys):
        for second, b in spelled_keys[i + 1:]:
            if a.startswith(b) or b.startswith(a):
                raise RootsError("root %r: %r and %r are one place inside the other (or the same); "
                                 "a root's locations and share address must not nest" % (name, first, second))


def check_locations(locations):
    """Validate {name: [native, ...]} and return it as {folded name: (stored, ...)}.

    Refused, each with a message naming it: a bad name, two names that fold to one, a
    root with no location, a location that is not absolute or that uses the long-path
    prefix (\\\\?\\), the same location twice (under two roots, or under one), and, within
    one root, one location inside another. Nested locations of different roots are fine:
    the deeper one wins.
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
        spelled_all, keyed = [], []
        for native in listed:
            _plain(native, "root %r: location" % folded)
            if not is_native_absolute(native):
                raise RootsError("root %r: %r is not an absolute location" % (folded, native))
            spelled, folded_key = _folded(native)
            if folded_key in seen:
                raise RootsError("%r is listed under %r and under %r" % (spelled, seen[folded_key], folded))
            seen[folded_key] = folded
            spelled_all.append(spelled)
            keyed.append((spelled, folded_key))
        _nested(folded, keyed)
        checked[folded] = tuple(spelled_all)
    return checked


class Roots:
    """The library's roots and where this machine keeps each: pure, prepared once.

    `logical` is {name: the share's own address} -- informational, but also a spelling
    that names the root (a path typed as the share's UNC address is under it). `locations`
    is {name: [native location, ...]}; the first is where from_row puts a path, and every
    one is a place to_row recognises. `map_file` is where the machine's map is, for the
    message that says what to add. Build with Roots.of(), which keeps the prepared map for
    as long as the same content is asked for; build one per operation and pass it down.

    The rules, each a refusal and not a guess:
    * a path under a location (or a logical address) of a root is that root's; of several,
      the longest match, so a nested location of another root resolves to the deeper root.
      Within one root, locations and address must not nest or repeat (RootsError);
    * a path under none is "unrooted" and keeps its native spelling -- unless some root of
      the library has no location on this machine (whether or not it has an address), when
      to_row raises UnmappedRoot for every path under no mapped root: it might belong to it;
    * a root with no location here is refused both ways: from_row of its rows, and to_row
      (so sql_*) of a path under its share address, raise UnmappedRoot, so that no row is
      ever written that cannot be read back. The message says what to add to the map. A
      machine whose drive is not mounted still maps the string; nothing here asks the disk;
    * a row naming a root the library does not have raises UnknownRoot.
    """

    def __init__(self, logical=None, locations=None, map_file=""):
        logical = {root_name(n): (a or "") for n, a in (logical or {}).items()}
        checked = check_locations(locations or {})
        self.logical = logical
        self.map_file = map_file
        # Roots the machine maps that this library does not have are another library's.
        self.locations = {n: v for n, v in checked.items() if n in logical}
        self.unmapped = tuple(sorted(n for n in logical if n not in self.locations))
        self.identity = not logical
        entries = {}
        explicit = {}
        for name, listed in self.locations.items():
            for spelled in listed:
                explicit[_folded(spelled)[1]] = (spelled, name)
        keyed = {name: [(s, _folded(s)[1]) for s in listed] for name, listed in self.locations.items()}
        for name, address in logical.items():
            if address and is_native_absolute(address):
                with self._sources(name):
                    _plain(address, "root %r: share address" % name)
                    spelled, folded_key = _folded(address)
                    if folded_key in explicit and explicit[folded_key][1] != name:
                        raise RootsError("%r is the share address of %r and a location of %r"
                                         % (address, name, explicit[folded_key][1]))
                    if folded_key in entries and entries[folded_key][1] != name:
                        raise RootsError("%r is the address of both %r and %r" % (address, entries[folded_key][1], name))
                    entries[folded_key] = (spelled, name)
                    # The same place as one of its own locations is harmless; anything else nested is not.
                    if all(folded_key != k for _s, k in keyed.get(name, ())):
                        keyed.setdefault(name, []).append((spelled, folded_key))
        for name, pairs in keyed.items():
            with self._sources(name):
                _nested(name, pairs)
        entries.update(explicit)
        # Longest first, so the first match is the deepest. (folder-form key, its length, name)
        self._matchers = tuple(sorted(((k, len(k), name) for k, (_s, name) in entries.items()),
                                      key=lambda m: -m[1]))
        self._first = {n: v[0] for n, v in self.locations.items()}

    @contextlib.contextmanager
    def _sources(self, name):
        """A refusal made while combining the two sources says where each comes from."""
        try:
            yield
        except RootsError as problem:
            raise RootsError("%s -- root %r: its share address is in the library's roots setting, "
                             "its locations in the machine map (%s)"
                             % (problem, name, self.map_file or "machine_roots.json")) from None

    _prepared = {}
    _lock = threading.Lock()

    @classmethod
    def of(cls, logical=None, locations=None, map_file=""):
        """The Roots for this content, built once however often it is asked for."""
        key = (tuple(sorted((logical or {}).items())),
               tuple(sorted((n, tuple([v] if isinstance(v, str) else v)) for n, v in (locations or {}).items())),
               map_file)
        with cls._lock:
            found = cls._prepared.get(key)
        if found is None:
            found = cls(logical, locations, map_file)
            with cls._lock:
                cls._prepared[key] = found
                while len(cls._prepared) > 64:
                    cls._prepared.pop(next(iter(cls._prepared)), None)
        return found

    def locate(self, native):
        """(name, relative part with "/" separators) of a native path, or None when it is
        under no root. Never raises for an unmapped root; to_row does."""
        spelled = stored(native)
        folder = _as_folder(os.path.normcase(spelled))
        for exact, size, name in self._matchers:
            if folder.startswith(exact):
                if len(folder) == size:
                    return name, ""
                rel = spelled[size:]
                return name, (rel if os.sep == ROW_SEP else rel.replace(os.sep, ROW_SEP))
        return None

    def what_to_add(self, name):
        where = self.map_file or "machine_roots.json in the TagPup home"
        return ('root %r has no location on this machine: add it to %s, for example '
                '{"version": 1, "roots": {"%s": ["D:\\\\where\\\\it\\\\lives"]}}' % (name, where, name))

    def describe(self):
        """One dict per root, for a page to show: name, logical address, this machine's
        locations (empty when it has none) and whether it has any."""
        return [{"name": n, "logical": self.logical[n], "locations": list(self.locations.get(n, ())),
                 "mapped": n in self.locations} for n in sorted(self.logical)]

    def propose_row(self, native):
        """What the row for a native path would be, or None when it is under no root, so a
        page can say "outside the roots" and where to move it. Never raises, and so says
        nothing of an unmapped root; to_row is what refuses."""
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
    With no roots, or none configured, stored() -- as it always was. "" for nothing.

    Raises UnmappedRoot for a path under a root this machine has no location for, and for
    a path under no root when such a root exists (it might be its): the machine's map
    (machine_roots.json) has to say where the root is first."""
    if not path:
        return ""
    if roots is None or roots.identity:
        return stored(path)
    found = roots.locate(path)
    if found is not None:
        if found[0] not in roots._first:
            raise UnmappedRoot(roots.what_to_add(found[0]))
        if _COLON_REFUSED and ":" in found[1]:
            raise RootsError("%r: a colon under a root is a stream name on Windows, and cannot be stored" % stored(path))
        return _row(*found)
    if roots.unmapped:
        raise UnmappedRoot("%r is under no root this machine knows, and %s %s no location here, so it "
                           "may belong to one. %s" % (stored(path), ", ".join(roots.unmapped),
                                                      "has" if len(roots.unmapped) == 1 else "have",
                                                      roots.what_to_add(roots.unmapped[0])))
    return stored(path)


def from_row(value, roots=None):
    """The native path of what the database holds. A row under no root is already native.

    A rooted row is refused, not repaired, when it is not what to_row writes: a relative
    part with an empty, "." or ".." element, a native separator, or (on Windows) a colon, or an empty
    one after the separator ("@name/"). The root's name is matched in any case, which is
    how a NOCASE comparison may hand it back."""
    if not value:
        return ""
    if not value.startswith(ROOT_MARK):
        return value
    if roots is None or roots.identity:
        raise UnknownRoot("%r is a root-relative row and no roots were given" % value)
    name, separator, rel = value[1:].partition(ROW_SEP)
    name = name.lower()
    if name not in roots.logical:
        raise UnknownRoot("%r names root %r, which this library does not have" % (value, name))
    if name not in roots._first:
        raise UnmappedRoot(roots.what_to_add(name))
    location = roots._first[name]
    if not separator:
        return location
    parts = rel.split(ROW_SEP)
    if any(part in ("", ".", "..") or (_COLON_REFUSED and ":" in part) or (os.sep != ROW_SEP and os.sep in part) for part in parts):
        raise RootsError("%r is not a path under a root" % value)
    return _as_folder(location) + (rel if os.sep == ROW_SEP else rel.replace(ROW_SEP, os.sep))


def exiftool_spelling(native):
    """A native path as ExifTool prints it in a SourceFile: with "/" for the separator, on
    every machine. Only for comparing with what ExifTool says; never stored or opened."""
    return native.replace(os.sep, "/") if os.sep != "/" else native


def outside_roots(folders, roots):
    """The folders under no root, grouped for a person to read: [{"group", "count"}], the
    biggest first, each group the first two elements of the path (D:\\Training; for a
    share, \\\\server\\share\\Folder), counting the folders given -- one per row or one per
    distinct folder, as the caller passes them. For the migration's dry run and for
    Verify. `folders` are native paths; a row already in root form is skipped. Pure, and
    never raises for an unmapped root: that root's paths are under it, not outside."""
    counts, shown = {}, {}
    for folder in folders:
        if not folder or folder.startswith(ROOT_MARK):
            continue
        spelled = stored(folder)
        if roots is not None and not roots.identity and roots.locate(spelled) is not None:
            continue
        drive, rest = os.path.splitdrive(spelled)
        names = [part for part in rest.split(os.sep) if part]
        group = drive + os.sep + names[0] if names else drive + os.sep
        group_key = os.path.normcase(group)
        counts[group_key] = counts.get(group_key, 0) + 1
        shown.setdefault(group_key, group)
    return [{"group": shown[k], "count": n} for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


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
    for exact, _size, name in roots._matchers:
        if exact != inside and exact.startswith(inside) and name not in names:
            names.append(name)
    return names
