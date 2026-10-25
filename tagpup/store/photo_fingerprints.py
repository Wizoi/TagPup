"""The fingerprint of each photo's picture: `photo_fingerprints` (migration 34; docs/PHASE6_DESIGN.md, section 7), one row
for each photo -- the key of the way the fingerprint was taken (`upright-sha256-1`: the sha256 of the upright RGB decode,
tagpup.files.images) and the hash.

A fact about the picture, not a decision and not a copy of the file's fields: not journaled (as indexing is not), not a
derived table (tagpup.store.derived), and nothing a journaled change of a file restores -- the picture an undo of a
metadata write leaves is the picture it found. A rotation changes the picture the decode shows, so it takes the row away
(tagpup.services.rotation), as it takes the photo's vectors.

No foreign key and no trigger, as `faces_detected`: a row whose photo is gone is read as none, a photo's id never being
handed out again, so deleting a photo takes nothing the journal must account for.

Two write a row: the index, as it saves a photo it decoded (tagpup.services.search), and a bake's write, in the transaction
that marks it done (tagpup.services.baking.follower), from the Fingerprint the file holds after. An undo of a bake leaves
the row: the picture is the one it was.
"""
import collections

from tagpup.store import roots as store_roots

TABLE = "photo_fingerprints"

#: A photo's fingerprint: the key of the way it was taken, and the hash (bytes).
Fingerprint = collections.namedtuple("Fingerprint", "key hash")


def get(conn, photo_id):
    """The Fingerprint of the photo with id `photo_id`, or None: none recorded, or its photo is gone."""
    row = conn.execute("SELECT f.key, f.hash FROM photo_fingerprints f JOIN photos p ON p.id = f.photo_id"
                       " WHERE f.photo_id = ?", (photo_id,)).fetchone()
    return Fingerprint(*row) if row else None


def put(conn, photo_id, key, hash):
    """Record the fingerprint of the photo with id `photo_id`, in place of any it had. The caller commits."""
    conn.execute("INSERT OR REPLACE INTO photo_fingerprints (photo_id, key, hash) VALUES (?, ?, ?)",
                 (photo_id, key, bytes(hash)))


def forget(conn, photo_path):
    """Take away the fingerprint of the photo at `photo_path`: its picture now decodes differently (a rotation). Returns
    rows deleted. The caller commits."""
    where, params = store_roots.sql_equals(conn, "path", photo_path)
    return conn.execute("DELETE FROM photo_fingerprints WHERE photo_id IN (SELECT id FROM photos WHERE %s)" % where,
                        params).rowcount
