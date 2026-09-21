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
except ImportError:  # imported as a top-level module
    import db as tagpup_db


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
        if tag.split("/")[0].strip().lower() not in roots:
            continue
        leaf = tag.split("/")[-1].strip().lower()
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
    pathed_leaves = {t.split("/")[-1].strip().lower() for t in tags if "/" in t}
    pathed_roots = {t.split("/")[0].strip().lower() for t in tags if "/" in t}

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
    import exiftool

    conn = tagpup_db.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
    paths, roots = people_paths(conn)
    conn.close()

    photo_paths = []
    for root, _dirs, files in os.walk(folder):
        for name in sorted(files):
            if name.lower().endswith(IMAGE_SUFFIXES):
                photo_paths.append(os.path.join(root, name))

    changes = []
    with exiftool.ExifToolHelper(executable=exiftool_path) as et:
        for i in range(0, len(photo_paths), 100):
            batch = photo_paths[i:i + 100]
            try:
                rows = et.get_tags(batch, tags=["XMP:Subject"])
            except Exception:
                rows = []
                for one in batch:
                    try:
                        rows.extend(et.get_tags([one], tags=["XMP:Subject"]))
                    except Exception:
                        continue
            for row in rows:
                photo_path = row.get("SourceFile") or ""
                subject = row.get("XMP:Subject", [])
                if isinstance(subject, str):
                    subject = [subject]
                subject = [str(t).strip() for t in subject if str(t).strip()]

                after, replaced = repair_tags(subject, paths, roots)
                if any(pathed for _bare, pathed in replaced):
                    changes.append({
                        "path": os.path.normpath(photo_path),
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
    conn = tagpup_db.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
    paths, roots = people_paths(conn)

    cur = conn.cursor()
    cur.execute("SELECT path, tags FROM photos")
    changes = []
    for photo_path, tags_json in cur.fetchall():
        try:
            tags = json.loads(tags_json or "[]")
        except Exception:
            continue

        after, replaced = repair_tags(tags, paths, roots)
        if any(pathed for _bare, pathed in replaced):
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
    import exiftool
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tagpup_server import write_keyword_fields

    written, missing, unchanged, failed = 0, 0, 0, []
    applied = {}
    with exiftool.ExifToolHelper(executable=exiftool_path) as et:
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
                    unchanged += 1
                    continue

                write_keyword_fields(et, photo_path, after)
                applied[photo_path] = after
                written += 1
            except Exception as exc:
                failed.append((photo_path, str(exc)))

    def store(conn):
        for photo_path, after in applied.items():
            conn.execute(
                "UPDATE photos SET tags = ? WHERE path = ?",
                (json.dumps(after), photo_path),
            )

    if applied:
        tagpup_db.write_with_connection(db_path, store, label="repair bare person tags")
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
