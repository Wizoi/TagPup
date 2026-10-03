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
  raw_metadata, no BLOB, no query for each photo, no look at the disk.
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
from tagpup.services import roots as roots_service
from tagpup.store import db
from tagpup.store import library_view as store
from tagpup.store import roots as store_roots

KINDS = store.KINDS
SECTIONS = ("folders", "keywords", "people", "dates")

#: Photos in a page when none is asked for, and the most a page holds.
DEFAULT_LIMIT = 200
MAX_LIMIT = 500

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


def _cards(conn, photo_ids):
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
        found.append({"id": photo_id, "name": os.path.basename(path), "path": path, "taken": taken,
                      "damaged": bool(flagged), "damage": record.kind if flagged else None,
                      "thumb": thumbnails.url(photo_id, mtime)})
    return found


def cards(library, photo_ids):
    """The cards of the photos `photo_ids`, in that order: those that have no photo (deleted since) are left out.
    One read in batches, whatever the number."""
    conn = _open(library)
    try:
        return _cards(conn, list(photo_ids))
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
            for node in sorted(nodes, key=lambda node: vocabulary.key(node["tag"] or ""))]

