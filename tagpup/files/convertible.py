"""What a clip needs before a browser plays it: the one place that says a browser cannot play one, and why.

    plan = convertible.plan_for(".mov", "mpeg4", "pcm_mulaw")   # Plan(action=REENCODE, reason="This video's picture ...")

Measured in Chrome 156 on the owner's PC (2026-11-09): H.264 with AAC, and H.264 with PCM (pcm_s16le, pcm_mulaw) in a
.mov, play picture and sound; HEVC plays with the HEVC extension the owner has; MPEG-4 Part 2 (mpeg4) in a .mov plays
its sound and shows no picture, with no error; .wmv, .mpg, .avi have no player at all. So:

  * an older format (.wmv, .mpg, .mpeg, .avi, .mod: a container no browser opens) is REENCODE, whatever it holds;
  * a picture in PLAYABLE_VIDEO with a sound in PLAYABLE_AUDIO (or none) plays: NOTHING, in a .mov as in an .mp4 (no
    file is converted because it is not an .mp4: owner, 2026-11-09);
  * a picture that plays with a sound that does not is REMUX_AUDIO: the picture copied, the sound made AAC;
  * any other picture is REENCODE (H.264 CRF 23 and AAC, when the converter is built).

Pure: codec names in (ffprobe's, tagpup.files.video_frames.streams), a Plan out; nothing is read or run. A `reason` names
no file, only the format: a file's name can hold a person's name. Remuxing a playable picture out of an older container
is not a plan: an older extension is REENCODE by the owner's rule, so no input makes it.
"""
from typing import NamedTuple

#: The clip plays as it is.
NOTHING = "NOTHING"
#: Its picture plays and its sound does not: the picture is copied into a new file, the sound made AAC.
REMUX_AUDIO = "REMUX_AUDIO"
#: Its picture does not play, or its container does not open: the whole clip is made H.264 and AAC.
REENCODE = "REENCODE"

#: The containers a browser opens (Chrome, measured): anything else of tagpup.files.images.VIDEO_TYPES is older.
OPENED = frozenset({".mp4", ".m4v", ".mov"})

#: The pictures a browser plays, by ffprobe's codec name (HEVC with the extension the owner has).
PLAYABLE_VIDEO = frozenset({"h264", "hevc", "vp9", "av1"})

#: The sounds a browser plays, by ffprobe's codec name; any PCM (pcm_s16le and pcm_mulaw measured) is one.
PLAYABLE_AUDIO = frozenset({"aac", "mp3", "opus", "flac"})

CONVERT = "it plays here once it is converted (Convert to play)."


class Plan(NamedTuple):
    """What a clip needs (`action`: NOTHING, REMUX_AUDIO or REENCODE) and the sentence that says why (`reason`)."""
    action: str
    reason: str


def plays_sound(audio):
    """Does a browser play a sound of codec `audio` (None: the clip has none)?"""
    return audio is None or audio in PLAYABLE_AUDIO or str(audio).startswith("pcm_")


def plan_for(extension, video, audio):
    """The Plan of a clip whose file ends in `extension` and holds a picture of codec `video` and a sound of codec
    `audio` (None for none), as ffprobe names them; `extension` with its dot, as os.path.splitext gives it."""
    extension = extension.lower()
    if extension not in OPENED:
        return Plan(REENCODE, "This video is a %s file, an older format browsers do not play: %s" % (extension, CONVERT))
    if video not in PLAYABLE_VIDEO:
        return Plan(REENCODE, "This video's picture is %s, a format browsers do not play (its sound may play without "
                              "it): %s" % (video, CONVERT))
    if not plays_sound(audio):
        return Plan(REMUX_AUDIO, "This video's sound is %s, a format browsers do not play: its picture plays, and %s"
                                 % (audio, CONVERT.replace("it plays", "its sound plays")))
    if video == "hevc":
        return Plan(NOTHING, "This video is HEVC: it plays in this browser once Windows has the HEVC Video Extensions.")
    return Plan(NOTHING, "This video's picture and sound are formats browsers play: its file may be damaged.")
