"""A clip converted to H.264 and AAC in an MP4, as one change of the library's journal (DECISIONS 2026-11-09, 'A CLIP IS
CONVERTED ONLY WHEN THE OWNER CLICKS CONVERT'; tagpup.files.clip_convert runs ffmpeg).

    result = convert.convert_clip(library, exiftool_path, tools, source, reason="old format")

One function for every caller (the one-time convert of the old formats, a clip's own button, a playable MOV made smaller):
`reason` alone tells them apart, in the change's summary.

How one conversion goes, the original kept until its copy is in place and holds what it carried:
1. Refused before ffmpeg runs, nothing planned: a clip away or gone, without a row, still holding Windows Tags (moved
   first: tagpup.services.windows_tags), open in another program, whose length cannot be read, with no room for its copy
   on its drive, or larger than a Recycle Bin takes (recycle_bin.MOST_CLIP_BYTES) unless the owner confirmed `for_good`.
   Under the lock of changes of photo files: one a cut-short change has still to settle, one whose converted name holds a
   file or a row, one another conversion is converting.
2. Planned (file_journal.CONVERT), one file: its path the original's, its new_path the MP4's (None for the same name),
   fields_before the original's fingerprint (file_changes.fingerprint), fields_after what it carries (`carry`: its tags,
   title and the name it had; no caption, clip_convert) and `for_good`; marked `writing`; the temp claimed (clip_convert.claimed).
3. ffmpeg, outside the lock (minutes); the temp verified, and its
   fingerprint recorded as `made`.
4. Onward (_onward, the forward run's and the settle's alike), under the lock: the temp renamed into place (a same-name
   conversion deletes the original first, its name being the copy's); the row moved to the new name and made to say
   what the copy holds, in one transaction; the carry written into the copy (CARRIED, file_changes.write_fields: only
   what differs); the original deleted LAST, to the Recycle Bin; marked done, the change finished.

The copy is known by its file id (`made`'s ino; _is_copy): the carry writes into it in place, which changes its size and
modified time but never its file id, nor does the rename into place; a volume that gives its files no file id is refused
before anything is planned, as a rename refuses one (file_changes.rename).

Interrupted part-way, the settle (file_changes._finish routes the change here, settle_change) goes by where `made` is:
nowhere, the temp is removed by the scratch rule (file_changes.removed_scratch: never one written in the last minutes)
and the file taken out of the change (or a conflict, when the original changed); in the temp, the conversion is let go
(temp removed, file taken out) unless a same-name original is gone already (then it is renamed into place); at the new
name, the rest of step 4 is done. A carry that fails, or a Recycle Bin that refuses the original, leaves the file
`writing` and the original where it was, for the next settle. Never undone (journal.UNDO_CHECKS): the original is in the
Recycle Bin.

The clip's vectors and frames are not carried over the conversion: its stamp changes and the indexer computes them again
from the copy. Files are named by their photo's id, never by path.
"""
import logging
import os

from tagpup.core import fields, paths, vocabulary
from tagpup.core.result import Result
from tagpup.files import clip_convert, field_values, images, lock_owners, names, recycle_bin, shares, video_frames
from tagpup.services import file_changes, tagging
from tagpup.store import db, file_journal, photos

logger = logging.getLogger(__name__)

#: The change a conversion is, as History names it.
OPERATION = file_journal.CONVERT
#: The change that writes what a conversion carries into its copy (file_changes.write_fields).
CARRIED = "convert: tags carried"
#: What the carry reads of the copy, and the conversion of the original: every keyword field, the title, the kept name.
READ = tagging.KEYWORD_READ + tuple(fields.title_fields("")) + (fields.CLIP_TITLE, fields.PRESERVED_NAME)
#: The reason of the one-time convert of the old formats (convert_clip's `reason`).
OLD_FORMAT = "old format"

#: The steps of a conversion, in order; `_reached` is told of each.
STEPS = ("temp verified", "renamed", "rows followed", "carried", "original deleted")

AWAY = "its folder could not be reached just now (a network share that did not answer); nothing was converted"
GONE = "it is not there any more; nothing was converted"
NO_ROW = "the library has no row for it; nothing was converted"
NOT_A_CLIP = "it is not a video; nothing was converted"
UNREADABLE = "its metadata could not be read (%s); nothing was converted"
WINDOWS_TAGS = "it still holds Windows Tags: move the Windows Tags first; nothing was converted"
HELD_OPEN = "it is open in another program; close it and try again; nothing was converted"
NO_FILE_ID = ("its drive gives its files no file id, so its converted copy could not be told from another file; nothing "
              "was converted")
NAME_TAKEN = "a file or a row is there already under its converted name; nothing was converted"
UNDER_WAY = "it is being converted already, or a conversion of it cut short is still to be settled; nothing was converted"
TEMP_TAKEN = ("an unfinished converted copy of it is there already (another conversion, or one cut short); nothing was "
              "converted")
CHANGED = "it changed while it was being converted; its converted copy was removed and the original is as it is"
NOT_MADE = "its converted copy is not as it was made; it was removed and the original is as it was"
NOT_IN_PLACE = ("its converted copy could not be put in its place (%s); the original is in the Recycle Bin and the copy is "
                "put in place when the change is settled")
NOT_RENAMED = "its converted copy could not be put in its place (%s); it was removed and the original is as it was"
NOT_CARRIED = ("its tags, title or kept name could not be written into its converted copy (%s); the original is kept, "
               "and the change is finished when it is settled")
ROW_THERE = "a row of the library is at its converted name already; both files are kept as they are"
ORIGINAL_KEPT = ("it was converted, but its original could not be deleted (%s); the original is kept, and its delete is "
                 "tried again when the change is settled")
CHANGED_SINCE = "it changed since it was converted: it holds neither what the conversion found nor its converted copy"


def _reached(step):
    """A step of a conversion is done (STEPS). Nothing happens here; the tests stop the conversion at each step by
    patching it."""


class _Ready:
    """What a conversion found of the clip before it planned (_ready)."""

    def __init__(self, photo_id, stamp, streams, carry, target):
        self.photo_id, self.stamp, self.streams, self.carry, self.target = photo_id, stamp, streams, carry, target


def convert_clip(library, exiftool_path, tools, source, reason=OLD_FORMAT, for_good=False, progress=None, stop=None):
    """Convert the clip at `source` of `library` (see the module), `tools` being config.locate_ffmpeg()'s. `for_good`: the
    owner confirmed a clip over recycle_bin.MOST_CLIP_BYTES is deleted for good. `progress(done, whole)` in seconds;
    `stop`, an Event: Cancel. A Result: `changed` 1 once the copy is in place, its row moved and what it carries written;
    refused with why, the original as it was, otherwise. details: `change` (its id or None), `reason`, `photo` (the
    photo's id), and `original_kept`, the sentence, when the copy is in place but the original could not be deleted (the
    result is refused too: the CLI exits 1)."""
    result = Result(attempted=1)
    result.details.update(change=None, reason=reason, photo=None)
    source = paths.stored(source)
    file_changes.settle(library, exiftool_path)
    ready = _ready(library, exiftool_path, tools, source, for_good)
    if isinstance(ready, str):
        result.refuse(ready)
        return result
    result.details["photo"] = ready.photo_id
    target = ready.target
    temp = clip_convert.temp_of(target)
    with file_changes.exclusively():
        why = _taken(library, source, target)
        before = file_changes.fingerprint(source)
        if why or before is None or not before["ino"]:
            result.refuse(why or (GONE if before is None else NO_FILE_ID))
            return result
        change_id, (row,) = file_journal.plan(library.path, OPERATION, [{
            "photo_id": ready.photo_id, "path": source, "new_path": None if paths.same(source, target) else target,
            "before": before, "after": {"carry": ready.carry, "for_good": bool(for_good)}}],
            summary={"photos": 1, "reason": reason})
        result.details["change"] = change_id
        try:
            file_journal.writing(library.path, row.id, ready.stamp)
            claim = clip_convert.claimed(temp)
            if claim is not True:
                file_journal.withdraw(library.path, [row.id])
                file_journal.finish(library.path, change_id)
                result.refuse(TEMP_TAKEN if claim is False else claim)
                return result
        except BaseException:
            file_journal.release(library.path, change_id)
            raise
    try:
        problem = clip_convert.convert(tools, source, temp, ready.streams, progress=progress, stop=stop,
                                       deadline=clip_convert.deadline_for(ready.stamp[1]))
        if problem is None:
            problem = clip_convert.verified(tools, ready.streams, temp)
        if problem is None:
            _reached("temp verified")
            row.after = dict(row.after, made=file_changes.fingerprint(temp))
            db.write_with_connection(library.path, lambda conn: file_journal.set_after(conn, row.id, row.after),
                                     label="change %d: its copy made" % change_id)
        with file_changes.exclusively():
            if _onward(library, exiftool_path, row, True, result, problem):
                file_journal.finish(library.path, change_id)
            else:
                file_journal.release(library.path, change_id)
    except BaseException:
        file_journal.release(library.path, change_id)
        raise
    return result


def _ready(library, exiftool_path, tools, source, for_good):
    """_Ready, or why the clip at `source` is not converted (a sentence), looked at before anything is planned."""
    if not images.is_clip(source):
        return NOT_A_CLIP
    stamp = shares.stamp_of(source)
    if stamp is shares.UNANSWERED:
        return AWAY
    if not isinstance(stamp, tuple):
        return GONE
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        found = photos.rows_of(conn, [source]).get(paths.key(source))
    finally:
        conn.close()
    if found is None:
        return NO_ROW
    try:
        with field_values.session(exiftool_path) as et:
            held = field_values.read_one(et, source, list(READ) + [fields.CLIP_CATEGORY])
    except field_values.Unreadable as e:
        return UNREADABLE % e
    if fields.field_values(held.get(fields.CLIP_CATEGORY)):
        return WINDOWS_TAGS
    try:
        lock_owners.opened(source, lock_owners.NO_SHARING).close()
    except OSError:
        return HELD_OPEN
    streams = video_frames.streams(tools, source)
    if streams.problem:
        return streams.problem
    if streams.duration is None:
        return clip_convert.NO_LENGTH
    room = clip_convert.room_for(source, stamp[1])
    if room:
        return room
    if recycle_bin.too_large_to_copy(source, stamp[1]) and not for_good:
        return recycle_bin.clip_not_kept(source, stamp[1])
    carry = {"tags": list(vocabulary.extract_tags({field: held.get(field) for field in fields.TAG_SOURCE_FIELDS})),
             "title": fields.own_title(held),
             "name": names.preserved_name(held) or os.path.basename(source)}
    return _Ready(found[0], stamp, streams, carry, clip_convert.target_of(source))


def _taken(library, source, target):
    """Why the clip at `source` may not be converted to `target` now, under the lock of changes of photo files, or None:
    a cut-short change has it still to settle, a file or a row is at `target` (another name), another conversion of it is
    not finished."""
    why = file_changes.unsettled(source)
    if why:
        return why
    if not paths.same(source, target):
        if shares.stamp_of(target) is not None:
            return NAME_TAKEN
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            if photos.rows_of(conn, [target]):
                return NAME_TAKEN
        finally:
            conn.close()
    for change in file_journal.unfinished(library.path):
        if change.operation == OPERATION and any(
                paths.same(row.path, source) for row in file_journal.files_of(library.path, change.id)
                if row.state in ("planned", "writing")):
            return UNDER_WAY
    return None


def settle_change(library, change, exiftool_path):
    """Settle a conversion `change` a crash or an error left (file_changes._finish routes it here): a file `planned` was
    never begun and is taken out; one `writing` is carried onward (_onward). True when it is finished; False, released by
    the caller, when a file is left `writing` (a share away, a temp written in the last minutes, a carry that failed, a
    Recycle Bin that refused)."""
    settled = True
    for row in file_journal.files_of(library.path, change.id):
        if row.state == "planned":
            file_journal.withdraw(library.path, [row.id])
        elif row.state == "writing":
            if any(shares.stamp_of(path) is shares.UNANSWERED for path in (row.path, row.new_path or row.path)):
                settled = False
                continue
            settled = _onward(library, exiftool_path, row, False, Result()) and settled
    if settled:
        file_journal.finish(library.path, change.id)
    return settled


def _onward(library, exiftool_path, row, forward, result, problem=None):
    """Carry the conversion `row` onward from where its copy is (the module, step 4), under the lock of changes of photo
    files: `forward`, the run that made it (`problem`, why ffmpeg or the verification failed); else a settle. True when the
    file is settled (done, taken out, or a conflict); False when it stays `writing` for the next settle. `result` is told
    what was done, or refused why."""
    source, target = row.path, row.new_path or row.path
    renamed = row.new_path is not None
    temp = clip_convert.temp_of(target)
    made = row.after.get("made")
    in_temp = _is_copy(temp, made)
    in_target = not in_temp and _is_copy(target, made)
    if not in_temp and not in_target:
        left = clip_convert.removed(temp) if forward else _stale_temp_removed(row, temp)
        if left:
            result.refuse((problem or NOT_MADE) + left)
            return False
        _let_go(library, row, result, problem or NOT_MADE)
        return True
    if in_temp:
        if not renamed:
            gone = shares.stamp_of(source) is None
            if not forward and not gone:
                return _given_up(library, row, temp, result, None)
            if forward:
                kept = _deleted(source, row.after.get("for_good", False))
                if kept:
                    return _given_up(library, row, temp, result, kept)
                _reached("original deleted")
        elif not forward:
            return _given_up(library, row, temp, result, None)
        elif file_changes.fingerprint(source) != row.before:
            return _given_up(library, row, temp, result, CHANGED)
        try:
            os.rename(temp, target)
        except FileExistsError:
            return _given_up(library, row, temp, result, NAME_TAKEN)
        except OSError as e:
            if renamed:
                return _given_up(library, row, temp, result, NOT_RENAMED % (e.strerror or e))
            _note(library, row, NOT_IN_PLACE % (e.strerror or e))
            result.refuse(NOT_IN_PLACE % (e.strerror or e))
            return False
    _reached("renamed")
    if not _followed(library, exiftool_path, row, target):
        file_journal.mark(library.path, [row.id], "conflict", ROW_THERE)
        result.refuse(ROW_THERE)
        return True
    _reached("rows followed")
    carried = file_changes.write_fields(library, CARRIED, exiftool_path, [target], list(READ), _carrier(library, row.after),
                                        summary={"photos": 1, "change": row.change_id})
    if not carried.ok:
        _note(library, row, NOT_CARRIED % carried.message())
        result.refuse(NOT_CARRIED % carried.message())
        return False
    _reached("carried")
    if renamed and file_changes.fingerprint(source) == row.before:
        kept = _deleted(source, row.after.get("for_good", False) if forward else False)
        if kept:
            _note(library, row, ORIGINAL_KEPT % kept)
            result.changed = 1
            result.details["original_kept"] = ORIGINAL_KEPT % kept
            result.refuse(ORIGINAL_KEPT % kept)
            return False
        _reached("original deleted")
    file_journal.mark(library.path, [row.id], "done")
    result.changed = 1
    return True


def _is_copy(path, made):
    """Is the file at `path` the conversion's copy, `made` its fingerprint when ffmpeg had made it: the same file id (the
    module: the carry and the rename keep it, a volume without file ids is never converted)."""
    if made is None:
        return False
    found = file_changes.fingerprint(path)
    return found is not None and found["ino"] == made["ino"]


def _stale_temp_removed(row, temp):
    """A settle's removal of a temp whose owner is not known (no `made` in it): None when it is gone; else why it stays
    (file_changes.removed_scratch: one written in the last minutes may be another conversion's under way)."""
    there = shares.stamp_of(temp)
    if there is None:
        return None
    if there is shares.UNANSWERED:
        return " Its unfinished copy could not be looked at just now."
    why = file_changes.removed_scratch(row.path, row.stamp, None, scratch=temp)
    return None if why is None else " Its unfinished copy is left for now: %s." % why


def _given_up(library, row, temp, result, why):
    """The conversion let go with its copy still in the temp: the temp removed (the conversion's own, by its `made`) and
    the file taken out of the change. `why`, the refusal, or None for a settle. True; False when the temp could not be
    removed (the file stays `writing`)."""
    left = clip_convert.removed(temp)
    if left:
        result.refuse((why or NOT_MADE) + left)
        return False
    _let_go(library, row, result, why)
    return True


def _let_go(library, row, result, why):
    """The file taken out of the change when the original holds what the conversion found (it was never touched); else a
    conflict. `why`, the refusal (None: none)."""
    if file_changes.fingerprint(row.path) == row.before:
        file_journal.withdraw(library.path, [row.id])
    else:
        file_journal.mark(library.path, [row.id], "conflict", CHANGED_SINCE)
    if why:
        result.refuse(why)


def _note(library, row, why):
    """The file left `writing` with `why` noted, for the next settle."""
    db.write_with_connection(library.path, lambda conn: file_journal.set_state(conn, row.id, "writing", why),
                             label="%s: left writing" % row.named())


def _deleted(source, for_good):
    """None when the original at `source` was deleted (recycle_bin.delete_file: to its Recycle Bin, or for good when the
    owner confirmed `for_good`); else why it is still there, a sentence."""
    try:
        recycle_bin.delete_file(source, for_good=for_good)
    except (OSError, ValueError) as e:
        return str(e)
    return None


def _followed(library, exiftool_path, row, target):
    """The photo's row moved to `target` (a new name) and made to say what the copy holds, read whole, in one transaction;
    its stamp the copy's, so its vectors and frames are computed again. False when another row is at `target`."""
    stat = os.stat(target)   # unbounded: the conversion's own copy, just put in place under the lock
    with field_values.session(exiftool_path) as et:
        whole = file_changes.read_whole(et, target, named=row.named())

    def work(conn):
        if row.new_path is not None:
            here = photos.rows_of(conn, [target]).get(paths.key(target))
            if here is None or here[0] != row.photo_id:
                _moved, skipped = photos.move_rows_in(conn, {row.path: target})
                if skipped:
                    return False
        photos.follow_fields(conn, target, {}, stat, None, whole=whole)
        return True

    return db.write_with_connection(library.path, work, label="%s: its converted copy" % row.named())


def _carrier(library, after):
    """The `plan_one` of the carry into a converted copy: of what the conversion carries (`after`'s `carry`), only what
    the copy does not hold already -- its tags (tagging.keywords_planner: the copy's kept, the carried added after), its
    title, the name it had -- else skipped."""
    carry = after["carry"]
    tags_planner = tagging.keywords_planner(library, lambda _path, held_tags: (
        [tag for tag in carry["tags"] if tag not in held_tags], ()))

    def plan_one(path, held):
        wanted = {}
        held_tags = vocabulary.extract_tags({field: held.get(field) for field in fields.TAG_SOURCE_FIELDS})
        if any(tag not in held_tags for tag in carry["tags"]):
            wanted.update(tags_planner(path, held).after)
        if carry["title"] and fields.own_title(held) != carry["title"]:
            wanted.update(fields.title_fields(carry["title"]))
        if carry["name"] and names.preserved_name(held) != carry["name"]:
            wanted[fields.PRESERVED_NAME] = carry["name"]
        if not wanted:
            return file_changes.skip("its converted copy holds what it carries already")
        return file_changes.Plan(after=wanted, detail=sorted(wanted))
    return plan_one
