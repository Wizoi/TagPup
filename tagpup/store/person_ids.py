"""People by the id of their node in the tag tree: the one conversion between a person's name, as
`faces.name` and `photo_people.name` hold it, and the `tag_taxonomy` node that is that person
(docs/ARCHITECTURE.md, "Identity by id", stage 1).

A person is a node of the tree with `has_face` set that is not a root (a root holding faces is a
category, as vocabulary.PeopleVocabulary reads it) and has no node under it: a branch tag cannot be a
person *(owner, 2026-10-04; docs/findings.md, #660)*. A name is that node when exactly one such node
is called it, compared as names are compared everywhere (`vocabulary.key`: trimmed, without case).
A name no person node is called -- none at all, or only a branch -- or that two are (the ambiguous
person path, #27), has no id: NULL, never a guess. The name itself is left as it is. `unresolved`
says which, and the doctor reports them.

Stage 1 is additive: `faces.tag_id` and `photo_people.tag_id` sit beside `name`, every read still
reads the name, and the id is DERIVED from the name and the tree -- what `People.id_of` gives the
name now. So it is kept wherever either changes, in the same transaction as the change, by this
module alone (tests/test_person_ids_single_owner.py):

* a face's name -- every writer of tagpup.store.faces ends in `_rebuilt`, which calls
  `follow_faces` for the photos it touched; `people.rename` calls `follow_names`;
* a photo's people -- `people.rebuild` alone writes them, and calls `follow_listed`;
* the tree -- `people.tree_edit` calls `follow_tree` after every edit;
* the journal -- `_derive`, after an apply, an undo or a settle, calls `follow_faces` for the photos
  whose faces it wrote and `sync` when it wrote the tree; `tag_id` is a derived column there
  (journal.DERIVED_COLUMNS), so an undo never holds a row to it and a row recorded before the
  column existed is put back and then given its id.

The tree is read inside the writer's transaction, every time, and never kept: a rename committed by
another process between two writes is what the second one reads. A version of the app from before
migration 21 still writing to the library leaves ids behind its names; `out_of_step` finds them and
`repair` (tools/doctor.py --person-ids --apply) puts them right.

Every function does nothing, and says 0, on a library without the column (before migration 21):
the migrations before it write faces and people too. The caller commits.
"""
import collections

from tagpup.core import vocabulary
from tagpup.store import db

#: The tables that name a person, and the column beside `name` that holds the node's id.
TABLES = ("faces", "photo_people")
COLUMN = "tag_id"

#: How many ids go in one IN (...).
CHUNK = 500


def present(conn):
    """Has the library on `conn` the column (migration 21)? Remembered on the connection once true:
    the migration adds it mid-connection, so a False is asked again."""
    if getattr(conn, "person_ids_ready", False):
        return True
    found = all(COLUMN in {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)} for table in TABLES)
    if found:
        try:
            conn.person_ids_ready = True
        except AttributeError:
            pass   # a connection that is not db.connect's cannot remember
    return found


class People:
    """The tree's people as names are matched to them: {name's key: the node's id}, or AMBIGUOUS
    when two nodes are called it; and the branches -- has_face nodes with nodes under them, which
    are not people -- by name, {key: [ids]}. Read once for one write, inside its transaction; never
    kept."""

    AMBIGUOUS = object()

    def __init__(self, nodes, parents=()):
        self.by_key, self.branches = {}, {}
        self.nodes = set()   # every person node's id, those two nodes are called alike too
        self.listing = []    # (id, tag, name) of each of them: what Directory tells the pages
        parents = set(parents)
        for node_id, tag, name in sorted(nodes):
            if not tag or vocabulary.SEPARATOR not in tag:
                continue   # a root: a category of people, not a person
            leaf = vocabulary.key(name)
            if not leaf:
                continue
            if node_id in parents:
                self.branches.setdefault(leaf, []).append(node_id)   # a branch tag is not a person
                continue
            self.nodes.add(node_id)
            self.listing.append((node_id, tag, name))
            self.by_key[leaf] = People.AMBIGUOUS if leaf in self.by_key else node_id

    @classmethod
    def read(cls, conn):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tag_taxonomy'").fetchone():
            return cls([])
        parents = [parent for (parent,) in conn.execute(
            "SELECT DISTINCT parent_id FROM tag_taxonomy WHERE parent_id IS NOT NULL")]
        return cls(conn.execute("SELECT id, tag, name FROM tag_taxonomy WHERE has_face = 1").fetchall(), parents)

    def id_of(self, name):
        """The id of the one person node called `name`; None for no name, no node or two."""
        if name is None:
            return None
        found = self.by_key.get(vocabulary.key(name))
        return None if found is None or found is People.AMBIGUOUS else found

    def why_not(self, name):
        """"branch" when no person node is called `name` but a branch is, "none" when neither is,
        "several" when more than one person node is, else None."""
        key = vocabulary.key(name)
        found = self.by_key.get(key)
        if found is None:
            return "branch" if key in self.branches else "none"
        return "several" if found is People.AMBIGUOUS else None

    def __eq__(self, other):
        return isinstance(other, People) and self.by_key == other.by_key

    def __hash__(self):
        return hash(tuple(sorted(self.by_key)))


def read(conn):
    """The People of the library on `conn`, as its tree stands in this transaction."""
    return People.read(conn)


class Directory:
    """The people of a library as the pages are told of them: each a dict
    `{"id", "name", "tag", "group", "shared"}` (docs/ARCHITECTURE.md, "People by id, stage 2", "The
    wire"). `shared` and `group` are tagpup.core.vocabulary.person_labels', computed over EVERY person of
    the library, so a list of one Sam still says which Sam. Read once for one answer, from the tree as it
    stands now, and never kept: a rename in another process is what the next answer reads.

    A row that names a person by NAME alone (every answer today: the id is not the key until part B)
    gets `of_name`: the person when exactly one is called it, the same fields with no id and no tag when
    two are (`shared`, and nothing says which), and None when none is -- a name no person tag has, a
    group, a bucket such as Unknown Faces."""

    def __init__(self, nodes):
        nodes = list(nodes)
        labels = vocabulary.person_labels([(node_id, tag) for node_id, tag, _name in nodes])
        self._records, self._by_key, self._by_tag = [], {}, {}
        for node_id, tag, name in sorted(nodes, key=lambda node: (vocabulary.tag_sort_key(node[2]), node[1])):
            label = labels.get(node_id, vocabulary.PersonLabel(False, ""))
            record = {"id": node_id, "name": name, "tag": tag, "group": label.group, "shared": label.shared}
            self._records.append(record)
            self._by_key.setdefault(vocabulary.key(name), []).append(record)
            self._by_tag[vocabulary.normalize(tag).lower()] = record

    @classmethod
    def read(cls, conn):
        return cls(People.read(conn).listing)

    def records(self):
        """Everyone, by name (vocabulary.tag_sort_key), then by group: a copy of each."""
        return [dict(record) for record in self._records]

    def of_name(self, name):
        """The person called `name` (without case), or None; see the class."""
        found = self._by_key.get(vocabulary.key(name))
        if not found:
            return None
        if len(found) == 1:
            return dict(found[0])
        return {"id": None, "name": str(name).strip(), "tag": None, "group": "", "shared": True}

    def of_tag(self, tag):
        """The person filed at the tag `tag`, or None."""
        found = self._by_tag.get(vocabulary.normalize(tag).lower()) if tag else None
        return dict(found) if found else None

    def of_reference(self, value):
        """The person a suggester's `name` is: a TAG PATH when the suggestion came from a face match
        (`Family/Immediate/Clara Ingersoll`: the suggester's item['tag']), else a bare name. A path is looked up
        by `of_tag` and so is exact even when the leaf is shared; a name by `of_name`."""
        if value and vocabulary.SEPARATOR in vocabulary.normalize(value):
            return self.of_tag(value)
        return self.of_name(value)

    def annotate(self, items, key="name", into="person"):
        """Give each dict of `items` a `person` beside the name it holds under `key` (None when it holds none, or a
        name no person is called): the fields a page labels the person by. Returns `items`."""
        for item in items:
            item[into] = self.of_name(item.get(key))
        return items


def _chunks(items):
    items = sorted(set(items))
    for start in range(0, len(items), CHUNK):
        yield items[start:start + CHUNK]


def _marks(chunk):
    return ",".join("?" * len(chunk))


def follow_faces(conn, photo_ids, known=None):
    """Give each face of the photos `photo_ids` the id its name gives now. Returns faces changed.
    idx_faces_photo_id finds them; the embedding is not read."""
    if not photo_ids or not present(conn):
        return 0
    known = known or read(conn)
    updates = []
    for chunk in _chunks(photo_ids):
        for face_id, name, held in conn.execute(
                "SELECT id, name, tag_id FROM faces WHERE photo_id IN (%s)" % _marks(chunk), chunk):
            wanted = known.id_of(name)
            if held != wanted:
                updates.append((wanted, face_id))
    conn.executemany("UPDATE faces SET tag_id = ? WHERE id = ?", updates)
    return len(updates)


def follow_listed(conn, photo_ids, known=None):
    """Give each person listed for the photos `photo_ids` (photo_people) the id their name gives now.
    Every photo, without `photo_ids`. Returns rows changed."""
    if not present(conn):
        return 0
    if photo_ids is None:
        return _sync_table(conn, "photo_people", known or read(conn))
    if not photo_ids:
        return 0
    known = known or read(conn)
    updates = []
    for chunk in _chunks(photo_ids):
        for photo_id, position, name, held in conn.execute(
                "SELECT photo_id, position, name, tag_id FROM photo_people WHERE photo_id IN (%s)" % _marks(chunk),
                chunk):
            wanted = known.id_of(name)
            if held != wanted:
                updates.append((wanted, photo_id, position))
    conn.executemany("UPDATE photo_people SET tag_id = ? WHERE photo_id = ? AND position = ?", updates)
    return len(updates)


def follow_names(conn, names, known=None):
    """Give every face and listed person called one of `names`, spelled exactly so, the id the name
    gives now: a rename of somebody's faces. One statement per name and table, by the name's index.
    Returns rows changed."""
    if not present(conn):
        return 0
    known = known or read(conn)
    changed = 0
    for name in {n for n in names if n is not None}:
        wanted = known.id_of(name)
        for table in TABLES:
            changed += conn.execute("UPDATE %s SET tag_id = ? WHERE name = ? AND tag_id IS NOT ?" % table,
                                    (wanted, name, wanted)).rowcount
    return changed


def _pairs(conn, table):
    """[(name, id held, rows)] of `table`: one per distinct pair, which idx_faces_person answers for
    faces from the index alone."""
    return conn.execute("SELECT name, tag_id, COUNT(*) FROM %s WHERE name IS NOT NULL GROUP BY name, tag_id"
                        % table).fetchall()


def _sync_table(conn, table, known):
    changed = 0
    for name, held, _rows in _pairs(conn, table):
        wanted = known.id_of(name)
        if held != wanted:
            changed += conn.execute("UPDATE %s SET tag_id = ? WHERE name = ? AND tag_id IS ?" % table,
                                    (wanted, name, held)).rowcount
    return changed


def sync(conn, known=None):
    """Give every face and every listed person the id their name gives now: the migration's
    backfill, the repair, and what follows an edit that changed who the tree's people are. One
    lookup per distinct name, never per row: the pairs of name and id held are read (from the index,
    for faces), and only a pair that is wrong is written. Returns {table: rows changed}."""
    if not present(conn):
        return {table: 0 for table in TABLES}
    known = known or read(conn)
    changed = {table: _sync_table(conn, table, known) for table in TABLES}
    # A face whose name was taken off by a writer that did not follow it.
    changed["faces"] += conn.execute("UPDATE faces SET tag_id = NULL WHERE name IS NULL AND tag_id IS NOT NULL").rowcount
    return changed


def follow_tree(conn, before):
    """After an edit of the tree: when who its people are changed (`before` is the People read
    before the edit), every name is matched again (sync). A node renamed, moved, deleted, flagged or
    added that names nobody new costs two reads of the tree. Returns rows changed."""
    if not present(conn):
        return 0
    after = read(conn)
    if after == before:
        return 0
    return sum(sync(conn, after).values())


# ---- What the doctor reads -------------------------------------------------------------------

Disagreement = collections.namedtuple("Disagreement", "rows examples")


def out_of_step(conn, table, examples=5):
    """(rows of `table` whose id is not what their name gives now, a few of their ids: face ids,
    or photo ids for photo_people). Reads only."""
    if not present(conn):
        return Disagreement(0, [])
    known = read(conn)
    rows, found = 0, []
    key = "id" if table == "faces" else "photo_id"
    for name, held, count in _pairs(conn, table):
        if held != known.id_of(name):
            rows += count
            if len(found) < examples:
                found += [row_id for (row_id,) in conn.execute(
                    "SELECT %s FROM %s WHERE name = ? AND tag_id IS ? ORDER BY %s LIMIT ?" % (key, table, key),
                    (name, held, examples - len(found)))]
    if table == "faces":
        stray = conn.execute("SELECT COUNT(*) FROM faces WHERE name IS NULL AND tag_id IS NOT NULL").fetchone()[0]
        if stray:
            rows += stray
            found += [row_id for (row_id,) in conn.execute(
                "SELECT id FROM faces WHERE name IS NULL AND tag_id IS NOT NULL ORDER BY id LIMIT ?",
                (max(0, examples - len(found)),))]
    return Disagreement(rows, sorted(set(found))[:examples])


Unresolved = collections.namedtuple("Unresolved", "none several branch")


def unresolved(conn):
    """The names faces and photos' people hold that have no id: those no person node is called
    (`none`) or more than one is (`several`), {name: rows of both tables}; and those called only by a
    branch -- a has_face node with nodes under it, such as Family/Coast, which is not a person
    *(owner, 2026-10-04; docs/findings.md, #660)* -- (`branch`: {name: ([the branches' ids], rows)}).
    Reported, not broken: no id is guessed, the names are left as they are, and the tree is the
    owner's to settle. Reads only."""
    if not present(conn):
        return Unresolved({}, {}, {})
    known = read(conn)
    none, several, branch = collections.Counter(), collections.Counter(), {}
    for table in TABLES:
        for name, _held, count in _pairs(conn, table):
            why = known.why_not(name)
            if why == "none":
                none[name] += count
            elif why == "several":
                several[name] += count
            elif why == "branch":
                nodes, rows = branch.get(name, (sorted(known.branches[vocabulary.key(name)]), 0))
                branch[name] = (nodes, rows + count)
    return Unresolved(dict(none), dict(several), branch)


def repair(db_path):
    """Make every id what its name gives, in one write under the library's write lock. Returns
    {table: rows changed}. Only the id columns change: no name, photo, tag or file."""
    def write(conn):
        return sync(conn)

    return db.write_with_connection(db_path, write, label="people's ids")
