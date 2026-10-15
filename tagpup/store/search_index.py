"""The word index a search's words are matched in (docs/ARCHITECTURE.md, phase 9e-1): two SQLite FTS5 tables, derived
from the photos' rows, never journaled, made by the baseline (tagpup.store.schema) and kept by the store's writes in their
own transactions.

* `search_words(tags, captions, people)`, tokenize `unicode61 remove_diacritics 2`, prefixes of 2 and 3 indexed: the
  words of each keyword the photo's row holds -- its path and its leaf, as the file spells it, so a keyword with no node
  of the tree is found by its words, and a node renamed in the tree is found by its new words once the files say them,
  as the photo's keyword rows follow --, of its captions and titles (`photos.captions`), and of the people
  `photo_people` lists for it (by the leaf rule: names on faces too, not only keywords). "Élodie" is found by "elodie".
* `search_names(name, folders)`, tokenize `trigram remove_diacritics 1`: the file name and the folders it is in -- of a
  root-relative row every folder below the root ("@pictures/2024/Coast" holds "2024/Coast": the root's name is never a
  word); of a native row only the folder it is directly in, since the folders above it are the machine's own layout
  (the drive, "Users", the profile, "Pictures"), whose words every photo of the library would match. A trigram finds
  any three characters inside a name: "0412" in "20190412_1430.jpg".

The camera and the lens a photo was taken with (`photo_meta.gear`: "Canon EOS R6m2", "EF24-70mm f/2.8L II USM") are in no
table: a search finds them in the metadata (`cameras_with`, below). There was an FTS5 table of them (`search_gear`, migration
27) until migration 30 dropped it; nothing but the search read it, and it had to be kept in step with every photo's metadata.

Both of them are CONTENTLESS (`content=''`, `contentless_delete=1`): the text is not stored twice, a row is replaced by INSERT OR
REPLACE and taken by its rowid, which is the photo's id. A photo deleted takes its rows by a trigger whichever connection
deletes it (`search_goes_with_its_photo`), as the derived tables do.

**Who keeps them.** `refresh(conn, photo_ids)`, in the writer's transaction: called by tagpup.store.derived for every
photo whose keywords, path, metadata or captions it refreshes (so the index's record, the bulk tag writes, sync, the moves
and renames, the journal's undo), by people.rebuild for the photos whose people it wrote (so a face named, a tree edit that
changes who is a person, a rename of a person), and by derived.rebuild_all whole. `rebuild(conn)`
makes both from the photos, whole.

**A search's words** (`clause`): the text typed, split at blanks, each TERM a set condition, ANDed with every other and with
the search's sources; never ranked. A term is found in a photo when its words table matches the term as a quoted PREFIX
phrase (`"term"*`: every term a prefix, so "beach" finds "beaches" and the term being typed finds as it is typed), for
a term of three characters or more its names table holds the term (`"term"`, a substring), or its camera or lens holds the
term (`cameras_with`: a substring, without case or accents, of the one name `photo_meta.gear` gives it). Everything typed is quoted,
so AND, OR, NOT, NEAR, `*`, `^`, `"`, `:`, `(`, `)` and `-` are text and never FTS5's syntax. A term with no letter or digit
and under three characters says nothing and is dropped (`terms`).

The caller commits.
"""
import json
import os
import unicodedata

from tagpup.core import paths, photo_meta
from tagpup.core.result import Refused
from tagpup.store import generations

WORDS, NAMES = "search_words", "search_names"
TABLES = (WORDS, NAMES)
#: The tables FTS5 makes for each (contentless: no _content).
SHADOWS = tuple("%s_%s" % (table, shadow) for table in TABLES for shadow in ("data", "idx", "docsize", "config"))

#: The statements the baseline runs (tagpup.store.schema).
CREATE = (
    "CREATE VIRTUAL TABLE %s USING fts5(tags, captions, people, tokenize = 'unicode61 remove_diacritics 2',"
    " prefix = '2 3', content = '', contentless_delete = 1)" % WORDS,
    "CREATE VIRTUAL TABLE %s USING fts5(name, folders, tokenize = 'trigram remove_diacritics 1', content = '',"
    " contentless_delete = 1)" % NAMES,
    "CREATE TRIGGER search_goes_with_its_photo AFTER DELETE ON photos BEGIN"
    " DELETE FROM %s WHERE rowid = OLD.id; DELETE FROM %s WHERE rowid = OLD.id; END" % (WORDS, NAMES),
)

#: Photos read at a time.
CHUNK = 500

#: The most terms a search's words hold.
MAX_TERMS = 20

#: The fewest characters a term is looked for inside a file or folder name with (a trigram).
SUBSTRING = 3

#: How many photos the check compares with their sources (spread over the ids).
SAMPLE = 500


# ---- A photo's text ------------------------------------------------------------------------------

def _texts(value):
    """The texts a JSON list holds (keywords, captions): [] for none, for JSON that is not a list, and for an item that is
    not text -- a damaged row says nothing, as derived.keywords_of reads it."""
    if not value:
        return []
    try:
        found = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [each for each in found if isinstance(each, str)] if isinstance(found, list) else []


def folder_text(path):
    """The folders a photo's row-form `path` is in, as words are looked for in them: every folder below the root of a
    root-relative row (the root's name is never a word); the one folder a native row is directly in, never a drive or a
    share. "" for a photo at a top."""
    folder = paths.row_parent(path)
    if not folder:
        return ""
    if folder.startswith(paths.ROOT_MARK):
        return folder.partition(paths.ROW_SEP)[2]
    drive, rest = os.path.splitdrive(folder)
    return paths.row_name(folder) if rest.strip("\\/") else ""


def photo_text(path, tags_json, captions_json, people):
    """((tags, captions, people), (name, folders)): what the two tables hold of one photo."""
    return (("\n".join(_texts(tags_json)), "\n".join(_texts(captions_json)), "\n".join(people)),
            (paths.row_name(path), folder_text(path)))


def _marks(items):
    return ",".join("?" * len(items))


def _rows(conn, ids):
    """{photo id: photo_text} of the photos `ids` that have a row: two reads, no BLOB, no metadata."""
    listed = {}
    for photo_id, name in conn.execute("SELECT photo_id, name FROM photo_people WHERE photo_id IN (%s)"
                                       " ORDER BY photo_id, position" % _marks(ids), ids):
        listed.setdefault(photo_id, []).append(name)
    return {photo_id: photo_text(path, tags, captions, listed.get(photo_id, ()))
            for photo_id, path, tags, captions in conn.execute(
                "SELECT id, path, tags, captions FROM photos WHERE id IN (%s)" % _marks(ids), ids)}


def _write(conn, found):
    conn.executemany("INSERT OR REPLACE INTO %s (rowid, tags, captions, people) VALUES (?, ?, ?, ?)" % WORDS,
                     [(photo_id,) + words for photo_id, (words, _names) in found.items()])
    conn.executemany("INSERT OR REPLACE INTO %s (rowid, name, folders) VALUES (?, ?, ?)" % NAMES,
                     [(photo_id,) + names for photo_id, (_words, names) in found.items()])


def refresh(conn, photo_ids):
    """Make the rows of the photos `photo_ids` what their keywords, captions, people, name and folders say now: called by
    every writer of any of them, in its transaction. An id with no photo has its rows taken. Returns how many photos."""
    ids = sorted({int(photo_id) for photo_id in photo_ids})
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start:start + CHUNK]
        found = _rows(conn, chunk)
        gone = [(photo_id,) for photo_id in chunk if photo_id not in found]
        if gone:
            for table in TABLES:
                conn.executemany("DELETE FROM %s WHERE rowid = ?" % table, gone)
        _write(conn, found)
    return len(ids)


def rebuild(conn):
    """Make both tables what the photos say, whole, in the caller's transaction: the doctor's repair, and
    an adoption's (every path's spelling changes). Reads each photo once, in chunks by id; merges the index's segments
    after (`optimize`). Returns how many photos."""
    for table in TABLES:
        conn.execute("INSERT INTO %s (%s) VALUES ('delete-all')" % (table, table))
    last, count = -1, 0
    while True:
        ids = [photo_id for (photo_id,) in conn.execute("SELECT id FROM photos WHERE id > ? ORDER BY id LIMIT ?",
                                                        (last, 2000))]
        if not ids:
            break
        last = ids[-1]
        for start in range(0, len(ids), CHUNK):
            found = _rows(conn, ids[start:start + CHUNK])
            _write(conn, found)
            count += len(found)
    for table in TABLES:
        conn.execute("INSERT INTO %s (%s) VALUES ('optimize')" % (table, table))
    return count


# ---- What a rule would say ------------------------------------------------------------------------

def _phrase(text):
    return '"%s"' % text.replace('"', '""')


def _spread(conn, sample):
    """The ids of `sample` photos spread over the ids (None: every photo)."""
    ids = [photo_id for (photo_id,) in conn.execute("SELECT id FROM photos ORDER BY id")]
    if sample is not None and len(ids) > sample:
        step = len(ids) / sample
        ids = [ids[int(n * step)] for n in range(sample)]
    return ids


def stale(conn, sample=SAMPLE):
    """The ids of the photos whose rows are not what their sources give: every photo with no row in any table, every
    row that is no photo's, and of `sample` photos spread over the ids (None: every photo), those a column of whose text
    -- matched as one phrase, its words in order -- is not found in its row. A contentless table cannot be read back, so a
    word left behind that the text no longer holds is not seen by the sample. Reads only."""
    found = set()
    for table in TABLES:
        found.update(photo_id for (photo_id,) in conn.execute(
            "SELECT id FROM photos WHERE id NOT IN (SELECT id FROM %s_docsize)" % table))
        found.update(row_id for (row_id,) in conn.execute(
            "SELECT id FROM %s_docsize WHERE id NOT IN (SELECT id FROM photos)" % table))
    ids = _spread(conn, sample)
    columns = ((WORDS, ("tags", "captions", "people")), (NAMES, ("name", "folders")))
    for start in range(0, len(ids), CHUNK):
        for photo_id, texts in _rows(conn, ids[start:start + CHUNK]).items():
            if photo_id in found:
                continue
            for (table, names), values in zip(columns, texts):
                for column, text in zip(names, values):
                    # A text with nothing to match -- a caption that is only a dash (photo_index holds such) -- is no phrase.
                    if not any(each.isalnum() for each in text) if table == WORDS else len(text.strip()) < SUBSTRING:
                        continue
                    if not conn.execute("SELECT 1 FROM %s WHERE %s MATCH ? AND rowid = ?" % (table, table),
                                        ("%s : %s" % (column, _phrase(text)), photo_id)).fetchone():
                        found.add(photo_id)
    return sorted(found)


#: What the check of the word index cannot see, and the remedy (#752): said wherever its count is reported.
LIMITS = ("the word index is checked by a row for every photo and a sample of %d photos' texts; words a photo no longer "
          "holds, left behind by a version of TagPup that does not know the index, are not seen. "
          "tools/doctor.py --rebuild-derived --apply makes it again from the photos" % SAMPLE)


def problems(conn):
    """What is wrong with the word index, as sentences of counts, [] when nothing."""
    wrong = stale(conn)
    return ["%d photo(s) have word index rows that are not what their rows give (tools/doctor.py --rebuild-derived --apply "
            "makes it again)" % len(wrong)] if wrong else []


# ---- A search's words ------------------------------------------------------------------------------

def _says_something(term):
    return any(each.isalnum() for each in term) or len(term) >= SUBSTRING


def terms(words):
    """The terms of the text `words` a search looks for: split at blanks, those that say nothing dropped (no letter or
    digit, and too short to look for inside a name). Refused for more than MAX_TERMS."""
    found = [term for term in (words or "").split() if _says_something(term)]
    if len(found) > MAX_TERMS:
        raise Refused("A search looks for at most %d words at once; that is %d." % (MAX_TERMS, len(found)))
    return found


# ---- The camera and the lens ---------------------------------------------------------------------

#: The fields a photo's camera and lens are read from (tagpup.core.photo_meta): a scan of the metadata reads these alone.
GEAR_FIELDS = (photo_meta.MAKE_FIELDS + photo_meta.MODEL_FIELDS + photo_meta.LENS_FIELDS + photo_meta.LENS_MAKE_FIELDS)

#: Every photo's gear fields in one read, each photo's metadata parsed once by SQLite: a JSON list of the values, in
#: GEAR_FIELDS' order, null for a field the photo lacks; null for text that is not JSON (which names no camera).
_GEAR_SQL = ("SELECT id, CASE WHEN json_valid(raw_metadata) THEN json_extract(raw_metadata, %s) END FROM photos"
             % ", ".join("?" * len(GEAR_FIELDS)))


def _fold(text):
    """`text` as a camera is compared with what is typed: without case and without accents ("Élan" is found by "elan")."""
    return "".join(each for each in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(each))


def _read_cameras(conn):
    """[(camera, lens, [photo ids])] of the photos that name either, each folded (_fold), one entry for each distinct pair
    (photo_index: 136). The pair is photo_meta.gear's, the one rule that names a camera in the details panel and in Shift Date
    Taken's list: SQLite reads the fields, `gear` reads them as the file's metadata. Reads photos' ids and the one column
    (a scan of the table: 0.25 s on 68,000 photos)."""
    by_values = {}
    for photo_id, values in conn.execute(_GEAR_SQL, tuple("$.\"%s\"" % field for field in GEAR_FIELDS)):
        if values is not None:
            by_values.setdefault(values, []).append(photo_id)
    found = {}
    for values, ids in by_values.items():
        raw = {field: value for field, value in zip(GEAR_FIELDS, json.loads(values)) if value is not None}
        gear = photo_meta.gear(raw)
        if gear.camera or gear.lens:
            found.setdefault((_fold(gear.camera or ""), _fold(gear.lens or "")), []).extend(ids)
    return [(camera, lens, ids) for (camera, lens), ids in found.items()]


#: Built again once any photo changes (generations: the photos' counter moves on every write, from any process).
_cameras = generations.Cache(["photos"], _read_cameras)


def _file_of(conn):
    for _seq, name, file in conn.execute("PRAGMA database_list"):
        if name == "main":
            return paths.key(file) if file else None
    return None


#: The fewest characters a camera term has unless it holds a digit.
CAMERA_TERM = 3


def cameras_with(conn, term):
    """The ids of the photos whose camera or lens holds `term` anywhere in it, without case or accents: "r6" finds "Canon
    EOS R6m2", "24-70" finds "EF24-70mm f/2.8L II USM". Plain text, never a pattern. From the cache of the photos'
    cameras (a library on no file, a test's in-memory one, reads them each time). A term of under three characters with no
    digit matches no camera ("on", "a", "to": a caption search must not return every Canon); "r6" and "x100" do."""
    if len(term) < CAMERA_TERM and not any(each.isdigit() for each in term):
        return []
    key = _file_of(conn)
    cameras = _read_cameras(conn) if key is None else _cameras.get(conn, key)
    folded = _fold(term)
    ids = []
    for camera, lens, held in cameras:
        if folded in camera or folded in lens:
            ids.extend(held)
    return ids


def clause(conn, words):
    """(SQL over `photos p`, params) of the photos that hold every term of `words` (terms): for each term, its words
    table as a prefix phrase, its names table as a substring (three characters or more), or its camera or lens as a
    substring (cameras_with: the ids as one JSON list, read by json_each); None when no term says anything. Each term is
    quoted, its quotes doubled: nothing typed is read as FTS5's syntax."""
    parts = []
    for term in terms(words):
        either, params = [], []
        if any(each.isalnum() for each in term):
            either.append("p.id IN (SELECT rowid FROM %s WHERE %s MATCH ?)" % (WORDS, WORDS))
            params.append(_phrase(term) + "*")
        if len(term) >= SUBSTRING:
            either.append("p.id IN (SELECT rowid FROM %s WHERE %s MATCH ?)" % (NAMES, NAMES))
            params.append(_phrase(term))
        taken = cameras_with(conn, term)
        if taken:
            either.append("p.id IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(taken))
        parts.append(("(%s)" % " OR ".join(either), tuple(params)))
    if not parts:
        return None
    return " AND ".join(sql for sql, _params in parts), tuple(value for _sql, params in parts for value in params)
