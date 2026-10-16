"""Who is in each photo, and who the library knows.

`photo_people` holds each photo's people, and `rebuild` alone writes it: from the
photo's keywords, the names on its faces that are not excluded, and the tag tree, by
the one rule (vocabulary.people_in_photo). Every row it writes holds the id of a node (owner, 2026-10-11: only the
id): a name no node has is given one first, and a name that cannot have one is not a person of the photo. It replaced `photos.people`, a list that
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



def _faces_named(conn, photo_ids):
    """{photo id: the people on its faces that are not excluded, as Refs (id, name), in detection order}."""
    named = collections.defaultdict(list)
    marks = ",".join("?" * len(photo_ids))
    for held, name, photo_id in conn.execute(
            "SELECT tag_id, name, photo_id FROM faces WHERE photo_id IN (%s) AND name IS NOT NULL AND name != ''"
            " AND COALESCE(excluded, 0) = 0 ORDER BY id" % marks, photo_ids):
        named[photo_id].append(vocabulary.Ref(held, name))
    return named


def _differences(conn, photo_ids, known, wanting=None):
    """(photo id, its people by the rule as [(id, name, source)]) of each photo in `photo_ids` -- every photo, without --
    whose rows say otherwise. A person the rule names with no id (a face named before names had ids, a keyword with no node) is
    not listed. `wanting`, when given, gets {key: name} of the names a photo's person fields hold that no node is called: the
    caller gives them nodes (_make_nodes)."""
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
        for photo_id, held_id, name, source in conn.execute(
                "SELECT photo_id, tag_id, name, source FROM photo_people"
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
            rows = vocabulary.people_rows(raw, tags, named.get(photo_id, []), known)
            if wanting is not None:
                # A keyword that is a PATH names its leaf as a person when it sits under a face root: when it has no node
                # (the writer of a keyword gives it one: derived.make_nodes) its leaf is no name to make a person of.
                pathed = {vocabulary.key(vocabulary.leaf_of(tag)) for tag in tags
                          if isinstance(tag, str) and vocabulary.SEPARATOR in vocabulary.normalize(tag)}
                wanting.update({vocabulary.key(ref.name): ref.name for ref, source in rows
                                if ref.id is None and source == "keyword" and vocabulary.key(ref.name) not in pathed})
            people = [(ref.id, ref.name, source) for ref, source in rows if ref.id is not None]
            if people != held.get(photo_id, []):
                yield photo_id, people


def _make_nodes(conn, wanting):
    """Give the names in `wanting` ({key: name}) their nodes (taxonomy.ensure_node). Returns whether any has one now, so the
    people are worked out again."""
    from tagpup.store import taxonomy   # taxonomy imports this module
    tree, found = derived.Tree.read(conn), False
    for name in wanting.values():
        found = taxonomy.ensure_node(conn, name, person=True, tree=tree)[0] is not None or found
    return found


def rebuild(conn, photo_ids=None, known=None, ids=None, make=False):
    """Write the people of each photo in `photo_ids` -- every photo, without -- by the one
    rule (vocabulary.people_rows: each person by the id of their node, a person with two faces
    or two keywords once, two people called alike both), and settle each row written
    (person_ids.follow_listed; every row, when every photo is rebuilt). `known` is the tree's
    PeopleVocabulary, and `ids` its person_ids.People, each read from `conn` when not given.
    A person is listed by the id of a node and no other way (owner, 2026-10-11): one the rule cannot give an id -- a keyword
    no node is (the writer of a keyword makes its node), a group's name, a face named before names had ids -- is not listed, so no
    row is written with a name and no id. `make`, which a writer that INTRODUCES names says (the index's read), gives a name a
    photo's person field holds that no node is called its node first (`_make_nodes`: every tag has an id), and the photo's
    people are worked out again; a tree edit that left a name behind makes nothing. Returns how many photos' people changed.
    The caller commits."""
    written, wanting = [], {}
    changes = dict(_differences(conn, photo_ids, known, wanting if make else None))
    if wanting:
        if _make_nodes(conn, wanting):
            ids = None   # read again: it holds none of the people just made
            again = None if photo_ids is None else sorted(set(photo_ids))
            if again is not None:
                for photo_id in again:
                    changes.pop(photo_id, None)
            else:
                changes = {}
            changes.update(_differences(conn, again, None))
    changes = list(changes.items())
    for photo_id, people in changes:
        conn.execute("DELETE FROM photo_people WHERE photo_id = ?", (photo_id,))
        conn.executemany("INSERT INTO photo_people (photo_id, position, name, source, tag_id) VALUES (?, ?, ?, ?, ?)",
                         [(photo_id, n, name, source, tag_id) for n, (tag_id, name, source) in enumerate(people)])
        written.append(photo_id)
    # Only the rows written can be without their id: a tree edit gives every other its new one
    # (tree_edit), and reading the tree for each of 5,000 one-photo rebuilds would cost them.
    person_ids.follow_listed(conn, None if photo_ids is None else written, ids)
    # The people are words a search finds (tagpup.store.search_index): of the photos whose people changed.
    search_index.refresh(conn, written)
    return len(written)


def listed_ids(conn):
    """{photo id: [the id of each person it lists, in order]} of every photo that lists someone (photo_people, by its key):
    who each photo names, by node, for clustering, which reads every photo once."""
    found = {}
    for photo_id, tag_id in conn.execute(
            "SELECT photo_id, tag_id FROM photo_people WHERE tag_id IS NOT NULL ORDER BY photo_id, position"):
        found.setdefault(photo_id, []).append(tag_id)
    return found


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


def rebuild_photos(conn, photo_paths, known=None, make=False):
    """rebuild, for the photos at `photo_paths`, however they are spelled. Returns how many
    photos' people changed. The caller commits."""
    ids = []
    for photo_path in photo_paths:
        where, params = store_roots.sql_equals(conn, "path", photo_path)
        ids += [photo_id for (photo_id,) in conn.execute("SELECT id FROM photos WHERE " + where, params)]
    return rebuild(conn, ids, known, make=make) if ids else 0


def keyword_keys(conn):
    """{photo id: the people its keywords name, as person_ids.key_of -- the node's id, or the name's key for a name no
    person is filed under}, for each photo with a face named and not excluded. photo_people is read by its primary key,
    one photo at a time."""
    listed = collections.defaultdict(set)
    for photo_id, held, name in conn.execute(
            "SELECT photo_id, tag_id, name FROM photo_people WHERE source = 'keyword' AND photo_id IN"
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
        "f.tag_id = pp.tag_id AND f.name_source = 'manual' AND f.excluded = 0)"
        " FROM photo_people pp JOIN photos p ON p.id = pp.photo_id"
        " WHERE pp.source = 'face'" + where + " ORDER BY pp.photo_id, pp.position", params).fetchall()]


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
    from tagpup.store import journal   # the journal imports this module; not at import
    photos = {photo_id for (photo_id,) in conn.execute("SELECT DISTINCT photo_id FROM faces WHERE tag_id = ?", (from_id,))}
    photos |= {photo_id for (photo_id,) in conn.execute("SELECT DISTINCT photo_id FROM photo_people WHERE tag_id = ?", (from_id,))}
    before = journal.read_faces(conn, [face_id for (face_id,) in conn.execute("SELECT id FROM faces WHERE tag_id = ?", (from_id,))])
    moved = 0
    for table in person_ids.TABLES:
        moved += conn.execute("UPDATE %s SET tag_id = ?, name = ? WHERE tag_id = ?" % table,
                              (to_id, target.name, from_id)).rowcount
    journal.record_faces(conn, journal.PERSON_MERGED, before)   # only a face whose name changed has anything to put back
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
    # The write lock first, THEN the reads: Python's sqlite3 opens a transaction only at the first write, so an edit that read the
    # tree first decided from what another process (the server beside a CLI run) committed before it wrote. The in-process lock
    # does not reach across processes; SQLite's does, and the busy timeout waits for it.
    if not conn.in_transaction:
        db.begin(conn, immediate=True)
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
