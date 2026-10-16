"""What a library holds, and whether it keeps its own rules: read-only, counts first.

tools/doctor.py reports these, scripts/verify_workflow.py compares them before and after
a run, and the MCP phase (docs/ARCHITECTURE.md) is to answer them as tools. Each check returns
how many rows break the rule and a few of them, so a report can say how much without
saying whose.
"""
import collections
import os
import weakref

from tagpup.core import paths, vocabulary
from tagpup.store import derived, generations, people, person_ids, roots_check, schema, search_index, taxonomy
from tagpup.store import roots as store_roots

#: One rule and what breaks it. `examples` are paths, ids or tags, a few at most.
Check = collections.namedtuple("Check", "name count examples")

#: How many of the rows that break a rule a check keeps to show.
EXAMPLES = 5


def summary(conn):
    """{photos, faces, named, manual, excluded, untagged}: what the library holds."""
    photos, untagged = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(CASE WHEN tags IS NULL OR tags = '[]' THEN 1 ELSE 0 END), 0)"
        " FROM photos").fetchone()
    faces, named, manual, excluded = conn.execute(
        "SELECT COUNT(*), COUNT(name), COALESCE(SUM(CASE WHEN name_source = 'manual' THEN 1 ELSE 0 END), 0),"
        " COALESCE(SUM(excluded), 0) FROM faces").fetchone()
    return dict(photos=photos, faces=faces, named=named, manual=manual, excluded=excluded,
                untagged=untagged)


def _check(name, broken):
    broken = list(broken)
    return Check(name, len(broken), broken[:EXAMPLES])


def schema_current(conn):
    """Migrations not yet applied (tagpup.store.schema)."""
    return _check("migrations not applied", [m.name for m in schema.pending_on(conn)])


def generations_kept(conn):
    """What stops the generations moving as they should: a row or a trigger missing, or
    the counters of an older version, made again beside them (docs/findings.md, #55,
    #60)."""
    objects = {(kind, name) for kind, name in conn.execute("SELECT type, name FROM sqlite_master")}
    broken = []
    if ("table", "generations") not in objects:
        broken += ["row %s" % n for n in generations.NAMES]
    else:
        counted = {name for (name,) in conn.execute("SELECT name FROM generations")}
        broken += ["row %s" % n for n in generations.NAMES if n not in counted]
    broken += ["trigger generation_%s_%s" % (n, event) for n in generations.NAMES
               for event in ("insert", "delete", "update")
               if ("trigger", "generation_%s_%s" % (n, event)) not in objects]
    return _check("generations not kept", broken)


def faces_without_a_photo(conn):
    """Faces whose photo row is gone: a photo deleted on a connection without foreign
    keys, by something other than the store's own deletes."""
    return _check("faces with no photo row", [face_id for (face_id,) in conn.execute(
        "SELECT f.id FROM faces f LEFT JOIN photos p ON p.id = f.photo_id"
        " WHERE p.id IS NULL ORDER BY f.id")])


def named_and_excluded(conn):
    """Faces both named and excluded: ruled out and claimed, which no view shows."""
    return _check("faces named and excluded", [i for (i,) in conn.execute(
        "SELECT id FROM faces WHERE excluded = 1 AND name IS NOT NULL ORDER BY id")])


def people_out_of_date(conn):
    """Photos whose people are not what the rule makes them from their keywords, faces
    and the tree (tagpup.store.people.rebuild): a writer that changed one of them and
    did not rebuild (docs/findings.md, #42, #63)."""
    stale = set(people.stale(conn))
    try:
        listed = store_roots.natives(conn, conn.execute("SELECT id, path FROM photos").fetchall(), 1)
    except paths.RootsError:
        # A root this machine does not place: the paths cannot be spelled. rooted_rows_convert says so.
        listed = []
    return _check("photos whose people are out of date", sorted(path for photo_id, path in listed if photo_id in stale))


# ---- The derived tables (tagpup.store.derived; docs/ARCHITECTURE.md, phase 9a) -----------------------

def photo_tags_out_of_date(conn):
    """Photos whose keyword rows (`photo_tags`) are not what their keywords and the tag tree give: a
    writer that changed one and did not refresh (tagpup.store.derived), or rows made by an older
    version of the app that did not know them. `tools/doctor.py --rebuild-derived --apply` makes
    them so."""
    return _check("photos whose keyword rows are out of date", derived.stale_tags(conn))


def photo_folders_out_of_date(conn):
    """Photos not in the folder their path names (`photo_folder`), or in one though it names none."""
    return _check("photos not in the folder their path names", derived.stale_folders(conn)[0])


def folders_out_of_date(conn):
    """Folders whose parent is not the folder above them (the chain to the top is complete), or that a
    photo needs and the table has not (counted, with no id to show). A folder holding no photo is
    `empty_folders`'s: reported, not broken."""
    stale = derived.stale_folders(conn)
    return Check("folders that are not what the photos' paths give", len(stale.folders) + stale.missing,
                 stale.folders[:EXAMPLES])


def empty_folders(conn):
    """The ids of the folders with no photo at or below them: what a delete that did not come through the
    store leaves (its own prune takes them). Reported, not broken: a count of the photos in a folder is the
    same with one there. `tools/doctor.py --rebuild-derived --apply` takes them."""
    return derived.stale_folders(conn).strays


def search_index_out_of_date(conn):
    """Photos whose rows of the word index (tagpup.store.search_index) are not what their rows give: a photo with no row,
    a row of no photo, and of a sample of SAMPLE photos those whose texts are not found in their rows. A writer that
    changed a photo's keywords, captions, people or path and did not refresh, or an older version of the app that does
    not know the index. `tools/doctor.py --rebuild-derived --apply` makes it so. What it cannot see
    -- a word left behind (a contentless table cannot be read back), a photo outside the sample -- is
    search_index.LIMITS, which a report says beside the count (#752)."""
    return _check("photos whose word index rows are out of date", search_index.stale(conn))


def tags_without_a_node(conn):
    """(distinct keywords, uses, photos, [(keyword, photos carrying it)] the most used first) of the
    keywords photos carry that name no node of the tag tree, so have no row in `photo_tags`. Reported,
    not broken: the tree is the owner's, indexing never adds to it, and a node made for the keyword
    gives its photos their rows."""
    found = derived.unnamed_keywords(conn)
    ranked = sorted(found.by_tag.items(), key=lambda pair: (-pair[1], pair[0]))
    return len(ranked), sum(found.by_tag.values()), found.photos, ranked


# ---- People by their node's id (tagpup.store.person_ids; docs/ARCHITECTURE.md, "Identity by id") ------------

def face_person_ids_out_of_step(conn):
    """Faces whose name is not their person's: the id (`faces.tag_id`) is the person and `name` the cache of the node's leaf, so a
    name written without the id (a version of the app from before migration 29 that renamed a face) or an id of a node that is
    gone. (A name with no id is not this: `names_without_a_person` reports it.) The examples are face ids. `tools/doctor.py --rebuild-derived
    --apply` puts them right, the id being the key."""
    found = person_ids.out_of_step(conn, "faces", EXAMPLES)
    return Check("faces whose name is not their person's", found.rows, found.examples)


def listed_person_ids_out_of_step(conn):
    """People listed for a photo (`photo_people`) whose name is not their person's, as face_person_ids_out_of_step says it of
    faces. The examples are photo ids."""
    found = person_ids.out_of_step(conn, "photo_people", EXAMPLES)
    return Check("people listed whose name is not their person's", found.rows, found.examples)


def names_without_a_person(conn):
    """(names no person node is called: {name: rows}, names only a
    branch is: {name: ([node ids], rows)}, names exactly one person is called whose rows are linked to nobody:
    {name: (id, faces by hand, other faces, listed)}) of faces and photos' people (person_ids.unresolved). Reported, not
    broken: none has an id, none is guessed, and the tree is the owner's to settle (the ambiguous person
    path, docs/findings.md, #27; a branch is not a person, #660)."""
    return person_ids.unresolved(conn)


def keywords_under_a_person(conn):
    """How many distinct keywords the photos hold that would go under a person faces or photos use, so are left without a node
    (a node would turn the person into a group, rule a). Reported, not broken: the keyword is the file's, the person the
    owner's. Reads every photo's keywords, as `tags_without_a_node` does."""
    tree = derived.Tree.read(conn)
    under = set()
    for (tags_json,) in conn.execute("SELECT tags FROM photos WHERE tags IS NOT NULL AND tags != '[]'"):
        for text in set(derived.keywords_of(tags_json)):
            if vocabulary.normalize(text) and tree.find(text) is None and taxonomy.under_a_used_person(conn, tree, text):
                under.add(derived.keyword(text))
    return len(under)


def people_at_the_top(conn):
    """The tags of the people directly under the tree's root, not filed in a branch (taxonomy.top_level_people). Reported, not
    broken: a name or a keyword seen for the first time is made a node there (every tag has an id, 2026-10-10), and the owner
    merges it into the right branch in the tag view of the library. Counts in the doctor; the tags only with --show."""
    return taxonomy.top_level_people(conn)


def people_on_faces_alone(conn):
    """(photos, people) a photo lists from a face alone (tagpup.store.people.on_faces_alone): reported, not broken --
    mending it writes the keywords into the photo files, which only the owner's `tags-from-faces --apply` does."""
    photos, rows = conn.execute("SELECT COUNT(DISTINCT photo_id), COUNT(*) FROM photo_people WHERE source = 'face'").fetchone()
    return photos, rows


def orphan_nodes(conn):
    """Tag-tree nodes whose parent is not in the tree."""
    return _check("tree nodes whose parent is missing", [tag for (tag,) in conn.execute(
        "SELECT c.tag FROM tag_taxonomy c LEFT JOIN tag_taxonomy p ON p.id = c.parent_id"
        " WHERE c.parent_id IS NOT NULL AND p.id IS NULL ORDER BY c.tag")])


def crops_without_a_face(conn):
    """Crops whose face is gone. The trigger that takes a crop with its face makes this
    none, whichever connection deletes the face."""
    return _check("crops whose face is gone", [face_id for (face_id,) in conn.execute(
        "SELECT c.face_id FROM face_crops c LEFT JOIN faces f ON f.id = c.face_id"
        " WHERE f.id IS NULL ORDER BY c.face_id")])


def _without_a_photo(conn, table, label):
    """The photo ids `table` holds rows for that have no photo: what its trigger, which
    takes them with their photo, makes none."""
    return _check(label, [photo_id for (photo_id,) in conn.execute(
        "SELECT DISTINCT t.photo_id FROM %s t LEFT JOIN photos p ON p.id = t.photo_id"
        " WHERE p.id IS NULL ORDER BY t.photo_id" % table)])


def vectors_without_a_photo(conn):
    """CLIP vectors whose photo is gone (#65)."""
    return _without_a_photo(conn, "embeddings", "vectors whose photo is gone")


def people_without_a_photo(conn):
    """People listed for a photo that is gone."""
    return _without_a_photo(conn, "photo_people", "people listed for a photo that is gone")


def suggestions_without_a_photo(conn):
    """Suggestions kept for a photo that is gone (#64)."""
    return _without_a_photo(conn, "suggestions", "suggestions for a photo that is gone")


def one_file_two_rows(conn):
    """Photos with more than one row, their paths differing only as paths.key ignores. Reported, not repaired:
    the owner merges the rows of one file by hand (the command that did it is gone, 2026-10-10)."""
    try:
        listed = store_roots.natives(conn, conn.execute("SELECT path FROM photos").fetchall(), 0)
    except paths.RootsError:
        listed = []   # a root this machine does not place: rooted_rows_convert says so
    seen = collections.Counter(paths.key(p) for (p,) in listed)
    return _check("photos with two rows", sorted(k for k, n in seen.items() if n > 1))


def without_a_vector(conn, model):
    """How many photos have no CLIP vector under `model` (tagpup.store.embeddings), the one
    search reads. Reported, not broken: the next index of their folders computes them."""
    return conn.execute("SELECT COUNT(*) FROM photos p WHERE NOT EXISTS"
                        " (SELECT 1 FROM embeddings e WHERE e.photo_id = p.id AND e.model = ?)",
                        (model,)).fetchone()[0]


# ---- The library's roots (docs/ARCHITECTURE.md, "Roots and machines") -------------------------

def _roots_or_why(conn):
    """(the Roots of the library on `conn`, None) -- or (None, why not): the machine's map is
    missing or does not place a root. A check says so; it does not stop."""
    try:
        return store_roots.roots_for(conn), None
    except paths.RootsError as problem:
        return None, str(problem)


#: The last scan of each connection's rooted rows (roots_check.broken): {conn: (what says nothing was written since -- the
#: connection's own changes and other connections' commits, PRAGMA data_version --, the Roots it was read by, what it found)}.
#: The two rules below read one scan, not one each (#1383: 0.34 s each on photo_index); a write or another Roots reads again.
_scanned = weakref.WeakKeyDictionary()


def _rooted(conn):
    roots = _roots_or_why(conn)[0]
    stamp = (conn.total_changes, conn.execute("PRAGMA data_version").fetchone()[0])
    held = _scanned.get(conn)
    if held is None or held[0] != stamp or held[1] is not roots:
        held = _scanned[conn] = (stamp, roots, roots_check.broken(conn, roots)[0])
    return held[2]


def rooted_rows_convert(conn):
    """Rooted rows -- `@name/...` in a path column -- that cannot be read: the root is not the
    library's, this machine does not place it (machine_roots.json), the row is not what
    paths.to_row writes, or the file lies under another root's place (roots_check.broken). A photo
    is named by its id, any other row by its table and key."""
    found = _rooted(conn)
    return _check("rooted rows that do not convert back", [row for kind in roots_check.DO_NOT_CONVERT for row in found[kind]])


def native_rows_under_a_root(conn):
    """Rows that still hold a native path under the location of a root the library has: a write
    that converted by the roots the library had before another process added one
    (docs/ARCHITECTURE.md, "Roots and machines"; roots_check.broken). Nothing when this machine
    does not place the roots: rooted_rows_convert says so. A photo is named by its id."""
    return _check("native rows under a root", _rooted(conn)[roots_check.NATIVE])


def unrooted_by_folder(conn):
    """The photos held under no root of the library, by folder: [{"group", "count"}], the
    biggest first (paths.outside_roots). Reported, not broken: a folder outside the roots can be
    opened and tagged and uses the library (docs/ARCHITECTURE.md). A library with no roots has
    every photo native, and says nothing."""
    if not store_roots.logical(conn):
        return []
    roots, _why = _roots_or_why(conn)
    if roots is None:
        return []
    return paths.outside_roots([os.path.dirname(path) for (path,) in conn.execute("SELECT path FROM photos")
                                if not path.startswith(paths.ROOT_MARK)], roots)


#: The rules a library keeps, in the order a report lists them.
RULES = (schema_current, generations_kept, faces_without_a_photo, named_and_excluded,
         people_out_of_date, orphan_nodes, crops_without_a_face, vectors_without_a_photo,
         people_without_a_photo, suggestions_without_a_photo, one_file_two_rows,
         rooted_rows_convert, native_rows_under_a_root, photo_tags_out_of_date, photo_folders_out_of_date,
         folders_out_of_date, search_index_out_of_date, face_person_ids_out_of_step,
         listed_person_ids_out_of_step)


def run(conn):
    """Every rule's Check, in RULES order. A library behind the code's schema has none of the tables the other rules read:
    it is migrated first (a started TagPup does so), and only the migrations not applied are said."""
    behind = schema_current(conn)
    return [behind] if behind.count else [rule(conn) for rule in RULES]
