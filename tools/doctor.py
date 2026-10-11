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


def _refuse_newer(db_path):
    """A library made by a newer TagPup than this tool's is not written: its rules are the newer one's. (The
    report, which only reads, is let through with a note: it is what an older checkout has for recovery.)"""
    problem = schema.newer_problem(db_path)
    if problem:
        raise SystemExit(problem)


def report(db_path, show=0, out=print):
    """Report on the library at `db_path`. Returns the number of rules broken."""
    if not os.path.exists(db_path):
        raise SystemExit("There is no library at %s." % db_path)
    note = schema.newer_note(db_path)   # the report only reads: a newer library is shown as it is, with a note
    if note:
        out(note)
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
        to_review = checks.names_to_review(conn)
        sharing = checks.people_sharing_a_leaf(conn)
        words = search_index.present(conn)
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
    if to_review[0] or to_review[1]:
        out("names to review: %d waiting, %d set aside (reported, not broken: a name no person's tag is, for the owner to settle one "
            "at a time in TagTuner's Review People -- make a person, link it, unname the faces or set it aside; nothing is "
            "converted by itself)" % to_review[:2])
    for label, found in (("no person node", nameless.none), ("several person nodes", nameless.several)):
        if found:
            out("names with %s: %d, on %d row(s) of faces and photos' people (reported, not broken: they have no "
                "person id, and none is guessed)" % (label, len(found), sum(found.values())))
            for name, count in sorted(found.items(), key=lambda pair: (-pair[1], pair[0]))[:show]:
                out("    %6d  %s" % (count, name))
    if sharing:
        out("people sharing a leaf: %d name(s), %d people (reported, not broken: two people called alike are asked for by path; "
            "rename one by hand in TagTuner's Rename Person)" % (len(sharing), sum(len(tags) for tags in sharing.values())))
        for leaf, tags in sorted(sharing.items())[:show]:
            out("    %s" % "  |  ".join(tags))
    if nameless.one:
        out("names one person is called whose rows are linked to nobody: %d, on %d face(s) decided by hand, %d other face(s) and %d "
            "listed person(s) (reported, not broken: nothing links them by itself, so they are on no person's page; "
            "TagTuner's names to review links one)"
            % (len(nameless.one), sum(each[1] for each in nameless.one.values()), sum(each[2] for each in nameless.one.values()),
               sum(each[3] for each in nameless.one.values())))
        for name, (_person, by_hand, by_guess, listed_people) in sorted(
                nameless.one.items(), key=lambda pair: (-(pair[1][1] + pair[1][2] + pair[1][3]), pair[0]))[:show]:
            out("    %6d  %s" % (by_hand + by_guess + listed_people, name))
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
        derived_problems = derived.problems(conn)
        tables = list(derived_problems)
        listed = people.stale(conn)   # photo_people is migration 6's; this tool starts at 19
        ids = _person_ids_wrong(conn)
        has_ids = person_ids.present(conn)
    finally:
        conn.close()
    if listed:
        tables = tables + ["%d photo(s) list people the rule makes otherwise (photo_people)" % len(listed)]
    for line in tables + ids:
        out("  " + line)
    if not tables and not ids:
        out("the derived tables are what the photos say" + (", and every person's name what their node is called" if has_ids else ""))
        return 0
    if not apply:
        out("a dry run: nothing was written. --apply makes them so; only derived rows and columns change")
        return 1
    after = []
    if ids:
        # First: a face's name is the cache the photo's list is written from, so the lists are rebuilt from the faces as they
        # should be (rebuilt before, the list took the stale name and was stale again).
        changed = person_ids.repair(db_path)
        out("people's names and ids put right: %d face(s), %d listed person(s)" % (changed["faces"], changed["photo_people"]))
        conn = db.connect(db.readonly_uri(db_path), uri=True)
        try:
            after += _person_ids_wrong(conn)
            listed = people.stale(conn)
        finally:
            conn.close()
    if listed:
        out("photos' people rebuilt: %d photo(s) changed" % people.repair(db_path, listed))
        conn = db.connect(db.readonly_uri(db_path), uri=True)
        try:
            left = len(people.stale(conn))
        finally:
            conn.close()
        if left:
            after.append("%d photo(s) still list people the rule makes otherwise" % left)
    if derived_problems:   # (read once, before the ids were put right: the people's lists are not these)
        _before, written, after_tables = derived.repair(db_path)
        after += after_tables
        out("rebuilt from %d photo(s): %d keyword row(s), %d folder(s), %d photo(s) in one, %d metadata row(s), "
            "%d word row(s), %d camera and lens row(s)"
            % (written["photos"], written["tag_rows"], written["folders"], written["in_a_folder"], written["meta_rows"],
               written["word_rows"], written["gear_rows"]))
    for line in after:
        out("  still wrong: " + line)
    return 1 if after else 0


def _person_ids_wrong(conn):
    """A line for each table whose rows hold a name that is not their person's (tagpup.store.person_ids: the id is the person and
    the name a cache of the node's leaf) or an id of a node that is gone. A name with no id is not this: it is reported by
    names_without_a_person, and linked only by the owner (the names to review)."""
    return ["%d row(s) of %s hold a name that is not their person's" % (found.rows, table)
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
    with schema.reading_newer():   # the report reads only; the writer above is refused by rebuild_derived
        return 1 if report(args.db, args.show) else 0


if __name__ == "__main__":
    sys.exit(main())
