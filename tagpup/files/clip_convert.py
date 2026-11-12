"""A clip made H.264 and AAC in an MP4, beside the original: the one module that runs ffmpeg to WRITE a file (DECISIONS
2026-11-09, 'A CLIP IS CONVERTED ONLY WHEN THE OWNER CLICKS CONVERT'; tagpup.files.convertible says which clip needs it).

    temp = clip_convert.temp_of(clip_convert.target_of(source))
    clip_convert.claimed(temp)                                          # True: the temp is this conversion's
    made = clip_convert.convert(tools, source, temp, streams, progress=lambda done, whole: ..., stop=threading.Event())
    problem = clip_convert.verified(tools, source_streams, temp)       # None: the temp is the clip, whole

What it writes: the picture re-encoded to H.264 at CRF 23 (ffmpeg's default quality, the owner's choice: about half the
size of the old formats), 4:2:0 so every browser decodes it; the sound to AAC at 160 kb/s, or copied when it is AAC
already (the 11 .mp4 clips of photo_index whose picture is MPEG-4 Part 2 carry AAC sound, counted 2026-11-09); the
container's own metadata (`-map_metadata 0`: its title) but NOT its creation time (`-metadata creation_time=`): a .wmv's
ASF creation time is when the file was made, often weeks or months after the capture, and carried into the MP4's mvhd
it is read as QuickTime:CreateDate, a Date Taken (tagpup.core.dates.CLIP_DATE_FIELDS), where an older clip stays undated
(DECISIONS 2026-11-09, item 6); cleared, the MP4's QuickTime dates are all zero (measured on a generated .wmv carrying
one, 2026-11-12). And the index at the front (`+faststart`, so the player starts before the whole file has come over a
share). The clip's XMP is not carried by ffmpeg: its tags, title
and kept name are written into the MP4 afterwards by the journaled write of a clip (tagpup.services.convert). Nor is a
.wmv's caption, its ASF Description, which ffmpeg maps to the MP4's Comment, a field the reader does not read as a caption
(measured on a generated .wmv, 2026-11-12; 45 photo_index .wmv rows hold one): the caller gives it (`description`) and it
is written as the MP4's description item, which the reader reads (fields.DESCRIPTION_COPIES), since a clip's journaled
write names no caption (fields.CLIP_WRITES). An MP4 made so takes that XMP by a patch in place: it holds no XMP box, its
moov before mdat, so the packet is appended at its end in a uuid box (tagpup.files.clip_patch, case 4; measured on a
generated clip of each old format, 2026-11-12).

Where: a temp file beside the original, on its volume (`<stem>.mp4.tagpup-convert`, temp_of), never over it: a rename
puts it in place once verified, and nothing but the conversion that made it, or the settle of one cut short, removes it
(tagpup.services.file_changes, the scratch rule). The temp is the caller's once it made it, empty and exclusively
(claimed: one create that fails when a file is there, on any process or library), and ffmpeg writes over that claim
(`-y`) and nothing else: two conversions of one clip never write, or remove, one temp.

How it fails, each as a plain problem (a sentence naming no file: a file's name can hold a person's name), never an
exception, and every failure removes the temp it made: ffmpeg cannot read the clip, or stops with an error (its last line
logged, unnamed); it makes no progress for STALL seconds (a share that stopped answering: ffmpeg blocked in a read), or
passes the caller's deadline, or the caller asks it to stop (`stop`, an Event: Cancel, an update's drain): killed with
its tree through tagpup.core.processes. A temp it could not remove (the share gone meanwhile) is said in the problem, so
the caller keeps its record for the settle to remove it. room_for refuses before anything runs when the volume has not
room for the temp: ROOM times the original, a guess (a re-encode at CRF 23 is about half an old format's size; an MPEG-4
Part 2 clip is already small, and its H.264 may be larger), plus a margin.

Two at once share nothing but the folder: a second conversion of the same clip finds the temp claimed and is refused by
its caller before ffmpeg runs (tagpup.services.convert).
"""
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from typing import Optional

from tagpup.core import processes
from tagpup.files import shares, video_frames
from tagpup.files.exiftool_session import unnamed

logger = logging.getLogger(__name__)

#: What a converted clip is named after its stem, and what its temp file adds to that name.
EXTENSION = ".mp4"
SUFFIX = ".tagpup-convert"

#: The picture: H.264 at CRF 23, ffmpeg's default (owner, 2026-11-09), 4:2:0 for every browser's decoder.
VIDEO = ("-c:v", "libx264", "-crf", "23", "-preset", "medium", "-pix_fmt", "yuv420p")
#: The container's metadata carried (its title), its creation time cleared: never a Date Taken (the module).
METADATA = ("-map_metadata", "0", "-metadata", "creation_time=")
#: The sound, re-encoded; or copied when it is one of COPIED_AUDIO.
AUDIO = ("-c:a", "aac", "-b:a", "160k")
COPIED_AUDIO = frozenset({"aac"})

#: The picture and the sound a converted clip holds, as ffprobe names them (verified).
MADE_VIDEO, MADE_AUDIO = "h264", "aac"

#: How many times the original's size the volume must have free for the temp (a guess: see the module), and the margin.
ROOM = 1.5
SPARE_BYTES = 1024 * 1024 * 1024

#: Seconds with no progress after which ffmpeg is taken as stuck (a share that stopped answering) and killed. Not once
#: the whole length is converted: then ffmpeg moves the index to the front (+faststart), a pass over the whole file that
#: tells no progress, and only the deadline (deadline_for) bounds it.
STALL = 120
#: The deadline of a conversion (deadline_for): this many seconds, and a second for each READ_RATE bytes of the original.
DEADLINE_FLOOR = 600
READ_RATE = 1024 * 1024
#: How long a stopped or stuck ffmpeg is waited for once killed.
KILL_WAIT = 5
#: How far the temp's length may be from the original's, in seconds.
DURATION_SLACK = 0.5
#: Seconds a verification's ffprobe and poster may take, each.
VERIFY_SECONDS = 120

NO_LENGTH = "its length cannot be read, so its conversion could not be checked; nothing was converted"
STOPPED = "the conversion was stopped before it finished; the original is as it was"
STUCK = "ffmpeg made no progress for %d s (a share that stopped answering?) and was stopped; the original is as it was"
TOO_LONG = "the conversion passed its deadline and was stopped; the original is as it was"
FAILED = "ffmpeg could not convert it (%s); the original is as it was"
NOT_STARTED = "ffmpeg could not be started (%s); the original is as it was"
TEMP_LEFT = " Its unfinished copy could not be removed just now (%s); it is removed when the change is settled."
NO_ROOM = ("there is not room for its converted copy on the original's drive: about {:,} MB needed, {:,} MB free (1 GB "
           "kept spare); nothing was converted")
ROOM_UNKNOWN = "how much room the original's drive has could not be read (%s); nothing was converted"


def target_of(source):
    """The path of the MP4 the clip at `source` is converted to: its stem with EXTENSION, in its folder."""
    return os.path.splitext(source)[0] + EXTENSION


def temp_of(target):
    """The temp file a conversion to `target` is written to, beside it (SUFFIX): what the scratch rule removes."""
    return target + SUFFIX


def room_for(source, size):
    """None when the volume of the clip at `source` (`size` bytes) has room for its converted copy (ROOM times its size,
    SPARE_BYTES kept); else why not, a sentence. One bounded look (shares.bounded): a share asleep is said, not waited
    on."""
    folder = os.path.dirname(source)
    state, free = shares.bounded(folder, lambda: shutil.disk_usage(folder).free, shares.LISTING_WAIT)
    if state != "ok":
        return ROOM_UNKNOWN % ("the share did not answer" if state == "away" else (free.strerror or free))
    needed = int(size * ROOM)
    if free >= needed + SPARE_BYTES:
        return None
    mb = 1024 * 1024
    return NO_ROOM.format(-(-needed // mb), free // mb)


def claimed(temp):
    """True when the temp file at `temp` was made now, empty, by this call (an exclusive create): the conversion's own from
    here on. False when a file is there already (another conversion's, or one cut short); else why it could not be made,
    a sentence (a share that did not answer: one bounded call, shares.bounded)."""
    def make():
        os.close(os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    state, error = shares.bounded(os.path.dirname(temp), make, shares.LISTING_WAIT)
    if state == "ok":
        return True
    if state == "error" and isinstance(error, FileExistsError):
        return False
    return "its converted copy could not be begun (%s); nothing was converted" % (
        "the share did not answer" if state == "away" else (error.strerror or error))


def deadline_for(size):
    """The time.monotonic() a conversion of a clip of `size` bytes is stopped at, whatever its progress: DEADLINE_FLOOR
    and a second for each READ_RATE bytes (a 10 GB clip, about three hours)."""
    return time.monotonic() + DEADLINE_FLOOR + size / READ_RATE


def arguments(tools, source, temp, audio, description=""):
    """ffmpeg's arguments to convert `source` into `temp` (claimed: written over), whose sound is of codec `audio` (None:
    none), with `description` as its description item when it is not empty (the module)."""
    sound = ("-c:a", "copy") if audio in COPIED_AUDIO else AUDIO
    described = ["-metadata", "description=" + description] if description else []
    return ([tools.ffmpeg, "-hide_banner", "-nostdin", "-nostats", "-v", "error", "-y", "-i", source,
             "-map", "0:V:0", "-map", "0:a:0?", "-sn", "-dn"] + list(VIDEO) + list(sound)
            + list(METADATA) + described + ["-movflags", "+faststart", "-progress", "pipe:1", "-f", "mp4", temp])


def convert(tools, source, temp, streams, progress=None, stop=None, deadline=None, stall=STALL, description=""):
    """Convert the clip at `source` into `temp` (see the module). `streams` is video_frames.streams of the source, read
    by the caller (its sound decides copy or re-encode; its duration is the whole progress is told against). `progress(done,
    whole)` is told seconds converted and the whole; `stop` an Event asked between progress lines; `deadline` a
    time.monotonic() after which it is stopped; `description`, the caption written into it (the module). `temp` is
    claimed by the caller (claimed). Returns None when ffmpeg finished and the temp is there to verify, else the problem,
    the temp removed (or TEMP_LEFT said)."""
    args = arguments(tools, source, temp, streams.audio, description)
    try:
        child = processes.start(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as problem:
        return NOT_STARTED % (problem.strerror or problem)
    lines, said = queue.Queue(), []
    readers = [threading.Thread(target=_lines, args=(child.stdout, lines), daemon=True),
               threading.Thread(target=_lines, args=(child.stderr, said), daemon=True)]
    for reader in readers:
        reader.start()
    try:
        return _watched(child, readers, lines, said, source, temp, streams, progress, stop, deadline, stall)
    finally:
        for reader in readers:
            reader.join(KILL_WAIT)
        for pipe in (child.stdout, child.stderr):
            pipe.close()


def _watched(child, readers, lines, said, source, temp, streams, progress, stop, deadline, stall):
    """convert's watch of its ffmpeg `child` to its end: None, or the problem (the temp removed or TEMP_LEFT said). The
    caller closes the child's pipes once its readers ended."""
    problem, last, ending = None, time.monotonic(), False
    while True:
        try:
            line = lines.get(timeout=0.25)
        except queue.Empty:
            line = ""
        now = time.monotonic()
        if line is None:
            break
        if line.startswith("out_time_us=") or line.startswith("progress="):
            last = now
            try:
                done = max(0.0, int(line.split("=", 1)[1]) / 1e6) if line.startswith("out_time_us=") else None
            except ValueError:
                done = None
            if done is not None:
                ending = ending or (streams.duration is not None and done >= streams.duration - DURATION_SLACK)
                if progress is not None:
                    progress(done, streams.duration)
        if stop is not None and stop.is_set():
            problem = STOPPED
        elif deadline is not None and now > deadline:
            problem = TOO_LONG
        elif now - last > stall and not ending:
            problem = STUCK % stall
        if problem:
            _end(child)
            break
    if problem is None:
        try:
            child.wait(timeout=KILL_WAIT)
        except subprocess.TimeoutExpired:
            _end(child)
            problem = TOO_LONG
    if problem is None and child.returncode != 0:
        for reader in readers:
            reader.join(KILL_WAIT)
        last_said = [text for text in said if text and text.strip()]
        logger.info("ffmpeg could not convert a clip: %s", unnamed(last_said[-1], [source, temp]) if last_said else
                    "it said nothing")
        problem = FAILED % ("exit code %s" % child.returncode)
    if problem:
        return problem + (removed(temp) or "")
    return None


def _lines(stream, into):
    """Every line of `stream`, decoded, put into `into` (a Queue, or a list kept to its last lines); a Queue is told
    None at the end."""
    try:
        for raw in iter(stream.readline, b""):
            text = raw.decode("utf-8", "replace").strip()
            if isinstance(into, list):
                into.append(text)
                del into[:-20]
            else:
                into.put(text)
    except (OSError, ValueError):
        pass
    finally:
        if not isinstance(into, list):
            into.put(None)


def _end(child):
    """ffmpeg and its tree ended, or as near as the system lets it; never an exception."""
    try:
        processes.kill_tree(child.pid)
    except Exception as problem:
        logger.warning("Ending ffmpeg's tree failed: %s", problem)
    try:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=KILL_WAIT)
    except Exception as problem:
        logger.warning("A stopped ffmpeg has not ended: %s", problem)


def removed(temp):
    """None when the temp file at `temp` is gone (removed now, or never made); else TEMP_LEFT, why it is still there.
    Only a conversion's own temp is passed here: never a listing's find, never the original."""
    def remove():
        try:
            os.remove(temp)
        except FileNotFoundError:
            pass
    state, error = shares.bounded(os.path.dirname(temp), remove, shares.LISTING_WAIT)
    if state == "ok":
        return None
    why = "the share did not answer" if state == "away" else (error.strerror or error)
    logger.warning("A conversion's unfinished copy could not be removed: %s", why)
    return TEMP_LEFT % why


def verified(tools, source, temp):
    """None when the file at `temp` is the clip whose streams are `source` (video_frames.Streams of the original), made
    whole: its picture H.264 and its sound AAC (or none, when the original has none), its length within DURATION_SLACK of
    the original's, and a picture of it decodes. Else why not, a sentence; the temp is the caller's to remove."""
    made = video_frames.streams(tools, temp, VERIFY_SECONDS)
    if made.problem:
        return "its converted copy could not be read back (%s)" % made.problem
    if made.video != MADE_VIDEO or made.audio != (MADE_AUDIO if source.audio else None):
        return "its converted copy holds %s and %s, not %s and %s" % (made.video, made.audio, MADE_VIDEO,
                                                                       MADE_AUDIO if source.audio else "no sound")
    if source.duration is None or made.duration is None or abs(made.duration - source.duration) > DURATION_SLACK:
        return "its converted copy is %s s long and the original %s s" % (_seconds(made.duration),
                                                                           _seconds(source.duration))
    shown = video_frames.poster(tools, temp, seconds=VERIFY_SECONDS)
    if not shown.pictures:
        return "no picture of its converted copy decodes (%s)" % shown.problem
    return None


def _seconds(value: Optional[float]):
    return "an unknown number of" if value is None else "%.2f" % value
