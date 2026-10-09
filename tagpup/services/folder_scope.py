"""Limiting a library-wide backfill to one folder (the owner's request, 2026-10-09; docs/findings.md #987).

`faces-from-tags` and `tags-from-faces` ran over a whole library. Run for `--folder` -- or from the dialog's "Only this
folder" -- they act on the photos under that folder, at any depth, and on no others.

The scope is a filter on WHICH PHOTOS A PLAN READS, never a different rule: who a photo's tags name, the decided faces a face is
compared with, and the "several photos of this person wait" gate stay the library's, so a folder's plan is exactly the
whole-library plan restricted to the folder's photos. The folder is spelled by the first place of its root
(tagpup.services.roots.canonical: a second location of a root works), and found by the path index's range
(paths.sql_under, through the connection's Roots): case-insensitively where the file system is, and `Run` never takes in
`Run2` or `Run_x`.

A folder the library holds no photo under -- outside its roots, never indexed, mistyped -- is REFUSED, with a sentence: it is not
"nothing to do", which would read as the folder being in step.
"""
import collections

from tagpup.core.result import Refused
from tagpup.services import roots
from tagpup.store import db, photos

#: The folder as the library's roots spell it, and the ids of the photos under it (one range of idx_photos_path_nocase).
Scope = collections.namedtuple("Scope", "folder photo_ids")


class NoPhotosThere(Refused):
    """The library holds no photo under the folder asked for."""


def refusal(library, folder):
    """The sentence for a folder with no photo of `library` under it."""
    return ("%s holds no photo of %s, at any depth: it is outside the library's folders, or its photos were never indexed, or "
            "the name is mistyped. Nothing was done." % (folder, library.name))


def resolve(library, folder):
    """The Scope of `folder` in `library`, read on a read-only connection. NoPhotosThere (a Refused, whose message is
    for the person) when the library holds no photo under it. A library that cannot be read raises, loudly: an
    unreadable library is not an empty folder."""
    spelled = roots.canonical(library, folder)
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        ids = photos.ids_under(conn, spelled)
    finally:
        conn.close()
    if not ids:
        raise NoPhotosThere(refusal(library, folder))
    return Scope(spelled, ids)
