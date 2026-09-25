"""Reading and writing the fields a journaled bulk edit changes in a photo file.

A bulk edit records, for each file, the fields it found and the fields it leaves
(tagpup.services.file_changes; docs/ARCHITECTURE.md, phase 7.5): its keywords, its
captions, its dates, its identity. This reads those fields as the journal keeps them --
each a list of texts (tagpup.core.fields.field_values) -- and writes them, forward, again
after a crash, or back in an undo, through the one writer, so a file is written the same
way whichever of the three it is.

A field written with an empty value is cleared: ExifTool reads an empty list as "no
change", so a cleared field is an explicit '-FIELD=' (tagpup.files.keywords), in the one
command that sets the rest. It was a second command, and a crash between the two left a
file holding neither what it held nor what it was to hold (docs/findings.md, #271).
"""
from tagpup.core import fields, paths
from tagpup.files import identity
from tagpup.files.keywords import MIME_TYPE

#: Photos read in one ExifTool command.
READ_BATCH = 200


class Unreadable(Exception):
    """A photo whose fields could not be read: missing, locked, or not a photo."""


def _held(row, wanted):
    kind = row.get(MIME_TYPE)
    if kind is not None and not str(kind).startswith("image/"):
        # A file of text read as TXT answers no fields, and would read as a photo holding
        # nothing (docs/findings.md, #46).
        raise Unreadable("Not a photo ExifTool can read (%s)" % kind)
    return {field: fields.field_values(row.get(fields.read_key(field))) for field in wanted}


def read(et, photo_paths, wanted):
    """{paths.key(path): {field: its values}} of each photo in `photo_paths`, for the
    fields in `wanted`; an Unreadable in place of the fields of a photo that could not be
    read. Read in batches; a batch ExifTool refuses is read again a photo at a time, so
    one bad file does not cost the others."""
    wanted = list(dict.fromkeys(wanted))
    asked = wanted + [MIME_TYPE]
    found = {}
    photo_paths = list(photo_paths)
    for start in range(0, len(photo_paths), READ_BATCH):
        batch = photo_paths[start:start + READ_BATCH]
        try:
            rows = et.get_tags(batch, tags=asked)
        except Exception:
            rows = None
        if rows is not None:
            for row in rows:
                source = row.get("SourceFile")
                if not source:
                    continue
                try:
                    found[paths.key(source)] = _held(row, wanted)
                except Unreadable as e:
                    found[paths.key(source)] = e
            missed = [p for p in batch if paths.key(p) not in found]
        else:
            missed = batch
        for one in missed:
            try:
                rows = et.get_tags([one], tags=asked)
                found[paths.key(one)] = _held(rows[0] if rows else {}, wanted)
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
    field whose value is empty. The photo's identity goes through tagpup.files.identity,
    which tells ExifTool an objection to the photo's XMP is minor and checks the write
    cost the keywords nothing. Raises when ExifTool fails."""
    params, clear = {}, []
    for field, value in values.items():
        texts = fields.field_values(value)
        if field == identity.DOCUMENT_ID_FIELD:
            identity.write_document_id(et, photo_path, texts[0] if texts else "")
        elif not texts:
            clear.append("-%s=" % field)
        elif fields.read_key(field) in fields.LIST_FIELDS or len(texts) > 1:
            params[field] = list(texts)
        else:
            params[field] = texts[0]
    if params:
        # The clears go before the values, in the same command.
        et.set_tags([photo_path], tags=params, params=["-overwrite_original"] + clear)
    elif clear:
        et.execute(*clear, "-overwrite_original", photo_path)
