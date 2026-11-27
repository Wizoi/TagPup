"""The library as the views see it: which photos a source holds, the cards of a list of ids, and the navigator's counts
(docs/ARCHITECTURE.md, phase 9a; the SQL is tagpup.store.library_view).

A SOURCE says which photos: `all`; a `folder` (by its path, never an id) and, when asked, its subfolders; a
`keyword` -- the tag as the tree spells it -- and everything under it, or `keyword_only`, its node alone; a `person`
(the leaf name, as photo_people holds it: identity by id comes later); a `year`; `year_other`, the photos of a year
whose date names no month of it; a `month` ("2024-06"); `untagged`, the photos with no keyword (no value, as
`all`); `any_of`, a list of those (at most MAX_MEMBERS): the
union of the rows selected in the navigator (phase 9, #672); or a `search` (phase 9e): {"all_of", "any_of", "none_of",
"words"} (search_of). `ids` answers the whole ORDERED list of a source's photo ids with the
TOTAL; `cards` turns any list of ids into cards.

* **Order**: by Date Taken (`photos.taken`, ExifTool's text, which sorts as time does) and then id; photos with
  no date after them, by id -- or, asked (`order`, store.ORDERS), newest first (the undated still after the dated,
  by id from the highest), by file name either way (ties by id), by caption either way (ties by id; photos with no
  caption after the captioned, by id in the order's direction, as the undated are), or by file size either way (ties by
  id; a size never read after the rest). Never by file time. The page
  holds the list and asks for the cards of the ids near its window.
* **A card** is small: id, file name, native path, taken, whether the photo is recorded damaged, and the
  thumbnail's URL by id (tagpup.services.thumbnails). One batch read of the columns it needs: no
  raw_metadata, no BLOB, no query for each photo. `cards` looks at no disk unless it is asked to; it may (`check_disk`, which the
  page's route asks): one stat per card, bounded for a network share, adds `stale` -- "changed" when the file's
  size and time no longer describe the row, "missing" when the file is gone -- and nothing to a card whose file
  is as the row says, is on a share that did not answer, cannot be read, or whose row was never stamped.
* **The navigator**: `navigator(library, section)` for `folders`, `keywords`, `people` or `dates`, each one query
  (or one read of a derived table) and a pass in memory -- the keywords' with the count of the photos with no keyword
  beside them, in the same snapshot; native paths, tag paths and names out, never ids. The folders also name the
  folders the library's index queue holds, marked, a folder just added among them before it holds a photo (_folders).
* **A browse row's cover**: `covers(library, section, rows)`, the first photo of each row's own source, as `ids` orders it,
  and its thumbnail's URL: a handful of rows near the phone's screen at a time, ids alone in one transaction.
* **What fails, and how it says so**: a root this machine does not place raises paths.RootsError, whose
  sentence the web layer's gate answers first; a request that cannot mean anything is `Refused`, with why. Reads
  only, on a read-only connection: nothing here migrates or writes a library.
"""
import json
import os
import re
import time

from tagpup.core import paths, vocabulary
from tagpup.core.result import NotFound, Refused
from tagpup.files import shares
from tagpup.services import thumbnails
from tagpup.services import photos as photo_actions
from tagpup.services import roots as roots_service
from tagpup.store import db, person_ids
from tagpup.store import library_view as store
from tagpup.store import photos as store_photos
from tagpup.store import roots as store_roots
from tagpup.store import search_index

KINDS = store.KINDS
SECTIONS = ("folders", "keywords", "people", "dates")

#: How long a batch of cards may spend looking at its files, in seconds: a share that answers every stat slowly (0.3 to 0.9 s)
#: never trips "away", and 200 of them in a row would hold the request for minutes. When the budget is spent the rest of the
#: batch is left unmarked, quietly (findings #570).
STAT_BUDGET_SECONDS = 1.5

#: What a card says of its file when the disk was looked at (`stale`).
CHANGED = "changed"
MISSING = "missing"

#: The most ids `ids` answers for one source, and the most cards `cards_of` answers for one ask.
MAX_IDS = 200_000
MAX_CARDS = 200

#: The most sources a union holds: the navigator's rows a person selected, compressed (a folder with every folder under it
#: is one). A union of more is refused with a sentence; its address would be longer than the server reads.
MAX_MEMBERS = 1000

#: The most browse rows one ask for covers names: the phone's Browse asks for the rows near its screen, 24 at a time
#: (docs/plans/phone-redesign.md).
MAX_COVERS = 24

ORDERS = store.ORDERS

_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_YEAR = re.compile(r"^\d{1,4}$")


def _open(library):
    """A read-only connection to a library."""
    if not os.path.exists(library.path):
        raise NotFound("There is no library at %s." % library.path)
    return db.connect(db.readonly_uri(library.path), uri=True)


def opened(library):
    """A read-only connection to a library, for the services that read beside this one (tagpup.services.selection)."""
    return _open(library)


# ---- The request's words, read ------------------------------------------------------------------

def source_of(library, kind, value=None, recursive=False, media=None):
    """The store's Source for a request's words, or Refused with why it means nothing; `media` (media_of) the one kind of
    its photos it holds, set on this source alone, never on a union's or a search's members. A folder is
    spelled by its first place when the library has roots (paths.canonical: the web layer's ingress does it
    for the request, this for a caller that is not one) and as the filesystem spells it (paths.stored).

    A union (`any_of`) is a list of sources, each {"kind", "value", "recursive"} -- as JSON text in a request's
    query, as a list in a body -- none of them a union, at least one and at most MAX_MEMBERS; the same source named
    twice is one. A union of one source is that source."""
    only = media_of(media)
    return _source_of(kind, value, recursive, _Canonical(library), member=False)._replace(only=only)


#: What a request's `media` may say: photos alone, videos alone; none (or "") is both.
MEDIA = (store.PHOTO, store.VIDEO)


def media_of(text):
    """The kind of photo a request's `media` keeps (store.PHOTO, store.VIDEO), None for both -- no `media`, or "" --, or
    Refused."""
    if text is None or text == "":
        return None
    if text not in MEDIA:
        raise Refused("media must be one of %s, or none for both." % ", ".join(MEDIA))
    return text


class _Canonical:
    """The library's canonicaliser, asked of its roots once for a request however many folders it names."""

    def __init__(self, library):
        self.library, self.made, self.found = library, False, None

    def __call__(self, folder):
        if not self.made:
            self.found, self.made = roots_service.canonicaliser(self.library), True
        return self.found(folder) if self.found else folder


def _source_of(kind, value, recursive, canonical, member):
    if kind not in KINDS:
        raise Refused("kind must be one of %s." % ", ".join(KINDS))
    if kind in (store.ALL, store.UNTAGGED):
        return store.Source(kind)
    if kind == store.ANY_OF:
        if member:
            raise Refused("A union cannot hold a union.")
        return _union_of(value, canonical)
    if kind == store.SEARCH:
        if member:
            raise Refused("A search cannot hold a search.")
        return _search_of(value, canonical)
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value).strip():
        raise Refused("A %s source needs a value." % kind)
    value = str(value)
    if kind == store.FOLDER:
        return store.Source(store.FOLDER, canonical(paths.stored(value.strip())), bool(recursive))
    if kind in (store.KEYWORD, store.KEYWORD_ONLY):
        tag = vocabulary.normalize(value)
        if not tag:
            raise Refused("A keyword source needs a tag.")
        return store.Source(kind, tag)
    if kind == store.PERSON:
        return store.Source(store.PERSON, value.strip())
    if kind in (store.YEAR, store.YEAR_OTHER):
        if not _YEAR.match(value.strip()):
            raise Refused("A year is up to four digits, as 2024.")
        return store.Source(kind, int(value.strip()))
    if not _MONTH.match(value.strip()):
        raise Refused("A month is written YYYY-MM, as 2024-06.")
    return store.Source(store.MONTH, value.strip())


def _union_of(value, canonical):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise Refused("A union's value is a list of sources, as [{\"kind\": \"month\", \"value\": \"2024-07\"}].") from None
    if not isinstance(value, list) or not value:
        raise Refused("A union's value is a list of sources, at least one.")
    if len(value) > MAX_MEMBERS:
        raise Refused("That selects %d rows of the navigator; at most %d can be shown at once. Select fewer, or a "
                      "row above them." % (len(value), MAX_MEMBERS))
    members = []
    for each in value:
        if not isinstance(each, dict):
            raise Refused("Each source of a union is {\"kind\": ..., \"value\": ..., \"recursive\": ...}.")
        members.append(_source_of(each.get("kind"), each.get("value"), each.get("recursive"), canonical, member=True))
    members = list(dict.fromkeys(members))
    if len(members) == 1:
        return members[0]
    return store.Source(store.ANY_OF, tuple(members))


#: The parts a search's value may hold; anything else is refused (a misspelt "none_of" would otherwise be a search for more).
SEARCH_PARTS = ("all_of", "any_of", "none_of", "words")

#: The longest text of words a search reads.
MAX_WORDS = 500


def _search_of(value, canonical):
    """A search's Source, from its value -- {"all_of": [source], "any_of": [source], "none_of": [source], "words": text},
    each part optional; as JSON text in a request's query, an object in a body -- or Refused with why it means nothing.

    Each list holds sources of the kinds a union holds, at most MAX_MEMBERS. A union (`any_of`) in `any_of` or `none_of` is
    its sources (OR of an OR); in `all_of` it is one member, its photos ANDed with the rest: the navigator's selection,
    "within" which the search looks. A source named twice in a list is once. Words are text, at most MAX_WORDS
    characters, their blanks made single.

    A search that says nothing more than a source is that source, as a union of one is: no part at all is the whole
    library; `any_of` alone its union; one member of `all_of` alone that member."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise Refused("A search's value is {\"all_of\": [...], \"any_of\": [...], \"none_of\": [...], "
                          "\"words\": \"...\"}.") from None
    if not isinstance(value, dict):
        raise Refused("A search's value is {\"all_of\": [...], \"any_of\": [...], \"none_of\": [...], \"words\": \"...\"}.")
    unknown = sorted(str(key) for key in value if key not in SEARCH_PARTS)
    if unknown:
        raise Refused("A search has no part %s: its parts are %s." % (", ".join(unknown), ", ".join(SEARCH_PARTS)))
    words = value.get("words")
    if words is None:
        words = ""
    if not isinstance(words, str):
        raise Refused("A search's words are text.")
    if len(words) > MAX_WORDS:
        raise Refused("A search's words are at most %d characters." % MAX_WORDS)
    words = " ".join(search_index.terms(words))   # the terms that say something: Refused for too many
    lists = {}
    for part in ("all_of", "any_of", "none_of"):
        named = value.get(part)
        if named is None:
            named = []
        if not isinstance(named, list):
            raise Refused("A search's %s is a list of sources." % part)
        if len(named) > MAX_MEMBERS:
            raise Refused("A search's %s names %d sources; at most %d can be asked at once." % (part, len(named), MAX_MEMBERS))
        found = []
        for each in named:
            if not isinstance(each, dict):
                raise Refused("Each source of a search is {\"kind\": ..., \"value\": ..., \"recursive\": ...}.")
            kind = each.get("kind")
            if kind == store.SEARCH:
                raise Refused("A search cannot hold a search.")
            if kind == store.ANY_OF:
                union = _union_of(each.get("value"), canonical)
                if part != "all_of":
                    found += list(union.value) if union.kind == store.ANY_OF else [union]
                    continue
                found.append(union)
                continue
            found.append(_source_of(kind, each.get("value"), each.get("recursive"), canonical, member=True))
        found = list(dict.fromkeys(found))
        if len(found) > MAX_MEMBERS:
            raise Refused("A search's %s holds %d sources; at most %d can be asked at once." % (part, len(found), MAX_MEMBERS))
        lists[part] = tuple(found)
    all_of, any_of, none_of = lists["all_of"], lists["any_of"], lists["none_of"]
    if not words and not none_of:
        if not all_of:
            if not any_of:
                return store.Source(store.ALL)
            return any_of[0] if len(any_of) == 1 else store.Source(store.ANY_OF, any_of)
        if not any_of and len(all_of) == 1:
            return all_of[0]
    return store.Source(store.SEARCH, store.Search(all_of, any_of, none_of, words))


def described(source):
    """A Source as a reply names it: {"kind", "value", "recursive"}, a union's value the list of its sources so named, a
    search's {"all_of", "any_of", "none_of", "words"} with its lists so named."""
    if source.kind == store.ANY_OF:
        return {"kind": source.kind, "value": [described(member) for member in source.value], "recursive": False}
    if source.kind == store.SEARCH:
        search = source.value
        return {"kind": source.kind, "value": {"all_of": [described(member) for member in search.all_of],
                                               "any_of": [described(member) for member in search.any_of],
                                               "none_of": [described(member) for member in search.none_of],
                                               "words": search.words}, "recursive": False}
    return {"kind": source.kind, "value": source.value, "recursive": source.recursive}


def order_of(text):
    """The order a request's `order` names (store.ORDERS; none: by Date Taken, oldest first), or Refused."""
    if text is None or text == "":
        return store.TAKEN
    if text not in store.ORDERS:
        raise Refused("order must be one of %s." % ", ".join(store.ORDERS))
    return text


# ---- The ids ---------------------------------------------------------------------------------------

def ids(library, kind, value=None, recursive=False, cap=None, order=None, media=None):
    """{"source", "order", "media", "total", "ids", "complete", "videos", "of"}: the whole ordered id list of a source, of its
    photos of the one kind `media` names (media_of; None: both), for a page that
    jumps to the middle of it. At most `cap` ids (MAX_IDS); `total` is the source's, `complete` false
    when the list was cut. `videos` the ids of the source's clips, by id (store.view_ids): the view's count by kind is
    their number and `total` less it, the page's to show, never to count (#2569). `of` is how many photos the source holds of
    either kind, `total` when `media` is none (store.view_ids). One transaction of ids alone: no card, no BLOB."""
    source = source_of(library, kind, value, recursive, media)
    order = order_of(order)
    conn = _open(library)
    try:
        found, total, videos, of = store.view_ids(conn, source, MAX_IDS if cap is None else cap, order)
    finally:
        conn.close()
    return {"source": described(source), "order": order, "media": source.only, "total": total, "ids": found,
            "complete": len(found) == total, "videos": videos, "of": of}


def read_ids(text):
    """The photo ids a request's `ids=1,2,3` says, each once, in order. Refused for more than MAX_CARDS, for anything
    that is not a whole number, or for one no photo id could be."""
    if text is None or not text.strip():
        raise Refused("ids needs photo ids, as ids=1,2,3.")
    found = []
    for part in text.split(","):
        part = part.strip()
        if not part.isascii() or not part.isdigit() or len(part) > 18:
            raise Refused("ids must be whole numbers separated by commas, as ids=1,2,3.")
        if int(part) not in found:
            found.append(int(part))
    if len(found) > MAX_CARDS:
        raise Refused("Ask for at most %d photos at a time." % MAX_CARDS)
    return found


def photo(library, photo_id, fields):
    """The photo `photo_id` as the page reads a photo of a folder (services.photos.page_record): what the details panel
    shows and edits by path. NotFound when the library has no such photo.

    Built as the folder scan builds one: the file's stamp is taken and set against the row's, and the row's tags,
    people and captions are the record only where it describes the file. A file that differs, or a row with no
    stamp (made for a photo nobody read), is READ with ExifTool; the panel sends the whole tag list on a save, and a
    list built from a row that says less than the file would write over what the file holds. The record's mtime and
    size are the FILE's, which a write then names (tagpup.services.tagging.save_photo). A file that is gone is the
    row's record with `missing` set."""
    conn = _open(library)
    try:
        row = store.photo_row(conn, photo_id) if 0 < photo_id < 2 ** 62 else None
    finally:
        conn.close()
    if row is None:
        raise NotFound("There is no photo %d in this library." % photo_id)
    path, mtime, size, tags, people, captions, raw, year, kind = row
    stamp = photo_actions.file_stamp(path)
    if stamp is not None and not store_photos.describes(mtime, size, stamp):
        record = photo_actions.read_file(library, path, fields, stamp)
    else:
        record = photo_actions.page_record(path, {
            "tags": json.loads(tags) if tags else [], "people": json.loads(people) if people else [],
            "captions": json.loads(captions) if captions else [], "raw_metadata": json.loads(raw) if raw else {},
            "year": year}, mtime if stamp is None else stamp[0], size if stamp is None else stamp[1])
        if stamp is None:
            record["missing"] = True
    record["id"] = photo_id
    record["media"] = kind
    return record


def disk_mark(path, mtime, size):
    """What the disk says of the file a row describes: MISSING when it is not there, CHANGED when its size and time are
    other than the row's (the folder scan's own test, store.photos.describes), else None. None too when the file
    cannot be read, when its share did not answer in time (a share found away is answered at once, so a page of cards
    on it waits once: shares.stamp_of), and when the row was never stamped -- Suggest's row for a photo nobody
    read holds no size or time to compare, and 'changed' would claim a difference nothing knows of."""
    stamp = shares.stamp_of(path)
    if stamp is None:
        return MISSING
    if stamp is shares.CANNOT_READ or stamp is shares.UNANSWERED:
        return None
    if mtime is None or size is None:
        return None
    return None if store_photos.describes(mtime, size, stamp) else CHANGED


def find(library, photo_path):
    """The id of the photo at `photo_path`, as the move from a folder on disk to its library view looks for the photo it
    was looking at (phase 9c): None when the library holds no photo there (asked speculatively, so not an error: a 404 is
    a line in the browser's console each time). Refused for a path that is not text."""
    if not isinstance(photo_path, str) or not photo_path.strip():
        raise Refused("find needs the path of a photo.")
    conn = _open(library)
    try:
        found = store.photo_id_of(conn, paths.stored(photo_path.strip()))
    finally:
        conn.close()
    return found


def _cards(conn, photo_ids, check_disk=False):
    held = store.card_rows(conn, photo_ids)
    found = []
    started = time.monotonic()
    for photo_id in photo_ids:
        row = held.get(photo_id)
        if row is None:
            continue   # deleted since the page was read
        path, mtime, size, taken, kind = row
        # `media`: 'photo' or 'video' (photos.kind), which the page shows a picture or plays by (/api/video-file);
        # `mtime`: the row's stamp, which the page versions the file's address by (web/common/file-addresses.js).
        card = {"id": photo_id, "name": os.path.basename(path), "path": path, "taken": taken,
                "thumb": thumbnails.url(photo_id, mtime), "mtime": mtime, "media": kind}
        if check_disk and time.monotonic() - started < STAT_BUDGET_SECONDS:
            mark = disk_mark(path, mtime, size)
            if mark:
                card["stale"] = mark
        found.append(card)
    return found


def cards(library, photo_ids, check_disk=False):
    """The cards of the photos `photo_ids`, in that order: those that have no photo (deleted since) are left out.
    One read in batches, whatever the number; with `check_disk`, one stat of each file besides (at most MAX_CARDS
    when the ids come from read_ids), adding `stale` where the disk differs from the row."""
    conn = _open(library)
    try:
        return _cards(conn, list(photo_ids), check_disk)
    finally:
        conn.close()


# ---- A browse row's cover ---------------------------------------------------------------------------

#: What a cover request's rows are, said when they are not.
ROWS_SAID = ("rows are a list of {\"id\": the row's id, \"kind\": ..., \"value\": ..., \"recursive\": ...}: each row "
             "and the source a tap on it opens.")


def covers(library, section, rows):
    """{"section", "covers": [{"id", "photo", "thumb"}]}: for each browse row of `section` (SECTIONS) the page names,
    the first photo of the row's own source -- the photos a tap on the row opens -- in the views' order (by Date Taken,
    as `ids` answers a view opened with no order) and the URL of its thumbnail (thumbnails.url: a clip's is its poster),
    in the order asked. `photo` and `thumb` are None for a row whose source holds no photo now: a folder emptied, a tag
    no photo carries any more, a row the library no longer has.

    `rows` is a list -- or that list as JSON text, a request's -- of at most MAX_COVERS {"id", "kind", "value",
    "recursive"}: the row's id as the page's model names it (navigator-model.js), which the reply gives back, and its
    source as the page would open it (`specOfMembers`: one source, or a union of the rows under it); a row named twice is
    once. Refused for anything else, with why. Every row's first photo is read in ONE transaction, ids alone
    (store.all_ids, the read `ids` makes, capped at one), and their stamps in one more read of it (store.card_rows): no
    BLOB, no card, no disk looked at. The rows of the phone's Browse are counted per level (the widest of the live
    libraries, 2026-11-23: 257 keywords, 203 folders), so the page asks for those near its screen, never a level whole."""
    if section not in SECTIONS:
        raise Refused("section must be one of %s." % ", ".join(SECTIONS))
    canonical = _Canonical(library)
    asked = [(row_id, _source_of(each.get("kind"), each.get("value"), each.get("recursive"), canonical, member=False))
             for row_id, each in _rows_of(rows)]
    conn = _open(library)
    try:
        db.begin(conn)
        first = {row_id: store.all_ids(conn, source, 1)[0] for row_id, source in asked}
        stamps = store.card_rows(conn, [found[0] for found in first.values() if found])
    finally:
        conn.close()
    said = []
    for row_id, _ in asked:
        photo_id = first[row_id][0] if first[row_id] else None
        said.append({"id": row_id, "photo": photo_id,
                     "thumb": None if photo_id is None else thumbnails.url(photo_id, stamps[photo_id][1])})
    return {"section": section, "covers": said}


def _rows_of(rows):
    """[(row id, {"kind", "value", "recursive"})] of a cover request's `rows`, each row once, or Refused."""
    if isinstance(rows, str):
        try:
            rows = json.loads(rows)
        except ValueError:
            raise Refused(ROWS_SAID) from None
    if not isinstance(rows, list) or not rows:
        raise Refused(ROWS_SAID)
    if len(rows) > MAX_COVERS:
        raise Refused("Ask for the covers of at most %d rows at a time." % MAX_COVERS)
    named = {}
    for each in rows:
        if not isinstance(each, dict) or not isinstance(each.get("id"), str) or not each["id"]:
            raise Refused(ROWS_SAID)
        named.setdefault(each["id"], each)
    return list(named.items())


# ---- The navigator ----------------------------------------------------------------------------------

def navigator(library, section, indexing=None):
    """The counts of one section of the navigator: `folders` {"folders": [{path, name, parent, direct,
    recursive, queue}]} (_folders; `indexing`, a function answering what the library's index queue holds now,
    IndexQueue.active, asked before the tree is read), `keywords` {"keywords": [{tag, name, parent, count}], "untagged":
    the photos with no keyword} -- both read in one snapshot, so a photo tagged meanwhile is counted once --, `people` {"people": [{name, count, group, person}],
    "groups": [{tag, name, parent, count}], "unfiled"} -- each person under the tag of the branch they are filed in (None:
    not filed), the branches above people with the photos naming anyone under them, and the photos naming someone not
    filed (store.people_groups) --, `dates` {"years": [{year, count, months: [{month, count}], other}], "undated"}. Each
    is one read of the library."""
    if section not in SECTIONS:
        raise Refused("section must be one of %s." % ", ".join(SECTIONS))
    conn = _open(library)
    try:
        if section == "folders":
            # The queue first: a folder whose index ends between the two reads is marked once more, and the page reads
            # the section again while one is marked; read after, it would show unmarked with its last photos uncounted.
            held = indexing() if indexing else None
            return {"folders": _folders(store.folders(conn), held)}
        if section == "keywords":
            db.begin(conn)
            return {"keywords": _keywords(store.keyword_counts(conn)), "untagged": store.count(conn, store.Source(store.UNTAGGED))}
        if section == "people":
            counted = store.people_counts(conn)
            group_of, groups, unfiled = store.people_groups(conn, counted)
            tag_of = {group["id"]: group["tag"] for group in groups}
            everyone = person_ids.Directory.read(conn)
            # `group` is the navigator's own (the tag of the branch the person is filed in); `person` is the person.
            return {"people": [{"name": person.name, "count": count,
                                "group": group_of.get(person.id),
                                "person": everyone.of_id(person.id), "person_id": person.id}
                               for person, count in counted],
                    "groups": [{"tag": group["tag"], "name": group["name"], "parent": tag_of.get(group["parent_id"]),
                                "count": group["count"]}
                               for group in sorted(groups, key=lambda group: vocabulary.tag_sort_key(group["tag"]))],
                    "unfiled": unfiled}
        return store.date_counts(conn)
    finally:
        conn.close()


#: A folder's `queue` in the Folders tab while the library's index queue holds it (DECISIONS 2026-11-26).
QUEUED, INDEXING = "queued", "indexing"


def _folders(tree, held=None):
    """The Folders tab's list, parents before children: each folder of the tree with `queue` -- INDEXING or QUEUED
    while the library's index queue (`held`, IndexQueue.active's answer) is indexing it or holds it waiting, else None --
    and each folder the queue holds that the tree has not yet, counted 0 under the nearest folder above it that is listed
    (paths.row_parent, the rule the tree's own parents are made by), so a folder just added is in the tab at once. As
    its photos are indexed the tree holds it and it is the tree's row, once, its mark kept until the queue lets it go.
    Another library's queue is its own: `held` is this library's."""
    marks = {}
    for mark, jobs in ((QUEUED, (held or {}).get("queued")), (INDEXING, (held or {}).get("active"))):
        for job in jobs or ():
            marks[paths.key(job["folder"])] = (job, mark)
    path_of = {each["id"]: each["path"] for each in tree}
    listed = {}
    for each in tree:
        key = paths.key(each["path"])
        listed[key] = {"path": each["path"], "name": each["name"], "parent": path_of.get(each["parent_id"]),
                       "direct": each["direct"], "recursive": each["recursive"], "queue": marks.pop(key, (None, None))[1]}
    for key, (job, mark) in marks.items():
        listed[key] = {"path": job["folder"], "name": job["name"], "parent": None, "direct": 0, "recursive": 0,
                       "queue": mark}
    for key, (job, _mark) in marks.items():
        above = paths.row_parent(job["folder"])
        while above is not None and paths.key(above) not in listed:
            above = paths.row_parent(above)
        if above is not None:
            listed[key]["parent"] = listed[paths.key(above)]["path"]
    return sorted(listed.values(), key=lambda each: store_roots.path_order(each["path"]))


def _keywords(nodes):
    tag_of = {node["id"]: node["tag"] for node in nodes}
    return [{"tag": node["tag"], "name": node["name"], "parent": tag_of.get(node["parent_id"]), "count": node["count"]}
            for node in sorted(nodes, key=lambda node: vocabulary.tag_sort_key(node["tag"]))]

