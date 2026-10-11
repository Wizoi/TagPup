"""One bulk edit of a library view's selection: read and checked up front, then written in one request (docs/ARCHITECTURE.md).

An EDIT is what is done to every photo of a selection (at most selection.MAX_SELECTED, 5,000):

* `tags`: add some tags and take some off, `{"add": [...], "remove": [...]}`;
* `people`: add and take off people, `{"add": [...], "remove": [...], "add_ids": [...], "remove_ids": [...]}`: a person_id is the
  one person (their node's tag; an id that is nobody's is refused, naming nothing, and writes nothing), a name is filed as a
  click on a person's chip files it (`vocabulary.person_tag`, the one rule; a name two people have is refused), a person a
  file already names (by the id of their node: two people called alike are two) is not added again, and no node of the tag
  tree is made;
* `delete`: send each photo's file to the Recycle Bin and forget its row, faces and thumbnail, as the folder view's Delete
  does it (tagpup.services.photos.delete, the one owner: not journaled, as that is not; a photo whose place has no Recycle Bin
  goes through this PC's, #694), `{"token": text}` -- the token of the photos the question named (selection.where_deleted: the
  request is refused when the selection no longer resolves to exactly them, #691).

`prepare` reads and checks one up front and refuses with a sentence, before a photo is touched. `run` does it to the photos,
synchronously: each id is resolved to its row's path NOW (a photo deleted since is skipped and counted), and the whole of it is
read, planned and written under the one lock of changes of photo files, by the machinery a single save and Add to all selected
use (tagging.change_tags): the held photos as ONE journaled change named for the edit ("bulk tags"), the photos of folders the
library does not hold to their files only, each file read again before its write and a file another program changed meanwhile
reported as a conflict and never overwritten. An edit never replaces a list: it adds and removes against each file's OWN tags.

**Interrupted.** The request runs to its end on the server whether or not the page waits: a closed tab or a lost connection
changes nothing. The change is planned in the journal before the first file is written and each file is recorded as it is
written, so a process that dies part-way leaves the files done and the rest planned, which the next change of photo files
settles (file_changes.settle); History lists what was written. Nothing is resumed: an edit made again changes nothing a photo
already holds, and a delete asks its question afresh.

**One at a time** in a library, in this process: a second request is a Conflict naming the first (the lock of changes of photo
files would only make it wait, minutes). Another process's writes meet the stamp check: a file changed between the read and the
write is a conflict, not overwritten.

What `run` reports is what CHANGED (a file written and read back), not what was attempted.
"""
import logging
import os
import threading
from dataclasses import dataclass, field

from tagpup.core import paths, validation, vocabulary
from tagpup.core.result import Conflict, Refused
from tagpup.files import exiftool_session, recycle_bin, shares
from tagpup.services import file_changes, libraries, library_view, tagging
from tagpup.services import people as people_service
from tagpup.services import photos as photo_actions
from tagpup.store import file_journal, taxonomy
from tagpup.store import library_view as store

logger = logging.getLogger(__name__)

TAGS, PEOPLE, DELETE = "tags", "people", "delete"
OPS = (TAGS, PEOPLE, DELETE)

#: The most tags, or people, one edit adds, and the same of what it takes off.
MOST_NAMED = 200

#: How long one ExifTool command may take, in seconds (the session's own default). One command reads up to 200 photos, scanning
#: each to its end; a share that went away mid-run costs this once: the session then stays dead (ChunkSession) and every file
#: after is an error at once.
COMMAND_TIMEOUT = exiftool_session.DEFAULT_TIMEOUT

#: The longest a photo's name or a reason is kept, in characters.
MOST_TEXT = 200

#: Errors a reply names (file and why); the rest are counted.
MOST_ERRORS = 50

#: What the journal calls the change of an edit (History's `operation`).
OPERATIONS = {TAGS: "bulk tags", PEOPLE: "bulk people", DELETE: "bulk delete"}


@dataclass
class Edit:
    """What is done to each photo. `add` and `remove` are tags as they are written (a person's already filed as a tag),
    `persons` the tags of `add` that are people; a delete's `token` (of the photos its question named)."""
    op: str
    add: list = field(default_factory=list)
    remove: list = field(default_factory=list)
    persons: list = field(default_factory=list)
    token: str = ""


# ---- Reading one -----------------------------------------------------------------------------

def _list_of(params, key):
    found = params.get(key, [])
    if not isinstance(found, list) or not all(isinstance(each, str) for each in found):
        raise Refused("%s must be a list of text." % key)
    if len(found) > MOST_NAMED:
        raise Refused("%s names %d; at most %d at a time." % (key, len(found), MOST_NAMED))
    return found


def _ids_of(params, key):
    found = params.get(key, [])
    if not isinstance(found, list) or not all(type(each) is int for each in found):
        raise Refused("%s must be a list of whole numbers." % key)
    if len(found) > MOST_NAMED:
        raise Refused("%s names %d; at most %d at a time." % (key, len(found), MOST_NAMED))
    return found


def prepare(library, op, params):
    """The Edit for `op` and `params` as a request sends them, or Refused with a sentence and nothing done: an unknown
    op, a list that is not text, a tag the rules forbid (tagpup.core.validation, as every write of a tag checks), a name with
    nothing in it, a person the tree files in two places (which one?), nothing to do, a tag both added and removed. What is
    taken off is not checked: a bad tag must stay removable."""
    if op not in OPS:
        raise Refused("op must be one of %s." % ", ".join(OPS))
    if not isinstance(params, dict):
        raise Refused("params must be an object.")
    if op == DELETE:
        token = params.get("token")
        if not isinstance(token, str) or not token:
            raise Refused("A delete names the photos its question was about (token, from delete-check): ask again.")
        return Edit(DELETE, token=token)
    add, remove = _list_of(params, "add"), _list_of(params, "remove")
    if op == TAGS:
        # The rules read the tag as typed ("A//B" has an empty level); what is written is its one spelling.
        problem = validation.first_problem("tag", add)
        if problem:
            raise Refused(problem)
        add = [vocabulary.normalize(each) for each in add]
        if not all(add) or not all(each.strip() for each in remove):
            raise Refused("A tag with nothing in it cannot be added or taken off.")
        # What is taken off is matched against the file's own tags as written, and also in the one spelling an added tag is
        # written in: "Trips / Coast" takes off "Trips/Coast" too.
        taken = [spelling for each in remove for spelling in (each, vocabulary.normalize(each))]
        edit = Edit(TAGS, add=list(dict.fromkeys(add)), remove=list(dict.fromkeys(taken)))
    else:
        edit = _people(library, add, remove, _ids_of(params, "add_ids"), _ids_of(params, "remove_ids"))
    if not edit.add and not edit.remove:
        raise Refused("There is nothing to add or to take off.")
    problem = validation.first_problem("tag", edit.add)
    if problem:
        raise Refused(problem)
    clash = {vocabulary.keyword_key(each) for each in edit.add} & {vocabulary.keyword_key(each) for each in edit.remove}
    if clash:
        raise Refused("A tag cannot be both added and taken off the same photos.")
    return edit


def _people(library, add, remove, add_ids=(), remove_ids=()):
    """The Edit of `people`: people by id (their node's tag) and names made tags by the one rule the chip and Apply All follow
    (`vocabulary.person_tag`), the tree only read. An id that is no person's (merged or deleted in another window) or a group's
    is refused before a photo is touched (people_service.resolve: NotFound, Refused)."""
    filed, roots = taxonomy.people_filing(library.path)
    tags, persons = [], []
    for person_id in add_ids:
        found = people_service.resolve(library, person_id)
        tags.append(found.tag)
        persons.append(found.tag)
    taken_by_id = [people_service.resolve(library, person_id).tag for person_id in remove_ids]
    for name in add:
        name = name.strip()
        if not name:
            raise Refused("A person's name cannot be empty.")
        tag = vocabulary.person_tag(name, filed.get(vocabulary.key(name), []), roots)
        if tag is None:
            raise Refused("%s is filed in more than one place in the tag tree (or the tree has several people roots), so "
                          "it is not known where to file them: name the path." % name)
        tags.append(tag)
        persons.append(tag)
    taken = list(taken_by_id)
    for name in remove:
        name = name.strip()
        if not name:
            raise Refused("A person's name cannot be empty.")
        # Every way the tree files this person, and the bare name Apply All wrote for one it could not place.
        taken.extend([name] if "/" in name else filed.get(vocabulary.key(name), []) + [name])
    return Edit(PEOPLE, add=list(dict.fromkeys(tags)), remove=list(dict.fromkeys(taken)), persons=list(dict.fromkeys(persons)))


# ---- Doing it ---------------------------------------------------------------------------------

@dataclass
class Outcome:
    """What an edit did, counted by outcome: every id is exactly one of `changed` (a file written and read back), `unchanged`
    (read, and holding what it was to hold), `skipped_missing` (no row, or its file is gone), `skipped_damaged`, or an entry of
    `errors` -- (id, file name, why)."""
    changed: int = 0
    unchanged: int = 0
    skipped_missing: int = 0
    skipped_damaged: int = 0
    errors: list = field(default_factory=list)
    #: Why the whole write was refused (a share holding a recorded-damaged photo did not answer, the roots changed), or None.
    refused: str = None

    def reply(self):
        """The numbers and the first MOST_ERRORS errors, for the page."""
        return {"changed": self.changed, "unchanged": self.unchanged, "skipped_missing": self.skipped_missing,
                "skipped_damaged": self.skipped_damaged, "error_count": len(self.errors),
                "errors": [{"id": photo_id, "name": str(name)[:MOST_TEXT], "why": str(why)[:MOST_TEXT]}
                           for photo_id, name, why in self.errors[:MOST_ERRORS]]}


class ChunkSession:
    """The ExifTool session of one edit, which stays dead once a command has timed out: pyexiftool starts its process again on
    the next command, and that command would wait its own deadline for a share that is not coming back. Every command after a
    timeout answers the same ExifToolTimeout at once, so the files after it are errors in the time it takes to say so."""

    def __init__(self, session):
        self._session = session
        self._dead = None

    def __getattr__(self, name):
        wanted = getattr(self._session, name)
        if not callable(wanted):
            return wanted

        def command(*args, **kwargs):
            if self._dead is not None:
                raise self._dead
            try:
                return wanted(*args, **kwargs)
            except exiftool_session.ExifToolTimeout as stalled:
                self._dead = stalled
                raise
        return command


_running = set()      # {library.key}: the libraries with an edit under way in this process
_guard = threading.Lock()


def running(library=None):
    """How many edits are under way in this process (the library's, or every one's)."""
    with _guard:
        return int(library.key in _running) if library is not None else len(_running)


def run(library, edit, ids, exiftool_path):
    """Do `edit` to the photos `ids` (resolved, in order, at most selection.MAX_SELECTED) and say what became of each (Outcome).
    Conflict, nothing done, when an edit is under way in the library. An exception that escapes is not one photo's -- ExifTool
    that cannot start, a library that cannot be read; the photos written before it keep the edit, in the journal."""
    if not ids:
        raise Refused("The selection holds no photos.")
    with _guard:
        if library.key in _running:
            raise Conflict("A bulk edit is already running in %s. Wait for it to finish." % library.name)
        _running.add(library.key)
    try:
        return _write(library, edit, list(ids), exiftool_path)
    finally:
        with _guard:
            _running.discard(library.key)


def _write(library, edit, ids, exiftool_path):
    conn = library_view.opened(library)
    try:
        found = store.paths_of(conn, ids)
    finally:
        conn.close()
    present = [(photo_id, found[photo_id]) for photo_id in ids if photo_id in found]
    out = Outcome(skipped_missing=len(ids) - len(present))
    present = _reachable(present, out)
    if not present:
        return out
    if edit.op == DELETE:
        return _delete(library, edit, present, out)
    by_key = {paths.key(path): photo_id for photo_id, path in present}
    chosen = [path for _photo_id, path in present]
    # The edit's own ExifTool session, with a deadline of its own and started before the lock is asked for. All of it then runs
    # under the one lock: the files are read, checked for being gone, planned and written without another change of photo files
    # between (the route of the folder view does the same around a bulk write).
    with exiftool_session.ExifToolSession(executable=exiftool_path, timeout=COMMAND_TIMEOUT) as session:
        et = ChunkSession(session)
        with file_changes.exclusively():
            result = tagging.change_tags(
                library, chosen, edit.add, edit.remove, exiftool_path, operation=OPERATIONS[edit.op], stop_at_first_error=False,
                persons={paths.key(path): set(edit.persons) for path in chosen} if edit.persons else None, et=et)
    _count(out, result, present, by_key)
    return out


def _cannot_keep(present):
    """{photo id: why} of the photos that go through this PC, when this PC cannot keep their copies; {} when it can."""
    bins, through, largest = {}, [], 0
    for photo_id, path in present:
        folder = paths.key(os.path.dirname(path))
        if folder not in bins:
            bins[folder] = recycle_bin.no_bin_reason(path)
        if bins[folder]:
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            through.append((photo_id, size))
            largest = max(largest, size)
    if not through:
        return {}
    why = recycle_bin.can_copy_here(sum(size for _id, size in through), largest)
    return {photo_id: why for photo_id, _size in through} if why else {}


#: What a delete's start says when the selection no longer resolves to the photos its question named (#691).
CHANGED = ("The selection changed since you were asked (photos came into it or left it): nothing was deleted. Ask again: "
           "click Delete once more.")


def refuse_if_changed(edit, photo_ids, token_of):
    """A delete is started only on exactly the photos its question named: Conflict (CHANGED), nothing begun, when `photo_ids`
    (the selection resolved at the start) is not the set whose token the question carried. `token_of` is
    tagpup.services.selection.token_of, the one hash of both."""
    if edit.op == DELETE and token_of(photo_ids) != edit.token:
        raise Conflict(CHANGED)


def _delete(library, edit, present, out):
    """Delete the photos `present` ([(id, path)], reachable) as Organize's Delete does each (photos.delete: the file to the
    Recycle Bin -- through this PC's where its place has none, #694 -- then its row, faces and thumbnail), under the one lock of
    changes of photo files, so that no write of one of them runs while it goes. A file gone already is counted missing and its
    row kept (sync reports it); one refused, or not moved (any step of going through this PC that failed), is an error, the
    original and its row kept. Before the chunk copies anything through this PC, it asks whether this PC can keep the chunk's
    copies (recycle_bin.can_copy_here: the Bin fills during a long job, #703); if not, those photos are errors with the
    sentence and nothing of them is touched; the others go on."""
    with file_changes.exclusively():
        there = [(photo_id, path) for photo_id, path in present if os.path.isfile(path)]
        out.skipped_missing += len(present) - len(there)
        refused = _cannot_keep(there)
        for photo_id, path in there:
            name = os.path.basename(path)[:MOST_TEXT]
            if photo_id in refused:
                out.errors.append((photo_id, name, refused[photo_id][:MOST_TEXT]))
                continue
            result = photo_actions.delete(library, path)
            if result.ok and result.changed:
                out.changed += 1
            else:
                why = result.refused or result.message() or "it could not be deleted"
                out.errors.append((photo_id, name, str(why)[:MOST_TEXT]))
    return out


#: What an error says of a photo on a network share that did not answer.
AWAY = "its folder could not be reached just now (a network share that did not answer), so nothing was written to it"


def _reachable(present, out):
    """`present` without the photos on a share that does not answer (each an error of `out`): a bounded stat of each file
    (shares.stamp_of: a share is asked within a second, and one found away is answered at once for 30 seconds, so a
    chunk of 25 waits once). Sent on, such a photo would cost ExifTool's whole deadline (five minutes) and then one more for each
    photo of the batch it is retried singly for. A file that is gone is no matter here (the write counts it missing) and one that
    cannot be read is the write's own error."""
    kept = []
    for photo_id, path in present:
        if shares.stamp_of(path) is shares.UNANSWERED:
            out.errors.append((photo_id, os.path.basename(path)[:MOST_TEXT], AWAY))
        else:
            kept.append((photo_id, path))
    return kept


def _count(out, result, present, by_key):
    """Count a Result of one chunk into `out`, whose errors so far are the photos left out as away."""
    away = len(out.errors)
    out.changed = result.changed
    gone = result.details.get(tagging.SKIPPED_MISSING, 0)
    damaged = result.details.get(libraries.SKIPPED_DAMAGED, 0)
    out.skipped_missing += gone
    out.skipped_damaged = damaged
    if result.refused:
        out.refused = result.refused
        written = {paths.key(path) for path in (result.details.get("written") or {})}
        left_out = {paths.key(what) for what, _why in result.skipped}
        for photo_id, path in present:
            if paths.key(path) not in written and paths.key(path) not in left_out:
                out.errors.append((photo_id, os.path.basename(path)[:MOST_TEXT], result.refused[:MOST_TEXT]))
    else:
        for what, why in result.errors:
            photo_id = by_key.get(paths.key(what))
            out.errors.append((photo_id, os.path.basename(what)[:MOST_TEXT], str(why)[:MOST_TEXT]))
    out.unchanged = max(0, len(present) - out.changed - gone - damaged - (len(out.errors) - away))


# ---- Whose run is it ---------------------------------------------------------------------------

def is_this_process(owner):
    """Is `owner` ('host:pid:start', as a run names its process) this process?"""
    return owner == file_journal.owner()


def process_alive(owner):
    """Is the process that owned a run -- 'host:pid:start' -- still going, and not this one? A run of this process that nothing
    in this process runs is a run that was lost (a test of a restart; a thread that died)."""
    return bool(owner) and owner != file_journal.owner() and file_journal.owner_alive(owner)
