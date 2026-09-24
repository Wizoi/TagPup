"""Re-read the photos whose index rows no longer describe their files, and record them.

Rows go stale in ways the indexer never notices, because it only re-reads a file
whose mtime or size changed:

* bulk keyword writes recorded only some of the fields they wrote, so a removed tag
  lingered in raw_metadata's IPTC:Keywords and came back on the next re-derivation;
* they also left mtime and size as they were, so every folder scan re-read those
  photos with ExifTool, every time;
* ExifTool's answers were decoded as cp1252 instead of UTF-8, so captions holding
  "ü" were stored as "Ã¼" from files that were right all along.

This finds rows where the file on disk disagrees with the row -- mtime or size
differ, the stored text shows UTF-8-read-as-cp1252, or the tags re-derived from
raw_metadata are not the row's tags -- reads those files the way the indexer does,
and records what they hold: tags, people, captions, raw_metadata, mtime, size, and
the DocumentID where the row has none. The files are only read, never written (no
DocumentID is minted); embeddings and faces are untouched.

Dry run by default: lists what would change and changes nothing.

    .venv/Scripts/python.exe scripts/refresh_rows_from_files.py --db data/photo_index.db
    .venv/Scripts/python.exe scripts/refresh_rows_from_files.py --db data/photo_index.db --apply

--apply backs the database up first and reports rows actually changed. Stop the
servers first: they hold rows in memory.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db as tagpup_db  # noqa: E402
from metadata import MetadataExtractor, extract_tags, photo_people  # noqa: E402


#: UTF-8 bytes decoded as cp1252: "ü" -> "Ã¼", "é" -> "Ã©", "–" -> "â€“", nbsp -> "Â ".
MOJIBAKE = re.compile("Ã[\u0080-ÿ]|Â[\u0080-ÿ ]|â€")

BATCH = 200


def is_garbled(stored_json):
    """Does a stored JSON value hold UTF-8 that was read as cp1252?

    Searched decoded: rows are written with json.dumps, which escapes non-ASCII,
    so the characters never appear in the stored text itself.
    """
    if not stored_json:
        return False
    try:
        text = json.dumps(json.loads(stored_json), ensure_ascii=False)
    except Exception:
        text = stored_json
    return bool(MOJIBAKE.search(text))


def why_stale(row):
    """The reasons a row may not describe its file, or [] if it looks right."""
    path, mtime, size, tags_json, captions_json, raw_json = row
    reasons = []
    try:
        stat = os.stat(path)
    except OSError:
        return None   # file gone: a job for relink or remove, not this
    if size != stat.st_size or mtime is None or abs(mtime - stat.st_mtime) >= 0.1:
        reasons.append("mtime/size")
    if is_garbled(captions_json) or is_garbled(raw_json):
        reasons.append("garbled text")
    try:
        stored = set(json.loads(tags_json or "[]"))
        derived = set(extract_tags(json.loads(raw_json or "{}")))
        if stored != derived:
            reasons.append("keywords disagree")
    except Exception:
        reasons.append("keywords disagree")
    return reasons


def distinct_captions(captions_json):
    """The stored captions with repeats removed, or None if there were none to remove.

    The extractor listed each caption once per field it appeared in, prefixed and
    bare; the stored list is exactly that output, so dropping repeats from it is
    exactly what the corrected extraction gives -- no file needs reading.
    """
    try:
        captions = json.loads(captions_json or "[]")
    except Exception:
        return None
    distinct = list(dict.fromkeys(captions))
    return distinct if len(distinct) != len(captions) else None


def plan(conn, folder=None):
    """(rows whose file must be re-read, {path: captions} fixable from the row alone)."""
    stale, captions_only = {}, {}
    query = "SELECT path, mtime, size, tags, captions, raw_metadata FROM photos"
    params = ()
    if folder:
        import paths
        clause, params = paths.sql_under("path", folder)
        query += " WHERE " + clause
    for row in conn.execute(query, params):
        reasons = why_stale(row)
        if reasons:
            stale[row[0]] = reasons   # re-reading the file fixes its captions too
            continue
        fixed = distinct_captions(row[4])
        if fixed is not None:
            captions_only[row[0]] = fixed
    return stale, captions_only


def read_files(photo_paths, db_path):
    extractor = MetadataExtractor(mint_identities=False)
    records = {}
    for start in range(0, len(photo_paths), BATCH):
        batch = photo_paths[start:start + BATCH]
        for record in extractor.batch_read(batch, db_path=db_path):
            # People from the keywords AND the photo's named faces, as every writer
            # of the column now records them; the extractor alone knows only the
            # keywords, and would take off everyone identified by face.
            record["people"] = photo_people(record["raw_metadata"], record["tags"],
                                            record["path"], db_path=db_path)
            records[record["path"]] = record
        print("  read %d / %d" % (min(start + BATCH, len(photo_paths)), len(photo_paths)),
              end="\r", flush=True)
    print()
    return records


def differences(conn, path, record):
    tags, people, captions, raw_json, mtime, size, doc_id = conn.execute(
        "SELECT tags, people, captions, raw_metadata, mtime, size, document_id"
        " FROM photos WHERE path = ?", (path,)).fetchone()
    changed = []
    if sorted(json.loads(tags or "[]")) != sorted(record["tags"]):
        changed.append("tags")
    if sorted(json.loads(people or "[]")) != sorted(record["people"]):
        changed.append("people")
    if json.loads(captions or "[]") != record["captions"]:
        changed.append("captions")
    if json.loads(raw_json or "{}") != record["raw_metadata"]:
        changed.append("raw_metadata")
    if (mtime, size) != (record["mtime"], record["size"]):
        changed.append("mtime/size")
    if not doc_id and record.get("document_id"):
        changed.append("document_id")
    return changed, json.loads(captions or "[]")


def record_all(db_path, records, to_write, captions_only):
    """Write both kinds of fix in one transaction. Returns (from files, captions only)."""
    def store(conn):
        from_files = 0
        for path in to_write:
            r = records[path]
            from_files += conn.execute(
                "UPDATE photos SET tags = ?, people = ?, captions = ?, raw_metadata = ?,"
                " mtime = ?, size = ?, document_id = COALESCE(document_id, ?) WHERE path = ?",
                (json.dumps(r["tags"]), json.dumps(r["people"]), json.dumps(r["captions"]),
                 json.dumps(r["raw_metadata"]), r["mtime"], r["size"], r.get("document_id"),
                 path)).rowcount
        captions = 0
        for path, fixed in captions_only.items():
            captions += conn.execute("UPDATE photos SET captions = ? WHERE path = ?",
                                     (json.dumps(fixed), path)).rowcount
        return from_files, captions
    return tagpup_db.write_with_connection(db_path, store, label="refresh rows from files")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True)
    parser.add_argument("--folder", help="only rows under this folder")
    parser.add_argument("--apply", action="store_true", help="write; the default is a dry run")
    parser.add_argument("--show", type=int, default=5, help="examples to print")
    args = parser.parse_args(argv)

    conn = tagpup_db.connect(tagpup_db.readonly_uri(args.db), uri=True)
    records, to_write, fields, unreadable, examples = {}, [], Counter(), 0, []
    try:
        stale, captions_only = plan(conn, args.folder)
        print("%s\n" % args.db)
        print("rows that may not describe their file: %d" % len(stale))
        for reason, count in Counter(r for rs in stale.values() for r in rs).most_common():
            print("  %-20s %d" % (reason, count))
        print("rows listing a caption more than once: %d (fixed from the row; no file read)"
              % len(captions_only))

        if stale:
            print("\nreading %d file(s) (read-only)..." % len(stale))
            records = read_files(sorted(stale), args.db)
        for path in sorted(stale):
            record = records.get(path)
            if record is None or not record.get("raw_metadata"):
                unreadable += 1
                continue
            changed, old_captions = differences(conn, path, record)
            if changed:
                to_write.append(path)
                fields.update(changed)
                if "captions" in changed and len(examples) < args.show:
                    examples.append((path, old_captions, record["captions"]))
    finally:
        conn.close()

    if stale:
        print("\nrows whose file says something different: %d" % len(to_write))
        for field, count in fields.most_common():
            print("  %-20s %d" % (field, count))
    if unreadable:
        print("files that could not be read (left alone): %d" % unreadable)
    for path, before, after in examples:
        print("\n  %s\n    caption was: %s\n    file holds : %s"
              % (os.path.basename(path), before, after))

    if not args.apply:
        print("\nDry run. Nothing was changed. Re-run with --apply to write.")
        return 0
    if not to_write and not captions_only:
        print("\nNothing to write.")
        return 0
    print("\nbacked up to %s" % tagpup_db.backup(args.db, "refresh"))
    from_files, captions = record_all(args.db, records, to_write, captions_only)
    print("rows changed from their files: %d" % from_files)
    print("rows with repeated captions removed: %d" % captions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
