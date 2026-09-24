# tagpup_server.py
import os
import json
import sqlite3
try:
    from . import db as tagpup_db
    from . import localserver
    from . import paths
except ImportError:  # imported as a top-level module
    import db as tagpup_db
    import localserver
    import paths
import urllib.parse
import io
import logging
import re
import threading
import subprocess
from http.server import BaseHTTPRequestHandler
from PIL import Image, ImageOps
Image.MAX_IMAGE_PIXELS = 500000000
import numpy as np

import _root  # noqa: F401
from tagpup import config as tagpup_config

logger = logging.getLogger("tagpup.server")

# A photo path has two spellings, and both come from tagpup/core/paths.py: paths.key() for
# the in-memory caches, paths.stored() for the index, the browser and the disk. This
# file used to have its own pair, and the one for the index turned the native paths the
# indexer writes into forward slashes, so every lookup made through it on Windows
# matched nothing: tag writes never reached the index, renames orphaned their rows and
# faces, deleted photos kept theirs, and the folder scan never found its cached
# metadata. The tests seeded their rows through that same function, so they agreed with
# it and not with the data.

_INDEXER_TQDM = re.compile(r"^(.*?):\s*(\d+)%\|[^|]*\|\s*(\d+)/(\d+)")

#: Longest message worth putting on a progress bar. Past this it is ellipsised in
#: the page anyway, so a truncated sentence is all anyone can read.
_INDEXER_MAX_MESSAGE = 90

#: The shapes library chatter arrives in. These are forms, not particular messages:
#: blacklisting the text of one warning only waits for the next library to add one.
_INDEXER_NOISE = (
    # "2026-09-19 21:31:50,515 [INFO] root - Instantiating..."
    re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+\s+\["),
    # "WARNING:huggingface_hub.utils._http:Warning: You are sending..."
    re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL):[\w.]+:"),
    # "Warning: You are sending unauthenticated requests to the HF Hub."
    # Printed bare, with no prefix at all, which is how it reached the progress bar.
    re.compile(r"^(User|Future|Deprecation|Runtime|Import|Resource)?Warning:", re.I),
    # warnings.warn's source line, and the echoed statement under it.
    re.compile(r"^.*:\d+:\s*\w*Warning:"),
    re.compile(r"^\s*warnings\.warn\("),
    # A bare traceback frame, which without its exception says nothing useful here.
    # The line is stripped before matching, so its indentation is already gone.
    re.compile(r"^File \".*\", line \d+"),
)


def summarize_indexer_line(line):
    """Turn one line of indexer output into progress text, or None to ignore it.

    The indexer's stdout carries three kinds of line: console output written for a
    person, tqdm progress bars, and library chatter. Only the first two say anything
    about progress, and the third is the bulk of it -- model loading alone logs
    dozens of lines nobody watching a progress bar wants.
    """
    if not line:
        return None
    # tqdm redraws with carriage returns; only the newest frame matters.
    clean = line.split("\r")[-1].strip()
    if not clean:
        return None
    if any(pattern.match(clean) for pattern in _INDEXER_NOISE):
        return None

    match = _INDEXER_TQDM.match(clean)
    if match:
        label, percent, done, total = match.groups()
        return f"{label.strip()}: {percent}% ({done}/{total})"

    if len(clean) > _INDEXER_MAX_MESSAGE:
        return clean[:_INDEXER_MAX_MESSAGE - 1].rstrip() + "\u2026"
    return clean


def index_folder_with_cli(folder_path, db_path, run_clustering, status, while_clustering=None):
    """Index a folder through the CLI, then optionally resolve faces, filling `status`.

    Both apps ran this as their own copy, and both reported "identities resolved" when
    cluster-faces had failed: its exit code was never read. The CLI is started from
    the repository, as TagTuner's copy did -- TagPup's relied on the server's working
    directory to find tagpup_cli.py. Returns True when every step succeeded.

    `while_clustering`, if given, is a context held while cluster-faces runs: TagTuner
    refuses assignments during it, as it does during Recluster, since clustering
    rewrites the names an assignment would be setting.
    """
    import sys
    from contextlib import nullcontext

    env = os.environ.copy()
    env["TAGPUP_DB_PATH"] = db_path
    workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def run(args, scale):
        proc = subprocess.Popen(
            [sys.executable, "tagpup_cli.py"] + args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, bufsize=1, cwd=workspace,
        )
        with proc.stdout:
            for line in iter(proc.stdout.readline, ""):
                clean = summarize_indexer_line(line)
                if clean:
                    status["message"] = clean
                    match = re.search(r"(\d+)%", clean) if scale else None
                    if match:
                        status["percent"] = int(float(match.group(1)) * scale)
        proc.wait()
        return proc.returncode

    # The indexer stores the paths it walks as given, so it is handed the stored form.
    code = run(["index", paths.stored(folder_path)], 0.9)
    if code != 0:
        status.update(status="failed", percent=0,
                      message="Indexing failed with exit code %s." % code)
        return False

    if run_clustering:
        # Only on explicit request: this re-derives every face name in the database,
        # not just the folder that was indexed.
        status.update(message="Resolving and matching face identities...", percent=95)
        with (while_clustering() if while_clustering else nullcontext()):
            code = run(["cluster-faces"], None)
        if code != 0:
            status.update(status="failed", percent=100,
                          message="Folder indexed, but resolving face identities failed "
                                  "(exit code %s). Run Recluster to try again." % code)
            return False

    status.update(status="completed", percent=100, message=(
        "Folder indexed and face identities resolved." if run_clustering
        else "Folder indexed. Faces detected; run Recluster to assign identities."))
    return True


def expand_tag_fields(tags):
    """Split a tag list into the flat and hierarchical keyword forms written to files.

    A tag is written whole. It used to be written whole *and* broken into its
    segments, so "Family/Immediate/Cora Ingersoll" became four keywords -- the path plus
    "Family", "Immediate" and "Cora Ingersoll". That buries a deliberate hierarchy under
    its own fragments, and the bare leaf is the form that gave one person two entries
    in the Add Person list.

    The convention comes from the library rather than from a default: of 18,502
    keyword values in this one, 18,364 are full paths separated by "/" and none are
    bare leaves.
    """
    flat, hierarchical = [], []
    for tag in tags:
        if tag not in flat:
            flat.append(tag)
        if "/" in tag and tag not in hierarchical:
            hierarchical.append(tag)
    return flat, hierarchical

#: One loaded taxonomy per database, so resolving on every write costs nothing after
#: the first. Each entry is (the tree's generation when it was read, the mapping):
#: this process clears it when it writes the tree (invalidate_people_cache), and the
#: generation catches the edits it cannot see -- TagTuner's, in another process.
_people_cache = {}
_people_cache_guard = threading.Lock()


def _taxonomy_generation_of(db_path):
    from index import taxonomy_generation

    try:
        conn = tagpup_db.connect(tagpup_db.readonly_uri(db_path), uri=True)
    except Exception:
        return None
    try:
        return taxonomy_generation(conn)
    finally:
        conn.close()


def people_paths_for(db_path):
    """Every person the taxonomy names, keyed by their lowercased leaf name."""
    if not db_path:
        return {}
    key = paths.key(str(db_path))
    generation = _taxonomy_generation_of(db_path) if os.path.exists(db_path) else None
    with _people_cache_guard:
        cached = _people_cache.get(key)
    # A generation that cannot be read says nothing changed; what was read stands.
    if cached is not None and (generation is None or cached[0] == generation):
        return cached[1]

    mapping = {}
    try:
        from taxonomy import TagTaxonomy

        taxonomy = TagTaxonomy(db_path=db_path)
        taxonomy.load()
        roots = taxonomy.people_roots()
        for tag_path in taxonomy.paths:
            if "/" not in tag_path:
                continue
            if tag_path.split("/")[0].strip().lower() not in roots:
                continue
            leaf = tag_path.split("/")[-1].strip().lower()
            # Someone filed in two places cannot be resolved without guessing, so
            # they are left alone rather than filed in whichever came first.
            mapping[leaf] = None if leaf in mapping and mapping[leaf] != tag_path else tag_path
        mapping = {k: v for k, v in mapping.items() if v}
    except Exception as e:
        logger.debug("Could not load people paths from %s: %s", db_path, e)

    with _people_cache_guard:
        _people_cache[key] = (generation, mapping)
    return mapping


def invalidate_people_cache(db_path=None):
    """Forget the cached taxonomy, after something changed it."""
    with _people_cache_guard:
        if db_path is None:
            _people_cache.clear()
        else:
            _people_cache.pop(paths.key(str(db_path)), None)


def resolve_people_tags(tags, db_path):
    """Give every person in `tags` the path they are filed under.

    The last line of defence, and deliberately at the write boundary rather than at
    each caller. A person's name reaches this program as a leaf from half a dozen
    directions -- the faces table, CLIP suggestions, neighbour propagation, a typed
    name -- and each of those paths resolving it for itself is exactly how "Hailey
    Brookmire" kept being written beside "People/Hazel Brookmire". One of them
    always gets missed; folder auto-apply was the one that outlived three fixes.

    Only a bare tag whose name matches somebody already in the people taxonomy is
    touched. A flat keyword that is not a person -- "Cross Country", "Kentridge" --
    is legitimate and is left exactly as it is.
    """
    people = people_paths_for(db_path)
    if not people:
        return list(tags)

    pathed_leaves = {t.split("/")[-1].strip().lower() for t in tags if "/" in t}

    resolved = []
    for tag in tags:
        if "/" in tag:
            if tag not in resolved:
                resolved.append(tag)
            continue
        low = str(tag).strip().lower()
        # A leaf duplicating a path already on this photo is simply dropped.
        if low in pathed_leaves:
            continue
        person = people.get(low)
        target = person or tag
        if target not in resolved:
            resolved.append(target)
    return resolved


def keyword_fields(flat, hierarchical):
    """Every field a keyword write sets, and what it sets it to.

    The one list of them. write_keyword_fields writes exactly these, and
    record_keyword_fields records exactly these, so the file and its index row cannot
    drift apart one field at a time. They did: the index recorded the two XMP fields
    and not IPTC:Keywords, which metadata.extract_tags also reads, so a tag removed in
    bulk stayed in raw_metadata and came back the next time anything re-derived tags
    from it -- renaming an unrelated tag, for one.

    An empty value means the field is cleared.
    """
    return {
        "XMP:Subject": list(flat),
        "IPTC:Keywords": list(flat),
        "EXIF:XPKeywords": ";".join(flat),
        "XMP:HierarchicalSubject": list(hierarchical),
    }


def record_keyword_fields(raw_meta, flat, hierarchical):
    """Make `raw_meta` say what a keyword write just put in the file. Returns it.

    Only fields the scan reads are recorded, so a row written here looks the same as
    one read back from the file. A field under its bare name ("Keywords") is the same
    value the scan stored twice, and is rewritten too; a stale copy there would be
    read back just the same. A cleared field is removed, as a scan would find nothing.
    """
    from metadata import METADATA_FIELDS

    for field, value in keyword_fields(flat, hierarchical).items():
        if field not in METADATA_FIELDS:
            continue
        bare = field.split(":", 1)[1]
        for name in (field, bare):
            if name != field and name not in raw_meta:
                continue
            if value:
                raw_meta[name] = list(value)
            else:
                raw_meta.pop(name, None)
    return raw_meta


def record_tags_in_index(db_path, photo_path, tags, flat=None, hierarchical=None):
    """Tell the index what a photo's keywords now are.

    Saving one photo has always done this; the bulk writers did not, so tagging fifty
    photos left fifty index rows describing what they used to hold. Nothing in the app
    showed the difference -- the folder cache was updated, so the screen was right --
    which is how it went unnoticed until a repair script, planning from the index,
    reported nothing to do on a folder that had just been tagged wholesale.

    `flat` and `hierarchical` are what was actually written to the file, as
    write_keyword_fields returns them, so raw_metadata keeps agreeing with the photo.
    Left out, they are what write_keyword_fields would have written for `tags`.

    The file's new mtime and size are recorded too. Writing keywords changes both, and
    the folder scan only trusts a row whose mtime and size match the file; without
    them every photo tagged in bulk was re-read with ExifTool on every scan after.
    """
    from metadata import photo_people

    if flat is None and hierarchical is None:
        flat, hierarchical = expand_tag_fields(tags)
    where, where_params = paths.sql_equals("path", photo_path)
    try:
        stat = os.stat(photo_path)
    except OSError:
        stat = None

    def store(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT rowid, raw_metadata FROM photos WHERE " + where, where_params)
        row = cursor.fetchone()
        if not row:
            return False   # never indexed; adding it here would be an index, not an edit
        rowid, raw_json = row

        try:
            raw_meta = json.loads(raw_json) if raw_json else {}
        except Exception:
            raw_meta = {}
        record_keyword_fields(raw_meta, flat or [], hierarchical or [])

        people = photo_people(raw_meta, tags, photo_path, db_path=db_path, conn=conn)
        if stat is None:
            cursor.execute(
                "UPDATE photos SET tags = ?, people = ?, raw_metadata = ? WHERE rowid = ?",
                (json.dumps(tags), json.dumps(people), json.dumps(raw_meta), rowid),
            )
        else:
            cursor.execute(
                "UPDATE photos SET tags = ?, people = ?, raw_metadata = ?, mtime = ?, size = ?"
                " WHERE rowid = ?",
                (json.dumps(tags), json.dumps(people), json.dumps(raw_meta),
                 stat.st_mtime, stat.st_size, rowid),
            )
        return cursor.rowcount > 0

    try:
        return tagpup_db.write_with_connection(
            db_path, store, label="index row for %s" % os.path.basename(photo_path)
        )
    except Exception as e:
        # The file is already written and correct; a stale index row is recoverable.
        logger.warning("Could not update the index for %s: %s", photo_path, e)
        return False


def write_keyword_fields(et, path, tags, extra_params=None, db_path=None):
    """Write `tags` into a photo's keyword fields, clearing fields that end up empty.

    ExifTool treats an empty list as "no change", so assigning [] silently leaves the
    old keywords in place. Removing a photo's last tag therefore has to be expressed as
    an explicit '-TAG=' deletion instead.

    Pass `db_path` and a person named by a bare leaf is written as the tag they are
    filed under instead. Every write path through this function should pass it.
    """
    if db_path:
        tags = resolve_people_tags(tags, db_path)
    flat, hierarchical = expand_tag_fields(tags)

    params = dict(extra_params or {})
    clear_args = []

    # The fields come from keyword_fields(), which record_keyword_fields() also reads,
    # so whatever is written here is what the index records. An empty string clears a
    # field as written; an empty list does not, and needs the explicit deletion.
    for field, value in keyword_fields(flat, hierarchical).items():
        if value or isinstance(value, str):
            params[field] = value
        else:
            clear_args.append("-%s=" % field)

    if params:
        et.set_tags([path], tags=params, params=["-overwrite_original"])
    if clear_args:
        et.execute(*clear_args, "-overwrite_original", path)

    return flat, hierarchical

def indexed_tags_for_photo(db_path, photo_path):
    """Tags recorded for a photo in the index, used when the folder cache is cold.

    Bulk writes overwrite a photo's whole keyword set, so starting from an empty list
    because nothing was cached would erase tags the photo already carries.
    """
    try:
        conn = tagpup_db.connect(db_path, timeout=10.0)
        try:
            where, where_params = paths.sql_equals("path", photo_path)
            row = conn.execute("SELECT tags FROM photos WHERE " + where, where_params).fetchone()
        finally:
            conn.close()
        if row and row[0]:
            return json.loads(row[0])
    except Exception as e:
        logger.warning(f"Could not read indexed tags for {photo_path}: {e}")
    return []


#: The fields a photo's tags are read from: exactly what metadata.extract_tags reads,
#: so tags read here are the tags a folder scan would have found.
TAG_SOURCE_FIELDS = ("IPTC:Keywords", "XMP:Subject", "XMP:HierarchicalSubject")


def tags_in_file(et, photo_path):
    """The tags a photo carries now, read from the file itself.

    The bulk writers replace a photo's whole keyword set, so they must start from
    what it holds. They used to start from the folder cache, then the index, then
    nothing: the cache is empty after a restart while the page still shows the folder,
    and a photo the index has no row for then kept only the tags being added. The
    file is the truth; it is read in the ExifTool session the writer already has open.
    Raises if the file cannot be read, rather than treat it as having no tags.
    """
    from metadata import clean_metadata_value, extract_tags

    found = et.get_tags([photo_path], tags=list(TAG_SOURCE_FIELDS))
    meta = found[0] if found else {}
    return extract_tags({k: clean_metadata_value(v) for k, v in meta.items()})


def record_file_stat_in_index(db_path, photo_path):
    """Record a photo's current mtime and size in its index row. Returns rows changed.

    For a write that changes the file but not what the index describes -- a rotation
    changes only the Orientation tag. Left stale, the folder scan would distrust the
    row and re-read the photo with ExifTool on every scan.
    """
    stat = os.stat(photo_path)
    where, where_params = paths.sql_equals("path", photo_path)

    def store(conn):
        cursor = conn.execute("UPDATE photos SET mtime = ?, size = ? WHERE " + where,
                              (stat.st_mtime, stat.st_size) + where_params)
        return cursor.rowcount

    return tagpup_db.write_with_connection(
        db_path, store, label="file stat for %s" % os.path.basename(photo_path))


def zero_shot_candidates(taxonomy, configured):
    """The words CLIP is asked about: config.ini's, plus every leaf that is not a person.

    People are matched by their faces, not by asking CLIP whether a photo looks like
    "a photo of Rowan Thackeray". The roots skipped were written out as family,
    friends and pets, so everyone under People -- and under any face root a library
    made for itself -- was offered to CLIP by name. The library's own face roots are
    used now.
    """
    face_roots = taxonomy.people_roots() | {"pets"}
    candidates = list(configured)
    seen = {c.lower() for c in candidates}
    for path in sorted(taxonomy.paths):
        parts = [p.strip() for p in path.split("/") if p.strip()]
        if not parts or parts[0].lower() in face_roots:
            continue
        leaf = parts[-1]
        if leaf.lower() not in seen:
            seen.add(leaf.lower())
            candidates.append(leaf)
    return candidates


def shift_photo_times(db_path, exiftool_path, photo_paths, shift_minutes):
    """Move Date Taken in each photo by `shift_minutes`, and tell the index.

    Returns (how many files ExifTool reports it updated, the photos re-read after).
    The count is ExifTool's own: a photo it could not write is not counted, where
    this used to answer with the number it had tried. The index rows get the new
    Date Taken, which orders photos and picks the era a face is compared against,
    and the file's new mtime and size, without which the next scan distrusts them.
    """
    from exiftool_session import ExifToolSession
    from metadata import MetadataExtractor

    sign = "+" if shift_minutes >= 0 else "-"
    by = "0:0:0 0:%d:0" % abs(shift_minutes)
    updated = 0
    # check_execute=False: a batch with one unwritable photo still shifts the rest,
    # and ExifTool's summary line says how many it did.
    with ExifToolSession(executable=exiftool_path, check_execute=False) as et:
        for i in range(0, len(photo_paths), 50):
            out = et.execute("-DateTimeOriginal%s=%s" % (sign, by), "-CreateDate%s=%s" % (sign, by),
                             "-overwrite_original", *photo_paths[i:i + 50])
            updated += sum(int(n) for n in re.findall(r"(\d+) image files? updated", out or ""))

    # Only reading: minting a DocumentID here would write the files a second time.
    entries = MetadataExtractor(exiftool_path=exiftool_path, mint_identities=False).batch_read(
        photo_paths, db_path=db_path)

    def store(conn):
        for entry in entries:
            if not entry.get("raw_metadata"):
                continue
            where, where_params = paths.sql_equals("path", entry["path"])
            conn.execute("UPDATE photos SET raw_metadata = ?, mtime = ?, size = ? WHERE " + where,
                         (json.dumps(entry["raw_metadata"]), entry.get("mtime", 0.0),
                          entry.get("size", 0)) + where_params)

    tagpup_db.write_with_connection(db_path, store, label="time shift")
    return updated, entries


def turned_box(box, direction, width, height):
    """A face box after a quarter turn of a `width` x `height` image.

    Left is counter-clockwise, as Image.rotate(90, expand=True) turns it.
    """
    x1, y1, x2, y2 = box[:4]
    if direction == "left":
        return [y1, width - x2, y2, width - x1]
    return [height - y2, x1, height - y1, x2]


def turn_face_boxes(db_path, photo_path, direction, width, height):
    """Turn a photo's stored face boxes with it. Returns how many rows changed.

    Only for a photo Pillow shows already oriented (a TIFF: Pillow applies its
    Orientation on load), where every box -- and every crop cut from it -- is in the
    turned picture's coordinates once the Orientation changes. The cached crop is
    dropped so the next request cuts it again from the right place.
    """
    where, where_params = paths.sql_equals("photo_path", photo_path)

    def turn(conn):
        cursor = conn.cursor()
        rows = cursor.execute("SELECT id, box FROM faces WHERE " + where, where_params).fetchall()
        changed = 0
        for face_id, box_json in rows:
            try:
                box = json.loads(box_json)
            except (TypeError, ValueError):
                continue
            if not isinstance(box, list) or len(box) < 4:
                continue
            cursor.execute("UPDATE faces SET box = ?, crop_image = NULL WHERE id = ?",
                           (json.dumps(turned_box(box, direction, width, height)), face_id))
            changed += cursor.rowcount
        return changed

    return tagpup_db.write_with_connection(
        db_path, turn, label="face boxes for rotated %s" % os.path.basename(photo_path))


def forget_photo_in_index(db_path, photo_path):
    """Remove a deleted photo's row, its faces and its cached embedding.

    Returns how many rows of each were removed. The faces are deleted by name rather
    than left to the foreign key's cascade: db.connect() does not turn foreign keys
    on, and a face left behind points at a photo that no longer exists.
    """
    def forget(conn):
        cursor = conn.cursor()
        removed = {}
        for table, column in (("faces", "photo_path"), ("photos", "path"),
                              ("embedding_cache", "path")):
            where, params = paths.sql_equals(column, photo_path)
            cursor.execute("DELETE FROM %s WHERE %s" % (table, where), params)
            removed[table] = cursor.rowcount
        return removed

    removed = tagpup_db.write_with_connection(
        db_path, forget, label="index rows for deleted %s" % os.path.basename(photo_path))
    if not removed.get("photos"):
        logger.info("Deleted %s, which the index had no row for.", photo_path)
    return removed


def move_photo_rows(db_path, renames):
    """Move index rows from each old path to its new one, and its faces with them.

    `renames` maps old path to new path, in any spelling. Returns (moved, skipped):
    how many photo rows actually changed, and the (old, new) pairs left where they
    were because the new path already had rows. Re-pointing rows at a path that
    already has them is how 233 duplicate faces were made, so a destination is
    checked first and an occupied one is reported rather than merged into.

    A destination is free when nothing is there, when it is the same file (a rename
    that only changes case), or when whatever is there is itself moving away in this
    same call -- a rename that shuffles numbered files among themselves, or the
    occupant of a name that was moved aside to make room. The rows are moved in one
    transaction, by rowid, through a placeholder, so a shuffle never collides with
    itself on the way.
    """
    def rows_at(cursor, table, column, id_column, path):
        where, params = paths.sql_equals(column, path)
        return [r[0] for r in cursor.execute(
            "SELECT %s FROM %s WHERE %s" % (id_column, table, where), params)]

    def move(conn):
        cursor = conn.cursor()
        plan = dict(renames)
        skipped = []
        # Settle what moves first: skipping one rename can make another's
        # destination occupied, so repeat until nothing changes.
        changed = True
        while changed:
            changed = False
            leaving = {paths.key(old) for old in plan}
            arriving = set()
            for old_path, new_path in list(plan.items()):
                new_key = paths.key(new_path)
                clash = new_key in arriving
                if not clash and not paths.same(old_path, new_path) and new_key not in leaving:
                    clash = bool(rows_at(cursor, "photos", "path", "rowid", new_path)
                                 or rows_at(cursor, "faces", "photo_path", "id", new_path))
                if clash:
                    skipped.append((old_path, new_path))
                    del plan[old_path]
                    changed = True
                    break
                arriving.add(new_key)

        has_cache = bool(cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'embedding_cache'").fetchone())
        staged = []
        for n, (old_path, new_path) in enumerate(plan.items()):
            photo_ids = rows_at(cursor, "photos", "path", "rowid", old_path)
            face_ids = rows_at(cursor, "faces", "photo_path", "id", old_path)
            # The cached CLIP embedding too: a rename keeps the file's mtime and size,
            # which is what the cache is checked against, so it is still good. Left
            # behind, the renamed photo was embedded again from scratch.
            cache_ids = (rows_at(cursor, "embedding_cache", "path", "rowid", old_path)
                         if has_cache else [])
            # "<" cannot appear in a Windows file name, and this never outlives
            # the transaction.
            placeholder = "<moving %d>" % n
            cursor.executemany("UPDATE photos SET path = ? WHERE rowid = ?",
                               [(placeholder, rowid) for rowid in photo_ids])
            if cache_ids:
                cursor.executemany("UPDATE embedding_cache SET path = ? WHERE rowid = ?",
                                   [(placeholder, rowid) for rowid in cache_ids])
            staged.append((paths.stored(new_path), photo_ids, face_ids, cache_ids))

        moved = 0
        for new_stored, photo_ids, face_ids, cache_ids in staged:
            for rowid in photo_ids:
                cursor.execute("UPDATE photos SET path = ? WHERE rowid = ?", (new_stored, rowid))
                moved += cursor.rowcount
            cursor.executemany("UPDATE faces SET photo_path = ? WHERE id = ?",
                               [(new_stored, face_id) for face_id in face_ids])
            if cache_ids:
                # Whatever was cached under the new name described another file; it
                # is only derived data, keyed by path, and would block the move.
                where, params = paths.sql_equals("path", new_stored)
                cursor.execute("DELETE FROM embedding_cache WHERE " + where, params)
                cursor.executemany("UPDATE embedding_cache SET path = ? WHERE rowid = ?",
                                   [(new_stored, rowid) for rowid in cache_ids])
        return moved, skipped

    moved, skipped = tagpup_db.write_with_connection(
        db_path, move, label="index rows for %d renamed photo(s)" % len(renames))
    for old_path, new_path in skipped:
        logger.warning("Renamed %s to %s, but the index already has rows for the new "
                       "name; left both as they were.", old_path, new_path)
    return moved, skipped

#: The files this app treats as photos.
PHOTO_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"})


def explorer_select_command(photo_path):
    """The command line that opens Explorer with this photo selected.

    Explorer parses its own command line and wants the path quoted after the
    switch -- /select,"D:\\a b\\c.jpg" -- not the whole switch quoted, which is what
    passing ["explorer.exe", "/select,<path>"] produced for any path with a space.
    A Windows path cannot contain a quote, so quoting it is safe.
    """
    return 'explorer.exe /select,"%s"' % paths.stored(photo_path)


def send_to_recycle_bin(file_path):
    import ctypes
    from ctypes import wintypes
    
    # SHFileOperationW wants native separators, which is what stored() gives.
    file_path = paths.stored(file_path)
    if not os.path.exists(file_path):
        return False
        
    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]
        
    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x0040
    FOF_NOCONFIRMATION = 0x0010
    FOF_NOERRORUI = 0x0400
    FOF_SILENT = 0x0004
    
    pFrom = file_path + "\0\0"
    
    fileop = SHFILEOPSTRUCTW()
    fileop.hwnd = None
    fileop.wFunc = FO_DELETE
    fileop.pFrom = pFrom
    fileop.pTo = None
    fileop.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
    fileop.fAnyOperationsAborted = False
    fileop.hNameMappings = None
    fileop.lpszProgressTitle = None
    
    SHFileOperationW = ctypes.windll.shell32.SHFileOperationW
    SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    SHFileOperationW.restype = ctypes.c_int
    
    res = SHFileOperationW(ctypes.byref(fileop))
    return res == 0 and not fileop.fAnyOperationsAborted


def make_json_serializable(obj):
    if isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [make_json_serializable(x) for x in obj]
    elif isinstance(obj, (np.float32, np.float64)):
        return float(obj)
    elif isinstance(obj, (np.int32, np.int64)):
        return int(obj)
    elif isinstance(obj, np.ndarray):
        return make_json_serializable(obj.tolist())
    return obj

# Date parsing helpers
YEAR_RE = re.compile(r"^(\d{4})")
DATE_KEYS = [
    "EXIF:DateTimeOriginal", "DateTimeOriginal",
    "XMP:DateTimeOriginal",
    "EXIF:CreateDate", "CreateDate",
    "XMP:CreateDate",
    "EXIF:ModifyDate", "ModifyDate",
    "XMP:ModifyDate"
]

def parse_year_from_raw_metadata(raw_meta):
    if not raw_meta:
        return None
    for key in DATE_KEYS:
        val = raw_meta.get(key)
        if val:
            if isinstance(val, list) and val:
                val = val[0]
            val_str = str(val).strip()
            match = YEAR_RE.match(val_str)
            if match:
                try:
                    year = int(match.group(1))
                    if 1800 <= year <= 2100:
                        return year
                except ValueError:
                    pass
    return None

def get_year_from_mtime_or_meta(mtime, raw_meta_json, path=None):
    parsed_year = None
    if raw_meta_json:
        try:
            if isinstance(raw_meta_json, str):
                raw_meta = json.loads(raw_meta_json)
            else:
                raw_meta = raw_meta_json
            parsed_year = parse_year_from_raw_metadata(raw_meta)
        except Exception:
            pass
                
    if not parsed_year and path:
        # Either separator: the path's segments are all that is wanted here.
        parts = re.split(r"[\\/]", path)
        if parts:
            filename = parts[-1]
            matches = re.findall(r'\d{4}', filename)
            for m in matches:
                val = int(m)
                if 1800 <= val <= 2100:
                    parsed_year = val
                    break
        if not parsed_year and len(parts) > 1:
            for folder in reversed(parts[:-1]):
                if not folder:
                    continue
                matches = re.findall(r'\d{4}', folder)
                for m in matches:
                    val = int(m)
                    if 1800 <= val <= 2100:
                        parsed_year = val
                        break
                if parsed_year:
                    break
                    
    return parsed_year if parsed_year else "Unknown"

_thread_local = threading.local()

def set_active_db_path(db_path):
    if db_path is None:
        if hasattr(_thread_local, "active_db_path"):
            delattr(_thread_local, "active_db_path")
    else:
        _thread_local.active_db_path = os.path.abspath(db_path).replace("\\", "/").lower()  # not a path: the database file, as a registry key


def resolve_library_from_url(handler, set_active):
    """Point `handler` at the library its URL names. False means it has been answered.

    The first part of the path names the library -- /kr-track/api/tags -- and is
    stripped from handler.path. With none, the request goes to the library the server
    started on, and a bare page request is redirected to its URL. Both apps had their
    own copy of this, word for word.

    A named library must exist. Requests used to go ahead against data/<name>.db
    either way, and the first handler to open it created it, so a typo in the address
    bar made an empty library that then sat in the list. Create makes libraries.
    `set_active` is the app's own set_active_db_path: each has its own thread-local.
    """
    parsed_url = urllib.parse.urlparse(handler.path)
    path = parsed_url.path

    data_dir = tagpup_config.data_dir()

    # Determine if we are in test mode based on startup database
    startup_db = os.path.basename(handler.__class__.db_path)
    test_mode = startup_db.startswith("test_")

    db_match = re.match(r"^/([^/]+)(/.*)?$", path)
    if db_match:
        potential_db = db_match.group(1)
        subpath = db_match.group(2) or "/"

        RESERVED_PATHS = {"api", "gui", "gui_tagpup", "index.html", "style.css", "app.js", "favicon.ico", ""}
        if potential_db not in RESERVED_PATHS and not potential_db.endswith((".css", ".js", ".html", ".png", ".jpg", ".jpeg", ".ico")):
            db_name = potential_db + ".db"

            if test_mode:
                if not db_name.startswith("test_"):
                    db_name = "test_" + db_name
            else:
                if db_name.startswith("test_"):
                    db_name = db_name[5:]

            resolved_db_path = os.path.join(data_dir, db_name).replace("\\", "/")  # not a path: a database file
            if not os.path.exists(resolved_db_path) and db_name != startup_db:
                handler.send_error(404, "There is no library called %s" % potential_db)
                return False
            set_active(resolved_db_path)
            handler.db_path = resolved_db_path

            # Rewrite path
            if parsed_url.query:
                handler.path = subpath + "?" + parsed_url.query
            else:
                handler.path = subpath
            return True

    # If path does not contain database subfolder, default to startup database
    db_name = startup_db

    if path in ["/", "/index.html", "/style.css", "/app.js"]:
        clean_url_name = os.path.splitext(db_name)[0]
        if clean_url_name.startswith("test_"):
            clean_url_name = clean_url_name[5:]
        new_path = f"/{clean_url_name}{handler.path}"
        handler.send_response(302)
        handler.send_header("Location", new_path)
        handler.end_headers()
        return False

    resolved_db_path = os.path.join(data_dir, db_name).replace("\\", "/")  # not a path: a database file
    set_active(resolved_db_path)
    handler.db_path = resolved_db_path
    return True

def get_active_db_path():
    active_db = getattr(_thread_local, "active_db_path", None)
    if active_db:
        return active_db
    handler_cls = globals().get("TagPupHTTPRequestHandler")
    if handler_cls:
        try:
            return os.path.abspath(handler_cls.db_path).replace("\\", "/").lower()  # not a path: the database file, as a registry key
        except Exception:
            pass
    return "data/photo_index.db"

class DatabaseIsolatedDict(dict):
    def __init__(self, registry):
        super().__init__()
        self._registry = registry

    def _get_current_dict(self):
        active_db = get_active_db_path()
        # setdefault, not check-then-set: two threads creating the same library's
        # entry at once each made one, and one thread's entries were lost.
        return self._registry.setdefault(active_db, {})

    def __getitem__(self, key):
        return self._get_current_dict()[key]

    def __setitem__(self, key, value):
        self._get_current_dict()[key] = value

    def __delitem__(self, key):
        del self._get_current_dict()[key]

    def __contains__(self, key):
        return key in self._get_current_dict()

    def __len__(self):
        return len(self._get_current_dict())

    def __iter__(self):
        return iter(self._get_current_dict())

    def get(self, key, default=None):
        return self._get_current_dict().get(key, default)

    def pop(self, key, default=None):
        return self._get_current_dict().pop(key, default)

    def update(self, other):
        self._get_current_dict().update(other)

    def values(self):
        return self._get_current_dict().values()

    def keys(self):
        return self._get_current_dict().keys()

    def items(self):
        return self._get_current_dict().items()

    def clear(self):
        self._get_current_dict().clear()

class TagPupHTTPRequestHandlerMeta(type):
    _shared_embedders = {}

    @property
    def shared_embedder(cls):
        db_key = get_active_db_path()
        return cls._shared_embedders.get(db_key)

    @shared_embedder.setter
    def shared_embedder(cls, val):
        db_key = get_active_db_path()
        cls._shared_embedders[db_key] = val

class TagPupHTTPRequestHandler(BaseHTTPRequestHandler, metaclass=TagPupHTTPRequestHandlerMeta):
    db_path = "data/photo_index.db"
    gui_dir = "gui_tagpup"
    
    # Static Class-level caches
    model_lock = threading.Lock()
    
    # Database-specific registries
    _db_folder_cache_registry = {}
    _db_suggest_status_registry = {}
    _db_suggest_threads_registry = {}
    _db_index_status_registry = {}
    _db_index_threads_registry = {}

    folder_cache = DatabaseIsolatedDict(_db_folder_cache_registry)
    suggest_status = DatabaseIsolatedDict(_db_suggest_status_registry)
    suggest_threads = DatabaseIsolatedDict(_db_suggest_threads_registry)
    index_status = DatabaseIsolatedDict(_db_index_status_registry)
    index_threads = DatabaseIsolatedDict(_db_index_threads_registry)

    def log_message(self, format, *args):
        pass # suppress request logs

    @classmethod
    def cached_photo_entries(cls, photo_path):
        """Every folder-cache map holding this photo, as (folder map, record) pairs.

        A scan stores its whole recursive walk under the scanned folder's key, so a
        photo in a subfolder is filed under an ancestor, not under its own directory.
        The writers looked it up under paths.key(dirname(photo)) and never found it:
        its record went on showing what it held before the write. A photo can be in
        more than one map when a folder and a subfolder of it were both scanned; each
        is kept true.
        """
        photo_key = paths.key(photo_path)
        found = []
        for folder_key, folder_map in list(cls.folder_cache.items()):
            if not paths.is_under(photo_path, folder_key):
                continue
            entry = folder_map.get(photo_key)
            if entry is not None:
                found.append((folder_map, entry))
        return found

    def validate_request_origin(self) -> bool:
        # Only this machine, and only pages this server served. See localserver.
        return localserver.is_local_request(self)

    def resolve_db_from_url(self) -> bool:
        # See resolve_library_from_url.
        return resolve_library_from_url(self, set_active_db_path)

    def do_GET(self):
        if not self.resolve_db_from_url():
            return
        if not self.validate_request_origin():
            return
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)

        # Static assets
        if path == "/" or path == "/index.html":
            self.serve_static_file("index.html", "text/html")
        elif path == "/style.css":
            self.serve_static_file("style.css", "text/css")
        elif path == "/app.js":
            self.serve_static_file("app.js", "application/javascript")
        
        # API Endpoints
        elif path == "/api/databases":
            self.handle_get_databases()
        elif path == "/api/browse-folder":
            self.handle_get_browse_folder()
        elif path == "/api/autocomplete-folder":
            self.handle_get_autocomplete_folder(query)
        elif path == "/api/folder/scan":
            self.handle_get_folder_scan(query)
        elif path == "/api/folder/suggest-status":
            self.handle_get_folder_suggest_status(query)
        elif path == "/api/folder/index-status":
            self.handle_get_folder_index_status(query)
        elif path == "/api/photo-faces":
            self.handle_get_photo_faces(query)
        elif path == "/api/face-crop":
            self.handle_serve_face_crop(query)
        elif path == "/api/photo-file":
            self.handle_serve_photo_file(query)
        elif path == "/api/tags":
            self.handle_get_tags()
        elif path == "/api/people":
            self.handle_get_people()
        elif path == "/api/taxonomy/tree":
            self.handle_get_taxonomy_tree()
        else:
            self.send_error(404, "File Not Found")

    def do_POST(self):
        if not self.resolve_db_from_url():
            return
        if not self.validate_request_origin():
            return
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/api/databases/select":
            self.handle_post_databases_select()
        elif path == "/api/databases/create":
            self.handle_post_databases_create()
        elif path == "/api/folder/suggest-start":
            self.handle_post_folder_suggest_start()
        elif path == "/api/folder/index-start":
            self.handle_post_folder_index_start()
        elif path == "/api/photo/rotate":
            self.handle_post_photo_rotate()
        elif path == "/api/photo/delete":
            self.handle_post_photo_delete()
        elif path == "/api/photo/open-explorer":
            self.handle_post_photo_open_explorer()
        elif path == "/api/photo/open":
            self.handle_post_photo_open()
        elif path == "/api/photo/save-metadata":
            self.handle_post_photo_save_metadata()
        elif path == "/api/photos/bulk-tags":
            self.handle_post_photos_bulk_tags()
        elif path == "/api/folder/auto-apply":
            self.handle_post_folder_auto_apply()
        elif path == "/api/folder/time-shift":
            self.handle_post_folder_time_shift()
        elif path == "/api/folder/rename-photos":
            self.handle_post_folder_rename_photos()
        elif path == "/api/taxonomy/create":
            self.handle_post_taxonomy_create()
        elif path == "/api/taxonomy/update":
            self.handle_post_taxonomy_update()
        elif path == "/api/taxonomy/delete-check":
            self.handle_post_taxonomy_delete_check()
        elif path == "/api/taxonomy/delete-confirm":
            self.handle_post_taxonomy_delete_confirm()
        elif path == "/api/taxonomy/rename":
            self.handle_post_taxonomy_rename()
        else:
            self.send_error(404, "Endpoint Not Found")

    def serve_static_file(self, filename, content_type):
        filepath = os.path.join(self.gui_dir, filename)
        if not os.path.exists(filepath):
            self.send_error(404, f"File {filename} not found")
            return
        try:
            with open(filepath, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Error: {e}")

    def send_json(self, data):
        data = make_json_serializable(data)
        content = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(content)

    def send_json_error(self, status_code, message):
        content = json.dumps({"success": False, "error": message}).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(content)

    def get_exiftool_path(self):
        return tagpup_config.exiftool_path()

    def read_json_body(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)
        return json.loads(post_data.decode("utf-8"))

    def handle_get_databases(self):
        settings = tagpup_config.load()
        data_dir = tagpup_config.data_dir(settings)
        default_db = tagpup_config.default_db(settings)

        startup_db = os.path.basename(self.__class__.db_path)
        test_mode = startup_db.startswith("test_")
        
        EXCLUDED_DBS = {
            "validation_index.db",
            "validation_perf.db",
            "multiple_db_startup.db",
            "tag_emb_cache.db"
        }
        
        databases = []
        if os.path.exists(data_dir):
            for file in os.listdir(data_dir):
                if file.endswith(".db"):
                    if file in EXCLUDED_DBS or file.startswith("test_tag_emb_cache.db"):
                        continue
                    if test_mode:
                        if file.startswith("test_"):
                            clean_name = file[5:]
                            if clean_name in EXCLUDED_DBS:
                                continue
                            db_base = os.path.splitext(clean_name)[0]
                            if db_base not in databases:
                                databases.append(db_base)
                    else:
                        if not file.startswith("test_"):
                            db_base = os.path.splitext(file)[0]
                            if db_base not in databases:
                                databases.append(db_base)
                        
        if not databases:
            databases = ["photo_index"]
            
        clean_default_db = os.path.splitext(default_db)[0]
        if clean_default_db.startswith("test_"):
            clean_default_db = clean_default_db[5:]
            
        self.send_json({
            "databases": sorted(databases),
            "selected": clean_default_db
        })

    def handle_post_databases_select(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON body")
            return
            
        db_name = data.get("db_name")
        if not db_name:
            self.send_json_error(400, "Invalid database name")
            return
            
        if not db_name.endswith(".db"):
            db_name = db_name + ".db"
            
        if db_name.startswith("test_"):
            db_name = db_name[5:]
            
        try:
            tagpup_config.remember_library(db_name)
            self.send_json({"success": True})
        except Exception as e:
            self.send_json_error(500, f"Error saving default database: {e}")

    def handle_post_databases_create(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON body")
            return
            
        db_name = data.get("db_name")
        if not db_name:
            self.send_json_error(400, "Invalid database name")
            return
            
        if not db_name.endswith(".db"):
            db_name = db_name + ".db"
            
        if db_name.startswith("test_"):
            db_name = db_name[5:]
            
        import re
        if not re.match(r"^[a-zA-Z0-9_\-]+\.db$", db_name):
            self.send_json_error(400, "Invalid characters in database name")
            return
            
        EXCLUDED_DBS = {
            "validation_index.db",
            "validation_perf.db",
            "multiple_db_startup.db",
            "tag_emb_cache.db"
        }
        if db_name in EXCLUDED_DBS:
            self.send_json_error(400, "Cannot create database with reserved test name")
            return
            
        startup_db = os.path.basename(self.__class__.db_path)
        test_mode = startup_db.startswith("test_")
        
        fs_db_name = db_name
        if test_mode:
            fs_db_name = "test_" + db_name
            
        db_path = tagpup_config.library_path(fs_db_name).replace("\\", "/")  # not a path: a database file

        try:
            if not os.path.exists(db_path):
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
                from index import PhotoIndex
                from taxonomy import seed_taxonomy_from_db
                photo_index = PhotoIndex(db_path=db_path)
                photo_index.load()
                seed_taxonomy_from_db(db_path)

            tagpup_config.remember_library(db_name)
            self.send_json({"success": True, "db_name": os.path.splitext(db_name)[0]})
        except Exception as e:
            self.send_json_error(500, f"Error creating database: {e}")

    def handle_get_folder_index_status(self, query):
        path_list = query.get("path")
        if not path_list:
            self.send_json_error(400, "Missing path parameter")
            return
        folder_path = urllib.parse.unquote(path_list[0])
        folder_path_norm = paths.key(folder_path)
        
        status = self.index_status.get(folder_path_norm, {"status": "completed", "percent": 100, "message": "Ready"})
        self.send_json(status)

    def handle_post_folder_index_start(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        folder_path = data.get("folder_path")
        if not folder_path or not os.path.isdir(folder_path):
            self.send_json_error(400, "Invalid folder path")
            return
            
        folder_path_norm = paths.key(folder_path)
        
        current_status = self.index_status.get(folder_path_norm)
        if current_status and current_status.get("status") == "running":
            self.send_json({"success": True, "status": "running"})
            return
            
        self.index_status[folder_path_norm] = {
            "status": "running",
            "percent": 0,
            "message": "Starting indexing..."
        }
        
        # Re-clustering rewrites every face name in the database from scratch and can
        # discard manual corrections, so it is opt-in rather than a silent side effect
        # of adding a folder. The UI does not request it.
        run_clustering = bool(data.get("cluster", False))

        t = threading.Thread(
            target=self.run_folder_index_thread,
            args=(folder_path, self.db_path, run_clustering),
            name="FolderIndexThread",
            daemon=True
        )
        self.index_threads[folder_path_norm] = t
        t.start()
        
        self.send_json({"success": True, "status": "running"})

    @classmethod
    def run_folder_index_thread(cls, folder_path, db_path, run_clustering=False):
        # Restore the active database in this worker thread. The thread-local set during the
        # request does not carry over, and the class-level fallback points at the startup
        # database, which would resolve the isolated registries to the wrong database.
        set_active_db_path(db_path)
        folder_path_norm = paths.key(folder_path)
        status_dict = cls.index_status.get(folder_path_norm)
        if status_dict is None:
            status_dict = {"status": "running", "percent": 0, "message": "Starting indexing..."}
            cls.index_status[folder_path_norm] = status_dict
        try:
            index_folder_with_cli(folder_path, db_path, run_clustering, status_dict)
            # Rows were written even when clustering failed afterwards.
            if folder_path_norm in cls.folder_cache:
                del cls.folder_cache[folder_path_norm]
        except Exception as e:
            logger.exception(f"Error running folder index thread for {folder_path}: {e}")
            status_dict["status"] = "failed"
            status_dict["message"] = f"Error: {e}"
            status_dict["percent"] = 0

    def handle_get_browse_folder(self):
        try:
            import sys
            python_cmd = (
                "import tkinter as tk; "
                "from tkinter import filedialog; "
                "root = tk.Tk(); "
                "root.withdraw(); "
                "root.lift(); "
                "root.focus_force(); "
                "root.attributes('-topmost', True); "
                "print(filedialog.askdirectory(title='Select Image Folder'))"
            )
            cmd = [sys.executable, "-c", python_cmd]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=0x08000000)
            path = res.stdout.strip()
            self.send_json({"path": path})
        except Exception as e:
            self.send_json_error(500, str(e))

    def handle_get_autocomplete_folder(self, query):
        path_list = query.get("path")
        if not path_list:
            self.send_json([])
            return
        typed_path = urllib.parse.unquote(path_list[0]).strip()
        
        # If empty, return standard drives on Windows
        if not typed_path:
            import string
            drives = []
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    drives.append(drive)
            self.send_json(drives)
            return
            
        # Clean paths (normalizing slashes)
        typed_path = os.path.expandvars(typed_path)
        
        # Handle simple drive letter typing (e.g., "C", "C:")
        if re.match(r'^[a-zA-Z]$', typed_path):
            self.send_json([f"{typed_path.upper()}:\\"])
            return
        if re.match(r'^[a-zA-Z]:$', typed_path):
            self.send_json([f"{typed_path.upper()}\\"])
            return
            
        norm_path = os.path.normpath(typed_path)
        ends_with_sep = typed_path.endswith(("\\", "/"))
        
        if ends_with_sep:
            base_dir = norm_path
            prefix = ""
        else:
            base_dir = os.path.dirname(norm_path)
            prefix = os.path.basename(norm_path).lower()
            
        suggestions = []
        try:
            if os.path.isdir(base_dir):
                for name in os.listdir(base_dir):
                    full_path = os.path.join(base_dir, name)
                    if os.path.isdir(full_path):
                        if not prefix or name.lower().startswith(prefix):
                            suggestions.append(full_path)
        except Exception:
            pass
            
        self.send_json(suggestions[:15])

    def handle_get_photo_faces(self, query):
        """Faces detected on one photo, with the best identity guess for each.

        TagPup previously ran face recognition invisibly: the suggester matched faces
        and surfaced only a name pill, so there was no way to see which face was
        unrecognised while tagging. This backs the face strip in the details panel.

        The similarity reported is to the nearest single resolved face of that person,
        which is the same measure TagTuner's suggestion list uses -- deliberately, so
        the two interfaces agree on how confident a match looks.
        """
        path_list = query.get("path")
        if not path_list:
            self.send_json_error(400, "Missing 'path' parameter")
            return
        photo_path = urllib.parse.unquote(path_list[0])

        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            on_photo, on_photo_params = paths.sql_equals("photo_path", photo_path)
            cursor.execute(
                "SELECT id, box, name, prob, embedding, excluded, excluded_reason"
                " FROM faces WHERE " + on_photo + " ORDER BY id",
                on_photo_params,
            )
            rows = cursor.fetchall()
            if not rows:
                self.send_json({"faces": [], "total": 0})
                return

            # Resolved faces elsewhere in the library, for suggesting a name.
            cursor.execute(
                "SELECT name, embedding FROM faces"
                " WHERE name IS NOT NULL AND excluded = 0 AND NOT (" + on_photo + ")",
                on_photo_params,
            )
            known_names, known_vectors = [], []
            for name, emb in cursor.fetchall():
                if not emb:
                    continue
                vec = np.frombuffer(emb, dtype=np.float32)
                norm = np.linalg.norm(vec)
                if norm > 0:
                    known_names.append(name)
                    known_vectors.append(vec / norm)
            known_matrix = np.array(known_vectors, dtype=np.float32) if known_vectors else None

            faces = []
            for face_id, box_json, name, prob, emb, excluded, reason in rows:
                try:
                    box = json.loads(box_json) if box_json else []
                except Exception:
                    box = []

                # Below this the nearest name is not a suggestion, it is just the
                # least-bad of a bad set. Offering "Jane Doe? 4%" for a stranger is
                # worse than saying nothing: it invites a wrong click.
                SUGGESTION_FLOOR = 0.5

                suggestion, similarity = None, None
                if known_matrix is not None and emb and not excluded:
                    vec = np.frombuffer(emb, dtype=np.float32)
                    norm = np.linalg.norm(vec)
                    if norm > 0:
                        sims = known_matrix @ (vec / norm)
                        best = int(np.argmax(sims))
                        best_sim = float(sims[best])
                        if best_sim >= SUGGESTION_FLOOR:
                            suggestion = known_names[best]
                            similarity = round(best_sim, 4)

                area = (box[2] - box[0]) * (box[3] - box[1]) if len(box) >= 4 else 0
                faces.append({
                    "id": face_id,
                    "box": box,
                    "area": area,
                    "name": name,
                    "prob": prob,
                    "excluded": bool(excluded),
                    "excluded_reason": reason,
                    "suggestion": suggestion if name is None else None,
                    "similarity": similarity if name is None else None,
                })

            # Named first, then by how confident the guess is, then largest first: the
            # faces needing attention are the ones the eye should land on.
            faces.sort(key=lambda f: (
                f["name"] is None,
                -(f["similarity"] or 0.0),
                -f["area"],
            ))
            self.send_json({
                "faces": faces,
                "total": len(faces),
                "unmatched": sum(1 for f in faces if f["name"] is None and not f["excluded"]),
            })
        except Exception as e:
            logger.error("Error listing faces for %s: %s" % (photo_path, e))
            self.send_json_error(500, str(e))
        finally:
            if conn:
                conn.close()

    def handle_serve_face_crop(self, query):
        """Serve a face thumbnail, cropping from the original when not cached."""
        face_id_list = query.get("id")
        if not face_id_list:
            self.send_json_error(400, "Missing 'id' parameter")
            return
        try:
            face_id = int(face_id_list[0])
        except (ValueError, TypeError):
            self.send_json_error(400, "Invalid 'id' parameter")
            return

        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT photo_path, box, crop_image FROM faces WHERE id = ?", (face_id,)
            )
            row = cursor.fetchone()
            if not row:
                self.send_json_error(404, "Face not found")
                return

            photo_path, box_str, crop_image = row
            if crop_image:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(crop_image)))
                self.end_headers()
                self.wfile.write(crop_image)
                return

            if not photo_path or not os.path.exists(photo_path):
                self.send_json_error(404, "Original photo not found")
                return

            try:
                box = json.loads(box_str) if box_str else []
            except Exception:
                box = []
            if len(box) < 4:
                self.send_json_error(500, "Invalid bounding box")
                return

            with Image.open(photo_path) as img:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                width, height = img.size
                x1, y1 = max(0, int(box[0])), max(0, int(box[1]))
                x2, y2 = min(width, int(box[2])), min(height, int(box[3]))
                if (x2 - x1) <= 0 or (y2 - y1) <= 0:
                    crop_img = Image.new("RGB", (100, 100), color=(50, 50, 50))
                else:
                    crop_img = img.crop((x1, y1, x2, y2))
                if max(crop_img.size) > 256:
                    try:
                        resample = Image.Resampling.LANCZOS
                    except AttributeError:
                        resample = Image.LANCZOS
                    crop_img.thumbnail((256, 256), resample)
                buffer = io.BytesIO()
                crop_img.save(buffer, format="JPEG", quality=90)
                crop_bytes = buffer.getvalue()

            try:
                cursor.execute(
                    "UPDATE faces SET crop_image = ? WHERE id = ?",
                    (sqlite3.Binary(crop_bytes), face_id),
                )
                conn.commit()
            except Exception as cache_err:
                logger.warning("Could not cache face crop %s: %s" % (face_id, cache_err))

            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(crop_bytes)))
            self.end_headers()
            self.wfile.write(crop_bytes)
        except Exception as e:
            logger.error("Error serving face crop %s: %s" % (face_id, e))
            self.send_json_error(500, str(e))
        finally:
            if conn:
                conn.close()

    def handle_get_tags(self):
        try:
            db_tags = set()
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("SELECT tags FROM photos WHERE tags IS NOT NULL")
            for row in cursor.fetchall():
                try:
                    tags_list = json.loads(row[0])
                    for t in tags_list:
                        db_tags.add(t)
                except Exception:
                    pass
            conn.close()

            # Also load from taxonomy file
            from taxonomy import TagTaxonomy
            tax_path = os.path.splitext(self.db_path)[0] + "_taxonomy.json"
            taxonomy = TagTaxonomy(file_path=tax_path)
            taxonomy.load()
            for p in taxonomy.paths:
                db_tags.add(p)

            # Filter out hidden tags
            hidden_tags = set()
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
            if cursor.fetchone():
                cursor.execute("SELECT tag FROM tag_taxonomy WHERE hidden_from_autocomplete = 1")
                for row in cursor.fetchall():
                    hidden_tags.add(row[0])
            conn.close()

            def is_tag_hidden(tag):
                normalized = TagTaxonomy.normalize_tag(tag)
                if not normalized:
                    return False
                parts = normalized.split("/")
                for i in range(1, len(parts) + 1):
                    ancestor = "/".join(parts[:i])
                    if ancestor in hidden_tags:
                        return True
                return False

            final_tags = [t for t in db_tags if not is_tag_hidden(t)]
            self.send_json(sorted(final_tags))
        except Exception as e:
            self.send_json_error(500, str(e))

    def handle_get_people(self):
        conn = None
        try:
            conn = tagpup_db.connect(self.db_path, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT name FROM faces WHERE name IS NOT NULL ORDER BY name")
            people = [row[0] for row in cursor.fetchall()]

            # Filter out people hidden from autocomplete
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
            if cursor.fetchone():
                cursor.execute("SELECT tag FROM tag_taxonomy WHERE hidden_from_autocomplete = 1")
                hidden_tags = {row[0] for row in cursor.fetchall()}

                from taxonomy import TagTaxonomy
                def is_tag_hidden(tag):
                    normalized = TagTaxonomy.normalize_tag(tag)
                    if not normalized:
                        return False
                    parts = normalized.split("/")
                    for i in range(1, len(parts) + 1):
                        ancestor = "/".join(parts[:i])
                        if ancestor in hidden_tags:
                            return True
                    return False

                filtered_people = []
                for p in people:
                    cursor.execute("SELECT tag FROM tag_taxonomy WHERE name = ? AND has_face = 1", (p,))
                    paths = [r[0] for r in cursor.fetchall()]
                    if paths:
                        hidden = all(is_tag_hidden(path) for path in paths)
                    else:
                        hidden = False
                    if not hidden:
                        filtered_people.append(p)
                people = filtered_people

            self.send_json(people)
        except Exception as e:
            self.send_json_error(500, str(e))
        finally:
            if conn:
                conn.close()

    def handle_get_folder_scan(self, query):
        folder_path_list = query.get("path")
        if not folder_path_list:
            self.send_json_error(400, "Missing 'path' parameter")
            return
            
        folder_path = urllib.parse.unquote(folder_path_list[0])
        force_refresh = query.get("force", ["false"])[0].lower() == "true"
        
        if not os.path.isdir(folder_path):
            self.send_json_error(400, f"Path is not a valid directory: {folder_path}")
            return
            
        folder_path = paths.stored(folder_path)
        folder_path_norm = paths.key(folder_path)

        # Check cache
        if folder_path_norm in TagPupHTTPRequestHandler.folder_cache and not force_refresh:
            cached_data = list(TagPupHTTPRequestHandler.folder_cache[folder_path_norm].values())
            def get_date_taken_str(meta):
                raw_meta = meta.get("raw_metadata", {})
                for k in ["EXIF:DateTimeOriginal", "DateTimeOriginal", "XMP:DateTimeOriginal", "EXIF:CreateDate", "CreateDate"]:
                    val = raw_meta.get(k)
                    if val:
                        if isinstance(val, list) and val:
                            val = val[0]
                        return str(val).strip()
                return f"mtime_{meta.get('mtime', 0.0)}"
            cached_data.sort(key=get_date_taken_str)
            self.send_json(cached_data)
            return
            
        # Scan folder for image files
        valid_exts = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
        image_files = []
        for root, _, files in os.walk(folder_path):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in valid_exts:
                    image_files.append(os.path.join(root, file))
                    
        if not image_files:
            self.send_json([])
            return
            
        # Query existing metadata from SQLite DB to avoid running ExifTool on unchanged files
        db_records = {}
        try:
            conn = tagpup_db.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            under, under_params = paths.sql_under("path", folder_path)
            cursor.execute(
                "SELECT path, mtime, size, tags, people, captions, raw_metadata FROM photos WHERE " + under,
                under_params,
            )
            for row in cursor.fetchall():
                p, mt, sz, t_json, pe_json, c_json, raw_json = row
                db_records[paths.key(p)] = {
                    "path": paths.stored(p),
                    "mtime": mt,
                    "size": sz,
                    "tags": json.loads(t_json) if t_json else [],
                    "people": json.loads(pe_json) if pe_json else [],
                    "captions": json.loads(c_json) if c_json else [],
                    "raw_metadata": json.loads(raw_json) if raw_json else {}
                }
            conn.close()
        except Exception as db_err:
            logger.warning(f"Failed to query index DB for folder scan cache: {db_err}")
            
        # Resolve file metadata
        folder_map = {}
        files_to_read = []
        
        for file in image_files:
            file_norm = paths.key(file)
            try:
                stat = os.stat(file)
                mtime = stat.st_mtime
                size = stat.st_size
            except Exception:
                continue
                
            cached = db_records.get(file_norm)
            if cached and abs(cached["mtime"] - mtime) < 0.1 and cached["size"] == size:
                from metadata import build_photo_ui_record
                folder_map[file_norm] = build_photo_ui_record(cached["path"], cached, mtime, size)
            else:
                files_to_read.append((file, mtime, size))
                
        # For new or modified files, run ExifTool
        if files_to_read:
            logger.info(f"Scan found {len(files_to_read)} new/modified files in {folder_path}. Running ExifTool...")
            try:
                from metadata import MetadataExtractor, build_photo_ui_record
                extractor = MetadataExtractor(exiftool_path=self.get_exiftool_path())
                batch_size = 500
                for i in range(0, len(files_to_read), batch_size):
                    batch = files_to_read[i:i+batch_size]
                    batch_paths = [b[0] for b in batch]
                    batch_meta = extractor.batch_read(batch_paths)
                    for (file, mtime, size), meta in zip(batch, batch_meta):
                        file_norm = paths.key(file)
                        folder_map[file_norm] = build_photo_ui_record(file, meta, mtime, size)
            except Exception as e:
                logger.error(f"Error running ExifTool during scan: {e}")
                
        TagPupHTTPRequestHandler.folder_cache[folder_path_norm] = folder_map
        
        response_list = list(folder_map.values())
        def get_date_taken_str(meta):
            raw_meta = meta.get("raw_metadata", {})
            for k in ["EXIF:DateTimeOriginal", "DateTimeOriginal", "XMP:DateTimeOriginal", "EXIF:CreateDate", "CreateDate"]:
                val = raw_meta.get(k)
                if val:
                    if isinstance(val, list) and val:
                        val = val[0]
                    return str(val).strip()
            return f"mtime_{meta.get('mtime', 0.0)}"
        response_list.sort(key=get_date_taken_str)
        self.send_json(response_list)

    def handle_get_folder_suggest_status(self, query):
        folder_path_list = query.get("path")
        if not folder_path_list:
            self.send_json_error(400, "Missing 'path' parameter")
            return
        folder_path = paths.key(urllib.parse.unquote(folder_path_list[0]))
        self.ensure_suggestions_loaded(self.db_path)
        # A copy taken under the lock the workers write under. Serialising the live
        # dict while four workers added to it failed the poll with "dictionary
        # changed size during iteration".
        with TagPupHTTPRequestHandler.model_lock:
            status_info = make_json_serializable(
                TagPupHTTPRequestHandler.suggest_status.get(folder_path, {"status": "idle"}))
        self.send_json(status_info)

    def handle_post_folder_suggest_start(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        folder_path = data.get("folder_path")
        if not folder_path or not os.path.isdir(folder_path):
            self.send_json_error(400, "Invalid folder path")
            return
            
        folder_path = paths.stored(folder_path)
        folder_path_norm = paths.key(folder_path)

        self.ensure_suggestions_loaded(self.db_path)
        status_info = TagPupHTTPRequestHandler.suggest_status.get(folder_path_norm)
        if status_info and status_info["status"] in ("preparing", "running"):
            self.send_json({"success": True, "status": status_info["status"]})
            return
            
        existing_suggestions = {}
        if status_info:
            existing_suggestions = status_info.get("suggestions", {})

        TagPupHTTPRequestHandler.suggest_status[folder_path_norm] = {
            "status": "preparing",
            # A photo whose suggestion failed is tried again, so it is not done yet.
            "completed": sum(1 for s in existing_suggestions.values()
                             if isinstance(s, dict) and "error" not in s),
            "total": 0,
            "suggestions": existing_suggestions
        }
        
        t = threading.Thread(
            target=TagPupHTTPRequestHandler.run_folder_suggestions_thread,
            args=(folder_path, self.db_path),
            name="FolderSuggestionsThread",
            daemon=True
        )
        TagPupHTTPRequestHandler.suggest_threads[folder_path_norm] = t
        t.start()
        
        self.send_json({"success": True, "status": "running"})

    #: Serialises writes of the suggestions cache file. Its own lock, not model_lock,
    #: so a slow disk never holds up the pool threads recording suggestions.
    _suggestions_file_lock = threading.Lock()
    #: When each cache file was last written, for the throttled saves during a run.
    _suggestions_last_saved = {}

    @staticmethod
    def _suggestions_cache_path(db_path):
        """The one suggestions cache file of a library, and the only place it is named.

        One file per database, next to it. The main library keeps the unsuffixed name
        it has always had, so the file already on disk goes on loading.
        """
        db_basename = os.path.splitext(os.path.basename(db_path))[0]
        if db_basename == "photo_index":
            return os.path.join(os.path.dirname(db_path), "gui_suggestions_cache.json")
        return os.path.join(os.path.dirname(db_path), f"gui_suggestions_cache_{db_basename}.json")

    @staticmethod
    def _rekey_saved_suggestions(data):
        """Saved suggestions, keyed the way this code looks them up.

        Files written before paths.py hold folder keys in the old lower-case,
        forward-slash form, which paths.key() does not produce; loading them as they
        were would keep every saved run where nothing ever asks for it. A folder saved
        under two spellings becomes one entry, its suggestions merged. The photos
        inside are keyed by the path the page was sent, which is paths.stored().
        """
        rekeyed = {}
        for folder, status in data.items():
            if not isinstance(status, dict):
                continue
            suggestions = status.get("suggestions")
            if isinstance(suggestions, dict):
                status["suggestions"] = {paths.stored(p): s for p, s in suggestions.items()}
            folder_key = paths.key(folder)
            existing = rekeyed.get(folder_key)
            if existing is None:
                rekeyed[folder_key] = status
            else:
                merged = existing.setdefault("suggestions", {})
                for p, s in (status.get("suggestions") or {}).items():
                    merged.setdefault(p, s)
        return rekeyed

    #: Libraries whose saved suggestions have been read into memory, by registry key.
    _suggestions_loaded = set()
    _suggestions_load_lock = threading.Lock()

    @classmethod
    def ensure_suggestions_loaded(cls, db_path):
        """Read a library's saved suggestions the first time anything uses them.

        They used to be read once, at startup, for the startup library only. Any
        other library's saved runs were never offered again, and the first save for
        it -- which writes everything in memory -- replaced its file with just this
        session's folders. Every reader of the suggestions and every save calls this,
        so no save can happen before the load.
        """
        set_active_db_path(db_path)
        registry_key = get_active_db_path()
        if registry_key in cls._suggestions_loaded:
            return
        with cls._suggestions_load_lock:
            if registry_key not in cls._suggestions_loaded:
                cls.load_suggestions_cache(db_path)

    @classmethod
    def load_suggestions_cache(cls, db_path):
        set_active_db_path(db_path)
        # Marked first: a file that fails to load is not tried again on every save.
        cls._suggestions_loaded.add(get_active_db_path())
        cache_path = cls._suggestions_cache_path(db_path)
        if os.path.exists(cache_path):
            try:
                import json
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Clean up any active/running statuses to "idle"
                for folder, status in data.items():
                    if status.get("status") in ("running", "preparing"):
                        status["status"] = "idle"
                data = cls._rekey_saved_suggestions(data)
                # Only folders nothing has touched yet. This runs in the background
                # at startup, so a folder chosen straight away can already have a run
                # in progress; overwriting it with the saved copy -- rewritten to
                # "idle" above -- told the page the run had stopped, and it stopped
                # asking while the server went on and finished.
                with cls.model_lock:
                    for folder, status in data.items():
                        if folder not in cls.suggest_status:
                            cls.suggest_status[folder] = status
                logger.info(f"Loaded suggestions cache from {cache_path} with {len(data)} folders.")
            except Exception as e:
                logger.error(f"Error loading suggestions cache: {e}")

    _library_embedder_lock = threading.Lock()

    @classmethod
    def library_embedder(cls, db_path, embedder_kwargs):
        """This library's embedder and index, made once and brought up to date.

        The startup library's was made at startup and never reloaded, so photos
        indexed and tags saved since never reached Suggest. Every other library had
        none, and loaded its whole index again on every run. Each library now keeps
        one, and a run reloads its index only when the photos table has changed.
        The CLIP model itself is shared by every embedder (ClipEmbedder._shared_model).
        Call with the library active on this thread.
        """
        from embedder import ClipEmbedder
        from index import PhotoIndex

        with cls._library_embedder_lock:
            embedder = cls.shared_embedder
            if embedder is None:
                photo_index = PhotoIndex(db_path=db_path)
                photo_index.load()
                embedder = ClipEmbedder(photo_index=photo_index, **embedder_kwargs)
                cls.shared_embedder = embedder
                return embedder
        embedder.photo_index.reload_if_changed()
        return embedder

    @classmethod
    def move_saved_suggestions(cls, db_path, renames):
        """File each renamed photo's saved suggestions under its new name, and save.

        `renames` maps old path to new, in any spelling. Returns how many moved. The
        page looks suggestions up by path, so a renamed photo showed none, the next
        Suggest ran it again from scratch, and the old entry stayed for ever.
        """
        by_key = {paths.key(old): paths.stored(new) for old, new in renames.items()}
        moved = 0
        with cls.model_lock:
            for status in cls.suggest_status.values():
                saved = status.get("suggestions") if isinstance(status, dict) else None
                if not saved:
                    continue
                # Taken out first, then put back, so names shuffled among themselves
                # never overwrite one another.
                leaving = {path: saved.pop(path) for path in list(saved)
                           if paths.key(path) in by_key}
                for old_path, entry in leaving.items():
                    new_path = by_key[paths.key(old_path)]
                    raw = entry.get("raw_suggestions") if isinstance(entry, dict) else None
                    if isinstance(raw, dict) and "path" in raw:
                        raw["path"] = new_path
                    saved[new_path] = entry
                    moved += 1
        if moved:
            cls.save_suggestions_cache(db_path)
        return moved

    @classmethod
    def save_suggestions_cache(cls, db_path, min_interval=0.0):
        """Write this library's saved suggestions. True if the file was written.

        The file is replaced, never rewritten in place. It used to be truncated and
        rewritten by four pool threads at once, after every photo: two saves
        overlapping, or the process stopping mid-write, left a file that did not
        parse, and a file that does not parse restores nothing for any folder.

        The snapshot is taken inside the file lock, so a save that started later can
        never be overwritten by one that started earlier.

        `min_interval` is for saves during a run: skip the write if this file was
        written less than that many seconds ago. The run's final save passes nothing,
        so what it finished with is always what is on disk.
        """
        import json
        import tempfile
        import time
        # What is written is everything in memory, so the saved folders must be in
        # memory first or this would replace them with only this session's.
        cls.ensure_suggestions_loaded(db_path)
        cache_path = cls._suggestions_cache_path(db_path)

        def too_soon():
            last = cls._suggestions_last_saved.get(cache_path)
            return bool(min_interval) and last is not None and time.monotonic() - last < min_interval

        if too_soon():
            return False
        with cls._suggestions_file_lock:
            if too_soon():
                return False
            tmp_path = None
            try:
                with cls.model_lock:
                    serializable_data = make_json_serializable(cls.suggest_status)
                directory = os.path.dirname(cache_path) or "."
                os.makedirs(directory, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(
                    prefix=os.path.basename(cache_path) + ".", suffix=".tmp", dir=directory)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(serializable_data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                for attempt in range(40):
                    try:
                        os.replace(tmp_path, cache_path)
                        break
                    except PermissionError:
                        # Windows refuses while something has the file open to read.
                        if attempt == 39:
                            raise
                        time.sleep(0.05)
                tmp_path = None
                cls._suggestions_last_saved[cache_path] = time.monotonic()
                return True
            except Exception as e:
                logger.error(f"Error saving suggestions cache {cache_path}: {e}")
                return False
            finally:
                if tmp_path is not None:
                    try:
                        os.remove(tmp_path)
                    except OSError as e:
                        logger.warning(f"Could not remove {tmp_path}: {e}")

    @classmethod
    def rescan_folder_to_cache_classmethod(cls, folder_path):
        valid_exts = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
        image_files = []
        for root, _, files in os.walk(paths.stored(folder_path)):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in valid_exts:
                    image_files.append(os.path.join(root, file))
        if not image_files:
            cls.folder_cache[paths.key(folder_path)] = {}
            return
            
        from metadata import MetadataExtractor, build_photo_ui_record
        extractor = MetadataExtractor(exiftool_path=tagpup_config.exiftool_path())
        batch_size = 500
        results = []
        for i in range(0, len(image_files), batch_size):
            batch = image_files[i:i+batch_size]
            batch_meta = extractor.batch_read(batch)
            results.extend(batch_meta)
            
        folder_map = {}
        for meta in results:
            path = meta["path"]
            folder_map[paths.key(path)] = build_photo_ui_record(path, meta, meta.get("mtime", 0.0), meta.get("size", 0))
            
        cls.folder_cache[paths.key(folder_path)] = folder_map

    @classmethod
    def run_folder_suggestions_thread(cls, folder_path, db_path):
        # Restore the active database in this worker thread (see run_folder_index_thread).
        set_active_db_path(db_path)
        folder_path = paths.stored(folder_path)
        folder_path_norm = paths.key(folder_path)
        try:
            import concurrent.futures
            photos_dict = cls.folder_cache.get(folder_path_norm, {})
            logger.info(f"run_folder_suggestions_thread started for {folder_path}. Found {len(photos_dict)} cached photos.")
            if not photos_dict:
                logger.info(f"Folder cache empty for {folder_path}. Performing on-the-fly scan to populate cache...")
                cls.rescan_folder_to_cache_classmethod(folder_path)
                photos_dict = cls.folder_cache.get(folder_path_norm, {})
                logger.info(f"On-the-fly scan completed. Found {len(photos_dict)} photos.")
                
            if not photos_dict:
                logger.warning(f"No photos found in {folder_path} after scan. Returning early.")
                entry = cls.suggest_status.get(folder_path_norm) or {
                    "completed": 0, "total": 0, "suggestions": {}
                }
                entry["status"] = "error"
                entry["message"] = "No images found in this folder."
                cls.suggest_status[folder_path_norm] = entry
                return
                
            photo_paths = list(photos_dict.keys())
            if folder_path_norm not in cls.suggest_status:
                cls.suggest_status[folder_path_norm] = {
                    "status": "preparing", "completed": 0, "total": 0, "suggestions": {}
                }
            existing_suggs = cls.suggest_status[folder_path_norm].get("suggestions", {})

            def already_suggested(p):
                # A photo whose suggestion failed has an entry too, marked "error";
                # counting it as done meant it was never tried again.
                entry = existing_suggs.get(paths.stored(photos_dict[p]["path"]))
                return isinstance(entry, dict) and "error" not in entry

            unprocessed_paths = [p for p in photo_paths if not already_suggested(p)]
            
            cls.suggest_status[folder_path_norm]["total"] = len(photo_paths)
            cls.suggest_status[folder_path_norm]["completed"] = len(photo_paths) - len(unprocessed_paths)
            cls.suggest_status[folder_path_norm]["status"] = "preparing"
            
            cls.save_suggestions_cache(db_path)
            
            settings = tagpup_config.load()
            candidate_tags = tagpup_config.candidate_tags(settings)

            from taxonomy import TagTaxonomy
            from suggester import TagSuggester

            tax_path = os.path.splitext(db_path)[0] + "_taxonomy.json"
            taxonomy = TagTaxonomy(file_path=tax_path)
            taxonomy.load()
            
            candidate_tags = zero_shot_candidates(taxonomy, candidate_tags)

            embedder = cls.library_embedder(db_path, tagpup_config.embedder_settings(settings))
            photo_index = embedder.photo_index

            suggester = TagSuggester(photo_index, taxonomy, embedder=embedder, candidate_tags=candidate_tags)
            # Precompute candidate text embeddings sequentially so they are cached before the parallel loop
            suggester._precompute_candidates()
            
            # Transition to running state as we begin processing the images
            with cls.model_lock:
                cls.suggest_status[folder_path_norm]["status"] = "running"
            cls.save_suggestions_cache(db_path)
            
            suggestions_list = []

            def offered(sugg):
                """What the panel shows for one suggestion: tags, people, title."""
                suggested_tags = []
                suggested_people = []
                for item in sugg.get("suggested_tags", []):
                    score = item.get("score", 0.0)
                    if score >= 0.6:
                        if item.get("has_face_match"):
                            suggested_people.append({"name": item["tag"], "score": score})
                        else:
                            suggested_tags.append({"tag": item["tag"], "score": score})
                all_sugg_tags = [t["tag"] for t in suggested_tags] + [p["name"] for p in suggested_people]
                from writer import derive_caption_from_tags
                return suggested_tags, suggested_people, derive_caption_from_tags(all_sugg_tags)

            #: Saving after every photo rewrote every folder every photo; during the
            #: run the file is brought up to date at most this often.
            save_interval = 2.0

            def process_single_photo(path):
                # Pool threads are separate threads again, so re-bind the active database.
                set_active_db_path(db_path)
                if folder_path_norm not in cls.suggest_status:
                    return None
                try:
                    meta = photos_dict[path]
                    orig_path = paths.stored(meta["path"])
                    emb = embedder.embed_image(orig_path)
                    sugg = suggester.suggest_for_photo(orig_path, emb, k=15, min_sim=0.35, target_metadata=meta)
                    suggested_tags, suggested_people, suggested_title = offered(sugg)

                    with cls.model_lock:
                        cls.suggest_status[folder_path_norm]["suggestions"][orig_path] = {
                            "tags": suggested_tags,
                            "people": suggested_people,
                            "title": suggested_title,
                            # Kept as the suggester produced it, so consensus can be
                            # taken again over the whole folder when more photos
                            # arrive. Entries saved before this flag hold scores
                            # consensus already adjusted, and are left out of it.
                            "raw_suggestions": sugg,
                            "raw_before_consensus": True,
                        }
                        cls.suggest_status[folder_path_norm]["completed"] += 1
                    cls.save_suggestions_cache(db_path, min_interval=save_interval)
                    return sugg
                except Exception as e:
                    logger.error(f"Error suggesting for {path}: {e}")
                    meta = photos_dict.get(path, {})
                    orig_path = paths.stored(meta.get("path", path))
                    with cls.model_lock:
                        # Marked as a failure, not stored as an empty suggestion: the
                        # next run retries it instead of skipping it for good.
                        cls.suggest_status[folder_path_norm]["suggestions"][orig_path] = {
                            "tags": [],
                            "people": [],
                            "title": None,
                            "raw_suggestions": {"suggested_tags": []},
                            "error": str(e) or type(e).__name__,
                        }
                        cls.suggest_status[folder_path_norm]["completed"] += 1
                    cls.save_suggestions_cache(db_path, min_interval=save_interval)
                    return None

            max_workers = min(4, os.cpu_count() or 1)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(process_single_photo, p) for p in unprocessed_paths]
                for fut in concurrent.futures.as_completed(futures):
                    res = fut.result()
                    if res is not None:
                        suggestions_list.append(res)

            # Folder consensus, before the run says "completed": the page stops polling
            # on "completed" and keeps what it fetched then, which used to be the
            # scores consensus was about to change.
            #
            # Taken over every photo of the folder that has a suggestion of its own,
            # not only this run's, or resuming a folder with two photos left judged
            # what the folder agrees on from those two. It runs on copies of the
            # suggester's own output, so the stored suggestions are never adjusted
            # twice, nor mutated in place while a status request is reading them.
            if suggestions_list and folder_path_norm in cls.suggest_status:
                try:
                    import copy
                    in_folder = {paths.stored(meta["path"]) for meta in photos_dict.values()}
                    with cls.model_lock:
                        saved = cls.suggest_status[folder_path_norm]["suggestions"]
                        consensus_input = [
                            copy.deepcopy(entry["raw_suggestions"])
                            for photo, entry in saved.items()
                            if photo in in_folder
                            and isinstance(entry, dict)
                            and "error" not in entry
                            and entry.get("raw_before_consensus")
                            and isinstance(entry.get("raw_suggestions"), dict)
                            and entry["raw_suggestions"].get("path")
                        ]
                    if len(consensus_input) > 1:
                        adjusted = {}
                        for sugg in suggester.apply_folder_consensus(consensus_input):
                            adjusted[paths.stored(sugg["path"])] = offered(sugg)
                        with cls.model_lock:
                            for path, (tags, people, title) in adjusted.items():
                                entry = saved.get(path)
                                if entry is not None:
                                    saved[path] = {**entry, "tags": tags, "people": people, "title": title}
                except Exception as e:
                    logger.error(f"Error folder consensus: {e}")

            with cls.model_lock:
                cls.suggest_status[folder_path_norm]["status"] = "completed"
            cls.save_suggestions_cache(db_path)
        except Exception as e:
            logger.exception(f"Error running suggestions thread for {folder_path}: {e}")
            # Always surface the failure, even if the status entry is missing, so the UI
            # stops polling instead of spinning on "preparing" forever.
            entry = cls.suggest_status.get(folder_path_norm) or {
                "completed": 0, "total": 0, "suggestions": {}
            }
            entry["status"] = "error"
            entry["message"] = str(e)
            cls.suggest_status[folder_path_norm] = entry
            cls.save_suggestions_cache(db_path)

    def handle_post_photo_open_explorer(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        photo_path = data.get("path")
        if not photo_path or not os.path.exists(photo_path):
            self.send_json_error(400, "Invalid file path")
            return
            
        try:
            import subprocess
            subprocess.Popen(explorer_select_command(photo_path))
            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error opening explorer for {photo_path}: {e}")
            self.send_json_error(500, str(e))

    def handle_post_photo_open(self):
        """Open a photo in the application Windows associates with its type."""
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return

        photo_path = paths.stored(data.get("path") or "")
        # startfile runs whatever it is handed; this route only ever opens a photo.
        if (not photo_path or not os.path.isfile(photo_path)
                or os.path.splitext(photo_path)[1].lower() not in PHOTO_EXTENSIONS):
            self.send_json_error(400, "Not a photo file")
            return
        try:
            os.startfile(photo_path)
            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error opening {photo_path}: {e}")
            self.send_json_error(500, str(e))

    def handle_post_photo_rotate(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        photo_path = data.get("path")
        direction = data.get("direction")

        if not photo_path or not os.path.exists(photo_path):
            self.send_json_error(400, "Invalid file path")
            return

        # Reject anything but an explicit direction rather than silently treating an
        # unrecognised value as a right turn.
        if direction not in ("left", "right"):
            self.send_json_error(400, "Direction must be 'left' or 'right'")
            return

        try:
            from metadata import rotate_image_file
            from PIL import Image
            photo_path = paths.stored(photo_path)

            # Face boxes are in the coordinates Pillow shows the photo in. For most
            # formats that is the stored pixels, which a rotation (an Orientation
            # change) leaves alone, so the boxes stay right. Pillow applies a TIFF's
            # Orientation as it loads it, so there the boxes must turn with it.
            with Image.open(photo_path) as img:
                shown_oriented = img.format == "TIFF"
                if shown_oriented:
                    img.load()
                width, height = img.size

            rotate_image_file(photo_path, direction, self.get_exiftool_path())

            if shown_oriented:
                turn_face_boxes(self.db_path, photo_path, direction, width, height)
            # The file changed, so the scan must not distrust its row; nothing the
            # index describes did.
            record_file_stat_in_index(self.db_path, photo_path)

            stat = os.stat(photo_path)
            for _folder_map, photo_entry in self.cached_photo_entries(photo_path):
                photo_entry["mtime"] = stat.st_mtime
                photo_entry["size"] = stat.st_size

            # The new mtime, which versions the page's image URLs: thumbnails are
            # cached for a day, so without a new URL the grid kept the old turn.
            self.send_json({"success": True, "mtime": stat.st_mtime})
        except Exception as e:
            logger.error(f"Error rotating image {photo_path}: {e}")
            self.send_json_error(500, str(e))

    def handle_post_photo_delete(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        photo_path = data.get("path")
        if not photo_path or not os.path.exists(photo_path):
            self.send_json_error(400, "Invalid file path")
            return
            
        try:
            # First, send the file to the recycle bin
            success = send_to_recycle_bin(photo_path)
            if not success:
                self.send_json_error(500, "Failed to move file to Recycle Bin")
                return

            # Delete the file record, its faces and its cached embedding.
            forget_photo_in_index(self.db_path, photo_path)

            # Remove from every folder-cache map that holds it.
            for folder_map, _entry in self.cached_photo_entries(photo_path):
                folder_map.pop(paths.key(photo_path), None)

            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error deleting image {photo_path}: {e}")
            self.send_json_error(500, str(e))


    def handle_post_photo_save_metadata(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        photo_path = data.get("path")
        title = data.get("title")
        tags = data.get("tags", [])
        date_taken = data.get("date_taken")
        
        if not photo_path or not os.path.exists(photo_path):
            self.send_json_error(400, "Invalid file path")
            return
        photo_path = paths.stored(photo_path)

        try:
            new_flat_tags, new_hierarchical_tags = expand_tag_fields(tags)

            params = {}
            if title:
                params["XMP:Description"] = title
                params["IPTC:Caption-Abstract"] = title
                params["EXIF:ImageDescription"] = title
                params["EXIF:XPComment"] = title
            else:
                params["XMP:Description"] = ""
                params["IPTC:Caption-Abstract"] = ""
                params["EXIF:ImageDescription"] = ""
                params["EXIF:XPComment"] = ""
                
            if date_taken:
                # Normalize ISO T separator to space, and replace dash in date with colon
                date_cleaned = str(date_taken).replace("T", " ").replace("-", ":").strip()
                params["EXIF:DateTimeOriginal"] = date_cleaned
                params["XMP:DateTimeOriginal"] = date_cleaned
                params["EXIF:CreateDate"] = date_cleaned
                
                # Write subseconds explicitly if present (e.g. .123)
                subsec_parts = date_cleaned.split(".")
                if len(subsec_parts) > 1:
                    subsec = subsec_parts[1]
                    subsec_digits = ""
                    for char in subsec:
                        if char.isdigit():
                            subsec_digits += char
                        else:
                            break
                    if subsec_digits:
                        params["EXIF:SubSecTimeOriginal"] = subsec_digits
                        params["EXIF:SubSecTimeDigitized"] = subsec_digits
                        params["EXIF:SubSecTime"] = subsec_digits

            executable = self.get_exiftool_path()
            from exiftool_session import ExifToolSession
            with ExifToolSession(executable=executable) as et:
                new_flat_tags, new_hierarchical_tags = write_keyword_fields(
                    et, photo_path, tags, extra_params=params, db_path=self.db_path)
                
            from metadata import sync_title_to_filename, METADATA_FIELDS
            new_path = paths.stored(sync_title_to_filename(photo_path, title, executable))
            renamed = not paths.same(new_path, photo_path)
            index_warning = None

            # Update SQLite database
            try:
                # Get new file stats on disk
                stat = os.stat(new_path)
                mtime = stat.st_mtime
                size = stat.st_size
                
                # Fetch new raw metadata from ExifTool
                with ExifToolSession(executable=executable) as et:
                    fresh_meta_list = et.get_tags([new_path], tags=METADATA_FIELDS)
                    fresh_meta = fresh_meta_list[0] if fresh_meta_list else {}
                    
                # Clean metadata
                from metadata import clean_metadata_value, extract_tags, photo_people
                cleaned_meta = {k: clean_metadata_value(v) for k, v in fresh_meta.items()}
                db_tags = extract_tags(cleaned_meta)
                db_people = photo_people(cleaned_meta, db_tags, photo_path, db_path=self.db_path)
                db_captions = [title] if title else []
                # A rename moves the row -- embedding, faces and all -- rather than
                # inserting a second one beside it and leaving the faces behind.
                skipped = move_photo_rows(self.db_path, {photo_path: new_path})[1] if renamed else []
                if skipped:
                    index_warning = ("Renamed, but the index already has a photo at %s; "
                                     "its rows were left as they were." % new_path)
                else:
                    where, where_params = paths.sql_equals("path", new_path)
                    def update_row(conn):
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE photos SET mtime = ?, size = ?, tags = ?, people = ?,"
                            " captions = ?, raw_metadata = ? WHERE " + where,
                            (mtime, size, json.dumps(db_tags), json.dumps(db_people),
                             json.dumps(db_captions), json.dumps(cleaned_meta)) + where_params,
                        )
                        return cursor.rowcount

                    # A photo the index has never seen is not added here: that would
                    # be a row with no embedding and no faces, which is an index
                    # entry in name only.
                    tagpup_db.write_with_connection(
                        self.db_path, update_row,
                        label="index row for %s" % os.path.basename(new_path))
            except Exception as db_err:
                logger.warning(f"Failed to update SQLite database metadata for {new_path}: {db_err}")

            # Update in-memory cache: every folder map holding the photo, found under
            # the name it had (a rename stays in the same directory).
            for folder_map, photo_entry in self.cached_photo_entries(photo_path):
                if renamed:
                    folder_map.pop(paths.key(photo_path), None)
                    photo_entry["path"] = new_path
                    photo_entry["filename"] = os.path.basename(new_path)
                    folder_map[paths.key(new_path)] = photo_entry

                from metadata import extract_tags, photo_people
                raw_meta = photo_entry.setdefault("raw_metadata", {})
                # Every keyword field, not just the XMP pair: tags are re-derived
                # from this on the next line, and a stale IPTC:Keywords brought a
                # removed tag straight back.
                record_keyword_fields(raw_meta, new_flat_tags, new_hierarchical_tags)
                if date_taken:
                    date_cleaned = str(date_taken).replace("T", " ").replace("-", ":").strip()
                    raw_meta["EXIF:DateTimeOriginal"] = date_cleaned
                    raw_meta["XMP:DateTimeOriginal"] = date_cleaned
                    raw_meta["EXIF:CreateDate"] = date_cleaned
                photo_entry["tags"] = extract_tags(raw_meta)
                photo_entry["captions"] = [title] if title else []
                photo_entry["title"] = title
                photo_entry["people"] = photo_people(raw_meta, tags, new_path, db_path=self.db_path)

            result = {"success": True, "new_path": new_path}
            if index_warning:
                result["index_warning"] = index_warning
            self.send_json(result)
        except Exception as e:
            logger.error(f"Error saving metadata for {photo_path}: {e}")
            self.send_json_error(500, str(e))

    def handle_post_photos_bulk_tags(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        photo_list = data.get("paths", [])
        add_tags = data.get("add_tags", [])
        remove_tags = data.get("remove_tags", [])
        
        if not photo_list:
            self.send_json_error(400, "Missing paths list")
            return
            
        executable = self.get_exiftool_path()
        from exiftool_session import ExifToolSession
        from metadata import photo_people

        try:
            with ExifToolSession(executable=executable) as et:
                for path in photo_list:
                    path = paths.stored(path)
                    # This write replaces the photo's whole keyword set, so it starts
                    # from what the file holds now -- never from a cache that may be
                    # cold or an index that may never have seen the photo.
                    new_tags_set = set(tags_in_file(et, path))
                    for t in add_tags:
                        new_tags_set.add(t)
                    for t in remove_tags:
                        new_tags_set.discard(t)

                    new_tags = list(new_tags_set)

                    new_tags = resolve_people_tags(new_tags, self.db_path)
                    flat, hierarchical = write_keyword_fields(
                        et, path, new_tags, db_path=self.db_path)
                    record_tags_in_index(self.db_path, path, new_tags, flat, hierarchical)

                    for _folder_map, photo_entry in self.cached_photo_entries(path):
                        photo_entry["tags"] = new_tags
                        raw_meta = record_keyword_fields(
                            photo_entry.setdefault("raw_metadata", {}), flat, hierarchical)
                        photo_entry["people"] = photo_people(raw_meta, new_tags, path, db_path=self.db_path)

            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error in bulk tags write: {e}")
            self.send_json_error(500, str(e))

    def handle_post_folder_auto_apply(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        folder_path = data.get("folder_path")
        # No floor of its own. The list this writes has already been filtered to what
        # the page was shown; a second threshold here could only take away some of
        # what was offered, which is the behaviour that made Apply All ambiguous.
        threshold = data.get("threshold", 0.0)
        
        if not folder_path or not os.path.isdir(folder_path):
            self.send_json_error(400, "Invalid folder path")
            return
            
        folder_path = paths.key(folder_path)
        self.ensure_suggestions_loaded(self.db_path)
        status_info = TagPupHTTPRequestHandler.suggest_status.get(folder_path)
        if not status_info or "suggestions" not in status_info:
            self.send_json_error(400, "No suggestions found for this folder")
            return
            
        suggestions_map = status_info["suggestions"]
        photo_paths = data.get("photo_paths")
        if photo_paths:
            wanted = {paths.key(p) for p in photo_paths}
            suggestions_map = {k: v for k, v in suggestions_map.items() if paths.key(k) in wanted}
        executable = self.get_exiftool_path()
        from exiftool_session import ExifToolSession
        from metadata import photo_people

        try:
            with ExifToolSession(executable=executable) as et:
                for path, sugg_info in suggestions_map.items():
                    # Apply exactly what the panel offered.
                    #
                    # This used to read `raw_suggestions`, which is everything the
                    # suggester produced down to its own floor, while the panel shows
                    # only what scored 0.6 or better. The two lists were built in
                    # different places and drifted: a photo came back from Apply All
                    # carrying two people the panel had never mentioned, and the
                    # suggestions it *had* listed were still sitting there unapplied.
                    #
                    # One list, two consumers. `tags` and `people` are what the page
                    # was shown, so they are what gets written.
                    offered = list(sugg_info.get("tags") or [])
                    offered_people = list(sugg_info.get("people") or [])

                    apply_tags = [t["tag"] for t in offered
                                  if t.get("score", 0.0) >= threshold]
                    apply_tags += [p["name"] for p in offered_people
                                   if p.get("score", 0.0) >= threshold]
                    if not apply_tags:
                        continue
                        
                    # The whole keyword set is written, so it starts from what the
                    # file holds now, not from the folder cache or the index.
                    path = paths.stored(path)
                    new_tags = list(set(tags_in_file(et, path) + apply_tags))

                    # Apply All writes whatever the suggester proposed, and the
                    # suggester deals in leaf names. Resolve before writing.
                    new_tags = resolve_people_tags(new_tags, self.db_path)
                    flat, hierarchical = write_keyword_fields(
                        et, path, new_tags, db_path=self.db_path)
                    record_tags_in_index(self.db_path, path, new_tags, flat, hierarchical)

                    for _folder_map, photo_entry in self.cached_photo_entries(path):
                        photo_entry["tags"] = new_tags
                        raw_meta = record_keyword_fields(
                            photo_entry.setdefault("raw_metadata", {}), flat, hierarchical)
                        photo_entry["people"] = photo_people(raw_meta, new_tags, path, db_path=self.db_path)

            self.send_json({"success": True})
        except Exception as e:
            logger.error(f"Error auto-applying suggestions: {e}")
            self.send_json_error(500, str(e))

    def handle_post_folder_time_shift(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        folder_path = data.get("folder_path")
        camera_model = data.get("camera_model")
        shift_minutes = data.get("shift_minutes", 0)
        
        if not folder_path or not os.path.isdir(folder_path):
            self.send_json_error(400, "Invalid folder path")
            return
            
        if shift_minutes == 0:
            self.send_json({"success": True, "message": "No shift applied (0 minutes)"})
            return
            
        # Walked and written in the stored form, looked up by the key. This used to
        # walk the lower-cased key itself, so every path it found -- and every path
        # it sent back to the page -- was lower case, and the cache it built was keyed
        # by those instead of by paths.key().
        folder_path = paths.stored(folder_path)
        folder_key = paths.key(folder_path)

        # Load from cache, or scan on the fly if missing
        if folder_key not in TagPupHTTPRequestHandler.folder_cache:
            try:
                from metadata import MetadataExtractor
                executable = self.get_exiftool_path()
                extractor = MetadataExtractor(exiftool_path=executable)

                valid_exts = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
                image_files = []
                for root, _, files in os.walk(folder_path):
                    for file in files:
                        ext = os.path.splitext(file)[1].lower()
                        if ext in valid_exts:
                            image_files.append(os.path.join(root, file))

                from metadata import build_photo_ui_record
                results = extractor.batch_read(image_files)
                folder_map = {}
                for meta in results:
                    path = paths.stored(meta["path"])
                    folder_map[paths.key(path)] = build_photo_ui_record(path, meta, meta.get("mtime", 0.0), meta.get("size", 0))
                TagPupHTTPRequestHandler.folder_cache[folder_key] = folder_map
            except Exception as scan_err:
                logger.error(f"Error scanning folder on the fly for time shift: {scan_err}")
                self.send_json_error(500, f"Folder must be scanned first, and scan fallback failed: {scan_err}")
                return

        photos_map = TagPupHTTPRequestHandler.folder_cache[folder_key]

        # Filter photos by camera model
        target_paths = []
        for entry in photos_map.values():
            raw = entry.get("raw_metadata", {})
            model = raw.get("EXIF:Model") or raw.get("Model") or raw.get("EXIF:Make") or raw.get("Make") or "Unknown Camera"
            if camera_model == "All Cameras" or model == camera_model:
                target_paths.append(paths.stored(entry["path"]))
                
        if not target_paths:
            self.send_json({"success": True, "message": "No photos matched the camera model"})
            return
            
        try:
            updated_count, updated_entries = shift_photo_times(
                self.db_path, self.get_exiftool_path(), target_paths, shift_minutes)

            from metadata import build_photo_ui_record
            for entry in updated_entries:
                p = paths.stored(entry["path"])
                # Every map holding the photo: this folder's, and an ancestor's scan
                # that walked into it.
                for folder_map, previous in self.cached_photo_entries(p):
                    folder_map[paths.key(p)] = build_photo_ui_record(
                        p, entry, entry.get("mtime", previous.get("mtime", 0.0)),
                        entry.get("size", previous.get("size", 0)))
                    
            updated_photos = list(photos_map.values())
            self.send_json({"success": True, "updated_photos": updated_photos,
                            "updated_count": updated_count,
                            "requested_count": len(target_paths)})
        except Exception as e:
            logger.error(f"Error applying time shift to {folder_path}: {e}")
            self.send_json_error(500, str(e))
    def rescan_folder_to_cache(self, folder_path):
        valid_exts = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
        image_files = []
        for root, _, files in os.walk(paths.stored(folder_path)):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in valid_exts:
                    image_files.append(os.path.join(root, file))
        if not image_files:
            TagPupHTTPRequestHandler.folder_cache[paths.key(folder_path)] = {}
            return
            
        from metadata import MetadataExtractor, build_photo_ui_record
        extractor = MetadataExtractor(exiftool_path=self.get_exiftool_path())
        batch_size = 500
        results = []
        for i in range(0, len(image_files), batch_size):
            batch = image_files[i:i+batch_size]
            batch_meta = extractor.batch_read(batch)
            results.extend(batch_meta)
            
        folder_map = {}
        for meta in results:
            path = meta["path"]
            folder_map[paths.key(path)] = build_photo_ui_record(path, meta, meta.get("mtime", 0.0), meta.get("size", 0))
            
        TagPupHTTPRequestHandler.folder_cache[paths.key(folder_path)] = folder_map

    def handle_post_folder_rename_photos(self):
        try:
            data = self.read_json_body()
        except Exception:
            self.send_json_error(400, "Invalid JSON payload")
            return
            
        folder_path = data.get("folder_path")
        photo_paths = data.get("photo_paths", [])
        grouping = data.get("grouping", "").strip()
        
        folder_path = paths.stored(folder_path)
        if photo_paths:
            photo_paths = [paths.stored(p) for p in photo_paths]
            
        if not folder_path or not os.path.exists(folder_path):
            self.send_json_error(400, "Invalid folder path")
            return
            
        if not photo_paths:
            self.send_json_error(400, "No photos selected for renaming")
            return
            
        try:
            format_pattern = tagpup_config.rename_format()

            # Sort the selected photo paths chronologically by Date Taken
            cache = TagPupHTTPRequestHandler.folder_cache.get(paths.key(folder_path), {})
            cache = {paths.key(k): v for k, v in cache.items()}

            def get_date_taken_sort_key(p_path):
                # The cache is keyed by paths.key; looking it up by the path itself
                # never matched, so every photo sorted by its file time instead.
                entry = cache.get(paths.key(p_path))
                if entry:
                    raw = entry.get("raw_metadata", {})
                    for k in ["EXIF:DateTimeOriginal", "DateTimeOriginal", "XMP:DateTimeOriginal", "EXIF:CreateDate", "CreateDate"]:
                        val = raw.get(k)
                        if val:
                            if isinstance(val, list) and val:
                                val = val[0]
                            return str(val).strip()
                    return f"mtime_{entry.get('mtime', 0.0)}"
                try:
                    return f"mtime_{os.path.getmtime(p_path)}"
                except OSError:
                    return "9999"
                    
            sorted_paths = sorted(photo_paths, key=get_date_taken_sort_key)
            
            # Calculate target path for each selected file
            N = len(sorted_paths)
            index_len = len(str(N))
            
            from exiftool_session import ExifToolSession
            from metadata import sanitize_filename
            executable = self.get_exiftool_path()
            
            selected_renames = {}
            
            for idx, old_path in enumerate(sorted_paths, start=1):
                if not os.path.exists(old_path):
                    continue
                    
                with ExifToolSession(executable=executable) as et:
                    meta = et.get_tags([old_path], tags=[
                        "XMP-xmpMM:PreservedFileName", "XMP:PreservedFileName",
                        "XMP:Title", "Title", "XMP:Description", "Description",
                        "IPTC:Caption-Abstract", "Caption-Abstract"
                    ])
                    meta_dict = meta[0] if meta else {}
                    
                preserved = None
                for k, v in meta_dict.items():
                    base = k.split(":")[-1] if ":" in k else k
                    if base == "PreservedFileName":
                        preserved = str(v).strip()
                        break
                        
                if not preserved:
                    orig_name = os.path.basename(old_path)
                    with ExifToolSession(executable=executable) as et:
                        et.set_tags([old_path], tags={"XMP-xmpMM:PreservedFileName": orig_name}, params=["-overwrite_original"])
                        
                title = ""
                for k, v in meta_dict.items():
                    base = k.split(":")[-1] if ":" in k else k
                    if base in ["Description", "Caption-Abstract", "Title"]:
                        if v:
                            if isinstance(v, list) and v:
                                title = str(v[0]).strip()
                            else:
                                title = str(v).strip()
                            if title:
                                break
                title = title.strip()
                
                index_str = str(idx).zfill(index_len)
                new_base = format_pattern.replace("{grouping}", grouping).replace("{index}", index_str)
                if title:
                    new_base = new_base.replace("{caption}", title)
                else:
                    new_base = new_base.replace(" - {caption}", "").replace("- {caption}", "").replace("{caption}", "")
                    
                new_base = sanitize_filename(new_base)
                ext = os.path.splitext(old_path)[1]
                new_name = new_base + ext
                # In the photo's own folder. A scan includes subfolders, and this
                # joined every new name to the top one, moving photos out of theirs.
                new_path = os.path.join(os.path.dirname(old_path), new_name)

                selected_renames[old_path] = new_path

            # Identify and resolve external conflicts on disk. The occupant is moved
            # aside, and its index row has to go with it -- otherwise the photo
            # renamed into its place finds the name taken in the index.
            selected_keys = {paths.key(p) for p in selected_renames}
            target_keys = {paths.key(p) for p in selected_renames.values()}
            occupant_moves = {}
            for old_path, target_path in selected_renames.items():
                if os.path.exists(target_path) and paths.key(target_path) not in selected_keys:
                    dir_name = os.path.dirname(target_path)
                    base, ext = os.path.splitext(os.path.basename(target_path))
                    counter = 1
                    safe_path = os.path.join(dir_name, f"{base}_conflict_{counter}{ext}")
                    while os.path.exists(safe_path) or paths.key(safe_path) in target_keys:
                        counter += 1
                        safe_path = os.path.join(dir_name, f"{base}_conflict_{counter}{ext}")
                    os.rename(target_path, safe_path)
                    occupant_moves[target_path] = safe_path

            # Two passes through temporary names, so a run shuffling numbered names
            # among themselves never lands on a name not yet vacated.
            import time

            def temporary_name(path):
                return os.path.join(os.path.dirname(path), "tmp_rename_%s_%s%s" % (
                    hash(path), time.time(), os.path.splitext(path)[1]))

            temp_of = {}             # old path -> where it waits
            updated_paths_map = {}   # old path -> new path, once it is there
            try:
                for old_path, target_path in selected_renames.items():
                    if old_path != target_path:
                        temp_path = temporary_name(old_path)
                        os.rename(old_path, temp_path)
                        temp_of[old_path] = temp_path
                    else:
                        updated_paths_map[old_path] = target_path
                for old_path, temp_path in list(temp_of.items()):
                    os.rename(temp_path, selected_renames[old_path])
                    del temp_of[old_path]
                    updated_paths_map[old_path] = selected_renames[old_path]
            except OSError as rename_err:
                # Put every photo back under its old name. A failure part way used to
                # leave them called tmp_rename_<hash>_<time>, with nothing to undo it.
                # Back through temporary names again: a photo already renamed may hold
                # the old name of one still waiting.
                stranded = []
                for old_path, new_path in list(updated_paths_map.items()):
                    if old_path == new_path:
                        continue
                    try:
                        temp_path = temporary_name(old_path)
                        os.rename(new_path, temp_path)
                        temp_of[old_path] = temp_path
                    except OSError as back_err:
                        stranded.append(new_path)
                        logger.error("Smart Rename could not undo %s: %s", new_path, back_err)
                for old_path, temp_path in temp_of.items():
                    try:
                        os.rename(temp_path, old_path)
                    except OSError as back_err:
                        stranded.append(temp_path)
                        logger.error("Smart Rename could not put %s back as %s: %s",
                                     temp_path, old_path, back_err)
                for original, moved_aside in occupant_moves.items():
                    try:
                        os.rename(moved_aside, original)
                    except OSError as back_err:
                        stranded.append(moved_aside)
                        logger.error("Smart Rename could not put %s back as %s: %s",
                                     moved_aside, original, back_err)
                logger.error("Smart Rename failed and was undone: %s", rename_err)
                message = "Could not rename: %s. Every photo was put back under its old name." % rename_err
                if stranded:
                    message = ("Could not rename: %s. These could not be put back: %s"
                               % (rename_err, ", ".join(stranded)))
                self.send_json_error(500, message)
                return

            # Tell the index where the photos went.
            #
            # Renaming on disk without this leaves a row naming a file that no longer
            # exists, while the photo itself looks unindexed. The row is the valuable
            # half: it carries the photo's embedding and its faces, names included. In
            # this library one such rename stranded 78 rows holding 234 faces, 88 of
            # them named by hand -- work that only survived because the renamer records
            # where each file came from and the rows could be matched back.
            #
            # Saving a single photo has always done this. This is the bulk path, and
            # it did not, which is the same shape as the bulk tag writes fixed earlier:
            # the screen was right and the database was not.
            renamed = {old: new for old, new in updated_paths_map.items() if old != new}
            index_rows_moved, index_skipped = 0, []
            if renamed or occupant_moves:
                try:
                    # One call, so the occupants moved aside free their names for the
                    # photos renamed into them within the same transaction.
                    index_rows_moved, index_skipped = move_photo_rows(
                        self.db_path, {**occupant_moves, **renamed})
                    logger.info("Renamed %d photo(s); moved %d index row(s).",
                                len(renamed), index_rows_moved)
                except Exception as e:
                    # The files are renamed either way; a stranded row is recoverable
                    # with scripts/relink_renamed_photos.py.
                    logger.error("Renamed %d photo(s) but could not move their index "
                                 "rows: %s", len(renamed), e)
                # The files moved whatever the index did, so their suggestions follow.
                try:
                    TagPupHTTPRequestHandler.move_saved_suggestions(
                        self.db_path, {**occupant_moves, **renamed})
                except Exception as e:
                    logger.error("Renamed %d photo(s) but could not move their saved "
                                 "suggestions: %s", len(renamed), e)

            # Clear old and scan new cache entries
            if paths.key(folder_path) in TagPupHTTPRequestHandler.folder_cache:
                del TagPupHTTPRequestHandler.folder_cache[paths.key(folder_path)]
                
            self.rescan_folder_to_cache(folder_path)
            
            # Send updated photos sorted chronologically
            updated_list = list(TagPupHTTPRequestHandler.folder_cache.get(paths.key(folder_path), {}).values())
            
            def get_date_taken_str(meta):
                raw_meta = meta.get("raw_metadata", {})
                for k in ["EXIF:DateTimeOriginal", "DateTimeOriginal", "XMP:DateTimeOriginal", "EXIF:CreateDate", "CreateDate"]:
                    val = raw_meta.get(k)
                    if val:
                        if isinstance(val, list) and val:
                            val = val[0]
                        return str(val).strip()
                return f"mtime_{meta.get('mtime', 0.0)}"
            updated_list.sort(key=get_date_taken_str)
            
            self.send_json({
                "success": True,
                "updated_paths": updated_paths_map,
                "updated_photos": updated_list,
                "index_rows_moved": index_rows_moved,
                "index_skipped": [new for _, new in index_skipped],
            })
            
        except Exception as e:
            logger.error(f"Error smart renaming photos: {e}", exc_info=True)
            self.send_json_error(500, str(e))

    def handle_serve_photo_file(self, query):
        photo_path_list = query.get("path")
        if not photo_path_list:
            self.send_error(400, "Missing 'path' parameter")
            return
        photo_path = urllib.parse.unquote(photo_path_list[0])
        
        # Security check: Restrict serving to only standard image extensions
        VALID_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif", ".heic", ".heif"}
        _, ext = os.path.splitext(photo_path.lower())
        if ext not in VALID_IMAGE_EXTS:
            self.send_error(400, "Forbidden: Invalid file type requested")
            return

        if not os.path.exists(photo_path):
            self.send_error(404, f"Photo file not found: {photo_path}")
            return
        try:
            size_param = query.get("size")
            content_type = "image/jpeg"
            if size_param:
                try:
                    max_size = int(size_param[0])
                    with Image.open(photo_path) as img:
                        # Exif transpose so preview is rotated properly in UI
                        img = ImageOps.exif_transpose(img)
                        if img.mode != "RGB":
                            img = img.convert("RGB")
                        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                        
                        out_io = io.BytesIO()
                        img.save(out_io, format="JPEG", quality=85)
                        content = out_io.getvalue()
                except Exception as e:
                    logger.warning(f"Could not resize thumbnail for {photo_path}: {e}")
                    with open(photo_path, "rb") as f:
                        content = f.read()
            else:
                with open(photo_path, "rb") as f:
                    content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "max-age=86400") # Cache local thumbnails
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Internal error serving image: {e}")

    def handle_get_taxonomy_tree(self):
        try:
            # Self-healing helper to resolve parent linkage for existing database entries
            def heal_taxonomy_parents(db_path):
                try:
                    conn = tagpup_db.connect(db_path, timeout=30.0)
                    cursor = conn.cursor()
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
                    if not cursor.fetchone():
                        conn.close()
                        return
                        
                    cursor.execute("SELECT id, tag, has_face FROM tag_taxonomy WHERE tag LIKE '%/%' AND parent_id IS NULL")
                    orphans = cursor.fetchall()
                    
                    if orphans:
                        logger.info(f"Taxonomy self-healing: found {len(orphans)} orphaned paths. Healing...")
                        for node_id, tag_path, has_face in orphans:
                            parts = [p.strip() for p in tag_path.split("/") if p.strip()]
                            current_parent_id = None
                            current_path = ""
                            
                            for i in range(len(parts) - 1):
                                part = parts[i]
                                if current_path:
                                    current_path = current_path + "/" + part
                                else:
                                    current_path = part
                                    
                                cursor.execute("SELECT id FROM tag_taxonomy WHERE tag = ?", (current_path,))
                                row = cursor.fetchone()
                                if row:
                                    current_parent_id = row[0]
                                else:
                                    parent_has_face = 1 if part.lower() in ("people", "family", "friends", "pets") else has_face
                                    cursor.execute(
                                        "INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, ?, ?, ?)",
                                        (current_path, current_parent_id, part, parent_has_face)
                                    )
                                    current_parent_id = cursor.lastrowid
                                    
                            cursor.execute("UPDATE tag_taxonomy SET parent_id = ? WHERE id = ?", (current_parent_id, node_id))
                        conn.commit()
                        
                    # Heal name column if it contains '/'
                    cursor.execute("SELECT id, tag, name FROM tag_taxonomy WHERE name LIKE '%/%'")
                    bad_names = cursor.fetchall()
                    if bad_names:
                        logger.info(f"Taxonomy self-healing: found {len(bad_names)} nodes with bad name values. Healing...")
                        for node_id, tag_path, name in bad_names:
                            leaf_name = tag_path.split("/")[-1].strip()
                            cursor.execute("UPDATE tag_taxonomy SET name = ? WHERE id = ?", (leaf_name, node_id))
                        conn.commit()
                        
                    # Heal has_face column for nodes nested under face-matching roots (People, Pets, Family, Friends)
                    cursor.execute("SELECT id, tag FROM tag_taxonomy WHERE has_face = 0")
                    zero_faces = cursor.fetchall()
                    if zero_faces:
                        healed_face_nodes = 0
                        for node_id, tag_path in zero_faces:
                            parts = tag_path.split("/")
                            if len(parts) >= 2:
                                root = parts[0].lower()
                                if root in ("people", "family", "friends", "pets"):
                                    cursor.execute("UPDATE tag_taxonomy SET has_face = 1 WHERE id = ?", (node_id,))
                                    healed_face_nodes += 1
                        if healed_face_nodes > 0:
                            logger.info(f"Taxonomy self-healing: healed has_face flags for {healed_face_nodes} nodes.")
                            conn.commit()
                        
                    conn.close()
                except Exception as e:
                    logger.error(f"Error healing taxonomy: {e}")

            heal_taxonomy_parents(self.db_path)

            conn = tagpup_db.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
            if not cursor.fetchone():
                from taxonomy import seed_taxonomy_from_db
                seed_taxonomy_from_db(self.db_path)
            
            cursor.execute("SELECT id, tag, parent_id, name, has_face, hidden_from_autocomplete FROM tag_taxonomy ORDER BY tag")
            rows = cursor.fetchall()
            conn.close()
            
            counts = get_tag_usage_counts(self.db_path)
            
            tree_nodes = []
            for row in rows:
                node = {
                    "id": row[0],
                    "tag": row[1],
                    "parent_id": row[2],
                    "name": row[3],
                    "has_face": row[4],
                    "hidden_from_autocomplete": row[5],
                    "usage_count": counts.get(row[1], 0)
                }
                tree_nodes.append(node)
                
            self.send_json(tree_nodes)
        except Exception as e:
            self.send_json_error(500, str(e))

    def handle_post_taxonomy_create(self):
        try:
            data = self.read_json_body()
            name = data.get("name", "").strip()
            parent_id = data.get("parent_id")
            has_face = data.get("has_face", 0)
            
            if not name:
                self.send_json_error(400, "Tag name cannot be empty")
                return
                
            conn = tagpup_db.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            
            if parent_id:
                cursor.execute("SELECT tag, has_face FROM tag_taxonomy WHERE id = ?", (parent_id,))
                parent_row = cursor.fetchone()
                if not parent_row:
                    conn.close()
                    self.send_json_error(404, "Parent tag not found")
                    return
                parent_path, parent_has_face = parent_row
                tag_path = parent_path + "/" + name
                has_face = parent_has_face
            else:
                tag_path = name
                
            from taxonomy import TagTaxonomy
            tag_path = TagTaxonomy.normalize_tag(tag_path)
            
            # Recursive build of hierarchy
            parts = [p.strip() for p in tag_path.split("/") if p.strip()]
            current_parent_id = None
            current_path = ""
            parent_has_face = has_face
            new_id = None
            
            if parent_id:
                current_parent_id = parent_id
                cursor.execute("SELECT tag, has_face FROM tag_taxonomy WHERE id = ?", (parent_id,))
                p_row = cursor.fetchone()
                if p_row:
                    current_path = p_row[0]
                    parent_has_face = p_row[1]
            
            for i, part in enumerate(parts):
                if parent_id and i == 0:
                    if current_path and current_path.lower() == part.lower():
                        continue
                        
                if current_path:
                    if current_path.split("/")[-1].lower() == part.lower():
                        continue
                    current_path = current_path + "/" + part
                else:
                    current_path = part
                    
                cursor.execute("SELECT id, has_face FROM tag_taxonomy WHERE tag = ?", (current_path,))
                row = cursor.fetchone()
                if row:
                    current_parent_id = row[0]
                    parent_has_face = row[1]
                    new_id = row[0]
                else:
                    segment_has_face = parent_has_face if current_parent_id is not None else (1 if part.lower() in ("people", "family", "friends", "pets") else has_face)
                    cursor.execute(
                        "INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, ?, ?, ?)",
                        (current_path, current_parent_id, part, segment_has_face)
                    )
                    current_parent_id = cursor.lastrowid
                    new_id = current_parent_id
                    parent_has_face = segment_has_face
            
            conn.commit()
            conn.close()
            
            taxonomy = TagTaxonomy(db_path=self.db_path)
            taxonomy.load()
            taxonomy.add_tag(tag_path)
            taxonomy.save()
            invalidate_people_cache(self.db_path)
            
            self.send_json({"success": True, "id": new_id, "tag": tag_path})
        except Exception as e:
            self.send_json_error(500, str(e))

    def handle_post_taxonomy_update(self):
        try:
            data = self.read_json_body()
            tag_id = data.get("id")
            has_face = data.get("has_face")
            hidden_from_autocomplete = data.get("hidden_from_autocomplete")
            
            if tag_id is None:
                self.send_json_error(400, "Missing 'id' parameter")
                return
                
            conn = tagpup_db.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            
            cursor.execute("SELECT tag, parent_id FROM tag_taxonomy WHERE id = ?", (tag_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                self.send_json_error(404, "Tag not found")
                return
            tag_path, parent_id = row
            
            if has_face is not None:
                cursor.execute("UPDATE tag_taxonomy SET has_face = ? WHERE id = ?", (has_face, tag_id))
                cursor.execute(
                    "UPDATE tag_taxonomy SET has_face = ? WHERE tag = ? OR tag LIKE ?",
                    (has_face, tag_path, tag_path + "/%")
                )
                
            if hidden_from_autocomplete is not None:
                cursor.execute("UPDATE tag_taxonomy SET hidden_from_autocomplete = ? WHERE id = ?", (hidden_from_autocomplete, tag_id))
                cursor.execute(
                    "UPDATE tag_taxonomy SET hidden_from_autocomplete = ? WHERE tag = ? OR tag LIKE ?",
                    (hidden_from_autocomplete, tag_path, tag_path + "/%")
                )
                
            conn.commit()
            conn.close()
            self.send_json({"success": True})
        except Exception as e:
            self.send_json_error(500, str(e))

    def handle_post_taxonomy_delete_check(self):
        try:
            data = self.read_json_body()
            tag_id = data.get("tag_id")
            if tag_id is None:
                self.send_json_error(400, "Missing 'tag_id' parameter")
                return
                
            conn = tagpup_db.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("SELECT tag FROM tag_taxonomy WHERE id = ?", (tag_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                self.send_json_error(404, "Tag not found")
                return
            tag_path = row[0]
            conn.close()
            
            conn = tagpup_db.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("SELECT path, tags FROM photos WHERE tags IS NOT NULL")
            affected_photos = []
            for path, tags_json in cursor.fetchall():
                try:
                    tags_list = json.loads(tags_json)
                    for tag in tags_list:
                        from taxonomy import TagTaxonomy
                        normalized = TagTaxonomy.normalize_tag(tag)
                        if normalized == tag_path or normalized.startswith(tag_path + "/"):
                            affected_photos.append(path)
                            break
                except Exception:
                    pass
            conn.close()
            
            self.send_json({
                "success": True,
                "tag": tag_path,
                "used": len(affected_photos) > 0,
                "count": len(affected_photos),
                "affected_photos": affected_photos[:100]
            })
        except Exception as e:
            self.send_json_error(500, str(e))

    def handle_post_taxonomy_delete_confirm(self):
        try:
            data = self.read_json_body()
            tag_id = data.get("tag_id")
            action = data.get("action")
            target_tag = data.get("target_tag")
            
            if tag_id is None or not action:
                self.send_json_error(400, "Missing parameters")
                return
                
            conn = tagpup_db.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("SELECT tag FROM tag_taxonomy WHERE id = ?", (tag_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                self.send_json_error(404, "Tag not found")
                return
            tag_path = row[0]
            conn.close()
            
            conn = tagpup_db.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("SELECT path, tags FROM photos WHERE tags IS NOT NULL")
            affected_photos = []
            for path, tags_json in cursor.fetchall():
                try:
                    tags_list = json.loads(tags_json)
                    for tag in tags_list:
                        from taxonomy import TagTaxonomy
                        normalized = TagTaxonomy.normalize_tag(tag)
                        if normalized == tag_path or normalized.startswith(tag_path + "/"):
                            affected_photos.append(path)
                            break
                except Exception:
                    pass
            conn.close()
            
            rewritten = 0
            if affected_photos:
                executable = self.get_exiftool_path()
                if action == "move":
                    if not target_tag:
                        self.send_json_error(400, "Target tag path is required for move action")
                        return
                    from taxonomy import TagTaxonomy
                    target_tag = TagTaxonomy.normalize_tag(target_tag)
                    conn = tagpup_db.connect(self.db_path, timeout=10.0)
                    cursor = conn.cursor()
                    insert_tag_path_to_db(cursor, target_tag)
                    conn.commit()
                    conn.close()
                    # The writes resolve names against the tree, which now has the target.
                    invalidate_people_cache(self.db_path)

                    rewritten = update_photo_metadata_tags(
                        self.db_path, executable, affected_photos, tag_path, target_tag)
                else:
                    rewritten = update_photo_metadata_tags(
                        self.db_path, executable, affected_photos, tag_path, None)

            # A photo that could not be rewritten still carries the tag, so the tag
            # still describes it and stays in the tree. It used to be deleted anyway,
            # and the reply said success.
            if rewritten < len(affected_photos):
                TagPupHTTPRequestHandler.folder_cache.clear()
                self.send_json({
                    "success": False,
                    "photos_affected": len(affected_photos),
                    "photos_rewritten": rewritten,
                    "error": "%d of %d photo(s) could not be rewritten, so '%s' was kept; "
                             "they still carry it." % (len(affected_photos) - rewritten,
                                                       len(affected_photos), tag_path),
                })
                return

            conn = tagpup_db.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("DELETE FROM tag_taxonomy WHERE id = ?", (tag_id,))
            conn.commit()
            conn.close()
            
            from taxonomy import TagTaxonomy
            taxonomy = TagTaxonomy(db_path=self.db_path)
            taxonomy.load()
            paths_to_remove = [p for p in taxonomy.paths if p == tag_path or p.startswith(tag_path + "/")]
            for p in paths_to_remove:
                taxonomy.paths.discard(p)
            taxonomy.save()
            invalidate_people_cache(self.db_path)

            TagPupHTTPRequestHandler.folder_cache.clear()
            self.send_json({"success": True, "photos_affected": len(affected_photos),
                            "photos_rewritten": rewritten})
        except Exception as e:
            self.send_json_error(500, str(e))

    def handle_post_taxonomy_rename(self):
        try:
            data = self.read_json_body()
            tag_id = data.get("tag_id")
            new_name = data.get("new_name", "").strip()
            
            if tag_id is None or not new_name:
                self.send_json_error(400, "Missing parameters")
                return
                
            conn = tagpup_db.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("SELECT tag, parent_id, name FROM tag_taxonomy WHERE id = ?", (tag_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                self.send_json_error(404, "Tag not found")
                return
            old_tag_path, parent_id, current_name = row
            
            if current_name == new_name:
                conn.close()
                self.send_json({"success": True})
                return
                
            # Compute new path
            if parent_id is not None:
                cursor.execute("SELECT tag FROM tag_taxonomy WHERE id = ?", (parent_id,))
                parent_row = cursor.fetchone()
                if not parent_row:
                    conn.close()
                    self.send_json_error(500, "Parent tag not found in DB")
                    return
                new_tag_path = parent_row[0] + "/" + new_name
            else:
                new_tag_path = new_name
                
            from taxonomy import TagTaxonomy
            new_tag_path = TagTaxonomy.normalize_tag(new_tag_path)
            
            # Check for conflict
            cursor.execute("SELECT id FROM tag_taxonomy WHERE tag = ?", (new_tag_path,))
            conflict = cursor.fetchone()
            if conflict:
                conn.close()
                self.send_json_error(400, f"A tag with path '{new_tag_path}' already exists.")
                return
                
            # Retrieve descendants: tags that begin with exactly this path and a slash.
            # LIKE read `_` as any character and ignored case, so renaming `Club_A`
            # renamed everything under `ClubXA` too.
            prefix = old_tag_path + "/"
            cursor.execute("SELECT id, tag FROM tag_taxonomy WHERE substr(tag, 1, ?) = ?",
                           (len(prefix), prefix))
            descendants = cursor.fetchall()
            
            # Update the node itself
            cursor.execute("UPDATE tag_taxonomy SET name = ?, tag = ? WHERE id = ?", (new_name, new_tag_path, tag_id))
            
            # Update descendants paths
            for desc_id, desc_tag in descendants:
                new_desc_tag = new_tag_path + desc_tag[len(old_tag_path):]
                cursor.execute("UPDATE tag_taxonomy SET tag = ? WHERE id = ?", (new_desc_tag, desc_id))
                
            conn.commit()
            conn.close()
            
            # Find and update affected photos
            conn = tagpup_db.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("SELECT path, tags FROM photos WHERE tags IS NOT NULL")
            affected_photos = []
            for path, tags_json in cursor.fetchall():
                try:
                    tags_list = json.loads(tags_json)
                    for tag in tags_list:
                        normalized = TagTaxonomy.normalize_tag(tag)
                        if normalized == old_tag_path or normalized.startswith(old_tag_path + "/"):
                            affected_photos.append(path)
                            break
                except Exception:
                    pass
            conn.close()
            
            # The tree has the new name now. The writes below resolve people against
            # it, and with the cache still holding the old tree a photo that also
            # carries the bare name had the old path written straight back.
            invalidate_people_cache(self.db_path)

            rewritten = 0
            if affected_photos:
                executable = self.get_exiftool_path()
                rewritten = update_photo_metadata_tags(
                    self.db_path, executable, affected_photos, old_tag_path, new_tag_path)

            # Resolved faces store the bare leaf name, so renaming a person in the tag
            # tree has to follow through to the faces table. Without this the taxonomy,
            # the photo files and the photos table all say the new name while every
            # matched face still says the old one, and TagTuner keeps showing it.
            old_leaf = old_tag_path.split("/")[-1]
            new_leaf = new_tag_path.split("/")[-1]
            if old_leaf != new_leaf:
                conn = tagpup_db.connect(self.db_path, timeout=10.0)
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT has_face FROM tag_taxonomy WHERE id = ?", (tag_id,)
                )
                row = cursor.fetchone()
                is_person = bool(row and row[0])
                renamed_faces = 0
                if is_person:
                    cursor.execute(
                        "UPDATE faces SET name = ? WHERE name = ?", (new_leaf, old_leaf)
                    )
                    renamed_faces = cursor.rowcount
                    # Keep the photos.people list in step with the faces it came from.
                    cursor.execute("SELECT rowid, people FROM photos WHERE people LIKE ?", (f"%{old_leaf}%",))
                    for p_rowid, people_json in cursor.fetchall():
                        try:
                            people = json.loads(people_json or "[]")
                        except Exception:
                            continue
                        if old_leaf not in people:
                            continue
                        updated = [new_leaf if x == old_leaf else x for x in people]
                        seen, deduped = set(), []
                        for x in updated:
                            if x not in seen:
                                seen.add(x)
                                deduped.append(x)
                        cursor.execute(
                            "UPDATE photos SET people = ? WHERE rowid = ?",
                            (json.dumps(deduped), p_rowid),
                        )
                conn.commit()
                conn.close()
                if renamed_faces:
                    logger.info(f"Tag rename also renamed {renamed_faces} resolved face(s) to '{new_leaf}'.")

            # Update taxonomy fallback JSON
            taxonomy = TagTaxonomy(db_path=self.db_path)
            taxonomy.load()
            
            # Remove old paths
            paths_to_remove = [p for p in taxonomy.paths if p == old_tag_path or p.startswith(old_tag_path + "/")]
            for p in paths_to_remove:
                taxonomy.paths.discard(p)
                
            # Add new paths
            taxonomy.paths.add(new_tag_path)
            for desc_id, desc_tag in descendants:
                new_desc_tag = new_tag_path + desc_tag[len(old_tag_path):]
                taxonomy.paths.add(new_desc_tag)
                
            taxonomy.save()
            invalidate_people_cache(self.db_path)

            TagPupHTTPRequestHandler.folder_cache.clear()
            reply = {"success": True, "photos_affected": len(affected_photos),
                     "photos_rewritten": rewritten}
            if rewritten < len(affected_photos):
                reply["warning"] = ("%d of %d photo(s) could not be rewritten and still carry "
                                    "'%s'." % (len(affected_photos) - rewritten,
                                               len(affected_photos), old_tag_path))
            self.send_json(reply)
        except Exception as e:
            self.send_json_error(500, str(e))

from typing import List, Optional
def get_tag_usage_counts(db_path):
    counts = {}
    if not os.path.exists(db_path):
        return counts
    try:
        conn = tagpup_db.connect(db_path, timeout=10.0)
        cursor = conn.cursor()
        cursor.execute("SELECT tags FROM photos WHERE tags IS NOT NULL")
        for row in cursor.fetchall():
            try:
                tags_list = json.loads(row[0])
                for tag in tags_list:
                    from taxonomy import TagTaxonomy
                    normalized = TagTaxonomy.normalize_tag(tag)
                    if not normalized:
                        continue
                    parts = normalized.split("/")
                    for i in range(1, len(parts) + 1):
                        ancestor = "/".join(parts[:i])
                        counts[ancestor] = counts.get(ancestor, 0) + 1
            except Exception:
                pass
        conn.close()
    except Exception:
        pass
    return counts

def insert_tag_path_to_db(cursor, path: str, has_face_root: bool = False) -> int:
    from taxonomy import TagTaxonomy
    normalized = TagTaxonomy.normalize_tag(path)
    if not normalized:
        return None
    
    parts = normalized.split("/")
    parent_id = None
    accumulated_path = ""
    
    for i, part in enumerate(parts):
        if i == 0:
            accumulated_path = part
        else:
            accumulated_path += "/" + part
            
        cursor.execute("SELECT id, has_face FROM tag_taxonomy WHERE tag = ?", (accumulated_path,))
        row = cursor.fetchone()
        if row:
            parent_id = row[0]
            current_has_face = row[1]
            if i == 0 and has_face_root and not current_has_face:
                cursor.execute("UPDATE tag_taxonomy SET has_face = 1 WHERE id = ?", (parent_id,))
        else:
            is_p = 0
            if i == 0:
                if has_face_root or part.lower() in ["people", "family", "friends", "pets"]:
                    is_p = 1
            else:
                if parent_id is not None:
                    cursor.execute("SELECT has_face FROM tag_taxonomy WHERE id = ?", (parent_id,))
                    p_row = cursor.fetchone()
                    if p_row:
                        is_p = p_row[0]
            
            cursor.execute(
                "INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, ?, ?, ?)",
                (accumulated_path, parent_id, part, is_p)
            )
            parent_id = cursor.lastrowid
            
    return parent_id

def update_photo_metadata_tags(db_path: str, exiftool_path: str, photo_paths: List[str], tag_to_remove: str, tag_to_add: Optional[str] = None):
    """Rename or remove a tag on every photo in `photo_paths`, in the file and the index.

    Returns how many index rows were rewritten. Each row goes through
    record_tags_in_index, the same as a bulk edit: this used to write its own rows,
    recording two of the keyword fields and not the file's new mtime, so a renamed
    tag's photos were re-read on every scan and a stale IPTC:Keywords was re-derived
    straight back into the tags.
    """
    import json
    from exiftool_session import ExifToolSession
    from metadata import extract_tags
    from taxonomy import TagTaxonomy

    # Read first, write after: this connection holds no transaction while the rows are
    # rewritten, one at a time, through the write lock.
    rows = []
    conn = tagpup_db.connect(db_path, timeout=30.0)
    try:
        cursor = conn.cursor()
        for path in photo_paths:
            path = paths.stored(path)
            where, where_params = paths.sql_equals("path", path)
            cursor.execute("SELECT tags, raw_metadata FROM photos WHERE " + where, where_params)
            row = cursor.fetchone()
            if row:
                rows.append((path, row))
    finally:
        conn.close()

    recorded = 0
    with ExifToolSession(executable=exiftool_path) as et:
        for path, row in rows:
            try:
                current_tags = json.loads(row[0]) if row[0] else []
                raw_meta = json.loads(row[1]) if row[1] else {}
            except Exception:
                continue

            new_tags = []
            changed = False
            for tag in current_tags:
                normalized = TagTaxonomy.normalize_tag(tag)
                if normalized == tag_to_remove or normalized.startswith(tag_to_remove + "/"):
                    changed = True
                    if tag_to_add:
                        suffix = normalized[len(tag_to_remove):]
                        new_tag = tag_to_add + suffix
                        new_tags.append(new_tag)
                else:
                    new_tags.append(tag)
                    
            if not changed:
                continue
                
            try:
                new_flat_tags, new_hierarchical_tags = write_keyword_fields(
                    et, path, new_tags, db_path=db_path)

                # The tags column holds the view extract_tags derives from the
                # file's fields, as it did before; derived here from exactly the
                # fields just written.
                updated_tags = extract_tags(record_keyword_fields(
                    raw_meta, new_flat_tags, new_hierarchical_tags))
                if record_tags_in_index(db_path, path, updated_tags,
                                        new_flat_tags, new_hierarchical_tags):
                    recorded += 1
            except Exception as err:
                logger.error(f"Failed to update metadata on disk/db for {path}: {err}")

    return recorded

#: Listens on IPv4 and IPv6 alike -- see scripts/localserver.py for why that is
#: worth two seconds on every click.
ThreadedHTTPServer = localserver.ThreadedHTTPServer

def warmup_embedder_thread(embedder):
    logger.info("Background thread starting CLIP model warmup...")
    try:
        embedder._init_model()
        embedder.embed_text("warmup")
        logger.info("Background CLIP model warmup completed successfully.")
    except Exception as e:
        logger.error(f"Error warming up CLIP model: {e}")

    try:
        logger.info("Background thread starting Face model warmup...")
        from faces import FaceProcessor
        import suggester
        with suggester._face_processor_lock:
            if suggester._global_face_processor is None:
                suggester._global_face_processor = FaceProcessor()
        # Constructing the processor loads nothing; the models load on first use. So
        # this reported the face models warm while the first Suggest still paid for
        # loading them.
        suggester._global_face_processor._init_models()
        logger.info("Background Face model warmup completed successfully.")
    except Exception as e:
        logger.error(f"Error warming up Face models: {e}")

def start_server(port=8090, db_path="data/photo_index.db", gui_dir="gui_tagpup"):
    TagPupHTTPRequestHandler.db_path = db_path
    TagPupHTTPRequestHandler.gui_dir = gui_dir

    # Instantiate the shared embedder and start background warmup in a background thread
    def init_embedder_in_background():
        try:
            from index import PhotoIndex
            from embedder import ClipEmbedder

            photo_index = PhotoIndex(db_path=db_path)
            # Load index asynchronously in the background so the HTTP server can bind instantly
            threading.Thread(
                target=photo_index.load,
                name="LoadIndexThread",
                daemon=True
            ).start()
            
            shared_embedder = ClipEmbedder(photo_index=photo_index,
                                           **tagpup_config.embedder_settings())
            TagPupHTTPRequestHandler.shared_embedder = shared_embedder
            # The startup library's saved suggestions, ahead of the first request;
            # any other library's are read the first time it is used.
            TagPupHTTPRequestHandler.ensure_suggestions_loaded(db_path)
            
            warmup_thread = threading.Thread(
                target=warmup_embedder_thread,
                args=(shared_embedder,),
                name="WarmupEmbedderThread",
                daemon=True
            )
            warmup_thread.start()
        except Exception as e:
            logger.error(f"Failed to initialize shared embedder for warmup: {e}")

    threading.Thread(
        target=init_embedder_in_background,
        name="InitEmbedderThread",
        daemon=True
    ).start()

    server_address = ("", port)
    server = None
    import time
    for attempt in range(5):
        try:
            server = ThreadedHTTPServer(server_address, TagPupHTTPRequestHandler)
            break
        except OSError as e:
            if attempt == 4:
                raise e
            logger.info(f"Port {port} is busy, retrying in 0.5s (attempt {attempt + 1}/5)...")
            time.sleep(0.5)

    logger.info(f"TagPup server started on port {port} using DB {db_path}...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info(f"Server shutting down... (PID: {os.getpid()})")
        server.shutdown()
        server.server_close()
