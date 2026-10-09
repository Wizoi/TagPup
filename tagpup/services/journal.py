"""A library's journal, as the CLI and the MCP server use it: what was changed, undoing a
change, and letting old changes go (tagpup.store.journal; docs/ARCHITECTURE.md, phase
7.5).

An undo is a dry run unless told to apply, as every bulk operation is: the dry run is
the rehearsal -- the undo and the change again inside a transaction rolled back -- and
says whether that restored every row exactly, or why the undo would be refused. What a
change holds can name people, so its values are shown only when asked (`reveal`): the
library is photographs of real people, many of them minors.

A stamp of a library's settings (tagpup.services.settings.STAMPS) is never undone: it
is the library's first settings, and undone the library held none -- the next read
stamped it again, from config.ini if one was still there.

A change of photo files (tagpup.services.file_changes) is undone file by file, and its
rows follow: a file that no longer holds what the change left is refused, named, and the
rest are put back. Its rehearsal reads every file and writes none. Undoing one needs the
library's ExifTool, which the caller names.
"""
import logging

from tagpup.core.result import NotFound, Result
from tagpup.services import bulk_edit, face_people, file_changes, folder_moves
from tagpup.services import settings as library_settings
from tagpup.store import journal

logger = logging.getLogger(__name__)

#: How long a change stays undoable, in days; then pruning takes its values away.
RETENTION_DAYS = journal.RETENTION_DAYS


def history(library, change_id=None, reveal=False, limit=20):
    """The library's changes, newest first, up to `limit`; or change `change_id` alone,
    with the keys of every row it wrote, and with `reveal` each column's values before
    and after (a BLOB by its size). NotFound for a change the library has not."""
    entries = journal.history(library.path, limit=limit, change_id=change_id, values=reveal)
    if not reveal:
        # A folder is a path, and paths can name people: the summary keeps it for `reveal` only.
        for entry in entries:
            entry["summary"] = {key: value for key, value in entry["summary"].items() if key != "folder"}
    if change_id is not None and not entries:
        raise NotFound("There is no change %d in the library %s." % (change_id, library.name))
    return {"changes": entries, "retention_days": RETENTION_DAYS}


def _stamp_refusal(operation):
    """Why a change made as `operation` is never undone: a stamp of the library's first
    settings (NOT_UNDONE). None for any other."""
    return library_settings.NOT_UNDONE if operation in library_settings.STAMPS else None


def refusals(library, entries):
    """{id: why it cannot be undone now, or None} of each of `entries`, entries of
    `library`'s history ({"id", "operation"}): what the History dialog shows instead of
    an Undo button. The account the rehearsal refuses by -- a stamp of the settings
    (_stamp_refusal), then the journal's (tagpup.store.journal.refusal: status, schema
    version, a newer change of the same rows or files) -- so a change offered is one the
    rehearsal does not refuse out of hand. Reads only."""
    reasons = {entry["id"]: _stamp_refusal(entry["operation"]) for entry in entries}
    found = journal.refusals(library.path, [cid for cid, why in reasons.items() if why is None])
    for change_id, why in found.items():
        reasons[change_id] = "; ".join(why) or None
    return reasons


def rehearse(library, change_id, exiftool_path=None):
    """Undo change `change_id` and apply it again inside a transaction rolled back. A
    Result: `attempted` is the rows the undo would write; details["rehearsal"] says
    whether the round trip restored every row exactly. Refused when the undo would be,
    and for a stamp of the library's settings (NOT_UNDONE), which undo() refuses too.
    A change of photo files is rehearsed by reading them (file_changes.rehearse_undo)."""
    if file_changes.writes_files(library, change_id):
        return file_changes.rehearse_undo(library, change_id, exiftool_path)
    result = Result(details={"dry_run": True, "change": change_id})
    stamped = _stamp_refusal(journal.operation(library.path, change_id))
    if stamped:
        result.refuse(stamped)
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
        result = file_changes.undo(library, change_id, exiftool_path, apply=apply)
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
    if journal.operation(library.path, change_id) in folder_moves.OPERATIONS:
        # The folders added are a record the journal does not hold: they follow the rows back.
        try:
            result.details["added_followed_back"] = folder_moves.undone(library, change_id)
        except Exception as e:
            # The undo is written; only the record of the folders added was not pointed back.
            result.details["added_followed_back"] = None
            result.fail("the folders added", "%s: %s (the rows and settings are back; the added folders were not "
                        "pointed back)" % (type(e).__name__, e))
    if not undone.settled:
        result.fail("the people and dates of the photos it touched",
                    "not rebuilt yet; they are, the next time the library is opened")
    return result


def _unname_what_was_taken_off(library, change_id, result):
    """The persons an undo of a change of photo files took off their photos (an add undone) are off their faces too (#908): the
    faces are unnamed, one journaled change of their own that History can undo. Said in the Result's details (`unnamed_faces`,
    `faces_change`); a failure is an error entry, the files are put back all the same."""
    try:
        done = face_people.follow_change(library, change_id, undone=True)
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


#: The line a prune says of the changes it leaves.
KEPT_SAYS = "kept for a resumable bulk job: %d"


def kept_operations(library):
    """The operations whose changes no prune takes: those of the bulk time shifts the library holds a record of (the list of
    photos and the state a resume reads, kept 30 days from the job's last activity, and while the job runs). Their journal changes
    are the only account of which photos of the chunk in flight were shifted, and a resume that cannot read it could shift them
    twice. Once the record goes (the job is done, or too old) its changes are pruned like any other."""
    return {bulk_edit.operation_of(bulk_edit.TIME_SHIFT, job) for job in bulk_edit.resumable_heads(library)}


def prune(library, days=RETENTION_DAYS, apply=False):
    """Take away the values of every change older than `days`, keeping its summary; it
    can no longer be undone. A dry run unless `apply`: `attempted` is the changes it
    would prune, details["values"] the column values it would delete, details["kept"] the
    changes it leaves for a bulk time shift that can be resumed (kept_operations) and
    details["kept_jobs"] those jobs' ids: this is said in `note`, the line a caller prints."""
    keep = kept_operations(library)
    changes, values = journal.prunable(library.path, days, keep=keep)
    held = journal.held_back(library.path, days, keep=keep) if keep else {}
    kept = sum(held.values())
    result = Result(attempted=changes, details={"dry_run": not apply, "days": days, "values": values, "kept": kept,
                                                "kept_jobs": sorted(job for job in bulk_edit.resumable_heads(library)
                                                                    if bulk_edit.operation_of(bulk_edit.TIME_SHIFT, job) in held)})
    result.details["note"] = KEPT_SAYS % kept if kept else None
    if apply and changes:
        pruned, deleted = journal.prune(library.path, days, keep=lambda: kept_operations(library))
        result.changed = pruned
        result.details["values"] = deleted
    return result
