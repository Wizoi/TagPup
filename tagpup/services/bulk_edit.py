"""One bulk edit, and the one chunk of it a job writes at a time (docs/ARCHITECTURE.md, phase 9d-1; the job is
tagpup.jobs.bulk_edits).

An EDIT is what is done to every photo of a selection:

* `tags`: add some tags and take some off, `{"add": [...], "remove": [...]}`;
* `people`: add and take off people, by name, `{"add": [...], "remove": [...]}`: a name is filed as a click on a person's chip
  files it (`vocabulary.person_tag`, the one rule), a person a file already names by their leaf is not added again, and no
  node of the tag tree is made;
* `time_shift`: move Date Taken by `{"minutes": n}` (tagpup.services.photos.date_shift_plan).

`prepare` reads and checks one up front and refuses with a sentence, before a photo is touched. `run_chunk` does it to a few
photos: each id is resolved to its row's path NOW (a photo renamed meanwhile is found by id, one deleted is skipped and
counted), and the whole chunk is read, planned and written under the one lock of changes of photo files, by the machinery a
single save and Add to all selected use: the held photos as one journaled change, the photos of folders the library does not
hold to their files only, each file read again before its write and a file another program changed meanwhile reported as a
conflict and never overwritten. An edit never replaces a list: it adds and removes against each file's OWN tags.

What a chunk reports is what CHANGED (a file written and read back), not what was attempted.
"""
import logging
import os
from dataclasses import dataclass, field

from tagpup.core import dates, paths, validation, vocabulary
from tagpup.core.result import Refused, Result
from tagpup.files import exiftool_session, job_files
from tagpup.services import damaged_photos, file_changes, file_only, libraries, library_view, tagging
from tagpup.services import photos as photo_actions
from tagpup.store import file_journal, taxonomy
from tagpup.store import library_view as store

logger = logging.getLogger(__name__)

TAGS, PEOPLE, TIME_SHIFT = "tags", "people", "time_shift"
OPS = (TAGS, PEOPLE, TIME_SHIFT)

#: The most tags, or people, one edit adds, and the same of what it takes off.
MOST_NAMED = 200

#: How long one ExifTool command of a chunk may take, in seconds. The session's own is 300: a share that went away in the middle
#: of a chunk would cost that, and a chunk is 25 photos. A chunk's worst case is about this, counted as errors.
CHUNK_TIMEOUT = 60

#: The longest a photo's name or a reason is kept, in characters (what a status and a state file carry of an error).
MOST_TEXT = 200

#: What the journal calls the changes of a job (History's `operation`): the work's own name, then the job, so that the
#: files a job wrote are found by it (tagpup.store.file_journal.photo_ids_done).
OPERATIONS = {TAGS: "bulk tags", PEOPLE: "bulk people", TIME_SHIFT: "bulk time shift"}


def operation_of(op, job):
    """The journal's name for the changes job `job` makes of kind `op`: exact, so a job's files are found by equality."""
    return "%s (job %d)" % (OPERATIONS[op], job)


@dataclass
class Edit:
    """What is done to each photo. `add` and `remove` are tags as they are written (a person's already filed as a tag),
    `persons` the tags of `add` that are people, `minutes` a time shift's."""
    op: str
    add: list = field(default_factory=list)
    remove: list = field(default_factory=list)
    persons: list = field(default_factory=list)
    minutes: int = 0

    def to_json(self):
        return {"op": self.op, "add": self.add, "remove": self.remove, "persons": self.persons, "minutes": self.minutes}

    @classmethod
    def from_json(cls, found):
        return cls(found["op"], list(found.get("add") or []), list(found.get("remove") or []),
                   list(found.get("persons") or []), int(found.get("minutes") or 0))

    def describe(self):
        """What it does, in words that name no tag or person (a record the Activity page lists, which can be read over a
        shoulder): "add 2 and remove 1 tag", "shift Date Taken by 90 minutes"."""
        if self.op == TIME_SHIFT:
            return "shift Date Taken by %d minute(s)" % self.minutes
        parts = []
        if self.add:
            parts.append("add %d" % len(self.add))
        if self.remove:
            parts.append("remove %d" % len(self.remove))
        return "%s %s" % (" and ".join(parts), "person(s)" if self.op == PEOPLE else "tag(s)")


# ---- Reading one -----------------------------------------------------------------------------

def _list_of(params, key):
    found = params.get(key, [])
    if not isinstance(found, list) or not all(isinstance(each, str) for each in found):
        raise Refused("%s must be a list of text." % key)
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
    if op == TIME_SHIFT:
        minutes = params.get("minutes")
        if type(minutes) is not int:
            raise Refused("A time shift is a whole number of minutes.")
        problem = validation.problem("time shift", minutes)
        if problem:
            raise Refused(problem)
        if minutes == 0:
            raise Refused("A shift of 0 minutes changes nothing.")
        return Edit(TIME_SHIFT, minutes=minutes)
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
        edit = _people(library, add, remove)
    if not edit.add and not edit.remove:
        raise Refused("There is nothing to add or to take off.")
    problem = validation.first_problem("tag", edit.add)
    if problem:
        raise Refused(problem)
    clash = {vocabulary.keyword_key(each) for each in edit.add} & {vocabulary.keyword_key(each) for each in edit.remove}
    if clash:
        raise Refused("A tag cannot be both added and taken off the same photos.")
    return edit


def _people(library, add, remove):
    """The Edit of `people`: names made tags by the one rule the chip and Apply All follow (`vocabulary.person_tag`), the tree
    only read."""
    filed, roots = taxonomy.people_filing(library.path)
    tags, persons = [], []
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
    taken = []
    for name in remove:
        name = name.strip()
        if not name:
            raise Refused("A person's name cannot be empty.")
        # Every way the tree files this person, and the bare name Apply All wrote for one it could not place.
        taken.extend([name] if "/" in name else filed.get(vocabulary.key(name), []) + [name])
    return Edit(PEOPLE, add=list(dict.fromkeys(tags)), remove=list(dict.fromkeys(taken)), persons=list(dict.fromkeys(persons)))


# ---- Doing one chunk of it ----------------------------------------------------------------------

@dataclass
class Outcome:
    """What one chunk did, counted by outcome: every id of the chunk is exactly one of `changed` (a file written and read
    back), `unchanged` (read, and holding what it was to hold, or with nothing to change: no Date Taken to shift),
    `skipped_missing` (no row, or its file is gone), `skipped_damaged`, or an entry of `errors` -- (id, file name, why)."""
    changed: int = 0
    unchanged: int = 0
    skipped_missing: int = 0
    skipped_damaged: int = 0
    errors: list = field(default_factory=list)
    #: Why the whole chunk was refused (a share holding a recorded-damaged photo did not answer, the roots changed), or None.
    refused: str = None

    @property
    def total(self):
        return self.changed + self.unchanged + self.skipped_missing + self.skipped_damaged + len(self.errors)


class ChunkSession:
    """The ExifTool session of one chunk, which stays dead once a command has timed out: pyexiftool starts its process again on
    the next command, and that command would wait its own deadline for a share that is not coming back. Every command after a
    timeout answers the same ExifToolTimeout at once, so the files after it are errors in the time it takes to say so."""

    def __init__(self, session):
        self._session = session
        self._dead = None

    @property
    def dead(self):
        """Did a command of this session time out (or its process die)? Then what it was doing to the file it was at is not
        known, and nor is what it did before it said so."""
        return self._dead is not None

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


class ChunkUndecided(Exception):
    """A command of the chunk stalled and the files could not then be read to see what it had done: which photos of the chunk
    were shifted is not known. The job stops with the chunk in flight in its record, for a resume to decide from the journal and
    the files, and shifts nothing over a guess."""


#: What an error says of a photo that is not shifted, decided after a stalled command by reading it.
NOT_SHIFTED = "ExifTool did not answer in time; read afterwards, the file was not shifted"

#: ... of one that cannot be told.
UNCONFIRMED = "could not confirm whether it was shifted: the file differs from both what it held and what the shift leaves"


def conflicts(library, operation):
    """The files of the changes named `operation` that the journal holds as conflicts: after a stalled command, a file that
    may nonetheless have been written."""
    return file_journal.files_of_operation(library.path, operation, "conflict")


def decide(library, rows, exiftool_path):
    """file_changes.reconcile over a session of its own -- the chunk's has timed out and stays dead -- with the chunk's deadline.
    (landed, not_written, elsewhere); ChunkUndecided when a file cannot be read to tell, or ExifTool cannot be had."""
    try:
        with exiftool_session.ExifToolSession(executable=exiftool_path, timeout=CHUNK_TIMEOUT) as fresh:
            landed, not_written, elsewhere, unknown = file_changes.reconcile(library, rows, ChunkSession(fresh))
    except Exception as problem:
        logger.warning("Could not read the files of a chunk after a stalled command: %s", problem)
        raise ChunkUndecided(type(problem).__name__) from None
    if unknown:
        raise ChunkUndecided("%d file(s) could not be read" % len(unknown))
    return landed, not_written, elsewhere


def _decided(library, result, exiftool_path):
    """After a stalled command in a time shift's chunk, read every file the chunk planned that is not recorded done and say what
    became of it: written after all (counted changed, its conflict row done), not written (an error saying so), or changed by
    something else (an error saying that). ChunkUndecided when it cannot be told."""
    change = result.details.get("change")
    rows = [row for row in file_journal.files_of(library.path, change) if row.state != "done"] if change else []
    if not rows:
        return
    landed, not_written, elsewhere = decide(library, rows, exiftool_path)
    landed_keys = {paths.key(row.path) for row in landed}
    said = {paths.key(row.path): NOT_SHIFTED for row in not_written}
    said.update({paths.key(row.path): UNCONFIRMED for row in elsewhere})
    result.errors = [(what, said.get(paths.key(what), why)) for what, why in result.errors if paths.key(what) not in landed_keys]
    result.changed += len(landed)


def run_chunk(library, edit, ids, exiftool_path, operation):
    """Do `edit` to the photos `ids` (a few: a job takes 25), and say what became of each (Outcome). An exception that
    escapes is not one photo's -- ExifTool that cannot start, a library that cannot be read -- and stops the job."""
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
    by_key = {paths.key(path): photo_id for photo_id, path in present}
    chosen = [path for _photo_id, path in present]
    # The chunk's own ExifTool, with a deadline of its own and started before the lock is asked for. The whole chunk then runs
    # under the one lock: the files are read, checked for being gone, planned and written without another change of photo files
    # between (the route of the folder view does the same around a bulk write). Left to write_fields, each write would start a
    # session with the default deadline of five minutes.
    with exiftool_session.ExifToolSession(executable=exiftool_path, timeout=CHUNK_TIMEOUT) as session:
        et = ChunkSession(session)
        with file_changes.exclusively():
            if edit.op == TIME_SHIFT:
                result = _shift(library, chosen, edit.minutes, exiftool_path, operation, et)
                if et.dead:
                    _decided(library, result, exiftool_path)
            else:
                result = tagging.change_tags(
                    library, chosen, edit.add, edit.remove, exiftool_path, operation=operation, stop_at_first_error=False,
                    persons={paths.key(path): set(edit.persons) for path in chosen} if edit.persons else None, et=et)
    _count(out, result, present, by_key)
    return out


#: What an error says of a photo on a network share that did not answer.
AWAY = "its folder could not be reached just now (a network share that did not answer), so nothing was written to it"


def _reachable(present, out):
    """`present` without the photos on a share that does not answer (each an error of `out`): a bounded stat of each file
    (damaged_photos.stamp_of: a share is asked within a second, and one found away is answered at once for 30 seconds, so a
    chunk of 25 waits once). Sent on, such a photo would cost ExifTool's whole deadline (five minutes) and then one more for each
    photo of the batch it is retried singly for. A file that is gone is no matter here (the write counts it missing) and one that
    cannot be read is the write's own error."""
    kept = []
    for photo_id, path in present:
        if damaged_photos.stamp_of(path) is damaged_photos.UNANSWERED:
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


def _shift(library, photo_paths, minutes, exiftool_path, operation, et=None):
    """photos.shift_date_taken for a bulk job's chunk: a photo whose file is gone, or found damaged, is left out and counted
    (the folder's shift refuses the whole batch for one damaged photo); one ExifTool cannot read is an error, not a skip;
    and no record of each photo is read back for a page, which nobody is waiting to receive. Nothing is caught here: an
    exception is the job's to stop at."""
    refused = Result(attempted=len(photo_paths))
    present, gone = tagging.leave_out_missing(photo_paths)
    held, loose = libraries.split(library, present)
    if held and libraries.refuse_writes(refused, library, held, damaged_ok=True):
        return refused
    kept, left = libraries.leave_out_damaged(refused, library, held) if held else ([], [])
    if kept is None:
        return refused
    writable, skipped = file_only.leave_out_unwritable(loose)
    plan_one = photo_actions.date_shift_plan(minutes, strict=True)
    journaled = file_changes.write_fields(library, operation, exiftool_path, kept, dates.SHIFTED_FIELDS, plan_one,
                                          summary={"photos": len(kept), "minutes": minutes}, et=et) if kept else None
    files = file_only.write_fields(exiftool_path, writable, dates.SHIFTED_FIELDS, plan_one, et=et) if writable else None
    result = libraries.with_skipped(file_only.combined(journaled, files), left + skipped)
    result.attempted += len(gone)
    for what, why in gone:
        result.skip(what, why)
    result.details[tagging.SKIPPED_MISSING] = len(gone)
    result.details.pop("written", None)
    return result


# ---- What a restart finds -----------------------------------------------------------------------

def write_state(library, job, state):
    """Keep `state` as job `job`'s record in the library's cache folder (tagpup.files.job_files)."""
    job_files.write_state(library.bulk_jobs, job, state)

def read_state(library, job):
    """Job `job`'s record, or None."""
    return job_files.read_state(library.bulk_jobs, job)

def write_ids(library, job, ids):
    job_files.write_ids(library.bulk_jobs, job, ids)

def read_ids(library, job):
    """The photos job `job` resolved when it began, or None when the list is lost."""
    return job_files.read_ids(library.bulk_jobs, job)

def forget_ids(library, job):
    job_files.forget_ids(library.bulk_jobs, job)

def forget(library, job):
    """Let go of everything kept of job `job` (its state and its list of photos): when it is over for good."""
    job_files.forget(library.bulk_jobs, job)


def sweep(library, now=None):
    """Let go of the records of jobs untouched too long (tagpup.files.job_files.sweep: a job's files go together, a time shift that
    can be resumed is kept a month, anything else a week). The ids of the resumable jobs let go."""
    return job_files.sweep(library.bulk_jobs, now)


def resumable_heads(library):
    """The ids of the jobs a resume can still carry on (both their list of photos and their state are there): the library keeps
    their first runs, which their ids are, however many runs come after."""
    return job_files.jobs_with_records(library.bulk_jobs)


def has_record(library, job):
    """Does job `job` have the record a resume needs: its list of photos and its state, both readable?"""
    return job_files.has_ids(library.bulk_jobs, job) and job_files.read_state(library.bulk_jobs, job) is not None


def is_this_process(owner):
    """Is `owner` ('host:pid:start', as a run names its process) this process?"""
    return owner == file_journal.owner()


def shifted_ids(library, operation):
    """The ids of the photos the changes named `operation` (operation_of) wrote and left done: the journal's account, and the
    only one that is exact after a crash."""
    return file_journal.photo_ids_done(library.path, operation)

def process_alive(owner):
    """Is the process that owned a run -- 'host:pid:start' -- still going, and not this one? A run of this process that nothing
    in this process runs is a run that was lost (a test of a restart; a thread that died)."""
    return bool(owner) and owner != file_journal.owner() and file_journal.owner_alive(owner)
