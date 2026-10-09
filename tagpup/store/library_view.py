"""What the library views ask of the database (docs/ARCHITECTURE.md, phase 9a-2): which photos a source
holds, in what order, a page at a time, and the counts the navigator shows.

A SOURCE says which photos: all of them; a folder -- by its path, never an id -- and, when asked, its
subfolders; a keyword and everything under it, or its node alone (`keyword_only`); a person; a year; the
photos of a year whose date names no month of it (`year_other`); a month; or ANY OF a list of those (a
union: the rows a person selected in the navigator, phase 9 #672 -- one statement, a photo in two of
them once); or a SEARCH (phase 9e): the photos in ALL OF a list of sources, ANY OF another and NONE OF a third, and
-- once the library has its word index -- matching the words typed, again one statement. Every source is ordered by when the photo was taken and then by id, those with no date at
the end (by id), unless another order is asked (ORDERS: Date Taken, file name or caption, either way), and
paged by a KEYSET -- the (taken, id) of the last photo of the page before -- never by OFFSET, so a page costs
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
import time

from tagpup.core import paths, vocabulary
from tagpup.core.result import Refused
from tagpup.store import damaged_files, db, derived, person_ids, schema, search_index
from tagpup.store import roots as store_roots
from tagpup.store.people import PEOPLE_JSON

#: The kinds of source (Source.kind).
ALL, FOLDER, KEYWORD, PERSON, YEAR, MONTH = "all", "folder", "keyword", "person", "year", "month"
#: What one row of the navigator holds of its own, apart from the rows under it (phase 9, #672): a keyword's node without
#: the nodes under it, and the photos of a year whose date names no month of it (the "Other" row under the year).
KEYWORD_ONLY, YEAR_OTHER = "keyword_only", "year_other"
#: A union: the photos of any of a list of the sources above (Source.value is a tuple of them, none of them a union).
#: Phase 9e's search extends it: its `any_of` is this list (docs/ARCHITECTURE.md, phase 9 review, #672).
ANY_OF = "any_of"
#: A search (phase 9e): Source.value is a Search. Its lists hold sources of the kinds a union may hold, and its `all_of` a
#: union too (the navigator's selection, "within" which the search looks: docs/ARCHITECTURE.md, phase 9e-1).
SEARCH = "search"
KINDS = (ALL, FOLDER, KEYWORD, PERSON, YEAR, MONTH, KEYWORD_ONLY, YEAR_OTHER, ANY_OF, SEARCH)
#: The kinds a union may hold.
MEMBER_KINDS = (ALL, FOLDER, KEYWORD, PERSON, YEAR, MONTH, KEYWORD_ONLY, YEAR_OTHER)
#: The kinds whose photos are a range of another index of photos (Scope.ranged): a union of only these is one too.
RANGED = (YEAR, MONTH, YEAR_OTHER)

#: A source: its kind, its value -- a folder's path (native), a keyword's tag as the tree spells it, a person's
#: name, a year as an integer, a month as "YYYY-MM", a union's tuple of Sources; None for all -- and, for a folder,
#: whether its subfolders are in.
Source = collections.namedtuple("Source", "kind value recursive", defaults=(None, False))

#: What a search asks (Source.value of a SEARCH): the photos in every source of `all_of` (each a member kind or a union),
#: in at least one of `any_of` (none: no such condition), in none of `none_of`, and -- `words`, text, "" for none --
#: matching every word typed. Tuples, so a Source holding one is hashable as a union's members are.
Search = collections.namedtuple("Search", "all_of any_of none_of words", defaults=((), (), (), ""))


class NoWordIndex(Refused):
    """A search asked for words of a library at the version that makes the word index and without it (its tables dropped
    by hand): refused with a sentence, wherever the search is read -- a page, the ids, a selection, a bulk edit."""

    def __init__(self, sentence=None):
        super().__init__(sentence or "This library has no word index, though it is at the version that makes one: search "
                                     "by tags, people, folders and dates.")


class WordIndexComing(NoWordIndex):
    """A search asked for words of a library below migration 24 (#753). The server brings every library it serves up to
    date as it starts and as a request first names it, so such a library is one whose migration is under way -- the
    startup thread, or another program, holds it -- or due: the web layer answers 503 with Retry-After, and the page asks
    again (docs/ARCHITECTURE.md, phase 9e-1, the contract for 9e-2)."""

    def __init__(self):
        super().__init__("The word index is being made now; try again in a few seconds. Searching by tags, people, "
                         "folders and dates works meanwhile.")

#: Where a page begins and after what: (phase, taken, id). Phase 0 holds the photos with a date, ordered by
#: (taken, id); phase 1 those with none, ordered by id (taken is None). None begins at the start. In an order by name
#: `taken` holds the file name of the last photo and the phase is 0: every photo has a name. In an order by caption
#: phase 0 holds the captioned photos, `taken` the caption's key (caption_sql), and phase 1 those with none.
Cursor = collections.namedtuple("Cursor", "phase taken id")

#: The orders a source is read in (phase 9, #671, #714): by Date Taken -- the photos with none AFTER the dated ones in both
#: directions, by id in the order's direction --, by file name, or by caption -- the photos with none after the captioned
#: ones in both directions, as the undated are --, the names and captions without case (NOCASE, as the indexes hold
#: them), either way; ties broken by id, in the order's direction.
TAKEN, TAKEN_DESC, NAME, NAME_DESC, CAPTION, CAPTION_DESC = (
    "taken", "taken-desc", "name", "name-desc", "caption", "caption-desc")
ORDERS = (TAKEN, TAKEN_DESC, NAME, NAME_DESC, CAPTION, CAPTION_DESC)
#: The orders read last first.
DESCENDING = (TAKEN_DESC, NAME_DESC, CAPTION_DESC)

#: The index migration 22 makes: the photos by file name, without case, then id.
NAME_INDEX = "idx_photos_name"
#: The index migration 23 makes: the photos by caption (caption_sql), without case, then id.
CAPTION_INDEX = "idx_photos_caption"
#: The characters of a caption an order by caption compares: two captions alike in their first CAPTION_KEY are in the
#: order of their ids. It bounds what a page token holds (the longest caption of photo_index is 616 characters).
CAPTION_KEY = 200


def name_sql(column="path"):
    """The file name of the path in `column`, in SQL: what follows its last separator ("/" in a row under a root, the
    machine's own in a native one). Built-in, deterministic functions only, so that it can be indexed (NAME_INDEX); a
    query names it with the same text, its column qualified as it likes, for SQLite to use the index."""
    return ("substr(%s, length(rtrim(%s, replace(replace(%s, '/', ''), char(92), ''))) + 1)"
            % (column, column, column))


def caption_sql(column="captions"):
    """The caption an order by caption reads of the captions JSON in `column`, in SQL: the first of the list (the one the
    page shows, vocabulary.extract_captions), its first CAPTION_KEY characters, NULL for a photo with none (an empty
    list, an empty text). Text that is not JSON is NULL too, never an error -- an index of an expression that raised would
    refuse the write of that row. Built-in, deterministic functions only, so that it can be indexed (CAPTION_INDEX), as
    name_sql is: nothing writes the index but SQLite and no writer of a caption has to know of it."""
    return ("NULLIF(substr(CASE WHEN json_valid(%s) THEN json_extract(%s, '$[0]') END, 1, %d), '')"
            % (column, column, CAPTION_KEY))


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
#: the `params` of the where, the (SQL, parameters) that count them, whether every one has a date
#: (a month does: the undated photos are not looked for), and whether the where is a RANGE of another index of photos
#: (a month, a year, a year's Other, a folder with its subfolders, a union of months and years): such a source is read
#: by that range and sorted, never by walking the name or caption index past every photo of the library (#722).
Scope = collections.namedtuple("Scope", "from_ where params count dated ranged", defaults=(False, False))


def _months_of(year):
    """(SQL, params) true of a photo whose `taken` is in one of the twelve months of `year` (as month_range reads one)."""
    clauses = [("(p.taken >= ? AND p.taken < ?)", month_range("%04d-%02d" % (year, month))) for month in range(1, 13)]
    return _any(clauses)


def _year_other(year):
    """(SQL, params) of the photos of `year` whose `taken` is no month of it: the year less its twelve month ranges, so a
    year is exactly its months and these, as the views read them."""
    months, params = _months_of(year)
    return "(p.year = ? AND (p.taken IS NULL OR NOT %s))" % months, (year,) + params


def _any(clauses):
    """(SQL, params) true when any of `clauses` -- [(SQL, params)] -- is: OR'd as a balanced tree, since SQLite refuses an
    expression nested 1,000 deep and a chain of ORs is nested as long as it is."""
    if len(clauses) == 1:
        return clauses[0]
    middle = len(clauses) // 2
    left, right = _any(clauses[:middle]), _any(clauses[middle:])
    return "(%s OR %s)" % (left[0], right[0]), tuple(left[1]) + tuple(right[1])


def _every(clauses):
    """(SQL, params) true when every one of `clauses` -- [(SQL, params)] -- is: AND'd as a balanced tree, as _any OR's them
    (a search of 1,000 sources ANDs as many)."""
    if len(clauses) == 1:
        return clauses[0]
    middle = len(clauses) // 2
    left, right = _every(clauses[:middle]), _every(clauses[middle:])
    return "(%s AND %s)" % (left[0], right[0]), tuple(left[1]) + tuple(right[1])


def _marks(items):
    return ",".join("?" * len(items))


def _words_clause(conn, words):
    """(SQL over `p`, params) of the photos holding every term of `words` (tagpup.store.search_index.clause: a set, never
    ranked), or None when no term says anything; NoWordIndex for a library without the index (before migration 24)."""
    if not search_index.present(conn):
        if schema.version(conn) < search_index.MIGRATION:
            raise WordIndexComing()
        raise NoWordIndex()
    return search_index.clause(words, gear=search_index.gear_present(conn))


class _Reads:
    """What resolving the members of one source reads once however many members name it (#751): the names photo_people
    holds, by vocabulary.key (one pass of its name index), the tag tree's nodes (read only when a keyword is not spelled
    exactly as a node is), and the folders' parent ids. One for each statement a source is compiled into, never kept."""

    def __init__(self, conn):
        self.conn = conn
        self._spelled = self._nodes = self._children = None

    def spellings(self, name):
        if self._spelled is None:
            self._spelled = collections.defaultdict(list)
            for (held,) in self.conn.execute("SELECT DISTINCT name FROM photo_people"):
                self._spelled[vocabulary.key(held)].append(held)
        return list(self._spelled.get(vocabulary.key(name), ()))

    def tag(self, tag):
        if self.conn.execute("SELECT 1 FROM tag_taxonomy WHERE tag = ?", (tag,)).fetchone():
            return tag
        if self._nodes is None:
            nodes = self.conn.execute("SELECT id, tag FROM tag_taxonomy").fetchall()
            self._nodes = (derived.Tree(nodes), dict(nodes))
        tree, tag_of = self._nodes
        found = tree.find(tag)
        return tag_of.get(found, tag) if found is not None else tag

    def children(self):
        if self._children is None:
            self._children = collections.defaultdict(list)
            for folder_id, parent_id in self.conn.execute("SELECT id, parent_id FROM folders"):
                self._children[parent_id].append(folder_id)
        return self._children


def _member_clause(conn, member, reads):
    """(SQL over `p`, params, the member's Scope) of the photos of one source of a search, or None when it holds none: a
    union's own clause, a folder alone by photo_folder, every other kind the where its own Scope reads it by."""
    if member.kind == FOLDER and not member.recursive:
        scope = _union_scope(conn, (member,), reads)
    else:
        scope = _scope(conn, member, reads)
    if scope is None:
        return None
    if scope.from_ != "photos p":
        raise ValueError("a search's member is read over photos p, not %r" % (scope.from_,))
    return "(%s)" % scope.where, tuple(scope.params), scope


def _search_scope(conn, search, reads):
    """The Scope of a search: ONE where-clause over `photos p`, the AND of its parts -- the union of `any_of` (_union_scope's
    clause), each member of `all_of` (its own clause: an AND of gathered INs would be "any"), NOT the union of `none_of`,
    and the words -- so a photo is one row and the count counts it once. None when it can hold nothing: `any_of` or a
    member of `all_of` holds nothing, or `none_of` holds every photo. No part at all is every photo.

    NOT is of the clause's truth, NULL taken as false: a photo with no date is in none of the months of `none_of`, so it is
    kept, where NOT of the bare comparison would be NULL and drop it.

    `ranged` is always set: whether the name or caption index is walked for it is decided by counting it (_walks), since
    an AND of a small list and the whole library is either a handful or most of it."""
    parts, dated = [], False
    if search.any_of:
        scope = _union_scope(conn, search.any_of, reads)
        if scope is None:
            return None
        parts.append(("(%s)" % scope.where, tuple(scope.params)))
        dated = dated or scope.dated
    for member in search.all_of:
        if member.kind == ALL:
            continue
        found = _member_clause(conn, member, reads)
        if found is None:
            return None
        parts.append(found[:2])
        dated = dated or found[2].dated
    if search.none_of:
        if any(member.kind == ALL for member in search.none_of):
            return None
        scope = _union_scope(conn, search.none_of, reads)
        if scope is not None:
            parts.append(("NOT IFNULL((%s), 0)" % scope.where, tuple(scope.params)))
    if search.words:
        words = _words_clause(conn, search.words)
        if words is not None:
            parts.append(words)
    where, params = _every(parts) if parts else ("1", ())
    return Scope("photos p", where, params, ("SELECT COUNT(*) FROM photos p WHERE " + where, params), dated, True)


def _union_scope(conn, members, reads=None):
    """The Scope of the union of `members`: ONE where-clause over `photos p`, so a photo in two of them is one row and the
    count counts it once. The members are gathered by kind -- the folders' ids (a folder with its subfolders is its own and
    every folder under it, by the tree's parent ids), the tag tree's node ids, the people's spellings, the years -- each
    gathering one IN (...), the months and the "Other" of years OR'd: a union of 400 people is one seek of the name index
    for each name, not 400 subqueries. None when no member holds anything.

    A folder with its subfolders is read through the derived folder tree and photo_folder, where the same source alone is a
    range of photos.path: the two agree while the derived tables are in step with the photos (the doctor checks them; #698)."""
    if any(member.kind == ALL for member in members):
        return _scope(conn, Source(ALL))
    reads = reads or _Reads(conn)
    folder_ids, tag_ids, names, years, clauses = set(), set(), set(), set(), []
    walked = set()   # the folders whose subfolders are in: apart from folder_ids, so a folder named alone first is still walked (#695)
    for member in members:
        kind = member.kind
        if kind == FOLDER:
            where, params = store_roots.sql_equals(conn, "path", member.value)
            row = conn.execute("SELECT id FROM folders WHERE " + where, params).fetchone()
            if row is None:
                continue
            folder_ids.add(row[0])
            if member.recursive:
                folders = reads.children()
                below = [row[0]]
                while below:
                    folder_id = below.pop()
                    if folder_id not in walked:
                        walked.add(folder_id)
                        folder_ids.add(folder_id)
                        below.extend(folders.get(folder_id, ()))
        elif kind in (KEYWORD, KEYWORD_ONLY):
            tag = reads.tag(member.value)
            sql, params = derived.under(tag) if kind == KEYWORD else ("SELECT id FROM tag_taxonomy WHERE tag = ?", (tag,))
            tag_ids.update(node_id for (node_id,) in conn.execute(sql, params))
        elif kind == PERSON:
            names.update(reads.spellings(member.value))
        elif kind == YEAR:
            years.add(int(member.value))
        elif kind == MONTH:
            clauses.append(("(p.taken >= ? AND p.taken < ?)", month_range(member.value)))
        elif kind == YEAR_OTHER:
            clauses.append(_year_other(int(member.value)))
        else:
            raise ValueError("no such kind of source in a union: %r" % (kind,))
    for table, column, held in (("photo_folder", "folder_id", folder_ids), ("photo_tags", "tag_id", tag_ids),
                                ("photo_people", "name", names)):
        if held:
            ordered = sorted(held)
            clauses.append(("p.id IN (SELECT photo_id FROM %s WHERE %s IN (%s))" % (table, column, _marks(ordered)),
                            tuple(ordered)))
    if years:
        ordered = sorted(years)
        clauses.append(("p.year IN (%s)" % _marks(ordered), tuple(ordered)))
    if not clauses:
        return None
    where, params = _any(clauses)
    dated = all(member.kind == MONTH for member in members)
    ranged = all(member.kind in RANGED for member in members)
    return Scope("photos p", where, params, ("SELECT COUNT(*) FROM photos p WHERE " + where, params), dated, ranged)


def _scope(conn, source, reads=None):
    """The Scope of `source`; None for a source that holds none whatever is asked (a folder the library has no
    row of, a person nobody is called)."""
    kind = source.kind
    if kind == ALL:
        return Scope("photos p", "1", (), ("SELECT COUNT(*) FROM photos", ()))
    reads = reads or _Reads(conn)
    if kind == ANY_OF:
        return _union_scope(conn, source.value, reads)
    if kind == SEARCH:
        return _search_scope(conn, source.value, reads)
    if kind == YEAR_OTHER:
        where, params = _year_other(int(source.value))
        return Scope("photos p", where, params, ("SELECT COUNT(*) FROM photos p WHERE " + where, params), ranged=True)
    if kind == KEYWORD_ONLY:
        tag = (reads.tag(source.value),)
        inner = "SELECT photo_id FROM photo_tags WHERE tag_id IN (SELECT id FROM tag_taxonomy WHERE tag = ?)"
        return Scope("photos p", "p.id IN (%s)" % inner, tag, ("SELECT COUNT(DISTINCT photo_id) FROM (%s)" % inner, tag))
    if kind == YEAR:
        year = (int(source.value),)
        return Scope("photos p", "p.year = ?", year, ("SELECT COUNT(*) FROM photos WHERE year = ?", year), ranged=True)
    if kind == MONTH:
        bounds = month_range(source.value)
        return Scope("photos p", "p.taken >= ? AND p.taken < ?", bounds,
                     ("SELECT COUNT(*) FROM photos WHERE taken >= ? AND taken < ?", bounds), dated=True, ranged=True)
    if kind == FOLDER:
        if source.recursive:
            where, params = store_roots.sql_under(conn, "p.path", source.value)
            return Scope("photos p", where, params, ("SELECT COUNT(*) FROM photos p WHERE " + where, params), ranged=True)
        where, params = store_roots.sql_equals(conn, "path", source.value)
        row = conn.execute("SELECT id FROM folders WHERE " + where, params).fetchone()
        if row is None:
            return None
        return Scope("photo_folder pf JOIN photos p ON p.id = pf.photo_id", "pf.folder_id = ?", (row[0],),
                     ("SELECT COUNT(*) FROM photo_folder WHERE folder_id = ?", (row[0],)))
    if kind == KEYWORD:
        sql, params = derived.under(reads.tag(source.value))
        # IN, not a join: a photo holding two tags under the keyword is one row, and the plan seeks
        # photo_tags's covering index once for each node and the photo by its primary key.
        return Scope("photos p", "p.id IN (SELECT photo_id FROM photo_tags WHERE tag_id IN (%s))" % sql, params,
                     ("SELECT COUNT(DISTINCT photo_id) FROM photo_tags WHERE tag_id IN (%s)" % sql, params))
    if kind == PERSON:
        names = reads.spellings(source.value)
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
    return _Reads(conn).tag(tag)


def spellings(conn, name):
    """The names `photo_people` holds that are `name` -- the same person without regard to case -- as it holds
    them: usually one. One pass of the name index (400 names on photo_index: 4 ms)."""
    return _Reads(conn).spellings(name)


#: The largest id a photo can have, and one more: the start of a descending read of ids.
_PAST_LAST_ID = 2 ** 62


#: The orders read by the index of an expression: {order: (the expression of `p`, the index, whether a photo may have
#: none)}. A photo with none comes after those that have one, by id in the order's direction, as an undated photo does.
_KEYED = {}
#: Those that have a key, in SQL of the key: a range of the index (a key is text and never empty: caption_sql), where `IS NOT
#: NULL` read every photo's row to compute it again -- 227 ms for the whole of photo_index, against 20.
_HAS_KEY = " AND %s COLLATE NOCASE >= ''"
for _orders, _key, _index, _nullable in (((NAME, NAME_DESC), name_sql("p.path"), NAME_INDEX, False),
                                         ((CAPTION, CAPTION_DESC), caption_sql("p.captions"), CAPTION_INDEX, True)):
    for _order in _orders:
        _KEYED[_order] = (_key, _index, _nullable)


def has_index(conn, index):
    """Has the library on `conn` the index `index` (NAME_INDEX, migration 22; CAPTION_INDEX, migration 23)? Without it an
    order by that key is a sort of the source, the same order; remembered on the connection once true."""
    held = getattr(conn, "view_indexes", None)
    if held is not None and index in held:
        return True
    found = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?", (index,)).fetchone() is not None
    if found:
        try:
            conn.view_indexes = (held or frozenset()) | {index}
        except AttributeError:
            pass   # a connection that is not db.connect's cannot remember
    return found


#: Read every source by the name or caption index, as before #722: what a measurement compares against.
FORCE_ALWAYS = False

#: A range of another index holding at most 1/RANGE_SHARE of the library is read by that range and sorted; a larger one is
#: read by walking the name or caption index (#722). Measured on a sandbox copy of photo_index (68,324 photos; a page of 200,
#: the first and the eleventh, and the whole id list; caption and name, either way), by the range against by the walk: a
#: month of 1,201 photos 2-11 ms a page and 7-9 its ids, against 1-117 and 44-156; a year's Other of 50, 4-10 ms against
#: 45-149 every time; a folder with its subfolders of 3,573, 8-40 ms a page and 16-22 its ids, against 2-34 and 47-157. A
#: range of much of the library is walked: the top folder by its path range was 114-949 ms a page, walked 0.2-1.8 (7-9 with
#: the count that decides it), and a year of 5,579 (8 %) 8-97 ms against 0.2-33. 1/16 is 4,270 photos of photo_index.
RANGE_SHARE = 16


def _walks(conn, scope, index):
    """Is `scope` read in the order of `index` by walking the index (`INDEXED BY`)? The whole library and a source given by a
    list of ids -- a keyword, a person, a folder alone, a union holding one of those -- are: left to itself SQLite reads the
    list and sorts it. A range of another index (Scope.ranged: a month, a year, a year's Other, a folder with its
    subfolders, a union of months and years) is left to SQLite, which reads the range and sorts it, unless it holds more
    than 1/RANGE_SHARE of the library: forced, a small range walked every photo of the library for each page (#722)."""
    if not has_index(conn, index):
        return False
    if FORCE_ALWAYS or not scope.ranged:
        return True
    held = conn.execute(*scope.count).fetchone()[0]
    return held * RANGE_SHARE > conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]


def _by_index(conn, scope, index):
    """(FROM, WHERE, params) that read the photos of `scope` in the order of `index`: walking the index where `_walks` says
    so, else as SQLite chooses. A source joined to another table is asked as a subquery."""
    where = scope.where if scope.from_ == "photos p" else "p.id IN (SELECT p.id FROM %s WHERE %s)" % (scope.from_, scope.where)
    from_ = "photos p INDEXED BY %s" % index if _walks(conn, scope, index) else "photos p"
    return from_, "(%s)" % where, scope.params


def _keyed_page(conn, scope, cursor, limit, order):
    """A page in an order of _KEYED: those with a key by (key without case, id), then -- for a key a photo may lack --
    those with none by id, each phase one statement, as _page reads the dated and the undated."""
    key, index, nullable = _KEYED[order]
    from_, where, params = _by_index(conn, scope, index)
    way, beyond = (" DESC", "<") if order in DESCENDING else ("", ">")
    want = limit + 1
    rows = []
    if cursor is None or cursor.phase == 0:
        sql = "SELECT %s, p.id FROM %s WHERE %s" % (key, from_, where)
        values = list(params)
        if nullable and cursor is None:
            # After a cursor its bound is the range, and NULL is neither above nor below it: one lower bound for the index to
            # seek, not two of which it might take the first.
            sql += _HAS_KEY % key
        if cursor is not None:
            # The bound on the key alone is what the index seeks; the pair with the id is the keyset.
            sql += " AND %s COLLATE NOCASE %s= ? AND (%s COLLATE NOCASE, p.id) %s (?, ?)" % (key, beyond, key, beyond)
            values += [cursor.taken, cursor.taken, cursor.id]
        sql += " ORDER BY %s COLLATE NOCASE%s, p.id%s LIMIT ?" % (key, way, way)
        rows = [(photo_id, value) for value, photo_id in conn.execute(sql, values + [want])]
    if nullable and len(rows) < want:
        start = _PAST_LAST_ID if order in DESCENDING else 0
        after = cursor.id if cursor is not None and cursor.phase == 1 else start
        rows += [(photo_id, None) for (photo_id,) in conn.execute(
            "SELECT p.id FROM %s WHERE %s AND %s IS NULL AND p.id %s ? ORDER BY p.id%s LIMIT ?" % (from_, where, key, beyond, way),
            list(params) + [after, want - len(rows)])]
    return rows[:limit], len(rows) > limit


def _page(conn, scope, cursor, limit, order=TAKEN):
    if order in _KEYED:
        return _keyed_page(conn, scope, cursor, limit, order)
    desc = order == TAKEN_DESC
    way, beyond = (" DESC", "<") if desc else ("", ">")
    want = limit + 1   # one more than the page says whether another page follows
    rows = []
    if cursor is None or cursor.phase == 0:
        sql = "SELECT p.taken, p.id FROM %s WHERE (%s) AND p.taken IS NOT NULL" % (scope.from_, scope.where)
        values = list(scope.params)
        if cursor is not None:
            sql += " AND (p.taken, p.id) %s (?, ?)" % beyond
            values += [cursor.taken, cursor.id]
        sql += " ORDER BY p.taken%s, p.id%s LIMIT ?" % (way, way)
        rows = [(photo_id, taken) for taken, photo_id in conn.execute(sql, values + [want])]
    if len(rows) < want and not scope.dated:
        # The undated photos come after the dated ones in either direction, by id in the order's.
        start = _PAST_LAST_ID if desc else 0
        after = cursor.id if cursor is not None and cursor.phase == 1 else start
        undated = conn.execute(
            "SELECT p.id FROM %s WHERE (%s) AND p.taken IS NULL AND p.id %s ? ORDER BY p.id%s LIMIT ?"
            % (scope.from_, scope.where, beyond, way), list(scope.params) + [after, want - len(rows)]).fetchall()
        rows += [(photo_id, None) for (photo_id,) in undated]
    return rows[:limit], len(rows) > limit


def all_ids(conn, source, cap, order=TAKEN):
    """([photo id] of the whole source in the order `page` gives, how many photos the source holds): at most `cap`
    ids, the total counted when the source holds more. Two statements at most, one per phase, each an index-ordered
    read of ids alone -- the keyset page's order without the keyset. By name, one: the name index walked in order. By
    caption, the caption index walked in order, the captioned and then those with none."""
    scope = _scope(conn, source)
    if scope is None:
        return [], 0
    if order in _KEYED:
        key, index, nullable = _KEYED[order]
        from_, where, params = _by_index(conn, scope, index)
        way = " DESC" if order in DESCENDING else ""
        ids = [photo_id for (photo_id,) in conn.execute(
            "SELECT p.id FROM %s WHERE %s%s ORDER BY %s COLLATE NOCASE%s, p.id%s LIMIT ?"
            % (from_, where, _HAS_KEY % key if nullable else "", key, way, way), list(params) + [cap + 1])]
        if nullable and len(ids) <= cap:
            ids += [photo_id for (photo_id,) in conn.execute(
                "SELECT p.id FROM %s WHERE %s AND %s IS NULL ORDER BY p.id%s LIMIT ?" % (from_, where, key, way),
                list(params) + [cap + 1 - len(ids)])]
    else:
        way = " DESC" if order == TAKEN_DESC else ""
        ids = [photo_id for (photo_id,) in conn.execute(
            "SELECT p.id FROM %s WHERE (%s) AND p.taken IS NOT NULL ORDER BY p.taken%s, p.id%s LIMIT ?"
            % (scope.from_, scope.where, way, way), list(scope.params) + [cap + 1])]
        if len(ids) <= cap and not scope.dated:
            ids += [photo_id for (photo_id,) in conn.execute(
                "SELECT p.id FROM %s WHERE (%s) AND p.taken IS NULL ORDER BY p.id%s LIMIT ?" % (scope.from_, scope.where, way),
                list(scope.params) + [cap + 1 - len(ids)])]
    if len(ids) <= cap:
        return ids, len(ids)
    return ids[:cap], conn.execute(*scope.count).fetchone()[0]


def source_ids(conn, source, cap, order=TAKEN):
    """all_ids, read in ONE transaction: the dated photos and the undated are two statements, and a photo dated
    between them (a bulk time shift of the library is under way, a sync) would be listed twice or not at all by two
    snapshots. The transaction is the connection's own and read-only; it ends with the connection."""
    db.begin(conn)
    return all_ids(conn, source, cap, order)


def existing_ids(conn, photo_ids):
    """The ids of `photo_ids` that have a photo, as a set: one primary-key read in batches of CHUNK."""
    found = set()
    ids = list(photo_ids)
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start:start + CHUNK]
        found.update(photo_id for (photo_id,) in conn.execute(
            "SELECT id FROM photos WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk))
    return found


def paths_of(conn, photo_ids):
    """{photo id: path -- native --} of the photos `photo_ids` that have a row: one read in batches of CHUNK of ids
    and paths alone (a bulk edit resolves each chunk of its photos as it reaches them). Raises paths.RootsError for a
    root this machine does not place."""
    found = {}
    ids = list(photo_ids)
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start:start + CHUNK]
        rows = conn.execute("SELECT id, path FROM photos WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk).fetchall()
        for photo_id, path in store_roots.natives(conn, rows, 1):
            found[photo_id] = path
    return found


def id_plans(conn, source, cap, order=TAKEN):
    """([(statement, [plan lines])] of every SELECT `all_ids` runs for `source`, the milliseconds it took): what the
    measurement script prints and a person reads to see that a source's id list searches an index, as SQLite plans it."""
    seen = []
    conn.set_trace_callback(seen.append)
    started = time.perf_counter()
    try:
        all_ids(conn, source, cap, order)
    finally:
        conn.set_trace_callback(None)
    elapsed = (time.perf_counter() - started) * 1000
    return [(sql, [row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + sql)])
            for sql in seen if sql.lstrip().upper().startswith("SELECT")], elapsed


def total(conn, source):
    """How many photos `source` holds."""
    scope = _scope(conn, source)
    return 0 if scope is None else conn.execute(*scope.count).fetchone()[0]


def page(conn, source, cursor=None, limit=100, order=TAKEN):
    """([(photo id, taken)] of the page after `cursor`, whether there are more after it): `limit` photos of
    `source` in order, those with a date first by (taken, id), then those with none by id (in `order`; by name the
    second of each pair is the file name, by caption the caption's key or None). One statement for each phase it
    reaches; `next_cursor` of the last photo continues."""
    scope = _scope(conn, source)
    return ([], False) if scope is None else _page(conn, scope, cursor, limit, order)


def view(conn, source, cursor=None, limit=100, order=TAKEN):
    """(page rows, whether more follow, the total): `page` and `total` with the source resolved once."""
    scope = _scope(conn, source)
    if scope is None:
        return [], False, 0
    rows, more = _page(conn, scope, cursor, limit, order)
    return rows, more, conn.execute(*scope.count).fetchone()[0]


def next_cursor(row):
    """The cursor that continues after `row`, (photo id, taken -- or name, by name; caption, by caption)."""
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


def photo_id_of(conn, photo_path):
    """The id of the photo whose row is at `photo_path` (any spelling the filesystem treats as one: paths.sql_equals), or
    None: one seek of the path index."""
    where, params = store_roots.sql_equals(conn, "path", photo_path)
    row = conn.execute("SELECT id FROM photos WHERE " + where + " LIMIT 1", params).fetchone()
    return row[0] if row else None


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


# ---- A selection's tally ---------------------------------------------------------------------

def selected_sql(conn, ids, source, excluded):
    """(SQL that selects the ids of the photos selected -- `SELECT p.id ...` --, its parameters): the photos `ids` that have
    a row, or those of `source` but the `excluded` ones. The ids are put in a TEMP table of this connection (`sel`, by primary
    key; a read-only connection may make one), so a selection of 20,000 is one statement and not forty."""
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS sel (id INTEGER PRIMARY KEY)")
    conn.execute("DELETE FROM sel")
    if source is None:
        conn.executemany("INSERT OR IGNORE INTO sel (id) VALUES (?)", ((photo_id,) for photo_id in ids))
        # CROSS JOIN fixes the order: the ids first, each photo by its key. Left to itself the planner scans the
        # library's index and looks each photo up in `sel`, which for one photo is 68,000 steps.
        return "SELECT p.id FROM sel CROSS JOIN photos p ON p.id = sel.id", ()
    scope = _scope(conn, source)
    if scope is None:
        return None, ()
    conn.executemany("INSERT OR IGNORE INTO sel (id) VALUES (?)", ((photo_id,) for photo_id in excluded))
    return ("SELECT p.id FROM %s WHERE %s AND p.id NOT IN (SELECT id FROM sel)" % (scope.from_, scope.where),
            tuple(scope.params))


def tally(conn, ids=None, source=None, excluded=()):
    """({"total": photos selected, "tags": [(tag, photos)], "people": [(name, photos)]}) of a selection: `ids`, or every photo
    of `source` but `excluded`. From photo_tags (by tag-tree node: the tag the tree spells, exactly -- not the tags under it --
    so a keyword no node holds is not counted, as the navigator's counts) and photo_people, each a single grouped statement over
    the selection; names that are one person without regard to case are one entry under the spelling most photos hold
    (people_counts). A tag that is a person's node (person_ids: a leaf of the tree that holds faces) is a person, listed under
    "people" and not here: left out BEFORE the service cuts the list, so the cut spends its slots on keywords (#865). "nameless" is
    the names among "people" that are no person node's by the tree's rule -- a branch, two nodes, none (#866): not people to
    open a view of. Unsorted: the service orders and cuts them. One read transaction."""
    db.begin(conn)
    selected, params = selected_sql(conn, ids, source, excluded)
    if selected is None:
        return {"total": 0, "tags": [], "people": [], "nameless": []}
    total = conn.execute("SELECT COUNT(*) FROM (%s)" % selected, params).fetchone()[0]
    known = person_ids.read(conn)
    tags = [(tag, count) for tag_id, tag, count in conn.execute(
        "SELECT t.id, t.tag, COUNT(*) FROM photo_tags pt JOIN tag_taxonomy t ON t.id = pt.tag_id"
        " WHERE pt.photo_id IN (%s) GROUP BY pt.tag_id" % selected, params) if tag_id not in known.nodes]
    held = conn.execute("SELECT name, COUNT(DISTINCT photo_id) FROM photo_people WHERE photo_id IN (%s) GROUP BY name"
                        % selected, params).fetchall()
    grouped = collections.defaultdict(list)
    for name, count in held:
        grouped[vocabulary.key(name)].append((name, count))
    people = []
    for spellings_of in grouped.values():
        spellings_of.sort(key=lambda each: (-each[1], each[0]))
        if len(spellings_of) == 1:
            people.append(spellings_of[0])
            continue
        names = [each[0] for each in spellings_of]
        people.append((names[0], conn.execute(
            "SELECT COUNT(DISTINCT photo_id) FROM photo_people WHERE name IN (%s) AND photo_id IN (%s)"
            % (",".join("?" * len(names)), selected), names + list(params)).fetchone()[0]))
    return {"total": total, "tags": tags, "people": people,
            "nameless": [name for name, _count in people if known.id_of(name) is None]}


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
    # Photos for each distinct set of tags, counted by SQLite (the table is read in photo order, so a photo's ids
    # come out in one order): most photos share theirs, and Python then sees thousands of sets, not 68,000 photos.
    sets = conn.execute("SELECT tag_ids, COUNT(*) FROM (SELECT group_concat(tag_id) AS tag_ids FROM photo_tags"
                        " GROUP BY photo_id) GROUP BY tag_ids").fetchall()
    for tag_ids, photos in sets:
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
    return sorted(found, key=lambda each: (-each[1], vocabulary.tag_sort_key(each[0])))


def people_groups(conn, counted):
    """The branches of the tag tree the people of `counted` -- people_counts's [(name, photos)] -- are filed under, as the
    navigator's People shows them (phase 9, #673): ({name: the tag of the node above the person, or None}, [{"id", "tag",
    "name", "parent_id", "count"}] of every branch above a person, `count` the photos naming anyone under it, each photo
    once), and how many photos name someone who is not filed (None above).

    A person is the tree's node by the one rule (tagpup.store.person_ids: a leaf `has_face` node, not a root, the only one
    called that name; a branch is never a person). A name with no such node -- none, only a branch, or two -- is not filed.
    The photos are one pass of photo_people grouped by photo in SQLite (Python sees the distinct sets of names, not 68,000
    photos) and rolled up the tree's parent ids, as the keywords' counts are."""
    known = person_ids.read(conn)
    nodes = {node_id: (tag, parent_id, name) for node_id, tag, parent_id, name in
             conn.execute("SELECT id, tag, parent_id, name FROM tag_taxonomy")}
    group_of, by_key = {}, {}
    for name, _photos in counted:
        node = known.id_of(name)
        parent = nodes[node][1] if node in nodes else None
        group_of[name] = nodes[parent][0] if parent in nodes else None
        by_key[vocabulary.key(name)] = parent if parent in nodes else None
    upward = {}   # each group and every node above it: a damaged tree that loops ends the walk, never the call
    for parent in set(by_key.values()) - {None}:
        walked, seen, here = [], set(), parent
        while here is not None and here in nodes and here not in seen:
            seen.add(here)
            walked.append(here)
            here = nodes[here][1]
        upward[parent] = walked
    counts, unfiled = collections.Counter(), 0
    separator = chr(31)   # a name never holds a control character (vocabulary.textProblem refuses them)
    for names, photos in conn.execute(
            "SELECT names, COUNT(*) FROM (SELECT group_concat(name, char(31)) AS names FROM photo_people GROUP BY photo_id)"
            " GROUP BY names"):
        reached, loose = set(), False
        for name in names.split(separator):
            parent = by_key.get(vocabulary.key(name))
            if parent is None:
                loose = True
            else:
                reached.update(upward[parent])
        for node_id in reached:
            counts[node_id] += photos
        unfiled += photos if loose else 0
    groups = [{"id": node_id, "tag": nodes[node_id][0], "name": nodes[node_id][2], "parent_id": nodes[node_id][1],
               "count": counts[node_id]} for node_id in sorted(set().union(*upward.values()) if upward else ())]
    return group_of, groups, unfiled


#: The first year a photo could be dated in: before it is a number from a file name or a scan's clock (photo_index
#: holds 1827, 1843, 1874 and 1888). The last is next year (a camera's clock a little ahead). The one place the rule
#: is, and the Dates navigator groups what falls outside it under "Other years" (docs/findings.md, #510).
FIRST_PLAUSIBLE_YEAR = 1900


def plausible_year(year, this_year=None):
    """Could a photo have been taken in `year`: FIRST_PLAUSIBLE_YEAR to next year."""
    this_year = time.localtime().tm_year if this_year is None else this_year
    return FIRST_PLAUSIBLE_YEAR <= year <= this_year + 1


def date_counts(conn, this_year=None):
    """{"years": [{"year", "count", "months": [{"month": "YYYY-MM", "count"}], "other", "implausible"}], "undated": n}:
    the photos of each year (`photos.year`, the year taken or else the one in its name), and of each month of it by
    `taken`; `other` is those of the year whose `taken` is no month of it (a date written with dashes, or
    none); `implausible` is a year before FIRST_PLAUSIBLE_YEAR or after next year (plausible_year). One statement, a
    scan of idx_photos_year, grouped. Years are in order, months in order."""
    years = {}
    undated = 0
    for year, month, count in conn.execute(
            "SELECT year, substr(taken, 1, 7), COUNT(*) FROM photos GROUP BY year, substr(taken, 1, 7)"):
        if year is None:
            undated += count
            continue
        entry = years.setdefault(year, {"year": year, "count": 0, "months": [], "other": 0,
                                        "implausible": not plausible_year(year, this_year)})
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
