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

So the rule is: read it, and mint one only where it is missing (tagpup.services.bake_after_index, a field of the
index's bake, written through the journal's one write of a photo's fields). Nothing is rewritten
that already has an answer, and the minted ones use Adobe's own `xmp.did:` form so
other tools recognise them.

`OriginalDocumentID` is deliberately not used as the identity. It is shared between a
RAW file and every JPEG derived from it, which is exactly what makes it useful for
grouping and useless for telling two files apart.
"""
import uuid

#: Where the identity lives. The short spelling is what ExifTool returns in a
#: metadata dict; the long one is what it must be written as.
DOCUMENT_ID_FIELD = "XMP-xmpMM:DocumentID"


def mint_document_id():
    """A new identity, in the form Adobe's tools write.

    `xmp.did:` prefixed, so anything reading this file recognises the value as an XMP
    document id rather than something TagPup invented.
    """
    return "xmp.did:%s" % uuid.uuid4()

