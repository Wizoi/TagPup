"""Give a person back the path their keywords are supposed to carry.

The keyword convention is a full path: "People/Hazel Brookmire", never "Hailey
Brookmire". Face recognition and the tag suggester both speak in leaf names, though,
and the code that applied their suggestions wrote whatever it was handed after
reducing it to its leaf. So every person added by clicking a recognised face, or by
Apply All on the suggestions panel, went into the photo as a bare name.

That write path is fixed -- a suggestion is now resolved to its taxonomy path before
it is written -- but the photos it already touched still hold the bare form. This
replaces it with the path the taxonomy files that person under, in the file itself:

    Hazel Brookmire   ->   People/Hazel Brookmire

Only a bare tag whose name matches a person already in the taxonomy is touched. A bare
tag that is not a person ("Cross Country", "Kentridge") is left exactly as it is: those
are legitimately flat keywords, and rewriting them would be a different change wearing
this one's name.

Where the photo already carries the pathed form as well, the bare one is simply
dropped rather than duplicated.

Run with --apply to write. Without it, nothing is changed and the plan is printed.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from . import db as tagpup_db
    from . import paths as photo_paths
except ImportError:  # imported as a top-level module
    import db as tagpup_db
    import paths as photo_paths   # `paths` here means taxonomy paths

import _root  # noqa: E402,F401
from tagpup.core import vocabulary  # noqa: E402


DEFAULT_PEOPLE_ROOTS = {"people", "family", "friends", "pets"}


def people_paths(conn):
    """Every person the taxonomy names, keyed by their lowercased leaf name."""
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'"
    )
    if not cur.fetchone():
        raise SystemExit("no tag_taxonomy table")

    roots = set(DEFAULT_PEOPLE_ROOTS)
    cur.execute("SELECT name FROM tag_taxonomy WHERE has_face = 1 AND tag NOT LIKE '%/%'")
    for row in cur.fetchall():
        if row[0]:
            roots.add(row[0].strip().lower())

    paths = {}
    cur.execute("SELECT tag FROM tag_taxonomy WHERE tag LIKE '%/%'")
    for (tag,) in cur.fetchall():
        if not tag:
            continue
        if vocabulary.key(vocabulary.root_of(tag)) not in roots:
            continue
        leaf = vocabulary.key(vocabulary.leaf_of(tag))
        # A person filed in two places is ambiguous; leave them to a human.
        if leaf in paths and paths[leaf] != tag:
            paths[leaf] = None
        else:
            paths.setdefault(leaf, tag)
    return {k: v for k, v in paths.items() if v}, roots


def repair_tags(tags, paths, roots):
    """The corrected keyword set for a photo, and what was replaced to get it.

    Three things are cleaned, all of them left by write paths since fixed:

    - a bare person name becomes the path they are filed under;
    - a bare root left behind by the old habit of writing a path as its separate
      segments ("People" sitting alone beside "People/Elias Marchetti-Oakes");
    - a leaf duplicating a path already present on the same photo.

    Anything else is passed through untouched, in its original order. A bare keyword
    that is not a person -- "Cross Country", "Kentridge" -- is a legitimate flat tag
    and is none of this script's business.
    """
    pathed_leaves = {vocabulary.key(vocabulary.leaf_of(t)) for t in tags if "/" in t}
    pathed_roots = {vocabulary.key(vocabulary.root_of(t)) for t in tags if "/" in t}

    result = []
    replaced = []
    for tag in tags:
        if "/" in tag:
            if tag not in result:
                result.append(tag)
            continue

        low = tag.strip().lower()

        # A root standing alone under which this photo already files someone.
        if low in roots and low in pathed_roots:
            replaced.append((tag, None))
            continue

        # A leaf duplicating a path already on this photo.
        if low in pathed_leaves:
            replaced.append((tag, None))
            continue

        person = paths.get(low)
        if not person:
            if tag not in result:
                result.append(tag)
            continue

        replaced.append((tag, person))
        if person not in result:
            result.append(person)

    return result, replaced


IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".webp")


def plan_for_folder(db_path, folder, exiftool_path=None):
    """The same plan, read from the files in a folder rather than from the index.

    Needed because the index lags the files: a folder tagged minutes ago still has
    its old keywords recorded, so planning from the index would report nothing to do
    on exactly the photos that need it most.
    """
    from exiftool_session import ExifToolSession

    conn = tagpup_db.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)  # not a path: the database file's URI
    paths, roots = people_paths(conn)
    conn.close()

    in_folder = []
    for root, _dirs, files in os.walk(photo_paths.stored(folder)):
        for name in sorted(files):
            if name.lower().endswith(IMAGE_SUFFIXES):
                in_folder.append(os.path.join(root, name))

    changes = []
    with ExifToolSession(executable=exiftool_path) as et:
        for i in range(0, len(in_folder), 100):
            batch = in_folder[i:i + 100]
            try:
                rows = et.get_tags(batch, tags=["XMP:Subject"])
            except Exception:
                rows = []
                for one in batch:
                    try:
                        rows.extend(et.get_tags([one], tags=["XMP:Subject"]))
                    except Exception as exc:
                        # Say so: a photo left out of the plan is one nobody checked.
                        print("could not read %s: %s" % (one, exc), file=sys.stderr)
                        continue
            for row in rows:
                photo_path = row.get("SourceFile") or ""
                subject = row.get("XMP:Subject", [])
                if isinstance(subject, str):
                    subject = [subject]
                subject = [str(t).strip() for t in subject if str(t).strip()]

                after, replaced = repair_tags(subject, paths, roots)
                if replaced:
                    changes.append({
                        # ExifTool answers with forward slashes.
                        "path": photo_paths.stored(photo_path),
                        "before": subject,
                        "after": after,
                        "replaced": [(b, p) for b, p in replaced if p],
                    })

    return changes, paths, roots


def plan_for(db_path):
    """Which photos hold a bare person name, and what each should become.

    Planned from the index because it is fast to scan; the files themselves are
    re-read at apply time, since the index holds a tidied view and the file can hold
    debris the index never shows.
    """
    conn = tagpup_db.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)  # not a path: the database file's URI
    paths, roots = people_paths(conn)

    cur = conn.cursor()
    cur.execute("SELECT path, tags FROM photos")
    changes = []
    for photo_path, tags_json in cur.fetchall():
        try:
            tags = json.loads(tags_json or "[]")
        except Exception:
            continue

        # Any repair counts, not only a bare name gaining its path. A photo whose only
        # fault is debris -- a "People" root standing alone, or a leaf duplicating a
        # path beside it -- was skipped, so the seed photo carrying both went unfixed
        # through a full pass.
        after, replaced = repair_tags(tags, paths, roots)
        if replaced:
            changes.append({
                "path": photo_path,
                "before": tags,
                "after": after,
                "replaced": [(b, p) for b, p in replaced if p],
            })

    conn.close()
    return changes, paths, roots


def apply_changes(db_path, changes, paths, roots, exiftool_path=None):
    """Write the corrected keywords into the photo files, then into the index.

    Each file is re-read rather than trusted from the index: the index stores a view
    with flat duplicates already stripped, so writing that view back would be correct
    but would also mean writing a set nobody had checked against the file. Reading
    first means a photo edited elsewhere since the last index is not quietly reverted.
    """
    from exiftool_session import ExifToolSession
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tagpup_server import record_tags_in_index, write_keyword_fields

    written, missing, unchanged, failed = 0, 0, 0, []
    applied = {}   # path -> (tags, flat, hierarchical); flat None = derive from tags
    with ExifToolSession(executable=exiftool_path) as et:
        for change in changes:
            photo_path = change["path"]
            if not os.path.exists(photo_path):
                missing += 1
                continue
            try:
                current = et.get_tags([photo_path], tags=["XMP:Subject"])[0]
                subject = current.get("XMP:Subject", [])
                if isinstance(subject, str):
                    subject = [subject]
                subject = [str(t).strip() for t in subject if str(t).strip()]

                after, replaced = repair_tags(subject, paths, roots)
                if not replaced or after == subject:
                    # The file is already right and the index disagreed with it --
                    # a stale row, not a bad photo. Two databases cover some of the
                    # same folders, so repairing through one leaves the other's rows
                    # describing keywords that have since been fixed. Record what the
                    # file holds rather than leaving the row wrong.
                    unchanged += 1
                    applied[photo_path] = (subject, None, None)
                    continue

                flat, hierarchical = write_keyword_fields(et, photo_path, after)
                applied[photo_path] = (after, flat, hierarchical)
                written += 1
            except Exception as exc:
                failed.append((photo_path, str(exc)))

    # Through the one recorder, like every other keyword write: this updated only
    # the tags column, leaving raw_metadata's keyword fields, people and the file's
    # new mtime and size describing the photo as it was before the repair.
    recorded = 0
    for photo_path, (tags, flat, hierarchical) in applied.items():
        if record_tags_in_index(db_path, photo_path, tags, flat, hierarchical):
            recorded += 1
    if recorded < len(applied):
        print("  %d photo(s) written but not in this index" % (len(applied) - recorded))
    return written, missing, unchanged, failed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/kr-track.db")
    parser.add_argument("--exiftool", default=None, help="path to exiftool")
    parser.add_argument("--folder", default=None,
                        help="read the photos in this folder instead of the index; "
                             "use it for a folder tagged since the last index")
    parser.add_argument("--apply", action="store_true",
                        help="write the changes; without this nothing is modified")
    args = parser.parse_args()

    if args.folder:
        changes, paths, roots = plan_for_folder(args.db, args.folder, args.exiftool)
    else:
        changes, paths, roots = plan_for(args.db)
    on_disk = [c for c in changes if os.path.exists(c["path"])]

    print("%s%s\n" % (args.db, (" -- " + args.folder) if args.folder else ""))
    print("photos holding a bare person name: %d (%d still on disk)"
          % (len(changes), len(on_disk)))

    pairs = {}
    for change in changes:
        for bare, pathed in change["replaced"]:
            pairs[bare] = pathed
    print("distinct people affected: %d\n" % len(pairs))
    for bare, pathed in sorted(pairs.items()):
        print("   %-28s -> %s" % (bare, pathed))

    print("\nexample photos:")
    for change in changes[:3]:
        print("   %s" % os.path.basename(change["path"]))
        print("      before: %s" % change["before"])
        print("      after:  %s" % change["after"])

    if not args.apply:
        print("\nNothing was changed. Re-run with --apply to write it.")
        return
    if not changes:
        print("\nNothing to repair.")
        return

    print("\nbacked up to %s" % tagpup_db.backup(args.db, "repair-person-tags"))

    written, missing, unchanged, failed = apply_changes(
        args.db, changes, paths, roots, args.exiftool)
    print("\nrewrote %d photo(s); %d no longer on disk; %d already correct in the file"
          % (written, missing, unchanged))
    for photo_path, error in failed:
        print("   FAILED %s: %s" % (os.path.basename(photo_path), error))

    if args.folder:
        remaining, _, _ = plan_for_folder(args.db, args.folder, args.exiftool)
    else:
        remaining, _, _ = plan_for(args.db)
    remaining = [c for c in remaining if os.path.exists(c["path"])]
    print("photos on disk still holding a bare person name: %d" % len(remaining))


if __name__ == "__main__":
    main()
