"""ONE-OFF, deleted with scripts/convert_old_clips.py after the owner's run (DECISIONS 2026-11-12): a library's clips in
an older format (.wmv .mpg .mpeg .avi .mod: tagpup.files.convertible.OPENED says which containers a browser opens) made
H.264 CRF 23 and AAC in an .mp4 of the same stem, beside the original (tagpup.files.clip_convert). No journal, no tag
carry: the 629 such rows of photo_index hold no tag, person, face, date or suggestion (counted 2026-11-12); 503 hold a
title (their ASF Title, which ffmpeg carries as the MP4's title, read as the title), and none a caption (45 hold an ASF
Description of blanks alone, counted 2026-11-12: nothing to carry). The journaled conversion behind the page's buttons
is another branch's.

One clip, in order: refused before ffmpeg when its share is away, a file or a library row already holds its .mp4's
name, a temp of a conversion under way is there, it is open in another program, its length cannot be read, or its
drive has no room; then its temp claimed (clip_convert.claimed), converted, verified (its length within 0.5 s of the
original's packets', a picture decodes), a marker `<temp>.ok` written beside it, THEN the original deleted through
tagpup.files.recycle_bin.delete_file (from a share, copied through this PC to its Recycle Bin), THEN the temp renamed
to the .mp4's name and the marker removed. Never deleted for good: a delete refused (the Bin cannot keep the copy, a
player holds the file) stops the whole run with the original and its verified temp kept.

A run killed part-way leaves one of: the original and a temp with no marker (the next run removes the temp once it is
STALE, and converts again); the original and a verified temp with its marker (the next run verifies it again, deletes
the original and renames it); no original and a verified temp with its marker (the next run renames it). A clip whose
original is gone and whose .mp4 is there is done, and passed over.

What the library does next (measured in tests/test_a_librarys_old_clips_converted.py with the real sync and index):
nothing here writes the library. The next sync of the whole library (TagPup's catch-up as it starts, the daily sync, or
`tagpup_cli.py sync --apply`) finds the old row's file gone and the .mp4 new: the two are not paired as a move (another
name, another size, no identity in either), so the old row is removed (journaled, undoable: its vector and frames with
it) and the folder is queued, whose index gives the .mp4 a row of its own, its title read.
"""
import os
import time
from typing import NamedTuple, Optional

from tagpup.core import paths
from tagpup.files import clip_convert, convertible, lock_owners, recycle_bin, shares, video_frames
from tagpup.store import db
from tagpup.store import photos as store_photos

#: What a clip is found as before anything is done to it (state_of).
TO_CONVERT, STALE_TEMP, VERIFIED, TO_FINISH = "to convert", "a stale temp", "a verified temp", "a temp to rename"
NAME_TAKEN, UNDER_WAY, DONE, GONE, AWAY = "name taken", "a conversion under way", "done", "original gone", "away"
#: The states a run acts on, and those it leaves for a later run (the owner's to settle, or a share to wake).
ACTED_ON = (TO_CONVERT, STALE_TEMP, VERIFIED, TO_FINISH)
LEFT = (NAME_TAKEN, UNDER_WAY, AWAY)

#: Seconds a temp must not have been written for to be no conversion's: twice the time clip_convert stops an ffmpeg
#: that made no progress (STALL), whose temp it then removes.
STALE = 2 * clip_convert.STALL
#: What a verified temp's marker adds to its name.
MARKER = ".ok"

HELD = "it is open in another program; close it and run this again"
UNDER_WAY_SAID = "a temp of another conversion of it, written in the last %d s, is beside it" % STALE
STOPPED_VERIFIED = "stopped with its copy verified and kept beside it: the next run finishes it"
NOT_RENAMED = "its original is deleted and its verified copy could not be renamed into place (%s): run this again"


class Clip(NamedTuple):
    photo_id: int
    path: str
    target: str
    temp: str


class Outcome(NamedTuple):
    """What became of one clip: `wrote` (the original gone, the .mp4 in place), the `problem` when not, and `stop` when
    the run must stop (a delete refused: the sentence)."""
    wrote: bool
    problem: Optional[str] = None
    stop: Optional[str] = None
    before: int = 0
    after: int = 0


def old_format(path):
    """Is the clip at `path` in an older format, one whose container no browser opens (convertible.OPENED)?"""
    return os.path.splitext(path)[1].lower() not in convertible.OPENED


def marker_of(temp):
    return temp + MARKER


def survey(library_path, ids=None, tools=None, also_part_2=False, on_probe=None):
    """[Clip] of the library at `library_path` to convert, by id: its clips in an older format, and with `also_part_2`
    each .mp4/.mov/.m4v clip whose picture or sound no browser plays (convertible.plan_for, its streams read by ffprobe
    through `tools`; `on_probe(done, of)` told every hundred), and each such clip whose original a run killed after
    its delete left gone beside its verified temp (its marker there: the run finishes it). Of `ids` alone when given.
    Opened read-only."""
    conn = db.connect(db.readonly_uri(library_path), uri=True)
    try:
        rows = [(photo_id, path) for photo_id, path in store_photos.clip_rows(conn) if ids is None or photo_id in ids]
        chosen = [(photo_id, path) for photo_id, path in rows if old_format(path)]
        if also_part_2:
            opened = [(photo_id, path) for photo_id, path in rows if not old_format(path)]
            for done, (photo_id, path) in enumerate(opened, 1):
                held = video_frames.streams(tools, path)
                if held.problem:
                    if _marked(path):
                        chosen.append((photo_id, path))
                elif convertible.plan_for(os.path.splitext(path)[1], held.video,
                                          held.audio).action == convertible.REENCODE:
                    chosen.append((photo_id, path))
                if on_probe is not None and (done % 100 == 0 or done == len(opened)):
                    on_probe(done, len(opened))
        clips = []
        for photo_id, path in sorted(chosen):
            target = clip_convert.target_of(path)
            clips.append(Clip(photo_id, path, target, clip_convert.temp_of(target)))
        taken = store_photos.rows_of(conn, [clip.target for clip in clips if not _same(clip)])
    finally:
        conn.close()
    return clips, {paths.key(target) for target in taken}


def _marked(path):
    """Is the marker of a verified temp beside the clip at `path` (converted to an .mp4 of its own name)?"""
    return isinstance(shares.stamp_of(marker_of(clip_convert.temp_of(clip_convert.target_of(path)))), tuple)


def _same(clip):
    return paths.key(clip.target) == paths.key(clip.path)


def state_of(clip, rows_taken):
    """(state, the original's size) of `clip` now, by one bounded look at each of its files (shares.stamp_of);
    `rows_taken` the keys of the targets a library row holds."""
    source, temp = shares.stamp_of(clip.path), shares.stamp_of(clip.temp)
    target = source if _same(clip) else shares.stamp_of(clip.target)
    marked = shares.stamp_of(marker_of(clip.temp))
    looks = (source, temp, target, marked)
    if any(look == shares.UNANSWERED for look in looks):
        return AWAY, 0
    there = [isinstance(look, tuple) for look in looks]
    if not there[0]:
        if there[1] and there[3] and (_same(clip) or not there[2]):
            return TO_FINISH, 0
        return (DONE if there[2] and not _same(clip) else GONE), 0
    if not _same(clip) and (there[2] or paths.key(clip.target) in rows_taken):
        return NAME_TAKEN, source[1]
    if there[1] and there[3]:
        return VERIFIED, source[1]
    if there[1]:
        return (STALE_TEMP if time.time() - temp[0] > STALE else UNDER_WAY), source[1]
    return TO_CONVERT, source[1]


def _held_open(path):
    """True when another program holds the clip at `path` open (an open sharing nothing is refused)."""
    def look():
        with lock_owners.opened(path, lock_owners.NO_SHARING):
            pass
    state, error = shares.bounded(os.path.dirname(path), look, shares.LISTING_WAIT)
    return state == "error" and lock_owners.looks_held(error)


def _bounded(path, call):
    """None when `call` ran on `path`'s folder; else why not, a phrase."""
    state, error = shares.bounded(os.path.dirname(path), call, shares.LISTING_WAIT)
    if state == "ok":
        return None
    return "the share did not answer" if state == "away" else (getattr(error, "strerror", None) or str(error))


def _write_marker(temp):
    def write():
        with open(marker_of(temp), "w", encoding="utf-8") as handle:
            handle.write("verified\n")
    return _bounded(temp, write)


def _remove_marker(temp):
    try:
        os.remove(marker_of(temp))
    except FileNotFoundError:
        pass


def _forget_temp(temp):
    """The temp and its marker removed (never the original); the sentence of what could not be."""
    return clip_convert.removed(temp) or _bounded(temp, lambda: _remove_marker(temp))


def convert_one(tools, clip, state, size, stop=None, progress=None):
    """Do what `state` (state_of) asks of `clip`, an original of `size` bytes: an Outcome. See the module."""
    if state in (STALE_TEMP, VERIFIED):
        source = video_frames.streams(tools, clip.path) if state == VERIFIED else None
        if source is not None and not source.problem and clip_convert.verified(tools, clip.path, source, clip.temp) is None:
            return _finish(clip, size)
        left = _forget_temp(clip.temp)
        if left:
            return Outcome(False, "an earlier temp of it could not be removed (%s)" % left)
    if state == TO_FINISH:
        return _rename(clip, size)
    if _held_open(clip.path):
        return Outcome(False, HELD)
    source = video_frames.streams(tools, clip.path)
    if source.problem:
        return Outcome(False, "ffprobe could not read it (%s)" % source.problem)
    if source.duration is None:
        return Outcome(False, clip_convert.NO_LENGTH)
    room = clip_convert.room_for(clip.path, size)
    if room:
        return Outcome(False, room)
    claim = clip_convert.claimed(clip.temp)
    if claim is not True:
        return Outcome(False, UNDER_WAY_SAID if claim is False else claim)
    problem = clip_convert.convert(tools, clip.path, clip.temp, source, progress=progress, stop=stop,
                                   deadline=clip_convert.deadline_for(size))
    if problem:
        return Outcome(False, problem)
    problem = clip_convert.verified(tools, clip.path, source, clip.temp) or _write_marker(clip.temp)
    if problem:
        return Outcome(False, problem + (_forget_temp(clip.temp) or ""))
    if stop is not None and stop.is_set():
        return Outcome(False, STOPPED_VERIFIED)
    return _finish(clip, size)


def _finish(clip, size):
    """The original deleted through the one delete (never for good), then the verified temp renamed into place."""
    try:
        recycle_bin.delete_file(clip.path)
    except (OSError, ValueError) as refused:
        return Outcome(False, stop=str(refused))
    return _rename(clip, size)


def _rename(clip, size):
    problem = _bounded(clip.temp, lambda: os.rename(clip.temp, clip.target))
    if problem:
        return Outcome(False, NOT_RENAMED % problem)
    _bounded(clip.temp, lambda: _remove_marker(clip.temp))
    made = shares.stamp_of(clip.target)
    return Outcome(True, before=size, after=made[1] if isinstance(made, tuple) else 0)
