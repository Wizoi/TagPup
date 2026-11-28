"""Reading and writing the fields a journaled bulk edit changes in a photo file.

A bulk edit records, for each file, the fields it found and the fields it leaves
(tagpup.services.file_changes; docs/ARCHITECTURE.md, phase 7.5): its keywords, its
captions, its dates, its identity. This reads those fields as the journal keeps them --
each a list of texts (tagpup.core.fields.field_values) -- and writes them, forward, again
after a crash, or back in an undo, through the one writer, so a file is written the same
way whichever of the three it is.

A field written with an empty value is cleared: ExifTool reads an empty list as "no
change", so a cleared field is an explicit '-FIELD=' (tagpup.core.fields), in the one
command that sets the rest. It was a second command, and a crash between the two left a
file holding neither what it held nor what it was to hold (docs/findings.md, #271). The
photo's identity goes in that command too (#282).

A struct field (tagpup.core.fields.STRUCT_FIELDS: a bake's regions) is one text, read with -struct and made the canonical
text of what ExifTool answers (tagpup.files.bake.region_text), written whole as that text and cleared whole. TagPup's own
bake fields are written only by a session that knows its namespace: _new_session(), which every Fields opens (opened).

**The picture check** (#2377; DECISIONS 2026-11-03). Photo files rewritten through this PC had bytes of their picture
data changed, a few at a 64-byte stride, after an ordinary write. So ExifTool never writes over a photo: it writes the new
file beside it (`-o`, at exiftool_session.scratch_of), its picture bytes -- a JPEG's from its first scan to its end -- are
compared with the photo's, and only when they are equal is the new file moved over the photo (os.replace of a file beside
it, on its volume: a move, never a copy). A new file whose picture differs is removed and the write raises PictureChanged,
the photo untouched. What it does not catch: a byte changed in the metadata ExifTool rewrote (before the first scan), which
only the read back of the fields finds; a fault below the SMB client's cache, since the new file is read back through it
(a warm re-read of a share's file is a few ms against hundreds cold: #2388). A TIFF or PNG (19 photos of the live
libraries) is written unchecked, one log line each (DECISIONS 2026-11-02). With `-o` ExifTool does not copy a file's
attributes: a photo marked hidden or system loses the mark at its write (#2389).

**A patch in place** (DECISIONS 2026-11-02, "THE JPEG PATCH IS BUILT WITH THE VIDEOS'"; the own writer, on since 4d of
docs/PLAN_OWN_METADATA.md). A journaled write (one given `saving`) of a JPEG whose new fields fit its old EXIF, IPTC and
XMP segments writes only those bytes (tagpup.files.in_place.patch_jpeg, PATCH_IN_PLACE). The new segments are TagPup's
own writer's (tagpup.files.segment_edits.new_header), given the journal's values by field, which writes a keyword or
caption save into the XMP copy alone and answers the copies it left (fields.XMP_ONLY). What does not fit, or what the
own writer refuses (a field it does not write, an XMP packet in IFD0, a segment an editor cannot read: segment_edits),
is ExifTool's rewrite as above; so is every write given no `saving` (a just-look
photo's, the shadow comparison's ExifTool side). The picture is never read or written by a patch; the journal checks
the bytes it changed, and the fields read back, before the file is done (tagpup.services.file_changes._verified), and
reads no other byte of the file.

**A clip** (DECISIONS 2026-11-05, M7). A clip's file is never ExifTool's to write: a journaled save of its tags, title,
name or Date Taken (fields.CLIP_FIELDS) patches its XMP alone (tagpup.files.in_place.patch_clip, its packet segment_edits.new_packet's),
on since the owner's trial on 20 copies (DECISIONS 2026-11-07, item 4); every other write of a clip is refused before a byte (a Refused, its sentence
the owner's), and so is one the patch cannot place (images.CLIP_NOT_PATCHED). Its Date Taken is written into its QuickTime
headers too, in the same patch (fields.CLIP_HEADER_DATES; DECISIONS 2026-11-13), never without its XMP date beside it
(clip_refused): the library reads a clip's XMP date first, so it never reads a header TagPup wrote. Those three fields are
read by TagPup's own walk of the clip, every box of each kind (_read_headers): ExifTool answers one box of each, and a
write's before, its verify and its undo are of every box (#3004).
"""
import contextlib
import json
import logging
import os

from exiftool.exceptions import ExifToolNotRunning

from tagpup.core import dates, fields, paths
from tagpup.core.result import Refused
from tagpup.files import (bake, clip_patch, containers, exiftool_session, images, in_place, lock_owners,
                          metadata, segment_edits, shares)

logger = logging.getLogger(__name__)

#: What ExifTool warns of a field it was given and did not write, in a command it reports as a success: a struct's field
#: it does not know ("is not a field of"), one in a namespace it cannot write ("is not a writable XMP tag"), TagPup's own
#: in a process started without its configuration ("is not defined"), and a photo whose XMP segment holds no packet
#: ("Invalid XMP": all NUL bytes on 3 photos of photo_index, #2349), where ExifTool writes nothing. Only these: 119 of 120 photos read carry a minor
#: validation warning of their own, which costs a write nothing.
NOT_WRITTEN = ("is not defined", "is not a field of", "is not a writable XMP tag", "Invalid XMP")

#: What ExifTool says a file is. A photo's starts with "image/", a clip's with "video/".
MIME_TYPE = "File:MIMEType"

#: Photos read in one ExifTool command.
READ_BATCH = 200

#: What a whole read for the index asks beside METADATA_FIELDS (Fields.whole's `bakes`): a bake's fields, kept out of the
#: record's raw_metadata (the turn of its regions is the decode's: tagpup.services.bake_reading), and the options they are
#: read under -- `-struct`, without which the regions are answered as flattened lists (tagpup.files.bake); no field of
#: METADATA_FIELDS is a structure, and each is answered alike under it (tests/test_an_index_reads_a_bake.py).
BAKE_FIELDS = tuple(bake.FIELDS)
BAKE_PARAMS = ("-struct",)

#: The options of every write: ExifTool told that an objection it calls minor does not stop the write (#2009). A photo
#: whose XMP holds descriptions under two rdf:about attributes (a phone's Extended XMP beside another program's packet)
#: is refused without it, and written with it, each description under the first about and nothing else changed; a
#: corrupt packet is refused with it as without it, and its warnings of a field not written (NOT_WRITTEN) are the same.
#: It also stops ExifTool cutting an IPTC value to IPTC's length, so a write cuts it first (fields.as_written). Never on
#: a read: it changes what ExifTool answers (DECISIONS 2026-10-27). Never -overwrite_original: ExifTool writes the new
#: file beside the photo (`-o`), and the picture check moves it over the photo (_landed).
WRITE_OPTIONS = ("-m",)

#: Whether a journaled write of a JPEG is patched in place by TagPup's own writer when its fields fit (in_place.patch_jpeg,
#: segment_edits.new_header; the owner's, 4d of docs/PLAN_OWN_METADATA.md, 2026-11-16); False, every write is ExifTool's
#: rewrite. Turned off only by the tests' stand-in for ExifTool (tests/fake_exiftool.py), a table of fields that holds no
#: bytes to patch.
PATCH_IN_PLACE = True

#: Seconds the picture check may take to read the photo and its new file (shares.bounded): a share that does not answer
#: in it is away, and the write fails as a stalled share's does, the photo as it was.
CHECK_SECONDS = 30.0

#: The bytes the picture check reads at a time.
BLOCK = 1 << 20

#: The first bytes of a JPEG; a photo starting otherwise (a TIFF, a PNG) is written unchecked.
JPEG_START = b"\xff\xd8\xff"

#: Why a write is refused: the photo is a JPEG whose picture cannot be found (no scan before its end), so a new file
#: could not be checked against it.
NO_PICTURE = ("it starts as a JPEG but holds no picture data that could be found (no scan before its end), so a write "
              "of it could not be checked; nothing was written")

#: Why a write failed whose checked new file could not be moved over the photo (a photo marked read-only, copied off a
#: disc; one open in another program): the OS's own words, then this. It names no path: a photo is named by its id.
NOT_MOVED = ("its new file could not be put in place of it (%s): the photo may be marked read-only or open in another "
             "program; the new file was removed and the photo is as it was")

#: Why a patch in place failed whose photo's folder did not answer (in_place.Away) in in_place.SECONDS: the file is
#: settled by its bytes (file_changes._by_bytes).
PATCH_NOT_ANSWERED = "its folder did not answer the patch of its fields in %d s; it is settled by its bytes the next time"


class Unreadable(Exception):
    """A photo whose fields could not be read: missing, locked, or not a photo."""


class NotAnswered(Unreadable):
    """A photo of a batch ExifTool did not answer for in its deadline (a share gone away, a disk that stopped): nothing is
    known of the file, not even that it cannot be read, so a reader leaves it unread."""


class PictureChanged(Exception):
    """The new file ExifTool wrote of a photo holds picture bytes other than the photo's (the picture check, #2377): it
    was removed, and the photo is as it was. Every writer of a photo writes once more (written_once_more)."""


#: Why a write failed whose new file's picture differed from the photo's in both of its tries (written_once_more).
CHANGED_TWICE = "its write changed the picture's bytes twice; the new file was discarded each time and the photo is as it was"

#: Why a whole change was refused before anything of it was recorded (Fields.ready_to_write): no ExifTool could be started
#: to read and write its files. It names no path; the log says what the start raised.
NOT_STARTED_TO_WRITE = "ExifTool could not be started, so nothing was written; the server's log says why"


def _new_session(exiftool_path):
    """The ExifTool session of an operation (opened): one that knows TagPup's namespace (bake.config_path), since a bake's
    fields are named by a write, an undo and a read alike. Looked up at call time, so a test standing in for
    ExifToolSession reaches it."""
    return exiftool_session.ExifToolSession(executable=exiftool_path, config_file=bake.config_path())


def _value(row, field):
    value = row.get(fields.read_key(field))
    if fields.is_struct(field) and value is not None:
        try:
            return [bake.region_text(value)]
        except ValueError as error:
            raise Unreadable("Its %s could not be read: %s" % (field, error)) from None
    return fields.field_values(value)


def _held(row, wanted):
    kind = row.get(MIME_TYPE)
    if kind is not None and not str(kind).startswith(("image/", "video/")):
        # A file of text read as TXT answers no fields, and would read as a photo holding
        # nothing (docs/findings.md, #46). A clip is read as a photo is: the indexer keeps it
        # as a row of kind 'video' (images.kind_of); refusing it left every clip unread.
        raise Unreadable("Not a photo or a video ExifTool can read (%s)" % kind)
    return {field: _value(row, field) for field in wanted}


def _read(et, photo_paths, wanted, also=(), records=None, stop=None, params=()):
    """{paths.key(path): {field: its values}} of each photo in `photo_paths`, for the
    fields in `wanted`; an Unreadable in place of the fields of a photo that could not be
    read (NotAnswered, of a photo ExifTool did not answer for). Read in batches; a batch ExifTool refuses is read again a
    photo at a time, so one bad file does not cost the others. A command ExifTool does not answer in its deadline (a share
    gone away) ends the read: that photo, and every photo not read yet, is NotAnswered, so one read waits one deadline. The one batch reader: an
    operation's reads (Fields.read, Fields.whole) and the write sessions' (tagpup.services.file_changes) go through it. `also` are fields asked as well, answered only
    in `records`, when given: {paths.key(path): ExifTool's record} of each photo read.

    `stop`, a threading.Event: once it is set no more batches are read, and the photos not reached are left out of the
    answer (a batch asked to stop while it plans, tagpup.services.file_changes). `params`, options of each read command
    (-struct, for the bake's region fields: Fields.whole).
    A struct field in `wanted` is read with -struct, which answers a list of one as a list: `also` beside one is refused,
    since its records would not be in the indexer's shape.

    The file is not scanned to its end first: a JPEG whose picture stops short takes a write of its fields, and its
    picture data is left as it was (DECISIONS 2026-10-15); a file ExifTool cannot write fails that write."""
    wanted = list(dict.fromkeys(wanted))
    asked = list(dict.fromkeys(wanted + list(also) + [MIME_TYPE]))
    if records is None:
        records = {}
    params = list(params)
    if any(fields.is_struct(field) for field in wanted):
        if also:
            raise ValueError("a struct field is read with -struct, and the fields asked beside it would not be read in the "
                             "indexer's shape")
        if "-struct" not in params:
            params.append("-struct")
    options = {"params": params} if params else {}
    found = {}
    photo_paths = list(photo_paths)

    def stalled(error, left):
        # ExifTool did not answer (its process was killed): a share gone away, a disk that stopped. Each photo read again
        # would wait its own deadline -- 25 photos of a bad share were two hours -- so nothing more is read; one bad path
        # ExifTool refuses (below) is read again singly.
        said = "ExifTool did not answer in %s s" % getattr(et, "timeout", "time")
        left = [one for one in left if paths.key(one) not in found]
        logger.warning("%s; %d photo(s) were not read: %s", said, len(left), error)
        for one in left:
            found[paths.key(one)] = NotAnswered(said)
        return _read_headers(found, photo_paths, wanted)

    for start in range(0, len(photo_paths), READ_BATCH):
        if stop is not None and stop.is_set():
            break
        batch = photo_paths[start:start + READ_BATCH]
        try:
            rows = et.get_tags(batch, tags=asked, **options)
        except (exiftool_session.ExifToolTimeout, ExifToolNotRunning) as error:
            return stalled(error, photo_paths[start:])
        except Exception:
            rows = None
        if rows is not None:
            for row in rows:
                source = row.get("SourceFile")
                if not source:
                    continue
                try:
                    found[paths.key(source)] = _held(row, wanted)
                    records[paths.key(source)] = row
                except Unreadable as e:
                    found[paths.key(source)] = e
            missed = [p for p in batch if paths.key(p) not in found]
        else:
            missed = batch
        for one in missed:
            try:
                rows = et.get_tags([one], tags=asked, **options)
                found[paths.key(one)] = _held(rows[0] if rows else {}, wanted)
                records[paths.key(one)] = rows[0] if rows else {}
            except (exiftool_session.ExifToolTimeout, ExifToolNotRunning) as error:
                # A batch refused, then a photo of it read singly not answered (#1490): the share went away.
                return stalled(error, missed + photo_paths[start + READ_BATCH:])
            except Exception as e:
                found[paths.key(one)] = e if isinstance(e, Unreadable) else Unreadable(str(e))
    return _read_headers(found, photo_paths, wanted)


def opened(exiftool_path):
    """The face for one operation (a batch of the index, a refresh, a page's look, a journaled write, a bake run, a
    settle, an undo): use it in a `with` block, or close() it. Its ExifTool, which knows TagPup's namespace (_new_session():
    every read and write gains the bake's configuration), is started at the first call that needs one, never here:
    opening never raises. One process for the whole operation; a test standing in for ExifToolSession reaches it
    (_new_session looks the class up when it starts)."""
    return Fields(exiftool_path)


class Fields:
    """The reads and writes of one operation, over one ExifTool started at its first call (opened). Dead for good after a
    timeout for ONE call: a call whose command is not answered in its deadline leaves that file and every later file of
    the call unread (read), and the next call starts ExifTool again (ExifToolHelper's auto-start) and answers: a
    long-lived Fields never leaves every later photo unread after one share hiccup. Inside together(), for the whole
    block."""

    def __init__(self, exiftool_path):
        self.exiftool_path = exiftool_path
        self._stack = contextlib.ExitStack()
        self._et = None
        # Inside together(): the session that stays dead after a timeout (StaysDead), made at the block's first command.
        self._together, self._stays = False, None

    def __enter__(self):
        return self

    def __exit__(self, *problem):
        self.close()
        return False

    def close(self):
        self._et = None
        self._stack.close()

    def _session(self):
        """The started session, started now when none is; raises what the start raised (the next call tries again).
        Inside together(), the one that stays dead from its first timeout to the block's end."""
        if self._et is None:
            self._et = self._stack.enter_context(_new_session(self.exiftool_path))
        if not self._together:
            return self._et
        if self._stays is None:
            self._stays = exiftool_session.StaysDead(self._et)
        return self._stays

    @contextlib.contextmanager
    def together(self):
        """One deadline across the commands of one operation: from the first command not answered in its deadline (a
        share gone away) to the block's end, every command fails at once (NotAnswered of every file after it), so the
        operation waits one deadline, never one a file, nor a second for the read that settles the file whose write timed
        out (#1927). One journaled write (file_changes.write_fields), one settle, one bake run; never a span that holds
        ffmpeg (Convert opens a Fields an operation). Inside another together(), the outer block's; after the block, the
        next command starts ExifTool again."""
        if self._together:
            yield self
            return
        self._together = True
        try:
            yield self
        finally:
            self._together, self._stays = False, None

    @property
    def dead(self):
        """Has a command of the together() block this is in gone unanswered in its deadline?"""
        return self._stays is not None and self._stays.dead

    def ready_to_write(self):
        """Raise Refused(NOT_STARTED_TO_WRITE) when no ExifTool can be started for a change: called before anything of
        the change is recorded (file_changes.write_fields), so a change whose files ExifTool cannot read or write is
        refused whole, never planned and failed file by file, nor a patch written that nothing reads back. The ExifTool
        it starts is the change's."""
        try:
            self._session()
        except Exception as error:
            logger.error("Could not start ExifTool to write a change of photo files: %s", error)
            raise Refused(NOT_STARTED_TO_WRITE) from None

    def read_one(self, photo_path, wanted, *, whole=False):
        """The fields `wanted` of one file, as read gives them; raises Unreadable (NotAnswered, of one not answered in its
        deadline). `whole`, (them, its raw_metadata in the shape the index records it, metadata.raw_metadata): the file
        read whole beside them, Unreadable too when ExifTool reported an error reading it (metadata.READ_ERROR_KEYS)."""
        et = self._session()
        if not whole:
            return _read_one(et, photo_path, wanted)
        records = {}
        held = _read(et, [photo_path], wanted, fields.METADATA_FIELDS, records).get(paths.key(photo_path))
        record = records.get(paths.key(photo_path))
        if isinstance(held, Exception):
            raise held
        if held is None or record is None:
            raise Unreadable("ExifTool answered nothing for it")
        said = "; ".join(str(record[key]) for key in metadata.READ_ERROR_KEYS if record.get(key))
        if said:
            raise Unreadable("ExifTool reported an error reading it: %s" % said)
        return held, metadata.raw_metadata(record)

    def read_back(self, photo_path, wanted, *, whole=False):
        """What a write just made of the file (before it is done), or what a settle finds of one: read_one's answer, read
        by a reader that did not write it, after the write path's own check of the bytes it wrote (in_place.held). Today
        ExifTool's, whichever wrote (the own patch or ExifTool's rewrite); a format's own reader answers here once the
        format is moved (docs/plans/own-metadata-library.md, Q10)."""
        return self.read_one(photo_path, wanted, whole=whole)

    def write(self, photo_path, values, *, saving=None, scratch_written=None, again=None):
        """`values` into the file (write: the own patch where it fits, given `saving`, else ExifTool's rewrite beside the
        photo, its picture checked, then moved). `again()`, given, is called before a second write when the first's new
        file's picture differed, and that one is made (written_once_more). Returns the fields left holding what they
        held. A clip is never ExifTool's to write (_write): its write starts no session."""
        options = {name: value for name, value in (("saving", saving), ("scratch_written", scratch_written))
                   if value is not None}
        et = None if images.is_clip(photo_path) else self._session()
        if again is None:
            return _write(et, photo_path, values, **options)
        return _written_once_more(et, photo_path, values, again, **options)

    def left_as_they_were(self, values, photo_path):
        """The fields of `values` a journaled write of `photo_path` leaves holding what they held (left_as_they_were)."""
        return left_as_they_were(values, photo_path)

    def read(self, photo_paths, wanted, *, also=None, records=None, stop=None):
        """{paths.key(path): {field: its values}} of each of `photo_paths`, for the fields in `wanted`, through the one
        batch reader (read): an Unreadable in place of a photo ExifTool refused, a NotAnswered of one it did not answer
        for in its deadline (it and every photo after it in this call). `also`, fields asked as well, answered only in
        `records` ({paths.key(path): ExifTool's record}) when given: kept until the reads answer by field alone
        (docs/plans/own-metadata-library.md, L4). `stop`, a threading.Event, read's (a batch asked to stop while it plans).

        A start that fails is RAISED, never answered as every photo Unreadable: a reader that took that answer would
        decide something wrong of each (sync's pairing would pair nothing, a rename would read no caption), as each read
        site's own `with ExifToolSession(...)` raised before. `whole` answers it, NOT_STARTED, since the index records
        what it reads. No photos: no ExifTool started."""
        photo_paths = list(photo_paths)
        if not photo_paths:
            return {}
        return _read(self._session(), photo_paths, wanted, also or (), records, stop=stop)

    def whole(self, photo_paths, *, people=None, bakes=False):
        """A record of each of `photo_paths`, in order, as the index records it (tagpup.files.metadata.structure), read
        through the one batch reader (read: a batch ExifTool refuses is read again a photo at a time, one it does not
        answer is not retried).

        A photo whose read failed is opened once (_why_unread): held open by another program (an editor; ExifTool says
        "File not found" of it) or on a share that does not answer, it is left UNREAD, as is a photo ExifTool did not
        answer for (a share gone away), or whose stat failed, or that no ExifTool could be started to read: its record has
        no stamp (mtime and size None) and says why in `unread` (metadata.HELD, NOT_ANSWERED, NO_STAT, NOT_STARTED), so
        nothing records it as read and the next index reads it; a start that fails is never an exception out of here.
        Any other photo ExifTool refuses (or that is not a photo) is a lasting failure: recorded holding nothing, with
        `read_error`, stamped, so a file that cannot be read is not read again every run, and its row keeps the fields it
        held (store.photos.record_indexed). `people` is what the library's tag tree says about people, read
        once for the batch; without it, only the usual face roots name anybody.

        `bakes` (the indexer's read) asks BAKE_FIELDS as well, in the same command, and puts what the file answers of them
        in the record's "baked" ({} for a file holding none), never in its raw_metadata."""
        photo_paths = list(photo_paths)
        if not photo_paths:
            return []
        # Each file's stamp before the read.
        stamps = [metadata.stamp_before_read(path) for path in photo_paths]
        try:
            et = self._session()
        except Exception as e:
            logger.error("Could not start ExifTool to read %d file(s): %s", len(photo_paths), e)
            why = str(e) or type(e).__name__
            return [dict(metadata.empty(path, why, (None, None)), unread=metadata.NOT_STARTED) for path in photo_paths]
        records = {}
        found = _read(et, photo_paths, [], list(fields.METADATA_FIELDS) + list(BAKE_FIELDS if bakes else ()), records,
                     params=BAKE_PARAMS if bakes else ())
        results = []
        for path, stamp in zip(photo_paths, stamps):
            held, record = found.get(paths.key(path)), records.get(paths.key(path))
            if isinstance(held, NotAnswered):
                results.append(dict(metadata.empty(path, held, (None, None)), unread=metadata.NOT_ANSWERED))
            elif stamp == (None, None):
                results.append(dict(metadata.empty(path, held or "its stat failed", stamp), unread=metadata.NO_STAT))
            elif isinstance(held, Exception) or not record:
                results.append(metadata.empty(path, held or "ExifTool answered nothing for it", stamp))
            else:
                # What the reader asked to tell a photo from another kind of file is not the photo's metadata, nor is a
                # bake.
                baked = {key: record[key] for key in BAKE_FIELDS if key in record} if bakes else {}
                record = {key: value for key, value in record.items()
                          if key != MIME_TYPE and not (bakes and key in BAKE_FIELDS)}
                results.append(metadata.structure(path, record, people, stamp))
                if bakes:
                    results[-1]["baked"] = baked
            if "read_error" in results[-1] and "unread" not in results[-1]:
                why = _why_unread(path, stamp)
                if why:
                    results[-1] = dict(metadata.empty(path, results[-1]["read_error"], (None, None)), unread=why)
                else:
                    logger.error("Unreadable metadata, an existing row is kept as it was: %s (%s)", path, results[-1]["read_error"])
        return results


def _why_unread(path, stamp):
    """Why the file at `path`, stamped `stamp`, whose read failed is left unread, from one open of it now
    (shares.read_span: bounded on a share): metadata.HELD when another program holds it (lock_owners.looks_held),
    NOT_ANSWERED when its share does not answer; None when it opens, or fails otherwise: a lasting failure."""
    try:
        shares.read_span(path, 0, 1, stamp)
    except shares.Unavailable:
        return metadata.NOT_ANSWERED
    except OSError as error:
        return metadata.HELD if lock_owners.looks_held(error) else None
    return None


#: The oracle's whole read of a file (its warnings are READER_READ's). The session's own common -G
#: (exiftool_session.DEFAULT_COMMON_ARGS) is given after each command's and wins over a -G0:1 in it, naming IFD0's and
#: IFD1's Orientation alike, so the oracle has a session of its own whose common arguments are -n alone. Keys read so
#: (ExifTool 13.59): "EXIF:IFD0:Orientation", "XMP:XMP-dc:Subject"; a group whose family 1 is its family 0 is named once,
#: "IPTC:Keywords", and a second APP13's "IPTC:IPTC2:Keywords". The file system's group (File:System: the folder, the
#: file's name, size and dates) is left out: it is no field of the file, the shadow comparison sets the File group aside
#: (metadata_check.SET_ASIDE), and an answer naming a folder or a file's dates is never kept for the tests' replay.
WHOLE_READ = ("-j", "-G0:1", "-a", "-all:all", "--System:all")
#: What ExifTool says of a file as it reads it, every warning and error of its validation: by their copy numbers (family
#: 4), as one key holds one ("ExifTool:Warning", "ExifTool:Copy1:Warning"); -G0:1 answers the first warning alone.
READER_READ = ("-j", "-G0:4", "-a", "-ExifTool:Warning", "-ExifTool:Error", "-validate")
#: The group of what ExifTool says of a file as it reads it, as ExifTool 13.59 names it: "ExifTool:Warning" (a warning,
#: "Value for IFD0 tag 0x011a XResolution overlaps IFD"), "ExifTool:Error", and "ExifTool:Validate" (the counts of errors,
#: warnings and minor warnings, "0 14 0").
READER_SAYS = "ExifTool"


def oracle(exiftool_path):
    """ExifTool's independent read of whole files (Oracle.whole), what the shadow comparison of the two writers judges
    their copies by (tagpup.services.metadata_check). Use it in a `with` block: its ExifTool, which knows TagPup's
    namespace, is started as the block is entered, and a start that fails raises there, before any file is compared."""
    return Oracle(exiftool_path)


class Oracle:
    """ExifTool's whole read of files, every tag of every group (WHOLE_READ) under -n, in a session of its own."""

    def __init__(self, exiftool_path):
        self.exiftool_path = exiftool_path
        self._stack = contextlib.ExitStack()
        self._et = None

    def __enter__(self):
        self._et = self._stack.enter_context(exiftool_session.ExifToolSession(
            executable=self.exiftool_path, config_file=bake.config_path(), common_args=["-n"]))
        return self

    def __exit__(self, *problem):
        self._et = None
        self._stack.close()
        return False

    def whole(self, photo_paths):
        """{paths.key(path): {tag: value}} of ExifTool's whole read of each (WHOLE_READ), with what it says of each
        (READER_READ's READER_SAYS tags). A file the second read leaves out (held a moment by another program) is raised,
        never read as one that says nothing."""
        rows = {paths.key(row.get("SourceFile", "")): row
                for row in json.loads(self._et.execute(*WHOLE_READ, *photo_paths) or "[]")}
        answered = set()
        for said in json.loads(self._et.execute(*READER_READ, *photo_paths) or "[]"):
            key = paths.key(said.get("SourceFile", ""))
            answered.add(key)
            row = rows.setdefault(key, {})
            row.update((tag, value) for tag, value in said.items() if tag.split(":")[0] == READER_SAYS)
        if set(rows) - answered:
            raise OSError("ExifTool answered the whole read of a copy and not the read of what it says of it")
        return rows

def _read_headers(found, photo_paths, wanted):
    """`found`, read's answer, each clip's QuickTime header dates of `wanted` (fields.CLIP_HEADER_DATES) put in place of
    ExifTool's: TagPup's own walk's, the texts of every box of each kind in file order (in_place.headers_held; DECISIONS
    2026-11-13, #3004), since ExifTool answers one box of each and a write sets, verifies and gives back every box. The
    indexer, asking them only `also`, keeps ExifTool's record. A clip the walk does not read (an old format: a .wmv or an
    .avi, whose date save the patch refuses) keeps ExifTool's values; a clip whose share does not answer is NotAnswered,
    and so is every clip after it, unread (one read waits one deadline); one whose headers could not be read, Unreadable."""
    asked = [field for field in fields.CLIP_HEADER_DATES if field in wanted]
    if not asked:
        return found
    away = None
    for one in photo_paths:
        key = paths.key(one)
        held = found.get(key)
        if not isinstance(held, dict) or not images.is_clip(one):
            continue
        if away is not None:
            found[key] = NotAnswered(away)
            continue
        try:
            headers = in_place.headers_held(one)
        except in_place.Away:
            away = "its folder did not answer the read of its QuickTime headers in %d s" % in_place.SECONDS
            found[key] = NotAnswered(away)
            continue
        except (OSError, ValueError) as error:
            found[key] = Unreadable("its QuickTime headers could not be read: %s"
                                    % (getattr(error, "strerror", None) or error))
            continue
        if headers is not None:
            held.update({field: headers[field] for field in asked})
    return found


def _read_one(et, photo_path, wanted):
    """The fields of one photo, as read() gives them; raises Unreadable."""
    held = _read(et, [photo_path], wanted).get(paths.key(photo_path))
    if isinstance(held, Exception) or held is None:
        raise held or Unreadable("ExifTool answered nothing for it")
    return held


def _write(et, photo_path, values, saving=None, scratch_written=None):
    """Write `values` ({field: value}) into a photo, in one ExifTool command, clearing a
    field whose value is empty, telling ExifTool an objection it calls minor does not stop it (WRITE_OPTIONS: a photo
    with two rdf:about attributes in its XMP takes a write; DECISIONS 2026-10-27). Raises
    when ExifTool fails. ExifTool writes the new file beside the photo, which the picture check moves over it or removes
    (_landed; the module's docstring): PictureChanged, the photo untouched. `scratch_written()`, the journal's, is called
    once the new file is written and before it is checked (file_changes.STEPS). Nothing else beside the photo is deleted
    here (its own new file, when the picture differs or the move fails, is the write's): the scratch file an
    interrupted write left is removed only by file_changes, by the owner's rules (#2180); an
    old one, of a write before the picture check that failed after deleting the original, is the photo's only copy (#1410).

    A struct field's value is its one text (bake.region_text); an empty one clears the structure whole. TagPup's own fields
    (bake.NAMESPACE) are written only through a session opened by _new_session(): ExifTool would refuse them, by a warning,
    under any other, so another is refused here, by a ValueError, before anything is written. A write of a struct field
    or of TagPup's own that ExifTool warns left a field unwritten (NOT_WRITTEN) raises RuntimeError before the photo's
    first byte: ExifTool's new file is discarded, and the photo holds what it held (the own writer writes neither: such a
    field is no patch's, segment_edits). A text holding a line break is sent as ExifToolSession sends it (exiftool_session.CSTR).

    `saving(record, before)` is the journal's (tagpup.services.file_changes): given, a JPEG whose fields fit is patched
    in place by the own writer (the module; PATCH_IN_PLACE; not while ExifTool's new file is beside the photo), which calls it with its record (tagpup.files.in_place.record) and writes no
    byte until it returns, nor at all when it raises. ExifTool's rewrite never calls it: it leaves the file it held or the
    one it was to hold, whole. A JPEG patch is at most three segments of 64 KB, with its record under
    file_journal.PATCH_CAP, so the journal's PatchTooLarge (a Refused) is not met here; a clip's may be (_clip_written). A patch whose photo's share does not answer raises NotAnswered, the file
    settled by its bytes (file_changes._by_bytes).

    Returns the fields of `values` the write left holding what they held before (fields.XMP_ONLY: a keyword or caption
    save the own writer patches into the XMP alone), even when the patch had no byte to write; file_changes records the
    file as fields.as_patched says. () for every other write, which writes them all.

    A clip (images.is_clip) is never ExifTool's to write: _clip_written patches its XMP or refuses it."""
    if images.is_clip(photo_path):
        return _clip_written(photo_path, values, saving)
    own = [field for field in values if str(field).startswith(bake.NAMESPACE + ":")]
    if own and getattr(et, "config_file", None) != bake.config_path():
        raise ValueError("%s is written only by a session that knows TagPup's namespace (field_values.opened); this one "
                         "does not" % own[0])
    params, clear = {}, []
    for field, value in values.items():
        texts = fields.as_written(field, value)
        named = fields.WRITTEN_AS.get(field, field)
        if fields.is_struct(field) and len(texts) > 1:
            raise ValueError("%s is one structure, one text, not %d" % (field, len(texts)))
        if not texts:
            clear.append("-%s=" % field)
        elif fields.read_key(field) in fields.LIST_FIELDS or len(texts) > 1:
            params[named] = list(texts)
        else:
            params[named] = texts[0]
    if not params and not clear:
        return ()
    checked = own or any(fields.is_struct(field) for field in values)
    scratch = exiftool_session.scratch_of(photo_path)
    # ExifTool's new file beside the photo is the one sign another process's rewrite is under way: its os.replace would land
    # over a patched file (the tag lost), so a patch is made only when none is there, and the rewrite settles that one.
    if PATCH_IN_PLACE and saving is not None and shares.stamp_of(scratch) is None:
        # The journal's values, by field (fields.EDITED_AS), never ExifTool's params by the name it is written as.
        def new_header(layout, header):
            return segment_edits.new_header(layout, header, values)
        try:
            stale = in_place.patch_jpeg(photo_path, new_header, saving)
        except in_place.Away:
            raise NotAnswered(PATCH_NOT_ANSWERED % in_place.SECONDS) from None
        if stale is not None:
            return stale
    _command(et, photo_path, scratch, params, clear)
    if scratch_written is not None:
        scratch_written()
    try:
        _refuse_dropped(et, photo_path, checked)
    except RuntimeError:
        try:
            os.remove(scratch)
        except FileNotFoundError:
            pass            # ExifTool wrote none ("Invalid XMP")
        except OSError as e:
            logger.warning("The new file of a write ExifTool did not write all of could not be removed; it is left: %s", e)
        raise
    _landed(photo_path, scratch)
    return ()


def writes(path):
    """What a journaled write may change of the file at `path`, by name: a photo's tags, title, Date Taken, turn and name
    (fields.PHOTO_WRITES); a clip's tags, title, name and Date Taken (fields.CLIP_WRITES). The one
    statement of it: a page's record carries it (tagpup.services.photos.page_record), and the writer (clip_refused) and
    the journal (tagpup.services.file_changes.leave_out_clips) refuse what it leaves out. Whether a clip's folder is held
    is not said here: a clip of a folder the library does not hold is refused at its write (file_only), never in a record
    the folder cache keeps across an Add to the library. Nothing is read of the file."""
    if not images.is_clip(path):
        return fields.PHOTO_WRITES
    return fields.CLIP_WRITES


def clip_refused(path, values):
    """images.CLIP_NOT_EDITED when a write of `values` to the clip at `path` is refused before the clip is looked at --
    a field outside those writes(path) names (fields.fields_of) -- else None: the
    one rule, asked by _clip_written and, before anything is written, by an undo's rehearsal
    (tagpup.services.file_changes._undoable), so the change it refuses stays applied -- or one naming a QuickTime header
    date (fields.CLIP_HEADER_DATES) without the XMP date the library reads first (DECISIONS 2026-11-13): a header TagPup
    writes always has it beside it; an undo giving back a clip with no XMP date names it, cleared."""
    named = fields.fields_of(writes(path))
    if not set(values) <= named:
        return images.CLIP_NOT_EDITED
    if any(field in values for field in fields.CLIP_HEADER_DATES) and dates.CLIP_XMP_DATE_FIELDS[0] not in values:
        return images.CLIP_NOT_EDITED
    return None


#: Why a clip's Date Taken is refused, nothing written: its QuickTime headers hold no such date (clip_date_refused).
CLIP_DATE_OUTSIDE = ("A video's Date Taken is written into its QuickTime header as well, which holds a date from 1904 to "
                     "early 2040 only; %s is outside that, and nothing was written")
CLIP_DATE_UNSPELLED = ("A video's Date Taken is written to the second (YYYY-MM-DD HH:MM:SS) into its QuickTime header as "
                       "well; %s is not spelled so, and nothing was written")


def clip_date_refused(date):
    """The sentence refusing a clip's Date Taken `date` (fields.date_taken_value's spelling) before anything is read or
    written, else None: its QuickTime headers are written with it (DECISIONS 2026-11-13), and a version 0 box holds no
    date before 1904 or after early 2040 (clip_patch.holds_date). The one check of it: no page or route checks a Date
    Taken; tagpup.services.tagging.save_photo asks it."""
    try:
        held = clip_patch.holds_date(date)
    except ValueError:
        return CLIP_DATE_UNSPELLED % date
    return None if held else CLIP_DATE_OUTSIDE % date


def _clip_written(photo_path, values, saving):
    """write() of a clip: the fields it left as they were (segment_edits.clip_stale_of) once its XMP holds `values`, by a
    patch of its XMP in place (in_place.patch_clip), saved by `saving` before a byte is written. Raises Refused, nothing
    written, for a write that is not the journal's (no `saving`: nothing could settle a
    patch a crash cut short), or one naming a field outside fields.CLIP_FIELDS (images.CLIP_NOT_EDITED); for a clip the
    patch cannot place (images.CLIP_NOT_PATCHED); a clip open in another program (in_place's sentence); a patch more
    than the journal keeps (`saving` raises file_journal.PatchTooLarge, its own sentence). Raises Unreadable
    when it could not be read before a byte was written (NotAnswered: its share did not answer); a write that failed
    after its record was saved raises as it failed, the file settled by its bytes. ExifTool is not asked."""
    if saving is None or clip_refused(photo_path, values):
        raise Refused(images.CLIP_NOT_EDITED)
    saved = []

    def saved_first(record, before):
        saving(record, before)
        saved.append(record)

    # A clip's QuickTime title, freed by a clear or given back by its undo (clip_patch; DECISIONS 2026-11-08).
    title = None
    if fields.CLIP_TITLE in values:
        title = (fields.as_written(fields.CLIP_TITLE, values[fields.CLIP_TITLE]) or [""])[0]
    # A clip's Windows Tags, its Xtra freed by their move into its XMP or given back by its undo (clip_patch;
    # DECISIONS 2026-11-09).
    category = None
    if fields.CLIP_CATEGORY in values:
        category = tuple(fields.as_written(fields.CLIP_CATEGORY, values[fields.CLIP_CATEGORY]))
    # A clip's QuickTime header dates, set beside its XMP date or given back box by box by its undo (clip_patch;
    # DECISIONS 2026-11-13).
    headers = {field: fields.as_written(field, values[field]) for field in fields.CLIP_HEADER_DATES if field in values}
    try:
        stale = in_place.patch_clip(photo_path, lambda old: segment_edits.new_packet(old, values), saved_first,
                                    title=title, category=category, dates=headers)
    except in_place.Away:
        raise NotAnswered(PATCH_NOT_ANSWERED % in_place.SECONDS) from None
    except OSError as error:
        if saved:
            raise
        raise Unreadable("it could not be read or opened for its write (%s); nothing was written"
                         % (error.strerror or error)) from None
    if stale is None:
        raise Refused(images.CLIP_NOT_PATCHED)
    return stale


#: What packet_held answers for a clip holding XMP no patch reads: two XMP boxes, or one it does not know (in_place.UNREAD).
PACKET_UNREAD = in_place.UNREAD


def packet_held(path):
    """The XMP packet the clip at `path` holds, for a conversion to carry whole into its copy (packet_given, its pair;
    tagpup.services.convert), read as a patch reads it (in_place.packet_held): its bytes; None when it holds none, or is
    an old format the box walk does not read; PACKET_UNREAD when it holds XMP no patch reads. Nothing written. Raises
    NotAnswered when its share does not answer, the OSError of the open or a read."""
    try:
        return in_place.packet_held(path)
    except in_place.Away:
        raise NotAnswered("its folder did not answer the read of its XMP in %d s" % in_place.SECONDS) from None


def packet_given(scratch, packet):
    """Put `packet`, the XMP packet a clip held (packet_held, its pair), whole into `scratch`, the copy of it a conversion
    made and has not recorded (tagpup.services.convert), by a patch of its XMP in place (in_place.patch_clip), appended
    in a uuid box at its end: ffmpeg makes it with one moov, at the front, and one sized mdat (clip_convert), which a
    patch always places. Its `saving` is nothing, the one patch saved by no journal: sound only because a scratch
    nothing records yet is settled by its removal (tagpup.services.file_changes.removed_scratch, the scratch rule),
    never by its bytes, so a patch of it cut short needs no record to be put back. Raises NotAnswered when its share
    does not answer, Refused when another program holds it open or, as any clip's write, when the patch cannot place it
    (images.CLIP_NOT_PATCHED, nothing written), the OSError of the open or a write."""
    try:
        stale = in_place.patch_clip(scratch, lambda _old: (packet, ()), lambda _record, _before: None)
    except in_place.Away:
        raise NotAnswered("its folder did not answer the patch of its XMP in %d s" % in_place.SECONDS) from None
    if stale is None:
        raise Refused(images.CLIP_NOT_PATCHED)


def _written_once_more(et, photo_path, values, again, **options):
    """write(et, photo_path, values, **options), made once more when its new file's picture differed (PictureChanged:
    discarded, the photo as it was), for a held photo's write (tagpup.services.file_changes._carry) and a Just-look
    photo's (tagpup.services.file_only) alike (DECISIONS 2026-11-05, #2476). `again()`, the caller's, is called before
    the second write, to log it and count it (the result's `pictures_refused`). A second PictureChanged raises
    PictureChanged(CHANGED_TWICE); any other failure of either write is raised as it is, for the caller to settle.
    Returns what write returns. The photo is not looked at between the two: the picture check compares the second new
    file with the photo as it is then."""
    try:
        return _write(et, photo_path, values, **options)
    except PictureChanged:
        again()
    try:
        return _write(et, photo_path, values, **options)
    except PictureChanged:
        raise PictureChanged(CHANGED_TWICE) from None


def left_as_they_were(values, photo_path):
    """The fields of `values` a journaled write (one given `saving`) of `photo_path` leaves holding what they held: a
    keyword or caption group's copies outside XMP, while its XMP value is not empty, which the own writer's patch leaves
    (segment_edits.stale_of, fields.XMP_ONLY; PATCH_IN_PLACE), and a clip's every copy outside XMP (segment_edits.clip_stale_of: a clip
    is written only by _clip_written, which patches its XMP alone); () when it does not, ExifTool's writes writing
    every field. What the file holds after the write is fields.as_patched of these: file_changes asks it of a plan
    before writing anything, so a file holding that already is not written, journaled or counted (a Save repeated, a tag
    the photo has added again)."""
    if images.is_clip(photo_path):
        return segment_edits.clip_stale_of(values)
    return segment_edits.stale_of(values) if PATCH_IN_PLACE else ()


def _refuse_dropped(et, source, checked):
    """Raise RuntimeError, when the write was `checked`, if ExifTool's last answer says a field it was given was not
    written (_dropped): before the new file it wrote of `source` reaches the photo."""
    dropped = _dropped(et, source) if checked else ()
    if dropped:
        raise RuntimeError("ExifTool did not write all it was given: %s" % "; ".join(dropped))


def _command(et, source, target, params, clear):
    """ExifTool's write of `params` and `clear` from the file at `source` into a new file at `target`, in one command,
    the clears before the values."""
    if params:
        et.set_tags([source], tags=params, params=list(WRITE_OPTIONS) + ["-o", target] + clear)
    else:
        et.execute(*clear, *WRITE_OPTIONS, "-o", target, source)


def _dropped(et, source):
    """The lines of ExifTool's last answer saying a field it was given was not written (NOT_WRITTEN; #1881: a write that
    reported what it attempted, the file holding less than it was given)."""
    return [line.strip() for line in exiftool_session.unnamed(et.last_stderr, [source]).splitlines()
            if any(said in line for said in NOT_WRITTEN)]


def _picture_at(handle):
    """Where the picture of the JPEG open in `handle` starts (containers.Jpeg.image_offset), or None when it has none."""
    layout = containers.read(handle)
    return layout.image_offset if isinstance(layout, containers.Jpeg) else None


def _same_picture(photo_path, scratch):
    """Do the photo and the new file beside it hold the same picture bytes, from each one's first scan to its end? None
    for a photo that is no JPEG (unchecked); NO_PICTURE for a JPEG whose picture is not found."""
    with open(photo_path, "rb") as photo, open(scratch, "rb") as written:
        if photo.read(len(JPEG_START)) != JPEG_START:
            return None
        start, new_start = _picture_at(photo), _picture_at(written)
        if start is None:
            return NO_PICTURE
        if new_start is None:
            return False
        size, new_size = photo.seek(0, 2), written.seek(0, 2)
        if size - start != new_size - new_start:
            return False
        photo.seek(start)
        written.seek(new_start)
        while True:
            block = photo.read(BLOCK)
            if block != written.read(BLOCK):
                return False
            if not block:
                return True


def _landed(photo_path, scratch):
    """Move the new file ExifTool wrote at `scratch` over the photo, once its picture is the photo's (_same_picture),
    with os.replace: a move of a file beside it on its volume, never a copy. A picture that differs: the new file is
    removed and PictureChanged raised. The look is bounded (CHECK_SECONDS): a share that does not answer raises
    NotAnswered, the photo as it was and the new file left beside it for the scratch rules (file_changes.removed_scratch).
    A move that fails (a photo marked read-only, one open in another program) leaves the photo untouched -- os.replace is
    one rename -- so the write removes its own new file, which would otherwise make the next write of the photo wait out
    the scratch rules and fail the same way, and raises an OSError of the same winerror (lock_owners.explain still names
    a holder) saying NOT_MOVED, no path in it. A kill between the new file and the move still leaves it to the rules."""
    answer, same = shares.bounded(photo_path, lambda: _same_picture(photo_path, scratch), CHECK_SECONDS)
    if answer == "away":
        raise NotAnswered("its folder did not answer the picture check in %d s; the photo is as it was" % CHECK_SECONDS)
    if answer == "error":
        raise same
    if same == NO_PICTURE:
        os.remove(scratch)
        raise RuntimeError(NO_PICTURE)
    if same is False:
        try:
            os.remove(scratch)
        except OSError as e:
            logger.warning("The new file of a write whose picture changed could not be removed; it is left: %s", e)
        raise PictureChanged("its write changed the picture's bytes; the new file was discarded and the photo is as it was")
    if same is None:
        logger.info("A photo that is no JPEG was written unchecked (its picture is not compared)")
    try:
        os.replace(scratch, photo_path)
    except OSError as e:
        try:
            os.remove(scratch)
        except OSError as left:
            logger.warning("The new file of a write that could not be moved over its photo could not be removed; it is "
                           "left: %s", left)
        # A refused rename on Windows always carries its winerror (5, access denied; 32, a sharing violation).
        raise OSError(e.errno, NOT_MOVED % e.strerror, None, e.winerror) from None
