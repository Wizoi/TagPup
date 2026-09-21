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


def ensure_document_id(et, path, metadata=None):
    """Return this photo's identity, writing one into the file if it has none.

    `metadata` is the dict already read for the photo, so the common case -- a photo
    that has an identity -- costs nothing beyond a dictionary lookup. Only a photo
    without one is written to.

    A failure to write is logged and swallowed: an identity is an improvement on
    knowing the path, and a photo that cannot take one is still perfectly indexable.
    Returns None in that case, which every caller must treat as "no identity", not as
    an error.
    """
    existing = read_document_id(metadata)
    if existing:
        return existing

    minted = mint_document_id()
    try:
        et.set_tags([path], tags={DOCUMENT_ID_FIELD: minted},
                    params=["-overwrite_original"])
    except Exception as e:
        logger.warning("Could not give %s an identity: %s", path, e)
        return None

    logger.debug("Minted %s for %s", minted, path)
    return minted
