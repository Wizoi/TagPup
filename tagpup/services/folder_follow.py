"""What following a renamed folder shares: the helpers of folder_ids (tagpup.services.folder_ids) and of the undo.

The command that looked for a renamed folder beside itself by its leading date (`relink-folders`) is gone
(owner, 2026-10-10: a one-off is fixed by hand and flagged, not corrected by a command); a folder that carries a
marker is followed by it (folder_ids), and a sync pairs the files it finds new with the rows missing by what the
files hold (sync). What is left here is what those two use:

- `Gone`, which folders are there, asked once each;
- `pair_by_evidence`, a row and a file shown to be one photo by DocumentID, else size and Date Taken, one to one;
- `settings_edits_for` and `plan_added`, which make the library's roots, ignored folders and added folders follow;
- `undone`, which points the added folders back after an undo of either operation.
"""
import os

from tagpup.core import dates, paths, validation
from tagpup.services import relink_photos
from tagpup.store import added_folders, db, journal
from tagpup.store import folder_ids as store_folder_ids
from tagpup.store import journal as journal_store
from tagpup.store import roots as store_roots
from tagpup.store import settings as store_settings

OPERATION = "relink_folders"

#: What the change is recorded as when the folders followed are told by their markers (tagpup.services.folder_ids): its
#: undo points the folders added back the same way.
MARKER_OPERATION = store_folder_ids.FOLLOW_OPERATION
OPERATIONS = (OPERATION, MARKER_OPERATION)

class Gone:
    """Which folders are there, asked once each."""

    def __init__(self):
        self._there = {}

    def there(self, folder):
        key = paths.key(folder)
        if key not in self._there:
            self._there[key] = os.path.isdir(folder)
        return self._there[key]

    def unit(self, folder):
        """The topmost gone folder at or above `folder` whose parent is there: None for a
        folder that is there, and for one with no parent there at all (a drive or share not
        reachable)."""
        if self.there(folder):
            return None
        top, current = folder, folder
        while not self.there(current):
            parent = os.path.dirname(current)
            if parent == current:
                return None
            top, current = current, parent
        return top


def _matches(row, info):
    """Do this row (id, path, size, taken, document_id) and this file (path, size, doc,
    taken) show one photo, and by what: "id", "content" or None."""
    _id, path, size, taken, doc = row
    f_path, f_size, f_doc, f_taken = info
    doc, f_doc = (doc or "").strip(), (f_doc or "").strip()
    if doc and f_doc:
        return "id" if doc == f_doc else None
    if size is None or size != f_size:
        return None
    taken, f_taken = (taken or "").strip(), (f_taken or "").strip()
    if taken and f_taken:
        return "content" if taken == f_taken else None
    if not taken and not f_taken and paths.name_key(os.path.basename(path)) == paths.name_key(os.path.basename(f_path)):
        return "content"
    return None


def _read(files, exiftool_path):
    """{key: (path, size, document id, date taken)} of `files` ({key: (path, mtime, size)}),
    read for the identity and the date: the only reads, of the files in the candidates."""
    rows = relink_photos.read_files([path for path, _m, _s in files.values()],
                                    [relink_photos.IDENTITY] + list(dates.DATE_TAKEN_FIELDS), exiftool_path)
    read = {}
    for row in rows:
        source = row.get("SourceFile")
        if not source:
            continue
        key = paths.key(paths.stored(source))
        if key not in files:
            continue
        doc = row.get("XMP:DocumentID") or row.get("XMP-xmpMM:DocumentID")
        read[key] = (files[key][0], files[key][2], str(doc).strip() if doc else "", dates.date_taken(row))
    # A file ExifTool did not answer for is known by its stamp alone.
    for key, (path, _m, size) in files.items():
        read.setdefault(key, (path, size, "", None))
    return read


def mapped(entry, old, new):
    """`entry`, a folder, once `old` is `new`: the entry itself or one under it; None when
    it is neither."""
    if paths.same(entry, old):
        return paths.stored(new)
    if paths.is_under(entry, old):
        return paths.stored(os.path.join(new, os.path.relpath(paths.stored(entry), paths.stored(old))))
    return None


def followed_settings(held, key, moved):
    """(the setting's text once the folders in `moved` [(old, new)] are followed, whether it
    differs) for the folder-list setting `key` of `held` (native)."""
    lines, changed = [], False
    for line in validation.folder_list(held.get(key, "")):
        for old, new in moved:
            target = mapped(line, old, new)
            if target is not None:
                line, changed = target, True
                break
        lines.append(line)
    return validation.FOLDER_SEPARATOR.join(lines), changed


def folder_pairs(moved):
    """[(old folder, new folder)] the photos `moved` [(old path, new path)] show a folder to have
    followed: each pair's folders with the names they share at the end taken off (`A/sub/x` to
    `B/sub/y` is A to B), the distinct ones, none where a photo stayed in its folder."""
    found = {}
    for old, new in moved:
        a, b = os.path.dirname(paths.stored(old)), os.path.dirname(paths.stored(new))
        while True:
            (a_up, a_name), (b_up, b_name) = os.path.split(a), os.path.split(b)
            if not a_name or not b_name or paths.name_key(a_name) != paths.name_key(b_name) or a_up == a or b_up == b:
                break
            a, b = a_up, b_up
        if not paths.same(a, b):
            found.setdefault((paths.key(a), paths.key(b)), (a, b))
    return sorted(found.values(), key=lambda pair: paths.key(pair[0]))


def settings_edits_for(library, written):
    """([journal edits], [setting keys]) that follow the folders `written` [(old, new)] in the
    library's two folder settings (roots and ignored folders): an entry at or under an old folder
    goes to the new. Reads the settings; writes nothing."""
    edits, followed = [], []
    if written:
        held_settings = store_settings.read_only(library.path)
        for key in store_roots.FOLDER_SETTINGS:
            if key not in held_settings:
                continue
            text, changed = followed_settings(held_settings, key, written)
            if changed:
                edits.append(journal.update(store_settings.TABLE, (key,), {"value": held_settings[key]},
                                            {"value": text}, kind="settings"))
                followed.append(key)
    return edits, followed


def plan_added(added, written):
    """([(old, new)] the folders added follow, [{from, to, why}] left) for the folders `written`
    [(old, new)] followed. The folders added follow a unit only on a clean one-to-one rename: the new
    name not added already, and no other unit going into it. Otherwise the record is left and
    reported; no added folder is merged. `added` is added_folders.every."""
    targets = {}
    for _old, new in written:
        targets[paths.key(new)] = targets.get(paths.key(new), 0) + 1
    added_keys = {paths.key(path) for path, _s in added}
    follow, left = [], []
    for old, new in written:
        into = [mapped(path, old, new) for path, _s in added]
        into = [target for target in into if target is not None]
        if not into:
            continue
        if targets[paths.key(new)] > 1:
            left.append({"from": old, "to": new, "why": "two folders into one"})
        elif any(paths.key(target) in added_keys for target in into):
            left.append({"from": old, "to": new, "why": "target already added"})
        else:
            follow.append((old, new))
    return follow, left


def pair_by_evidence(rows, files, exiftool_path):
    """[(row path, file path)] of the rows (id, path, size, taken, document_id) and the files
    ({key: (path, mtime, size)}) that show one photo each, by the evidence of a folder that was
    renamed (see the module): the DocumentID, else size and Date Taken. One to one: a row with
    two matching files, or a file two rows match, is left. A file name is never enough alone.
    Reads the files with ExifTool."""
    if not rows or not files:
        return []
    info = _read(files, exiftool_path)
    by_size, by_doc = {}, {}
    for file_key, (_path, size, doc, _taken) in info.items():
        by_size.setdefault(size, []).append(file_key)
        if doc:
            by_doc.setdefault(doc, []).append(file_key)
    matches, claimed = {}, {}
    for row in rows:
        options = set(by_doc.get((row[4] or "").strip(), ())) | set(by_size.get(row[2], ()))
        for file_key in sorted(options):
            if _matches(row, info[file_key]):
                matches.setdefault(row[0], []).append(file_key)
                claimed.setdefault(file_key, set()).add(row[0])
    return [(row[1], info[matches[row[0]][0]][0]) for row in rows
            if len(matches.get(row[0], ())) == 1 and len(claimed[matches[row[0]][0]]) == 1]


def undone(library, change_id):
    """After an undo of a `relink_folders` change: the folders added point back at the folders the
    rows are back in, derived from the change's own photo rows (no path is kept in its summary).
    Not when the change left any added folder where it was (its summary counts them): which were
    pointed cannot be told apart again. Returns the records changed, or None when it left them."""
    entries = journal_store.history(library.path, change_id=change_id, values=True)
    if not entries or entries[0]["operation"] not in OPERATIONS:
        return None
    if (entries[0]["summary"].get("counts") or {}).get("added_left"):
        return None
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        moved = [(store_roots.from_row(conn, change["old"]["path"]), store_roots.from_row(conn, change["new"]["path"]))
                 for change in entries[0].get("values", ())
                 if change["table"] == "photos" and change["action"] == "update"
                 and change["old"] and change["new"] and "path" in change["old"] and "path" in change["new"]]
        # A change that followed folders by their markers moved folder_ids rows too, a folder whose photos
        # moved earlier among them: those are the folders themselves.
        marked = [(store_roots.from_row(conn, change["old"]["path"]), store_roots.from_row(conn, change["new"]["path"]))
                  for change in entries[0].get("values", ())
                  if change["table"] == "folder_ids" and change["action"] == "update"
                  and change["old"] and change["new"] and "path" in change["old"] and "path" in change["new"]]
    finally:
        conn.close()
    pairs = folder_pairs(moved)
    known = {(paths.key(a), paths.key(b)) for a, b in pairs}
    pairs += [(a, b) for a, b in marked if (paths.key(a), paths.key(b)) not in known]

    def back(conn):
        return sum(added_folders.follow(conn, new, old) for old, new in pairs)
    return db.write_with_connection(library.path, back) if pairs else 0
