"""Point index rows at photos that were renamed under them.

Renaming a photo outside TagPup leaves its index row behind, with the photo's embedding
and its faces -- names given by hand among them. A dead row is matched to the renamed
file beside it by the identity indexing recorded (the DocumentID), else by the name
TagPup's renamer preserved (`XMP-xmpMM:PreservedFileName`):

    row:  .../2Z6A5820.jpg          (no such file)
    file: .../Meet - 01.jpg         PreservedFileName = 2Z6A5820.CR3
    ->    the row now names Meet - 01.jpg, with its faces and embedding intact

What it finds and re-points is tagpup.services.relink_photos, on the maintenance
scaffold: without --apply the change is rehearsed and nothing is written; with it, the
rows are re-pointed as one change of the library's journal, which `tagpup_cli.py undo`
takes back. No copy of the library is taken.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import maintenance, relink_photos  # noqa: E402

# The pieces, by the names older code and tests use.
stem_of = relink_photos.stem_of
dead_rows = relink_photos.dead_rows
identities = relink_photos.identities
preserved_names = relink_photos.preserved_names
merge_unambiguous = relink_photos.merge_unambiguous


def plan_for(db_path, exiftool_path=None):
    """Which dead rows can be re-pointed, and to what (relink_photos.plan_for)."""
    return relink_photos.plan_for(Library(db_path), exiftool_path)


def apply_moves(db_path, moves):
    """Re-point each row, and its faces, at the renamed file, as one change of the
    journal. Returns (moved, skipped): rows changed, not rows planned, and the pairs whose
    new name already had rows, left where they were."""
    return relink_photos.apply_moves(Library(db_path), moves)


def reported(result):
    """Print what was skipped and what failed; 1 when anything failed, else 0."""
    lines = maintenance.skipped(result) + maintenance.failed(result)
    if lines:
        print()
    for line in lines:
        print(line)
    return 1 if result.errors else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="the library file to work on (#100: no default)")
    parser.add_argument("--exiftool", default=None)
    parser.add_argument("--apply", action="store_true",
                        help="write the changes; without this nothing is modified")
    args = parser.parse_args(argv)

    result = relink_photos.relink(Library(args.db), args.exiftool, apply=args.apply)
    counts, reveal = result.details["counts"], result.details["reveal"]

    print("%s\n" % args.db)
    print("rows that can be re-pointed: %d" % counts.get("relinkable", 0))
    print("   faces they carry: %d (%d named)" % (counts.get("faces", 0), counts.get("named", 0)))
    for move in reveal.get("moves", [])[:4]:
        print("   %-28s -> %-46s (%d faces, %d named)"
              % (os.path.basename(move["from"]), os.path.basename(move["to"])[:46], move["faces"], move["named"]))
    if len(reveal.get("moves", [])) > 4:
        print("   ... and %d more" % (len(reveal["moves"]) - 4))
    if counts.get("occupied"):
        print("left where they are, the new name already has rows (look at these by hand): %d"
              % counts["occupied"])
        for old, new in reveal["occupied"][:10]:
            print("   %s -> %s" % (os.path.basename(old), os.path.basename(new)))
    print("\ndead rows with no renamed file to match: %d" % counts.get("unmatched", 0))
    for path in reveal.get("unmatched", [])[:5]:
        print("   %s" % os.path.basename(path))

    if result.details["dry_run"]:
        print("\n%s" % maintenance.rehearsed(result))
        print("Nothing was changed. Re-run with --apply to write it.")
        return reported(result)
    if not result.attempted:
        print("\nNothing to re-point.")
        return 0
    if result.refused:
        raise SystemExit(result.refused)
    print("\n%s" % maintenance.recorded(result, args.db))
    print("\nre-pointed %d of %d planned row(s), with their faces." % (result.changed, result.attempted))
    remaining = result.details.get("remaining")
    if remaining is not None:
        print("rows still re-pointable: %d; dead rows remaining: %d"
              % (remaining["relinkable"], remaining["unmatched"]))
    return reported(result)


if __name__ == "__main__":
    sys.exit(main())
