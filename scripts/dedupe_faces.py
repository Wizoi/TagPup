"""Remove face rows that describe a face already recorded for the same photo.

A photo whose path changed can end up with each face recorded twice: once under the
old rows, re-pointed, and once detected again. The copy kept is the one that knows
something -- a decision a person made, then an exclusion, then a name -- and a face
whose copies disagree about a name is left for a person to look at. What it finds and
removes is tagpup.services.duplicate_faces; this runs it and prints the report.

Run with --apply to write. Without it, nothing is changed and the plan is printed.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import duplicate_faces, maintenance  # noqa: E402


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
    parser.add_argument("--apply", action="store_true",
                        help="write the changes; without this nothing is modified")
    args = parser.parse_args(argv)

    result = duplicate_faces.dedupe_faces(Library(args.db), apply=args.apply)
    counts, reveal = result.details["counts"], result.details["reveal"]

    print("%s\n" % args.db)
    print("duplicate face rows to remove: %d" % counts["redundant"])
    print("   of those, carrying a name  : %d" % counts["redundant_named"])
    print("   of those, marked excluded  : %d" % counts["redundant_excluded"])
    if counts["redundant_named"] or counts["redundant_excluded"]:
        print("   (kept copies carry at least as much, or this would not be a duplicate)")

    for face_id, photo_path in reveal["redundant"][:4]:
        print("   id=%-7d %s" % (face_id, os.path.basename(str(photo_path))[:60]))

    print("\nfaces two rows disagree about: %d (left alone)" % counts["disputed"])
    for photo_path, names in reveal["disputed"][:5]:
        print("   %-50s %s" % (os.path.basename(str(photo_path))[:50], " vs ".join(names)))

    if result.details["dry_run"]:
        print("\n%s" % maintenance.rehearsed(result))
        print("Nothing was changed. Re-run with --apply to write it.")
        return reported(result)
    if not result.attempted:
        print("\nNothing to remove.")
        return 0

    if result.refused:
        raise SystemExit(result.refused)
    print("\n%s" % maintenance.recorded(result, args.db))
    print("\nremoved %d row(s)." % result.changed)
    remaining = result.details.get("remaining")
    if remaining is not None:
        print("duplicates remaining: %d; disagreements remaining: %d"
              % (remaining["redundant"], remaining["disputed"]))
    return reported(result)


if __name__ == "__main__":
    sys.exit(main())
