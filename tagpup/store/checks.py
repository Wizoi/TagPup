"""What a library holds, and whether it keeps its own rules: read-only, counts first.

tools/doctor.py reports these, scripts/verify_workflow.py compares them before and after
a run, and the MCP phase (docs/ARCHITECTURE.md) is to answer them as tools. Each check returns
how many rows break the rule and a few of them, so a report can say how much without
saying whose.
"""
import collections
import os

from tagpup.core import paths
from tagpup.store import derived, generations, people, schema
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
    current = schema.version(conn)
    return _check("migrations not applied", [m.name for m in schema.MIGRATIONS if m.version > current])


def generations_kept(conn):
    """What stops the generations moving as they should: a row or a trigger missing, or
    the counters of an older version, made again beside them (docs/findings.md, #55,
    #60). Before migration 2 makes them, nothing: schema_current says why."""
    if schema.version(conn) < 2:
        return _check("generations not kept", [])
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
    broken += ["left over: %s" % name for name in schema.legacy_counters(conn)]
    return _check("generations not kept", broken)


def _faces_by_id(conn):
    """Whether faces point at photos by id (migration 4). The doctor reads a library as it
    is: on one from before, the face checks wait, and schema_current says why (#78)."""
    return "photo_id" in {row[1] for row in conn.execute("PRAGMA table_info(faces)")}


def faces_without_a_photo(conn):
    """Faces whose photo row is gone: a photo deleted on a connection without foreign
    keys, by something other than the store's own deletes."""
    if not _faces_by_id(conn):
        return _check("faces with no photo row", [])
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
    did not rebuild (docs/findings.md, #42, #63). Waits for migration 6."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'photo_people'").fetchone():
        return _check("photos whose people are out of date", [])
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
    them so. Waits for migration 19."""
    if not derived.present(conn):
        return _check("photos whose keyword rows are out of date", [])
    return _check("photos whose keyword rows are out of date", derived.stale_tags(conn))


def photo_folders_out_of_date(conn):
    """Photos not in the folder their path names (`photo_folder`), or in one though it names none."""
    if not derived.present(conn):
        return _check("photos not in the folder their path names", [])
    return _check("photos not in the folder their path names", derived.stale_folders(conn)[0])


def folders_out_of_date(conn):
    """Folders whose parent is not the folder above them (the chain to the top is complete), or that a
    photo needs and the table has not (counted, with no id to show). A folder holding no photo is
    `empty_folders`'s: reported, not broken."""
    if not derived.present(conn):
        return _check("folders that are not what the photos' paths give", [])
    stale = derived.stale_folders(conn)
    return Check("folders that are not what the photos' paths give", len(stale.folders) + stale.missing,
                 stale.folders[:EXAMPLES])


def empty_folders(conn):
    """The ids of the folders with no photo at or below them: what a delete that did not come through the
    store leaves (its own prune takes them). Reported, not broken: a count of the photos in a folder is the
    same with one there. `tools/doctor.py --rebuild-derived --apply` takes them."""
    return derived.stale_folders(conn).strays if derived.present(conn) else []


def photo_meta_out_of_date(conn):
    """Photos whose rating, camera, size and place (`photo_meta`) are not what their raw metadata gives."""
    if not derived.present(conn):
        return _check("photos whose metadata rows are out of date", [])
    return _check("photos whose metadata rows are out of date", derived.stale_meta(conn))


def tags_without_a_node(conn):
    """(distinct keywords, uses, photos, [(keyword, photos carrying it)] the most used first) of the
    keywords photos carry that name no node of the tag tree, so have no row in `photo_tags`. Reported,
    not broken: the tree is the owner's, indexing never adds to it, and a node made for the keyword
    gives its photos their rows. A library without the tables says none."""
    if not derived.present(conn):
        return 0, 0, 0, []
    found = derived.unnamed_keywords(conn)
    ranked = sorted(found.by_tag.items(), key=lambda pair: (-pair[1], pair[0]))
    return len(ranked), sum(found.by_tag.values()), found.photos, ranked


def orphan_nodes(conn):
    """Tag-tree nodes whose parent is not in the tree."""
    return _check("tree nodes whose parent is missing", [tag for (tag,) in conn.execute(
        "SELECT c.tag FROM tag_taxonomy c LEFT JOIN tag_taxonomy p ON p.id = c.parent_id"
        " WHERE c.parent_id IS NOT NULL AND p.id IS NULL ORDER BY c.tag")])


def crops_without_a_face(conn):
    """Crops whose face is gone. The trigger that takes a crop with its face makes this
    none, whichever connection deletes the face. A library from before migration 3 keeps
    its crops in faces, and has none to lose."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'face_crops'").fetchone():
        return _check("crops whose face is gone", [])
    return _check("crops whose face is gone", [face_id for (face_id,) in conn.execute(
        "SELECT c.face_id FROM face_crops c LEFT JOIN faces f ON f.id = c.face_id"
        " WHERE f.id IS NULL ORDER BY c.face_id")])


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()


def _without_a_photo(conn, table, label):
    """The photo ids `table` holds rows for that have no photo: what its trigger, which
    takes them with their photo, makes none. Waits for the migration that makes it."""
    if not _table(conn, table):
        return _check(label, [])
    return _check(label, [photo_id for (photo_id,) in conn.execute(
        "SELECT DISTINCT t.photo_id FROM %s t LEFT JOIN photos p ON p.id = t.photo_id"
        " WHERE p.id IS NULL ORDER BY t.photo_id" % table)])


def vectors_without_a_photo(conn):
    """CLIP vectors whose photo is gone (migration 5; #65)."""
    return _without_a_photo(conn, "embeddings", "vectors whose photo is gone")


def people_without_a_photo(conn):
    """People listed for a photo that is gone (migration 6)."""
    return _without_a_photo(conn, "photo_people", "people listed for a photo that is gone")


def suggestions_without_a_photo(conn):
    """Suggestions kept for a photo that is gone (migration 7; #64)."""
    return _without_a_photo(conn, "suggestions", "suggestions for a photo that is gone")


def one_file_two_rows(conn):
    """Photos with more than one row, their paths differing only as paths.key ignores."""
    try:
        listed = store_roots.natives(conn, conn.execute("SELECT path FROM photos").fetchall(), 0)
    except paths.RootsError:
        listed = []   # a root this machine does not place: rooted_rows_convert says so
    seen = collections.Counter(paths.key(p) for (p,) in listed)
    return _check("photos with two rows", sorted(k for k, n in seen.items() if n > 1))


def without_a_vector(conn, model):
    """How many photos have no CLIP vector under `model` (tagpup.store.embeddings), the one
    search reads. Reported, not broken: the next index of their folders computes them."""
    if not _table(conn, "embeddings"):
        return 0
    return conn.execute("SELECT COUNT(*) FROM photos p WHERE NOT EXISTS"
                        " (SELECT 1 FROM embeddings e WHERE e.photo_id = p.id AND e.model = ?)",
                        (model,)).fetchone()[0]


def faces_to_detect(conn):
    """How many photos have faces still to be detected (tagpup.store.faces_pending).
    Reported, not broken: the next index of their folders detects them."""
    from tagpup.store import faces_pending
    return faces_pending.count(conn)


def missing_files(conn):
    """Rows whose file is not on disk, as (folder, rows, whether the folder is there).
    Reported, not broken: a folder on an unplugged drive looks the same as a deleted one
    (docs/ARCHITECTURE.md, phase 8)."""
    by_folder = collections.Counter()
    for (path,) in store_roots.natives(conn, conn.execute("SELECT path FROM photos").fetchall(), 0):
        if not os.path.exists(path):
            by_folder[os.path.dirname(path)] += 1
    return [(folder, count, os.path.isdir(folder)) for folder, count in sorted(by_folder.items())]


# ---- The library's roots (docs/ARCHITECTURE.md, "Roots and machines") -------------------------

#: The tables with a column of paths the roots convert, and the column (tagpup.store.adoption.TABLES). Not
#: the derived `folders`: its paths are the photos' folders in the form the photos' rows are in, which the
#: adoption rebuilds, and `photo_folders_out_of_date` / `folders_out_of_date` compare it with the photos.
ROOT_COLUMNS = (("photos", "path"), ("change_files", "path"), ("change_files", "new_path"),
                ("added_folders", "path"), ("damaged_files", "path"))


def _roots_or_why(conn):
    """(the Roots of the library on `conn`, None) -- or (None, why not): the machine's map is
    missing or does not place a root. A check says so; it does not stop."""
    try:
        return store_roots.roots_for(conn), None
    except paths.RootsError as problem:
        return None, str(problem)


def _path_rows(conn):
    """(table, row id, the path) of every row of each ROOT_COLUMNS: the key of the row in its
    table (the id; the path itself, as a rowid, for the tables keyed by it)."""
    for table, column in ROOT_COLUMNS:
        if not _table(conn, table):
            continue
        key = "id" if table in ("photos", "change_files") else "rowid"
        for row_id, value in conn.execute("SELECT %s, %s FROM %s WHERE %s IS NOT NULL" % (key, column, table, column)):
            yield table, row_id, value


def rooted_rows_convert(conn):
    """Rooted rows -- `@name/...` in a path column -- that cannot be read: the root is not the
    library's, this machine does not place it (machine_roots.json), or the row is not what
    paths.to_row writes. A photo is named by its id, any other row by its table and key."""
    if not _table(conn, "roots"):
        # Behind migration 18: nothing can be rooted, and a row that says so is not a root's.
        return _check("rooted rows that do not convert back", [])
    held = {name for name, _address in store_roots._rows(conn)}
    roots, _why = _roots_or_why(conn)
    broken = []
    for table, row_id, value in _path_rows(conn):
        if not value.startswith(paths.ROOT_MARK):
            continue
        name = value[1:].partition(paths.ROW_SEP)[0].lower()
        good = name in held and roots is not None
        if good:
            try:
                found = roots.locate(paths.from_row(value, roots))
                # Held under one root, though the file lies under another's place: a nested root's
                # rows left under the outer one, which a lookup under the inner root misses.
                good = found is None or found[0] == name
            except paths.RootsError:
                good = False
        if not good:
            broken.append(row_id if table == "photos" else "%s %s" % (table, row_id))
    return _check("rooted rows that do not convert back", broken)


def native_rows_under_a_root(conn):
    """Rows that still hold a native path under the location of a root the library has: a
    write that converted by the roots the library had before another process adopted one. The
    adoption converts every row under the root, and every write after it converts its own; one
    here was written between (docs/ARCHITECTURE.md, "Roots and machines"). A photo is named by
    its id."""
    if not _table(conn, "roots") or not store_roots._rows(conn):
        return _check("native rows under a root", [])
    roots, _why = _roots_or_why(conn)
    if roots is None:
        return _check("native rows under a root", [])
    broken = []
    for table, row_id, value in _path_rows(conn):
        if value and not value.startswith(paths.ROOT_MARK) and roots.locate(value) is not None:
            broken.append(row_id if table == "photos" else "%s %s" % (table, row_id))
    return _check("native rows under a root", broken)


def unrooted_by_folder(conn):
    """The photos held under no root of the library, by folder: [{"group", "count"}], the
    biggest first (paths.outside_roots). Reported, not broken: a folder outside the roots can be
    opened and tagged and uses the library (docs/ARCHITECTURE.md). A library with no roots has
    every photo native, and says nothing."""
    if not _table(conn, "roots") or not store_roots._rows(conn):
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
         folders_out_of_date, photo_meta_out_of_date)


def run(conn):
    """Every rule's Check, in RULES order."""
    return [rule(conn) for rule in RULES]
