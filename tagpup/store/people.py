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

from tagpup.core import vocabulary
from tagpup.store import roots as store_roots
from tagpup.store import db, derived, person_ids, search_index

#: A photo's people as a JSON list, in order, for a query whose photos are `p`: what
#: `photos.people` held, so every reader gets the shape it always had.
PEOPLE_JSON = ("(SELECT json_group_array(name) FROM (SELECT name FROM photo_people"
               " WHERE photo_id = p.id ORDER BY position))")

#: A photo's people as a JSON list of [the node's id or null, the name], in order, for a query whose photos are `p`: who
#: a photo lists, told apart by id (the Identify Faces queue groups by it).
PEOPLE_REFS_JSON = ("(SELECT json_group_array(json_array(tag_id, name)) FROM (SELECT tag_id, name FROM photo_people"
                    " WHERE photo_id = p.id ORDER BY position))")

#: How many photos one pass of rebuild reads at a time.
CHUNK = 500

#: A face `f` is the person a listed person `pp` is: by the node's id, else -- a name no person is filed under -- the name.
SAME_PERSON = "(f.tag_id = pp.tag_id OR (pp.tag_id IS NULL AND f.tag_id IS NULL AND f.name = pp.name COLLATE NOCASE))"


def _faces_named(conn, photo_ids, loose=None):
    """{photo id: the people on its faces that are not excluded, as Refs (id, name), in detection order}. A face with a name and
    no id is added to `loose` as (face id, name), when it is given: a name written by something that knew no ids."""
    named = collections.defaultdict(list)
    marks = ",".join("?" * len(photo_ids))
    tag_id = "tag_id" if person_ids.present(conn) else "NULL"
    for face_id, photo_id, held, name in conn.execute(
            "SELECT id, photo_id, " + tag_id + ", name FROM faces WHERE photo_id IN (%s) AND name IS NOT NULL AND name != ''"
            " AND COALESCE(excluded, 0) = 0 ORDER BY id" % marks, photo_ids):
        named[photo_id].append(vocabulary.Ref(held, name))
        if held is None and loose is not None:
            loose.append((face_id, name))
    return named


def _differences(conn, photo_ids, known, loose=None):
    """(photo id, its people by the rule as [(id, name, source)]) of each photo in `photo_ids`
    -- every photo, without -- whose rows say otherwise. `loose`: see _faces_named."""
    if known is None:
        from tagpup.store import taxonomy   # taxonomy imports this module
        known = taxonomy.read_people_vocabulary(conn)
    if photo_ids is None:
        photo_ids = [photo_id for (photo_id,) in conn.execute("SELECT id FROM photos")]
    photo_ids = sorted(set(photo_ids))
    for start in range(0, len(photo_ids), CHUNK):
        chunk = photo_ids[start:start + CHUNK]
        marks = ",".join("?" * len(chunk))
        named = _faces_named(conn, chunk, loose)
        held = collections.defaultdict(list)
        for photo_id, held_id, name, source in conn.execute(
                "SELECT photo_id, " + ("tag_id" if person_ids.present(conn) else "NULL") + ", name, source FROM photo_people"
                " WHERE photo_id IN (%s) ORDER BY photo_id, position" % marks, chunk):
            held[photo_id].append((held_id, name, source))
        for photo_id, raw_json, tags_json in conn.execute(
                "SELECT id, raw_metadata, tags FROM photos WHERE id IN (%s)" % marks, chunk).fetchall():
            try:
                raw = json.loads(raw_json) if raw_json else {}
                tags = json.loads(tags_json) if tags_json else []
            except (TypeError, ValueError):
                raw, tags = {}, []
            if not isinstance(tags, list):
                tags = []   # a damaged read wrote null, or a string: no keywords, as derived.keywords_of reads it
            people = [(ref.id, ref.name, source)
                      for ref, source in vocabulary.people_rows(raw, tags, named.get(photo_id, []), known)]
            if people != held.get(photo_id, []):
                yield photo_id, people


def rebuild(conn, photo_ids=None, known=None, ids=None):
    """Write the people of each photo in `photo_ids` -- every photo, without -- by the one
    rule (vocabulary.people_rows: each person by the id of their node, a person with two faces
    or two keywords once, two people called alike both), and settle each row written
    (person_ids.follow_listed; every row, when every photo is rebuilt). `known` is the tree's
    PeopleVocabulary, and `ids` its person_ids.People, each read from `conn` when not given.
    A face of these photos with a name and no id (written by something that knew none) is given the person its name is, when
    exactly one is called so (person_ids.follow_faces' rule). Returns how many photos' people changed. The caller commits."""
    written, loose = [], []
    for photo_id, people in list(_differences(conn, photo_ids, known, loose)):
        conn.execute("DELETE FROM photo_people WHERE photo_id = ?", (photo_id,))
        if person_ids.present(conn):
            conn.executemany("INSERT INTO photo_people (photo_id, position, name, source, tag_id) VALUES (?, ?, ?, ?, ?)",
                             [(photo_id, n, name, source, tag_id) for n, (tag_id, name, source) in enumerate(people)])
        else:
            conn.executemany("INSERT INTO photo_people (photo_id, position, name, source) VALUES (?, ?, ?, ?)",
                             [(photo_id, n, name, source) for n, (_tag_id, name, source) in enumerate(people)])
        written.append(photo_id)
    if loose:
        person_ids.settle_faces(conn, [face_id for face_id, _name in loose], ids)
    # Only the rows written can be without their id: a tree edit gives every other its new one
    # (tree_edit), and reading the tree for each of 5,000 one-photo rebuilds would cost them.
    person_ids.follow_listed(conn, None if photo_ids is None else written, ids)
    # The people are words a search finds (tagpup.store.search_index): of the photos whose people changed.
    search_index.refresh(conn, written)
    return len(written)


def stale(conn):
    """The ids of the photos whose people are not what the rule makes them: a writer that
    changed keywords, faces or the tree without rebuilding. Reads only."""
    return [photo_id for photo_id, _people in _differences(conn, None, None)]


def repair(db_path, photo_ids):
    """Rebuild the people of `photo_ids` (what `stale` found, read before and outside the write: reading
    every photo's metadata takes seconds, and the write lock is held for the writing only) by the one
    rule, under the library's write lock. Only photos whose rows still differ are written. Returns how
    many photos' people changed."""
    return db.write_with_connection(db_path, lambda conn: rebuild(conn, photo_ids) if photo_ids else 0,
                                    label="photos' people")


def rebuild_photos(conn, photo_paths, known=None):
    """rebuild, for the photos at `photo_paths`, however they are spelled. Returns how many
    photos' people changed. The caller commits."""
    ids = []
    for photo_path in photo_paths:
        where, params = store_roots.sql_equals(conn, "path", photo_path)
        ids += [photo_id for (photo_id,) in conn.execute("SELECT id FROM photos WHERE " + where, params)]
    return rebuild(conn, ids, known) if ids else 0


def keyword_keys(conn):
    """{photo id: the people its keywords name, as person_ids.key_of -- the node's id, or the name's key for a name no
    person is filed under}, for each photo with a face named and not excluded. photo_people is read by its primary key,
    one photo at a time."""
    listed = collections.defaultdict(set)
    tag_id = "tag_id" if person_ids.present(conn) else "NULL"
    for photo_id, held, name in conn.execute(
            "SELECT photo_id, " + tag_id + ", name FROM photo_people WHERE source = 'keyword' AND photo_id IN"
            " (SELECT photo_id FROM faces WHERE name IS NOT NULL AND excluded = 0)"):
        listed[photo_id].add(person_ids.key_of(held, name))
    return listed


def on_faces_alone(conn, folder=None):
    """[(photo path as stored, Ref, decided)] of each person a photo lists from a face alone (`photo_people` source 'face':
    a face not ruled out names them, and no keyword does), by photo and position -- the drift between a face and the photo's
    tags (#861), which `tagpup_cli.py tags-from-faces` mends and the doctor counts. `decided`: some face of that name on the
    photo was named by a person (name_source 'manual'), not only guessed by clustering or automatch. A scan of photo_people
    (nothing indexes `source`; 100,000 rows read in milliseconds), then one photo row and its faces (idx_faces_photo_id) for
    each. With `folder`, only the photos under it at any depth: their range of the path index (paths.sql_under), the same rows
    the whole library's answer holds for those photos."""
    where, params = ("", ())
    if folder is not None:
        where, params = store_roots.sql_under(conn, "p.path", folder)
        where = " AND " + where
    return [(path, vocabulary.Ref(tag_id, name), decided) for path, tag_id, name, decided in conn.execute(
        "SELECT p.path, pp.tag_id, pp.name, EXISTS (SELECT 1 FROM faces f WHERE f.photo_id = pp.photo_id AND "
        + SAME_PERSON + " AND f.name_source = 'manual' AND f.excluded = 0)"
        " FROM photo_people pp JOIN photos p ON p.id = pp.photo_id"
        " WHERE pp.source = 'face'" + where + " ORDER BY pp.photo_id, pp.position", params).fetchall()]


def of_photo(conn, photo_path):
    """The people of one photo, in order; [] without a row."""
    where, params = store_roots.sql_equals(conn, "path", photo_path)
    return [name for (name,) in conn.execute(
        "SELECT pp.name FROM photo_people pp JOIN photos p ON p.id = pp.photo_id WHERE " + where
        + " ORDER BY pp.position", params)]


def _touched(before, after):
    """(keywords, roots) whose meaning as a person differs between two vocabularies."""
    keys = {k for k in set(before.by_keyword) | set(after.by_keyword)
            if (before.by_keyword.get(k), before.ids.get(k)) != (after.by_keyword.get(k), after.ids.get(k))}
    keys |= before.groups ^ after.groups   # a person given a tag under them, or a group emptied (#986)
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
            parts = vocabulary.segments(tag)   # none for a keyword that is only a separator (#88)
            if vocabulary.keyword_key(tag) in keys or (roots and parts and parts[0].lower() in roots):
                affected.append(photo_id)
                break
    return rebuild(conn, affected, after) if affected else 0


def photos_named_by(conn, nodes):
    """The ids of the photos whose people a change to the tree nodes `nodes` -- (tag,
    name) of each, as it was or is -- may have changed: those carrying a keyword spelled
    as one of their tags or names, or under one of them that is a root.

    follow_tree compares the tree's people from before an edit with after; a change the
    journal finishes at start (tagpup.store.journal) has only its rows, not the tree from
    before. A keyword whose person changes is one some changed node spells, by tag or by
    name (PeopleVocabulary.from_rows), or one under a root that changed, so this finds
    every photo follow_tree would, and some it would not, which rebuild leaves alone.
    """
    keys, roots = set(), set()
    for tag, name in nodes:
        keys.update(vocabulary.keyword_key(text) for text in (tag, name) if text)
        if tag and len(vocabulary.segments(tag)) == 1:
            roots.update(vocabulary.key(text) for text in (tag, name) if text)
    if not keys:
        return []
    affected = []
    for photo_id, tags_json in conn.execute("SELECT id, tags FROM photos WHERE tags IS NOT NULL AND tags != '[]'"):
        try:
            tags = json.loads(tags_json)
        except (TypeError, ValueError):
            continue
        for tag in tags:
            parts = vocabulary.segments(tag)
            if vocabulary.keyword_key(tag) in keys or (roots and parts and parts[0].lower() in roots):
                affected.append(photo_id)
                break
    return affected


def merge_person(conn, from_id, to_id):
    """Everything that names the person `from_id` -- the faces, the photos' lists -- names `to_id`, with the cache of their name
    (the tree edit that joins two tags that are one person calls this before the node `from_id` goes). Returns (rows moved, the
    ids of the photos whose list named `from_id` or had a face of them: rebuild them once the node is gone, so a photo that
    named both lists the person once). `to_id` must be a person (a leaf under a face root): PersonInUse otherwise, while faces
    name `from_id`. The caller commits."""
    users = [(table, conn.execute("SELECT 1 FROM %s WHERE tag_id = ? LIMIT 1" % table, (from_id,)).fetchone() is not None)
             for table in person_ids.TABLES]
    if not any(there for _table, there in users):
        return 0, set()
    known = person_ids.read(conn)
    target = known.by_id.get(to_id)
    if target is None:
        raise person_ids.PersonInUse(conn.execute("SELECT COUNT(*) FROM faces WHERE tag_id = ?", (from_id,)).fetchone()[0],
                                     [known.node_names.get(from_id, "a person")])
    photos = {photo_id for (photo_id,) in conn.execute("SELECT DISTINCT photo_id FROM faces WHERE tag_id = ?", (from_id,))}
    photos |= {photo_id for (photo_id,) in conn.execute("SELECT DISTINCT photo_id FROM photo_people WHERE tag_id = ?", (from_id,))}
    moved = 0
    for table in person_ids.TABLES:
        moved += conn.execute("UPDATE %s SET tag_id = ?, name = ? WHERE tag_id = ?" % table,
                              (to_id, target.name, from_id)).rowcount
    return moved, photos


def sharing(conn, first_id, second_id):
    """(photos whose list of people names both persons, photos with a face of each) -- what merging two people who are one
    would leave naming the one person twice in a photo: both faces stay named, the list names them once. By
    idx_photo_people_tag and idx_faces_tag. Reads only."""
    listed = conn.execute(
        "SELECT COUNT(*) FROM (SELECT photo_id FROM photo_people WHERE tag_id IN (?, ?) GROUP BY photo_id"
        " HAVING COUNT(DISTINCT tag_id) = 2)", (first_id, second_id)).fetchone()[0]
    faced = conn.execute(
        "SELECT COUNT(*) FROM (SELECT photo_id FROM faces WHERE tag_id IN (?, ?) GROUP BY photo_id"
        " HAVING COUNT(DISTINCT tag_id) = 2)", (first_id, second_id)).fetchone()[0]
    return listed, faced


def follow_nodes(conn, nodes):
    """Rebuild the photos whose people a change to the tree nodes `nodes` may have changed
    (photos_named_by). Returns how many photos' people changed. The caller commits."""
    affected = photos_named_by(conn, nodes)
    return rebuild(conn, affected) if affected else 0


@contextlib.contextmanager
def tree_edit(conn):
    """An edit of the tag tree on `conn`, after which the photos whose people it changed
    are rebuilt (follow_tree), those whose keywords name a node it made, moved or took away
    have their keyword rows made again (derived.follow_tree), and every face and listed person
    whose name now means another node, or none, is given its id (person_ids.follow_tree). The
    caller commits."""
    from tagpup.store import taxonomy   # taxonomy imports this module
    before = taxonomy.read_people_vocabulary(conn)
    nodes = derived.tree_before(conn)
    ids = person_ids.read(conn)
    yield
    follow_tree(conn, before)
    derived.follow_tree(conn, nodes)
    person_ids.follow_tree(conn, ids)


def names(db_path, keywords_too=False, include_hidden=False):
    """The people the library knows, alphabetical (vocabulary.tag_sort_key): the names given to faces -- and, with
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
    return sorted((person for person in people if person), key=vocabulary.tag_sort_key)
