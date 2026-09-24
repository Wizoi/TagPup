"""Who is in each photo, and who the library knows.

`photo_people` holds each photo's people, and `rebuild` alone writes it: from the
photo's keywords, the names on its faces that are not excluded, and the tag tree, by
the one rule (vocabulary.people_in_photo). It replaced `photos.people`, a list that
seven face actions patched with a rule of their own, and that clustering, re-detection
and tree edits never updated (docs/findings.md, #63). Whatever changes one of the three
sources rebuilds the photos it touched: a keyword write, a face named, unnamed,
excluded, detected again or deleted, and a tree edit that changes who is a person
(`tree_edit`).

Also the names the pages offer while a person is typed, and renaming somebody. Both
servers listed them with their own copy, asking the tag tree about each person with a
query of its own.
"""
import collections
import contextlib
import json
import os

from tagpup.core import paths, vocabulary
from tagpup.store import db

#: A photo's people as a JSON list, in order, for a query whose photos are `p`: what
#: `photos.people` held, so every reader gets the shape it always had.
PEOPLE_JSON = ("(SELECT json_group_array(name) FROM (SELECT name FROM photo_people"
               " WHERE photo_id = p.id ORDER BY position))")

#: How many photos one pass of rebuild reads at a time.
CHUNK = 500


def _faces_named(conn, photo_ids):
    """{photo id: the names on its faces that are not excluded, in detection order}."""
    named = collections.defaultdict(list)
    marks = ",".join("?" * len(photo_ids))
    for photo_id, name in conn.execute(
            "SELECT photo_id, name FROM faces WHERE photo_id IN (%s) AND name IS NOT NULL AND name != ''"
            " AND COALESCE(excluded, 0) = 0 ORDER BY id" % marks, photo_ids):
        named[photo_id].append(name)
    return named


def _differences(conn, photo_ids, known):
    """(photo id, its people by the rule as [(name, source)]) of each photo in `photo_ids`
    -- every photo, without -- whose rows say otherwise."""
    if known is None:
        from tagpup.store import taxonomy   # taxonomy imports this module
        known = taxonomy.read_people_vocabulary(conn)
    if photo_ids is None:
        photo_ids = [photo_id for (photo_id,) in conn.execute("SELECT id FROM photos")]
    photo_ids = sorted(set(photo_ids))
    for start in range(0, len(photo_ids), CHUNK):
        chunk = photo_ids[start:start + CHUNK]
        marks = ",".join("?" * len(chunk))
        named = _faces_named(conn, chunk)
        held = collections.defaultdict(list)
        for photo_id, name, source in conn.execute(
                "SELECT photo_id, name, source FROM photo_people WHERE photo_id IN (%s)"
                " ORDER BY photo_id, position" % marks, chunk):
            held[photo_id].append((name, source))
        for photo_id, raw_json, tags_json in conn.execute(
                "SELECT id, raw_metadata, tags FROM photos WHERE id IN (%s)" % marks, chunk).fetchall():
            try:
                raw = json.loads(raw_json) if raw_json else {}
                tags = json.loads(tags_json) if tags_json else []
            except (TypeError, ValueError):
                raw, tags = {}, []
            from_keywords = {name.lower() for name in vocabulary.extract_people(raw, tags, known)}
            people = [(name, "keyword" if name.lower() in from_keywords else "face")
                      for name in vocabulary.people_in_photo(raw, tags, named.get(photo_id, []), known)]
            if people != held.get(photo_id, []):
                yield photo_id, people


def rebuild(conn, photo_ids=None, known=None):
    """Write the people of each photo in `photo_ids` -- every photo, without -- by the one
    rule. `known` is the tree's PeopleVocabulary, read from `conn` when not given.
    Returns how many photos' people changed. The caller commits."""
    changed = 0
    for photo_id, people in list(_differences(conn, photo_ids, known)):
        conn.execute("DELETE FROM photo_people WHERE photo_id = ?", (photo_id,))
        conn.executemany("INSERT INTO photo_people (photo_id, position, name, source) VALUES (?, ?, ?, ?)",
                         [(photo_id, n, name, source) for n, (name, source) in enumerate(people)])
        changed += 1
    return changed


def stale(conn):
    """The ids of the photos whose people are not what the rule makes them: a writer that
    changed keywords, faces or the tree without rebuilding. Reads only."""
    return [photo_id for photo_id, _people in _differences(conn, None, None)]


def rebuild_photos(conn, photo_paths, known=None):
    """rebuild, for the photos at `photo_paths`, however they are spelled. Returns how many
    photos' people changed. The caller commits."""
    ids = []
    for photo_path in photo_paths:
        where, params = paths.sql_equals("path", photo_path)
        ids += [photo_id for (photo_id,) in conn.execute("SELECT id FROM photos WHERE " + where, params)]
    return rebuild(conn, ids, known) if ids else 0


def of_photo(conn, photo_path):
    """The people of one photo, in order; [] without a row."""
    where, params = paths.sql_equals("path", photo_path)
    return [name for (name,) in conn.execute(
        "SELECT pp.name FROM photo_people pp JOIN photos p ON p.id = pp.photo_id WHERE " + where
        + " ORDER BY pp.position", params)]


def _touched(before, after):
    """(keywords, roots) whose meaning as a person differs between two vocabularies."""
    keys = {k for k in set(before.by_keyword) | set(after.by_keyword)
            if before.by_keyword.get(k) != after.by_keyword.get(k)}
    return keys, before.roots ^ after.roots


def follow_tree(conn, before):
    """Rebuild the photos whose people a tree edit changed: those carrying a keyword that
    names someone else now, or nobody, or that sits under a root that gained or lost its
    faces. `before` is the PeopleVocabulary from before the edit. Returns how many
    photos' people changed. The caller commits."""
    from tagpup.store import taxonomy   # taxonomy imports this module
    after = taxonomy.read_people_vocabulary(conn)
    keys, roots = _touched(before, after)
    if not keys and not roots:
        return 0
    affected = []
    for photo_id, tags_json in conn.execute("SELECT id, tags FROM photos WHERE tags IS NOT NULL AND tags != '[]'"):
        try:
            tags = json.loads(tags_json)
        except (TypeError, ValueError):
            continue
        for tag in tags:
            # Spelled as extract_people looks a keyword up.
            spelled = str(tag).replace("\\", "/").strip().lower()  # not a path: a keyword hierarchy
            if spelled in keys or (roots and vocabulary.segments(tag)[0].lower() in roots):
                affected.append(photo_id)
                break
    return rebuild(conn, affected, after) if affected else 0


@contextlib.contextmanager
def tree_edit(conn):
    """An edit of the tag tree on `conn`, after which the photos whose people it changed
    are rebuilt (follow_tree). The caller commits."""
    from tagpup.store import taxonomy   # taxonomy imports this module
    before = taxonomy.read_people_vocabulary(conn)
    yield
    follow_tree(conn, before)


def names(db_path, keywords_too=False, include_hidden=False):
    """The people the library knows, sorted: the names given to faces -- and, with
    `keywords_too`, the people photos' keywords name.

    A person whose every node in the tag tree is hidden from autocomplete, or sits in a
    hidden branch, is left out, unless `include_hidden`: that answers "does this person
    exist?", which a hidden person does -- asked of the shorter list, naming one of them
    offered to create them as someone new.
    """
    if not os.path.exists(db_path):
        return []
    conn = db.connect(db_path, timeout=30.0)
    try:
        people = {name for (name,) in conn.execute("SELECT DISTINCT name FROM faces WHERE name IS NOT NULL")}
        if keywords_too and conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table'"
                                         " AND name = 'photo_people'").fetchone():
            people.update(name for (name,) in conn.execute(
                "SELECT DISTINCT name FROM photo_people WHERE source = 'keyword'") if name)
        has_tree = conn.execute("SELECT name FROM sqlite_master WHERE type='table'"
                                " AND name='tag_taxonomy'").fetchone()
        if not include_hidden and has_tree:
            hidden = {tag for (tag,) in conn.execute(
                "SELECT tag FROM tag_taxonomy WHERE hidden_from_autocomplete = 1")}
            filed = {}
            for tag, name in conn.execute("SELECT tag, name FROM tag_taxonomy WHERE has_face = 1"):
                filed.setdefault(name, []).append(tag)
            people = {person for person in people
                      if not (filed.get(person) and all(vocabulary.hidden_by(tag, hidden)
                                                        for tag in filed[person]))}
    finally:
        conn.close()
    return sorted(person for person in people if person)


def rename(conn, old, new):
    """Faces named `old` are named `new`, and so is `old` in each photo's list of
    people, listed once. A name matches whatever its case (vocabulary.key). The caller
    commits. Returns (faces renamed, photos changed).

    Both apps did this with a copy of their own (docs/findings.md, #38). TagTuner
    matched faces whatever their case with LOWER(name), which no index serves, and
    TagPup matched exactly. Here the distinct names are read, and each spelling of
    `old` is renamed by its value. Both found the photos with LIKE over the JSON text,
    which spells a name past ASCII with escapes, so a name with an accent was not
    found; both spellings are looked for here.
    """
    wanted = vocabulary.key(old)
    spellings = [name for (name,) in conn.execute("SELECT DISTINCT name FROM faces WHERE name IS NOT NULL")
                 if vocabulary.key(name) == wanted]
    faces = sum(conn.execute("UPDATE faces SET name = ? WHERE name = ?", (new, spelling)).rowcount
                for spelling in spellings)

    # The photos listing `old`, from a face or a keyword, in any spelling.
    affected = [photo_id for photo_id, listed in conn.execute(
        "SELECT DISTINCT photo_id, name FROM photo_people") if vocabulary.key(listed) == wanted]
    affected += [photo_id for (photo_id,) in conn.execute(
        "SELECT DISTINCT photo_id FROM faces WHERE name = ?", (new,))]
    changed = rebuild(conn, affected) if affected else 0
    return faces, changed
