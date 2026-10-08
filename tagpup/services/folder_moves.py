"""A folder renamed outside the apps: its rows follow it, with their faces and names.

Sync keeps a library in step with the folders it walks. A folder renamed in Explorer is
no longer at the path its rows name, and the new name is under no folder the library
holds, so nothing walks it: sync reported the old folder as wholly gone (`folders_gone`,
`roots_gone`) and could do no more (docs/findings.md). Rows cannot be matched to files
by name alone, and a folder holding photos the library has no row for is not the library's
(sync lists it to review, or leaves it), so a renamed folder needs a look of its own.

What it does, on the maintenance scaffold (tagpup.services.maintenance): a dry run unless
applied, and applied one journaled change, `relink_folders`, that History lists and undo
reverses.

- **The unit**: a folder whose rows' folder is gone from disk, taken at the topmost folder
  that is gone below one that is there (a renamed folder and every subfolder under it are
  one). If nothing above it is there -- an unplugged drive or share -- it is not looked
  at: it looks as it always did, missing rows reported, none relinked.
- **Candidates, a filter only** *(owner, 2026-10-08)*: the folders beside it, in the same
  parent, whose name begins with the same date (`2026-01-31 - Parkrun #347`, renamed to
  `2026-01-31 - Parkrun #347 Harbour`). The date picks which folders to look at; it
  proves nothing.
- **Evidence, per photo**: a file in a candidate matches a row when both hold the same
  DocumentID; else, when the sizes agree and Date Taken agrees (or neither has one and the
  file name is the same). A DocumentID that differs rules a pair out. A file name is never
  enough alone. Matching is one to one across every candidate: a row with two matching
  files, or a file two rows match (a copy, a burst), is ambiguous and left. A file that
  already has a row is the destination of nothing (the check that stopped 233 duplicate
  faces): it is not matched, and a row whose new path has one is left where it is.
- **Verdict**, per unit: *relink* when exactly one candidate holds matches and they are
  at least SHARE of the unit's rows with none ambiguous; *propose* when some match but
  that is not so (several candidates, a thin match); *none* otherwise (no date in the name,
  no candidate, no match). Only *relink* is written. A proposal the owner confirms is run
  again naming both folders (`only`), when one-to-one matches alone are enough.
- **What follows**: the matched rows are pointed at their files (photo ids, faces and
  names stay), and an entry of the library's roots or ignored folders (settings, in the
  same journaled change) under the old folder follows to the new. The folders added
  (tagpup.store.added_folders) are a record, not journaled, and follow after the change.
  Rows of the unit that matched nothing stay missing, reported, never removed.
"""
import os
import re

from tagpup.core import dates, paths, validation
from tagpup.core.result import Result
from tagpup.files import images
from tagpup.services import maintenance, relink_photos
from tagpup.services import roots as roots_service
from tagpup.store import added_folders, db, journal
from tagpup.store import journal as journal_store
from tagpup.store import photos as store_photos
from tagpup.store import roots as store_roots
from tagpup.store import settings as store_settings

OPERATION = "relink_folders"
KINDS = ("relinked", "settings")

#: The least share of a unit's rows a candidate must hold for the relink to be written
#: unasked; a folder that lost a few photos still follows, one that matches a handful does
#: not.
SHARE = 0.8

#: The most files looked at in one candidate; a folder beyond it is not looked at.
MAX_FILES = 20000

_LEADING_DATE = re.compile(r"^(\d{4})[-_.](\d{2})[-_.](\d{2})(?!\d)")


def leading_date(name):
    """The date a folder's name begins with, as YYYY-MM-DD, or None."""
    found = _LEADING_DATE.match(name or "")
    return "-".join(found.groups()) if found else None


class _Gone:
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


def _candidates(unit, extra=None):
    """The folders in `unit`'s parent whose name begins with the unit's date, plus `extra`
    (a folder the owner named). [] for a name with no date."""
    wanted = leading_date(os.path.basename(unit))
    found = {}
    if wanted:
        try:
            with os.scandir(os.path.dirname(unit)) as listing:
                for entry in listing:
                    try:
                        if entry.is_dir(follow_symlinks=False) and leading_date(entry.name) == wanted:
                            found[paths.key(entry.path)] = paths.stored(entry.path)
                    except OSError:
                        continue
        except OSError:
            pass
    if extra:
        found.setdefault(paths.key(extra), paths.stored(extra))
    return sorted(found.values(), key=paths.key)


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


def _mapped(entry, old, new):
    """`entry`, a folder, once `old` is `new`: the entry itself or one under it; None when
    it is neither."""
    if paths.same(entry, old):
        return paths.stored(new)
    if paths.is_under(entry, old):
        return paths.stored(os.path.join(new, os.path.relpath(paths.stored(entry), paths.stored(old))))
    return None


def _followed(held, key, moved):
    """(the setting's text once the folders in `moved` [(old, new)] are followed, whether it
    differs) for the folder-list setting `key` of `held` (native)."""
    lines, changed = [], False
    for line in validation.folder_list(held.get(key, "")):
        for old, new in moved:
            target = _mapped(line, old, new)
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


def undone(library, change_id):
    """After an undo of a `relink_folders` change: the folders added point back at the folders the
    rows are back in, derived from the change's own photo rows (no path is kept in its summary).
    Not when the change merged an added folder into one already there (its summary counts it): that
    cannot be told apart again. Returns the records changed, or None when it left them."""
    entries = journal_store.history(library.path, change_id=change_id, values=True)
    if not entries or entries[0]["operation"] != OPERATION:
        return None
    if (entries[0]["summary"].get("counts") or {}).get("added_merged"):
        return None
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        moved = [(store_roots.from_row(conn, change["old"]["path"]), store_roots.from_row(conn, change["new"]["path"]))
                 for change in entries[0].get("values", ())
                 if change["table"] == "photos" and change["action"] == "update"
                 and change["old"] and change["new"] and "path" in change["old"] and "path" in change["new"]]
    finally:
        conn.close()
    pairs = folder_pairs(moved)

    def back(conn):
        return sum(added_folders.follow(conn, new, old) for old, new in pairs)
    return db.write_with_connection(library.path, back) if pairs else 0


def look(library, exiftool_path=None, only=None):
    """A Plan: what the folders renamed outside the apps would do. `only` is (old folder,
    new folder) the owner confirmed. Reads the rows, lists the folders beside each gone one,
    and reads ExifTool for the files in the candidates alone. Writes nothing.
    work = {"edits", "followed": [(old, new)] of the units written}."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        evidence = store_photos.evidence(conn)
        known = {paths.key(path) for _id, path, _s, _t, _d in evidence}
        added = added_folders.every(conn)
    finally:
        conn.close()
    # Every folder a row is in, and every folder above one: where the library holds photos.
    held_dirs = set()
    for _id, path, _s, _t, _d in evidence:
        current = os.path.dirname(path)
        while paths.key(current) not in held_dirs:
            held_dirs.add(paths.key(current))
            above = os.path.dirname(current)
            if above == current:
                break
            current = above
    gone = _Gone()
    units, unreachable = {}, 0
    for row in evidence:
        folder = os.path.dirname(row[1])
        if gone.there(folder):
            continue
        top = gone.unit(folder)
        if top is None:
            unreachable += 1
            continue
        units.setdefault(paths.key(top), [top, []])[1].append(row)
    asked_from = asked_to = None
    if only:
        asked_from, asked_to = paths.stored(only[0]), paths.stored(only[1])
        units = {key: unit for key, unit in units.items() if paths.same(unit[0], asked_from)}

    # The folders to look in, and the files in them without a row.
    found, files, held, home, held_in = {}, {}, 0, {}, {}
    for key, (top, _rows) in units.items():
        candidates = [paths.stored(asked_to)] if only else _candidates(top)
        found[key] = []
        for candidate in candidates:
            if not os.path.isdir(candidate):
                continue
            seen = images.stamps_under(candidate)
            if len(seen) > MAX_FILES:
                continue
            found[key].append(candidate)
            for file_key, stamp in seen.items():
                if file_key in known:
                    held += 1
                    held_in[key] = held_in.get(key, 0) + 1
                else:
                    files[file_key] = stamp
                    home[(key, file_key)] = candidate
    info = _read(files, exiftool_path) if files else {}
    by_size, by_doc = {}, {}
    for file_key, (_path, size, doc, _taken) in info.items():
        by_size.setdefault(size, []).append(file_key)
        if doc:
            by_doc.setdefault(doc, []).append(file_key)

    # Each row's matches, among the files of its own unit's candidates.
    matches, claimed = {}, {}
    for key, (top, rows) in units.items():
        for row in rows:
            options = set(by_doc.get((row[4] or "").strip(), ())) | set(by_size.get(row[2], ()))
            for file_key in sorted(options):
                if (key, file_key) not in home:
                    continue
                how = _matches(row, info[file_key])
                if how:
                    matches.setdefault(row[0], []).append((file_key, how))
                    claimed.setdefault(file_key, set()).add(row[0])
    ambiguous_rows, ambiguous_files, pairs_of = set(), set(), {}
    for row_id, options in matches.items():
        if len(options) != 1 or len(claimed[options[0][0]]) != 1:
            ambiguous_rows.add(row_id)
            ambiguous_files.update(file_key for file_key, _how in options)
        else:
            pairs_of[row_id] = options[0]

    # The verdict of each unit.
    reports, written, tally, pairs = [], [], {"relink": 0, "propose": 0, "none": 0}, []
    matched = by_id = 0
    for key, (top, rows) in sorted(units.items(), key=lambda item: item[0]):
        mine = [(row, pairs_of[row[0]]) for row in rows if row[0] in pairs_of]
        by_candidate = {}
        for _row, (file_key, how) in mine:
            candidate = home[(key, file_key)]
            entry = by_candidate.setdefault(paths.key(candidate), [candidate, 0, 0])
            entry[1] += 1
            entry[2] += how == "id"
        ambiguous = sum(1 for row in rows if row[0] in ambiguous_rows)
        target = None
        if not found[key]:
            verdict = "none"
            why = ("no date in the name" if not leading_date(os.path.basename(top))
                   else "no folder beside it begins with its date")
        elif not mine:
            verdict, why = "none", "no photo of a candidate matches"
            if held_in.get(key):
                why += ("; %d photo(s) in the candidate already have rows, and a file with a row is no destination"
                        % held_in[key])
        elif only:
            verdict, why, target = "relink", "confirmed by the owner", found[key][0]
        elif len(by_candidate) == 1 and len(mine) >= SHARE * len(rows) and not ambiguous:
            verdict, why = "relink", "one candidate holds %d of %d photos" % (len(mine), len(rows))
            target = next(iter(by_candidate.values()))[0]
        else:
            verdict = "propose"
            why = "%d candidate(s) hold matches (%d of %d photos), %d ambiguous" % (
                len(by_candidate), len(mine), len(rows), ambiguous)
            if held_in.get(key):
                why += "; %d photo(s) there already have rows" % held_in[key]
        tally[verdict] += 1
        if verdict == "relink":
            pairs += [(row[1], info[file_key][0]) for row, (file_key, _how) in mine]
            matched += len(mine)
            by_id += sum(1 for _row, (_file_key, how) in mine if how == "id")
            written.append((top, target))
        reports.append({"from": top, "verdict": verdict, "why": why, "to": target, "rows": len(rows),
                        "matched": len(mine), "ambiguous": ambiguous,
                        "candidates": [{"folder": c[0], "matched": c[1], "by_id": c[2]}
                                       for c in by_candidate.values()]})

    # An added folder gone from disk with no row under it -- its rows moved already, by hand or by a sync --
    # is a ghost: reported gone by every sync, and watched. It follows the one folder beside it whose name
    # begins with its date and which holds the library's photos, or is dropped into it if that was added.
    ghosts = unreachable_added = 0
    if not only:
        tops = [unit[0] for unit in units.values()]
        for path, _subfolders in added:
            if gone.there(path):
                continue
            top = gone.unit(path)
            if top is None:
                unreachable_added += 1
                continue
            if any(paths.same(top, t) or paths.same(path, t) or paths.is_under(path, t) for t in tops):
                continue
            options = [c for c in _candidates(top) if paths.key(c) in held_dirs]
            target, candidates = None, [{"folder": c, "matched": 0, "by_id": 0} for c in options]
            if len(options) == 1:
                verdict, target = "relink", options[0]
                why = ("an added folder with no photo under it: it follows the one folder beside it that begins with "
                       "its date and holds the library's photos")
            elif options:
                verdict, why = "propose", "%d folders beside it begin with its date and hold the library's photos" % len(options)
            else:
                verdict = "none"
                why = ("no date in the name" if not leading_date(os.path.basename(top))
                       else "no folder beside it begins with its date and holds the library's photos")
            tally[verdict] += 1
            if target:
                written.append((path, target))
                ghosts += 1
            reports.append({"from": path, "verdict": verdict, "why": why, "to": target, "rows": 0, "matched": 0,
                            "ambiguous": 0, "candidates": candidates, "kind": "added"})
    added_keys = {paths.key(path) for path, _s in added}
    renamed = merged = 0
    for old, new in written:
        for path, _s in added:
            target = _mapped(path, old, new)
            if target is not None:
                merged += paths.key(target) in added_keys
                renamed += paths.key(target) not in added_keys

    edits, occupied = [], []
    if pairs:
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            moves = relink_photos.moves_with_faces(conn, pairs)
        finally:
            conn.close()
        edits, occupied = relink_photos.edits_for(library, moves)
    else:
        moves = []
    followed = []
    if written:
        held_settings = store_settings.read_only(library.path)
        for key in store_roots.FOLDER_SETTINGS:
            if key not in held_settings:
                continue
            text, changed = _followed(held_settings, key, written)
            if changed:
                edits.append(journal.update(store_settings.TABLE, (key,), {"value": held_settings[key]},
                                            {"value": text}, kind="settings"))
                followed.append(key)
    return maintenance.Plan(
        size=len(edits),
        counts={"rows_in_gone_folders": sum(len(unit[1]) for unit in units.values()),
                "rows_unreachable": unreachable, "gone_folders": len(units),
                "relink": tally["relink"], "propose": tally["propose"], "none": tally["none"],
                "rows_matched": matched, "by_document_id": by_id, "by_content": matched - by_id,
                "ambiguous_rows": len(ambiguous_rows), "ambiguous_files": len(ambiguous_files),
                "occupied": len(occupied), "moved_faces": sum(m["faces"] for m in moves),
                "moved_named": sum(m["named"] for m in moves), "files_read": len(files),
                "files_with_rows": held, "settings_followed": len(followed),
                "added_ghosts": ghosts, "added_unreachable": unreachable_added,
                "added_renamed": renamed, "added_merged": merged},
        reveal={"folders": reports, "occupied": occupied},
        work={"edits": edits, "followed": written})


def relink(library, exiftool_path=None, apply=False, only=None):
    """Follow the folders renamed outside the apps (see the module): a dry run unless
    `apply`. A Result on the maintenance scaffold; details["counts"] what was found,
    ["reveal"]["folders"] each unit's verdict. Applied, `changed` is the rows and settings
    written, and the folders added follow after (details["added_followed"]). `only` is (old,
    new), the owner's word for a proposal: a path in any place of its root is spelled by the first
    (roots_service.canonical); refused when the old folder is still there or the new one is not."""
    if only:
        only = (roots_service.canonical(library, paths.stored(only[0])),
                roots_service.canonical(library, paths.stored(only[1])))
        why = None
        if os.path.isdir(only[0]):
            why = "The folder named by --from is still there: there is nothing to follow."
        elif not os.path.isdir(only[1]):
            why = "The folder named by --to is not there, or is not a folder."
        if why:
            result = Result()
            result.refuse(why)
            result.details.update(counts={}, changed={kind: 0 for kind in KINDS}, dry_run=not apply)
            return result
    try:
        with roots_service.pinned(library):
            return _relink(library, exiftool_path, apply, only)
    except (roots_service.RootsChanged, roots_service.Unplaced) as stop:
        result = Result()
        result.refuse(roots_service.stopped(stop))
        result.details.update(counts={}, changed={kind: 0 for kind in KINDS}, dry_run=not apply)
        return result


def _relink(library, exiftool_path, apply, only):
    held = {}

    def plan(found):
        held["plan"] = look(found, exiftool_path, only)
        return held["plan"]

    result = maintenance.run(library, OPERATION, plan, lambda planned: planned.work["edits"], apply=apply, kinds=KINDS)
    result.details["added_followed"] = 0
    planned = held.get("plan")
    if (apply and result.ok and planned is not None and planned.work["followed"]
            and (result.changed or not planned.size)):
        followed = planned.work["followed"]

        def follow(conn):
            return sum(added_folders.follow(conn, old, new) for old, new in followed)
        try:
            result.details["added_followed"] = db.write_with_connection(library.path, follow)
        except Exception as e:
            result.fail("the folders added", "%s: %s" % (type(e).__name__, e))
    return result
