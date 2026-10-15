"""A photo's identity, independent of where it happens to sit on disk.

A path is a bad name for a photo. Rename the file, move the folder, reorganise a
shoot, and the index is left describing something that no longer exists while the
photo itself looks unindexed. Both halves are wrong, and the index is the valuable
half: it carries the photo's embedding and its faces, including every name assigned
by hand. One rename in this library stranded 78 rows holding 234 faces, 88 of them
named.

Photos already carry a stable identity, and most already have one filled in.
`XMP-xmpMM:DocumentID` is the XMP standard's per-document identifier: Lightroom and
Camera Raw write it, it survives copying and renaming, and it is what a cataloguing
tool is meant to track a file by. Of 1,129 photos sampled from this library, 1,075
already had one -- 1,075 distinct values, no collisions.

So the rule is: read it, and mint one only where it is missing. Nothing is rewritten
that already has an answer, and the minted ones use Adobe's own `xmp.did:` form so
other tools recognise them.

`OriginalDocumentID` is deliberately not used as the identity. It is shared between a
RAW file and every JPEG derived from it, which is exactly what makes it useful for
grouping and useless for telling two files apart.
"""
import logging
import uuid

logger = logging.getLogger("tagpup_cli.identity")

#: Where the identity lives. The short spelling is what ExifTool returns in a
#: metadata dict; the long one is what it must be written as.
DOCUMENT_ID_FIELD = "XMP-xmpMM:DocumentID"
DOCUMENT_ID_KEYS = ("XMP:DocumentID", "XMP-xmpMM:DocumentID", "DocumentID")


def read_document_id(metadata):
    """The photo's identity as its metadata reports it, or None."""
    if not isinstance(metadata, dict):
        return None
    for key in DOCUMENT_ID_KEYS:
        value = metadata.get(key)
        if isinstance(value, list):
            value = value[0] if value else None
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def mint_document_id():
    """A new identity, in the form Adobe's tools write.

    `xmp.did:` prefixed, so anything reading this file recognises the value as an XMP
    document id rather than something TagPup invented.
    """
    return "xmp.did:%s" % uuid.uuid4()


#: Keyword fields a forced write carries as they hold, and checks afterwards. If one of
#: these changes, the write cost the photo something it will not get back.
KEYWORD_FIELDS = ("XMP:Subject", "IPTC:Keywords", "XMP:HierarchicalSubject")


def _keywords(et, path):
    """The photo's keyword fields as they stand, for comparison."""
    try:
        found = et.get_tags([path], tags=list(KEYWORD_FIELDS))[0]
    except Exception:
        return None
    snapshot = {}
    for field in KEYWORD_FIELDS:
        value = found.get(field)
        if isinstance(value, str):
            value = [value]
        snapshot[field] = list(value) if value else []
    return snapshot


#: How ExifTool marks an error `-m` lets it write past.
MINOR = "[minor]"


def is_minor_refusal(error):
    """Did ExifTool refuse a write for an error it calls minor (its message holds
    "[minor]", on stderr)? Not a timeout, a locked file, a file it cannot read."""
    stderr = getattr(error, "stderr", None)
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    return MINOR in ("%s %s" % (stderr or "", error)).lower()


def tolerating_minor_errors(et, path, write, written=()):
    """Make a write that sets or takes away a photo's identity -- `write(extra_params,
    keep)`, one ExifTool command, with whatever else it writes (tagpup.files.field_values)
    -- and when ExifTool refuses it for an error it calls minor, and only then, make it
    again with `-m`. `-m` rewrites the photo's XMP, so the keyword fields the write does
    not set (`written`: the fields it sets, as ExifTool answers them) go in that same
    command as `keep`, with the values they hold: one command, as the file journal
    records it; they were put back afterwards in a second, unrecorded one. Any other
    failure -- a timeout, a locked file -- is raised as it is, and so is the refusal when
    the keyword fields cannot be read to carry: a read that fails is not a photo with no
    keywords, and nothing is forced. Raises when the write is not made."""
    try:
        write([], {})
    except Exception as error:
        if not is_minor_refusal(error):
            raise
        before = _keywords(et, path)
        if before is None:
            raise
        keep = {field: values for field, values in before.items() if values and field not in written}
        write(["-m"], keep)
        after = _keywords(et, path)
        lost = sorted(field for field, values in keep.items() if after is not None and after.get(field) != values)
        if lost:
            logger.error("Writing %s past a minor error left %s other than it held", path, ", ".join(lost))


def ensure_document_id(et, path, metadata=None):
    """Return this photo's identity, writing one into the file if it has none.

    `metadata` is the dict already read for the photo, so the common case -- a photo
    that has an identity -- costs nothing beyond a dictionary lookup. Only a photo
    without one is written to.

    The write is one command, made again with `-m` only when ExifTool refuses it for an
    error it calls minor -- a photo with two XMP blocks -- carrying the keywords as they
    hold (tolerating_minor_errors); a lock or a timeout leaves the photo as it was.
    A failure to write is logged and swallowed: an identity is an improvement on
    knowing the path, and a photo that cannot take one is still perfectly indexable.
    Returns None in that case, which every caller must treat as "no identity", not as
    an error.
    """
    existing = read_document_id(metadata)
    if existing:
        return existing

    minted = mint_document_id()

    def command(extra, keep):
        tags = {DOCUMENT_ID_FIELD: minted}
        tags.update(keep)
        et.set_tags([path], tags=tags, params=list(extra) + ["-overwrite_original"])

    try:
        tolerating_minor_errors(et, path, command)
    except Exception as e:
        logger.warning("Could not give %s an identity: %s", path, e)
        return None

    logger.debug("Minted %s for %s", minted, path)
    return minted
