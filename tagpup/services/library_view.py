"""The library as the views see it: which photos a source holds, a page of them, and the navigator's counts
(docs/ARCHITECTURE.md, phase 9a; the SQL is tagpup.store.library_view).

A SOURCE says which photos: `all`; a `folder` (by its path, never an id) and, when asked, its subfolders; a
`keyword` -- the tag as the tree spells it -- and everything under it; a `person` (the leaf name, as photo_people
holds it: identity by id comes later); a `year`; a `month` ("2024-06"). `view` answers an ORDERED page of photo
ids with the TOTAL and the cards of the page; `cards` turns any list of ids into cards.

* **Order**: by Date Taken (`photos.taken`, ExifTool's text, which sorts as time does) and then id; photos with
  no date after them, by id. Never by file time. A page is `limit` photos (at most MAX_LIMIT) after a KEYSET
  token (`after`, from the page before: opaque; a forged or corrupt one is refused, never trusted), never an
  OFFSET: a photo added, deleted or re-dated between two pages neither repeats nor skips another beyond what
  that change itself explains, and a page deep in a 20,000-photo source costs what the first does.
* **A card** is small: id, file name, native path, taken, whether the photo is recorded damaged, and the
  thumbnail's URL by id (tagpup.services.thumbnails). One batch read of the columns it needs: no
  raw_metadata, no BLOB, no query for each photo. `view` looks at no disk. `cards` may (`check_disk`, which the
  page's route asks): one stat per card, bounded for a network share, adds `stale` -- "changed" when the file's
  size and time no longer describe the row, "missing" when the file is gone -- and nothing to a card whose file
  is as the row says, is on a share that did not answer, cannot be read, or whose row was never stamped.
* **The navigator**: `navigator(library, section)` for `folders`, `keywords`, `people` or `dates`, each one query
  (or one read of a derived table) and a pass in memory; native paths, tag paths and names out, never ids.
* **What fails, and how it says so**: a library that has not had migrations 19 and 20 (the derived tables and
  the date indexes) is `NotReady`, a sentence; a root this machine does not place raises paths.RootsError, whose
  sentence the web layer's gate answers first; a request that cannot mean anything is `Refused`, with why. Reads
  only, on a read-only connection: nothing here migrates or writes a library.
"""
import base64
import binascii
import json
import os
import re

from tagpup.core import paths, vocabulary
from tagpup.core.result import NotFound, Refused
from tagpup.services import damaged_photos, thumbnails
from tagpup.services import photos as photo_actions
from tagpup.services import roots as roots_service
from tagpup.store import db
from tagpup.store import library_view as store
from tagpup.store import photos as store_photos
from tagpup.store import roots as store_roots

KINDS = store.KINDS
SECTIONS = ("folders", "keywords", "people", "dates")

#: What a card says of its file when the disk was looked at (`stale`).
CHANGED = "changed"
MISSING = "missing"

#: Photos in a page when none is asked for, and the most a page holds.
DEFAULT_LIMIT = 200
MAX_LIMIT = 500

#: The most ids `ids` answers for one source, and the most cards `cards_of` answers for one ask.
MAX_IDS = 200_000
MAX_CARDS = 200

#: The longest page token read: a token is a few dozen characters; a very large one is refused before it is decoded.
MAX_TOKEN = 200

_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_YEAR = re.compile(r"^\d{1,4}$")


class NotReady(Refused):
    """The library has not been brought up to date for the views: its derived tables or its date indexes are
    missing. TagPup does that as it opens a library; a program that could not (the library held by another, or
    read-only) says so here. A web route answers it 409."""


def not_ready_sentence(library):
    return ("%s has not been brought up to date for browsing by folder, keyword, person or date yet. TagPup does "
            "that when it opens a library: open it in TagPup once, or run `tagpup_cli.py --db %s thumbs warm --apply`, "
            "which brings it up to date first, and try again." % (library.name, library.name))


def _open(library):
    """A read-only connection to a library that is ready for the views."""
    if not os.path.exists(library.path):
        raise NotFound("There is no library at %s." % library.path)
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        if not store.ready(conn):
            raise NotReady(not_ready_sentence(library))
    except BaseException:
        conn.close()
        raise
    return conn


# ---- The request's words, read ------------------------------------------------------------------

def source_of(library, kind, value=None, recursive=False):
    """The store's Source for a request's words, or Refused with why it means nothing. A folder is
    spelled by its first place when the library has roots (paths.canonical: the web layer's ingress does it
    for the request, this for a caller that is not one) and as the filesystem spells it (paths.stored)."""
    if kind not in KINDS:
        raise Refused("kind must be one of %s." % ", ".join(KINDS))
    if kind == store.ALL:
        return store.Source(store.ALL)
    if not isinstance(value, str) or not value.strip():
        raise Refused("A %s source needs a value." % kind)
    if kind == store.FOLDER:
        folder = paths.stored(value.strip())
        canonical = roots_service.canonicaliser(library)
        return store.Source(store.FOLDER, canonical(folder) if canonical else folder, bool(recursive))
    if kind == store.KEYWORD:
        tag = vocabulary.normalize(value)
        if not tag:
            raise Refused("A keyword source needs a tag.")
        return store.Source(store.KEYWORD, tag)
    if kind == store.PERSON:
        return store.Source(store.PERSON, value.strip())
    if kind == store.YEAR:
        if not _YEAR.match(value.strip()):
            raise Refused("A year is up to four digits, as 2024.")
        return store.Source(store.YEAR, int(value.strip()))
    if not _MONTH.match(value.strip()):
        raise Refused("A month is written YYYY-MM, as 2024-06.")
    return store.Source(store.MONTH, value.strip())


def page_size(limit):
    """How many photos a page holds for the `limit` asked (None: DEFAULT_LIMIT): at most MAX_LIMIT. Refused for
    one that is not a number or is not above zero."""
    if limit is None or limit == "":
        return DEFAULT_LIMIT
    try:
        wanted = int(limit)
    except (TypeError, ValueError):
        raise Refused("limit must be a whole number from 1 to %d." % MAX_LIMIT) from None
    if wanted < 1:
        raise Refused("limit must be a whole number from 1 to %d." % MAX_LIMIT)
    return min(wanted, MAX_LIMIT)


def encode(cursor):
    """The token that continues after `cursor` (store.Cursor): opaque to a page."""
    raw = json.dumps([cursor.phase, cursor.taken, cursor.id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode(token):
    """The store.Cursor a token says, None for no token. Refused for one that is not what `encode` makes -- too
    long, not base64, not JSON, the wrong shape: it is read and checked, never trusted."""
    if token is None or token == "":
        return None
    if not isinstance(token, str) or len(token) > MAX_TOKEN:
        raise Refused("That page token is not one this server made.")
    try:
        found = json.loads(base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise Refused("That page token is not one this server made.") from None
    if not (isinstance(found, list) and len(found) == 3):
        raise Refused("That page token is not one this server made.")
    phase, taken, photo_id = found
    ok = (phase in (0, 1) and type(phase) is int and type(photo_id) is int and 0 <= photo_id < 2 ** 62
          and (isinstance(taken, str) and 0 < len(taken) <= 64 if phase == 0 else taken is None))
    if not ok:
        raise Refused("That page token is not one this server made.")
    return store.Cursor(phase, taken, photo_id)


# ---- A page ---------------------------------------------------------------------------------------

def view(library, kind, value=None, recursive=False, after=None, limit=None):
    """A page of the photos of a source: {"source": {kind, value, recursive}, "total", "ids", "next" (a token or
    None), "limit", "cards"}. The total is of the whole source, not of the page. A source that holds nothing
    -- a folder with no photo, a keyword no node has, a person nobody is -- is an empty page, not an error."""
    source = source_of(library, kind, value, recursive)
    cursor = decode(after)
    size = page_size(limit)
    conn = _open(library)
    try:
        rows, more, total = store.view(conn, source, cursor, size)
        shown = _cards(conn, [photo_id for photo_id, _taken in rows])
    finally:
        conn.close()
    return {"source": {"kind": source.kind, "value": source.value, "recursive": source.recursive},
            "total": total, "ids": [photo_id for photo_id, _taken in rows],
            "next": encode(store.next_cursor(rows[-1])) if more and rows else None,
            "limit": size, "cards": shown}


def ids(library, kind, value=None, recursive=False, cap=None):
    """{"source", "total", "ids", "complete"}: the whole ordered id list of a source, in the order `view` pages it, for
    a page that jumps to the middle of it. At most `cap` ids (MAX_IDS); `total` is the source's, `complete` false
    when the list was cut. One read of ids alone: no card, no BLOB."""
    source = source_of(library, kind, value, recursive)
    conn = _open(library)
    try:
        found, total = store.all_ids(conn, source, MAX_IDS if cap is None else cap)
    finally:
        conn.close()
    return {"source": {"kind": source.kind, "value": source.value, "recursive": source.recursive},
            "total": total, "ids": found, "complete": len(found) == total}


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


def photo(library, photo_id, exiftool_path=None):
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
    path, mtime, size, tags, people, captions, raw, year = row
    stamp = photo_actions.file_stamp(path)
    if stamp is not None and not store_photos.describes(mtime, size, stamp):
        record = photo_actions.read_file(library, path, exiftool_path() if callable(exiftool_path) else exiftool_path, stamp)
    else:
        record = photo_actions.page_record(path, {
            "tags": json.loads(tags) if tags else [], "people": json.loads(people) if people else [],
            "captions": json.loads(captions) if captions else [], "raw_metadata": json.loads(raw) if raw else {},
            "year": year}, mtime if stamp is None else stamp[0], size if stamp is None else stamp[1])
        if stamp is None:
            record["missing"] = True
    record["id"] = photo_id
    # What the library records of the photo's damage, as its card says it (the panel shows why, and asks for no
    # picture of a file recorded unreadable).
    shown = cards(library, [photo_id])
    record["damaged"] = bool(shown and shown[0]["damaged"])
    record["damage"] = shown[0]["damage"] if shown else None
    return record


def disk_mark(path, mtime, size):
    """What the disk says of the file a row describes: MISSING when it is not there, CHANGED when its size and time are
    other than the row's (the folder scan's own test, store.photos.describes), else None. None too when the file
    cannot be read, when its share did not answer in time (a share found away is answered at once, so a page of cards
    on it waits once: damaged_photos.stamp_of), and when the row was never stamped -- Suggest's row for a photo nobody
    read holds no size or time to compare, and 'changed' would claim a difference nothing knows of."""
    stamp = damaged_photos.stamp_of(path)
    if stamp is None:
        return MISSING
    if stamp is damaged_photos.CANNOT_READ or stamp is damaged_photos.UNANSWERED:
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
    recorded = store.damaged(conn)
    found = []
    for photo_id in photo_ids:
        row = held.get(photo_id)
        if row is None:
            continue   # deleted since the page was read
        path, mtime, size, taken = row
        record = recorded.get(paths.key(path))
        flagged = record is not None and damaged_photos.describes((record.mtime, record.size), (mtime, size))
        card = {"id": photo_id, "name": os.path.basename(path), "path": path, "taken": taken,
                "damaged": bool(flagged), "damage": record.kind if flagged else None,
                "thumb": thumbnails.url(photo_id, mtime)}
        if check_disk:
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


# ---- The navigator ----------------------------------------------------------------------------------

def navigator(library, section):
    """The counts of one section of the navigator: `folders` {"folders": [{path, name, parent, direct,
    recursive}]}, `keywords` {"keywords": [{tag, name, parent, count}]}, `people` {"people": [{name, count}]}, `dates`
    {"years": [{year, count, months: [{month, count}], other}], "undated"}. Each is one read of the library."""
    if section not in SECTIONS:
        raise Refused("section must be one of %s." % ", ".join(SECTIONS))
    conn = _open(library)
    try:
        if section == "folders":
            return {"folders": _folders(store.folders(conn))}
        if section == "keywords":
            return {"keywords": _keywords(store.keyword_counts(conn))}
        if section == "people":
            return {"people": [{"name": name, "count": count} for name, count in store.people_counts(conn)]}
        return store.date_counts(conn)
    finally:
        conn.close()


def _folders(tree):
    path_of = {each["id"]: each["path"] for each in tree}
    return [{"path": each["path"], "name": each["name"], "parent": path_of.get(each["parent_id"]),
             "direct": each["direct"], "recursive": each["recursive"]}
            for each in sorted(tree, key=lambda each: store_roots.path_order(each["path"]))]


def _keywords(nodes):
    tag_of = {node["id"]: node["tag"] for node in nodes}
    return [{"tag": node["tag"], "name": node["name"], "parent": tag_of.get(node["parent_id"]), "count": node["count"]}
            for node in sorted(nodes, key=lambda node: vocabulary.tag_sort_key(node["tag"]))]

