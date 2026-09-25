"""Give every already-indexed photo the identity that new ones now get.

Indexing records `XMP-xmpMM:DocumentID` and mints one where a photo has none, so a
renamed or moved file can be matched back to the index row holding its embedding and
its faces. Rows indexed before that existed have `document_id` NULL, and would only
fill in as those photos were re-indexed -- which for a settled library is never.

This walks them (tagpup.services.document_ids): it reads each file's DocumentID, records
the ones the files hold, and writes one into a file that has none. Most photos already
have one: 1,075 of 1,129 sampled from this library, so the great majority of this is
reading. Only rows with no identity are read, so a run stopped part way is picked up by
the next.

Files that have moved away are skipped rather than counted as failures: the row is
not wrong, it is just pointing somewhere the file no longer is.

Run with --apply to write. Without it, nothing is changed: the recording is rehearsed
and the plan printed. With it, the identities are recorded as one change of the
library's journal and minted as another, a change of photo files, each undoable with
`tagpup_cli.py undo` (an identity undone comes out of its file again). No copy of the
library is taken.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.services import document_ids, maintenance  # noqa: E402


def rows_without_identity(db_path):
    """Indexed photos with no identity recorded, and whether their file is there."""
    try:
        return document_ids.without_identity(Library(db_path))
    except document_ids.NoIdentityColumn:
        # `from None`: the underlying "no such column" is noise in front of a message
        # that already says what to do about it.
        raise SystemExit(
            "%s has no document_id column yet; open it with TagPup once so the "
            "migration runs." % db_path) from None


def record(db_path, found, minted=()):
    """Record a batch of identities in the index, as one change of the journal. Returns
    rows written: a row whose spelling matched nothing is not counted as done."""
    return document_ids.record(Library(db_path), found, minted)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="the library file to work on (#100: no default)")
    parser.add_argument("--exiftool", default=None)
    parser.add_argument("--batch", type=int, default=200)
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after this many photos; 0 means all")
    parser.add_argument("--apply", action="store_true",
                        help="write the changes; without this nothing is modified")
    args = parser.parse_args(argv)

    rows_without_identity(args.db)
    started = time.time()

    def progress(done, total, held, lacking, failures):
        elapsed = max(0.001, time.time() - started)
        rate = done / elapsed
        left = (total - done) / rate if rate else 0
        print("   %6d / %d  (%5.1f/s, ~%dm left)  held %d, to mint %d, unreadable %d"
              % (done, total, rate, int(left / 60), held, lacking, failures), flush=True)

    result = document_ids.backfill(Library(args.db), args.exiftool, apply=args.apply, limit=args.limit,
                                   batch_size=args.batch, on_progress=progress)
    counts = result.details["counts"]

    print("%s\n" % args.db)
    print("photos with no identity recorded : %d" % (counts["on_disk"] + counts["gone"]))
    print("   of those, read                : %d" % counts["on_disk"])
    print("   file has moved away           : %d (skipped)" % counts["gone"])
    print("files holding an identity already: %d" % counts["held"])
    print("files to be given one            : %d" % counts["lacking"])
    print("files that could not be read     : %d" % counts["unreadable"])

    recorded = Result(details=result.details["record"])
    if result.details["dry_run"]:
        print("\n%s" % maintenance.rehearsed(recorded))
        print("Nothing was changed. Re-run with --apply to write it.")
        return 0
    if result.refused:
        raise SystemExit(result.refused)
    print("\n%s" % maintenance.recorded(recorded, args.db))
    minted = result.details["mint"]
    if minted and minted["change"] is not None:
        print("Minted into %d file(s) as change %d; to undo it: tagpup_cli.py --db \"%s\" undo %d --apply"
              % (minted["changed"], minted["change"], args.db, minted["change"]))
    for what, why in (result.skipped + result.errors)[:5]:
        print("   %s: %s" % (os.path.basename(what), why))
    print("photos on disk still without an identity: %d" % result.details["remaining"])
    return 1 if result.errors else 0


if __name__ == "__main__":
    sys.exit(main())
