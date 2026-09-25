"""Give every already-indexed photo the identity that new ones now get.

Indexing records `XMP-xmpMM:DocumentID` and mints one where a photo has none, so a
renamed or moved file can be matched back to the index row holding its embedding and
its faces. Rows indexed before that existed have `document_id` NULL, and would only
fill in as those photos were re-indexed -- which for a settled library is never.

This walks them. For each row without an identity it reads the file's DocumentID,
writes one where the file has none, and records it. Most photos already have one:
1,075 of 1,129 sampled from this library, so the great majority of this is reading.

**It is resumable.** Only rows with a NULL identity are considered and each batch is
committed as it completes, so an interrupted run loses at most one batch and
re-running picks up where it stopped. That matters at 68,065 photos.

Files that have moved away are skipped rather than counted as failures: the row is
not wrong, it is just pointing somewhere the file no longer is, which is precisely
the problem an identity solves and this pass cannot solve retroactively.

Run with --apply to write. Without it, nothing is changed and the plan is printed.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup.store import db as tagpup_db  # noqa: E402
from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core import paths as photo_paths  # noqa: E402  -- not `paths`: backfill() takes a list by that name
from tagpup.store import photos as store_photos  # noqa: E402

from tagpup.files.identity import ensure_document_id, read_document_id


def rows_without_identity(db_path):
    """Indexed photos with no identity recorded, and whether their file is there."""
    conn = tagpup_db.connect(tagpup_db.readonly_uri(db_path), uri=True)
    try:
        without = store_photos.without_identity(conn)
    except Exception:
        conn.close()
        # `from None`: the underlying "no such column" is noise in front of a message
        # that already says what to do about it.
        raise SystemExit(
            "%s has no document_id column yet; open it with TagPup once so the "
            "migration runs." % db_path) from None

    conn.close()
    missing, gone = [], []
    for path in without:
        (missing if os.path.exists(path) else gone).append(path)
    return missing, gone


def record(db_path, found, minted=()):
    """Write a batch of identities into the index. Returns rows written.

    Rows are found by sql_equals, as every lookup is. A photo given an identity
    here was written to, so its row takes the file's new mtime and size: left with
    the old ones, every scan afterwards distrusted it and read it again.
    """
    def store(conn):
        written = 0
        for path, doc_id in found.items():
            stat = None
            if path in minted:
                try:
                    stat = os.stat(path)
                except OSError:
                    stat = None
            written += store_photos.record_identity(conn, path, doc_id, stat)
        return written

    return tagpup_db.write_with_connection(
        db_path, store, label="record %d identity/identities" % len(found))


def backfill(db_path, paths, exiftool_path=None, batch_size=200, on_progress=None):
    """Read, mint where missing, and record. Returns (read, minted, failed, recorded).

    `recorded` is rows the index took, not identities found: a row whose spelling
    matched nothing used to count as done.
    """
    from tagpup.files.exiftool_session import ExifToolSession

    # ExifTool answers with forward slashes whatever it was handed, and the index
    # stores whatever spelling it was given -- usually backslashes on Windows. The
    # UPDATE matches on path exactly, so recording against ExifTool's spelling
    # silently updates nothing: the run reports success and the column stays NULL.
    # So ExifTool's answer is mapped back, by paths.key, to the row's own spelling.
    as_indexed = {photo_paths.key(p): p for p in paths}

    def indexed_path(reported):
        return as_indexed.get(photo_paths.key(reported), reported)

    read_count = minted_count = recorded = 0
    failed = []

    with ExifToolSession(executable=exiftool_path) as et:
        for start in range(0, len(paths), batch_size):
            batch = paths[start:start + batch_size]
            try:
                results = et.get_tags(batch, tags=["XMP-xmpMM:DocumentID"])
            except Exception:
                # One unreadable file fails the whole batch and pyexiftool raises on
                # the exit status, so fall back to one at a time rather than let a
                # single bad photo cost the other 199 their identity.
                results = []
                for one in batch:
                    try:
                        results.extend(et.get_tags([one], tags=["XMP-xmpMM:DocumentID"]))
                    except Exception as e:
                        failed.append((one, str(e)))

            found, minted_here = {}, set()
            for row in results:
                reported = row.get("SourceFile")
                if not reported:
                    continue
                path = indexed_path(reported)
                existing = read_document_id(row)
                if existing:
                    found[path] = existing
                    read_count += 1
                    continue
                minted = ensure_document_id(et, reported, row)
                if minted:
                    found[path] = minted
                    minted_here.add(path)
                    minted_count += 1
                else:
                    failed.append((path, "could not write an identity"))

            if found:
                recorded += record(db_path, found, minted_here)
            if on_progress:
                on_progress(min(start + batch_size, len(paths)), len(paths),
                            read_count, minted_count, len(failed))

    return read_count, minted_count, failed, recorded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=tagpup_config.library_path("photo_index.db"))
    parser.add_argument("--exiftool", default=None)
    parser.add_argument("--batch", type=int, default=200)
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after this many photos; 0 means all")
    parser.add_argument("--apply", action="store_true",
                        help="write the changes; without this nothing is modified")
    args = parser.parse_args()

    missing, gone = rows_without_identity(args.db)
    if args.limit:
        missing = missing[:args.limit]

    print("%s\n" % args.db)
    print("photos with no identity recorded : %d" % (len(missing) + len(gone)))
    print("   of those, still on disk       : %d" % len(missing))
    print("   file has moved away           : %d (skipped)" % len(gone))

    if not args.apply:
        print("\nNothing was changed. Re-run with --apply to write it.")
        return
    if not missing:
        print("\nNothing to do.")
        return

    print("\nbacked up to %s" % tagpup_db.backup(args.db, "backfill-ids"))

    started = time.time()

    def progress(done, total, read_count, minted_count, failures):
        elapsed = max(0.001, time.time() - started)
        rate = done / elapsed
        left = (total - done) / rate if rate else 0
        print("   %6d / %d  (%5.1f/s, ~%dm left)  read %d, minted %d, failed %d"
              % (done, total, rate, int(left / 60), read_count, minted_count, failures),
              flush=True)

    read_count, minted_count, failed, recorded = backfill(
        args.db, missing, args.exiftool, args.batch, progress)

    print("\nread an existing identity : %d" % read_count)
    print("minted a new one          : %d" % minted_count)
    print("could not be given one    : %d" % len(failed))
    print("rows the index recorded   : %d of %d" % (recorded, read_count + minted_count))
    for path, why in failed[:5]:
        print("   %s: %s" % (os.path.basename(path), why))

    still, _gone = rows_without_identity(args.db)
    print("photos on disk still without an identity: %d" % len(still))


if __name__ == "__main__":
    main()
