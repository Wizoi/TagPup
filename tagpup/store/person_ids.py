"""People by the id of their node in the tag tree: the one door a person goes through
(docs/ARCHITECTURE.md, "Identity by id", stage 1, and "People by id, stage 2").

A person is a node of the tree with `has_face` set and no node under it, wherever it sits -- directly under the
tree's root too (owner, 2026-10-10, "every tag has an id"): a branch tag cannot be a person *(owner, 2026-10-04;
docs/findings.md, #660)*, it is a GROUP of people (Family/Thackeray beside Family/Thackeray/Sam, or the root People
beside People/Sam), and a group is never put on a photo as a person (rule b, `resolve`).

**The id is the person.** `faces.tag_id` and `photo_people.tag_id` hold the node a row means, and
everything that groups, counts or joins "this person" reads the id. The name beside it
(`faces.name`, `photo_people.name`) is a CACHE of the node's leaf, written by this module's callers in
the same statement as the id and kept by `follow_*` / `sync` when the tree renames the node: identity
never reads it. A name alone is nobody (owner, 2026-10-15): no writer makes a row with a name and no id (the last one in the live
libraries was cleared that day), and `settle` makes one it meets nobody.

**One door.** A person comes in as an id (a page, a journal), a tag path (a keyword, a saved search) or a
bare name (the CLI, the MCP, a page not reloaded since the update), and `resolve` turns it into a
`Person` -- or refuses: `StalePerson` for an id no person has (merged or deleted meanwhile: a 404, never a
new person), `AmbiguousPerson` for a name two people have (naming the candidates), `GroupNotPerson` for a
group. A name no node is called resolves to None: the caller makes the person's node first (`taxonomy.ensure_node`, or
`face_people.made`: the tag first, as TagTuner's New Person does) or refuses.

Nothing links a name to a person: `follow_*`, `sync` and `settle` keep an id that is set (with its node's leaf as the cache); a
row with no id, or the id of a node that is gone, is nobody. A row gets its id from the writer that wrote it (`target`), or from
the keyword's path.

The tree is read inside the writer's transaction, every time, and never kept: a rename committed by
another process between two writes is what the second one reads. Ids are AUTOINCREMENT, never given
again, so an id left behind by a node deleted by an older writer names nothing rather than someone else;
a trigger (migration 29) refuses a delete of a node that a face names, for any connection. The caller commits.
"""
import collections

from tagpup.core import vocabulary
from tagpup.store import db

#: The tables that name a person, and the column beside `name` that holds the node's id.
TABLES = ("faces", "photo_people")
COLUMN = "tag_id"

#: How many ids go in one IN (...).
CHUNK = 500

#: One person: the id of the node, its tag (the path) and its name (the leaf).
Person = collections.namedtuple("Person", "id tag name")

class PersonProblem(Exception):
    """A person could not be resolved. `str()` is the sentence a person reads."""


class StalePerson(PersonProblem):
    """An id that is no person of this library: merged into another, deleted or never there. A web route
    answers 404 and offers a reload; nothing is created."""

    def __init__(self, person_id):
        self.person_id = person_id
        super().__init__("That person is no longer in the tag tree (they may have been merged or removed in "
                         "another window): reload the page.")


class AmbiguousPerson(PersonProblem):
    """A name two people have. `candidates` are their Persons, by tag."""

    def __init__(self, name, candidates):
        self.name = name
        self.candidates = sorted(candidates, key=lambda person: person.tag)
        super().__init__("More than one person is called %s: %s. Choose one of them (a page sends their person_id)."
                         % (name, ", ".join(person.tag for person in self.candidates)))


class PersonInUse(PersonProblem):
    """A tag a person's faces are named by that an edit of the tree would delete or give nowhere (docs/ARCHITECTURE.md, "Tree
    operations, by id"). `faces` is how many, `tags` which. Deleting it needs `force`, which unnames them."""

    def __init__(self, faces, tags):
        self.faces, self.tags = faces, list(tags)
        what = ", ".join(self.tags[:3]) + (" and %d more" % (len(self.tags) - 3) if len(self.tags) > 3 else "")
        super().__init__("%d face(s) are named %s: unname them first, or delete with force (which unnames them and records them "
                         "in History: about %d rows)." % (faces, what, 3 * faces))


class PersonHasNoChildren(PersonProblem):
    """A tag put under a person (rule a): a person that faces or photos carry has no tags under them."""

    def __init__(self, tag, faces, photos):
        self.tag, self.faces, self.photos = tag, faces, photos
        super().__init__("%s is a person (%d face(s), %d photo(s)); a person cannot have tags under them. Choose another group."
                         % (vocabulary.leaf_of(tag), faces, photos))


class GroupNotPerson(PersonProblem):
    """A group tag put forward as a person (rule b): Family/Thackeray holds people, it is not one."""

    def __init__(self, tag):
        self.tag = tag
        super().__init__("%s is a group of people, not a person." % tag)


def wire(ref):
    """A person as it travels in a plan, a job's state or a request: the id of the node (None for a Ref or a Person with none:
    a name alone is nobody). A typed name is passed on as it is, for `target` to resolve. `ref` is a Ref,
    a Person, an id or a name."""
    if isinstance(ref, (vocabulary.Ref, Person)):
        return ref.id
    return ref


class People:
    """The tree's people as names are matched to them: {name's key: the node's id}, or AMBIGUOUS
    when two nodes are called it; the people by id and by tag; and the groups -- has_face nodes with
    nodes under them, which are not people -- by name, {key: [ids]}, by id and by tag. Read once for one
    write, inside its transaction; never kept."""

    AMBIGUOUS = object()

    def __init__(self, nodes, parents=(), every=None, words=()):
        self.by_key, self.branches = {}, {}
        self.nodes = set()   # every person node's id, those two nodes are called alike too
        self.listing = []    # (id, tag, name) of each of them: what Directory tells the pages
        self.by_id, self.by_tag = {}, {}
        self.groups, self.group_by_tag = {}, {}
        #: {id: tag} of a new library's empty People: where people are filed, never a person, by id too (#1470).
        self.filing_roots = {}
        #: {key: tag} of the nodes at the top with nodes under them that are not marked as holding faces (Places): groups too.
        self.words = {vocabulary.key(tag): tag for tag in words}
        #: {id: name} of EVERY node of the tree (not only people): what a row's cached name is held to while its
        #: id is set. Without `every`, only the nodes given.
        self.node_names = {}
        parents = set(parents)
        for node_id, tag, name in sorted(nodes):
            self.node_names[node_id] = name
            if not tag:
                continue
            if node_id not in parents and vocabulary.is_filing_root(tag):
                self.filing_roots[node_id] = tag   # a new library's empty People is where people are filed
                continue
            leaf = vocabulary.key(name)
            if not leaf:
                continue
            person = Person(node_id, tag, name)
            if node_id in parents:
                self.branches.setdefault(leaf, []).append(node_id)   # a branch tag is not a person
                self.groups[node_id] = person
                self.group_by_tag[vocabulary.normalize(tag).lower()] = person
                continue
            self.nodes.add(node_id)
            self.listing.append((node_id, tag, name))
            self.by_id[node_id] = person
            self.by_tag[vocabulary.normalize(tag).lower()] = person
            self.by_key[leaf] = People.AMBIGUOUS if leaf in self.by_key else node_id
        for node_id, name in (every or ()):
            self.node_names.setdefault(node_id, name)

    @classmethod
    def read(cls, conn):
        parents = [parent for (parent,) in conn.execute(
            "SELECT DISTINCT parent_id FROM tag_taxonomy WHERE parent_id IS NOT NULL")]
        every = conn.execute("SELECT id, name, has_face, tag FROM tag_taxonomy").fetchall()
        parented = set(parents)
        return cls([(node_id, tag, name) for node_id, name, has_face, tag in every if has_face == 1], parents,
                   [(node_id, name) for node_id, name, _has_face, _tag in every],
                   [tag for node_id, _name, has_face, tag in every
                    if has_face != 1 and node_id in parented and tag and vocabulary.SEPARATOR not in tag])

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

    def called(self, name):
        """The Persons called `name` (without case), by tag."""
        key = vocabulary.key(name)
        return sorted((person for person in self.by_id.values() if vocabulary.key(person.name) == key),
                      key=lambda person: person.tag)

    def person(self, ref):
        """The Person `ref` names -- an id, a Person (by its id), a tag path or a bare name -- or None for a name or a path
        no person is filed under. Raises StalePerson for an id that is no person (and no group), GroupNotPerson for a
        group or a new library's empty People, by id, path or name, and AmbiguousPerson for a name more than one person has."""
        if isinstance(ref, vocabulary.Ref):
            ref = ref.id if ref.id is not None else ref.name
        if isinstance(ref, Person):
            ref = ref.id
        if isinstance(ref, bool):
            raise TypeError("a person is named by an id, a tag or a name")
        if isinstance(ref, int):
            found = self.by_id.get(ref)
            if found is not None:
                return found
            if ref in self.groups:
                raise GroupNotPerson(self.groups[ref].tag)
            if ref in self.filing_roots:
                raise GroupNotPerson(self.filing_roots[ref])
            raise StalePerson(ref)
        text = "" if ref is None else str(ref).strip()
        if not text:
            return None
        path = vocabulary.normalize(text)
        if vocabulary.SEPARATOR in path:
            found = self.by_tag.get(path.lower())
            if found is not None:
                return found
            if path.lower() in self.group_by_tag:
                raise GroupNotPerson(self.group_by_tag[path.lower()].tag)
            return None
        key = vocabulary.key(text)
        found = self.by_key.get(key)
        if found is None:
            if key in self.branches:
                raise GroupNotPerson(self.groups[self.branches[key][0]].tag)
            if key in self.words:
                raise GroupNotPerson(self.words[key])
            if vocabulary.is_filing_root(text):   # with its node or without: where people are filed, never a person (#1470)
                raise GroupNotPerson(vocabulary.NEW_LIBRARY_FACE_ROOT)
            return None
        if found is People.AMBIGUOUS:
            raise AmbiguousPerson(text, self.called(text))
        return self.by_id[found]

    def settle(self, name, held):
        """(the id, the name) a row naming `name` and holding `held` should hold: a set id stays, with the cache the node's leaf;
        a row with no name, no id or the id of a node that is gone is nobody, (None, None). A name alone is nobody (owner,
        2026-10-15): nothing links a name to a person, only the writer that knew the id gives it one, and a keyword's row is
        resolved by its PATH (vocabulary.extract_refs)."""
        node = None if name is None else self.node_names.get(held)
        return (held, node) if node is not None else (None, None)

    def __eq__(self, other):
        return isinstance(other, People) and self.by_key == other.by_key and self.node_names == other.node_names

    def __hash__(self):
        return hash(tuple(sorted(self.by_key)))


def read(conn):
    """The People of the library on `conn`, as its tree stands in this transaction."""
    return People.read(conn)


def resolve(conn, ref, known=None):
    """THE door for a person: the Person `ref` is (an id, a tag path or a bare name; People.person), as the tree
    stands in this transaction, or None for a name no person is filed under. Raises StalePerson, AmbiguousPerson or
    GroupNotPerson (rule b). `known` is the People when the caller has read them."""
    return (known or read(conn)).person(ref)


def target(conn, ref, known=None):
    """(the id, the name) a row naming `ref` is written with: the Person's, or -- for a name no person is filed under --
    (None, the name trimmed), an unresolved name. Raises as `resolve` does; (None, None) for no person at all."""
    found = resolve(conn, ref, known)
    if found is not None:
        return found.id, found.name
    if isinstance(ref, vocabulary.Ref):
        ref = ref.name
    if ref is None or isinstance(ref, (int, Person)):
        return None, None
    text = str(ref).strip()
    return None, text or None


def matches(face, target):
    """Is the person a face carries, `face` (a Ref), the person `target` (the pair `target()` gives) is? By the node's id."""
    return face is not None and face.id is not None and face.id == target[0]


class Directory:
    """The people of a library as the pages are told of them: each a dict `{"id", "name", "tag"}` (docs/ARCHITECTURE.md, "People
    by id, stage 2", "The wire"). A person is the whole tag path; the name is its leaf, shown and never the identity. Read once
    for one answer, from the tree as it stands now, and never kept: a rename in another process is what the next answer reads.

    A row that names a person by ID (every answer that has the row's `tag_id`) gets `of_id`, exactly the person. A row with a name
    and no id is NOT a person (owner, 2026-10-10): the leaf is never identity, so no row is given a person by its name. `of_name`
    is for a name somebody TYPED or an external tool supplied: the person when exactly one is called it, None when none is or when
    two are (a typed name two people have is asked "which one?" by path)."""

    def __init__(self, nodes):
        self._records, self._by_key, self._by_tag, self._by_id = [], {}, {}, {}
        for node_id, tag, name in sorted(nodes, key=lambda node: (vocabulary.tag_sort_key(node[2]), node[1])):
            record = {"id": node_id, "name": name, "tag": tag}
            self._records.append(record)
            self._by_key.setdefault(vocabulary.key(name), []).append(record)
            self._by_tag[vocabulary.normalize(tag).lower()] = record
            self._by_id[node_id] = record

    @classmethod
    def read(cls, conn):
        return cls(People.read(conn).listing)

    def records(self):
        """Everyone, by name (vocabulary.tag_sort_key), then by tag: a copy of each."""
        return [dict(record) for record in self._records]

    def of_id(self, person_id):
        """The person whose node is `person_id`, or None (a group, a node gone, no id)."""
        found = self._by_id.get(person_id) if person_id is not None else None
        return dict(found) if found else None

    def of_name(self, name):
        """The person called `name` (without case), or None; see the class."""
        found = self._by_key.get(vocabulary.key(name))
        return dict(found[0]) if found and len(found) == 1 else None

    def of_tag(self, tag):
        """The person filed at the tag `tag`, or None."""
        found = self._by_tag.get(vocabulary.normalize(tag).lower()) if tag else None
        return dict(found) if found else None

    def of_reference(self, value):
        """The person a suggester's `name` is: a TAG PATH when the suggestion came from a face match
        (`Family/Immediate/Clara Ingersoll`: the suggester's item['tag']), else a bare name. A path is looked up
        by `of_tag` and so is exact even when the leaf is shared; a name by `of_name`, after `of_tag`: a person at the top
        of the tree is suggested by their own tag, which has no separator (#1408)."""
        if value and vocabulary.SEPARATOR in vocabulary.normalize(value):
            return self.of_tag(value)
        return self.of_tag(value) or self.of_name(value)

    def annotate(self, items, into="person"):
        """Give each dict of `items` the `person` its `person_id` is (None when it holds none, or a node that is no person):
        the fields a page labels the person by. A name alone gives nobody. Returns `items`."""
        for item in items:
            item[into] = self.of_id(item.get("person_id"))
        return items


def _chunks(items):
    items = sorted(set(items))
    for start in range(0, len(items), CHUNK):
        yield items[start:start + CHUNK]


def _marks(chunk):
    return ",".join("?" * len(chunk))


#: A face People.settle makes nobody is UNNAMED: no person, no name and no decider, so it is not "decided nobody" (owner,
#: 2026-10-15). A listed person it makes nobody is not listed: the row goes (photo_people.name is NOT NULL).
FACE_UNNAMED = "tag_id = NULL, name = NULL, name_source = NULL"


def follow_faces(conn, photo_ids, known=None):
    """Settle each face of the photos `photo_ids` (People.settle): an id that is set stays with its name the node's
    leaf; a face named with no id is unnamed (FACE_UNNAMED), an id on a face with no name comes off. Returns faces changed.
    idx_faces_photo_id finds them; the embedding is not read."""
    if not photo_ids:
        return 0
    known = known or read(conn)
    kept, unnamed, stray = [], [], []
    for chunk in _chunks(photo_ids):
        for face_id, name, held in conn.execute(
                "SELECT id, name, tag_id FROM faces WHERE photo_id IN (%s)" % _marks(chunk), chunk):
            wanted, cached = known.settle(name, held)
            if held != wanted or name != cached:
                if wanted is not None:
                    kept.append((wanted, cached, face_id))
                else:
                    (unnamed if name is not None else stray).append((face_id,))
    conn.executemany("UPDATE faces SET tag_id = ?, name = ? WHERE id = ?", kept)
    conn.executemany("UPDATE faces SET " + FACE_UNNAMED + " WHERE id = ?", unnamed)
    conn.executemany("UPDATE faces SET tag_id = NULL WHERE id = ?", stray)   # a decided nobody keeps its decider
    return len(kept) + len(unnamed) + len(stray)


def follow_listed(conn, photo_ids, known=None):
    """Settle each person listed for the photos `photo_ids` (photo_people), as follow_faces does; one who is nobody is not listed
    (FACE_UNNAMED). Every photo, without `photo_ids`. Returns rows changed."""
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
            wanted, cached = known.settle(name, held)
            if held != wanted or name != cached:
                updates.append((wanted, cached, photo_id, position))
    conn.executemany("UPDATE photo_people SET tag_id = ?, name = ? WHERE photo_id = ? AND position = ?",
                     [update for update in updates if update[0] is not None])
    conn.executemany("DELETE FROM photo_people WHERE photo_id = ? AND position = ?",
                     [(photo_id, position) for wanted, _cached, photo_id, position in updates if wanted is None])
    return len(updates)


def _pairs(conn, table):
    """[(name, id held, rows)] of `table`: one per distinct pair, which idx_faces_person answers for
    faces from the index alone."""
    return conn.execute("SELECT name, tag_id, COUNT(*) FROM %s WHERE name IS NOT NULL GROUP BY name, tag_id"
                        % table).fetchall()


def _sync_table(conn, table, known):
    changed = 0
    for name, held, _rows in _pairs(conn, table):
        wanted, cached = known.settle(name, held)
        if held != wanted or name != cached:
            if wanted is not None:
                sql, params = "UPDATE %s SET tag_id = ?, name = ? WHERE name = ? AND tag_id IS ?" % table, (wanted, cached, name, held)
            elif table == "faces":
                sql, params = "UPDATE faces SET " + FACE_UNNAMED + " WHERE name = ? AND tag_id IS ?", (name, held)
            else:
                sql, params = "DELETE FROM photo_people WHERE name = ? AND tag_id IS ?", (name, held)
            changed += conn.execute(sql, params).rowcount
    return changed


def sync(conn, known=None):
    """Settle every face and every listed person (People.settle): the migration's backfill, the repair, and what
    follows an edit that changed who the tree's people are. One lookup per distinct pair of name and id, never per
    row: the pairs are read (from the index, for faces), and only a pair that is wrong is written. Returns {table:
    rows changed}."""
    known = known or read(conn)
    changed = {table: _sync_table(conn, table, known) for table in TABLES}
    # A face whose name was taken off by a writer that did not follow it.
    changed["faces"] += conn.execute("UPDATE faces SET tag_id = NULL WHERE name IS NULL AND tag_id IS NOT NULL").rowcount
    return changed


def follow_tree(conn, before):
    """After an edit of the tree (`before` is the People read before it): a node renamed gives its name to the rows
    that hold its id (UPDATE ... WHERE tag_id = ?, by idx_faces_tag); a node gone takes its listed people away. No face names a
    node gone: the tree edit unnamed or moved them first, and the trigger person_tag_not_deleted_while_named refuses the delete
    otherwise. Nothing links a name to a person: a row has the id or is not a person (owner, 2026-10-11). An edit that renames or
    removes nothing costs two reads of the tree. Returns rows changed."""
    after = read(conn)
    if after == before:
        return 0
    changed = 0
    for node_id, name in after.node_names.items():
        if node_id in before.node_names and before.node_names[node_id] != name:
            for table in TABLES:
                changed += conn.execute("UPDATE %s SET name = ? WHERE tag_id = ? AND name IS NOT ?" % table,
                                        (name, node_id, name)).rowcount
    for node_id in set(before.node_names) - set(after.node_names):
        changed += conn.execute("DELETE FROM photo_people WHERE tag_id = ?", (node_id,)).rowcount   # not a person without a node
    return changed


def faces_using(conn, ids):
    """({id: faces naming it}, for each of `ids` that some face names) -- idx_faces_tag, one query a chunk."""
    found = {}
    for chunk in _chunks(ids):
        found.update((person_id, count) for person_id, count in conn.execute(
            "SELECT tag_id, COUNT(*) FROM faces WHERE tag_id IN (%s) GROUP BY tag_id" % _marks(chunk), chunk))
    return found


def given(conn, ids):
    """Which of the person ids `ids` some face carries now, on `conn`: one indexed query for them all (idx_faces_tag). An
    id read before a write began may have been unnamed everywhere before it is written."""
    found = set()
    for chunk in _chunks(ids):
        found.update(person_id for (person_id,) in conn.execute(
            "SELECT DISTINCT tag_id FROM faces WHERE tag_id IN (%s)" % _marks(chunk), chunk))
    return found


# ---- What the doctor reads -------------------------------------------------------------------

Disagreement = collections.namedtuple("Disagreement", "rows examples")


def out_of_step(conn, table, examples=5, spelling=True):
    """(rows of `table` that People.settle would change -- a name that is not its node's leaf, an id of a node gone, a name with no
    id -- and a few of their ids: face ids, or photo ids for photo_people). Reads only. Without `spelling`, the cached name's spelling is let be: only an id that is missing, or of a node gone (migration
    21's own post-condition, which changes no name)."""
    known = read(conn)
    rows, found = 0, []
    key = "id" if table == "faces" else "photo_id"
    for name, held, count in _pairs(conn, table):
        wanted, cached = known.settle(name, held)
        if held != wanted or (spelling and name != cached):
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


def repair(db_path):
    """Settle every id and cached name, in one write under the library's write lock: a row of a node that is gone, or with a
    name and no id, is nobody (FACE_UNNAMED); a row gets its node's name; an id comes off a face with no name. A name is NOT linked (People.settle). Returns {table: rows changed}. Only the id and name columns of faces
    and photo_people change: no photo, tag or file."""
    def write(conn):
        return sync(conn)

    return db.write_with_connection(db_path, write, label="people's ids")
