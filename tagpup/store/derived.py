"""The tables the library views stand on, derived from the photos (docs/ARCHITECTURE.md, phase 9a).

Keywords are JSON in `photos.tags`, a folder is the front of `photos.path`, and a photo's rating,
camera and place are inside `photos.raw_metadata`: "everything under Trips/" and "the photos of this
folder" each read every row. Three tables hold them as rows a query can seek,
made from the photos, never written by anything else, never journaled (journal.DERIVED):

* `photo_tags(photo_id, tag_id)`: a photo's keywords, each as the id of the tag-tree node it names
  (identity by id; docs/ARCHITECTURE.md, "Identity by id"). A keyword is matched to a node as the
  tree spells it, else without case, trimmed and with "|" and "\\" read as "/"
  (`vocabulary.normalize`); a bare leaf ("Cora Ingersoll") is not "People/Cora Ingersoll", which
  is the people rule's to resolve. A keyword the index READS with no node gets one when it is first seen
  (`make_nodes`, owner 2026-10-10: every tag has an id), so its photo has the row; a bare keyword that is a person's
  leaf is that person's, and gets none. A keyword written some other way, or read before, and with no node has no row.
  "A keyword and everything under it" is a range on `tag_taxonomy.tag`, the node's ids joined to
  this table (`photos_under_tag`).
* `folders(id, parent_id, path, name)` and `photo_folder(photo_id, folder_id)`: the folder tree of
  the library's photos and the folder each is directly in. A folder has a row for every ancestor of
  a photo's folder up to the top of its spelling -- the root (`@pictures`) of a rooted row, the
  drive or the share of a native one -- so the navigator can draw the tree; a folder with no photo
  at or below it has none (it goes with its last photo). `path` is a path column like any other: the
  folder in ROW form (docs/ARCHITECTURE.md, "Roots and machines"), `@pictures/2024/Coast` in a
  library that holds a root and the machine's native path in one that holds none, compared without
  case where the filesystem is. `from_row` makes it native on the way out (`folder_tree`).
  A folder's id is kept while the folder is, and is not kept across the roots' adoption (the paths
  change spelling): a page names a folder by its path.
A fourth, `photo_meta` (rating, camera, size and place from the raw metadata), was written here until migration 30 dropped
it: nothing read it. The camera a photo names is read from its metadata when it is shown or searched
(tagpup.core.photo_meta.gear; the search's cache is tagpup.store.search_index.cameras_with).

Written by `rebuild_all` (migration 19, the doctor's repair) and by the writers of the photos'
keywords, path, metadata and captions in the SAME transaction as the write (`refresh_photos`, `record`), and
by the tag tree's edits for the photos whose keywords a changed node names (`follow_tree`,
`follow_nodes`). A delete of a photo, or of a node, takes its rows by a trigger whichever
connection deletes it, as photo_people does; only the folders a delete emptied are left, and `prune`
takes them. tests/test_derived_writers.py fails the build on a writer of a photo's tags, path or
metadata that does not go through here.

The word index a search's words are matched in (tagpup.store.search_index, migration 24) is kept from here too: every
photo refreshed here has its rows of it made again, whatever of its rows changed (a caption is in no table of these three).

The caller commits. Every function does nothing, and says 0, on a library that has not had
migration 19 yet: the migrations before it write photos too.
"""
import collections
import json
import re
import unicodedata

from tagpup.core import paths, vocabulary
from tagpup.store import db, search_index
from tagpup.store import roots as store_roots

TABLES = ("photo_tags", "folders", "photo_folder")

#: How many photos one read or write handles at a time.
CHUNK = 500


def present(conn):
    """Has the library on `conn` the three tables (migration 19)? Remembered once true."""
    if getattr(conn, "derived_ready", False):
        return True
    found = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name IN (%s)"
                         % ",".join("?" * len(TABLES)), TABLES).fetchone()[0]
    if found != len(TABLES):
        return False
    try:
        conn.derived_ready = True
    except AttributeError:
        pass   # a connection that is not db.connect's cannot remember
    return True


# ---- Keywords and the tree ----------------------------------------------------------------

#: What makes a keyword's lower case differ from its key: separators other than "/", blanks at
#: the ends of a segment, an empty segment.
_ODD = re.compile(r"[|\\]|^\s|\s$|\s/|/\s|//|^/|/$")


def keyword(text):
    """The form a keyword and a node are compared in: `vocabulary.normalize`d, in one Unicode form (an accent typed as one
    character or as two is one keyword), without case."""
    return unicodedata.normalize("NFC", vocabulary.key(vocabulary.normalize(text)))


def _fast_key(text):
    if _ODD.search(text):
        return keyword(text)
    return text.lower() if text.isascii() else unicodedata.normalize("NFC", text.lower())


class Tree:
    """The tag tree as a photo's keywords are matched to it: a keyword is the node whose tag it
    is, else the node it is without case and by `keyword`'s spelling -- the lowest id when two
    nodes differ only in case. Read once for a run of writes (Batch), never kept past it: a tree
    edited meanwhile is read again by the next."""

    def __init__(self, nodes):
        self.exact, self.folded, self.tags = {}, {}, {}
        for node_id, tag in sorted(nodes):
            self.add(node_id, tag)

    def add(self, node_id, tag):
        """A node the tree has now (one made after it was read)."""
        if not tag:
            return
        self.exact.setdefault(tag, node_id)
        self.folded.setdefault(keyword(tag), node_id)
        self.tags[node_id] = tag

    @classmethod
    def read(cls, conn):
        return cls(conn.execute("SELECT id, tag FROM tag_taxonomy").fetchall())

    def find(self, text):
        """The id of the node `text` names, or None."""
        found = self.exact.get(text)
        return found if found is not None else self.folded.get(_fast_key(text))

    def ids(self, keywords):
        """The node ids a photo's keywords name."""
        return frozenset(found for found in map(self.find, keywords) if found is not None)


def keywords_of(tags_json):
    """The keywords a row's `tags` holds: its texts. [] for no tags, JSON that is not a list, and
    an item that is not text -- a row some writer damaged says nothing, as everything that reads
    the column treats it."""
    if not tags_json:
        return []
    try:
        found = json.loads(tags_json)
    except (TypeError, ValueError):
        return []
    return [tag for tag in found if isinstance(tag, str)] if isinstance(found, list) else []


# ---- One photo's rows ------------------------------------------------------------------------

def _state(path, keywords, tree):
    """(node ids, folder or None) of a photo: the things its rows say."""
    return tree.ids(keywords), paths.row_parent(path)


def _fold(folder):
    """A folder as the unique index compares it: without case where the filesystem is."""
    return store_roots.path_order(folder)


class Batch:
    """What a run of writes shares: the tree, read once, and the id of each folder found or made.
    Made by the caller for a run (a batch of the index's) and handed to each write of it, or made
    by the write for itself. Used inside one transaction: a rollback ends it."""

    def __init__(self, conn):
        self.conn = conn
        self._tree = None
        self.folders = {}
        #: The batch's keywords were all given to make_nodes at once (so a read does not make its own); and how many nodes
        #: that left unmade for being too many.
        self.prepared, self.flagged = False, 0
        #: {person: the unit vectors of their decided faces, or None}: what a run of writes naming faces
        #: from their photos' tags has read of each person, once (tagpup.store.face_tags, #841).
        self.decided = {}

    @property
    def tree(self):
        """The tree, read at first use: inside the transaction of the first write that needs it,
        so a tree another process edited before that write is the one used."""
        if self._tree is None:
            self._tree = Tree.read(self.conn)
        return self._tree


def _folder_id(conn, folder, cache):
    """The id of `folder`'s row, made -- with every folder above it -- if it has none."""
    key = _fold(folder)
    found = cache.get(key)
    if found is not None:
        return found
    row = conn.execute("SELECT id FROM folders WHERE path = ?", (folder,)).fetchone()
    if row is None:
        parent = paths.row_parent(folder)
        parent_id = _folder_id(conn, parent, cache) if parent is not None else None
        row = (conn.execute("INSERT INTO folders (parent_id, path, name) VALUES (?, ?, ?)",
                            (parent_id, folder, paths.row_name(folder))).lastrowid,)
    cache[key] = row[0]
    return row[0]


def _marks(items):
    return ",".join("?" * len(items))


def _put(conn, states, batch, gone=()):
    """Make the rows of each photo in `states` ({photo id: _state}) what its state says, writing
    only what differs, and take the rows of each id in `gone` (photos with none). Returns how many
    photos' rows changed. Folders a photo left are pruned."""
    ids = list(states)
    held_tags, held_folder = collections.defaultdict(set), {}
    if ids:
        for photo_id, tag_id in conn.execute(
                "SELECT photo_id, tag_id FROM photo_tags WHERE photo_id IN (%s)" % _marks(ids), ids):
            held_tags[photo_id].add(tag_id)
        held_folder = dict(conn.execute(
            "SELECT photo_id, folder_id FROM photo_folder WHERE photo_id IN (%s)" % _marks(ids), ids))
    drop, add, left, changed = [], [], set(), set()
    for photo_id, (tag_ids, folder) in states.items():
        have = held_tags.get(photo_id, set())
        drop += [(photo_id, tag_id) for tag_id in have - tag_ids]
        add += [(photo_id, tag_id) for tag_id in tag_ids - have]
        if have != tag_ids:
            changed.add(photo_id)
        folder_id = _folder_id(conn, folder, batch.folders) if folder is not None else None
        if folder_id != held_folder.get(photo_id):
            changed.add(photo_id)
            if photo_id in held_folder:
                left.add(held_folder[photo_id])
            if folder_id is None:
                conn.execute("DELETE FROM photo_folder WHERE photo_id = ?", (photo_id,))
            else:
                conn.execute("INSERT OR REPLACE INTO photo_folder (photo_id, folder_id) VALUES (?, ?)",
                             (photo_id, folder_id))
    conn.executemany("DELETE FROM photo_tags WHERE photo_id = ? AND tag_id = ?", drop)
    conn.executemany("INSERT INTO photo_tags (photo_id, tag_id) VALUES (?, ?)", add)
    gone = list(gone)
    for start in range(0, len(gone), CHUNK):
        chunk = gone[start:start + CHUNK]
        for table in ("photo_tags", "photo_folder"):
            conn.execute("DELETE FROM %s WHERE photo_id IN (%s)" % (table, _marks(chunk)), chunk)
    pruned = prune(conn) if gone else prune(conn, left) if left else 0
    if pruned:
        batch.folders.clear()   # an id found before may be one pruned now
    # The word index, for every photo asked about: its captions and people are in no table of these, so what changed
    # here says nothing of whether its words did.
    search_index.refresh(conn, list(states) + list(gone))
    return len(changed) + len(gone)


def refresh_photos(conn, photo_ids, batch=None):
    """Make the rows of the photos `photo_ids` what their keywords and path say now:
    called by every write of either, in its transaction. An id with no photo has its rows
    taken. Returns how many photos' rows changed."""
    if not present(conn):
        return 0
    ids = sorted({int(photo_id) for photo_id in photo_ids})
    if not ids:
        return 0
    batch = batch or Batch(conn)
    changed = 0
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start:start + CHUNK]
        states = {}
        for photo_id, path, tags_json in conn.execute(
                "SELECT id, path, tags FROM photos WHERE id IN (%s)" % _marks(chunk), chunk):
            states[photo_id] = _state(path, keywords_of(tags_json), batch.tree)
        changed += _put(conn, states, batch, [photo_id for photo_id in chunk if photo_id not in states])
    return changed


#: The most nodes one batch of reads may make from the keywords it meets. A batch that would make more makes NONE and counts them
#: (`Batch.flagged`): a folder from another program with thousands of distinct keywords is the owner's to look at, since each
#: node is a tag to merge or delete by hand and Suggest embeds every one of them. `tree-fill` makes them when the owner asks.
MAX_NEW_NODES_PER_BATCH = 500


def make_nodes(conn, tag_lists, batch=None):
    """Give each keyword of a batch of reads (a list of keyword lists; one list for one photo) that has no node its node
    (taxonomy.ensure_node: the keyword's own path, every missing level of it), so that every keyword a file holds has an id.
    All of the batch's nodes are made in ONE tree_edit, which scans the photos' keywords once for the batch and not once for each
    node; and a batch that would make more than MAX_NEW_NODES_PER_BATCH makes none. Run before the photos' people are rebuilt: a
    keyword under a face root makes a person, linked in the same transaction. A keyword that would go under a person faces or
    photos use is left without a node. The caller commits. Returns how many nodes were made, the levels above a keyword too."""
    if not present(conn):
        return 0
    from tagpup.store import people, taxonomy   # taxonomy imports this module
    batch = batch or Batch(conn)
    tree, wanted, levels = batch.tree, {}, set()
    for tags in tag_lists:
        for text in tags if isinstance(tags, (list, tuple)) else ():
            if not isinstance(text, str) or tree.find(text) is not None or keyword(text) in wanted:
                continue
            path = vocabulary.normalize(text)
            if not path:
                continue
            wanted[keyword(text)] = text
            for level in vocabulary.lineage(path):
                if tree.find(level) is None:
                    levels.add(keyword(level))
    if not wanted:
        return 0
    if len(levels) > MAX_NEW_NODES_PER_BATCH:
        batch.flagged += len(levels)
        return 0
    before = len(tree.tags)
    with people.tree_edit(conn):
        for text in wanted.values():
            taxonomy.ensure_node(conn, text, tree=tree, edited=True)
    return len(tree.tags) - before


def record(conn, photo_id, path, tags, batch=None):
    """refresh_photos for one photo whose row the caller has just written and holds: `path` as
    stored (the row form) and `tags` the list written -- what the index records of a read, which
    has no need to read its own row back. Returns 0 or 1."""
    if not present(conn):
        return 0
    batch = batch or Batch(conn)
    # What record_indexed wrote is what keywords_of reads back: tags that are None or not a list say nothing.
    keywords = [tag for tag in tags if isinstance(tag, str)] if isinstance(tags, (list, tuple)) else []
    return _put(conn, {photo_id: _state(path, keywords, batch.tree)}, batch)


# ---- Folders no photo is in ------------------------------------------------------------------

def prune(conn, candidates=None):
    """Take the folders with no photo at or below them: those in `candidates` (ids) and the
    ancestors they leave empty, or, without, every one. A delete of photos leaves them, since a
    trigger cannot walk up a tree. Returns how many were taken."""
    if not present(conn):
        return 0
    taken = 0
    if candidates is None:
        while True:
            more = conn.execute(
                "DELETE FROM folders WHERE NOT EXISTS (SELECT 1 FROM photo_folder pf WHERE pf.folder_id = folders.id)"
                " AND NOT EXISTS (SELECT 1 FROM folders child WHERE child.parent_id = folders.id)").rowcount
            if not more:
                return taken
            taken += more
    for folder_id in candidates:
        while folder_id is not None:
            row = conn.execute("SELECT parent_id FROM folders WHERE id = ?", (folder_id,)).fetchone()
            if row is None or conn.execute(
                    "SELECT 1 FROM photo_folder WHERE folder_id = ? LIMIT 1", (folder_id,)).fetchone() or conn.execute(
                    "SELECT 1 FROM folders WHERE parent_id = ? LIMIT 1", (folder_id,)).fetchone():
                break
            conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
            taken += 1
            folder_id = row[0]
    return taken


# ---- The tag tree's edits --------------------------------------------------------------------

def tree_before(conn):
    """{node id: tag} of the tree now, to hand to follow_tree once an edit of it is done; None for
    a library that has no derived tables yet."""
    return dict(conn.execute("SELECT id, tag FROM tag_taxonomy")) if present(conn) else None


def follow_tree(conn, before):
    """Refresh the photos a tree edit changed the keywords' nodes of: the nodes added, taken away
    or moved from `before` (tree_before). Returns how many photos' rows changed."""
    if before is None or not present(conn):
        return 0
    after = dict(conn.execute("SELECT id, tag FROM tag_taxonomy"))
    changed = {tag for node_id, tag in before.items() if after.get(node_id) != tag}
    changed |= {tag for node_id, tag in after.items() if before.get(node_id) != tag}
    return follow_nodes(conn, changed)


def follow_nodes(conn, tags):
    """Refresh the photos carrying a keyword that is, or is spelled as, one of the node `tags`: a
    node made gives a keyword that had none its row, a node taken away or moved takes its rows (a
    trigger does that for a delete) and may leave a keyword another node now names. The photos
    are found by reading every photo's keywords once, as people.follow_tree does, since a keyword
    with no node has no row to find them by. Returns how many photos' rows changed."""
    keys = {keyword(tag) for tag in tags if tag}
    if not keys or not present(conn):
        return 0
    affected = []
    for photo_id, tags_json in conn.execute("SELECT id, tags FROM photos WHERE tags IS NOT NULL AND tags != '[]'"):
        if any(_fast_key(tag) in keys for tag in keywords_of(tags_json)):
            affected.append(photo_id)
    return refresh_photos(conn, affected)


# ---- Whole ---------------------------------------------------------------------------------

def _wanted(placed):
    """{folder key: (folder spelling, its parent's key)} of every folder in `placed` -- an iterable
    of folders -- and every one above them."""
    wanted = {}
    for folder in placed:
        while folder is not None:
            key = _fold(folder)
            if key in wanted:
                break
            parent = paths.row_parent(folder)
            wanted[key] = (folder, None if parent is None else _fold(parent))
            folder = parent
    return wanted


def _depth(key, wanted):
    depth = 0
    while wanted[key][1] is not None:
        key, depth = wanted[key][1], depth + 1
    return depth


def _write_folders(conn, placed, wanted):
    """Make `folders` and `photo_folder` what `placed` ({photo id: its folder, or None}) and
    `wanted` (_wanted) say. The folders that remain keep their ids. Returns (folders, photos)."""
    existing = {}
    for folder_id, parent_id, path, name in conn.execute("SELECT id, parent_id, path, name FROM folders"):
        existing[_fold(path)] = (folder_id, parent_id, path, name)
    conn.execute("DELETE FROM photo_folder")
    gone = [found[0] for key, found in existing.items() if key not in wanted]
    for start in range(0, len(gone), CHUNK):
        chunk = gone[start:start + CHUNK]
        conn.execute("DELETE FROM folders WHERE id IN (%s)" % _marks(chunk), chunk)
    ids = {}
    for key in sorted(wanted, key=lambda k: (_depth(k, wanted), k)):
        spelling, parent_key = wanted[key]
        parent_id = ids[parent_key] if parent_key is not None else None
        found = existing.get(key)
        if found is None:
            ids[key] = conn.execute("INSERT INTO folders (parent_id, path, name) VALUES (?, ?, ?)",
                                    (parent_id, spelling, paths.row_name(spelling))).lastrowid
            continue
        ids[key] = found[0]
        if found[1] != parent_id or found[3] != paths.row_name(found[2]):
            conn.execute("UPDATE folders SET parent_id = ?, name = ? WHERE id = ?",
                         (parent_id, paths.row_name(found[2]), found[0]))
    conn.executemany("INSERT INTO photo_folder (photo_id, folder_id) VALUES (?, ?)",
                     [(photo_id, ids[_fold(folder)]) for photo_id, folder in placed.items() if folder is not None])
    return len(wanted), sum(1 for folder in placed.values() if folder is not None)


def rebuild_folders(conn):
    """Make the folder tree what the photos' paths say, whole: for the roots' adoption and its
    undo, which change the spelling of every path in one transaction. Returns (folders, photos in
    one)."""
    if not present(conn):
        return 0, 0
    placed = {photo_id: paths.row_parent(path) for photo_id, path in conn.execute("SELECT id, path FROM photos")}
    written = _write_folders(conn, placed, _wanted(set(folder for folder in placed.values() if folder is not None)))
    # Every path's spelling changed, so every photo's folders as the word index holds them (below the root, or the drive).
    search_index.rebuild(conn)
    return written


def rebuild_all(conn):
    """Make all three tables what the photos say, whole, in the caller's transaction: the
    migration's and the repair's. Reads each photo once. Returns {photos, tag_rows, folders,
    in_a_folder, word_rows}."""
    tree = Tree.read(conn)
    placed, tag_rows = {}, []
    last = -1
    while True:
        chunk = conn.execute("SELECT id, path, tags FROM photos WHERE id > ? ORDER BY id LIMIT ?",
                             (last, 2000)).fetchall()
        if not chunk:
            break
        last = chunk[-1][0]
        for photo_id, path, tags_json in chunk:
            tag_ids, folder = _state(path, keywords_of(tags_json), tree)
            placed[photo_id] = folder
            tag_rows += [(photo_id, tag_id) for tag_id in tag_ids]
    conn.execute("DELETE FROM photo_tags")
    conn.executemany("INSERT INTO photo_tags (photo_id, tag_id) VALUES (?, ?)", tag_rows)
    folders, in_a_folder = _write_folders(conn, placed, _wanted(set(f for f in placed.values() if f is not None)))
    words = search_index.rebuild(conn)
    return {"photos": len(placed), "tag_rows": len(tag_rows), "folders": folders, "in_a_folder": in_a_folder,
            "word_rows": words}


def listing(conn, photo_ids, node_ids=()):
    """The rows of the three tables for the photos `photo_ids`, and the (photo, node) rows of the
    nodes `node_ids`, as lists to compare: ([(photo, node)], [(photo, folder path)]).
    The folder by its path, not its id: a folder pruned and made again is the same folder. What
    the journal's rehearsal compares before a change and after its undo. Reads only."""
    if not present(conn):
        return [], []
    tags, folders = [], []
    ids = sorted(set(photo_ids))
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start:start + CHUNK]
        tags += conn.execute("SELECT photo_id, tag_id FROM photo_tags WHERE photo_id IN (%s)" % _marks(chunk), chunk)
        folders += conn.execute("SELECT pf.photo_id, f.path FROM photo_folder pf JOIN folders f ON f.id = pf.folder_id"
                                " WHERE pf.photo_id IN (%s)" % _marks(chunk), chunk)
    nodes = sorted(set(node_ids))
    for start in range(0, len(nodes), CHUNK):
        chunk = nodes[start:start + CHUNK]
        tags += conn.execute("SELECT photo_id, tag_id FROM photo_tags WHERE tag_id IN (%s)" % _marks(chunk), chunk)
    return sorted(set(map(tuple, tags))), sorted(map(tuple, folders))


# ---- What a rule would say: the rows against the photos --------------------------------------

def stale_tags(conn):
    """The ids of the photos whose rows in `photo_tags` are not what their keywords and the tree
    give -- a writer that changed one and did not refresh -- and of the ids the table holds rows
    for that are no photo. Reads only."""
    tree = Tree.read(conn)
    held = collections.defaultdict(set)
    for photo_id, tag_id in conn.execute("SELECT photo_id, tag_id FROM photo_tags"):
        held[photo_id].add(tag_id)
    stale, seen = [], set()
    for photo_id, tags_json in conn.execute("SELECT id, tags FROM photos ORDER BY id"):
        seen.add(photo_id)
        if tree.ids(keywords_of(tags_json)) != held.get(photo_id, set()):
            stale.append(photo_id)
    return stale + sorted(set(held) - seen)


Stale = collections.namedtuple("Stale", "photos folders missing strays")


def stale_folders(conn):
    """What is not as the photos' paths give, as Stale: the ids of the photos not in the folder their
    path names (or in one though it names none); the ids of the folders whose parent is not the folder
    above them; how many folders a photo needs that have no row; and the ids of the `strays`, folders
    with no photo at or below them, which a delete that did not come through the store leaves (the
    store's own prune takes them) -- harmless to a count, and so told apart from the rest. Reads only."""
    folders = {}
    by_key = {}
    for folder_id, parent_id, path in conn.execute("SELECT id, parent_id, path FROM folders"):
        folders[folder_id] = (parent_id, path)
        by_key[_fold(path)] = folder_id
    path_of = {folder_id: path for folder_id, (_parent, path) in folders.items()}
    held = {photo_id: path_of.get(folder_id) for photo_id, folder_id in conn.execute(
        "SELECT photo_id, folder_id FROM photo_folder")}
    wrong_photos, expected = [], {}
    seen = set()
    for photo_id, path in conn.execute("SELECT id, path FROM photos ORDER BY id"):
        seen.add(photo_id)
        folder = paths.row_parent(path)
        expected[photo_id] = folder
        have = held.get(photo_id)
        if (folder is None) != (have is None) or (folder is not None and _fold(folder) != _fold(have)):
            wrong_photos.append(photo_id)
    wrong_photos += sorted(set(held) - seen)
    wanted = _wanted(folder for folder in expected.values() if folder is not None)
    wrong_folders, strays = [], []
    for folder_id, (parent_id, path) in folders.items():
        key = _fold(path)
        above = paths.row_parent(path)
        if key not in wanted:
            strays.append(folder_id)
        elif by_key.get(_fold(above) if above is not None else None) != parent_id:
            wrong_folders.append(folder_id)
    return Stale(wrong_photos, sorted(wrong_folders), len(set(wanted) - set(by_key)), sorted(strays))


def problems(conn):
    """What is wrong with the three tables, as sentences of counts, [] when nothing: what the
    migration checks before it commits. Reads only."""
    found = []
    tags = stale_tags(conn)
    photos, folders, missing, strays = stale_folders(conn)
    if tags:
        found.append("%d photo(s) have keyword rows that are not what their tags and the tree give" % len(tags))
    if photos or folders or missing or strays:
        found.append("the folders are not what the photos' paths give (%d photo(s), %d folder(s), %d missing, "
                     "%d holding no photo)" % (len(photos), len(folders), missing, len(strays)))
    return found + search_index.problems(conn)


def repair(db_path):
    """Make the three tables, and the word index, what the photos say, in one write under the library's write lock, and
    verify it. Returns (what was wrong before, as problems() says it, what rebuild_all wrote,
    what is wrong after: [] when it worked). Derived rows only: no photo, tag or file is touched,
    so no backup is taken."""
    def work(conn):
        before = problems(conn)
        written = rebuild_all(conn)
        return before, written, problems(conn)
    return db.write_with_connection(db_path, work, label="rebuild the derived tables")


Unnamed = collections.namedtuple("Unnamed", "by_tag photos")


def unnamed_keywords(conn):
    """The keywords that name no node of the tree, so have no row: (a Counter of keyword, as
    photos spell it, to the photos carrying it -- one photo counts once for a keyword it holds twice
    -- and how many photos carry any). The tree is the owner's and indexing a photo never adds to it;
    a keyword whose node was deleted while the files keep it is here until a node is made. Reads
    only."""
    tree = Tree.read(conn)
    found, photos = collections.Counter(), 0
    for (tags_json,) in conn.execute("SELECT tags FROM photos WHERE tags IS NOT NULL AND tags != '[]'"):
        unnamed = [tag for tag in set(keywords_of(tags_json)) if tree.find(tag) is None]
        found.update(unnamed)
        photos += 1 if unnamed else 0
    return Unnamed(found, photos)


def tags_without_a_node(conn):
    """{keyword: photos carrying it} of each keyword that names no node (unnamed_keywords)."""
    return unnamed_keywords(conn).by_tag


# ---- What the views ask ----------------------------------------------------------------------

#: The ids of a tag's node and of every node under it, for the tag `?` -- as the tree spells it --
#: and the range its children's tags make: "People/Rowan/" up to "People/Rowan0", the character
#: after "/", so "People/Rowanne" is not in it. Two seeks of tag_taxonomy's unique index; never LIKE,
#: which reads "_" and "%" as wildcards and ignores case. (tag, tag + "/", tag + "0")
UNDER = ("SELECT id FROM tag_taxonomy WHERE tag = ? UNION ALL"
         " SELECT id FROM tag_taxonomy WHERE tag >= ? AND tag < ?")


def under(tag):
    """(SQL, parameters) of the ids of the node `tag` and of every node under it."""
    prefix = tag + vocabulary.SEPARATOR
    return UNDER, (tag, prefix, tag + chr(ord(vocabulary.SEPARATOR) + 1))


def photos_under_tag(conn, tag, limit=None, offset=0):
    """The ids of the photos carrying `tag` or a tag under it, each once, by id: a page of them
    with `limit`. `tag` is as the tree spells it; a photo whose keyword spells it otherwise (case,
    blanks) is found by its node. One seek of photo_tags for each node."""
    sql, params = under(tag)
    query = ("SELECT pt.photo_id FROM photo_tags pt WHERE pt.tag_id IN (%s) GROUP BY pt.photo_id"
             " ORDER BY pt.photo_id" % sql)
    if limit is not None:
        query, params = query + " LIMIT ? OFFSET ?", params + (limit, offset)
    return [photo_id for (photo_id,) in conn.execute(query, params)]


def count_under_tag(conn, tag):
    """How many photos carry `tag` or a tag under it."""
    sql, params = under(tag)
    return conn.execute("SELECT COUNT(DISTINCT pt.photo_id) FROM photo_tags pt WHERE pt.tag_id IN (%s)" % sql,
                        params).fetchone()[0]


def folder_tree(conn):
    """The library's folders as the navigator draws them: [{id, parent_id, path (native), name,
    direct, recursive}], `direct` the photos in the folder itself and `recursive` those in it and
    below, from one GROUP BY of photo_folder rolled up the tree here -- not a range query a folder.
    The order is not promised. Raises paths.RootsError for a root this machine does not place."""
    direct = dict(conn.execute("SELECT folder_id, COUNT(*) FROM photo_folder GROUP BY folder_id"))
    rows = conn.execute("SELECT id, parent_id, path, name FROM folders").fetchall()
    total = {folder_id: direct.get(folder_id, 0) for folder_id, _parent, _path, _name in rows}
    parent_of = {folder_id: parent_id for folder_id, parent_id, _path, _name in rows}

    def depth(folder_id):
        count = 0
        while parent_of.get(folder_id) is not None:
            folder_id, count = parent_of[folder_id], count + 1
        return count

    for folder_id in sorted(parent_of, key=depth, reverse=True):
        if parent_of[folder_id] in total:
            total[parent_of[folder_id]] += total[folder_id]
    native = store_roots.natives(conn, rows, 2)
    return [{"id": folder_id, "parent_id": parent_id, "path": path, "name": name,
             "direct": direct.get(folder_id, 0), "recursive": total[folder_id]}
            for folder_id, parent_id, path, name in native]
