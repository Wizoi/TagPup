"""Pictures out of a video file: `count` equally spaced frames, or one poster, decoded by ffmpeg on the CPU.

What Suggest's vectors and, later, the faces of a video are made from (DECISIONS 2026-11-02, "VIDEO IS MEDIA TAGPUP
MANAGES" 9 and 10, "THE OWNERS' PLANS" 5): five frames at equal spacing, never the whole stream. Each frame is one ffmpeg
run that seeks in the file before it decodes (`-ss` before `-i`), so a ten-gigabyte video on a share costs five short
reads, not a read of the whole file.

    tools = tagpup.config.locate_ffmpeg()          # which ffmpeg the machine has, or why there is none
    found = video_frames.frames(tools, path, 5)    # Frames(pictures=(jpeg bytes, ...), problem=None)
    found = video_frames.poster(tools, path)       # one picture, from the middle of the video

A frame is JPEG bytes, upright (ffmpeg applies a video's rotation matrix before any filter of ours: it autorotates by
default), its pixels made square (a DV or MPEG-2 video stores 720 wide for 4:3 and 16:9 alike), its longer side at most
`max_side`. Frame i of n is taken at (2i + 1) / 2n of the time from the first frame to the start of the last (the
duration less one frame, by the stream's frame rate): the middle of each n-th, so neither a black first frame nor a
moment after the last frame, where an exact seek decodes nothing (a clip of half a second, five frames asked). A video
with no duration (a raw stream) gives one frame, its first, read without a seek (ffmpeg cannot seek in one): there is
nothing to space them by, and decoding the whole stream to count its frames is the read this module exists to avoid.

How it fails, each as a plain `problem` and no picture, never an exception: ffmpeg not found (the sentence
locate_ffmpeg gives); the file not there, unreadable, or on a share that does not answer (one bounded look first,
tagpup.files.shares.stamp_of: a share asleep holds no ffmpeg); a file ffmpeg cannot read as a video (not a video, or cut
short before its index); ffmpeg past the call's deadline, `seconds` for the whole call (killed with its tree through
tagpup.core.processes, so a run blocked on a share that stopped answering half way ends too). A file cut short after its
index gives the frames before the cut and no problem: fewer pictures than asked. Two calls at once share nothing: each
starts processes of its own. A problem names no file: a file's name can hold a person's name.
"""
import json
import logging
import subprocess
import time
from fractions import Fraction
from typing import NamedTuple, Optional, Tuple

from tagpup.core import processes
from tagpup.files import shares
from tagpup.files.exiftool_session import unnamed

logger = logging.getLogger(__name__)

#: The longer side of a frame, at most, in pixels.
MAX_SIDE = 1024

#: Seconds one call may take, every ffmpeg run of it together.
DEFAULT_SECONDS = 120

#: Seconds a killed ffmpeg is waited for, once killed; one blocked in a read of a share may outlive it.
KILL_WAIT = 5

#: JPEG quality ffmpeg encodes a frame at (2 best, 31 worst).
QUALITY = "3"

NOT_THERE = "The video is not there."
CANNOT_READ = "The video cannot be read (permissions, or another program has it locked)."
SHARE_AWAY = "The share the video is on did not answer."
NOT_A_VIDEO = "ffmpeg cannot read this file as a video."
NO_FRAME = "ffmpeg decoded no frame of this video."
TOO_LONG = "ffmpeg took longer than %d s and was stopped."


class Frames(NamedTuple):
    """The pictures (JPEG bytes, in the video's order) and, when there are none, why."""
    pictures: Tuple[bytes, ...]
    problem: Optional[str]


def _refused(problem):
    return Frames((), problem)


def frames(tools, path, count, max_side=MAX_SIDE, seconds=DEFAULT_SECONDS):
    """`count` frames of the video at `path`, equally spaced (see the module); `tools` is what
    tagpup.config.locate_ffmpeg answered."""
    if tools.problem:
        return _refused(tools.problem)
    count = max(1, int(count))
    stamp = shares.stamp_of(path)
    if stamp is None:
        return _refused(NOT_THERE)
    if stamp is shares.UNANSWERED:
        return _refused(SHARE_AWAY)
    if stamp is shares.CANNOT_READ:
        return _refused(CANNOT_READ)
    deadline = time.monotonic() + seconds
    span, problem = _span(tools.ffprobe, path, deadline, seconds)
    if problem:
        return _refused(problem)
    times = [None] if span is None else [span * (2 * i + 1) / (2 * count) for i in range(count)]
    pictures = []
    for at in times:
        picture, problem = _picture(tools.ffmpeg, path, at, max_side, deadline, seconds)
        if problem:
            return _refused(problem)
        if picture:
            pictures.append(picture)
    if not pictures:
        return _refused(NO_FRAME)
    return Frames(tuple(pictures), None)


def poster(tools, path, max_side=MAX_SIDE, seconds=DEFAULT_SECONDS):
    """One frame of the video at `path`, from its middle: Frames with one picture, or the problem."""
    return frames(tools, path, 1, max_side, seconds)


def _rate(said):
    """Frames a second, from ffprobe's "30000/1001"; 0 when it says none ("0/0", or nothing)."""
    try:
        return float(Fraction(str(said)))
    except (ValueError, ZeroDivisionError):
        return 0.0


def _span(ffprobe, path, deadline, seconds):
    """(seconds from the first frame of the video's first picture stream to the start of its last, or None when it
    has no duration; problem)."""
    done, problem = _run([ffprobe, "-v", "error", "-select_streams", "V:0",
                          "-show_entries", "stream=codec_type,duration,r_frame_rate:format=duration", "-of", "json",
                          path],
                         deadline, seconds)
    if problem:
        return None, problem
    code, out, err = done
    try:
        said = json.loads(out.decode("utf-8", "replace")) if code == 0 else {}
    except ValueError:
        said = {}
    streams = said.get("streams") or []
    if not streams:
        _log("ffprobe", err, path)
        return None, NOT_A_VIDEO
    for value in (streams[0].get("duration"), (said.get("format") or {}).get("duration")):
        try:
            found = float(value)
        except (TypeError, ValueError):
            continue
        if found > 0:
            rate = _rate(streams[0].get("r_frame_rate"))
            frame = 1 / rate if rate > 0 else 0
            return (found - frame if found > frame else found), None
    return None, None


def _picture(ffmpeg, path, at, max_side, deadline, seconds):
    """(JPEG bytes of the frame at `at` seconds -- the first frame, unsought, when None -- b"" when none decodes
    there; problem)."""
    bound = "scale='min(%d,iw)':'min(%d,ih)':force_original_aspect_ratio=decrease" % (max_side, max_side)
    seek = [] if at is None else ["-ss", "%.3f" % at]
    done, problem = _run([ffmpeg, "-hide_banner", "-nostdin", "-v", "error"] + seek + ["-i", path,
                          "-map", "0:V:0", "-frames:v", "1", "-an", "-sn", "-dn",
                          "-vf", "scale='iw*sar':'ih',setsar=1," + bound,
                          "-c:v", "mjpeg", "-q:v", QUALITY, "-f", "image2pipe", "pipe:1"],
                         deadline, seconds)
    if problem:
        return b"", problem
    code, out, err = done
    if code != 0:
        _log("ffmpeg", err, path)
        return b"", None
    return out, None


def _run(args, deadline, seconds):
    """((exit code, stdout, stderr), None), or (None, problem): `args` run with whatever is left before
    `deadline`, both pipes drained together, killed with its tree when the time is up."""
    left = deadline - time.monotonic()
    if left <= 0:
        return None, TOO_LONG % seconds
    try:
        child = processes.start(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as problem:
        return None, "ffmpeg could not be started: %s." % (problem.strerror or problem)
    try:
        out, err = child.communicate(timeout=left)
    except subprocess.TimeoutExpired:
        processes.kill_tree(child.pid)
        try:
            child.communicate(timeout=KILL_WAIT)
        except subprocess.TimeoutExpired:
            logger.warning("A killed %s has not ended after %s s; it is left to end when the system lets it.",
                           args[0], KILL_WAIT)
        return None, TOO_LONG % seconds
    return (child.returncode, out, err), None


def _log(program, err, path):
    said = [line for line in err.decode("utf-8", "replace").splitlines() if line.strip()]
    logger.info("%s could not read a video: %s", program, unnamed(said[-1], [path]) if said else "it said nothing")
