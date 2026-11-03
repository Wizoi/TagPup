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
bake fields are written only by a session that knows its namespace: session(), which every journaled write opens.

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

**A patch in place** (DECISIONS 2026-11-02, "THE JPEG PATCH IS BUILT WITH THE VIDEOS'"). A journaled write (one given
`saving`) of a JPEG whose new fields fit its old EXIF, IPTC and XMP segments writes only those bytes
(tagpup.files.in_place.patch_jpeg): the same ExifTool command writes the fields into a copy of the photo's header alone,
on this PC, and the segments it wrote are patched into the photo at their old lengths. What does not fit is rewritten
as above. The picture is never read or written by a patch; the journal checks the bytes it changed, and the fields
read back, before the file is done (tagpup.services.file_changes._verified), and reads no other byte of the file.
"""
import logging
import os

from exiftool.exceptions import ExifToolNotRunning

from tagpup.core import fields, paths
from tagpup.files import bake, containers, exiftool_session, identity, in_place, shares

logger = logging.getLogger(__name__)

#: What ExifTool warns of a field it was given and did not write, in a command it reports as a success: a struct's field
#: it does not know ("is not a field of"), one in a namespace it cannot write ("is not a writable XMP tag"), TagPup's own
#: in a process started without its configuration ("is not defined"), and a photo whose XMP segment holds no packet
#: ("Invalid XMP": all NUL bytes on 3 photos of photo_index, #2349), where ExifTool writes nothing. Only these: 119 of 120 photos read carry a minor
#: validation warning of their own, which costs a write nothing.
NOT_WRITTEN = ("is not defined", "is not a field of", "is not a writable XMP tag", "Invalid XMP")

#: What ExifTool says a file is. A photo's starts with "image/".
MIME_TYPE = "File:MIMEType"

#: Photos read in one ExifTool command.
READ_BATCH = 200

#: The options of every write: ExifTool told that an objection it calls minor does not stop the write (#2009). A photo
#: whose XMP holds descriptions under two rdf:about attributes (a phone's Extended XMP beside another program's packet)
#: is refused without it, and written with it, each description under the first about and nothing else changed; a
#: corrupt packet is refused with it as without it, and its warnings of a field not written (NOT_WRITTEN) are the same.
#: It also stops ExifTool cutting an IPTC value to IPTC's length, so a write cuts it first (fields.as_written). Never on
#: a read: it changes what ExifTool answers (DECISIONS 2026-10-27). Never -overwrite_original: ExifTool writes the new
#: file beside the photo (`-o`), and the picture check moves it over the photo (_landed).
WRITE_OPTIONS = ("-m",)

#: Whether a journaled write of a JPEG patches it in place when its fields fit (in_place.patch_jpeg); False, every write
#: is ExifTool's rewrite, as before the patch was built.
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


class Unreadable(Exception):
    """A photo whose fields could not be read: missing, locked, or not a photo."""


class NotAnswered(Unreadable):
    """A photo of a batch ExifTool did not answer for in its deadline (a share gone away, a disk that stopped): nothing is
    known of the file, not even that it cannot be read, so a reader leaves it unread."""


class PictureChanged(Exception):
    """The new file ExifTool wrote of a photo holds picture bytes other than the photo's (the picture check, #2377): it
    was removed, and the photo is as it was. The journal writes once more (tagpup.services.file_changes._carry)."""


def session(exiftool_path):
    """The ExifTool session of a journaled write (tagpup.services.file_changes): one that knows TagPup's namespace
    (bake.config_path), since a write or an undo of a bake names its fields. Looked up at call time, so a test standing in
    for ExifToolSession reaches it."""
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
    if kind is not None and not str(kind).startswith("image/"):
        # A file of text read as TXT answers no fields, and would read as a photo holding
        # nothing (docs/findings.md, #46).
        raise Unreadable("Not a photo ExifTool can read (%s)" % kind)
    return {field: _value(row, field) for field in wanted}


def read(et, photo_paths, wanted, also=(), records=None, stop=None, params=()):
    """{paths.key(path): {field: its values}} of each photo in `photo_paths`, for the
    fields in `wanted`; an Unreadable in place of the fields of a photo that could not be
    read (NotAnswered, of a photo ExifTool did not answer for). Read in batches; a batch ExifTool refuses is read again a
    photo at a time, so one bad file does not cost the others. A command ExifTool does not answer in its deadline (a share
    gone away) ends the read: that photo, and every photo not read yet, is NotAnswered, so one read waits one deadline. The one batch reader: the indexer's
    (tagpup.files.metadata.MetadataExtractor) and sync's pairing (tagpup.services.relink_photos) read through it. `also` are fields asked as well, answered only
    in `records`, when given: {paths.key(path): ExifTool's record} of each photo read.

    `stop`, a threading.Event: once it is set no more batches are read, and the photos not reached are left out of the
    answer (a batch asked to stop while it plans, tagpup.services.file_changes). `params`, options of each read command
    (the XMP census's -validate, which alone makes ExifTool say a JPEG holds two XMP blocks: tagpup.services.inspect).
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
        return found

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
    return found


def read_one(et, photo_path, wanted):
    """The fields of one photo, as read() gives them; raises Unreadable."""
    held = read(et, [photo_path], wanted).get(paths.key(photo_path))
    if isinstance(held, Exception) or held is None:
        raise held or Unreadable("ExifTool answered nothing for it")
    return held


def write(et, photo_path, values, saving=None, scratch_written=None):
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
    (bake.NAMESPACE) are written only through a session opened by session(): ExifTool would refuse them, by a warning,
    under any other, so another is refused here, by a ValueError, before anything is written. A write of a struct field
    or of TagPup's own that ExifTool warns left a field unwritten (NOT_WRITTEN) raises RuntimeError before the photo's
    first byte, by a patch or a rewrite: ExifTool's new file (of the photo, or of its header's copy) is discarded, and
    the photo holds what it held. A text holding a line break is sent as ExifToolSession sends it (exiftool_session.CSTR).

    `saving(record, before)` is the journal's (tagpup.services.file_changes): given, a JPEG whose fields fit is patched
    in place (the module; PATCH_IN_PLACE), which calls it with its record (tagpup.files.in_place.record) and writes no
    byte until it returns, nor at all when it raises. ExifTool's rewrite never calls it: it leaves the file it held or the
    one it was to hold, whole. A JPEG patch is at most three segments of 64 KB, under file_journal.PATCH_CAP, so the
    journal's PatchTooLarge is not met here. A patch whose photo's share does not answer raises NotAnswered, the file
    settled by its bytes (file_changes._by_bytes)."""
    own = [field for field in values if str(field).startswith(bake.NAMESPACE + ":")]
    if own and getattr(et, "config_file", None) != bake.config_path():
        raise ValueError("%s is written only by a session that knows TagPup's namespace (field_values.session); this one "
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
        return
    checked = own or any(fields.is_struct(field) for field in values)
    if PATCH_IN_PLACE and saving is not None:
        def serialise(source, target):
            try:
                _command(et, source, target, params, clear)
            except exiftool_session.ExifToolFailed:
                return False        # the rewrite's command says why, of the photo
            _refuse_dropped(et, source, checked)
            return True

        try:
            patched = in_place.patch_jpeg(photo_path, serialise, saving)
        except in_place.Away:
            raise NotAnswered("its folder did not answer the patch of its fields in %d s; it is settled by its bytes "
                              "the next time" % in_place.SECONDS) from None
        if patched:
            return
    scratch = exiftool_session.scratch_of(photo_path)
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
    NotAnswered, the photo as it was and the new file left beside it for the scratch rules (file_changes._removed_scratch).
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


def ensure_document_id(et, path, metadata=None):
    """Return this photo's identity, writing one into the file if it has none (tagpup.files.identity).

    `metadata` is the dict already read for the photo, so the common case -- a photo
    that has an identity -- costs nothing beyond a dictionary lookup. Only a photo
    without one is written to, through write(): the one writer of a photo's fields.

    The write is one command, as every write is (write: an objection ExifTool calls minor does not stop it); a lock
    or a timeout leaves the photo as it was.
    A failure to write is logged and swallowed: an identity is an improvement on
    knowing the path, and a photo that cannot take one is still perfectly indexable.
    Returns None in that case, which every caller must treat as "no identity", not as
    an error.
    """
    existing = fields.read_document_id(metadata)
    if existing:
        return existing
    minted = identity.mint_document_id()
    try:
        write(et, path, {identity.DOCUMENT_ID_FIELD: minted})
    except Exception as e:
        logger.warning("Could not give %s an identity: %s", path, e)
        return None
    logger.debug("Minted %s for %s", minted, path)
    return minted
