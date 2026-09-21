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
import json
import os
try:
    from . import db as tagpup_db
except ImportError:  # imported as a top-level module
    import db as tagpup_db
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def leaf_of(tag):
    return tag.split("/")[-1].strip().lower()


def plan_for(db_path):
    """Which bare tags duplicate a pathed one, and which photos carry them."""
    conn = tagpup_db.connect(db_path, timeout=60.0)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'"
    )
    if not cursor.fetchone():
        conn.close()
        raise SystemExit("no tag_taxonomy table in %s" % db_path)

    cursor.execute("SELECT tag FROM tag_taxonomy")
    tags = [r[0] for r in cursor.fetchall() if r[0]]

    # People only. A first pass over this library would also have rewritten
    # "Cross Country" to "Activity/Cross Country" and "Kentridge" to
    # "School/Kentridge" -- defensible tidying, but not what was asked for, and not
    # something a merge named after person tags should be doing quietly.
    people_roots = {"people", "family", "friends"}
    cursor.execute(
        "SELECT name FROM tag_taxonomy WHERE has_face = 1 AND tag NOT LIKE '%/%'"
    )
    for row in cursor.fetchall():
        if row[0]:
            people_roots.add(row[0].strip().lower())

    def under_people(tag):
        return tag.split("/")[0].strip().lower() in people_roots

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
    cursor.execute("SELECT path, tags FROM photos")
    for photo_path, tags_json in cursor.fetchall():
        try:
            photo_tags = json.loads(tags_json or "[]")
        except Exception:
            continue
        # Reported for information only; these rows are not changed.
        if any(t in duplicates for t in photo_tags):
            affected.append(photo_path)

    conn.close()
    return duplicates, affected


def apply_plan(db_path, duplicates, affected):
    conn = tagpup_db.connect(db_path, timeout=60.0)
    cursor = conn.cursor()
    for bare in duplicates:
        cursor.execute("DELETE FROM tag_taxonomy WHERE tag = ?", (bare,))
    conn.commit()
    conn.close()

    # Keep the JSON copy of the taxonomy in step with the table.
    tax_path = os.path.splitext(db_path)[0] + "_taxonomy.json"
    if os.path.exists(tax_path):
        try:
            from taxonomy import TagTaxonomy

            taxonomy = TagTaxonomy(file_path=tax_path, db_path=db_path)
            taxonomy.load()
            taxonomy.paths = {p for p in taxonomy.paths if p not in duplicates}
            taxonomy.save()
        except Exception as exc:  # the table is the source of truth either way
            print("  (could not rewrite %s: %s)" % (tax_path, exc))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/kr-track.db")
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

    apply_plan(args.db, duplicates, affected)
    print("\nRemoved %d duplicate taxonomy node(s)." % len(duplicates))

    remaining, _ = plan_for(args.db)
    print("duplicates remaining: %d" % len(remaining))


if __name__ == "__main__":
    main()
