"""What a library holds and whether it keeps its rules: read-only questions, answered
with counts and ids.

Settling #42 in docs/findings.md took four throwaway scripts to learn that its 35 names
sat on 28 rows of a folder deleted on purpose. Each question a finding needed a script
for is a function here, and a tool of the MCP server (tagpup.mcp) Claude works through:
what a library holds, photos by folder, tag or person, a photo's row against its file,
the faces in a photo, the consistency checks tools/doctor.py runs, the rows whose file
is gone, and the plan of a query.

Every connection is read-only (db.readonly_uri); nothing here writes, migrates or
creates a library. The library is photographs of real people, many of them minors, so
an answer carries counts and ids, and paths, names and tags -- tags name people -- only
when the caller passes `reveal=True`.

A read returns what it read, and raises NotFound for a photo that is not there and
Refused for a question it will not answer (docs/ARCHITECTURE.md, Decisions, 2026-09-24).
"""
import json
import os
from contextlib import closing

from tagpup.core import paths, vocabulary
from tagpup.core.result import NotFound, Refused
from tagpup.files.metadata import MetadataExtractor
from tagpup.store import checks as rules
from tagpup.store import db, embeddings, inspection, taxonomy

#: How many ids an answer lists at most, unless asked for more; the count is always whole.
LIMIT = 100

#: Checks whose examples name no photo or person: migrations and triggers.
PLAIN_EXAMPLES = frozenset({"schema_current", "generations_kept"})

#: Checks whose examples are photo paths, which an answer gives as the photos' ids.
PATH_EXAMPLES = frozenset({"people_out_of_date"})

#: Checks whose examples are paths.key spellings, which may match several rows.
KEY_EXAMPLES = frozenset({"one_file_two_rows"})

#: Each check by the name a caller asks for it by: tagpup.store.checks' function names.
CHECKS = {rule.__name__: rule for rule in rules.RULES}


def _reading(library):
    if not os.path.exists(library.path):
        raise NotFound("There is no library at the path given.")
    return closing(db.connect(db.readonly_uri(library.path), uri=True))


def _listed(pairs, reveal, limit):
    """{count, ids, (paths)} of (id, path) pairs: the ids and paths up to `limit`."""
    shown = pairs[:max(0, limit)]
    answer = {"count": len(pairs), "ids": [photo_id for photo_id, _path in shown]}
    if len(pairs) > len(shown):
        answer["more"] = len(pairs) - len(shown)
    if reveal:
        answer["paths"] = [path for _photo_id, path in shown]
    return answer


# ---- What a library holds ---------------------------------------------------------------

def summary(library, embedder_settings=None):
    """What the library holds, as tools/doctor.py prints it: photos, faces, named, named by
    hand, excluded, untagged; with the schema's version, the tree's nodes, the people the
    photos list, and -- given the CLIP model's settings (the library's: tagpup.services.settings)
    -- how many photos have no vector for that model. Counts only."""
    with _reading(library) as conn:
        held = dict(rules.summary(conn))
        held["schema_version"] = inspection.schema_version(conn)
        held["tree_nodes"] = inspection.tree_nodes(conn)
        held["people"] = inspection.people_named(conn)
        if embedder_settings is not None:
            held["without_a_vector"] = rules.without_a_vector(conn, embeddings.model_key(**embedder_settings))
    return held


def folders(library, reveal=False, limit=LIMIT):
    """The folders the library's photos are directly in: for each, how many photos and
    whether it is on disk, the fullest first. Folders are numbered in that order; a
    path only with `reveal`."""
    with _reading(library) as conn:
        pairs = inspection.ids_and_paths(conn)
    counted, spelled = {}, {}
    for _photo_id, path in pairs:
        folder = os.path.dirname(path)
        counted[paths.key(folder)] = counted.get(paths.key(folder), 0) + 1
        spelled.setdefault(paths.key(folder), folder)
    order = sorted(counted, key=lambda k: (-counted[k], k))
    shown = []
    for number, key in enumerate(order[:max(0, limit)], 1):
        entry = {"folder": number, "photos": counted[key], "on_disk": os.path.isdir(spelled[key])}
        if reveal:
            entry["path"] = spelled[key]
        shown.append(entry)
    return {"count": len(order), "photos": len(pairs), "folders": shown}


def photos(library, folder=None, tag=None, person=None, reveal=False, limit=LIMIT):
    """The photos under a folder (any depth), carrying a tag (or a tag under it), or listing
    a person among their people -- each given narrows the others. Their count and ids;
    their paths only with `reveal`. A person is a name (Rowan Thackeray) or their tag
    (People/Rowan Thackeray), spelled as the photos spell it."""
    if not (folder or tag or person):
        raise Refused("Name a folder, a tag or a person.")
    found = None
    with _reading(library) as conn:
        if folder:
            found = dict(inspection.under(conn, folder))
        if person:
            name = vocabulary.leaf_of(person)
            of_person = dict(inspection.of_person(conn, name))
            found = of_person if found is None else {i: p for i, p in found.items() if i in of_person}
        if tag:
            wanted = vocabulary.normalize(tag)
            carrying = {photo_id: path for photo_id, path, tags in inspection.tags_of_every_photo(conn)
                        if (found is None or photo_id in found) and vocabulary.retag(tags, wanted)[1]}
            found = carrying
    return _listed(sorted(found.items()), reveal, limit)


# ---- One photo --------------------------------------------------------------------------

def _row(conn, photo_id):
    found = inspection.row(conn, photo_id)
    if found is None:
        raise NotFound("There is no photo %d." % photo_id)
    return found


def _compared(in_row, in_file, reveal):
    """How two lists differ, as sets: the order ExifTool reads fields in is not a
    difference. Counts, and the values themselves only with `reveal`."""
    row_set, file_set = set(in_row), set(in_file)
    answer = {"same": row_set == file_set, "in_row": len(in_row), "in_file": len(in_file),
              "only_in_row": len(row_set - file_set), "only_in_file": len(file_set - row_set)}
    if reveal:
        answer.update(row=list(in_row), file=list(in_file),
                      only_in_row_values=sorted(row_set - file_set), only_in_file_values=sorted(file_set - row_set))
    return answer


def _as_stored(value):
    """A value as it reads back from the row's JSON, so the two sides compare alike."""
    return json.loads(json.dumps(value, default=str))


def _by_field(raw):
    """Raw metadata keyed by field name alone. A fresh read keeps each field twice,
    group-prefixed and bare (`XMP:Subject`, `Subject`); most stored rows hold the prefixed
    spelling only, so comparing the dicts as they are called nearly every row stale."""
    fields = {}
    for key, value in raw.items():
        fields.setdefault(key.split(":", 1)[-1], value)
    return fields


def photo_against_file(library, photo_id, exiftool_path=None, reveal=False):
    """A photo's row against what its file holds now, read with ExifTool the way the
    indexer reads it: whether the file is there; its modified time and size; its tags,
    captions and the people its keywords name; its DocumentID; and which raw metadata
    fields differ (field names, not values). Counts, and the values only with `reveal`.
    The file is only read."""
    with _reading(library) as conn:
        row = _row(conn, photo_id)
        known = taxonomy.read_people_vocabulary(conn)
        keyword_people = [name for name, source in inspection.people_of(conn, photo_id) if source == "keyword"]
    answer = {"photo": photo_id, "file_exists": os.path.exists(row["path"])}
    if reveal:
        answer["path"] = row["path"]
    if not answer["file_exists"]:
        answer["stale"] = True
        return answer
    record = MetadataExtractor(exiftool_path).batch_read([row["path"]], people=known)[0]
    if "document_id" not in record:
        # ExifTool could not be started, or could not read this file: the extractor
        # answers an empty record, which is no statement about what the file holds.
        answer.update(file_read=False, stale=None)
        return answer
    answer["file_read"] = True
    in_row, in_file = _by_field(row["raw_metadata"]), _by_field(_as_stored(record["raw_metadata"]))
    # A field one side lacks is not a difference: rows were written by readers that kept
    # different fields, and only what both hold can disagree.
    differing = sorted(k for k in set(in_row) & set(in_file) if in_row[k] != in_file[k])
    answer.update(
        mtime={"same": row["mtime"] is not None and abs(row["mtime"] - record["mtime"]) < 0.1,
               "seconds_apart": None if row["mtime"] is None else round(record["mtime"] - row["mtime"], 3)},
        size={"same": row["size"] == record["size"], "row": row["size"], "file": record["size"]},
        tags=_compared(row["tags"], record["tags"], reveal),
        captions=_compared(row["captions"], record["captions"], reveal),
        keyword_people=_compared(keyword_people, record["people"], reveal),
        document_id={"same": (row["document_id"] or None) == (record["document_id"] or None),
                     "in_row": bool(row["document_id"]), "in_file": bool(record["document_id"])},
        raw_metadata={"same": not differing, "fields_differing": differing,
                      "only_in_row": len(set(in_row) - set(in_file)),
                      "only_in_file": len(set(in_file) - set(in_row))})
    answer["stale"] = not all(answer[k]["same"] for k in
                              ("mtime", "size", "tags", "captions", "keyword_people", "document_id", "raw_metadata"))
    return answer


def faces_in_photo(library, photo_id, reveal=False):
    """The faces in one photo, in detection order: each one's id, box, whether it is named
    and who named it (a person, or clustering), how sure detection was, and whether it
    is excluded and why. Names only with `reveal`. Never the embedding or the crop."""
    with _reading(library) as conn:
        _row(conn, photo_id)
        found = inspection.faces_of(conn, photo_id)
        listed = len(inspection.people_of(conn, photo_id))
    faces = []
    for face_id, box_json, name, source, prob, excluded, reason in found:
        try:
            box = json.loads(box_json) if box_json else None
        except (TypeError, ValueError):
            box = None
        face = {"id": face_id, "box": box, "named": bool(name), "name_source": source,
                "prob": prob, "excluded": bool(excluded), "excluded_reason": reason}
        if reveal:
            face["name"] = name
        faces.append(face)
    return {"photo": photo_id, "count": len(faces), "named": sum(f["named"] for f in faces),
            "excluded": sum(f["excluded"] for f in faces), "people_listed": listed, "faces": faces}


# ---- The checks -------------------------------------------------------------------------

def _reported(conn, name, check, reveal):
    """One Check as an answer: its count, and its examples as ids, or as they are when
    they name nobody, or with `reveal`."""
    answer = {"check": name, "rule": check.name, "count": check.count}
    examples = list(check.examples)
    if name in PLAIN_EXAMPLES or all(isinstance(e, int) for e in examples):
        answer["examples"] = examples
        return answer
    if name in PATH_EXAMPLES:
        found = inspection.ids_of_stored(conn, examples)
        answer["example_ids"] = [found[e] for e in examples if e in found]
    elif name in KEY_EXAMPLES:
        answer["example_ids"] = inspection.ids_of_files(conn, examples)
    if reveal:
        answer["examples"] = examples
    return answer


def check(library, name, reveal=False):
    """One of tools/doctor.py's rules (tagpup.store.checks), by its name: how many rows
    break it, and a few of them as ids -- paths and tags only with `reveal`."""
    rule = CHECKS.get(name)
    if rule is None:
        raise Refused("There is no check called %r; the checks are %s." % (name, ", ".join(CHECKS)))
    with _reading(library) as conn:
        return _reported(conn, name, rule(conn), reveal)


def all_checks(library, reveal=False, embedder_settings=None):
    """Every rule tools/doctor.py checks, in its order, and how many are broken; with the
    CLIP model's settings, how many photos have no vector for it (reported, not broken:
    the next index computes them). Rows whose file is gone are `missing_files`'s."""
    with _reading(library) as conn:
        results = [_reported(conn, rule.__name__, rule(conn), reveal) for rule in rules.RULES]
        answer = {"broken": sum(1 for r in results if r["count"]), "checks": results}
        if embedder_settings is not None:
            answer["without_a_vector"] = rules.without_a_vector(conn, embeddings.model_key(**embedder_settings))
    return answer


# ---- Rows whose file is gone ------------------------------------------------------------

def missing_files(library, reveal=False, limit=LIMIT):
    """The rows whose file is not on disk, by folder, and what they carry: for each folder
    whether it is gone entirely, its rows (as ids), its faces, those named, named by hand
    and excluded, and how many different names sit on them -- which answers #42's
    question, which names sit on rows of a folder whose files are gone. Folder paths
    and the names only with `reveal`. Missing rows are reported, never removed: a
    folder on an unplugged drive looks the same as a deleted one."""
    with _reading(library) as conn:
        gone = inspection.whose_file_is_gone(conn)
        faces = inspection.faces_on(conn, [photo_id for photo_id, _path in gone])
    by_folder = {}
    for photo_id, path in gone:
        folder = os.path.dirname(path)
        by_folder.setdefault(paths.key(folder), (folder, []))[1].append(photo_id)
    listed = []
    for key in sorted(by_folder, key=lambda k: (-len(by_folder[k][1]), k)):
        folder, ids = by_folder[key]
        on_them = [face for photo_id in ids for face in faces.get(photo_id, [])]
        names = sorted({name for _id, name, _source, _excluded in on_them if name})
        entry = {"folder": len(listed) + 1, "folder_gone": not os.path.isdir(folder), "rows": len(ids),
                 "ids": ids[:max(0, limit)], "faces": len(on_them),
                 "named": sum(1 for f in on_them if f[1]),
                 "named_by_hand": sum(1 for f in on_them if f[1] and f[2] == "manual"),
                 "excluded": sum(1 for f in on_them if f[3]),
                 "photos_with_a_name": sum(1 for photo_id in ids if any(f[1] for f in faces.get(photo_id, []))),
                 "names": len(names)}
        if reveal:
            entry["path"] = folder
            entry["name_list"] = names
        listed.append(entry)
    return {"rows": len(gone), "folders": len(listed),
            "folders_gone": sum(1 for f in listed if f["folder_gone"]),
            "named": sum(f["named"] for f in listed), "names": len({
                name for faces_of in faces.values() for _id, name, _s, _e in faces_of if name}),
            "by_folder": listed}


# ---- Query plans ------------------------------------------------------------------------

def query_plan(library, sql, params=None):
    """The plan SQLite would follow for `sql` on this library (EXPLAIN QUERY PLAN), as
    rows of (id, parent, detail): which index each step uses, or that it scans. The
    statement is never run. Refused unless it is a single SELECT or WITH statement that
    only reads. A placeholder without a value is planned as NULL."""
    with _reading(library) as conn:
        try:
            plan = inspection.query_plan(conn, sql, params)
        except inspection.NotARead as e:
            raise Refused(str(e)) from e
    return {"plan": [{"id": i, "parent": parent, "detail": detail} for i, parent, _unused, detail in plan],
            "scans": [detail for _i, _p, _u, detail in plan if detail.startswith("SCAN")]}
