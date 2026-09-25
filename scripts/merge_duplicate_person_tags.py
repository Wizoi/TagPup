"""Merge a person's bare tag into the People/<name> tag that means the same thing.

Indexing used to record every person it found as a bare root tag, beside the
hierarchical keyword that already named them, so one person existed twice: as
"Josephine Sandoval" and as "People/Josephine Sandoval". Both did the same job, which made
the Add Person list offer a choice nobody could make correctly.

That source is fixed -- people now enter the taxonomy under a people root -- but the
pairs it already made are still there, and it ran on every index, so a library may
have accumulated many. This merges them:

Only the taxonomy is touched. photos.tags is reported and left alone: it holds what the
file holds, and a photo file legitimately carries both a hierarchy and, in some
libraries, a flat leaf. Rewriting those rows only made the database disagree with the
file until the next index put it back. photos.people is left alone for the same reason:
leaf names are its correct content.

Run with --apply to write. Without it, nothing is changed and the plan is printed.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup.store import db as tagpup_db  # noqa: E402
from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core import vocabulary  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402
from tagpup.store import taxonomy as store_taxonomy  # noqa: E402


def leaf_of(tag):
    return vocabulary.key(vocabulary.leaf_of(tag))


def plan_for(db_path):
    """Which bare tags duplicate a pathed one, and which photos carry them."""
    conn = tagpup_db.connect(tagpup_db.readonly_uri(db_path), uri=True)
    if not store_taxonomy.tree_exists(conn):
        conn.close()
        raise SystemExit("no tag_taxonomy table in %s" % db_path)

    tags = store_taxonomy.tags(conn)

    # People only. A first pass over this library would also have rewritten
    # "Cross Country" to "Activity/Cross Country" and "Kentridge" to
    # "School/Kentridge" -- defensible tidying, but not what was asked for, and not
    # something a merge named after person tags should be doing quietly.
    people_roots = store_taxonomy.people_roots(conn)

    def under_people(tag):
        return vocabulary.key(vocabulary.root_of(tag)) in people_roots

    pathed = {}
    for tag in tags:
        if "/" in tag and under_people(tag):
            pathed.setdefault(leaf_of(tag), tag)

    # A bare tag is a duplicate only when a people path already names that person.
    duplicates = {
        tag: pathed[leaf_of(tag)]
        for tag in tags
        if "/" not in tag and leaf_of(tag) in pathed and tag.strip().lower() not in people_roots
    }

    affected = []
    for photo_path, photo_tags in store_photos.tags_by_path(conn):
        # Reported for information only; these rows are not changed.
        if any(t in duplicates for t in photo_tags):
            affected.append(photo_path)

    conn.close()
    return duplicates, affected


def apply_plan(db_path, duplicates, affected):
    """Delete the duplicate nodes. Returns how many rows were deleted, not planned."""
    def delete(conn):
        return sum(store_taxonomy.remove_node(conn, bare) for bare in duplicates)

    return tagpup_db.write_with_connection(db_path, delete, label="merge duplicate person tags")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=tagpup_config.library_path("kr-track.db"))
    parser.add_argument("--apply", action="store_true",
                        help="write the changes; without this nothing is modified")
    args = parser.parse_args()

    duplicates, affected = plan_for(args.db)

    print("%s\n" % args.db)
    print("people held under two tags: %d" % len(duplicates))
    for bare, pathed in sorted(duplicates.items())[:10]:
        print("   %-28s -> %s" % (bare, pathed))
    if len(duplicates) > 10:
        print("   ... and %d more" % (len(duplicates) - 10))

    print("\nphotos whose keywords also carry the bare form: %d (left as they are)"
          % len(affected))
    for photo_path in affected[:3]:
        print("   %s" % os.path.basename(photo_path))

    if not args.apply:
        print("\nNothing was changed. Re-run with --apply to write it.")
        return

    if not duplicates:
        print("\nNothing to merge.")
        return

    print("\nbacked up to %s" % tagpup_db.backup(args.db, "merge-person-tags"))

    removed = apply_plan(args.db, duplicates, affected)
    print("\nRemoved %d of %d planned duplicate taxonomy node(s)." % (removed, len(duplicates)))

    remaining, _ = plan_for(args.db)
    print("duplicates remaining: %d" % len(remaining))


if __name__ == "__main__":
    main()
