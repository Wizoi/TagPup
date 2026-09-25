"""Merge a person's bare tag into the People/<name> tag that means the same thing.

Indexing used to record every person it found as a bare root tag, beside the
hierarchical keyword that already named them, so one person existed twice. This takes
the bare node out of the tag tree wherever a People path already names that person.
Only the taxonomy is touched: photos carrying the bare form are counted and left as
they are. What it finds and removes is tagpup.services.person_tags; this runs it and
prints the report.

Run with --apply to write. Without it, nothing is changed and the plan is printed.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import maintenance, person_tags  # noqa: E402


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

    result = person_tags.merge_duplicate_person_tags(Library(args.db), apply=args.apply)
    if result.refused:
        raise SystemExit(result.refused)
    counts, reveal = result.details["counts"], result.details["reveal"]

    print("%s\n" % args.db)
    print("people held under two tags: %d" % counts["duplicates"])
    for bare, pathed in reveal["duplicates"][:10]:
        print("   %-28s -> %s" % (bare, pathed))
    if counts["duplicates"] > 10:
        print("   ... and %d more" % (counts["duplicates"] - 10))

    print("\nphotos whose keywords also carry the bare form: %d (left as they are)"
          % counts["affected_photos"])
    for photo_path in reveal["affected_photos"][:3]:
        print("   %s" % os.path.basename(photo_path))

    if result.details["dry_run"]:
        print("\n%s" % maintenance.rehearsed(result))
        print("Nothing was changed. Re-run with --apply to write it.")
        return reported(result)
    if not result.attempted:
        print("\nNothing to merge.")
        return 0

    if result.refused:
        raise SystemExit(result.refused)
    print("\n%s" % maintenance.recorded(result, args.db))
    print("\nRemoved %d of %d planned duplicate taxonomy node(s)." % (result.changed, result.attempted))
    if "remaining" in result.details:
        print("duplicates remaining: %d" % result.details["remaining"]["duplicates"])
    return reported(result)


if __name__ == "__main__":
    sys.exit(main())
