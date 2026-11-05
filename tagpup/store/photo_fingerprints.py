"""The fingerprint of each photo's picture: `photo_fingerprints` (migration 34; docs/PHASE6_DESIGN.md, section 7), one row
for each photo -- the key of the way the fingerprint was taken (`upright-sha256-1`: the sha256 of the upright RGB decode,
tagpup.files.images) and the hash.

A fact about the picture, not a decision and not a copy of the file's fields: not journaled (as indexing is not), not a
derived table (tagpup.store.derived), and nothing a journaled change of a file restores -- the picture an undo of a
metadata write leaves is the picture it found. A rotation changes the picture the decode shows, so it takes the row away
(tagpup.services.rotation), as it takes the photo's vectors.

No foreign key and no trigger, as `faces_detected`: a row whose photo is gone is read as none, a photo's id never being
handed out again, so deleting a photo takes nothing the journal must account for.

The row says which picture the photo's vectors were made of, so it goes with them: every write of a vector records the
fingerprint of the picture it was made of, or forgets the row when the writer does not know it (`follows`, called by
store.embeddings.put and put_by_id, whose `fingerprint` argument is required). Suggest keeping a vector it made, and a
re-embedding pass, decode no fingerprint and forget the row; the index records the decode's (tagpup.services.search,
build_or_update). Left behind, a row of the picture before a crop told the index a vector of the cropped picture was of
the picture restored after it (same_picture SAME). A bake's write records the Fingerprint the file holds after, in the
transaction that marks it done (tagpup.services.baking.follower). An undo of a bake leaves the row: the picture is the
one it was.

The one rule of a vector's currency (owner, DECISIONS 2026-10-27; docs/INVARIANTS.md) is `same_picture`'s: a photo's
vector is of the picture its file holds now when the vector's stamp describes the file (trusted, nothing decoded), or else
when the decode's fingerprint equals this table's row. A metadata write (keywords, a caption, XMP another program wrote)
changes the stamp and never the picture, so it never makes a vector stale. Sync (what to queue), the index (what is
current) and baking (what is to be baked) each ask it.

Which photos the index owes work is `owed`'s, the one answer sync (what to queue) and the index (what is current) ask:
a photo with no vector, one whose vector's stamp does not describe its file, one never examined for faces, and one whose
vector is current by its stamp and whose picture was never fingerprinted (Suggest's vector, a re-embedding pass, a photo
indexed before migration 34): the bake after the index decodes it, writes its file from the library and the row follows
the write (tagpup.services.baking.follower), or, its file holding a current bake already, the row is taken from the
decode with no write (`adopt`). Every write of a file's bake records the row, so a photo whose file was baked by this
library is never owed for this reason (owner, 2026-10-28: every photo baked, with no command).
"""
import collections

from tagpup.core import paths
from tagpup.store import faces_detected
from tagpup.store import photos as store_photos
from tagpup.store import roots as store_roots

TABLE = "photo_fingerprints"

#: A photo's fingerprint: the key of the way it was taken, and the hash (bytes).
Fingerprint = collections.namedtuple("Fingerprint", "key hash")

#: What same_picture answers. STAMPED: the vector's stamp describes the file, so the vector is of its picture (nothing
#: decoded). SAME: the stamp does not, and the decode's fingerprint equals the row's: the picture is the vector's, the
#: file's metadata alone changed. CHANGED: no vector, no row to compare with, or a different fingerprint: the vector is
#: not of the picture as it is now. UNDECIDED: the stamp does not describe the file and the row was not read (NOT_READ),
#: or there is a row and nothing was decoded to compare with it.
STAMPED, SAME, CHANGED, UNDECIDED = "stamped", "same", "changed", "undecided"

#: same_picture's `row` when the caller has not read it.
NOT_READ = object()


def same_picture(vector_stamp, file_stamp, row=NOT_READ, decoded=None):
    """Is a photo's vector of the picture its file holds now? `vector_stamp`, the (mtime, size) the vector was stamped
    with (None: no vector); `file_stamp`, the file's (mtime, size); `row`, the photo's Fingerprint (None: none), or
    NOT_READ; `decoded`, the (key, hash) of the file's decode now, or None. STAMPED, SAME, CHANGED or UNDECIDED (above)."""
    if vector_stamp is None:
        return CHANGED
    if store_photos.describes(vector_stamp[0], vector_stamp[1], file_stamp):
        return STAMPED
    if row is NOT_READ:
        return UNDECIDED
    if row is None:
        return CHANGED
    if decoded is None:
        return UNDECIDED
    return SAME if (decoded[0], bytes(decoded[1])) == (row.key, bytes(row.hash)) else CHANGED


#: Why the index owes a photo work (owed). NO_VECTOR: it holds no vector under the library's photo model. STALE: its
#: vector's stamp does not describe its file (same_picture not STAMPED): the index's decode tells a metadata write (kept)
#: from another picture (worked out). UNEXAMINED: never examined for faces by the library's detector
#: (faces_detected.unexamined): no face row and no record of a look. NO_FINGERPRINT: its vector's stamp describes its
#: file and it has no row here: its file is the bake's to write (no model, no card), the row following the write.
NO_VECTOR, STALE, UNEXAMINED, NO_FINGERPRINT = "no vector", "stale", "unexamined", "no fingerprint"

#: A photo the index owes work: its path as the walk spelled it, and why (a tuple of NO_VECTOR or STALE, and UNEXAMINED).
Owed = collections.namedtuple("Owed", "path reasons")


def owed(conn, photo_key, detector, on_disk, folder=None):
    """{photo id: Owed} of the photos (under `folder`, or every one) whose file is among `on_disk` ({paths.key: (path,
    mtime, size)}: the files as a walk found them) and that the index owes work: no vector under `photo_key` (an
    embeddings.model_key), a vector whose stamp does not describe the file, a current vector with no fingerprint row
    (NO_FINGERPRINT), or -- `detector` a faces_detected name, None when faces are not asked (--skip-faces) -- never
    examined for faces. A row whose stamp does not describe its file is sync's to read again (store.photos.describes),
    not a reason here; nor is a clip (kind 'video'), which is owed none of these. Nothing decoded, nothing written: one read of the rows' stamps with their vectors'
    (store.photos.stamps), one of the never-examined and one of the ids holding a fingerprint (a scan of this table's
    keys: 0.11 s on a process's first ask, 0.02 s warm; the whole answer 0.48 s, faces not asked, on photo_index's
    59,524 photos, 2026-10-28)."""
    unexamined = faces_detected.unexamined(conn, detector) if detector is not None else set()
    fingerprinted = {photo_id for (photo_id,) in conn.execute("SELECT photo_id FROM photo_fingerprints")}
    found = {}
    for photo_id, path, _mtime, _size, kind, vector in store_photos.stamps(conn, folder, vector_stamps=photo_key,
                                                                         with_kind=True):
        disk = on_disk.get(paths.key(path))
        if disk is None or kind == "video":
            # A clip has no vector, no fingerprint and no faces looked for: the index owes it nothing once its row
            # describes its file (it would queue every clip's folder on every sync).
            continue
        reasons = ()
        if vector is None:
            reasons = (NO_VECTOR,)
        elif same_picture(vector, disk[1:]) != STAMPED:
            reasons = (STALE,)
        elif photo_id not in fingerprinted:
            reasons = (NO_FINGERPRINT,)
        if photo_id in unexamined:
            reasons += (UNEXAMINED,)
        if reasons:
            found[photo_id] = Owed(disk[0], reasons)
    return found


def get(conn, photo_id):
    """The Fingerprint of the photo with id `photo_id`, or None: none recorded, or its photo is gone."""
    row = conn.execute("SELECT f.key, f.hash FROM photo_fingerprints f JOIN photos p ON p.id = f.photo_id"
                       " WHERE f.photo_id = ?", (photo_id,)).fetchone()
    return Fingerprint(*row) if row else None


def fingerprinted(conn, photo_ids):
    """The ids among `photo_ids` that have a row: one read of the key, no hash."""
    found = set()
    photo_ids = list(photo_ids)
    for start in range(0, len(photo_ids), 500):
        chunk = photo_ids[start:start + 500]
        found.update(photo_id for (photo_id,) in conn.execute(
            "SELECT photo_id FROM photo_fingerprints WHERE photo_id IN (%s)" % ",".join("?" * len(chunk)), chunk))
    return found


def of_path(conn, photo_path):
    """The Fingerprint of the photo at `photo_path`, or None: none recorded, or no such photo."""
    where, params = store_roots.sql_equals(conn, "p.path", photo_path)
    row = conn.execute("SELECT f.key, f.hash FROM photos p JOIN photo_fingerprints f ON f.photo_id = p.id WHERE " + where,
                       params).fetchone()
    return Fingerprint(*row) if row else None


def put(conn, photo_id, key, hash):
    """Record the fingerprint of the photo with id `photo_id`, in place of any it had. The caller commits."""
    conn.execute("INSERT OR REPLACE INTO photo_fingerprints (photo_id, key, hash) VALUES (?, ?, ?)",
                 (photo_id, key, bytes(hash)))


def adopt(conn, photo_id, photo_key, stamp, fingerprint):
    """Record `fingerprint`, the (key, hash) of the decode of the photo's file taken at `stamp` (its (mtime, size)), as
    the row of the photo with id `photo_id`, when it has none and its vector under `photo_key` is stamped describing that
    file still (same_picture STAMPED): the picture the vector was made of. Nothing when a row came meanwhile (its writer
    knew the picture) or the vector was made again or went: a row of another picture beside a new vector would keep that
    vector past a crop (follows). True when it was recorded. The caller commits.

    Its instances (the bake after the index, tests/test_the_index_writes_the_bake.py): another program writes the
    keywords of a baked file, a sync reads its row again and Suggest, finding the vector stale, keeps a new one with no
    fingerprint (its file holds a current bake: nothing to write); and a file ExifTool cannot write (read-only), whose
    row is recorded so sync does not queue its folder again for a write that cannot change."""
    vector = conn.execute("SELECT mtime, size FROM embeddings WHERE photo_id = ? AND model = ?",
                          (photo_id, photo_key)).fetchone()
    if vector is None or same_picture(tuple(vector), stamp) != STAMPED:
        return False
    return conn.execute("INSERT OR IGNORE INTO photo_fingerprints (photo_id, key, hash) VALUES (?, ?, ?)",
                        (photo_id, fingerprint[0], bytes(fingerprint[1]))).rowcount == 1


def follows(conn, photo_id, fingerprint):
    """A vector of the photo with id `photo_id` was just written (store.embeddings): its row is `fingerprint`, the (key,
    hash) of the picture the vector was made of, or none when `fingerprint` is None -- a vector made with no fingerprint
    taken leaves no row of another picture beside it. The caller commits."""
    if fingerprint is None:
        conn.execute("DELETE FROM photo_fingerprints WHERE photo_id = ?", (photo_id,))
    else:
        put(conn, photo_id, *fingerprint)


def forget_photos(conn, photo_ids):
    """Take away the fingerprints of the photos with ids `photo_ids`: rows the index recorded of the photos its bake is
    to write, so that one the bake does not write or find current is counted by `owed` again
    (tagpup.services.bake_after_index). Returns rows deleted. The caller commits."""
    photo_ids, deleted = list(photo_ids), 0
    for start in range(0, len(photo_ids), 500):
        chunk = photo_ids[start:start + 500]
        deleted += conn.execute("DELETE FROM photo_fingerprints WHERE photo_id IN (%s)" % ",".join("?" * len(chunk)),
                                chunk).rowcount
    return deleted


def forget(conn, photo_path):
    """Take away the fingerprint of the photo at `photo_path`: its picture now decodes differently (a rotation). Returns
    rows deleted. The caller commits."""
    where, params = store_roots.sql_equals(conn, "path", photo_path)
    return conn.execute("DELETE FROM photo_fingerprints WHERE photo_id IN (SELECT id FROM photos WHERE %s)" % where,
                        params).rowcount
