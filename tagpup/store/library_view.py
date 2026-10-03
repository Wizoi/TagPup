"""What the library views ask of the database (docs/ARCHITECTURE.md, phase 9a-2): which photos a source
holds, in what order, a page at a time, and the counts the navigator shows.

A SOURCE says which photos: all of them; a folder -- by its path, never an id -- and, when asked, its
subfolders; a keyword and everything under it; a person; a year; a month. Every source is ordered the
same way, by when the photo was taken and then by id, those with no date at the end (by id), and paged
by a KEYSET -- the (taken, id) of the last photo of the page before -- never by OFFSET, so a page costs
what a page costs however far down it is, and a photo added, taken away or re-dated between two pages
neither repeats nor skips another.

`taken` is the text ExifTool gave ("2024:06:01 10:00:00", with a zone after it in a few), which sorts as
the time does; a few rows hold a date written with dashes, which sort apart from the rest and are paged
all the same. The plans are SEARCHes, never a SCAN of photos, on a library of photo_index's scale
(tests/test_library_view_plans.py): `idx_photos_taken` and `idx_photos_year` (migration 20) for the whole
library, a year and a month; the path index for a folder and its subfolders; `photo_folder` for a folder
alone; `photo_tags` through the tag tree's unique index for a keyword; `photo_people`'s name index for a
person. A source whose photos are not in date order in an index is sorted -- a folder of 20,000 photos is
7 ms.

Reads only. Every function does nothing useful on a library that has not had migrations 19 and 20 (`ready`
says so); the service answers that in a sentence.
"""
import collections

from tagpup.core import paths, vocabulary
from tagpup.store import damaged_files, derived
from tagpup.store import roots as store_roots
from tagpup.store.people import PEOPLE_JSON

#: The kinds of source (Source.kind).
ALL, FOLDER, KEYWORD, PERSON, YEAR, MONTH = "all", "folder", "keyword", "person", "year", "month"
KINDS = (ALL, FOLDER, KEYWORD, PERSON, YEAR, MONTH)

#: A source: its kind, its value -- a folder's path (native), a keyword's tag as the tree spells it, a person's
#: name, a year as an integer, a month as "YYYY-MM"; None for all -- and, for a folder, whether its subfolders are in.
Source = collections.namedtuple("Source", "kind value recursive", defaults=(None, False))

#: Where a page begins and after what: (phase, taken, id). Phase 0 holds the photos with a date, ordered by
#: (taken, id); phase 1 those with none, ordered by id (taken is None). None begins at the start.
Cursor = collections.namedtuple("Cursor", "phase taken id")

#: Ids read in one statement.
CHUNK = 500

#: The indexes migration 20 makes.
INDEXES = ("idx_photos_taken", "idx_photos_year")


def ready(conn):
    """Has the library on `conn` what the views stand on: the derived tables (migration 19) and the indexes
    the pages are ordered by (migration 20)?"""
    if not derived.present(conn):
        return False
    found = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name IN (%s)"
                         % ",".join("?" * len(INDEXES)), INDEXES).fetchone()[0]
    return found == len(INDEXES)


# ---- A source's photos -----------------------------------------------------------------------

def month_range(month):
    """(low, high) of the `taken` texts of month "YYYY-MM": "YYYY:MM" up to "YYYY:MM;", the character after the
    ":" that follows, so that every "YYYY:MM:DD ..." is in it and "YYYY:MM" alone, and nothing of the next month."""
    year, _sep, number = month.partition("-")
    low = "%s:%s" % (year, number)
    return low, low + chr(ord(":") + 1)


#: The photos a source holds, for the statements that read them: `from_` and `where` (the photos table is `p`),
#: the `params` of the where, the (SQL, parameters) that count them, and whether every one has a date
#: (a month does: the undated photos are not looked for).
Scope = collections.namedtuple("Scope", "from_ where params count dated", defaults=(False,))


def _scope(conn, source):
    """The Scope of `source`; None for a source that holds none whatever is asked (a folder the library has no
    row of, a person nobody is called)."""
    kind = source.kind
    if kind == ALL:
        return Scope("photos p", "1", (), ("SELECT COUNT(*) FROM photos", ()))
    if kind == YEAR:
        year = (int(source.value),)
        return Scope("photos p", "p.year = ?", year, ("SELECT COUNT(*) FROM photos WHERE year = ?", year))
    if kind == MONTH:
        bounds = month_range(source.value)
        return Scope("photos p", "p.taken >= ? AND p.taken < ?", bounds,
                     ("SELECT COUNT(*) FROM photos WHERE taken >= ? AND taken < ?", bounds), dated=True)
    if kind == FOLDER:
        if source.recursive:
            where, params = store_roots.sql_under(conn, "p.path", source.value)
            return Scope("photos p", where, params, ("SELECT COUNT(*) FROM photos p WHERE " + where, params))
        where, params = store_roots.sql_equals(conn, "path", source.value)
        row = conn.execute("SELECT id FROM folders WHERE " + where, params).fetchone()
        if row is None:
            return None
        return Scope("photo_folder pf JOIN photos p ON p.id = pf.photo_id", "pf.folder_id = ?", (row[0],),
                     ("SELECT COUNT(*) FROM photo_folder WHERE folder_id = ?", (row[0],)))
    if kind == KEYWORD:
        sql, params = derived.under(resolve_tag(conn, source.value))
        # IN, not a join: a photo holding two tags under the keyword is one row, and the plan seeks
        # photo_tags's covering index once for each node and the photo by its primary key.
        return Scope("photos p", "p.id IN (SELECT photo_id FROM photo_tags WHERE tag_id IN (%s))" % sql, params,
                     ("SELECT COUNT(DISTINCT photo_id) FROM photo_tags WHERE tag_id IN (%s)" % sql, params))
    if kind == PERSON:
        names = spellings(conn, source.value)
        if not names:
            return None
        marks = ",".join("?" * len(names))
        return Scope("photos p", "p.id IN (SELECT photo_id FROM photo_people WHERE name IN (%s))" % marks, tuple(names),
                     ("SELECT COUNT(DISTINCT photo_id) FROM photo_people WHERE name IN (%s)" % marks, tuple(names)))
    raise ValueError("no such kind of source: %r" % (kind,))


def resolve_tag(conn, tag):
    """The tag of the node a typed keyword names: itself when the tree has exactly it (that wins), else the node
    it is without case and with its segments trimmed, as photo_tags ties a photo's keywords to nodes
    (derived.Tree: the lowest id when two differ only in case); `tag` as typed when there is none, which holds
    nothing."""
    if conn.execute("SELECT 1 FROM tag_taxonomy WHERE tag = ?", (tag,)).fetchone():
        return tag
    nodes = conn.execute("SELECT id, tag FROM tag_taxonomy").fetchall()
    found = derived.Tree(nodes).find(tag)
    return next((node_tag for node_id, node_tag in nodes if node_id == found), tag)


def spellings(conn, name):
    """The names `photo_people` holds that are `name` -- the same person without regard to case -- as it holds
    them: usually one. One pass of the name index (400 names on photo_index: 4 ms)."""
    wanted = vocabulary.key(name)
    return [held for (held,) in conn.execute("SELECT DISTINCT name FROM photo_people") if vocabulary.key(held) == wanted]


def _page(conn, scope, cursor, limit):
    want = limit + 1   # one more than the page says whether another page follows
    rows = []
    if cursor is None or cursor.phase == 0:
        sql = "SELECT p.taken, p.id FROM %s WHERE %s AND p.taken IS NOT NULL" % (scope.from_, scope.where)
        values = list(scope.params)
        if cursor is not None:
            sql += " AND (p.taken, p.id) > (?, ?)"
            values += [cursor.taken, cursor.id]
        sql += " ORDER BY p.taken, p.id LIMIT ?"
        rows = [(photo_id, taken) for taken, photo_id in conn.execute(sql, values + [want])]
    if len(rows) < want and not scope.dated:
        after = cursor.id if cursor is not None and cursor.phase == 1 else 0
        undated = conn.execute(
            "SELECT p.id FROM %s WHERE %s AND p.taken IS NULL AND p.id > ? ORDER BY p.id LIMIT ?"
            % (scope.from_, scope.where), list(scope.params) + [after, want - len(rows)]).fetchall()
        rows += [(photo_id, None) for (photo_id,) in undated]
    return rows[:limit], len(rows) > limit


def all_ids(conn, source, cap):
    """([photo id] of the whole source in the order `page` gives, how many photos the source holds): at most `cap`
    ids, the total counted when the source holds more. Two statements at most, one per phase, each an index-ordered
    read of ids alone -- the keyset page's order without the keyset."""
    scope = _scope(conn, source)
    if scope is None:
        return [], 0
    ids = [photo_id for (photo_id,) in conn.execute(
        "SELECT p.id FROM %s WHERE %s AND p.taken IS NOT NULL ORDER BY p.taken, p.id LIMIT ?" % (scope.from_, scope.where),
        list(scope.params) + [cap + 1])]
    if len(ids) <= cap and not scope.dated:
        ids += [photo_id for (photo_id,) in conn.execute(
            "SELECT p.id FROM %s WHERE %s AND p.taken IS NULL ORDER BY p.id LIMIT ?" % (scope.from_, scope.where),
            list(scope.params) + [cap + 1 - len(ids)])]
    if len(ids) <= cap:
        return ids, len(ids)
    return ids[:cap], conn.execute(*scope.count).fetchone()[0]


def total(conn, source):
    """How many photos `source` holds."""
    scope = _scope(conn, source)
    return 0 if scope is None else conn.execute(*scope.count).fetchone()[0]


def page(conn, source, cursor=None, limit=100):
    """([(photo id, taken)] of the page after `cursor`, whether there are more after it): `limit` photos of
    `source` in order, those with a date first by (taken, id), then those with none by id. One statement for
    each phase it reaches; `next_cursor` of the last photo continues."""
    scope = _scope(conn, source)
    return ([], False) if scope is None else _page(conn, scope, cursor, limit)


def view(conn, source, cursor=None, limit=100):
    """(page rows, whether more follow, the total): `page` and `total` with the source resolved once."""
    scope = _scope(conn, source)
    if scope is None:
        return [], False, 0
    rows, more = _page(conn, scope, cursor, limit)
    return rows, more, conn.execute(*scope.count).fetchone()[0]


def next_cursor(row):
    """The cursor that continues after `row`, (photo id, taken)."""
    photo_id, taken = row
    return Cursor(0 if taken is not None else 1, taken, photo_id)


# ---- The cards -------------------------------------------------------------------------------

def card_rows(conn, photo_ids):
    """{photo id: (path -- native --, mtime, size, taken)} of the photos `photo_ids` that have a row: one read in
    batches of CHUNK of the columns a card needs, never raw_metadata, tags or a BLOB. Raises
    paths.RootsError for a root this machine does not place."""
    found = {}
    ids = list(photo_ids)
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start:start + CHUNK]
        rows = conn.execute("SELECT id, path, mtime, size, taken FROM photos WHERE id IN (%s)" % ",".join("?" * len(chunk)),
                            chunk).fetchall()
        for photo_id, path, mtime, size, taken in store_roots.natives(conn, rows, 1):
            found[photo_id] = (path, mtime, size, taken)
    return found


def photo_row(conn, photo_id):
    """(path -- native --, mtime, size, tags JSON, people JSON, captions JSON, raw_metadata JSON, year) of the photo
    `photo_id`, or None: one row by its primary key, what the details panel shows of a photo. Raises
    paths.RootsError for a root this machine does not place."""
    rows = conn.execute("SELECT p.path, p.mtime, p.size, p.tags, " + PEOPLE_JSON + ", p.captions, p.raw_metadata, p.year"
                        " FROM photos p WHERE p.id = ?", (photo_id,)).fetchall()
    rows = store_roots.natives(conn, rows, 0, raw=(6,))
    return tuple(rows[0]) if rows else None


def damaged(conn):
    """{paths.key: store.damaged_files.Record} of every record of a damaged photo (a handful)."""
    return {paths.key(each.path): each for each in damaged_files.every(conn)}


# ---- The navigator's counts ------------------------------------------------------------------

def keyword_counts(conn):
    """[{"id", "tag", "name", "parent_id", "count"}] of every node of the tag tree: `count` the photos carrying the
    node's tag or a tag under it, each photo once for a node however many tags of it lie under it. The nodes
    are one read; the photos' tags one pass of photo_tags by photo (151,000 rows on photo_index) rolled up the
    tree's parent ids in Python -- not a range query for each node. Equal, node for node, to
    `derived.count_under_tag`."""
    nodes = conn.execute("SELECT id, tag, parent_id, name FROM tag_taxonomy").fetchall()
    parent = {node_id: parent_id for node_id, _tag, parent_id, _name in nodes}
    upward = {}   # each node and every node above it: a damaged tree that loops ends the walk, never the call
    for node_id in parent:
        walked, seen, here = [], set(), node_id
        while here is not None and here not in seen:
            seen.add(here)
            walked.append(here)
            here = parent.get(here)
        upward[node_id] = tuple(walked)
    counts = collections.Counter()
    sets = collections.Counter()   # photos for each distinct set of tags: most photos share theirs
    for _photo, tag_ids in conn.execute("SELECT photo_id, group_concat(tag_id) FROM photo_tags GROUP BY photo_id"):
        sets[tag_ids] += 1
    for tag_ids, photos in sets.items():
        reached = set()
        for tag_id in tag_ids.split(","):
            reached.update(upward.get(int(tag_id), ()))
        for node_id in reached:
            counts[node_id] += photos
    return [{"id": node_id, "tag": tag, "name": name, "parent_id": parent_id, "count": counts.get(node_id, 0)}
            for node_id, tag, parent_id, name in nodes]


def people_counts(conn):
    """[(name, photos)] of everyone in photo_people, most photos first, then by name: one pass of the name
    index. Names that are one person without regard to case are one entry, under the spelling most photos
    hold, counting each photo once."""
    held = conn.execute("SELECT name, COUNT(*) FROM photo_people GROUP BY name").fetchall()
    grouped = collections.defaultdict(list)
    for name, count in held:
        grouped[vocabulary.key(name)].append((name, count))
    found = []
    for spellings_of in grouped.values():
        spellings_of.sort(key=lambda each: (-each[1], each[0]))
        name = spellings_of[0][0]
        if len(spellings_of) == 1:
            found.append((name, spellings_of[0][1]))
            continue
        names = [each[0] for each in spellings_of]
        found.append((name, conn.execute("SELECT COUNT(DISTINCT photo_id) FROM photo_people WHERE name IN (%s)"
                                         % ",".join("?" * len(names)), names).fetchone()[0]))
    return sorted(found, key=lambda each: (-each[1], each[0]))


def date_counts(conn):
    """{"years": [{"year", "count", "months": [{"month": "YYYY-MM", "count"}], "other"}], "undated": n}: the photos
    of each year (`photos.year`, the year taken or else the one in its name), and of each month of it by
    `taken`; `other` is those of the year whose `taken` is no month of it (a date written with dashes, or
    none). One statement, a scan of idx_photos_year, grouped. Years are in order, months in order."""
    years = {}
    undated = 0
    for year, month, count in conn.execute(
            "SELECT year, substr(taken, 1, 7), COUNT(*) FROM photos GROUP BY year, substr(taken, 1, 7)"):
        if year is None:
            undated += count
            continue
        entry = years.setdefault(year, {"year": year, "count": 0, "months": [], "other": 0})
        entry["count"] += count
        if month is not None and len(month) == 7 and month[:4] == "%04d" % year and month[4] == ":" and month[5:].isdigit() \
                and 1 <= int(month[5:]) <= 12:
            entry["months"].append({"month": "%s-%s" % (month[:4], month[5:]), "count": count})
        else:
            entry["other"] += count
    for entry in years.values():
        entry["months"].sort(key=lambda each: each["month"])
    return {"years": [years[year] for year in sorted(years)], "undated": undated}


def folders(conn):
    """The library's folder tree with counts (derived.folder_tree)."""
    return derived.folder_tree(conn)
