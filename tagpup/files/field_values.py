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
"""
import logging

from exiftool.exceptions import ExifToolNotRunning

from tagpup.core import fields, paths
from tagpup.files import exiftool_session, identity

logger = logging.getLogger(__name__)

#: What ExifTool says a file is. A photo's starts with "image/".
MIME_TYPE = "File:MIMEType"

#: Photos read in one ExifTool command.
READ_BATCH = 200

class Unreadable(Exception):
    """A photo whose fields could not be read: missing, locked, or not a photo."""


class NotAnswered(Unreadable):
    """A photo of a batch ExifTool did not answer for in its deadline (a share gone away, a disk that stopped): nothing is
    known of the file, not even that it cannot be read, so a reader leaves it unread."""


def _held(row, wanted):
    kind = row.get(MIME_TYPE)
    if kind is not None and not str(kind).startswith("image/"):
        # A file of text read as TXT answers no fields, and would read as a photo holding
        # nothing (docs/findings.md, #46).
        raise Unreadable("Not a photo ExifTool can read (%s)" % kind)
    return {field: fields.field_values(row.get(fields.read_key(field))) for field in wanted}


def read(et, photo_paths, wanted, also=(), records=None, stop=None):
    """{paths.key(path): {field: its values}} of each photo in `photo_paths`, for the
    fields in `wanted`; an Unreadable in place of the fields of a photo that could not be
    read (NotAnswered, of a photo ExifTool did not answer for). Read in batches; a batch ExifTool refuses is read again a
    photo at a time, so one bad file does not cost the others. A command ExifTool does not answer in its deadline (a share
    gone away) ends the read: that photo, and every photo not read yet, is NotAnswered, so one read waits one deadline. The one batch reader: the indexer's
    (tagpup.files.metadata.MetadataExtractor) and sync's pairing (tagpup.services.relink_photos) read through it. `also` are fields asked as well, answered only
    in `records`, when given: {paths.key(path): ExifTool's record} of each photo read.

    `stop`, a threading.Event: once it is set no more batches are read, and the photos not reached are left out of the
    answer (a batch asked to stop while it plans, tagpup.services.file_changes).

    The file is not scanned to its end first: a JPEG whose picture stops short takes a write of its fields, and its
    picture data is left as it was (DECISIONS 2026-10-15); a file ExifTool cannot write fails that write."""
    wanted = list(dict.fromkeys(wanted))
    asked = list(dict.fromkeys(wanted + list(also) + [MIME_TYPE]))
    if records is None:
        records = {}
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
            rows = et.get_tags(batch, tags=asked)
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
                rows = et.get_tags([one], tags=asked)
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


def write(et, photo_path, values):
    """Write `values` ({field: value}) into a photo, in one ExifTool command, clearing a
    field whose value is empty. A write of the photo's identity that ExifTool refuses is
    made again telling it an objection to the photo's XMP is minor, and checked to have
    cost the keywords nothing (tagpup.files.identity.tolerating_minor_errors). Raises
    when ExifTool fails. Nothing beside the photo is deleted, a temporary file ExifTool left included: one ExifTool
    fails after deleting the original is the photo's only copy (#1410)."""
    params, clear = {}, []
    for field, value in values.items():
        texts = fields.field_values(value)
        named = fields.WRITTEN_AS.get(field, field)
        if not texts:
            clear.append("-%s=" % field)
        elif fields.read_key(field) in fields.LIST_FIELDS or len(texts) > 1:
            params[named] = list(texts)
        else:
            params[named] = texts[0]

    def command(extra, keep):
        # `keep`: fields the write does not set, written as they hold in the same command
        # when it is forced (identity.tolerating_minor_errors).
        tags = dict(params)
        tags.update(keep)
        if tags:
            # The clears go before the values, in the same command.
            et.set_tags([photo_path], tags=tags, params=list(extra) + ["-overwrite_original"] + clear)
        elif clear:
            et.execute(*extra, *clear, "-overwrite_original", photo_path)

    if identity.DOCUMENT_ID_FIELD in values:
        identity.tolerating_minor_errors(et, photo_path, command, written=[fields.read_key(f) for f in values])
    else:
        command([], {})


def ensure_document_id(et, path, metadata=None):
    """Return this photo's identity, writing one into the file if it has none (tagpup.files.identity).

    `metadata` is the dict already read for the photo, so the common case -- a photo
    that has an identity -- costs nothing beyond a dictionary lookup. Only a photo
    without one is written to, through write(): the one writer of a photo's fields.

    The write is one command, made again with `-m` only when ExifTool refuses it for an
    error it calls minor -- a photo with two XMP blocks -- carrying the keywords as they
    hold (identity.tolerating_minor_errors); a lock or a timeout leaves the photo as it was.
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
