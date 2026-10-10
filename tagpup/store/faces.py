"""The faces table.

Every write here that changes who a face is -- named, unnamed, excluded, detected,
deleted -- rebuilds the people of the photos it touched (tagpup.store.people.rebuild),
in the same transaction: clustering, re-detection and dedupe changed faces and left
each photo's people as they were (docs/findings.md, #63).

A face names its person by the id of the person's node (`tag_id`): a writer here takes the person as
an id, a tag path or a bare name (`person`), turns it into the pair of id and cached name through
tagpup.store.person_ids.target -- the one door, which refuses a stale id, a name two people have and
a group -- and writes both in the one statement. `name` is the cache of the node's leaf, never what a
face is told apart by. Readers group, count and join by the id, and by the name's key for a face whose
name no person is filed under (person_ids.key_of). `_rebuilt` settles what a statement left (a name
that is one person's links, a renamed node's leaf follows: person_ids.follow_faces).

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
from tagpup.core.vocabulary import Ref
from tagpup.store import db, faces_detected, generations, people, person_ids
from tagpup.store import roots as store_roots
from tagpup.store.people import PEOPLE_REFS_JSON

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


def _ref(tag_id, name):
    """The person a face carries as a Ref (the node's id, the cached name), or None for a face that carries nobody."""
    return None if name is None else Ref(tag_id, name)


def people_in_photo(conn, photo_path):
    """The people named on the faces of one photo, as a set of Refs, on `conn`."""
    where, params = _on_photo(conn, photo_path)
    return {Ref(tag_id, name) for tag_id, name in conn.execute(
        "SELECT tag_id, name FROM faces WHERE " + where + " AND name IS NOT NULL", params)}


def people_by_face(conn, photo_path):
    """{face id: Ref} of the faces of one photo that carry a person, on `conn`."""
    where, params = _on_photo(conn, photo_path)
    return {face_id: Ref(tag_id, name) for face_id, tag_id, name in conn.execute(
        "SELECT id, tag_id, name FROM faces WHERE " + where + " AND name IS NOT NULL", params)}


def named_in_photo(conn, photo_path):
    """[(face id, Ref, name_source)] of the faces of one photo that carry a person and are not ruled out, on `conn`."""
    return [(face_id, person, source) for face_id, person, source, excluded in _of_photo(conn, photo_path)
            if person is not None and not excluded]


def nobody_in_photo(conn, photo_path):
    """[face id] of the faces of one photo a person called nobody (name NULL, name_source 'manual') and that are not ruled out."""
    return [face_id for face_id, person, source, excluded in _of_photo(conn, photo_path)
            if person is None and source == "manual" and not excluded]


def _of_photo(conn, photo_path):
    """[(id, Ref or None, name_source, excluded)] of one photo's faces, found by idx_faces_photo_id: with the name and the exclusion in
    the WHERE the planner reads idx_faces_identify (every named face of the library, 0.12 s on photo_index) for each photo."""
    where, params = store_roots.sql_equals(conn, "path", photo_path)
    return [(face_id, _ref(tag_id, name), source, excluded)
            for (photo_id,) in conn.execute("SELECT id FROM photos WHERE " + where, params).fetchall()
            for face_id, tag_id, name, source, excluded in conn.execute(
                "SELECT id, tag_id, name, name_source, excluded FROM faces INDEXED BY idx_faces_photo_id"
                " WHERE photo_id = ? ORDER BY id", (photo_id,)).fetchall()]


def people_given(conn, persons):
    """Which of `persons` some face carries now, on `conn`: a person given as an id by idx_faces_tag, one given as a name (a
    name no person is filed under) by the name's index; one query a chunk. A person read before a write began may have been
    unnamed everywhere, or merged away, before it is written."""
    persons = list(persons)
    ids = person_ids.given(conn, [person for person in persons if isinstance(person, int)])
    names = set()
    named = [person for person in persons if isinstance(person, str)]
    for chunk in _chunks(named):
        names.update(name for (name,) in conn.execute(
            "SELECT DISTINCT name FROM faces WHERE tag_id IS NULL AND name IN (" + ",".join("?" * len(chunk)) + ")", chunk))
    return {person for person in persons if (person in ids if isinstance(person, int) else person in names)}


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


def face_refs(photo_path, db_path=None, conn=None):
    """The people a photo's faces were given, as Refs, excluded faces left out, in detection order.

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
            "SELECT tag_id, name FROM faces WHERE " + clause
            + " AND name IS NOT NULL AND COALESCE(excluded, 0) = 0 ORDER BY id", params).fetchall()
        return [Ref(tag_id, name) for tag_id, name in rows if name]
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


def _rebuilt(conn, photo_ids, changed, vocabulary=None, known=None):
    """`changed`, after rebuilding the people of `photo_ids` and giving their faces and listed people
    the ids their names give (person_ids), if anything changed. The tree's people are read once for
    both (docs/findings.md, #659). `vocabulary` is the tree's PeopleVocabulary when the caller has it
    already (a batch that rebuilds many photos reads the tree once: #838)."""
    if changed and photo_ids:
        known = known or person_ids.read(conn)
        person_ids.follow_faces(conn, photo_ids, known)
        people.rebuild(conn, photo_ids, known=vocabulary, ids=known)
    return changed


def relist(conn, face_ids):
    """The lists of people of the photos `face_ids` are in, written again by the one rule after faces were given a person (the
    owner's link or make in the names to review): a listed row follows its face's id, and a keyword's row is its path's, as
    always. The caller commits."""
    return _rebuilt(conn, _photos_of(conn, set(face_ids)), True)


def remove_for_photo(conn, photo_path):
    """Delete a photo's face rows, names and decisions with them. Returns rows deleted.
    The caller commits."""
    found, found_params = store_roots.sql_equals(conn, "path", photo_path)
    photo_ids = {photo_id for (photo_id,) in conn.execute("SELECT id FROM photos WHERE " + found, found_params)}
    where, params = _on_photo(conn, photo_path)
    removed = conn.execute("DELETE FROM faces WHERE " + where, params).rowcount
    if removed:
        # Their detection is no longer on file (a caller that detects again records it after).
        faces_detected.forget_faceless(conn, photo_ids)
    return _rebuilt(conn, photo_ids, removed)


def insert(conn, photo_path, box, embedding, name=None, crop=None, prob=None):
    """Record one detected face: `box` as a list, `embedding` as float32 bytes, and its
    crop, if one was cut, in face_crops. `name` is a person (an id, a tag path or a name:
    person_ids.target), or None. Returns the face's id. The caller commits."""
    from tagpup.store import photos   # photos imports this module
    photo_id = photos.ensure_row(conn, photo_path)
    tag_id, name = person_ids.target(conn, name) if name is not None else (None, None)
    if person_ids.present(conn):
        face_id = conn.execute(
            "INSERT INTO faces (photo_id, box, embedding, name, tag_id, prob) VALUES (?, ?, ?, ?, ?, ?)",
            (photo_id, json.dumps(box), embedding, name, tag_id, prob)).lastrowid
    else:
        face_id = conn.execute(
            "INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES (?, ?, ?, ?, ?)",
            (photo_id, json.dumps(box), embedding, name, prob)).lastrowid
    if crop:
        conn.execute("INSERT INTO face_crops (face_id, jpeg) VALUES (?, ?)", (face_id, crop))
    _rebuilt(conn, [photo_id], bool(name))
    return face_id


def manual_names(conn):
    """face id -> Ref for every face a person decided by hand. A None is a
    deliberate "this is nobody", as binding as a person."""
    return {face_id: _ref(tag_id, name)
            for face_id, tag_id, name in conn.execute("SELECT id, tag_id, name FROM faces WHERE name_source = 'manual'")}


def excluded_ids(conn):
    """The faces kept out of identity work."""
    return {face_id for (face_id,) in conn.execute("SELECT id FROM faces WHERE excluded = 1")}


def for_clustering(conn):
    """(id, photo_path, box JSON, embedding bytes, Ref or None, prob) of every face. The crop is
    left behind: 6 KB a face, and clustering never looks at it."""
    return [(face_id, photo_path, box, embedding, _ref(tag_id, name), prob)
            for face_id, photo_path, box, embedding, tag_id, name, prob in store_roots.natives(conn, conn.execute(
                "SELECT f.id, p.path, f.box, f.embedding, f.tag_id, f.name, f.prob FROM faces f" + PHOTO).fetchall(), 1)]


def named_for_known(conn):
    """(Ref, embedding bytes, photo path, the year it was taken or None) of every named
    face that is not excluded: what tagpup.core.clustering.KnownFaces is made of."""
    return [(Ref(tag_id, name), embedding, photo_path, year)
            for tag_id, name, embedding, photo_path, year in store_roots.natives(conn, conn.execute(
                "SELECT f.tag_id, f.name, f.embedding, p.path, p.year FROM faces f" + PHOTO
                + " WHERE f.excluded = 0 AND f.name IS NOT NULL AND f.embedding IS NOT NULL").fetchall(), 3)]


def in_photo(conn, photo_path):
    """(box JSON, embedding bytes, prob, excluded, Ref or None, name_source) of each face in one
    photo, for suggesting who is in it."""
    where, params = _on_photo(conn, photo_path)
    return [(box, embedding, prob, excluded, _ref(tag_id, name), source)
            for box, embedding, prob, excluded, tag_id, name, source in conn.execute(
                "SELECT box, embedding, prob, excluded, tag_id, name, name_source FROM faces WHERE " + where,
                params).fetchall()]


def _targets(conn, persons, known=None):
    """({person: (id, name)} for each distinct person among `persons`, the People read for it): the tree is read once,
    inside the caller's transaction (or is `known`, which the caller read in it), and each distinct person is resolved once
    (person_ids.target)."""
    known = known or person_ids.read(conn)
    return {person: person_ids.target(conn, person, known) for person in set(persons)}, known


def _assign(conn):
    """The SET fragment that gives a face a person, and the order its parameters go in: (SQL, "pair") -- name then id --
    where the library has the id column (migration 21), the name alone before."""
    return "name = ?, tag_id = ?" if person_ids.present(conn) else "name = ?"


def _pair(conn, target):
    """The parameters of `_assign` for a (id, name) target."""
    tag_id, name = target
    return (name, tag_id) if person_ids.present(conn) else (name,)


def _unassign(conn):
    """The SET fragment that takes a face's person off."""
    return "name = NULL, tag_id = NULL" if person_ids.present(conn) else "name = NULL"


def set_names(conn, persons_by_id):
    """Give each face in {id: person} its person (an id, a tag path or a name: person_ids.target), leaving who decided
    it alone. The caller commits."""
    targets, known = _targets(conn, persons_by_id.values())
    conn.executemany("UPDATE faces SET " + _assign(conn) + " WHERE id = ?",
                     [_pair(conn, targets[person]) + (face_id,) for face_id, person in persons_by_id.items()])
    _rebuilt(conn, _photos_of(conn, persons_by_id), bool(persons_by_id), known=known)


def clear_automatic_names(conn):
    """Clear the names clustering gave, leaving the ones given by hand. Returns how many
    were cleared. The caller commits."""
    automatic = "name IS NOT NULL AND COALESCE(name_source, '') <> 'manual'"
    photo_ids = {photo_id for (photo_id,) in conn.execute("SELECT DISTINCT photo_id FROM faces WHERE " + automatic)}
    return _rebuilt(conn, photo_ids, conn.execute("UPDATE faces SET " + _unassign(conn) + " WHERE " + automatic).rowcount)


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
    """{id: (photo_path, Ref or None, excluded)} of the faces among `face_ids` that exist."""
    found = {}
    for chunk in _chunks(face_ids):
        for face_id, photo_path, tag_id, name, excluded in store_roots.natives(conn, conn.execute(
                "SELECT f.id, p.path, f.tag_id, f.name, f.excluded FROM faces f" + PHOTO
                + " WHERE f." + _in(chunk), chunk).fetchall(), 1):
            found[face_id] = (photo_path, _ref(tag_id, name), excluded)
    return found


def named_among(conn, face_ids):
    """[(photo path, Ref)] of the faces among `face_ids` that carry a person and are not excluded -- and only those: ignoring
    a cluster sends thousands of nameless faces, whose photos are not read. By the faces' key, then each one's photo by its."""
    found = []
    for chunk in _chunks(face_ids):
        found.extend((photo_path, Ref(tag_id, name)) for photo_path, tag_id, name in store_roots.natives(conn, conn.execute(
            "SELECT p.path, f.tag_id, f.name FROM faces f" + PHOTO + " WHERE f." + _in(chunk)
            + " AND f.name IS NOT NULL AND f.excluded = 0", chunk).fetchall(), 0))
    return found


def decided_by_id(conn, face_ids):
    """{face id: (person, name_source)} of the faces among `face_ids` that carry a name and are not excluded: what they were, for a
    job that can put them back (its Undo). `person` is the node's id, or -- for a face whose name no person is filed under -- the
    name (person_ids.target takes either)."""
    found = {}
    for chunk in _chunks(face_ids):
        found.update((face_id, (tag_id if tag_id is not None else name, source))
                     for face_id, name, tag_id, source, excluded in conn.execute(
            "SELECT f.id, f.name, " + _tag_id_of(conn) + ", f.name_source, f.excluded FROM faces f WHERE f." + _in(chunk), chunk)
            if name is not None and not excluded)
    return found


def _tag_id_of(conn):
    """The select item for a face's person id: the column, or NULL before migration 21."""
    return "f.tag_id" if person_ids.present(conn) else "NULL"


def reinstate(conn, prior):
    """Give each face of `prior` ({face id: (person, name_source)}) the person and decider it had, when it is unnamed and not ruled
    out now (a face named, or ruled out, since is somebody's newer decision). A person merged away or removed since is not given
    back (their faces stay unnamed). Returns the ids reinstated. The caller commits."""
    done = []
    known = person_ids.read(conn)
    for face_id, (person, source) in prior.items():
        try:
            target = person_ids.target(conn, person, known)
        except person_ids.PersonProblem:
            continue
        if conn.execute("UPDATE faces SET " + _assign(conn) + ", name_source = ? WHERE id = ? AND name IS NULL AND excluded = 0",
                        _pair(conn, target) + (source, face_id)).rowcount:
            done.append(face_id)
    _rebuilt(conn, _photos_of(conn, done), len(done), known=known)
    return done


def name(conn, face_ids, person):
    """Name faces as a person's decision (name_source 'manual'), which re-clustering does
    not revise. `person` is an id, a tag path or a name (person_ids.target: refused when the id is stale, the name two
    people have, or it is a group). Excluded faces are left alone. Returns rows named. The caller commits."""
    known = person_ids.read(conn)
    given = _pair(conn, person_ids.target(conn, person, known))
    changed = sum(conn.execute(
        "UPDATE faces SET " + _assign(conn) + ", name_source = 'manual' WHERE " + _in(chunk) + " AND excluded = 0",
        list(given) + chunk).rowcount for chunk in _chunks(face_ids))
    return _rebuilt(conn, _photos_of(conn, face_ids), changed, known=known)


def confirm(conn, face_id):
    """A person confirms the name a face carries (an automatic one: clustering's, or its photo's tag's):
    name_source 'manual', the name and its spelling left as they are. Not an excluded face, a nameless
    one or one confirmed already. Returns rows changed. The caller commits."""
    return conn.execute("UPDATE faces SET name_source = 'manual' WHERE id = ? AND name IS NOT NULL AND excluded = 0"
                        " AND COALESCE(name_source, '') <> 'manual'", (face_id,)).rowcount


def name_if_unnamed(conn, face_id, person):
    """Give an unnamed, unexcluded face a person as a guess -- who decided is left alone,
    so re-clustering may revise it. Not a face somebody unmatched by hand: "this is
    nobody" is a decision, which a guess does not overrule (docs/findings.md, #643).
    Returns rows named. The caller commits."""
    known = person_ids.read(conn)
    given = _pair(conn, person_ids.target(conn, person, known))
    changed = conn.execute("UPDATE faces SET " + _assign(conn) + " WHERE id = ? AND name IS NULL AND excluded = 0"
                           " AND " + NOT_DECIDED_NOBODY % "", given + (face_id,)).rowcount
    return _rebuilt(conn, _photos_of(conn, [face_id]), changed, known=known)


def name_unnamed(conn, persons_by_id, vocabulary=None, known=None):
    """Give each face in {id: person} its person as a guess, as name_if_unnamed does -- only a face still
    unnamed, not excluded and not unmatched by hand -- in one statement per person and chunk, and rebuild
    their photos once: automatch's write (docs/findings.md, #659), which named a folder's faces one by
    one and rebuilt a photo for each. Returns the ids its UPDATE changed (RETURNING), in the order given:
    a face another process named meanwhile is not counted, inside a transaction or not (#665). The
    caller commits, inside the transaction that read the faces it chose. `vocabulary`: the tree's PeopleVocabulary when the
    caller has read it (_rebuilt); `known`: its person_ids.People, read in the same transaction."""
    guard = " AND name IS NULL AND excluded = 0 AND " + NOT_DECIDED_NOBODY % ""
    targets, known = _targets(conn, persons_by_id.values(), known)
    by_person = collections.defaultdict(list)
    for face_id, person in persons_by_id.items():
        by_person[person].append(face_id)
    named = set()
    for person, face_ids in by_person.items():
        given = _pair(conn, targets[person])
        for chunk in _chunks(face_ids):
            named.update(face_id for (face_id,) in conn.execute(
                "UPDATE faces SET " + _assign(conn) + " WHERE " + _in(chunk) + guard + " RETURNING id",
                list(given) + chunk).fetchall())
    done = [face_id for face_id in persons_by_id if face_id in named]
    _rebuilt(conn, _photos_of(conn, done), len(done), vocabulary, known)
    return done


def _carries(conn, target, alias=""):
    """(SQL, parameters): a face (of the table aliased `alias`, with its dot) carries the person `target` (id, name) is: the
    id, or -- an unresolved name -- the name with no id."""
    tag_id, name = target
    if tag_id is not None:
        return "%stag_id = ?" % alias, [tag_id]
    return ("%sname = ? AND %stag_id IS NULL" % (alias, alias) if person_ids.present(conn) else "%sname = ?" % alias), [name]


def revert_automatic(conn, persons_by_id):
    """Take back guesses just made (automatch's names, when their photo's tag could not be written):
    each face in {id: person} that still carries that person AS A GUESS (name_source NULL) is unnamed again,
    as it was before, not as a decision. A face a person named, confirmed or unmatched meanwhile is theirs
    and is left. A person merged away or removed meanwhile (a stale id) is nobody's guess to take back. Returns the
    ids reverted. The caller commits."""
    known = person_ids.read(conn)
    by_person = collections.defaultdict(list)
    for face_id, person in persons_by_id.items():
        by_person[person].append(face_id)
    reverted = []
    for person, face_ids in by_person.items():
        try:
            where, params = _carries(conn, person_ids.target(conn, person, known))
        except person_ids.PersonProblem:
            continue
        for chunk in _chunks(face_ids):
            reverted.extend(face_id for (face_id,) in conn.execute(
                "UPDATE faces SET " + _unassign(conn) + " WHERE " + _in(chunk) + " AND " + where
                + " AND name_source IS NULL RETURNING id", chunk + params).fetchall())
    _rebuilt(conn, _photos_of(conn, reverted), len(reverted), known=known)
    return reverted


def unname(conn, face_ids, source="manual"):
    """Take the names off faces, recording who decided in name_source: 'manual' for
    "this is nobody", None for an undone guess. Returns rows changed. The caller commits."""
    changed = sum(conn.execute(
        "UPDATE faces SET " + _unassign(conn) + ", name_source = ? WHERE " + _in(chunk),
        [source] + chunk).rowcount for chunk in _chunks(face_ids))
    return _rebuilt(conn, _photos_of(conn, face_ids), changed)


def unname_person(conn, tag_ids, source=None):
    """Take the people whose nodes are `tag_ids` off every face that names them (the tag tree's delete of a person, with force):
    the faces become unnamed and unreviewed -- name_source `source`, NULL by default, so they are not "nobody" for good --
    and the photos they were in are rebuilt, in the same transaction as the tree row goes. By the node's index. Returns rows
    changed. The caller commits."""
    from tagpup.store import journal   # the journal imports the people; not at import
    photo_ids, changed, before = set(), 0, {}
    for chunk in _chunks(sorted(set(tag_ids))):
        marks = ",".join("?" * len(chunk))
        for face_id, photo_id, name, held_source, held_person in conn.execute(
                "SELECT id, photo_id, name, name_source, tag_id FROM faces WHERE tag_id IN (%s)" % marks, chunk):
            photo_ids.add(photo_id)
            before[face_id] = (name, held_source, held_person)
        changed += conn.execute("UPDATE faces SET " + _unassign(conn) + ", name_source = ? WHERE tag_id IN (%s)" % marks,
                                [source] + chunk).rowcount
    # One journaled change of the faces' identity, in this transaction (History puts their names and decisions back).
    journal.record_faces(conn, journal.PERSON_DELETED, before)
    return _rebuilt(conn, photo_ids, changed)


def unname_photo(conn, photo_path):
    """Take the names off every named face in a photo, as a decision. Returns rows
    changed. By equality: a LIKE pass as well cleared every name in IMG-1234.jpg along
    with IMG_1234.jpg. The caller commits.

    Only the faces that had a name: marking the photo's nameless faces 'manual' as well
    called each of them "nobody" for good, and automatch never named them again
    (docs/findings.md, #656)."""
    where, params = _on_photo(conn, photo_path)
    changed = conn.execute("UPDATE faces SET " + _unassign(conn) + ", name_source = 'manual' WHERE " + where
                           + " AND name IS NOT NULL", params).rowcount
    return _rebuilt(conn, {photo_id for (photo_id,) in conn.execute(
        "SELECT DISTINCT photo_id FROM faces WHERE " + where, params)}, changed)


def exclude(conn, face_ids, reason):
    """Take faces out of identity work, their names with them, as a decision. Returns
    rows excluded. The caller commits."""
    changed = sum(conn.execute(
        "UPDATE faces SET excluded = 1, excluded_reason = ?, " + _unassign(conn) + ", name_source = 'manual'"
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


def named_elsewhere_in_photo(conn, photo_path, person, face_id, decided_only=False):
    """Does a face in the photo other than `face_id` carry the person (an id, a tag path or a name: person_ids.target)? By
    the node's id, or -- a name no person is filed under -- by the name's equality: a LIKE retry scanned every face row, and
    read an underscore in a file name as any character. With `decided_only`, only a person somebody decided counts, not a
    guess (name_source NULL)."""
    where, params = _on_photo(conn, photo_path)
    carried, carried_params = _carries(conn, person_ids.target(conn, person))
    return conn.execute("SELECT 1 FROM faces WHERE " + where + " AND " + carried + " AND id != ?"
                        + (" AND name_source IS NOT NULL" if decided_only else ""),
                        tuple(params) + tuple(carried_params) + (face_id,)).fetchone() is not None


def guesses_named(conn, photo_path, person, face_id):
    """{id: person} of the faces of the photo other than `face_id` that carry the person as a guess (name_source NULL), the
    person as it was given."""
    where, params = _on_photo(conn, photo_path)
    carried, carried_params = _carries(conn, person_ids.target(conn, person))
    return {other: person for (other,) in conn.execute(
        "SELECT id FROM faces WHERE " + where + " AND " + carried + " AND id != ? AND name_source IS NULL",
        tuple(params) + tuple(carried_params) + (face_id,))}


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

def counts_by_person(conn):
    """[(Ref, faces)] for everyone named, most faces first: a person is the node (the id), so two people called alike are two
    rows; a name no person is filed under is one row by its name (without case)."""
    counted = {}
    for tag_id, name, count in conn.execute("SELECT tag_id, name, COUNT(*) FROM faces WHERE name IS NOT NULL"
                                           " GROUP BY tag_id, name").fetchall():
        key = person_ids.key_of(tag_id, name)
        seen = counted.setdefault(key, [Ref(tag_id, name), 0])
        seen[1] += count
    return sorted(((ref, count) for ref, count in counted.values()), key=lambda found: -found[1])


def identify_candidates(conn):
    """(id, photo_path, the photo's people as JSON [[id, name]], embedding length) of every nameless face
    still in play: the Identify Faces queue.

    LENGTH(embedding) rather than the embedding: the queue groups faces by the people
    their photo names and counts them, and only needs to know a face HAS one -- a face
    without cannot take part in identifying. Selecting the column read 380 MB of BLOB
    to answer a question about integers.
    """
    return store_roots.natives(conn, conn.execute(
        "SELECT f.id, p.path, " + PEOPLE_REFS_JSON + ", LENGTH(f.embedding) FROM faces f" + PHOTO
        + " WHERE f.name IS NULL AND f.excluded = 0").fetchall(), 1)


def unnamed_for_matching(conn):
    """(id, photo_path, box JSON, prob, mtime, embedding, year, people as JSON [[id, name]]) of every
    nameless face still in play, for a person's Identify grid."""
    return store_roots.natives(conn, conn.execute(
        "SELECT f.id, p.path, f.box, f.prob, p.mtime, f.embedding, p.year, " + PEOPLE_REFS_JSON + ""
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
    """{photo_path as stored: the people on its faces, as person_ids.key_of}, for every photo with a named face."""
    named = {}
    for photo_path, tag_id, name in store_roots.natives(conn, conn.execute(
            "SELECT p.path, f.tag_id, f.name FROM faces f" + PHOTO + " WHERE f.name IS NOT NULL").fetchall(), 0):
        named.setdefault(photo_path, set()).add(person_ids.key_of(tag_id, name))
    return named


def count_excluded(conn):
    """How many faces are excluded. idx_faces_identify answers it without the rows."""
    return conn.execute("SELECT COUNT(*) FROM faces WHERE excluded = 1").fetchone()[0]


def for_named_matrix(conn):
    """(id, Ref, embedding) of every named face that has an embedding and is not
    excluded: what a face is compared against to say who it resembles."""
    return [(face_id, Ref(tag_id, name), embedding) for face_id, tag_id, name, embedding in conn.execute(
        "SELECT id, tag_id, name, embedding FROM faces"
        " WHERE name IS NOT NULL AND embedding IS NOT NULL AND excluded = 0").fetchall()]


def for_decided_matrix(conn):
    """(id, Ref, embedding, name_source, photo_id) of every named face that has an embedding
    and is not excluded: the candidates for what automatch compares a face against. The
    caller keeps those a person decided (tagpup.services.identify.decided_faces)."""
    return [(face_id, Ref(tag_id, name), embedding, source, photo_id)
            for face_id, tag_id, name, embedding, source, photo_id in conn.execute(
                "SELECT id, tag_id, name, embedding, name_source, photo_id FROM faces"
                " WHERE name IS NOT NULL AND embedding IS NOT NULL AND excluded = 0").fetchall()]


def embedding_row(conn, face_id):
    """(embedding,) of one face, or None when there is no such face."""
    return conn.execute("SELECT embedding FROM faces WHERE id = ?", (face_id,)).fetchone()


def in_photo_with_names(conn, photo_path):
    """(id, box JSON, Ref or None, embedding, excluded) of each face in one photo."""
    where, params = _on_photo(conn, photo_path)
    return [(face_id, box, _ref(tag_id, name), embedding, excluded)
            for face_id, box, tag_id, name, embedding, excluded in conn.execute(
                "SELECT id, box, tag_id, name, embedding, excluded FROM faces WHERE " + where, params).fetchall()]


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


def count_named(conn, person):
    """How many faces carry the person (an id, a tag path or a name: person_ids.target): by the node's id (idx_faces_tag)."""
    where, params = _carries(conn, person_ids.target(conn, person))
    return conn.execute("SELECT COUNT(*) FROM faces WHERE " + where, params).fetchone()[0]


def count_of_people(conn, person_ids_, named=None):
    """How many faces name any of the people `person_ids_` (their nodes' ids; idx_faces_tag) -- and, with `named`, now carry that
    name: what a rename reports, counted after the write."""
    ids = list(person_ids_)
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    if named is None:
        return conn.execute("SELECT COUNT(*) FROM faces WHERE tag_id IN (%s)" % marks, ids).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM faces WHERE tag_id IN (%s) AND name = ?" % marks, ids + [named]).fetchone()[0]


def person_embeddings(conn, person):
    """(embedding, mtime, year, photo_path) of each of a person's faces that
    has an embedding: what their era-aware centroids are made from."""
    where, params = _carries(conn, person_ids.target(conn, person), "f.")
    return store_roots.natives(conn, conn.execute(
        "SELECT f.embedding, p.mtime, p.year, p.path FROM faces f" + PHOTO
        + " WHERE " + where + " AND f.embedding IS NOT NULL", params).fetchall(), 3)


def person_page(conn, person, limit, offset):
    """(id, photo_path, box JSON, prob, mtime, embedding, year) of a page of a
    person's faces. A negative limit is no limit."""
    where, params = _carries(conn, person_ids.target(conn, person), "f.")
    return store_roots.natives(conn, conn.execute(
        "SELECT f.id, p.path, f.box, f.prob, p.mtime, f.embedding, p.year"
        " FROM faces f" + PHOTO + " WHERE " + where + " LIMIT ? OFFSET ?",
        list(params) + [limit, offset]).fetchall(), 1)


def excluded_for_review(conn):
    """(id, photo_path, box JSON, prob, mtime, year, excluded_reason) of every
    excluded face, the latest excluded first."""
    return store_roots.natives(conn, conn.execute(
        "SELECT f.id, p.path, f.box, f.prob, p.mtime, p.year, f.excluded_reason"
        " FROM faces f" + PHOTO + " WHERE f.excluded = 1 ORDER BY f.id DESC").fetchall(), 1)


# ---- What TagPup's photo panel and the CLI read -----------------------------------------

def in_photo_for_panel(conn, photo_path):
    """(id, box JSON, Ref or None, prob, embedding, excluded, excluded_reason) of each face in one
    photo, in detection order."""
    where, params = _on_photo(conn, photo_path)
    return [(face_id, box, _ref(tag_id, name), prob, embedding, excluded, reason)
            for face_id, box, tag_id, name, prob, embedding, excluded, reason in conn.execute(
                "SELECT id, box, tag_id, name, prob, embedding, excluded, excluded_reason"
                " FROM faces WHERE " + where + " ORDER BY id", params).fetchall()]


def named_embeddings_elsewhere(conn, photo_path):
    """(Ref, embedding) of every named, unexcluded face in any photo but this one: who a
    face in it might be."""
    where, params = _on_photo(conn, photo_path)
    return [(Ref(tag_id, name), embedding) for tag_id, name, embedding in conn.execute(
        "SELECT tag_id, name, embedding FROM faces"
        " WHERE name IS NOT NULL AND excluded = 0 AND NOT (" + where + ")", params).fetchall()]


def photos_with_faces(conn):
    """The photo paths, as stored, that have any face row."""
    return {photo_path for (photo_path,) in store_roots.natives(conn, conn.execute(
        "SELECT path FROM photos WHERE id IN (SELECT photo_id FROM faces)").fetchall(), 0)}


def names_by_photo_key(conn):
    """{paths.key of a photo: the NAMES on its faces that are not excluded}: what a photo's list of people names
    (photos.people holds names, however many people share one)."""
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
    """(id, photo_path, box JSON, Ref or None, name_source, excluded) of every face: what was
    decided about each, without its embedding or crop."""
    return [(face_id, photo_path, box, _ref(tag_id, name), source, excluded)
            for face_id, photo_path, box, tag_id, name, source, excluded in store_roots.natives(conn, conn.execute(
                "SELECT f.id, p.path, f.box, f.tag_id, f.name, f.name_source, f.excluded FROM faces f" + PHOTO).fetchall(), 1)]


def delete(conn, face_ids):
    """Delete faces by id. Returns rows deleted. The caller commits."""
    photo_ids = _photos_of(conn, face_ids)
    removed = sum(conn.execute("DELETE FROM faces WHERE " + _in(chunk), chunk).rowcount
                  for chunk in _chunks(face_ids))
    if removed:
        faces_detected.forget_faceless(conn, photo_ids)
    return _rebuilt(conn, photo_ids, removed)


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
