"""One bulk edit of a library view's selection: read and checked up front, then written in one request (docs/ARCHITECTURE.md).

An EDIT is what is done to every photo of a selection (at most batches.MAX_BATCH, 5,000):

* `tags`: add some tags and take some off, `{"add": [...], "remove": [...]}`;
* `people`: add and take off people, `{"add": [...], "remove": [...], "add_ids": [...], "remove_ids": [...]}`: a person_id is the
  one person (their node's tag; an id that is nobody's is refused, naming nothing, and writes nothing), a name is filed as a
  click on a person's chip files it (`vocabulary.person_tag`, the one rule; a name two people have is refused, naming their paths), a person a
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
from tagpup.services import batches, file_changes, selection, tagging
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

    def reply(self):
        """The numbers and the first MOST_ERRORS errors, for the page."""
        return {"changed": self.changed, "unchanged": self.unchanged, "skipped_missing": self.skipped_missing,
                "error_count": len(self.errors),
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
    result = tagging.change_tags(
        library, chosen, edit.add, edit.remove, exiftool_path, operation=OPERATIONS[edit.op], stop_at_first_error=False,
        persons={paths.key(path): set(edit.persons) for path in chosen} if edit.persons else None)
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
        # Where each file is (file_changes.file_states): one on a share that did not answer is its error, one gone missing.
        states = dict((paths.key(path), state) for path, state in file_changes.file_states([path for _id, path in present]))
        there = [(photo_id, path) for photo_id, path in present if states[paths.key(path)] == file_changes.PRESENT]
        out.errors.extend((photo_id, os.path.basename(path)[:MOST_TEXT], file_changes.AWAY_WHY)
                          for photo_id, path in present if states[paths.key(path)] == file_changes.AWAY)
        out.skipped_missing += sum(1 for _id, path in present if states[paths.key(path)] == file_changes.GONE)
        refused = _cannot_keep(there)
        batch = batches.current()
        for n, (photo_id, path) in enumerate(there):
            if n and n % batches.CHUNK == 0 and not file_changes.between_chunks(batch):
                out.errors.extend((left_id, os.path.basename(left)[:MOST_TEXT], "stopped as asked: not deleted")
                                  for left_id, left in there[n:])
                break
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


def _count(out, result, present, by_key):
    """Count a Result of one chunk into `out`, whose errors so far are none (a photo on a share away is the write's error)."""
    away = len(out.errors)
    out.changed = result.changed
    gone = result.details.get(tagging.SKIPPED_MISSING, 0)
    out.skipped_missing += gone
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
    out.unchanged = max(0, len(present) - out.changed - gone - (len(out.errors) - away))
