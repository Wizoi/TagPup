"""A clip converted to H.264 and AAC in an MP4, as one change of the library's journal (DECISIONS 2026-11-09, 'A CLIP IS
CONVERTED ONLY WHEN THE OWNER CLICKS CONVERT'; tagpup.files.clip_convert runs ffmpeg).

    result = convert.convert_clip(library, fields, tools, source, reason="old format")

One function for every caller (the one-time convert of the old formats, a clip's own button, a playable MOV made smaller):
`reason` alone tells them apart, in the change's summary.

The panel's two buttons (start, POST /api/clip/convert): Convert, for a clip that does not play (OLD_FORMAT), and Make
smaller (MP4), for one that does (SMALLER; a copy no smaller is removed, the clip left as it is). Each is a batch of one on
a thread of the server (batches.claim), the request answered at once with its ticket; no bulk convert (owner 2026-11-12).

How one conversion goes, the original kept until its copy is in place and holds what it carried:
1. Refused before ffmpeg runs, nothing planned: a clip away or gone, without a row, holding a Windows tag it cannot
   carry (CANNOT_CARRY, _carryable), open in another program (HELD_OPEN, naming who when this PC can tell,
   lock_owners.explain; another failure of that open keeps its own sentence), whose length cannot be read, whose plan
   is not its reason's (NEEDS: a clip that plays is not converted, one that does not is not made smaller), or with no
   room for its copy on its drive.
   Under the lock of changes of photo files: one a cut-short change has still to settle, one whose converted name holds a
   file or a row, one another conversion is converting.
   Before the lock, beside the refusals above, its XMP packet is read (field_values.packet_held), to be carried whole into
   its copy (owner 2026-11-13: every XMP property is kept, those TagPup does not use too; #2938); a clip holding XMP no
   patch reads (two XMP boxes) is refused (XMP_UNREAD), and an old format holds none to carry.
   Its fingerprint (file_changes.fingerprint) is taken before any of it is read, and taken again under the lock: a clip
   another program wrote meanwhile (seconds: ExifTool's read, the packet's, ffprobe's) is refused CHANGED, so what is
   planned, carried and deleted is the file that was read.
2. Planned (file_journal.CONVERT), one file: its path the original's, its new_path the MP4's (None for the same name),
   fields_before the original's fingerprint (file_changes.fingerprint), fields_after what it carries (`carry`: its tags,
   title, the name it had and its Date Taken, as the library reads it of the original read whole; no caption,
   clip_convert; and its Windows Tags, after its tags: a clip tagged long ago in Windows Photo Gallery holds them in the
   WM/Category of its Xtra, fields.CLIP_CATEGORY, which ffmpeg does not carry and the library never reads as tags);
   marked `writing`; the temp claimed (clip_convert.claimed).
3. ffmpeg, outside the lock (minutes); the original's XMP packet put whole into the temp, which ffmpeg gives none
   (field_values.packet_given; _packet_carried: one it cannot take is a problem like ffmpeg's, the temp removed, the
   original as it was); the temp flushed to its disk and verified
   (clip_convert), and its fingerprint recorded as `made`. What the copy carries is written over that packet (step 4):
   TagPup's fields win, every other property of it is kept (xmp_packet.edit).
4. Onward (_onward, the forward run's and the settle's alike), under the lock: the temp renamed into place (a same-name
   conversion deletes the original first, its name being the copy's); the row moved to the new name and made to say
   what the copy holds, in one transaction; the carry written into the copy (CARRIED, file_changes.write_fields: only
   what differs; the Date Taken into its XMP and its QuickTime headers in one patch, as a clip's date is saved,
   fields.date_taken_fields: ffmpeg carries no date, clip_convert; no clip holds one no header can, before 1904 or
   after early 2040: the save refuses it, 3017); the copy read back holding all it carries and no Windows Tags (_short_of); the
   original deleted LAST; marked done, the change finished. The row's Date Taken is so the original's before the
   original is deleted.

The original is deleted FOR GOOD, never to a Recycle Bin (owner 2026-11-12: its copy is verified first; the Bin is for
deletes), by one bounded call (_removed_for_good), and only while it is the file the conversion found (its fingerprint).
A delete that fails (a program holding it, a share that stopped answering, or that says "not found" at once as one
asleep does: _original_kept) keeps the original and the copy: the file is left `writing` and the next settle deletes it
(a same-name conversion, whose copy has no name of its own yet, is let go instead: its temp removed, the original kept).

The copy is known by its file id (`made`'s ino; _is_copy): the carry writes into it in place, which changes its size and
modified time but never its file id, nor does the rename into place; a volume that gives its files no file id is refused
before anything is planned, as a rename refuses one (file_changes.rename).

Interrupted part-way, the settle (file_changes._finish routes the change here, settle_change) goes by where `made` is,
and trusts it without verifying the copy again: `made` is recorded only for a copy on its disk (clip_convert, 'On its
disk'):
nowhere, the temp is removed by the scratch rule (file_changes.removed_scratch: never one written in the last minutes)
and the file taken out of the change (or a conflict, when the original changed); in the temp, the conversion is let go
(temp removed, file taken out) unless a same-name original is gone already (then it is renamed into place); at the new
name, the rest of step 4 is done. A carry that fails, or an original that could not be deleted, leaves the file
`writing` and the original where it was, for the next settle. Never undone (journal.UNDO_CHECKS): the original is
deleted.

The clip's vectors and frames are not carried over the conversion: its stamp changes and the indexer computes them again
from the copy. Files are named by their photo's id, never by path.
"""
import logging
import os
import threading

from tagpup.core import dates, fields as core_fields, paths, vocabulary
from tagpup.core.result import (CHANGED_ON_DISK, CHANGED_ON_DISK_SENTENCE, Conflict, NotFound, Refused, Result,
                                Unavailable)
from tagpup.files import (clip_convert, convertible, field_values, images, lock_owners, names, shares,
                          video_frames)
from tagpup.services import batches, file_changes, tagging
from tagpup.services import photos as photo_actions
from tagpup.store import db, file_journal, photos, taxonomy

logger = logging.getLogger(__name__)

#: The change a conversion is, as History names it.
OPERATION = file_journal.CONVERT
#: The change that writes what a conversion carries into its copy (file_changes.write_fields).
CARRIED = "convert: tags carried"
#: What the conversion reads of the original, and the carry of the copy: every keyword field, the title, the kept name,
#: the XMP dates (a clip's write names no other: fields.CLIP_FIELDS).
READ = (tagging.KEYWORD_READ + tuple(core_fields.title_fields("")) + (core_fields.CLIP_TITLE, core_fields.PRESERVED_NAME)
        + dates.CLIP_XMP_DATE_FIELDS)
#: What the carry reads of the copy: READ and its QuickTime header dates, box by box, by TagPup's walk of the copy's box
#: headers (field_values.read: no byte of its picture or sound; the original's are never read).
CARRY_READ = READ + core_fields.CLIP_HEADER_DATES
#: The reason of the one-time convert of the old formats (convert_clip's `reason`).
OLD_FORMAT = "old format"
#: The reason of a clip that plays made smaller, an MP4 (the panel's Make smaller (MP4): a .mov over SmugMug's limit; owner
#: 2026-11-12). Its copy is kept only when it is smaller than the original (NOT_SMALLER).
SMALLER = "made smaller"
#: What the clip panel's two buttons ask (POST /api/clip/convert's `mode`): the reason each converts with.
MODES = {"convert": OLD_FORMAT, "shrink": SMALLER}
#: What the clip's plan (convertible.plan_for, from the streams _ready reads) must be for each reason: Convert is for a
#: clip that does not play, Make smaller for one that does.
NEEDS = {OLD_FORMAT: convertible.REENCODE, SMALLER: convertible.NOTHING}
#: The conversions of this process kept for the panel to read once they have ended (outcome), the last ones to end; one
#: under way is always kept.
MOST_KEPT = 50
#: The name of a conversion's thread (start), with its library's.
CONVERT_THREAD = "ConvertThread %s"

#: The steps of a conversion, in order; `_reached` is told of each.
STEPS = ("temp verified", "renamed", "rows followed", "carried", "original deleted")

AWAY = "its folder could not be reached just now (a network share that did not answer); nothing was converted"
GONE = "it is not there any more; nothing was converted"
NO_ROW = "the library has no row for it; nothing was converted"
NOT_A_CLIP = "it is not a video; nothing was converted"
UNREADABLE = "its metadata could not be read (%s); nothing was converted"
CANNOT_CARRY = ("it holds Windows Tags its tags cannot take, not in the tag tree or under a people root and no person "
                "(%s): add them to the tree, or take them off in Windows; nothing was converted")
HELD_OPEN = "it is open in another program; close it and try again; nothing was converted"
NO_FILE_ID = ("its drive gives its files no file id, so its converted copy could not be told from another file; nothing "
              "was converted")
NAME_TAKEN = "a file or a row is there already under its converted name; nothing was converted"
UNDER_WAY = "it is being converted already, or a conversion of it cut short is still to be settled; nothing was converted"
TEMP_TAKEN = ("an unfinished converted copy of it is there already (another conversion, or one cut short); nothing was "
              "converted")
#: The sentences of a conversion let go with its copy in the temp claim no removal of it: clip_convert.TEMP_LEFT
#: follows them when its removal fails (#2884).
CHANGED = "it changed while it was being converted; the original is as it is"
NOT_MADE = "its converted copy is not as it was made; the original is as it was"
NOT_IN_PLACE = ("its converted copy could not be put in its place (%s); the original is deleted and the copy is put in "
                "place when the change is settled")
NOT_RENAMED = "its converted copy could not be put in its place (%s); the original is as it was"
NOT_CARRIED = ("its tags, title or kept name could not be written into its converted copy (%s); the original is kept, "
               "and the change is finished when it is settled")
ROW_THERE = "a row of the library is at its converted name already; both files are kept as they are"
#: Why an original is still there when its share did not answer (ORIGINAL_KEPT, NOT_DELETED).
SHARE_AWAY = "its share did not answer"
#: Why an original is still there when it could not be looked at: neither there nor gone (ORIGINAL_KEPT).
NOT_LOOKED_AT = "it could not be looked at just now"
ORIGINAL_KEPT = ("it was converted, but its original could not be deleted (%s); the original and its verified copy "
                 "are both kept, and the original is deleted when the change is settled (the next start)")
ORIGINAL_CHANGED = ("it was converted, but its original changed meanwhile (another program wrote it), so it is not "
                    "deleted: it and its verified copy are both kept, and the next sync finds it as a clip of its own")
CHANGED_SINCE = "it changed since it was converted: it holds neither what the conversion found nor its converted copy"
PLAYS_ALREADY = "it plays as it is: it is made smaller (Make smaller), not converted; nothing was converted"
DOES_NOT_PLAY = "it does not play as it is: it is converted (Convert), not made smaller; nothing was converted"
NOT_SMALLER = "its MP4 came out no smaller than it is: left as it is"
NOT_DELETED = ("its original could not be deleted (%s), so its converted copy, which takes its name, is not put in "
               "its place; the original is as it was")
XMP_UNREAD = ("its XMP is held in a way TagPup does not read (two XMP boxes, or one it does not know), so it could not "
              "be carried into its converted copy; nothing was converted")
XMP_NOT_GIVEN = "its XMP could not be put into its converted copy (%s); the original is as it was"
ASKED_ALREADY = "it is being converted already; wait for it to finish"
NO_SUCH = "This server has no conversion %d (it may have been long ago, or before the server restarted)."


def _reached(step):
    """A step of a conversion is done (STEPS). Nothing happens here; the tests stop the conversion at each step by
    patching it."""


class _Ready:
    """What a conversion found of the clip before it planned (_ready): `before`, its fingerprint taken before it was read;
    `packet`, the XMP packet it holds (bytes), None for none."""

    def __init__(self, photo_id, stamp, before, streams, carry, target, packet):
        self.photo_id, self.stamp, self.streams, self.carry, self.target = photo_id, stamp, streams, carry, target
        self.before, self.packet = before, packet


def convert_clip(library, fields, tools, source, reason=OLD_FORMAT, stop=None):
    """Convert the clip at `source` of `library` (see the module), `tools` being config.locate_ffmpeg()'s, its original
    deleted for good once its copy is verified and in place.
    `stop`, an Event: Cancel. A Result: `changed` 1 once the copy is in place, its row moved and what it carries written;
    refused with why, the original as it was, otherwise. `reason` is one of NEEDS: the clip's plan must be its own (a
    clip that plays is not converted, one that does not is not made smaller); made SMALLER, a copy no smaller than the
    original is removed and the clip refused NOT_SMALLER. details: `change` (its id or None), `reason`, `photo` (the
    photo's id), `path` (where its copy goes), `size_before` (the original's bytes), `size_after` (the copy's, in place;
    the removed copy's for NOT_SMALLER), and `original_kept`, the sentence, when the copy is in place but the original could not be deleted (the
    result is refused too)."""
    result = Result(attempted=1)
    result.details.update(change=None, reason=reason, photo=None)
    source = paths.stored(source)
    file_changes.settle(library, fields)
    ready = _ready(library, fields, tools, source, reason)
    if isinstance(ready, str):
        result.refuse(ready)
        return result
    target = ready.target
    result.details.update(photo=ready.photo_id, path=target, size_before=ready.stamp[1])
    temp = clip_convert.temp_of(target)
    with file_changes.exclusively():
        why = _taken(library, source, target)
        before = file_changes.fingerprint(source)
        if not why:
            why = (GONE if before is None else NO_FILE_ID if not before["ino"] else
                   CHANGED if before != ready.before else None)
        if why:
            result.refuse(why)
            return result
        change_id, (row,) = file_journal.plan(library.path, OPERATION, [{
            "photo_id": ready.photo_id, "path": source, "new_path": None if paths.same(source, target) else target,
            "before": before, "after": {"carry": ready.carry}}],
            summary={"photos": 1, "reason": reason})
        result.details["change"] = change_id
        try:
            file_journal.writing(library.path, row.id, ready.stamp)
            claim = clip_convert.claimed(temp)
            if claim is not True:
                file_journal.withdraw(library.path, [row.id])
                file_changes.finish_forward(library, change_id, result)
                result.refuse(TEMP_TAKEN if claim is False else claim)
                return result
        except BaseException:
            file_journal.release(library.path, change_id)
            raise
    try:
        problem = clip_convert.convert(tools, source, temp, ready.streams, stop=stop,
                                       deadline=clip_convert.deadline_for(ready.stamp[1]))
        if problem is None and ready.packet is not None:
            problem = _packet_carried(temp, ready.packet)
        if problem is None:
            problem = clip_convert.verified(tools, source, ready.streams, temp)
        made = file_changes.fingerprint(temp) if problem is None else None
        if made is not None and reason == SMALLER and made["size"] >= ready.stamp[1]:
            result.details["size_after"] = made["size"]
            problem, made = NOT_SMALLER, None     # not recorded: _onward removes the temp and lets the clip go
        if problem is None:
            _reached("temp verified")
            row.after = dict(row.after, made=made)
            db.write_with_connection(library.path, lambda conn: file_journal.set_after(conn, row.id, row.after),
                                     label="change %d: its copy made" % change_id)
        with file_changes.exclusively():
            if _onward(library, fields, row, True, result, problem):
                file_changes.finish_forward(library, change_id, result)
            else:
                file_journal.release(library.path, change_id)
            if result.changed:
                result.details["size_after"] = _size_recorded(library, target)
    except BaseException:
        file_journal.release(library.path, change_id)
        raise
    return result


_asked_lock = threading.Lock()
_converting = set()   # (library key, photo id) of the conversions asked of this process and not yet ended
_asked = {}           # (library key, ticket): [its thread, what `done` made of how it ended, or None while under way]


def start(library, exiftool_path, find_ffmpeg, photo_id, mode, stamp, done=None):
    """Convert the clip `photo_id` of `library` as the panel's button `mode` asks (MODES), on a thread of its own as a
    batch of one (batches.claim: listed on the Activity page's strip, cancelled by its ticket, waited for and stopped by an
    update's drain), so that the request returns at once and a closed tab loses nothing. `stamp`, (mtime, size), the clip
    as the page's record had it: a clip changed since (a second window, or the panel's own MP4 made already) is refused
    CHANGED_ON_DISK. Raises Unavailable without ffmpeg, as photos.video does (NotFound, Conflict, Unavailable,
    paths.RootsError), Conflict (ASKED_ALREADY) while this process converts it already, and batches.claim's Conflict when
    too many batches wait. A Result: refused, or details `ticket` and `path` (the clip's). When it ends, `done(path, how)`
    -- how: convert_clip's Result, or what it raised -- is run on the thread and what it returns kept for outcome()."""
    tools = find_ffmpeg()
    if tools.problem:
        raise Unavailable(tools.problem)
    source = photo_actions.video(library, photo_id).path
    result = Result(attempted=1)
    if photo_actions.changed_on_disk(source, stamp):
        result.refuse(CHANGED_ON_DISK_SENTENCE, CHANGED_ON_DISK)
        return result
    asked = (library.key, photo_id)
    with _asked_lock:
        if asked in _converting:
            raise Conflict("The video was not converted: %s." % ASKED_ALREADY)
        _converting.add(asked)
    lined, said = threading.Event(), {}

    def in_line(batch):
        with _asked_lock:
            said["ticket"] = batch.ticket
            _asked[(library.key, batch.ticket)] = [threading.current_thread(), None]
            for old in [key for key, held in _asked.items() if held[1] is not None][:-MOST_KEPT]:
                del _asked[old]
        lined.set()

    def work():
        try:
            # Its Fields opened here, on the thread that uses it: the request has returned already.
            with batches.claim(library, OPERATION, 1, on_line=in_line) as batch, field_values.opened(exiftool_path) as fields:
                how = convert_clip(library, fields, tools, source, reason=MODES[mode], stop=batch.stop)
        except Exception as e:   # told to the panel through `done`, the request answered already; or before it was
            if not lined.is_set():
                said["raised"] = e
                lined.set()
                return
            how = e
        finally:
            with _asked_lock:
                _converting.discard(asked)
        ended = done(source, how)
        with _asked_lock:
            if (library.key, said["ticket"]) in _asked:
                _asked[(library.key, said["ticket"])][1] = ended

    threading.Thread(target=work, name=CONVERT_THREAD % library.name, daemon=True).start()
    lined.wait()
    if "raised" in said:
        raise said["raised"]
    result.details.update(ticket=said["ticket"], path=source)
    return result


def outcome(library, ticket, wait=None):
    """What `done` made of how the conversion `ticket` of `library` ended (start), or None while it is under way; with
    `wait`, seconds to wait for it first (a test). NotFound for a ticket this process has none of (MOST_KEPT)."""
    with _asked_lock:
        held = _asked.get((library.key, ticket))
    if held is None:
        raise NotFound(NO_SUCH % ticket)
    if wait:
        held[0].join(wait)
    return held[1]


def _ready(library, fields, tools, source, reason):
    """_Ready, or why the clip at `source` is not converted (a sentence), looked at before anything is planned."""
    if not images.is_clip(source):
        return NOT_A_CLIP
    stamp = shares.stamp_of(source)
    if stamp is shares.UNANSWERED:
        return AWAY
    if not isinstance(stamp, tuple):
        return GONE
    before = file_changes.fingerprint(source)     # before any byte of it is read: compared under the lock
    if before is None:
        return GONE
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        found = photos.rows_of(conn, [source]).get(paths.key(source))
    finally:
        conn.close()
    if found is None:
        return NO_ROW
    # Whole, in the indexer's shape: its Date Taken is the row's (dates.date_taken of what the indexer records), but
    # for a date in its name, which is not carried: the copy has the clip's name and is dated by it again.
    try:
        held, whole = fields.read_one(source, list(READ) + [core_fields.CLIP_CATEGORY], whole=True)
    except field_values.Unreadable as e:
        logger.warning("A clip to convert could not be read whole: %s", e)
        return UNREADABLE % "ExifTool could not read it"
    windows = [str(text) for text in dict.fromkeys(core_fields.field_values(held.get(core_fields.CLIP_CATEGORY)))]
    carryable = _carryable(library) if windows else frozenset()
    cannot = [text for text in windows if text not in carryable]
    if cannot:
        return CANNOT_CARRY % "; ".join(cannot)
    try:
        lock_owners.opened(source, lock_owners.NO_SHARING).close()
    except OSError as e:
        if lock_owners.looks_held(e):
            return lock_owners.explain(e, source, HELD_OPEN)
        return UNREADABLE % (e.strerror or e)
    try:
        packet = field_values.packet_held(source)
    except field_values.NotAnswered:
        return AWAY
    except OSError as e:
        return UNREADABLE % (e.strerror or e)
    if packet is field_values.PACKET_UNREAD:
        return XMP_UNREAD
    streams = video_frames.streams(tools, source)
    if streams.problem:
        return streams.problem
    if streams.duration is None:
        return clip_convert.NO_LENGTH
    plan = convertible.plan_for(os.path.splitext(source)[1], streams.video, streams.audio)
    if plan.action != NEEDS[reason]:
        return PLAYS_ALREADY if plan.action == convertible.NOTHING else DOES_NOT_PLAY
    room = clip_convert.room_for(source, stamp[1])
    if room:
        return room
    tags = list(vocabulary.extract_tags({field: held.get(field) for field in core_fields.TAG_SOURCE_FIELDS}))
    # As a read of the copy gives them back (a Windows level, Places beside Places/Coast, is no tag of its own), so that
    # the copy reads back holding what it carries (_short_of).
    carry = {"tags": list(vocabulary.extract_tags(core_fields.keyword_fields(*core_fields.expand_tag_fields(tags + windows)))),
             "title": core_fields.own_title(held),
             "name": names.preserved_name(held) or os.path.basename(source), "date": dates.date_taken(whole, None, dates.VIDEO)}
    return _Ready(found[0], stamp, before, streams, carry, clip_convert.target_of(source), packet)


def _packet_carried(temp, packet):
    """None once the converted copy at `temp`, made by ffmpeg and not yet recorded, holds `packet`, the XMP packet of its
    original, whole (field_values.packet_given); else why not, XMP_NOT_GIVEN, a problem as ffmpeg's is: the temp removed
    and the original as it was (_onward)."""
    try:
        field_values.packet_given(temp, packet)
    except field_values.NotAnswered:
        return XMP_NOT_GIVEN % SHARE_AWAY
    except (OSError, Refused) as e:
        return XMP_NOT_GIVEN % (getattr(e, "strerror", None) or e)
    return None


def _carryable(library):
    """The tags of `library`'s tag tree a clip's Windows Tags may be carried into its copy's tags as, the rule of the
    one-time move of 2026-11-12 (DECISIONS 2026-11-09): a text the tree holds, as it is (a tag path, People/Wren Ashcombe;
    a branch is a tag like another, owner 2026-11-12), but under a people root a person's alone: a keyword there names a
    person (vocabulary.extract_refs), so one that is no person (a group of people) would name one by its leaf. Read once a
    conversion, of one snapshot of the tree."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        db.begin(conn)
        tree = taxonomy.tags(conn)
        known = taxonomy.read_people_vocabulary(conn)
    finally:
        conn.close()
    return frozenset(tag for tag in tree
                     if vocabulary.keyword_key(tag) in known.ids or vocabulary.key(vocabulary.root_of(tag)) not in known.roots)


def _taken(library, source, target):
    """Why the clip at `source` may not be converted to `target` now, under the lock of changes of photo files, or None:
    a cut-short change has it still to settle, a file or a row is at `target` (another name; AWAY when its share did not
    answer), another conversion of it is not finished."""
    why = file_changes.unsettled(source)
    if why:
        return why
    if not paths.same(source, target):
        there = shares.stamp_of(target)
        if there is shares.UNANSWERED:
            return AWAY
        if there is not None:
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


def settle_change(library, change, fields):
    """Settle a conversion `change` a crash or an error left (file_changes._finish routes it here): a file `planned` was
    never begun and is taken out; one `writing` is carried onward (_onward). True when it is finished; False, released by
    the caller, when a file is left `writing` (a share away, a temp written in the last minutes, a carry that failed, an
    original that could not be deleted)."""
    settled = True
    for row in file_journal.files_of(library.path, change.id):
        if row.state == "planned":
            file_journal.withdraw(library.path, [row.id])
        elif row.state == "writing":
            # Neither there nor gone (a share asleep, or one whose connection dropped): waited for, never decided on.
            if any(shares.stamp_of(path) in (shares.UNANSWERED, shares.CANNOT_READ)
                   for path in (row.path, row.new_path or row.path)):
                settled = False
                continue
            settled = _onward(library, fields, row, False, Result()) and settled
    if settled:
        file_journal.finish(library.path, change.id)
    return settled


def _onward(library, fields, row, forward, result, problem=None):
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
                if file_changes.fingerprint(source) != row.before:
                    return _given_up(library, row, temp, result, CHANGED)
                kept = _removed_for_good(source)
                if kept:
                    return _given_up(library, row, temp, result, NOT_DELETED % kept)
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
            why = lock_owners.explain(e, temp, e.strerror or str(e))   # who holds the copy, if a program does
            if renamed:
                return _given_up(library, row, temp, result, NOT_RENAMED % why)
            _note(library, row, NOT_IN_PLACE % why)
            result.refuse(NOT_IN_PLACE % why)
            return False
    _reached("renamed")
    if not _followed(library, fields, row, target):
        file_journal.mark(library.path, [row.id], "conflict", ROW_THERE)
        result.refuse(ROW_THERE)
        return True
    _reached("rows followed")
    carried = file_changes.write_fields(library, CARRIED, fields, [target], list(CARRY_READ), _carrier(library, row.after),
                                        summary={"photos": 1, "change": row.change_id})
    short = carried.message() if not carried.ok else _short_of(library, fields, target, row.after)
    if short:
        _note(library, row, NOT_CARRIED % short)
        result.refuse(NOT_CARRIED % short)
        return False
    _reached("carried")
    if renamed:
        kept, again = _original_kept(source, row.before)
        if kept:
            result.changed = 1
            result.details["original_kept"] = kept
            result.refuse(kept)
            if again:
                _note(library, row, kept)
                return False
            file_journal.mark(library.path, [row.id], "done", kept)
            return True
        _reached("original deleted")
    file_journal.mark(library.path, [row.id], "done")
    result.changed = 1
    return True


def _size_recorded(library, target):
    """The size of the converted copy at `target` as its row records it (the carry's write, or _followed, read it last):
    no look at its share, which may have stopped answering as the conversion ended."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return photos.stamps_of(conn, [target])[paths.key(target)][3]
    finally:
        conn.close()


def _short_of(library, fields, target, after):
    """None when the copy at `target` reads back holding all the conversion carries (`after`'s carry: _carrier finds
    nothing to write) and no Windows Tags (ffmpeg carries no Xtra); else what it lacks, a phrase naming fields, never
    tags. Read after the carry and before the original, the one other holder of what it carries, is deleted for good."""
    try:
        held = fields.read_back(target, list(CARRY_READ) + [core_fields.CLIP_CATEGORY])
    except (OSError, field_values.Unreadable) as e:
        return "it could not be read back: %s" % e
    if core_fields.field_values(held.get(core_fields.CLIP_CATEGORY)):
        return "it holds Windows Tags"
    plan = _carrier(library, after)(target, held)
    return None if plan.skip else "read back, it lacks %s" % ", ".join(plan.detail)


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
        return ". Its unfinished copy could not be looked at just now."
    why = file_changes.removed_scratch(row.path, row.stamp, None, scratch=temp)
    return None if why is None else ". Its unfinished copy is left for now: %s." % why


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


def _original_kept(source, before):
    """Why the original at `source` is still there once its copy is in place, the sentence the owner is told, and whether
    a settle deletes it later (True: the file stays `writing`); (None, False) when it was deleted for good now
    (_removed_for_good) or is not there to delete (deleted already: a settle after its delete). Every other answer is
    said, never done in silence:
    - ORIGINAL_KEPT, for the next settle: its share did not answer, or says "not found" at once as one asleep does
      (shares.stamp_of's UNANSWERED, #1543, #2890); it could not be looked at (CANNOT_READ: a share whose connection
      dropped, WinError 64, or a look failing after the share answered); its delete failed.
    - ORIGINAL_CHANGED, finished: it is not the file the conversion found (`before`, its fingerprint), so it is never
      deleted."""
    there = shares.stamp_of(source)
    if there is None:
        return None, False
    if there is shares.UNANSWERED:
        return ORIGINAL_KEPT % SHARE_AWAY, True
    found = None if there is shares.CANNOT_READ else file_changes.fingerprint(source)
    if found is None:
        return ORIGINAL_KEPT % NOT_LOOKED_AT, True
    if found != before:
        return ORIGINAL_CHANGED, False
    why = _removed_for_good(source)
    return (None, False) if why is None else (ORIGINAL_KEPT % why, True)


def _removed_for_good(source):
    """None when the original at `source` was deleted for good (never to a Recycle Bin: the module), by one call bounded
    as a listing is (shares.bounded); else why it is still there, a phrase, naming who holds it open if a program does
    (lock_owners.explain)."""
    state, error = shares.bounded(os.path.dirname(source), lambda: os.remove(source), shares.LISTING_WAIT)
    if state == "ok":
        return None
    if state == "away":
        return SHARE_AWAY
    return lock_owners.explain(error, source, getattr(error, "strerror", None) or str(error))


def _followed(library, fields, row, target):
    """The photo's row moved to `target` (a new name) and made to say what the copy holds, read whole, in one transaction;
    its stamp the copy's, so its vectors and frames are computed again. False when another row is at `target`."""
    stat = os.stat(target)   # unbounded: the conversion's own copy, just put in place under the lock
    try:
        _held, whole = fields.read_back(target, [], whole=True)
    except field_values.Unreadable as e:
        logger.warning("%s: could not read it back whole: %s", row.named(), e)
        whole = None

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
    title, the name it had, its Date Taken (in the XMP dates and QuickTime headers a clip's date is saved in,
    fields.date_taken_fields) -- else skipped."""
    carry = after["carry"]
    tags_planner = tagging.keywords_planner(library, lambda _path, held_tags: (
        [tag for tag in carry["tags"] if tag not in held_tags], ()))

    def plan_one(path, held):
        wanted = {}
        held_tags = vocabulary.extract_tags({field: held.get(field) for field in core_fields.TAG_SOURCE_FIELDS})
        if any(tag not in held_tags for tag in carry["tags"]):
            wanted.update(tags_planner(path, held).after)
        if carry["title"] and core_fields.own_title(held) != carry["title"]:
            wanted.update(core_fields.title_fields(carry["title"]))
        if carry["name"] and names.preserved_name(held) != carry["name"]:
            wanted[core_fields.PRESERVED_NAME] = carry["name"]
        if carry["date"]:
            dated = core_fields.held_dates(core_fields.date_taken_fields(carry["date"], clip=True), held)
            # Every box compared: a copy whose XMP holds the date (its packet carried whole) while ffmpeg left its
            # headers zero is written, the XMP date beside them in the patch (field_values.clip_refused).
            if dates.date_taken(held, None, dates.VIDEO) != carry["date"] or any(
                    core_fields.field_values(held.get(field)) != [dated[field]]
                    for field in core_fields.CLIP_HEADER_DATES if field in dated):
                wanted.update(dated)
        if not wanted:
            return file_changes.skip("its converted copy holds what it carries already")
        return file_changes.Plan(after=wanted, detail=sorted(wanted))
    return plan_one
