"""A library's journal, as the CLI and the MCP server use it: what was changed, and undoing a
change (tagpup.store.journal; docs/ARCHITECTURE.md, phase
7.5).

An undo is a dry run unless told to apply, as every bulk operation is: the dry run is
the rehearsal -- the undo and the change again inside a transaction rolled back -- and
says whether that restored every row exactly, or why the undo would be refused. What a
change holds can name people, so its values are shown only when asked (`reveal`): the
library is photographs of real people, many of them minors.

Whether a change may be undone now has one owner: why_not, which asks UNDO_CHECKS -- every kind of change the code
makes, by its operation's name, with what its way back says of itself -- and the journal's own account (status, schema
version, a newer change of the same rows or files: tagpup.store.journal.refusal). The History dialog lists by it, and the
rehearsal and the undo refuse by it before anything is written: nothing in tagpup/ but this module calls the store's undo
(tests/test_every_change_kind_has_an_undo_check.py). A switch or a retiring landing between the rehearsal and the
undo's write is not asked again (owner, 2026-10-23: accepted). A stamp of a library's settings
(tagpup.services.settings.STAMPS) is never undone: it is the library's first settings, and undone the library held
none -- the next read stamped it again; nor is a migration. A change whose undo would put back a face model the library
may not be switched to now is refused by the switch's own check (tagpup.services.settings.undo_check), a face pass's
change while a retiring of the old detector's faces after it stands, and that retiring while a pass after it made faces
in its photos (tagpup.services.retire_faces' checks).

A change of photo files (tagpup.services.file_changes) is undone file by file, and its
rows follow: a file that no longer holds what the change left is refused, named, and the
rest are put back. Its rehearsal reads every file and writes none. Undoing one needs the
library's ExifTool, which the caller names.
"""
import logging
import re

from tagpup.core.result import NotFound, Result
from tagpup.services import (
    bulk_edit,
    face_batch,
    face_pass,
    face_people,
    file_changes,
    retire_faces,
    sync,
    tagging,
    tags_from_faces,
)
from tagpup.services import settings as library_settings
from tagpup.store import db, journal

logger = logging.getLogger(__name__)

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
    face_pass.OPERATION: retire_faces.pass_check,
    retire_faces.OPERATION: retire_faces.retiring_check,
    retire_faces.VECTORS_OPERATION: retire_faces.vectors_check,
    face_people.UNNAMED: None,
    face_people.ADDED: None,
    face_people.REMOVED: None,
    journal.PERSON_DELETED: None,
    journal.PERSON_MERGED: None,
    tagging.ADD_TO_ALL: None,
    tags_from_faces.OPERATION: None,
    sync.OPERATION: None,
    "save photo": None,
    "apply all suggestions": None,
    "rename tag": None,
    "remove tag": None,
    "rename after caption": None,
    "smart rename": None,
    "smart rename: original names": None,
    "time shift": None,
    "rotate": None,
    "refresh_rows": None,
    "dedupe_faces": None,
    "faces_from_tags": None,
    "merge_duplicate_person_tags": None,
}
UNDO_CHECKS.update({operation: None for operation in bulk_edit.OPERATIONS.values()})
UNDO_CHECKS.update({operation: None for operation in face_batch.DESCRIPTIONS.values()})

#: Kinds of change no code makes any more, which the live journals hold (counted read-only 2026-10-23: photo_index 3,247
#: changes, kr-track 341, renton_parkrun 1,220): what their way back says of itself, as UNDO_CHECKS.
RETIRED = {
    # A stamp of the settings read from config.ini: photo_index 1, kr-track 1.
    **{stamp: library_settings.NOT_UNDONE for stamp in library_settings.RETIRED_STAMPS},
    # The roots' adoption of a folder and repair of a root's address: 1 adopt in each library, 1 repair in renton_parkrun.
    "roots adopt: %s": None,
    "roots repair-address: %s": None,
    # The relink of renamed photos' rows: renton_parkrun 1.
    "relink_renamed_photos": None,
    # A bulk edit run as a job, before bulk edits were named by their edit: renton_parkrun 1 each.
    "bulk people (job %d)": None,
    "bulk tags (job %d)": None,
}
UNDO_CHECKS.update(RETIRED)


def _pattern(name):
    """The regular expression an UNDO_CHECKS key matches an operation by: the key whole, a %s any text, a %d digits."""
    return re.compile("".join(".+" if part == "%s" else r"\d+" if part == "%d" else re.escape(part)
                              for part in re.split(r"(%[sd])", name)) + r"\Z", re.S)


_PATTERNED = [(_pattern(name), rule) for name, rule in UNDO_CHECKS.items() if "%" in name]


def undo_rule(operation):
    """What UNDO_CHECKS says of the way back of a change made as `operation`: a sentence, a check, or None (also for a
    name it does not hold)."""
    if operation in UNDO_CHECKS and "%" not in operation:
        return UNDO_CHECKS[operation]
    for pattern, rule in _PATTERNED:
        if pattern.match(operation):
            return rule
    return None


def why_not(conn, change_id):
    """Why change `change_id` cannot be undone now, or None when it may be tried: THE one account (#1805), in order -- a
    kind never undone (UNDO_CHECKS' sentence), the journal's (tagpup.store.journal.refusal: no such change, its status, its
    schema version, a newer change of the same rows or files), then the kind's own check (UNDO_CHECKS). The caller's
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
        # A folder is a path, and paths can name people: the summary keeps it for `reveal` only.
        for entry in entries:
            entry["summary"] = {key: value for key, value in entry["summary"].items() if key != "folder"}
    if change_id is not None and not entries:
        raise NotFound("There is no change %d in the library %s." % (change_id, library.name))
    return {"changes": entries}


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
