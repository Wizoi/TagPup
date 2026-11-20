"""A library's journal, as the CLI, the pages and the MCP server's `history` use it: what was changed, and undoing a
change (tagpup.store.journal; docs/ARCHITECTURE.md, phase
7.5).

An undo is a dry run unless told to apply, as every bulk operation is: the dry run is
the rehearsal -- the undo and the change again inside a transaction rolled back -- and
says whether that restored every row exactly, or why the undo would be refused. What a
change holds can name people, so its values are shown only when asked (`reveal`): the
library is photographs of real people, many of them minors.

Whether a change may be undone now has one owner: why_not, which asks UNDO_CHECKS -- every kind of change the code
makes, by its operation's name, with what its way back says of itself -- and the journal's own account (status, schema
version, a newer change of the same rows or files, or of the files of its photos: tagpup.store.journal.refusal). The History dialog lists by it, and the
rehearsal and the undo refuse by it before anything is written: nothing in tagpup/ but this module calls the store's undo
(tests/test_every_change_kind_has_an_undo_check.py). A switch or a retiring landing between the rehearsal and the
undo's write is not asked again (owner, 2026-10-23: accepted). A stamp of a library's settings
(tagpup.services.settings.STAMPS) is never undone: it is the library's first settings, and undone the library held
none -- the next read stamped it again; nor is a migration. A change whose undo would put back a face model the library
may not be switched to now is refused by the switch's own check (tagpup.services.settings.undo_check); a retiring of a
model's vectors never (tagpup.services.retire_photo_vectors.vectors_check); nor a retiring of the old detector's
faces, a kind the live journals hold from a command since deleted (RETIRED): undone, it would bring the old model's
faces back; nor a face pass recorded before such a retiring (pass_check).

A change of photo files (tagpup.services.file_changes) is undone file by file, and its
rows follow: a file that no longer holds what the change left is refused, named, and the
rest are put back. Its rehearsal reads every file and writes none. Undoing one needs the
library's ExifTool, which the caller names.
"""
import logging
import re

from tagpup.core.result import NotFound, Result
from tagpup.services import (
    baking,
    bulk_edit,
    convert,
    face_batch,
    face_people,
    file_changes,
    retire_photo_vectors,
    sync,
    tagging,
    tags_from_faces,
)
from tagpup.services import settings as library_settings
from tagpup.store import db, file_journal, journal

logger = logging.getLogger(__name__)

#: The retiring of the old detector's faces, by its operation's name with the model key: a kind no code makes any more
#: (RETIRED), which a face pass's undo still asks of (pass_check).
RETIRED_FACES = "retire faces (%s)"

#: The new face model's pass over a folder, by its operation's name with the model key: a kind no code makes any more
#: (RETIRED), which the face gate reads the passes by and pass_check matches.
FACE_PASS = "face pass (%s)"

#: How many changes History reads at a time while it gathers its lines (lines).
READ_AT_ONCE = 100

#: Why an undo of a face pass's change is refused while a retiring after it stands (pass_check).
PASS_STANDS_IN = "change %d (retire faces) deleted the old faces this pass's new faces stand in for, and is not undone"


def pass_check(conn, change_id, operation, _summary):
    """Why a face pass's change ("face pass (<key>)") is not undone now, or None: a retiring of the old detector's faces
    under the same key recorded after it stands (applied, not undone): PASS_STANDS_IN, naming the newest such. Undone, the
    pass would take the new model's faces and vectors from photos whose old faces are gone, and the retiring is never
    undone (RETIRED). The journal alone allows it -- each wrote other rows than the other did. Live: photo_index's passes
    before change 3247, kr-track's before 347, renton_parkrun's before 1225 (2026-10-26). Removed with RETIRED by the
    baseline migration, which drops the journals holding both. The caller's read-only connection."""
    head, tail = FACE_PASS.split("%s")
    key = operation[len(head):len(operation) - len(tail)]
    later = [kept_id for kept_id, _kept in journal.kept_named(conn, RETIRED_FACES % key) if kept_id > change_id]
    return PASS_STANDS_IN % later[-1] if later else None


#: Every kind of change the code makes, by its operation's name -- a format ("face pass (%s)") for a kind named with a
#: key, matched whole -- and what its way back says of itself:
#: - a sentence: never undone, said before the journal's account (a stamp of the settings; a migration's record, which
#:   has no rows to put back); formatted with the change's id and operation (%(change)d, %(operation)s);
#: - check(conn, change_id, operation, summary): asked after the journal's account allows it, on the same read-only
#:   connection, before an undo writes (never inside its write) -- the way forward's own check where there is one (a
#:   switch of face model);
#: - None: the journal's account is the whole.
#: A kind the code makes and this does not name fails tests/test_every_change_kind_has_an_undo_check.py; a name no code
#: makes any more stays while a live journal holds it (RETIRED). A name neither knows (only a test makes one) gets the
#: journal's account alone.
UNDO_CHECKS = {
    # A stamp of the settings, the library's first (settings.STAMPS: the table cannot name one it leaves out).
    **{stamp: library_settings.NOT_UNDONE for stamp in library_settings.STAMPS - library_settings.RETIRED_STAMPS},
    library_settings.CHANGE: library_settings.undo_check,
    "migration %d: %s": "change %(change)d (%(operation)s): a migration is not undone",
    retire_photo_vectors.VECTORS_OPERATION: retire_photo_vectors.vectors_check,
    face_people.UNNAMED: None,
    face_people.ADDED: None,
    face_people.REMOVED: None,
    journal.PERSON_DELETED: None,
    journal.PERSON_MERGED: None,
    tagging.ADD_TO_ALL: None,
    tags_from_faces.OPERATION: None,
    # The index's bake (file_journal.INTERNAL): no operation; listed nowhere, and erased once it finishes (its conflicts of NOTES kept).
    baking.INDEX_OPERATION: "change %(change)d (%(operation)s): a bake the index wrote is not an operation; it is not undone",
    sync.OPERATION: None,
    "save photo": None,
    "apply all suggestions": None,
    "rename tag": None,
    "remove tag": None,
    tagging.CLEAR_DUPLICATES: None,
    "rename after caption": None,
    "smart rename": None,
    "smart rename: original names": None,
    "time shift": None,
    "rotate": None,
    "faces_from_tags": None,
    # A clip's conversion: its original is deleted for good (tagpup.services.convert); the carry into its copy is a
    # write of fields as Save's.
    convert.OPERATION: "change %(change)d (%(operation)s): not undone, the original is deleted",
    convert.CARRIED: None,
    # A Delete (file_journal.INTERNAL): its record lasts only while it is under way, and the photo is in the Bin.
    file_journal.DELETE: ("change %(change)d (%(operation)s): a Delete is not undone; restore the photo from the "
                          "Recycle Bin"),
}
UNDO_CHECKS.update({operation: None for operation in bulk_edit.OPERATIONS.values()})
UNDO_CHECKS.update({operation: None for operation in face_batch.DESCRIPTIONS.values()})

#: Kinds of change no code makes any more, which the live journals hold (counted read-only 2026-10-23: photo_index 3,247
#: changes, kr-track 341, renton_parkrun 1,220): what their way back says of itself, as UNDO_CHECKS.
RETIRED = {
    # The one-off that dated undated photos and clips by their folders (deleted, DECISIONS 2026-11-15): 2 applied in
    # photo_index (changes 10630 and 10631, counted 2026-11-15). A save of their Date Taken, undone as Save's is.
    "date undated photos by their folders": None,
    # The retiring of the old detector's faces: 1 applied in each library (photo_index 3247, kr-track 347,
    # renton_parkrun 1225, counted 2026-10-25). Never undone: the faces it deleted are the old model's.
    RETIRED_FACES: ("change %(change)d (%(operation)s): the old detector's faces it deleted are not put back: "
                          "restore the snapshot taken before (TagPup CLI: snapshots list, snapshots restore)"),
    # A stamp of the settings read from config.ini: photo_index 1, kr-track 1.
    **{stamp: library_settings.NOT_UNDONE for stamp in library_settings.RETIRED_STAMPS},
    # The deletion of the old detector's rows (MTCNN's settings and its detection records), from a command since deleted
    # with the model (2026-10-28): 1 applied in each library (photo_index 4871, kr-track 371, renton_parkrun 1235,
    # counted read-only 2026-10-28). Never undone: it would put back settings no validation knows any more and records
    # under a detector no library is on (DECISIONS 2026-10-28, THE OLD MODELS ARE DELETED).
    "delete old detector rows (mtcnn)": ("change %(change)d (%(operation)s): the old detector's settings and detection "
                                         "records it deleted are not put back: the old detector and its settings are "
                                         "deleted, and no library is on it"),
    # The maintenance operations deleted with the MCP server's write tools (DECISIONS 2026-10-28, THE MCP SERVER ONLY
    # READS), counted read-only 2026-10-28: a merge of bare person tags into their People paths, photo_index change 1, and
    # a refresh of rows from their files, kr-track change 2, each applied. Both are listed in History and can never be
    # undone: the libraries are at schema 35 and each change was made at 9 and 10, so `why_not` refuses on the schema
    # version before it reaches this entry. The entries stay for the journal's own account of a change made at the
    # library's current schema (the tests). dedupe_faces was never applied in any library: a name no table knows gets
    # the journal's account alone, which is what it had.
    "merge_duplicate_person_tags": None,
    "refresh_rows": None,
    # The roots' adoption of a folder and repair of a root's address: 1 adopt in each library, 1 repair in renton_parkrun.
    "roots adopt: %s": None,
    "roots repair-address: %s": None,
    # The new face model's pass over a folder, from a command since deleted: photo_index 2,500 applied, kr-track 20
    # applied and 1 undone, renton_parkrun 8 applied (counted 2026-10-27). Undone while a later retiring stands, it would
    # take the new faces from photos whose old faces are gone (pass_check).
    FACE_PASS: pass_check,
    # A folder's bake, from a command since deleted (the index bakes now, baking.INDEX_OPERATION): photo_index 2,845
    # applied and 6 failed, kr-track 41, renton_parkrun 16 (counted 2026-10-27). The journal puts back what each file
    # held; the fingerprint rows stay (baking.follower, #1894).
    baking.OPERATION: None,
    # The relink of renamed photos' rows: renton_parkrun 1.
    "relink_renamed_photos": None,
    # A bulk edit run as a job, before bulk edits were named by their edit: renton_parkrun 1 each.
    "bulk people (job %d)": None,
    "bulk tags (job %d)": None,
    # The re-derives of rows from their own raw_metadata, one-offs deleted once each had run (#2734): photo_index holds 2
    # "rederive tags and captions" applied and 1 "rederive clip dates" applied, kr-track and renton_parkrun none
    # (counted read-only 2026-11-08). The journal's account is the whole: each is undone from History as a change of
    # rows is.
    "rederive tags and captions": None,
    "rederive clip dates": None,
    # The one-off dating of clips by their names, a program since deleted (it ran 2026-11-13: photo_index change 8261,
    # 669 clips written). As a save of clip files is undone from History: the journal puts back what each held.
    "date clips by their names": None,
    # The one-off writing clips' Date Taken into their QuickTime headers, a program since deleted (it ran 2026-11-14:
    # photo_index changes 8891 and 8892, 778 clips written). As a save of clip files is undone from History: the journal
    # puts back what each held.
    "date clip headers": None,
}
UNDO_CHECKS.update(RETIRED)


def _pattern(name):
    """The regular expression an UNDO_CHECKS key matches an operation by: the key whole, a %s any text, a %d digits."""
    return re.compile("".join(".+" if part == "%s" else r"\d+" if part == "%d" else re.escape(part)
                              for part in re.split(r"(%[sd])", name)) + r"\Z", re.S)


_PATTERNED = [(_pattern(name), name) for name in UNDO_CHECKS if "%" in name]


def kind_of(operation):
    """The UNDO_CHECKS key a change made as `operation` is of: the name whole, else the format it matches ("migration
    37: ..." is of "migration %d: %s"); None for a name the table does not hold (only a test makes one)."""
    if operation in UNDO_CHECKS and "%" not in operation:
        return operation
    for pattern, name in _PATTERNED:
        if pattern.match(operation):
            return name
    return None


def undo_rule(operation):
    """What UNDO_CHECKS says of the way back of a change made as `operation`: a sentence, a check, or None (also for a
    name it does not hold)."""
    kind = kind_of(operation)
    return None if kind is None else UNDO_CHECKS[kind]


def why_not(conn, change_id):
    """Why change `change_id` cannot be undone now, or None when it may be tried: THE one account (#1805), in order -- a
    kind never undone (UNDO_CHECKS' sentence), the journal's (tagpup.store.journal.refusal: no such change, its status, its
    schema version, a newer change of the same rows or files, or of its photos' files), then the kind's own check (UNDO_CHECKS). The caller's
    connection. Reads only."""
    described = journal.described(conn, change_id)
    if described is None:
        return "there is no change %d" % change_id
    operation, summary = described
    rule = undo_rule(operation)
    if isinstance(rule, str):
        return rule % {"change": change_id, "operation": operation}
    reasons = journal.refusal(conn, change_id)
    if reasons:
        return "; ".join(reasons)
    return rule(conn, change_id, operation, summary) if rule is not None else None


def history(library, change_id=None, reveal=False, limit=20):
    """The library's changes, newest first, up to `limit`; or change `change_id` alone,
    with the keys of every row it wrote, and with `reveal` each column's values before
    and after (a BLOB by its size). NotFound for a change the library has not."""
    entries = journal.history(library.path, limit=limit, change_id=change_id, values=reveal)
    reasons = refusals(library, entries)
    for entry in entries:
        entry["why_not"] = reasons[entry["id"]]
    if not reveal:
        for entry in entries:
            entry["summary"] = _without_folder(entry["summary"])
    if change_id is not None and not entries:
        raise NotFound("There is no change %d in the library %s." % (change_id, library.name))
    return {"changes": entries}


def _without_folder(summary):
    """A summary without its folder: a folder is a path, and paths can name people (kept for `reveal` only)."""
    return {key: value for key, value in summary.items() if key != "folder"}


def lines(library, limit=20):
    """The library's history as the History dialog lists it: up to `limit` lines, newest first, each one change (an entry
    of history's, with its why_not, without its folder) -- or the changes of one action of the owner's written in chunks,
    each holding one token in its summary (journal.GROUP: a page's bulk tag write, tagging.change_each), all of them,
    however far back, as ONE line (_group_line). A token on one change alone is that change's own line; two groups
    written at once are two lines, by their tokens, never by being next to each other."""
    entries, before = [], None
    while True:
        batch = journal.history(library.path, limit=READ_AT_ONCE, before=before)
        entries += batch
        if len(batch) < READ_AT_ONCE or len(_by_line(entries)) >= limit:
            break
        before = batch[-1]["id"]
    kept = dict(list(_by_line(entries).items())[:limit])
    tokens = [token for kind, token in kept if kind == journal.GROUP]
    if tokens:
        # The older changes of a group listed: past what was read, however many.
        for older in journal.history(library.path, before=entries[-1]["id"], groups=tokens):
            kept[(journal.GROUP, older["summary"][journal.GROUP])].append(older)
    members = [entry for line in kept.values() for entry in line]
    reasons = refusals(library, members)
    for entry in members:
        entry["why_not"] = reasons[entry["id"]]
        entry["summary"] = _without_folder(entry["summary"])
    return {"changes": [line[0] if len(line) == 1 else _group_line(line) for line in kept.values()]}


def _by_line(entries):
    """{key: its entries, newest first} of `entries` (newest first), in the order of each line's newest: a change of a
    group (journal.GROUP in its summary) under (GROUP, its token), any other under ("change", its id)."""
    found = {}
    for entry in entries:
        token = entry["summary"].get(journal.GROUP)
        found.setdefault((journal.GROUP, token) if token else ("change", entry["id"]), []).append(entry)
    return found


def _group_line(members):
    """One line of History for the changes of one group (`members`, newest first, each with its why_not): what is left of
    it -- the changes not undone, or all of them once every one is -- its photos (the photo files WRITTEN, the sum of its
    files, not the photos a chunk planned: a photo skipped is not counted), rows and files summed, `writes` how many
    changes, and `members` those not undone, {"id", "why_not"} newest first, which the dialog's Undo posts one at a time
    through the one per-change undo (a newer write of a file is undone before an older one: journal.refusal). Undoable
    while one of them is; else why not, as its newest says."""
    left = [entry for entry in members if entry["status"] != "undone"]
    shown = left or members
    newest = shown[0]
    token = newest["summary"][journal.GROUP]
    files = _summed(entry["files"] for entry in shown)
    return {**newest, "created": shown[-1]["created"],
            "summary": {journal.GROUP: token, "photos": sum(files.values())},
            "rows": _summed(entry["rows"] for entry in shown), "files": files,
            "why_not": None if any(entry["why_not"] is None for entry in left) else newest["why_not"],
            "group": token, "writes": len(shown),
            "members": [{"id": entry["id"], "why_not": entry["why_not"]} for entry in left]}


def _summed(counts):
    """The counts of `counts` ({key: count, or counts of their own}: rows by table and action, files by state) added up."""
    total = {}
    for each in counts:
        for key, value in each.items():
            total[key] = _summed([total.get(key, {}), value]) if isinstance(value, dict) else total.get(key, 0) + value
    return total


def _why_not(library, change_id):
    """why_not, on a read-only connection of `library`'s own."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return why_not(conn, change_id)
    finally:
        conn.close()


def undo_rows(library, change_id):
    """Undo change `change_id`'s rows at once, with no rehearsal: what face_people.undo_unnaming does to name a removal's
    faces again. Refusal (tagpup.store.journal.Refusal), nothing written, when why_not says so, or the journal does in its
    write. Returns what it wrote (tagpup.store.journal.Undone)."""
    why = _why_not(library, change_id)
    if why:
        raise journal.Refusal([why])
    return journal.undo(library.path, change_id)


def refusals(library, entries):
    """{id: why it cannot be undone now, or None} of each of `entries`, entries of
    `library`'s history ({"id", ...}): what the History dialog shows instead of an Undo
    button, why_not of each on one read-only connection -- the account the rehearsal
    and the undo refuse by, so a change offered is one they do not refuse out of hand."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return {entry["id"]: why_not(conn, entry["id"]) for entry in entries}
    finally:
        conn.close()


def rehearse(library, change_id, exiftool_path=None):
    """Undo change `change_id` and apply it again inside a transaction rolled back. A
    Result: `attempted` is the rows the undo would write; details["rehearsal"] says
    whether the round trip restored every row exactly. Refused, before a row is read,
    when why_not says so. A change of photo files is rehearsed by reading them
    (file_changes.rehearse_undo)."""
    if file_changes.writes_files(library, change_id):
        return file_changes.rehearse_undo(library, change_id, exiftool_path, why_not)
    result = Result(details={"dry_run": True, "change": change_id})
    why = _why_not(library, change_id)
    if why:
        result.refuse(why)
        return result
    rehearsal = journal.rehearse_undo(library.path, change_id)
    result.details["rehearsal"] = rehearsal.as_dict()
    if rehearsal.refused:
        result.refuse(rehearsal.refused)
    result.attempted = rehearsal.rows
    return result


def undo(library, change_id, apply=False, exiftool_path=None):
    """Undo change `change_id`: a rehearsal unless `apply`. Applied, a Result whose
    `changed` is the rows the undo wrote back; refused, naming the rows, the newer change
    or the schema in the way, with nothing written -- and when the rehearsal, which runs
    first, finds the round trip does not restore every row exactly. A change of photo
    files puts back every file still holding what it left, `changed` counting them, and
    names the rest in its errors (file_changes.undo)."""
    if file_changes.writes_files(library, change_id):
        result = file_changes.undo(library, change_id, exiftool_path, why_not, apply=apply)
        if apply and not result.refused and result.changed:
            _unname_what_was_taken_off(library, change_id, result)
        return result
    rehearsal = rehearse(library, change_id)
    if not apply or rehearsal.refused:
        return rehearsal
    rehearsed = rehearsal.details["rehearsal"]
    if not rehearsed["exact"]:
        rehearsal.details["dry_run"] = False
        rehearsal.refuse("Nothing was written: undoing it and applying it again did not restore every row"
                         " exactly: %s" % "; ".join(rehearsed["differences"]))
        return rehearsal
    result = Result(attempted=rehearsal.attempted, details={"dry_run": False, "change": change_id})
    try:
        undone = journal.undo(library.path, change_id)
    except journal.Refusal as e:
        result.refuse("Nothing was written: %s" % e)
        return result
    result.changed = undone.rows
    if not undone.settled:
        result.fail("the people and dates of the photos it touched",
                    "not rebuilt yet; they are, the next time the library is opened")
    return result


def _unname_what_was_taken_off(library, change_id, result):
    """The persons an undo of a change of photo files took off their photos (an add undone) are off their faces too (#908): the
    faces are unnamed, one journaled change of their own that History can undo. Said in the Result's details (`unnamed_faces`,
    `faces_change`); a failure is an error entry, the files are put back all the same."""
    try:
        done = face_people.follow_change(library, change_id, undone=True, undo=undo_rows)
    except Exception:
        logger.exception("Could not unname the faces of the people change %d took off", change_id)
        result.fail("the faces of the people taken off", "could not be unnamed; the server's log says why")
        return
    for what, why in done.errors:
        result.fail(what, why)
    if done.details.get("unnamed"):
        result.details["unnamed_faces"] = done.details["unnamed"]
    if done.details.get("renamed"):
        result.details["renamed_faces"] = done.details["renamed"]
    result.details["faces_changes"] = done.details.get("faces_changes", [])
    if done.details.get("faces_problem"):
        result.details["faces_problem"] = done.details["faces_problem"]
