"""The faces table.

Every write here that changes who a face is -- named, unnamed, excluded, detected,
deleted -- rebuilds the people of the photos it touched (tagpup.store.people.rebuild),
in the same transaction: clustering, re-detection and dedupe changed faces and left
each photo's people as they were (docs/findings.md, #63). It gives their faces the id of
the person their name now is, too (tagpup.store.person_ids.follow_faces), by the same
call (`_rebuilt`).

The names a photo's faces were given, turning their boxes when a photo is turned, a
face's crop, a write to the table that the Identify Faces grids can account for, and
what indexing and clustering read and record. The servers' queries move here in
phase 3 (docs/ARCHITECTURE.md).

A face names its photo by id; a photo's path crosses this module native, converted by the
library's roots on the way in and out (tagpup.store.roots).
"""
import collections
import contextlib
import json
import logging
import os
import types

from tagpup.core import paths
from tagpup.store import db, generations, people, person_ids
from tagpup.store import roots as store_roots
from tagpup.store.people import PEOPLE_JSON

logger = logging.getLogger(__name__)

#: A face's photo, by its id. Every read that answers with a photo's path joins it.
PHOTO = " JOIN photos p ON p.id = f.photo_id"


def _on_photo(conn, photo_path, column="photo_id"):
    """WHERE clause and parameters for the faces in one photo: its id, found by path the
    way paths compare, which idx_photos_path_nocase serves."""
    where, params = store_roots.sql_equals(conn, "path", photo_path)
    return "%s IN (SELECT id FROM photos WHERE %s)" % (column, where), params


def _under(conn, folder, column="photo_id"):
    """WHERE clause and parameters for the faces in the photos under a folder, at any
    depth."""
    where, params = store_roots.sql_under(conn, "path", folder)
    return "%s IN (SELECT id FROM photos WHERE %s)" % (column, where), params


def names_in_photo(conn, photo_path):
    """The names given to faces in one photo, on `conn`."""
    where, params = _on_photo(conn, photo_path)
    return {name for (name,) in conn.execute(
        "SELECT name FROM faces WHERE " + where + " AND name IS NOT NULL", params)}


def names_by_face(conn, photo_path):
    """{face id: name} of the faces of one photo that carry a name, on `conn`."""
    where, params = _on_photo(conn, photo_path)
    return dict(conn.execute("SELECT id, name FROM faces WHERE " + where + " AND name IS NOT NULL", params).fetchall())


def names_given(conn, names):
    """Which of `names` some face carries now, on `conn`: one indexed query for them all.
    A name read before a write began may have been renamed, or taken off every face,
    before it is written."""
    names = list(names)
    found = set()
    for chunk in _chunks(names):
        found.update(name for (name,) in conn.execute(
            "SELECT DISTINCT name FROM faces WHERE name IN (" + ",".join("?" * len(chunk)) + ")", chunk))
    return found


def generation(conn):
    """The faces table's generation (tagpup.store.generations), or 0 on a library that
    does not count it yet. It moves when a name changes, which none of the table's
    counts need to."""
    return generations.value(conn, "faces")


def fingerprint(conn):
    """A cheap signature of the faces table, which moves whenever a face is added, named,
    renamed, given to someone else or excluded. TagTuner caches its Identify Faces grids
    against it."""
    # SUM(excluded) matters: excluding an unnamed face changes what the queue should show
    # without changing the row count, the name count, or the maximum id, so leaving it
    # out serves a stale queue after every exclusion.
    row = conn.execute(
        "SELECT COUNT(*), COUNT(name), COALESCE(MAX(id), 0), COALESCE(SUM(excluded), 0) FROM faces"
    ).fetchone()
    return (tuple(row) if row else (0, 0, 0, 0)) + (generation(conn),)


@contextlib.contextmanager
def accounted_write(db_path, label="faces write"):
    """A write to the faces table whose effect alone the fingerprint's change describes.

    Holds the library's write lock and one IMMEDIATE transaction. Yields `write`, with
    `conn` and `before`, the fingerprint as the write began; `after` is read just before
    the commit. Nobody else can write between the two, so a cache that drops the faces
    this write took out of the pool may re-stamp itself with `after`: the difference is
    this write, and no batch the indexer or TagPup committed meanwhile. Reading them
    before the lock and after the commit stamped such a batch as accounted for, and its
    faces never reached the grid. Rolled back if the block raises.
    """
    with db.lock_for(db_path):
        conn = db.connect(db_path, timeout=30.0)
        try:
            conn.execute("BEGIN IMMEDIATE")
            write = types.SimpleNamespace(conn=conn, before=fingerprint(conn), after=None)
            try:
                yield write
            except BaseException:
                conn.rollback()
                raise
            write.after = fingerprint(conn)
            conn.commit()
            logger.debug("%s committed", label)
        finally:
            conn.close()


def face_names(photo_path, db_path=None, conn=None):
    """The names given to a photo's faces, excluded faces left out, in detection order.

    From `conn`, else from the library at `db_path`, read-only. Nothing when neither
    can be read: a photo's people are its keywords' then, rather than an error.
    """
    own = None
    try:
        if conn is None:
            if not db_path or not os.path.exists(db_path):
                return []
            own = conn = db.connect(db.readonly_uri(db_path), uri=True)
        clause, params = _on_photo(conn, photo_path)
        rows = conn.execute(
            "SELECT name FROM faces WHERE " + clause
            + " AND name IS NOT NULL AND COALESCE(excluded, 0) = 0 ORDER BY id", params).fetchall()
        return [name for (name,) in rows if name]
    except Exception as e:
        logger.warning("Could not read face names for %s: %s", photo_path, e)
        return []
    finally:
        if own is not None:
            own.close()


def turned_box(box, direction, width, height):
    """A face box after a quarter turn of a `width` x `height` image.

    Left is counter-clockwise, as Image.rotate(90, expand=True) turns it.
    """
    x1, y1, x2, y2 = box[:4]
    if direction == "left":
        return [y1, width - x2, y2, width - x1]
    return [height - y2, x1, height - y1, x2]


def turn_boxes(db_path, photo_path, direction, width, height):
    """Turn a photo's stored face boxes with it. Returns how many rows changed.

    Only for a photo Pillow shows already oriented (a TIFF: Pillow applies its
    Orientation on load), where every box -- and every crop cut from it -- is in the
    turned picture's coordinates once the Orientation changes. The cached crop is
    dropped so the next request cuts it again from the right place.
    """
    def turn(conn):
        cursor = conn.cursor()
        where, where_params = _on_photo(conn, photo_path)
        rows = cursor.execute("SELECT id, box FROM faces WHERE " + where, where_params).fetchall()
        changed = 0
        for face_id, box_json in rows:
            try:
                box = json.loads(box_json)
            except (TypeError, ValueError):
                continue
            if not isinstance(box, list) or len(box) < 4:
                continue
            cursor.execute("UPDATE faces SET box = ? WHERE id = ?",
                           (json.dumps(turned_box(box, direction, width, height)), face_id))
            changed += cursor.rowcount
            cursor.execute("DELETE FROM face_crops WHERE face_id = ?", (face_id,))
        return changed

    return db.write_with_connection(
        db_path, turn, label="face boxes for rotated %s" % os.path.basename(photo_path))


def crop_of(db_path, face_id):
    """(photo path, box, cached crop) of a face, or None if there is no such face -- or
    no such library, which connecting would create."""
    if not os.path.exists(db_path):
        return None
    conn = db.connect(db_path, timeout=30.0)
    try:
        row = store_roots.native_one(conn, conn.execute(
            "SELECT p.path, f.box, c.jpeg FROM faces f" + PHOTO
            + " LEFT JOIN face_crops c ON c.face_id = f.id WHERE f.id = ?", (face_id,)).fetchone(), 0)
    finally:
        conn.close()
    return tuple(row) if row else None


def cache_crop(db_path, face_id, jpeg):
    """Keep a face's crop in face_crops, so it is cut from the photo once. Returns rows
    changed: none for a face that is not there.

    Through the write lock: both servers used to write it back on their own connection."""
    def store(conn):
        if not conn.execute("SELECT 1 FROM faces WHERE id = ?", (face_id,)).fetchone():
            return 0
        return conn.execute("INSERT OR REPLACE INTO face_crops (face_id, jpeg) VALUES (?, ?)",
                            (face_id, jpeg)).rowcount

    return db.write_with_connection(db_path, store, label="crop of face %s" % face_id)


# ---- What PhotoIndex and clustering read and write ---------------------------------------

def count_for_photo(conn, photo_path):
    """How many face rows a photo has."""
    where, params = _on_photo(conn, photo_path)
    return conn.execute("SELECT COUNT(*) FROM faces WHERE " + where, params).fetchone()[0]


def decided_for_photo(conn, photo_path):
    """How many of a photo's faces carry a decision somebody made: a name or a "nobody"
    given by hand (name_source 'manual'), an exclusion. Re-detecting the photo's faces
    would lose them. A name clustering gave is not one: it is revised whenever clustering
    runs again (clear_automatic_names)."""
    where, params = _on_photo(conn, photo_path)
    return conn.execute("SELECT COUNT(*) FROM faces WHERE " + where
                        + " AND (name_source = 'manual' OR excluded = 1)", params).fetchone()[0]


def _photos_of(conn, face_ids):
    """The ids of the photos the faces among `face_ids` are in."""
    found = set()
    for chunk in _chunks(face_ids):
        found.update(photo_id for (photo_id,) in conn.execute(
            "SELECT DISTINCT photo_id FROM faces WHERE " + _in(chunk), chunk))
    return found


def _rebuilt(conn, photo_ids, changed, vocabulary=None):
    """`changed`, after rebuilding the people of `photo_ids` and giving their faces and listed people
    the ids their names give (person_ids), if anything changed. The tree's people are read once for
    both (docs/findings.md, #659). `vocabulary` is the tree's PeopleVocabulary when the caller has it
    already (a batch that rebuilds many photos reads the tree once: #838)."""
    if changed and photo_ids:
        known = person_ids.read(conn)
        person_ids.follow_faces(conn, photo_ids, known)
        people.rebuild(conn, photo_ids, known=vocabulary, ids=known)
    return changed


def remove_for_photo(conn, photo_path):
    """Delete a photo's face rows, names and decisions with them. Returns rows deleted.
    The caller commits."""
    found, found_params = store_roots.sql_equals(conn, "path", photo_path)
    photo_ids = {photo_id for (photo_id,) in conn.execute("SELECT id FROM photos WHERE " + found, found_params)}
    where, params = _on_photo(conn, photo_path)
    return _rebuilt(conn, photo_ids, conn.execute("DELETE FROM faces WHERE " + where, params).rowcount)


def insert(conn, photo_path, box, embedding, name=None, crop=None, prob=None):
    """Record one detected face: `box` as a list, `embedding` as float32 bytes, and its
    crop, if one was cut, in face_crops. Returns the face's id. The caller commits."""
    from tagpup.store import photos   # photos imports this module
    photo_id = photos.ensure_row(conn, photo_path)
    face_id = conn.execute(
        "INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
        (photo_id, json.dumps(box), embedding, name, prob)).lastrowid
    if crop:
        conn.execute("INSERT INTO face_crops (face_id, jpeg) VALUES (?, ?)", (face_id, crop))
    _rebuilt(conn, [photo_id], bool(name))
    return face_id


def manual_names(conn):
    """face id -> name for every face a person decided by hand. A name of None is a
    deliberate "this is nobody", as binding as a name."""
    return dict(conn.execute("SELECT id, name FROM faces WHERE name_source = 'manual'").fetchall())


def excluded_ids(conn):
    """The faces kept out of identity work."""
    return {face_id for (face_id,) in conn.execute("SELECT id FROM faces WHERE excluded = 1")}


def for_clustering(conn):
    """(id, photo_path, box JSON, embedding bytes, name, prob) of every face. The crop is
    left behind: 6 KB a face, and clustering never looks at it."""
    return store_roots.natives(conn, conn.execute(
        "SELECT f.id, p.path, f.box, f.embedding, f.name, f.prob FROM faces f" + PHOTO).fetchall(), 1)


def named_embeddings(conn):
    """(name, embedding bytes) of every named face that is not excluded, which
    idx_faces_identify answers without touching the rest of the table."""
    return conn.execute(
        "SELECT name, embedding FROM faces WHERE excluded = 0 AND name IS NOT NULL").fetchall()


def named_for_known(conn):
    """(name, embedding bytes, photo path, the year it was taken or None) of every named
    face that is not excluded: what tagpup.core.clustering.KnownFaces is made of."""
    return store_roots.natives(conn, conn.execute(
        "SELECT f.name, f.embedding, p.path, p.year FROM faces f" + PHOTO
        + " WHERE f.excluded = 0 AND f.name IS NOT NULL AND f.embedding IS NOT NULL").fetchall(), 2)


def in_photo(conn, photo_path):
    """(box JSON, embedding bytes, prob, excluded, name, name_source) of each face in one
    photo, for suggesting who is in it."""
    where, params = _on_photo(conn, photo_path)
    return conn.execute(
        "SELECT box, embedding, prob, excluded, name, name_source FROM faces WHERE " + where,
        params).fetchall()


def set_names(conn, names_by_id):
    """Give each face in {id: name} its name, leaving who decided it alone. The caller
    commits."""
    conn.executemany("UPDATE faces SET name = ? WHERE id = ?",
                     [(name, face_id) for face_id, name in names_by_id.items()])
    _rebuilt(conn, _photos_of(conn, names_by_id), bool(names_by_id))


def clear_automatic_names(conn):
    """Clear the names clustering gave, leaving the ones given by hand. Returns how many
    were cleared. The caller commits."""
    automatic = "name IS NOT NULL AND COALESCE(name_source, '') <> 'manual'"
    photo_ids = {photo_id for (photo_id,) in conn.execute("SELECT DISTINCT photo_id FROM faces WHERE " + automatic)}
    return _rebuilt(conn, photo_ids, conn.execute("UPDATE faces SET name = NULL WHERE " + automatic).rowcount)


# ---- What the face actions read and write (tagpup.services.faces) ------------------------

#: How many ids go in one IN (...): well under SQLite's limit on parameters.
CHUNK = 500


def _chunks(face_ids):
    face_ids = list(face_ids)
    for start in range(0, len(face_ids), CHUNK):
        yield face_ids[start:start + CHUNK]


def _in(chunk):
    return "id IN (%s)" % ",".join("?" * len(chunk))


def rows(conn, face_ids):
    """{id: (photo_path, name, excluded)} of the faces among `face_ids` that exist."""
    found = {}
    for chunk in _chunks(face_ids):
        for face_id, photo_path, name, excluded in store_roots.natives(conn, conn.execute(
                "SELECT f.id, p.path, f.name, f.excluded FROM faces f" + PHOTO
                + " WHERE f." + _in(chunk), chunk).fetchall(), 1):
            found[face_id] = (photo_path, name, excluded)
    return found


def named_among(conn, face_ids):
    """[(photo path, name)] of the faces among `face_ids` that carry a name and are not excluded -- and only those: ignoring
    a cluster sends thousands of nameless faces, whose photos are not read. By the faces' key, then each one's photo by its."""
    found = []
    for chunk in _chunks(face_ids):
        found.extend(store_roots.natives(conn, conn.execute(
            "SELECT p.path, f.name FROM faces f" + PHOTO + " WHERE f." + _in(chunk)
            + " AND f.name IS NOT NULL AND f.excluded = 0", chunk).fetchall(), 0))
    return found


def name(conn, face_ids, person_name):
    """Name faces as a person's decision (name_source 'manual'), which re-clustering does
    not revise. Excluded faces are left alone. Returns rows named. The caller commits."""
    changed = sum(conn.execute(
        "UPDATE faces SET name = ?, name_source = 'manual' WHERE " + _in(chunk) + " AND excluded = 0",
        [person_name] + chunk).rowcount for chunk in _chunks(face_ids))
    return _rebuilt(conn, _photos_of(conn, face_ids), changed)


def decided_by_hand(conn, face_id):
    """Did a person decide this face's name -- or that it is nobody (name_source 'manual')?"""
    row = conn.execute("SELECT name_source FROM faces WHERE id = ?", (face_id,)).fetchone()
    return bool(row) and row[0] == "manual"


def confirm(conn, face_id):
    """A person confirms the name a face carries (an automatic one: clustering's, or its photo's tag's):
    name_source 'manual', the name and its spelling left as they are. Not an excluded face, a nameless
    one or one confirmed already. Returns rows changed. The caller commits."""
    return conn.execute("UPDATE faces SET name_source = 'manual' WHERE id = ? AND name IS NOT NULL AND excluded = 0"
                        " AND COALESCE(name_source, '') <> 'manual'", (face_id,)).rowcount


def name_if_unnamed(conn, face_id, person_name):
    """Give an unnamed, unexcluded face a name as a guess -- who decided is left alone,
    so re-clustering may revise it. Not a face somebody unmatched by hand: "this is
    nobody" is a decision, which a guess does not overrule (docs/findings.md, #643).
    Returns rows named. The caller commits."""
    changed = conn.execute("UPDATE faces SET name = ? WHERE id = ? AND name IS NULL AND excluded = 0"
                           " AND " + NOT_DECIDED_NOBODY % "", (person_name, face_id)).rowcount
    return _rebuilt(conn, _photos_of(conn, [face_id]), changed)


def name_unnamed(conn, names_by_id, vocabulary=None):
    """Give each face in {id: name} its name as a guess, as name_if_unnamed does -- only a face still
    unnamed, not excluded and not unmatched by hand -- in one statement per name and chunk, and rebuild
    their photos once: automatch's write (docs/findings.md, #659), which named a folder's faces one by
    one and rebuilt a photo for each. Returns the ids its UPDATE changed (RETURNING), in the order given:
    a face another process named meanwhile is not counted, inside a transaction or not (#665). The
    caller commits, inside the transaction that read the faces it chose. `vocabulary`: the tree's people
    when the caller has read them (_rebuilt)."""
    guard = " AND name IS NULL AND excluded = 0 AND " + NOT_DECIDED_NOBODY % ""
    by_name = collections.defaultdict(list)
    for face_id, person_name in names_by_id.items():
        by_name[person_name].append(face_id)
    named = set()
    for person_name, face_ids in by_name.items():
        for chunk in _chunks(face_ids):
            named.update(face_id for (face_id,) in conn.execute(
                "UPDATE faces SET name = ? WHERE " + _in(chunk) + guard + " RETURNING id", [person_name] + chunk).fetchall())
    done = [face_id for face_id in names_by_id if face_id in named]
    _rebuilt(conn, _photos_of(conn, done), len(done), vocabulary)
    return done


def revert_automatic(conn, names_by_id):
    """Take back guesses just made (automatch's names, when their photo's tag could not be written):
    each face in {id: name} that still carries that name AS A GUESS (name_source NULL) is unnamed again,
    as it was before, not as a decision. A face a person named, confirmed or unmatched meanwhile is theirs
    and is left. Returns the ids reverted. The caller commits."""
    by_name = collections.defaultdict(list)
    for face_id, person_name in names_by_id.items():
        by_name[person_name].append(face_id)
    reverted = []
    for person_name, face_ids in by_name.items():
        for chunk in _chunks(face_ids):
            reverted.extend(face_id for (face_id,) in conn.execute(
                "UPDATE faces SET name = NULL WHERE " + _in(chunk) + " AND name = ? AND name_source IS NULL"
                " RETURNING id", chunk + [person_name]).fetchall())
    _rebuilt(conn, _photos_of(conn, reverted), len(reverted))
    return reverted


def unname(conn, face_ids, source="manual"):
    """Take the names off faces, recording who decided in name_source: 'manual' for
    "this is nobody", None for an undone guess. Returns rows changed. The caller commits."""
    changed = sum(conn.execute(
        "UPDATE faces SET name = NULL, name_source = ? WHERE " + _in(chunk),
        [source] + chunk).rowcount for chunk in _chunks(face_ids))
    return _rebuilt(conn, _photos_of(conn, face_ids), changed)


def unname_photo(conn, photo_path):
    """Take the names off every named face in a photo, as a decision. Returns rows
    changed. By equality: a LIKE pass as well cleared every name in IMG-1234.jpg along
    with IMG_1234.jpg. The caller commits.

    Only the faces that had a name: marking the photo's nameless faces 'manual' as well
    called each of them "nobody" for good, and automatch never named them again
    (docs/findings.md, #656)."""
    where, params = _on_photo(conn, photo_path)
    changed = conn.execute("UPDATE faces SET name = NULL, name_source = 'manual' WHERE " + where
                           + " AND name IS NOT NULL", params).rowcount
    return _rebuilt(conn, {photo_id for (photo_id,) in conn.execute(
        "SELECT DISTINCT photo_id FROM faces WHERE " + where, params)}, changed)


def exclude(conn, face_ids, reason):
    """Take faces out of identity work, their names with them, as a decision. Returns
    rows excluded. The caller commits."""
    changed = sum(conn.execute(
        "UPDATE faces SET excluded = 1, excluded_reason = ?, name = NULL, name_source = 'manual'"
        " WHERE " + _in(chunk), [reason] + chunk).rowcount for chunk in _chunks(face_ids))
    return _rebuilt(conn, _photos_of(conn, face_ids), changed)


def restore(conn, face_ids):
    """Bring excluded faces back, unnamed and unclaimed. Only faces that are excluded:
    clearing name_source on another would unpin a manual name. Returns rows restored.
    The caller commits."""
    changed = sum(conn.execute(
        "UPDATE faces SET excluded = 0, excluded_reason = NULL, name_source = NULL"
        " WHERE " + _in(chunk) + " AND excluded = 1", chunk).rowcount for chunk in _chunks(face_ids))
    # A face both named and excluded counts again once restored (#89).
    return _rebuilt(conn, _photos_of(conn, face_ids), changed)


def named_elsewhere_in_photo(conn, photo_path, person_name, face_id, decided_only=False):
    """Does a face in the photo other than `face_id` carry the name? By equality: a LIKE
    retry scanned every face row, and read an underscore in a file name as any character.
    With `decided_only`, only a name somebody decided counts, not a guess (name_source NULL)."""
    where, params = _on_photo(conn, photo_path)
    return conn.execute("SELECT 1 FROM faces WHERE " + where + " AND name = ? AND id != ?"
                        + (" AND name_source IS NOT NULL" if decided_only else ""),
                        params + (person_name, face_id)).fetchone() is not None


def guesses_named(conn, photo_path, person_name, face_id):
    """{id: name} of the faces of the photo other than `face_id` that carry the name as a guess (name_source NULL)."""
    where, params = _on_photo(conn, photo_path)
    return {other: person_name for (other,) in conn.execute(
        "SELECT id FROM faces WHERE " + where + " AND name = ? AND id != ? AND name_source IS NULL",
        params + (person_name, face_id))}


#: The faces of the photos under a folder, the photos found first: their range on
#: idx_photos_path_nocase, then each one's faces by idx_faces_photo_id. CROSS JOIN keeps
#: that order, and INDEXED BY the index: with `excluded = 0` and `name IS NULL` beside
#: it, SQLite otherwise took idx_faces_identify for each photo, every unnamed face of
#: the library once per photo. Asked as faces whose photo is IN the folder, it started
#: from the unnamed faces of the whole library: 190,000 of them in the largest, for a
#: folder of 2,400 (docs/findings.md, #644).
UNDER_FROM_PHOTOS = " FROM photos p CROSS JOIN faces f INDEXED BY idx_faces_photo_id ON f.photo_id = p.id"


def _photos_under(conn, folder):
    """WHERE clause and parameters for the photos `p` under a folder, at any depth."""
    return store_roots.sql_under(conn, "p.path", folder)


#: A nameless face nobody has called nobody: unmatching a face records name_source
#: 'manual' (unname), and automatch must not name it again. %s: the table's alias and a
#: dot, or nothing.
NOT_DECIDED_NOBODY = "COALESCE(%sname_source, '') <> 'manual'"


def unnamed(conn, photo_path=None, folder=None):
    """(id, embedding bytes, photo_path) of the unnamed, unexcluded faces in one photo, or
    under a folder at any depth, that automatch may name: not those unmatched by hand
    (docs/findings.md, #643)."""
    if photo_path is not None:
        where, params = _on_photo(conn, photo_path, "f.photo_id")
        source = " FROM faces f" + PHOTO
    else:
        where, params = _photos_under(conn, folder)
        source = UNDER_FROM_PHOTOS
    return store_roots.natives(conn, conn.execute(
        "SELECT f.id, f.embedding, p.path" + source + " WHERE " + where
        + " AND f.name IS NULL AND f.excluded = 0 AND " + NOT_DECIDED_NOBODY % "f.", params).fetchall(), 2)


def unnamed_counts(conn, folder):
    """{photo_path as stored: faces still unnamed} for the photos under a folder. An
    excluded face is not waiting for a name (docs/findings.md, #642)."""
    where, params = _photos_under(conn, folder)
    return dict(store_roots.natives(conn, conn.execute(
        "SELECT p.path, COUNT(*)" + UNDER_FROM_PHOTOS + " WHERE " + where
        + " AND f.name IS NULL AND f.excluded = 0 GROUP BY f.photo_id", params).fetchall(), 0))


# ---- What TagTuner's screens read -----------------------------------------------------

def counts_by_name(conn):
    """[(name, faces)] for everyone named, most faces first."""
    return conn.execute("SELECT name, COUNT(*) AS count FROM faces WHERE name IS NOT NULL"
                        " GROUP BY name ORDER BY count DESC").fetchall()


def identify_candidates(conn):
    """(id, photo_path, the photo's people JSON, embedding length) of every nameless face
    still in play: the Identify Faces queue.

    LENGTH(embedding) rather than the embedding: the queue groups faces by the people
    their photo names and counts them, and only needs to know a face HAS one -- a face
    without cannot take part in identifying. Selecting the column read 380 MB of BLOB
    to answer a question about integers.
    """
    return store_roots.natives(conn, conn.execute(
        "SELECT f.id, p.path, " + PEOPLE_JSON + ", LENGTH(f.embedding) FROM faces f" + PHOTO
        + " WHERE f.name IS NULL AND f.excluded = 0").fetchall(), 1)


def unnamed_for_matching(conn):
    """(id, photo_path, box JSON, prob, mtime, embedding, year, people JSON) of every
    nameless face still in play, for a person's Identify grid."""
    return store_roots.natives(conn, conn.execute(
        "SELECT f.id, p.path, f.box, f.prob, p.mtime, f.embedding, p.year, " + PEOPLE_JSON + ""
        " FROM faces f" + PHOTO + " WHERE f.name IS NULL AND f.excluded = 0").fetchall(), 1)


def count_unnamed(conn):
    """How many nameless faces are in play. idx_faces_identify answers it without the rows."""
    return conn.execute("SELECT COUNT(*) FROM faces WHERE name IS NULL AND excluded = 0").fetchone()[0]


def named_and_unnamed(conn, folder=None):
    """(named, unnamed) faces in play -- an excluded face is neither -- in the whole library, or in the photos under
    `folder` at any depth. The library's by idx_faces_identify, without the rows; a folder's by its photos'
    faces (UNDER_FROM_PHOTOS), as unnamed_counts."""
    if folder is None:
        named = conn.execute("SELECT COUNT(*) FROM faces WHERE excluded = 0 AND name IS NOT NULL").fetchone()[0]
        return named, count_unnamed(conn)
    where, params = _photos_under(conn, folder)
    named, unnamed = conn.execute(
        "SELECT COUNT(f.name), COUNT(*) - COUNT(f.name)" + UNDER_FROM_PHOTOS + " WHERE " + where
        + " AND f.excluded = 0", params).fetchone()
    return named, unnamed


def unnamed_embeddings(conn):
    """(id, embedding) of every nameless face in play, by id: New Person's pool
    (tagpup.services.identify.unnamed_faces). idx_faces_identify finds them. A cursor,
    not a list: the caller takes them a row at a time, never all 390 MB at once."""
    return conn.execute("SELECT id, embedding FROM faces WHERE name IS NULL AND excluded = 0"
                        " ORDER BY id")


def unnamed_among(conn, face_ids):
    """(id, photo_path, box JSON, embedding) of those of `face_ids` that are nameless
    faces in play, read by id."""
    found = []
    for chunk in _chunks(list(face_ids)):
        found.extend(conn.execute(
            "SELECT f.id, p.path, f.box, f.embedding FROM faces f" + PHOTO + " WHERE f.id IN (%s)"
            " AND f.name IS NULL AND f.excluded = 0" % ",".join("?" * len(chunk)), chunk).fetchall())
    return store_roots.natives(conn, found, 1)


def names_by_photo(conn):
    """{photo_path as stored: names on its faces}, for every photo with a named face."""
    named = {}
    for photo_path, name in store_roots.natives(conn, conn.execute(
            "SELECT p.path, f.name FROM faces f" + PHOTO + " WHERE f.name IS NOT NULL").fetchall(), 0):
        named.setdefault(photo_path, set()).add(name)
    return named


def count_excluded(conn):
    """How many faces are excluded. idx_faces_identify answers it without the rows."""
    return conn.execute("SELECT COUNT(*) FROM faces WHERE excluded = 1").fetchone()[0]


def for_named_matrix(conn):
    """(id, name, embedding) of every named face that has an embedding and is not
    excluded: what a face is compared against to say who it resembles."""
    return conn.execute("SELECT id, name, embedding FROM faces"
                        " WHERE name IS NOT NULL AND embedding IS NOT NULL AND excluded = 0").fetchall()


def for_decided_matrix(conn):
    """(id, name, embedding, name_source, photo_id) of every named face that has an embedding
    and is not excluded: the candidates for what automatch compares a face against. The
    caller keeps those a person decided (tagpup.services.identify.decided_faces)."""
    return conn.execute("SELECT id, name, embedding, name_source, photo_id FROM faces"
                        " WHERE name IS NOT NULL AND embedding IS NOT NULL AND excluded = 0").fetchall()


def embedding_row(conn, face_id):
    """(embedding,) of one face, or None when there is no such face."""
    return conn.execute("SELECT embedding FROM faces WHERE id = ?", (face_id,)).fetchone()


def in_photo_with_names(conn, photo_path):
    """(id, box JSON, name, embedding, excluded) of each face in one photo."""
    where, params = _on_photo(conn, photo_path)
    return conn.execute("SELECT id, box, name, embedding, excluded FROM faces WHERE " + where, params).fetchall()


def photos_with_unnamed(conn, every=False):
    """(photo_path, unnamed faces, named faces, mtime, year) of every photo
    with a face still unnamed, newest first; with `every`, of every photo with a face,
    those whose faces are all named too (TagTuner's Show matched). An excluded face is
    neither: it was listed as unmatched, and a photo whose only nameless face had been
    ruled out stayed in Folder Matches for good (docs/findings.md, #642)."""
    return store_roots.natives(conn, conn.execute(
        "SELECT p.path,"
        " SUM(CASE WHEN f.name IS NULL AND f.excluded = 0 THEN 1 ELSE 0 END) AS unmatched,"
        " SUM(CASE WHEN f.name IS NOT NULL THEN 1 ELSE 0 END) AS matched,"
        " p.mtime, p.year"
        " FROM faces f" + PHOTO
        + " GROUP BY f.photo_id" + ("" if every else " HAVING unmatched > 0")
        + " ORDER BY p.mtime DESC").fetchall(), 0)


def count_named(conn, person_name):
    """How many faces carry the name."""
    return conn.execute("SELECT COUNT(*) FROM faces WHERE name = ?", (person_name,)).fetchone()[0]


def person_embeddings(conn, person_name):
    """(embedding, mtime, year, photo_path) of each of a person's faces that
    has an embedding: what their era-aware centroids are made from."""
    return store_roots.natives(conn, conn.execute(
        "SELECT f.embedding, p.mtime, p.year, p.path FROM faces f" + PHOTO
        + " WHERE f.name = ? AND f.embedding IS NOT NULL", (person_name,)).fetchall(), 3)


def person_page(conn, person_name, limit, offset):
    """(id, photo_path, box JSON, prob, mtime, embedding, year) of a page of a
    person's faces. A negative limit is no limit."""
    return store_roots.natives(conn, conn.execute(
        "SELECT f.id, p.path, f.box, f.prob, p.mtime, f.embedding, p.year"
        " FROM faces f" + PHOTO + " WHERE f.name = ? LIMIT ? OFFSET ?",
        (person_name, limit, offset)).fetchall(), 1)


def excluded_for_review(conn):
    """(id, photo_path, box JSON, prob, mtime, year, excluded_reason) of every
    excluded face, the latest excluded first."""
    return store_roots.natives(conn, conn.execute(
        "SELECT f.id, p.path, f.box, f.prob, p.mtime, p.year, f.excluded_reason"
        " FROM faces f" + PHOTO + " WHERE f.excluded = 1 ORDER BY f.id DESC").fetchall(), 1)


# ---- What TagPup's photo panel and the CLI read -----------------------------------------

def in_photo_for_panel(conn, photo_path):
    """(id, box JSON, name, prob, embedding, excluded, excluded_reason) of each face in one
    photo, in detection order."""
    where, params = _on_photo(conn, photo_path)
    return conn.execute("SELECT id, box, name, prob, embedding, excluded, excluded_reason"
                        " FROM faces WHERE " + where + " ORDER BY id", params).fetchall()


def named_embeddings_elsewhere(conn, photo_path):
    """(name, embedding) of every named, unexcluded face in any photo but this one: who a
    face in it might be."""
    where, params = _on_photo(conn, photo_path)
    return conn.execute("SELECT name, embedding FROM faces"
                        " WHERE name IS NOT NULL AND excluded = 0 AND NOT (" + where + ")",
                        params).fetchall()


def photos_with_faces(conn):
    """The photo paths, as stored, that have any face row."""
    return {photo_path for (photo_path,) in store_roots.natives(conn, conn.execute(
        "SELECT path FROM photos WHERE id IN (SELECT photo_id FROM faces)").fetchall(), 0)}


def names_by_photo_key(conn):
    """{paths.key of a photo: names on its faces that are not excluded}."""
    named = {}
    for photo_path, name in store_roots.natives(conn, conn.execute(
            "SELECT p.path, f.name FROM faces f" + PHOTO + " WHERE f.name IS NOT NULL AND f.excluded = 0").fetchall(),
            0):
        named.setdefault(paths.key(photo_path), set()).add(name)
    return named


def counts_on(conn, stored_path):
    """(faces, named faces) of the photo stored under exactly `stored_path`."""
    return conn.execute("SELECT COUNT(*), COUNT(name) FROM faces"
                        " WHERE photo_id IN (SELECT id FROM photos WHERE path = ?)",
                        (store_roots.to_row(conn, stored_path),)).fetchone()


# ---- What dedupe_faces reads and writes -------------------------------------------------

def decisions(conn):
    """(id, photo_path, box JSON, name, name_source, excluded) of every face: what was
    decided about each, without its embedding or crop."""
    return store_roots.natives(conn, conn.execute(
        "SELECT f.id, p.path, f.box, f.name, f.name_source, f.excluded FROM faces f" + PHOTO).fetchall(), 1)


def delete(conn, face_ids):
    """Delete faces by id. Returns rows deleted. The caller commits."""
    photo_ids = _photos_of(conn, face_ids)
    return _rebuilt(conn, photo_ids, sum(conn.execute("DELETE FROM faces WHERE " + _in(chunk), chunk).rowcount
                                         for chunk in _chunks(face_ids)))


# ---- What verify_workflow reads --------------------------------------------------------

def latest_unnamed_ids(conn, limit):
    """The ids of the most recently detected unnamed faces still in play."""
    return [face_id for (face_id,) in conn.execute(
        "SELECT id FROM faces WHERE name IS NULL AND excluded = 0 ORDER BY id DESC LIMIT ?", (limit,))]


def is_excluded(conn, face_id):
    """Whether a face is excluded; None when there is no such face."""
    row = conn.execute("SELECT excluded FROM faces WHERE id = ?", (face_id,)).fetchone()
    return None if row is None else bool(row[0])


def busiest_photo(conn):
    """The photo, as stored, with the most faces; None with no faces."""
    row = conn.execute("SELECT p.path FROM faces f" + PHOTO + " GROUP BY f.photo_id"
                       " ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
    return store_roots.from_row(conn, row[0]) if row else None
