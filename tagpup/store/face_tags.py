"""A photo's person tag names its face (docs/findings.md, #788).

Tagging a photo with a person did nothing for its faces: only identity clustering, run on
demand, named a face from the photo's keywords, so a photo with one face and one person tag
kept an unnamed face, and the person had no reference for the next automatch.

When a photo has exactly one face still to be named and exactly one keyword person no face of
it carries, that face is that person. It is written as an automatic name (name_source NULL),
not a person's decision: clustering may revise it. The photo's own keyword confirms it, which
makes it a reference for later guesses (tagpup.services.identify.decided_faces, #640).

* A face to be named is unnamed, not excluded, and not marked "nobody" by hand (name_source
  'manual'): the owner's decisions are never overruled, as automatch's (#643).
* A keyword person is a photo_people row of source 'keyword' that is a leaf person of the tree
  (tag_id set): a branch tag is never a person, and an ambiguous name is no one. People are told apart by the id of
  their node throughout (two people called alike are two): a face carries a keyword person when its id is theirs.
* The tag alone is trusted only as far as the face looks like the person (#833). The detector often finds
  the other person in frame, not the tagged one: on photo_index a third of the faces the tag alone would
  name were below 0.70 of the person's decided faces. So when the person HAS decided faces, the face must
  be one a name is offered at (clustering.is_offered, 0.70) against them; otherwise it is left for Identify Faces. A person with NO
  decided face -- a new person on one photo -- is named by the tag alone, but ONLY when exactly one photo of
  theirs has a face to be named (#839): there is nothing to compare, and the face is then the person's first
  decided face. With several such photos none is named by the tag (clustering or Identify Faces names them):
  naming the first would make it the reference the next is compared with, and the answer would depend on
  the order of the photos and on whether they came in one batch or one at a time. The count is the whole
  library's, so it is the same either way. A face under clustering.BACKGROUND_AREA is never
  named by the tag. Because of the gate, a face named by this rule is a keyword-confirmed reference only
  when it passed it or the person had no decided face, so a stranger cannot seed a person.
* With several faces or several people nothing is guessed from the tag alone. Given
  `references` -- the decided faces as (ids, names, unit vectors), tagpup.services.identify.
  decided_faces -- a face is named only when exactly ONE person's decided face is as alike as
  naming unasked allows (clustering.names_unasked), that person is one the photo's keywords
  name and no face of it carries, and no other face of the photo is proposed for them. A face
  two people reach, or none, is left for Identify Faces. No new threshold: the owner's value.

Called by the writes that change a photo's keywords or record its faces, after the photo's
people are rebuilt (tagpup.store.photos, tagpup.services.faces), inside the writer's transaction;
`plan` is the read alone, for the backfill's dry run. Faces are written by
tagpup.store.faces.name_unnamed, whose guards hold at the write, whatever was read.
"""
import collections
import json

import numpy as np

from tagpup.core import clustering
from tagpup.core.vocabulary import Ref
from tagpup.store import db, faces, person_ids, removals
from tagpup.store import roots as store_roots

#: How many photos or faces go in one IN (...).
CHUNK = 500

#: faces.name_source of a face a person decided: a name, or "nobody".
BY_HAND = "manual"

class Choice(collections.namedtuple("Choice", "face_id photo_id person how source")):
    """A face to be named, and who names it: `person` (a Ref: the node's id and name), how the choice was made, "tag" (one
    face, one person) or "match" (alike one person's decided faces), and the face's name_source as read."""
    __slots__ = ()

    @property
    def name(self):
        return self.person.name


class Plan:
    """What `plan` found: the faces to name, and counts of the photos by what became of them.

    counts: `photos`, those with a face to be named and a keyword person no face carries;
    `one_face_one_person`, of them those named by the tag alone; `matched`, those with a face
    named by comparison; `left`, those with nothing named (for Identify Faces). Of the photos
    with one face and one person (`tag_alone`): `no_decided_face`, the person has none, so the
    tag alone names it; `like_them`, `like_them_somewhat` and `not_like_them`, the face's best
    cosine to their decided faces at or above 0.80, from 0.70 to 0.80, and below 0.70 (those are
    left); `not_decidable_yet`, the person has no decided face and several photos with a face to be named,
    all left; `unreadable_face`, a face with no embedding to compare, left; `background_sized`,
    faces under clustering.BACKGROUND_AREA that were not counted as faces to be named."""

    def __init__(self):
        self.named = []
        self.counts = collections.Counter(
            photos=0, one_face_one_person=0, matched=0, left=0, tag_alone=0, no_decided_face=0, like_them=0,
            like_them_somewhat=0, not_like_them=0, unreadable_face=0, background_sized=0, not_decidable_yet=0)


def _chunks(items):
    items = list(items)
    for start in range(0, len(items), CHUNK):
        yield items[start:start + CHUNK]


def _keyword_people(conn, photo_ids):
    """{photo id: [Ref]} of the keyword people of each photo in `photo_ids` -- every photo
    with one, without -- that are leaf people, in order."""
    leaf = " AND tag_id IS NOT NULL" if person_ids.present(conn) else ""
    found = collections.defaultdict(list)
    tag_id = "tag_id" if person_ids.present(conn) else "NULL"
    if photo_ids is None:
        rows = conn.execute("SELECT photo_id, " + tag_id + ", name FROM photo_people WHERE source = 'keyword'" + leaf
                            + " ORDER BY photo_id, position").fetchall()
    else:
        rows = []
        for chunk in _chunks(photo_ids):
            rows += conn.execute(
                "SELECT photo_id, " + tag_id + ", name FROM photo_people WHERE source = 'keyword'" + leaf
                + " AND photo_id IN (%s) ORDER BY photo_id, position" % ",".join("?" * len(chunk)),
                chunk).fetchall()
    for photo_id, held, name in rows:
        found[photo_id].append(Ref(held, name))
    return found


def _faces_of(conn, photo_ids):
    """{photo id: [(face id, Ref or None, name_source, excluded, box)]} of those photos' faces, no BLOB."""
    found = collections.defaultdict(list)
    for chunk in _chunks(photo_ids):
        for face_id, photo_id, tag_id, name, source, excluded, box in conn.execute(
                "SELECT id, photo_id, tag_id, name, name_source, excluded, box FROM faces WHERE photo_id IN (%s) ORDER BY id"
                % ",".join("?" * len(chunk)), chunk):
            found[photo_id].append((face_id, None if name is None else Ref(tag_id, name), source, excluded, box))
    return found


def _embeddings(conn, face_ids):
    found = {}
    for chunk in _chunks(face_ids):
        for face_id, blob in conn.execute(
                "SELECT id, embedding FROM faces WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk):
            found[face_id] = blob
    return found


def _key(person):
    return person_ids.key_of(person.id, person.name)


def _free_people(people, faces_here):
    """The keyword people (first spelling of each) that no named face of the photo carries."""
    carried = {_key(person) for _id, person, _source, excluded, _box in faces_here if person and not excluded}
    free, seen = [], set()
    for person in people:
        key = _key(person)
        if key is not None and key not in carried and key not in seen:
            seen.add(key)
            free.append(person)
    return free


def _not_taken_off(conn, free, faces_here):
    """`free` without the people the owner took off THIS photo on purpose (tagpup.store.removals): a face of the photo that a
    removal of that person unnamed is called nobody, and while that record stands the tag alone does not name ANOTHER face of the
    photo as them -- the rule only ever blocks on it; the owner names the right face by hand, or undoes the removal in History."""
    nobody = [face_id for face_id, person, source, excluded, _box in faces_here if not person and not excluded and source == BY_HAND]
    if not nobody or not free:
        return free
    taken = removals.removed_people(conn, nobody).values()
    return [person for person in free if not any(removals.same_person(each, person) for each in taken)]


def _small(box_json):
    """Is a face's stored box (JSON) a speck: under clustering.BACKGROUND_AREA?"""
    try:
        return clustering.is_background_sized(json.loads(box_json))
    except (TypeError, ValueError):
        return False


def _to_be_named(faces_here):
    """({id: name_source} of the faces nobody has named, ruled out or called nobody, specks left out,
    how many specks that was)."""
    found, specks = {}, 0
    for face_id, person, source, excluded, box in faces_here:
        if person or excluded or source == BY_HAND:
            continue
        if _small(box):
            specks += 1
        else:
            found[face_id] = source
    return found, specks


def _decided_of(conn, person):
    """The unit vectors of the faces a person decided of `person` (a Ref with the node's id): named by hand, or on a photo whose
    keywords name them (the references identify.decided_faces counts), none excluded. By the node's index (idx_faces_tag), one
    person at a time."""
    rows = conn.execute(
        "SELECT f.embedding FROM faces f WHERE f.tag_id = ? AND f.excluded = 0 AND f.embedding IS NOT NULL"
        " AND (f.name_source = 'manual' OR EXISTS (SELECT 1 FROM photo_people pp WHERE pp.photo_id = f.photo_id"
        " AND pp.source = 'keyword' AND pp.tag_id = f.tag_id))", (person.id,)).fetchall()
    vectors = []
    for (blob,) in rows:
        vec = np.frombuffer(blob, dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm:
            vectors.append(vec / norm)
    return np.stack(vectors) if vectors else None


def _photos_to_be_named(conn, person):
    """How many photos -- 2 at most: it is asked whether there is one or several -- have a face to be named
    (unnamed, not excluded, not called nobody, no speck) and `person` (a Ref with the node's id) among their keyword people. By
    the people's node index (idx_photo_people_tag), one person at a time, and from each of their photos to its faces
    by idx_faces_photo_id: left to itself SQLite drove the join from the library's 190,000 unnamed faces
    (idx_faces_identify), each probing photo_people, and the count took 134 ms a person (#844; the same join as
    tagpup.store.faces.UNDER_FROM_PHOTOS, #644)."""
    seen = set()
    for photo_id, box in conn.execute(
            "SELECT pp.photo_id, f.box FROM photo_people pp CROSS JOIN faces f INDEXED BY idx_faces_photo_id"
            " ON f.photo_id = pp.photo_id"
            " WHERE pp.tag_id = ? AND pp.source = 'keyword' AND f.name IS NULL AND f.excluded = 0"
            " AND COALESCE(f.name_source, '') <> 'manual'", (person.id,)):
        if photo_id not in seen and not _small(box):
            seen.add(photo_id)
            if len(seen) > 1:
                break
    return len(seen)


def _gated(conn, singles, result, cache, on_step=None):
    """The `singles` -- (photo id, face id, person, name_source), each a photo's one face to be named
    and one person -- that the tag alone may name (#833), as Choices, counted in `result.counts`.
    A person with no decided face is named; one with decided faces only if the face's best cosine to them
    is one a name is offered at (clustering.is_offered); one with none, only when no other photo of theirs has a face
    to be named (#839). The decided faces are read once per person, kept in `cache` ({the node's id: vectors or None},
    a batch's: #841), not once per photo."""
    vectors = _embeddings(conn, [face_id for _photo, face_id, _person, _source in singles])
    for number, (photo_id, face_id, person, source) in enumerate(singles):
        if on_step is not None and number % STEP == 0:
            on_step("checking", number, len(singles))
        result.counts["tag_alone"] += 1
        if person.id not in cache:
            cache[person.id] = _decided_of(conn, person)
        decided = cache[person.id]
        if decided is None:
            if _photos_to_be_named(conn, person) > 1:
                result.counts["not_decidable_yet"] += 1
                result.counts["left"] += 1
                continue
            result.counts["no_decided_face"] += 1
        else:
            blob = vectors.get(face_id)
            vec = np.frombuffer(blob, dtype=np.float32) if blob and len(blob) == decided.shape[1] * 4 else None
            norm = np.linalg.norm(vec) if vec is not None else 0
            if not norm:
                result.counts["unreadable_face"] += 1
                result.counts["left"] += 1
                continue
            best = float((decided @ (vec / norm)).max())
            if not clustering.is_offered(best):
                result.counts["not_like_them"] += 1
                result.counts["left"] += 1
                continue
            result.counts["like_them" if clustering.names_unasked(best) else "like_them_somewhat"] += 1
        result.named.append(Choice(face_id, photo_id, person, "tag", source))
        result.counts["one_face_one_person"] += 1


#: How many photos or faces a plan goes through between two calls of its `on_step`.
STEP = 50

#: How many faces are compared with every decided face at once: a block of faces x the decided
#: faces, 73 MB for 36,000 of them (services.faces.COMPARE_BLOCK).
BLOCK = 512


def _reached(vectors, references, on_step=None):
    """{face id: {the keys of the people some decided face of whom it is alike enough to}} for the
    faces of `vectors` {face id: embedding bytes} that can be compared: compared a block at a
    time, never a face at a time (a library's faces to place are tens of thousands). A person's key is
    person_ids.key_of."""
    _ids, people, matrix = references
    if matrix is None or not len(people):
        return {}
    width = matrix.shape[1]
    keys = [_key(person) for person in people]
    order = list(dict.fromkeys(keys))
    index = {key: n for n, key in enumerate(order)}
    code = np.array([index[key] for key in keys])
    usable = [(face_id, blob) for face_id, blob in vectors.items() if blob and len(blob) == width * 4]
    reached = {}
    for start in range(0, len(usable), BLOCK):
        if on_step is not None:
            on_step("comparing", start, len(usable))
        block = usable[start:start + BLOCK]
        stacked = np.stack([np.frombuffer(blob, dtype=np.float32) for _face, blob in block])
        norms = np.linalg.norm(stacked, axis=1)
        stacked = stacked / np.where(norms == 0, 1, norms)[:, None]
        rows, columns = np.nonzero(clustering.might_name_unasked(stacked @ matrix.T, 0.0))
        pairs = np.unique(rows.astype(np.int64) * len(order) + code[columns])
        for pair in pairs.tolist():
            row, person = divmod(pair, len(order))
            if norms[row]:
                reached.setdefault(block[row][0], set()).add(order[person])
    return reached


def _matched(unnamed, free, reached):
    """[(face id, Ref)] among `unnamed` that exactly one person's decided faces are alike enough
    to name unasked (`reached`), the person being one of `free`, and nobody else's face of the
    photo is proposed for."""
    wanted = {_key(person): person for person in free}
    proposed = collections.defaultdict(list)
    for face_id in unnamed:
        people = reached.get(face_id, ())
        if len(people) == 1 and next(iter(people)) in wanted:
            proposed[next(iter(people))].append(face_id)
    return [(faces_for[0], wanted[key]) for key, faces_for in proposed.items() if len(faces_for) == 1]


def guards(conn, choices):
    """What the plan read of each photo it names a face of, beyond the face itself, for the write to hold the change to (#869):
    ({photo id: its tags as stored}, {face id: Ref or None} of the photo's other faces, those the plan does not name). A person's tag
    taken off a planned photo, or another face of it named meanwhile, is a reason the plan no longer holds: the face itself is
    guarded by its own row (name NULL, excluded 0, name_source as read), but the photo's keyword people (its tags) and its
    other faces' names are what made it a candidate. Read on `conn` as it stands: inside the plan's own transaction, it is the
    state the plan read."""
    photo_ids = sorted({choice.photo_id for choice in choices})
    planned = {choice.face_id for choice in choices}
    tags = {}
    for chunk in _chunks(photo_ids):
        for photo_id, text in conn.execute("SELECT id, tags FROM photos WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk):
            tags[photo_id] = text
    siblings = {}
    for rows in _faces_of(conn, photo_ids).values():
        for face_id, person, _source, _excluded, _box in rows:
            if face_id not in planned:
                siblings[face_id] = person
    return tags, siblings


def plan(conn, photo_ids=None, references=None, cache=None, on_step=None):
    """A Plan: which faces of `photo_ids` -- every photo, without -- a keyword person names.
    Reads only, on `conn` as it stands; the photos with such a person are found from
    photo_people and their faces by idx_faces_photo_id, a chunk at a time, never a photo at a
    time. `references` as the module's docstring says -- or a function that gives them, called
    only when a photo needs them: without them, only the photos with one face to be named and
    one person are decided.

    `on_step(stage, done, total)`, if given, hears where the plan has got -- "reading", "checking" (the photos with
    one face and one person), "comparing" (the faces of the others against the decided faces), "deciding" -- every
    STEP photos or each block of faces, and may raise to stop it: the plan writes nothing, so nothing is left."""
    result = Plan()
    if on_step is not None:
        on_step("reading", 0, 1)
    people_of = _keyword_people(conn, None if photo_ids is None else sorted(set(photo_ids)))
    faces_of = _faces_of(conn, sorted(people_of))
    open_ones = []   # (photo id, unnamed face ids, free people) still to be compared
    singles = []     # (photo id, face id, person, name_source): one face, one person
    for photo_id in sorted(people_of):
        here = faces_of.get(photo_id, [])
        unnamed, specks = _to_be_named(here)
        free = _not_taken_off(conn, _free_people(people_of[photo_id], here), here)
        if not unnamed or not free:
            continue
        result.counts["background_sized"] += specks
        result.counts["photos"] += 1
        if len(unnamed) == 1 and len(free) == 1:
            (face_id, source), = unnamed.items()
            singles.append((photo_id, face_id, free[0], source))
        elif references is not None:
            open_ones.append((photo_id, unnamed, free))
        else:
            result.counts["left"] += 1
    if singles:
        _gated(conn, singles, result, {} if cache is None else cache, on_step)
    if open_ones:
        vectors = _embeddings(conn, [face_id for _photo, unnamed, _free in open_ones for face_id in unnamed])
        if on_step is not None:
            on_step("comparing", 0, len(vectors))
        references = references() if callable(references) else references
        reached = _reached(vectors, references, on_step)
        if on_step is not None:
            on_step("deciding", 0, len(open_ones))
        for photo_id, unnamed, free in open_ones:
            found = _matched(unnamed, free, reached)
            result.named += [Choice(face_id, photo_id, person, "match", unnamed[face_id]) for face_id, person in found]
            result.counts["matched" if found else "left"] += 1
    return result


def _remember(conn, cache, choices, done):
    """Add the vectors of the faces just named to the decided faces a batch holds of their people: they are
    keyword-confirmed now, and the next photo of the batch is compared with them as a save after it would."""
    named = {choice.face_id: choice.person for choice in choices if choice.face_id in set(done)}
    for face_id, blob in _embeddings(conn, list(named)).items():
        person = named[face_id]
        if person.id not in cache or not blob:
            continue
        vec = np.frombuffer(blob, dtype=np.float32)
        norm = np.linalg.norm(vec)
        if not norm:
            continue
        row = (vec / norm)[None, :]
        known = cache[person.id]
        cache[person.id] = row if known is None or known.shape[1] != row.shape[1] else np.vstack([known, row])


def name_photos(conn, photo_ids, references=None, vocabulary=None, batch=None):
    """Name the faces of `photo_ids` that their keyword people name (`plan`), in the caller's
    write: the people the plan read are read in the same transaction as the write, begun here
    when the caller has none open, so a person renamed by another process meanwhile is not
    written under the old spelling. Returns the ids of the faces named -- those the write
    changed, a face named or ruled out meanwhile not counted. `vocabulary` is the tree's people when the
    caller has read them (a batch of photos reads the tree once, #838); `batch` the run's derived.Batch, which
    keeps each person's decided faces for the run (#841). The caller commits."""
    photo_ids = sorted(set(photo_ids))
    if not photo_ids:
        return []
    if not conn.in_transaction:
        db.begin(conn, immediate=True)
    cache = batch.decided if batch is not None else None
    found = plan(conn, photo_ids, references, cache)
    if not found.named:
        return []
    done = faces.name_unnamed(conn, {choice.face_id: choice.person.id for choice in found.named}, vocabulary)
    if cache is not None and done:
        _remember(conn, cache, found.named, done)
    return done


def photo_ids_of(conn, photo_paths):
    """The ids of the rows of the photos at `photo_paths`, however they are spelled."""
    ids = []
    for photo_path in photo_paths:
        where, params = store_roots.sql_equals(conn, "path", photo_path)
        ids += [photo_id for (photo_id,) in conn.execute("SELECT id FROM photos WHERE " + where, params)]
    return ids


def name_paths(conn, photo_paths, references=None):
    """name_photos, for the photos at `photo_paths`."""
    return name_photos(conn, photo_ids_of(conn, photo_paths), references)
