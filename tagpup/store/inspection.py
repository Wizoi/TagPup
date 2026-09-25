"""What tagpup.services.inspect reads that no other store module does: photos by id, the
faces of one photo without their BLOBs, the rows whose file is gone, and the plan of a
query the caller wrote.

Every function takes a connection the caller opened, read-only (db.readonly_uri); none
writes. The questions are the ones findings were settled with, which each took a script
of its own to ask (docs/ARCHITECTURE.md, phase 7).
"""
import json
import os
import re
import sqlite3

from tagpup.core import paths

#: How many ids go in one IN (...): well under SQLite's limit on parameters.
CHUNK = 500


def _chunks(ids):
    ids = list(ids)
    for start in range(0, len(ids), CHUNK):
        yield ids[start:start + CHUNK]


def _marks(chunk):
    return ",".join("?" * len(chunk))


def _json(text, empty):
    try:
        return json.loads(text) if text else empty
    except (TypeError, ValueError):
        return empty


# ---- What a library holds ---------------------------------------------------------------

def schema_version(conn):
    """The highest migration applied, or 0 before any was recorded."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'").fetchone():
        return 0
    return conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]


def tree_nodes(conn):
    """How many nodes the tag tree has."""
    return conn.execute("SELECT COUNT(*) FROM tag_taxonomy").fetchone()[0]


def people_named(conn):
    """How many distinct people the photos list (photo_people), by exact spelling."""
    return conn.execute("SELECT COUNT(DISTINCT name) FROM photo_people").fetchone()[0]


# ---- Photos -----------------------------------------------------------------------------

def ids_and_paths(conn):
    """(id, path as stored) of every photo, by path: the unique index on path covers it,
    where ordering by id reads every row, raw metadata and all."""
    return conn.execute("SELECT id, path FROM photos ORDER BY path").fetchall()


def under(conn, folder):
    """(id, path as stored) of each photo under `folder`, at any depth, by id."""
    where, params = paths.sql_under("path", folder)
    return conn.execute("SELECT id, path FROM photos WHERE " + where + " ORDER BY id", params).fetchall()


def tags_of_every_photo(conn):
    """(id, path as stored, tags) of every photo; unreadable tags are []."""
    return [(photo_id, path, _json(tags_json, []))
            for photo_id, path, tags_json in conn.execute("SELECT id, path, tags FROM photos ORDER BY id")]


def of_person(conn, name):
    """(id, path as stored) of each photo whose people (photo_people) list `name`, spelled
    exactly as the rows spell it, by id. idx_photo_people_name serves it."""
    return conn.execute("SELECT p.id, p.path FROM photos p WHERE p.id IN"
                        " (SELECT photo_id FROM photo_people WHERE name = ?) ORDER BY p.id",
                        (name,)).fetchall()


def row(conn, photo_id):
    """{path, mtime, size, tags, captions, raw_metadata, document_id} of one photo, or None."""
    found = conn.execute("SELECT path, mtime, size, tags, captions, raw_metadata, document_id"
                         " FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if found is None:
        return None
    path, mtime, size, tags, captions, raw, document_id = found
    return dict(path=path, mtime=mtime, size=size, tags=_json(tags, []), captions=_json(captions, []),
                raw_metadata=_json(raw, {}), document_id=document_id)


def people_of(conn, photo_id):
    """[(name, source)] of one photo's people, in order."""
    return conn.execute("SELECT name, source FROM photo_people WHERE photo_id = ? ORDER BY position",
                        (photo_id,)).fetchall()


def ids_of_stored(conn, stored_paths):
    """{path: id} of the photos stored under exactly these paths."""
    found = {}
    for chunk in _chunks(stored_paths):
        found.update({path: photo_id for photo_id, path in conn.execute(
            "SELECT id, path FROM photos WHERE path IN (%s)" % _marks(chunk), chunk)})
    return found


def ids_of_files(conn, photo_paths):
    """The ids of the photos stored under any spelling of these paths, as paths compare."""
    found = set()
    for photo_path in photo_paths:
        where, params = paths.sql_equals("path", photo_path)
        found.update(photo_id for (photo_id,) in conn.execute("SELECT id FROM photos WHERE " + where, params))
    return sorted(found)


# ---- Faces ------------------------------------------------------------------------------

def faces_of(conn, photo_id):
    """(id, box JSON, name, name_source, prob, excluded, excluded_reason) of each face in one
    photo, in detection order. Never the embedding or the crop."""
    return conn.execute("SELECT id, box, name, name_source, prob, excluded, excluded_reason"
                        " FROM faces WHERE photo_id = ? ORDER BY id", (photo_id,)).fetchall()


def faces_on(conn, photo_ids):
    """{photo id: [(face id, name, name_source, excluded)]} for the photos among `photo_ids`
    that have faces. idx_faces_photo_id serves it; no BLOB is read."""
    found = {}
    for chunk in _chunks(photo_ids):
        for photo_id, face_id, name, source, excluded in conn.execute(
                "SELECT photo_id, id, name, name_source, excluded FROM faces"
                " WHERE photo_id IN (%s) ORDER BY id" % _marks(chunk), chunk):
            found.setdefault(photo_id, []).append((face_id, name, source, excluded))
    return found


# ---- Files ------------------------------------------------------------------------------

def whose_file_is_gone(conn):
    """(id, path as stored) of each photo whose file is not on disk, by path. What
    tagpup.store.checks.missing_files counts by folder, row by row."""
    return [(photo_id, path) for photo_id, path in conn.execute("SELECT id, path FROM photos ORDER BY path")
            if not os.path.exists(path)]


# ---- Query plans ------------------------------------------------------------------------

class NotARead(ValueError):
    """A query whose plan is refused: not one SELECT or WITH statement."""


#: Comments and blanks before a statement's first word.
_LEADING = re.compile(r"^(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*", re.S)

#: What a caller may already have put in front of the query.
_EXPLAIN = re.compile(r"^EXPLAIN\s+QUERY\s+PLAN\s+", re.I)

#: The first word of a statement this answers for.
_READ = re.compile(r"^(?:SELECT|WITH)\b", re.I)

#: What a statement may do while its plan is made: read tables, call functions, recurse.
_ALLOWED = frozenset({sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                      getattr(sqlite3, "SQLITE_RECURSIVE", 33)})

#: How SQLite says how many values the statement wants.
_WANTS = re.compile(r"statement uses (\d+)")


def _only_reads(action, *_names):
    return sqlite3.SQLITE_OK if action in _ALLOWED else sqlite3.SQLITE_DENY


def query_plan(conn, sql, params=None):
    """The rows of EXPLAIN QUERY PLAN for `sql` -- (id, parent, unused, detail) -- which
    is never run.

    Refuses (NotARead) anything but one SELECT or WITH statement: its first word is
    checked, SQLite refuses a second statement, and while the plan is made an authorizer
    denies every action but reading -- which catches a WITH that ends in a DELETE. A
    placeholder the caller gave no value for is bound to NULL.
    """
    text = _EXPLAIN.sub("", _LEADING.sub("", sql or "", count=1), count=1)
    text = _LEADING.sub("", text, count=1)
    if not _READ.match(text):
        raise NotARead("Only a SELECT or WITH statement has its plan read; this starts %r."
                       % text.split(None, 1)[0][:20] if text.strip() else "There is no statement.")
    conn.set_authorizer(_only_reads)
    try:
        values = params
        for _attempt in range(2):
            try:
                return [tuple(r) for r in conn.execute("EXPLAIN QUERY PLAN " + text,
                                                       values if values is not None else ()).fetchall()]
            except sqlite3.ProgrammingError as e:
                wanted = _WANTS.search(str(e))
                if "one statement at a time" in str(e):
                    raise NotARead("Only one statement has its plan read.") from e
                if params is not None or not wanted:
                    raise NotARead(str(e)) from e
                values = (None,) * int(wanted.group(1))
            except sqlite3.DatabaseError as e:
                if "not authorized" in str(e):
                    raise NotARead("Only a statement that reads has its plan read.") from e
                raise NotARead(str(e)) from e
        raise NotARead("The statement's values could not be bound.")
    finally:
        conn.set_authorizer(None)
