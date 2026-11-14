"""A clip made H.264 and AAC in an MP4, beside the original: the one module that runs ffmpeg to WRITE a file (DECISIONS
2026-11-09, 'A CLIP IS CONVERTED ONLY WHEN THE OWNER CLICKS CONVERT'; tagpup.files.convertible says which clip needs it).

    temp = clip_convert.temp_of(clip_convert.target_of(source))
    clip_convert.claimed(temp)                                          # True: the temp is this conversion's
    made = clip_convert.convert(tools, source, temp, streams, stop=threading.Event())
    problem = clip_convert.verified(tools, source, streams, temp)     # None: the temp is the clip, whole

What it writes: the picture re-encoded to H.264 at CRF 23 (ffmpeg's default quality, the owner's choice: about half the
size of the old formats), 4:2:0 so every browser decodes it; the sound to AAC at 160 kb/s, or copied when it is AAC
already (the 11 .mp4 clips of photo_index whose picture is MPEG-4 Part 2 carry AAC sound, counted 2026-11-09); the
container's own metadata (`-map_metadata 0`: its title), its creation time cleared, always
(`-metadata creation_time=`: every mvhd, tkhd and mdhd of the copy zero, measured on a dated .mov 2026-11-13).
ffmpeg is never given a date: a creation_time spelled without a `Z` is stored as UTC plus the zone's offset (#3001),
and a .wmv's ASF creation time is when its file was made, not its Date Taken (DECISIONS 2026-11-09, item 6). The
Date Taken the library reads is carried by the caller into the copy's XMP and its QuickTime headers in one journaled
patch, as a clip's date is saved (tagpup.services.convert; DECISIONS 2026-11-13, A CLIP'S DATE TAKEN IS WRITTEN INTO
ITS QUICKTIME HEADERS). And the index at the front (`+faststart`, so the player starts before the whole file has
come over a share). The clip's XMP is not carried by ffmpeg: its caller puts the original's packet whole into the temp before it
is verified (tagpup.services.convert, #2938), and its tags, title and kept name are written over it into the MP4
afterwards by the journaled write of a clip. No
caption is carried: none of the 2,833 clips of photo_index holds one (its Description blank in every row, counted
2026-11-12), and a clip's journaled write names none (fields.CLIP_WRITES). An MP4 made
so takes that XMP by a patch in place: it holds no XMP box, its moov before mdat, so the packet is appended at its end in a uuid box (tagpup.files.clip_patch, case 4; measured on a
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
its tree through tagpup.core.processes. An exception through the watch (a second Ctrl+C) ends ffmpeg's tree and removes the temp before it goes on up: no ffmpeg is left writing a temp nothing owns. A temp it could not remove (the share gone meanwhile) is said in the problem, so
the caller keeps its record for the settle to remove it. room_for refuses before anything runs when the volume has not
room for the temp: ROOM times the original, a guess (a re-encode at CRF 23 is about half an old format's size; an MPEG-4
Part 2 clip is already small, and its H.264 may be larger), plus a margin.

What `verified` compares the copy's length with is the clip's own: the end of its last packet less its start
(length_of), not the length its header gives. The two differ in real clips: each of the 62 photo_index .wmv of MPEG-4 v2
picture and MP3 sound says in its ASF header a length about 2.02 s short of its packets (its play duration written
without its 2 s preroll; ffmpeg converts every packet, so its copy came out exactly that much longer and was refused
every run), and the 108 of WMV 8 picture say 0.47 to 3.59 s MORE than their packets hold (refused as well); 159 of the
522 old clips read so were more than 0.5 s off their header (counted read-only, 2026-11-12). By its packets' end the copy
of a real clip of each of the 9 kinds of old clip was within 0.17 s (measured on copies, 2026-11-12). The packets are read
whole, not from a seek near the end: ffprobe's seek in an ASF file found no packet at random, about one run in eight, on
the share and on a local copy alike, where the whole read gave one answer every time; the whole read is a second read
of the original, after ffmpeg's (on the share, 1.8 GB in about 4 minutes cold, 2 s once read).

On its disk before it is the caller's: ffmpeg does not flush what it wrote, so once it ended the temp is flushed
(_flushed: one fsync, bounded on a share); one its disk will not take is a problem like any other (NOT_FLUSHED), the
temp removed. The caller records the copy (its file id and size, `made`) only after convert and verified answered None,
so a record of it names bytes on the disk: after a power cut, the next start's settle finds the temp it names whole, and
may put it in place and delete the original by its file id alone, without reading it again (tagpup.services.convert).

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
from tagpup.files import lock_owners, shares, video_frames
from tagpup.files.exiftool_session import unnamed

logger = logging.getLogger(__name__)

#: What a converted clip is named after its stem, and what its temp file adds to that name.
EXTENSION = ".mp4"
SUFFIX = ".tagpup-convert"

#: The picture: H.264 at CRF 23, ffmpeg's default (owner, 2026-11-09), 4:2:0 for every browser's decoder.
VIDEO = ("-c:v", "libx264", "-crf", "23", "-preset", "medium", "-pix_fmt", "yuv420p")
#: The container's metadata carried: its title; its creation time cleared, every header's (the module).
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
NOT_FLUSHED = "its converted copy could not be written to its disk (%s); the original is as it was"
#: Added to a problem (this module's, or convert's) when the copy could not be removed: a sentence of its own, after one
#: that claims no removal of the copy (#2884).
TEMP_LEFT = ". Its unfinished copy could not be removed just now (%s); it is removed when the change is settled."
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


def arguments(tools, source, temp, audio):
    """ffmpeg's arguments to convert `source` into `temp` (claimed: written over), whose sound is of codec `audio` (None:
    none)."""
    sound = ("-c:a", "copy") if audio in COPIED_AUDIO else AUDIO
    return ([tools.ffmpeg, "-hide_banner", "-nostdin", "-nostats", "-v", "error", "-y", "-i", source,
             "-map", "0:V:0", "-map", "0:a:0?", "-sn", "-dn"] + list(VIDEO) + list(sound)
            + list(METADATA)
            + ["-movflags", "+faststart", "-progress", "pipe:1", "-f", "mp4", temp])


def convert(tools, source, temp, streams, stop=None, deadline=None, stall=STALL):
    """Convert the clip at `source` into `temp` (see the module). `streams` is video_frames.streams of the source, read
    by the caller (its sound decides copy or re-encode; its duration tells when ffmpeg is at the end). `stop` an Event asked between progress lines; `deadline` a
    time.monotonic() after which it is stopped. `temp` is claimed by the caller (claimed). Returns None when ffmpeg finished
    and the temp is on its disk (_flushed) to verify, else the problem,
    the temp removed (or TEMP_LEFT said)."""
    args = arguments(tools, source, temp, streams.audio)
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
        return _watched(child, readers, lines, said, source, temp, streams, stop, deadline, stall)
    except BaseException:
        _end(child)
        removed(temp)
        raise
    finally:
        for reader in readers:
            reader.join(KILL_WAIT)
        for pipe in (child.stdout, child.stderr):
            pipe.close()


def _watched(child, readers, lines, said, source, temp, streams, stop, deadline, stall):
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
    if problem is None:
        problem = _flushed(temp)
    if problem:
        return problem + (removed(temp) or "")
    return None


def _flushed(temp):
    """None once what ffmpeg wrote to the temp at `temp` is on its disk (os.fsync: ffmpeg flushes nothing, and a power cut
    would leave a temp of the recorded name, file id and length holding short or zeroed bytes); else NOT_FLUSHED. One
    bounded call (shares.bounded), not the share's look the next waits behind: its time is the file's size, so its bound is
    VERIFY_SECONDS and a second for each READ_RATE bytes, as the other looks at the copy are (a 1.8 GB clip is not refused
    for taking longer to reach a share's disk than a small one)."""
    def flush():
        with open(temp, "r+b") as handle:
            os.fsync(handle.fileno())
    stamp = shares.stamp_of(temp)
    if not isinstance(stamp, tuple):
        return NOT_FLUSHED % "the share did not answer"
    wait = VERIFY_SECONDS + stamp[1] / READ_RATE
    state, error = shares.bounded(os.path.dirname(temp), flush, wait, waited_behind=False)
    if state == "ok":
        return None
    return NOT_FLUSHED % ("the share did not answer" if state == "away" else (error.strerror or error))


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
    Only a conversion's own temp is passed here: never a listing's find, never the original. A "not found" is a temp gone
    only while its share answers (shares.bounded): of a share asleep it is "the share did not answer"; of a temp another
    program holds open (a scanner given the new file), who holds it (lock_owners.explain)."""
    state, error = shares.bounded(os.path.dirname(temp), lambda: os.remove(temp), shares.LISTING_WAIT)
    if state == "ok" or isinstance(error, FileNotFoundError):
        return None
    if state == "away":
        why = "the share did not answer"
    else:
        why = lock_owners.explain(error, temp, error.strerror or str(error))
    logger.warning("A conversion's unfinished copy could not be removed: %s", why)
    return TEMP_LEFT % why


def length_of(tools, path):
    """The length in seconds of the clip at `path` as its packets give it: the end of its last packet (its time and its
    own length) less the clip's start, by one ffprobe reading it whole (the module: not its header's length), within
    VERIFY_SECONDS and a second for each READ_RATE bytes. None when it cannot be read."""
    stamp = shares.stamp_of(path)
    if not isinstance(stamp, tuple):
        return None
    try:
        done = processes.run([tools.ffprobe, "-v", "error", "-show_entries",
                              "packet=pts_time,dts_time,duration_time:format=start_time", "-of", "compact=p=0", path],
                             stdin=subprocess.DEVNULL, capture_output=True, timeout=VERIFY_SECONDS + stamp[1] / READ_RATE)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    ends, start = [], 0.0
    for line in done.stdout.decode("utf-8", "replace").splitlines():
        said = dict(part.split("=", 1) for part in line.split("|") if "=" in part)
        try:
            if "start_time" in said:
                start = float(said["start_time"])
                continue
            at = said.get("pts_time") if said.get("pts_time") != "N/A" else said.get("dts_time")
            if at is not None and at != "N/A":
                ends.append(float(at) + (float(said["duration_time"]) if said.get("duration_time", "N/A") != "N/A" else 0))
        except ValueError:
            continue
    return max(ends) - start if ends else None


def verified(tools, path, source, temp):
    """None when the file at `temp` is the clip at `path` whose streams are `source` (video_frames.Streams of it), made
    whole: its picture H.264 and its sound AAC (or none, when the original has none), its length within DURATION_SLACK of
    the original's as its packets give it (length_of), and a picture of it decodes. Else why not, a sentence; the temp is
    the caller's to remove."""
    made = video_frames.streams(tools, temp, VERIFY_SECONDS)
    if made.problem:
        return "its converted copy could not be read back (%s)" % made.problem
    if made.video != MADE_VIDEO or made.audio != (MADE_AUDIO if source.audio else None):
        return "its converted copy holds %s and %s, not %s and %s" % (made.video, made.audio, MADE_VIDEO,
                                                                       MADE_AUDIO if source.audio else "no sound")
    length = length_of(tools, path)
    if length is None or made.duration is None or abs(made.duration - length) > DURATION_SLACK:
        return "its converted copy is %s s long and the original %s s" % (_seconds(made.duration), _seconds(length))
    shown = video_frames.poster(tools, temp, seconds=VERIFY_SECONDS)
    if not shown.pictures:
        return "no picture of its converted copy decodes (%s)" % shown.problem
    return None


def _seconds(value: Optional[float]):
    return "an unknown number of" if value is None else "%.2f" % value
