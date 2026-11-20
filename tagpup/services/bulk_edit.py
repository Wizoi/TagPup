"""One bulk edit of a library view's selection: read and checked up front, then written in one request (docs/ARCHITECTURE.md).

An EDIT is what is done to every photo of a selection (at most batches.MAX_BATCH, 5,000):

* `tags`: add some tags and take some off, `{"add": [...], "remove": [...]}`;
* `people`: add and take off people, `{"add": [...], "remove": [...], "add_ids": [...], "remove_ids": [...]}`: a person_id is the
  one person (their node's tag; an id that is nobody's is refused, naming nothing, and writes nothing), a name is filed as a
  click on a person's chip files it (`vocabulary.person_tag`, the one rule; a name two people have is refused, naming their paths), a person a
  file already names (by the id of their node: two people called alike are two) is not added again, and no node of the tag
  tree is made;
* `delete`: send each photo's file to the Recycle Bin and forget its row, faces and thumbnail, as the folder view's Delete
  does it (tagpup.services.photos.delete_in_chunks, the one owner: the whole selection's Delete journaled as ONE INTERNAL
  crash record, file_journal.DELETE, that a settle finishes and History never lists; a photo whose place has no Recycle
  Bin goes through this PC's, #694), `{"token": text}` -- the token of the photos the question named
  (selection.where_deleted: the request is refused when the selection no longer resolves to exactly them, #691).

`prepare` reads and checks one up front and refuses with a sentence, before a photo is touched. `run` does it to the photos,
synchronously: each id is resolved to its row's path NOW (a photo deleted since is skipped and counted), and the whole of it is
read, planned and written under the one lock of changes of photo files, through the one entry point of a write of photo files
(tagpup.services.photo_writes, op `tags`; the machinery a page's tag write uses, tagging.change_tags_of): the held photos as ONE
journaled change named for the edit ("bulk tags"), the photos of folders the
library does not hold to their files only, each file read again before its write and a file another program changed meanwhile
reported as a conflict and never overwritten. An edit never replaces a list: it adds and removes against each file's OWN tags.

**Interrupted.** The request runs to its end on the server whether or not the page waits: a closed tab or a lost connection
changes nothing. The change is planned in the journal before the first file is written and each file is recorded as it is
written, so a process that dies part-way leaves the files done and the rest planned, which the next change of photo files
settles (file_changes.settle); History lists what was written. Nothing is resumed: an edit made again changes nothing a photo
already holds, and a delete asks its question afresh -- but for a delete an update stopped, whose photos not reached are
deleted when TagPup starts again (tagpup.services.photos.settle_delete), each photo told so.

**One at a time** in this process, whichever library: a second edit waits its turn (tagpup.services.batches: at most eight wait, one
more is a Conflict saying how many are ahead), and the lock of changes of photo files is let go between chunks. Another process's
writes meet the stamp check: a file changed between the read and the write is a conflict, not overwritten.

What `run` reports is what CHANGED (a file written and read back), not what was attempted.
"""
import logging
import os
from dataclasses import dataclass, field

from tagpup.core import paths, validation, vocabulary
from tagpup.core.result import Conflict, Refused
from tagpup.files import recycle_bin
from tagpup.services import batches, file_changes, photo_writes, selection, tagging
from tagpup.services import people as people_service
from tagpup.services import photos as photo_actions
from tagpup.store import taxonomy

logger = logging.getLogger(__name__)

TAGS, PEOPLE, DELETE = "tags", "people", "delete"
OPS = (TAGS, PEOPLE, DELETE)

#: The most tags, or people, one edit adds, and the same of what it takes off.
MOST_NAMED = 200

#: The longest a photo's name or a reason is kept, in characters.
MOST_TEXT = 200

#: Errors a reply names (file and why); the rest are counted.
MOST_ERRORS = 50

#: What the journal calls the change of an edit (History's `operation`).
OPERATIONS = {TAGS: "bulk tags", PEOPLE: "bulk people", DELETE: "bulk delete"}


@dataclass
class Edit:
    """What is done to each photo. `add` and `remove` are tags as they are written (a person's already filed as a tag),
    `persons` the tags of `add` that are people; a delete's `token` (of the photos its question named), `for_good` (its
    question warned that some would be deleted for good, and the owner confirmed it) and `for_good_ids` (those files, asked
    again at the start, the token then of them: refuse_if_changed; no other file is deleted for good)."""
    op: str
    add: list = field(default_factory=list)
    remove: list = field(default_factory=list)
    persons: list = field(default_factory=list)
    token: str = ""
    for_good: bool = False
    for_good_ids: frozenset = frozenset()


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
    nothing in it, nothing to do, a tag both added and removed. What is
    taken off is not checked: a bad tag must stay removable."""
    if op not in OPS:
        raise Refused("op must be one of %s." % ", ".join(OPS))
    if not isinstance(params, dict):
        raise Refused("params must be an object.")
    if op == DELETE:
        token = params.get("token")
        if not isinstance(token, str) or not token:
            raise Refused("A delete names the photos its question was about (token, from delete-check): ask again.")
        return Edit(DELETE, token=token, for_good=params.get("for_good") is True)
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
        found = None if vocabulary.SEPARATOR in name else people_service.resolve(library, name)   # Refused: two people have it
        tag = found.tag if found else vocabulary.person_tag(name, [], roots)
        if tag is None:
            raise Refused("The tag tree has several people roots, so it is not known where to file %s: name the path." % name)
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
    (read, and holding what it was to hold), `skipped_missing` (no row, or its file is gone), or an entry of
    `errors` -- (id, file name, why)."""
    changed: int = 0
    unchanged: int = 0
    skipped_missing: int = 0
    errors: list = field(default_factory=list)
    #: Why the whole write was refused (a share holding a recorded-damaged photo did not answer, the roots changed), or None.
    refused: str = None
    #: Of `changed`, the files deleted for good, confirmed (a delete's Edit.for_good): the page says so, never "Recycle Bin".
    for_good: int = 0
    #: The write's own sentence when it stopped before its end -- asked to (Cancel, an update) or ExifTool stopped answering
    #: (file_changes: details "stopped" or "cancelled") -- else None; the page says it in place of an error a photo.
    stopped: str = None

    def reply(self):
        """The numbers and the first MOST_ERRORS errors, for the page."""
        return {"changed": self.changed, "unchanged": self.unchanged, "skipped_missing": self.skipped_missing,
                "error_count": len(self.errors), "stopped": self.stopped, "for_good": self.for_good,
                "errors": [{"id": photo_id, "name": str(name)[:MOST_TEXT], "why": str(why)[:MOST_TEXT]}
                           for photo_id, name, why in self.errors[:MOST_ERRORS]]}


def run(library, edit, ids, exiftool_path):
    """Do `edit` to the photos `ids` (resolved, in order, at most batches.MAX_BATCH) and say what became of each (Outcome). It waits
    its turn behind the batches ahead of it (batches.claim; Conflict, nothing done, when too many wait). An exception that escapes
    is not one photo's -- ExifTool that cannot start, a library that cannot be read; the photos written before it keep the edit,
    in the journal."""
    if not ids:
        raise Refused("The selection holds no photos.")
    with batches.claim(library, OPERATIONS[edit.op], len(ids)):
        return _write(library, edit, list(ids), exiftool_path)


def _write(library, edit, ids, exiftool_path):
    found = selection.paths_of(library, ids)
    present = [(photo_id, found[photo_id]) for photo_id in ids if photo_id in found]
    out = Outcome(skipped_missing=len(ids) - len(present))
    if not present:
        return out
    if edit.op == DELETE:
        return _delete(library, edit, present, out)
    by_key = {paths.key(path): photo_id for photo_id, path in present}
    chosen = [path for _photo_id, path in present]
    # Read, planned and written by file_changes.write_fields: one change, a chunk at a time, others let in between the chunks.
    # Named by the paths resolved just now and no id: the ids are the server's own of this request, not a page's record. No
    # lock held here: each chunk takes it, and a save that waits is let in between them (photo_writes' docstring).
    result = photo_writes.write(
        library, photo_writes.TAGS, {path: (None, {"add": edit.add, "remove": edit.remove}) for path in chosen},
        exiftool_path, None, stop_at_first_error=False, operation=OPERATIONS[edit.op],
        persons={paths.key(path): set(edit.persons) for path in chosen} if edit.persons else None)
    _count(out, result, present, by_key)
    return out


def _cannot_keep(present, sizes, confirmed):
    """{photo id: why} of the photos that go through this PC, when this PC cannot keep their copies; {} when it can. `sizes`,
    {paths.key(path): bytes}, is what file_changes.file_stamps found; a file whose size it did not find is judged by the
    delete's own read of it. A photo of `confirmed` (edit.for_good_ids) is left to its own delete: it still goes to the Bin
    while the Bin has room for it, and is deleted for good after that (for_good is leave, never force)."""
    bins, through, largest = {}, [], 0
    for photo_id, path in present:
        if photo_id in confirmed:
            continue
        folder = paths.key(os.path.dirname(path))
        if folder not in bins:
            bins[folder] = recycle_bin.no_bin_reason(path)
        if bins[folder]:
            size = sizes.get(paths.key(path))
            # A clip over recycle_bin.MOST_CLIP_BYTES makes no copy: it is deleted for good where it is, or refused.
            if size is None or recycle_bin.too_large_to_copy(path, size):
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

#: What it says when the photos are the same but not the files that would be deleted for good: a drive's Recycle Bin set
#: otherwise, or a file's size changed, since the question.
FOR_GOOD_CHANGED = ("The files that would be deleted for good are not the ones you were warned of (how full a Recycle Bin "
                    "is, its setting, or a file's size changed since you were asked): nothing was deleted. Ask again: click "
                    "Delete once more.")


def refuse_if_changed(edit, photo_ids, token_of, deleted_for_good):
    """A delete is started only on exactly the photos its question named: Conflict (CHANGED), nothing begun, when `photo_ids`
    (the selection resolved at the start) is not the set whose token the question carried -- nor, when the start confirmed a
    delete for good, the files `deleted_for_good(photo_ids)` says it would delete for good now (a Bin set smaller since), which
    are then the edit's for_good_ids; the photos the same and those files not, Conflict (FOR_GOOD_CHANGED). `token_of` and
    `deleted_for_good` are tagpup.services.selection's, the one hash of both and the one answer of which files a Bin would not
    keep."""
    if edit.op != DELETE:
        return
    confirmed = frozenset(deleted_for_good(photo_ids)) if edit.for_good else frozenset()
    if token_of(photo_ids, confirmed) != edit.token:
        same_photos = edit.token.startswith(token_of(photo_ids))
        raise Conflict(FOR_GOOD_CHANGED if same_photos else CHANGED)
    edit.for_good_ids = confirmed


def _delete(library, edit, present, out):
    """Delete the photos `present` ([(id, path)], reachable) as Organize's Delete does each (photos.delete: the file to the
    Recycle Bin -- through this PC's where its place has none, #694 -- then its row, faces and thumbnail), under the one lock of
    changes of photo files, so that no write of one of them runs while it goes. A file gone already is counted missing and its
    row kept (sync reports it); one refused, or not moved (any step of going through this PC that failed), is an error, the
    original and its row kept. Before it copies anything through this PC, it asks once, for the whole delete, whether this PC can
    keep the copies of the photos not confirmed for good (recycle_bin.can_copy_here: the Bin filled since the question, #703);
    if not, those photos are errors with the sentence and nothing of them is touched; the others go on. One confirmed for good
    goes through this PC while this PC's Bin keeps its copy, and for good after that (recycle_bin.recycle_through_this_pc). A clip goes as a photo does, up to recycle_bin.MOST_CLIP_BYTES;
    a larger one is deleted for good when it was among the files confirmed (edit.for_good_ids), its size counted in no copy.
    The photos that go are ONE change, carried out a chunk at a time, each with the look this start took of it
    (photos.delete_in_chunks: a stop between chunks, the rest not deleted, or, an update's, deleted at the next start)."""
    with file_changes.exclusively():
        # Where each file is, and its size, by one bounded stat of each (file_changes.file_stamps): one on a share that did
        # not answer is its error, one gone missing.
        stamps = file_changes.file_stamps([path for _id, path in present])
        states = {paths.key(path): state for path, state, _stamp in stamps}
        sizes = {paths.key(path): stamp[1] for path, _state, stamp in stamps if stamp is not None}
        # Each Delete's own look: this one; a file there that could not be read has none, and is its own error (delete_planned).
        looks = {paths.key(path): stamp for path, _state, stamp in stamps if stamp is not None}
        there = [(photo_id, path) for photo_id, path in present if states[paths.key(path)] == file_changes.PRESENT]
        out.errors.extend((photo_id, os.path.basename(path)[:MOST_TEXT], file_changes.AWAY_WHY)
                          for photo_id, path in present if states[paths.key(path)] == file_changes.AWAY)
        out.skipped_missing += sum(1 for _id, path in present if states[paths.key(path)] == file_changes.GONE)
        refused = _cannot_keep(there, sizes, edit.for_good_ids)
        out.errors.extend((photo_id, os.path.basename(path)[:MOST_TEXT], refused[photo_id][:MOST_TEXT])
                          for photo_id, path in there if photo_id in refused)
        going = [(photo_id, path, photo_id in edit.for_good_ids, looks.get(paths.key(path)))
                 for photo_id, path in there if photo_id not in refused]
        if going:
            # Deleted for good only if it was among the files confirmed: one grown past its Bin since is refused.
            photo_actions.delete_in_chunks(library, going, lambda row, result: _deleted(out, row, result))
    return out


def _deleted(out, row, result):
    """Count the Delete of the photo of journal row `row` (its photo_id the selection's id), told by `result`, into `out`."""
    if result.ok and result.changed:
        out.changed += 1
        out.for_good += 1 if result.details.get("for_good") else 0
        return
    why = result.refused or result.message() or "it could not be deleted"
    out.errors.append((row.photo_id, os.path.basename(row.path)[:MOST_TEXT], str(why)[:MOST_TEXT]))


def _count(out, result, present, by_key):
    """Count a Result of one chunk into `out`, whose errors so far are none (a photo on a share away is the write's error)."""
    away = len(out.errors)
    out.changed = result.changed
    gone = result.details.get(tagging.SKIPPED_MISSING, 0)
    out.skipped_missing += gone
    if result.refused:
        out.refused = result.refused
        if result.details.get("stopped") or result.details.get("cancelled"):
            out.stopped = result.refused
        written = {paths.key(path) for path in (result.details.get("written") or {})}
        left_out = {paths.key(what) for what, _why in result.skipped}
        for photo_id, path in present:
            if paths.key(path) not in written and paths.key(path) not in left_out:
                out.errors.append((photo_id, os.path.basename(path)[:MOST_TEXT], result.refused[:MOST_TEXT]))
    else:
        for what, why in result.errors:
            photo_id = by_key.get(paths.key(what))
            out.errors.append((photo_id, os.path.basename(what)[:MOST_TEXT], str(why)[:MOST_TEXT]))
    out.unchanged = max(0, len(present) - out.changed - gone - (len(out.errors) - away))
