"""Whether a library keeps its own rules. Reads only; changes nothing (but for --rebuild-derived --apply).

    .venv/Scripts/python.exe tools/doctor.py --db data/photo_index.db
    .venv/Scripts/python.exe tools/doctor.py --db data/photo_index.db --show 5

Prints what the library holds, then each rule (tagpup.store.checks) with how many rows
break it -- among them the library's roots: a rooted row that cannot be read, a native row
under a root -- then how many photos have no CLIP vector for the model the config names,
then the photos that list a person from a face alone (a face was named and no keyword says so: reported, not broken),
then the rows whose file is not on disk, by folder, and the photos under no root, by folder.
Counts only, unless --show asks for examples: they are paths and tags, and paths name
people. Then the keywords photos carry that the tag tree has no node for, which is reported, not
broken (the tree is the owner's, and indexing never adds to it).

    .venv/Scripts/python.exe tools/doctor.py --db data/photo_index.db --rebuild-derived [--apply]

makes the derived tables (photo_tags, folders, photo_folder, photo_meta) what the photos say, each
photo's list of people (photo_people) what its keywords, faces and the tag tree make it -- a group tag is
not a person (#986) -- and each face's and listed person's id (faces.tag_id, photo_people.tag_id) the node
their name is: a dry run that says what is wrong, and with --apply a write under the library's write lock
that makes them so and verifies it. Only derived rows and columns are written -- no name, photo, tag or
file -- so no backup is taken.

The names faces and photos' people hold that no person node is called, or that two are, are reported
by count, and by name with --show: they have no id, and the tree is the owner's to settle. So are the
names on a branch -- a tag with tags under it, which is not a person -- with the branches' ids.

A library stamped with an identifier (`folder-ids mark --apply`) is checked against the other library files in its
folder: one that carries the same identifier is a copy, named by file name, and counts as a rule broken.

Exits 1 when a rule is broken, else 0. Missing files do not count against it: a folder
on an unplugged drive looks the same as a deleted one, and removing either is the
owner's choice.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tagpup import runtime  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.store import checks, db, derived, embeddings, people, person_ids, schema, search_index  # noqa: E402
from tagpup.store import folder_ids  # noqa: E402


def _refuse_newer(db_path):
    """A library made by a newer TagPup than this tool's is not read: its rules are the newer one's."""
    problem = schema.newer_problem(db_path)
    if problem:
        raise SystemExit(problem)


def report(db_path, show=0, out=print):
    """Report on the library at `db_path`. Returns the number of rules broken."""
    if not os.path.exists(db_path):
        raise SystemExit("There is no library at %s." % db_path)
    _refuse_newer(db_path)
    # The library's CLIP model, read without writing: a library never stamped reads as
    # stamping would make it.
    # A library holding a root this machine does not place cannot spell its paths: what needs
    # them is left out, and said, and the rules that can still be asked are.
    unplaced = None
    try:
        model = embeddings.model_key(**runtime.peek_settings(Library(db_path)).embedder)
    except paths.RootsError as problem:
        unplaced, model = str(problem), None
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        held = checks.summary(conn)
        results = checks.run(conn)
        try:
            missing = checks.missing_files(conn)
        except paths.RootsError as problem:
            unplaced, missing = unplaced or str(problem), []
        unembedded = checks.without_a_vector(conn, model) if model else None
        undetected = checks.faces_to_detect(conn)
        on_faces_alone = checks.people_on_faces_alone(conn)
        unrooted = checks.unrooted_by_folder(conn)
        unnamed = checks.tags_without_a_node(conn)
        empty = checks.empty_folders(conn)
        nameless = checks.names_without_a_person(conn)
        words = search_index.present(conn)
        identity, marked = folder_ids.identity(conn), folder_ids.count(conn)
    finally:
        conn.close()

    out("library: %s" % db_path)
    out("  " + "  ".join("%s %d" % (name, count) for name, count in held.items()))
    out("")
    broken = 0
    for check in results:
        out("%-48s %s" % (check.name, "ok" if not check.count else check.count))
        if check.count:
            broken += 1
            for example in check.examples[:show]:
                out("    %s" % example)
    if identity:
        # The library's identifier is in the markers of its folders (`folder-ids mark`): a second library file
        # carrying it is a copy, and two libraries answering to one line would follow each other's folders.
        twins = folder_ids.twins(db_path, identity)
        out("%-48s %s" % ("library identity, %d folder(s) marked" % marked,
                          "ok" if not twins else "carried by %d other library file(s): %s" % (len(twins), ", ".join(twins))))
        if twins:
            broken += 1
            out("    a copy of this library kept for a trial: `folder-ids mark` refuses both until one is given up")
    out("")
    if words:
        out(search_index.LIMITS + " (#752).")
    if unplaced:
        out("this machine does not say where the library's roots are, so its photos' paths cannot be read "
            "(what needs them is not reported): %s" % unplaced)
    if unembedded is not None:
        out("photos without a vector for the configured model: %d (the next index of their folders "
            "computes them)" % unembedded)
    out("photos whose faces are still to be detected: %d (indexed from a damaged copy; the next index "
        "of their folders detects them)" % undetected)
    if on_faces_alone[0]:
        out("photos listing a person from a face alone: %d, %d people (reported, not broken: a face was named and the "
            "photo's keywords do not say so; `tagpup_cli.py tags-from-faces` counts them, and with --apply writes the "
            "keywords into those photo FILES)" % on_faces_alone)
    if not unplaced:
        rows = sum(count for _folder, count, _there in missing)
        gone = [(folder, count) for folder, count, there in missing if not there]
        out("rows whose file is not on disk: %d, in %d folder(s); %d folder(s) are gone entirely"
            % (rows, len(missing), len(gone)))
        for folder, count, there in missing[:show]:
            out("    %6d  %s%s" % (count, folder, "" if there else "  (folder gone)"))
    distinct, uses, photos, ranked = unnamed
    if distinct:
        out("photo tags with no tree node: %d keyword(s), %d use(s) on %d photo(s) (reported, not broken: indexing "
            "never adds to the tree; a node made for one gives its photos their rows)" % (distinct, uses, photos))
        for tag, count in ranked[:show]:
            out("    %6d  %s" % (count, tag))
    if empty:
        out("folders holding no photo: %d (left by a delete that did not come through TagPup; reported, not broken: "
            "--rebuild-derived --apply takes them)" % len(empty))
    for label, found in (("no person node", nameless.none), ("several person nodes", nameless.several)):
        if found:
            out("names with %s: %d, on %d row(s) of faces and photos' people (reported, not broken: they have no "
                "person id, and none is guessed)" % (label, len(found), sum(found.values())))
            for name, count in sorted(found.items(), key=lambda pair: (-pair[1], pair[0]))[:show]:
                out("    %6d  %s" % (count, name))
    if nameless.branch:
        out("names on a branch: %d, on %d row(s) of faces and photos' people (reported, not broken: a tag with tags "
            "under it is not a person, so they have no person id): node %s"
            % (len(nameless.branch), sum(rows for _nodes, rows in nameless.branch.values()),
               ", node ".join(str(node) for node in sorted({n for nodes, _rows in nameless.branch.values() for n in nodes}))))
        for name, (nodes, count) in sorted(nameless.branch.items(), key=lambda pair: (-pair[1][1], pair[0]))[:show]:
            out("    %6d  %s (node %s)" % (count, name, ", ".join(str(node) for node in nodes)))
    if unrooted:
        out("photos under no root of the library: %d, in %d place(s) (they keep their native paths)"
            % (sum(group["count"] for group in unrooted), len(unrooted)))
        for group in unrooted[:show]:
            out("    %6d  %s" % (group["count"], group["group"]))
    return broken


def rebuild_derived(db_path, apply=False, out=print):
    """Say what is wrong with the library's derived tables (tagpup.store.derived) and, with `apply`, make
    them what the photos say. Returns 0 when they are (now) right, 1 when they are not. Counts only."""
    if not os.path.exists(db_path):
        raise SystemExit("There is no library at %s." % db_path)
    _refuse_newer(db_path)
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        version = schema.version(conn)
        if version < 19:
            out("the library is at schema %d: the derived tables are made by migration 19, which opening it with "
                "TagPup or the CLI applies" % version)
            return 1
        tables = derived.problems(conn)
        listed = people.stale(conn) if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'photo_people'").fetchone() else []
        ids = _person_ids_wrong(conn)
        has_ids = person_ids.present(conn)
    finally:
        conn.close()
    if listed:
        tables = tables + ["%d photo(s) list people the rule makes otherwise (photo_people)" % len(listed)]
    for line in tables + ids:
        out("  " + line)
    if not tables and not ids:
        out("the derived tables are what the photos say" + (", and every person id what its name is" if has_ids else ""))
        return 0
    if not apply:
        out("a dry run: nothing was written. --apply makes them so; only derived rows and columns change")
        return 1
    after = []
    if listed:
        out("photos' people rebuilt: %d photo(s) changed" % people.repair(db_path, listed))
        conn = db.connect(db.readonly_uri(db_path), uri=True)
        try:
            left = len(people.stale(conn))
        finally:
            conn.close()
        if left:
            after.append("%d photo(s) still list people the rule makes otherwise" % left)
    if len(tables) > bool(listed):
        _before, written, after_tables = derived.repair(db_path)
        after += after_tables
        out("rebuilt from %d photo(s): %d keyword row(s), %d folder(s), %d photo(s) in one, %d metadata row(s), "
            "%d word row(s), %d camera and lens row(s)"
            % (written["photos"], written["tag_rows"], written["folders"], written["in_a_folder"], written["meta_rows"],
               written["word_rows"], written["gear_rows"]))
    if ids:
        changed = person_ids.repair(db_path)
        out("person ids put right: %d face(s), %d listed person(s)" % (changed["faces"], changed["photo_people"]))
        conn = db.connect(db.readonly_uri(db_path), uri=True)
        try:
            after += _person_ids_wrong(conn)
        finally:
            conn.close()
    for line in after:
        out("  still wrong: " + line)
    return 1 if after else 0


def _person_ids_wrong(conn):
    """A line for each table whose rows hold a person id their name does not give (tagpup.store.person_ids)."""
    return ["%d row(s) of %s hold a person id their name does not give" % (found.rows, table)
            for table in person_ids.TABLES for found in [person_ids.out_of_step(conn, table)] if found.rows]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, help="the library's database file")
    parser.add_argument("--show", type=int, default=0, help="examples to list per rule (paths; default none)")
    parser.add_argument("--rebuild-derived", action="store_true",
                        help="make photo_tags, folders, photo_folder, photo_meta and the person ids what the photos and"
                             " the names say (a dry run)")
    parser.add_argument("--apply", action="store_true", help="with --rebuild-derived: write it")
    args = parser.parse_args(argv)
    if args.apply and not args.rebuild_derived:
        parser.error("--apply goes with --rebuild-derived")
    if args.rebuild_derived:
        return rebuild_derived(args.db, args.apply)
    return 1 if report(args.db, args.show) else 0


if __name__ == "__main__":
    sys.exit(main())
