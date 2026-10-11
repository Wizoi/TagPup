"""`tree-fill`: the one-time command that gives a library's names and keywords their nodes (owner, 2026-10-10, "every tag has an
id").

Indexing now gives a keyword it reads, and a name a face or a photo is given, a node when it is first seen
(tagpup.store.taxonomy.ensure_node). A library indexed before holds some with none: file keywords no node of the tag tree
is (photo_index: 201 of them, 888 uses on 722 photos) and names faces and listed people hold with no id (kr-track 12, photo_index
4). This makes their nodes, in ONE transaction, by the same function, and links the rows: a keyword its own path (every level
above it), a name a node directly under the tree's root marked as a person. The owner then merges them into the right branch in
the tag view of the library, with the tag merge, which moves faces and photo lists by id.

Left as they are, and counted: a name two people have, a name a group has, a bare keyword that is a person's name (their
keyword, not a tag of its own), and a keyword under a person that faces or photos use (a node would turn the person into a
group). Run twice, the second finds nothing to make. `skip_roots` leaves out the keywords of a root the owner removed on purpose
(photo_index: 197 of its 200 keywords share one root the tree does not have).

Not journaled: the tag tree is not (`undo` cannot take it back); the command line takes a backup first. A rehearsal (no
`apply`) runs the same work in the library's write lock and rolls it back, so its counts are what an apply would change.
"""

from tagpup.store import db, derived, people, person_ids, taxonomy, tree_scan

#: How many keywords sharing one root the tree lacks (and at least half of all) the command says so prominently for.
SHARED_ROOT_MIN = 10

class _Rehearsed(Exception):
    """Ends the transaction of a rehearsal, which is rolled back with the report."""

    def __init__(self, report):
        super().__init__("rehearsed")
        self.report = report


def _run(conn, skip_roots=()):
    """The work, in the caller's transaction: its report."""
    tree, known = derived.Tree.read(conn), person_ids.read(conn)
    scan = tree_scan.keywords(conn, tree, known, skip_roots)
    keywords, uses, photos, kept = scan.found, scan.uses, scan.photos, scan.left
    names, name_rows, name_left = tree_scan.names(conn, known)
    before = tree_scan.numbers(conn)
    made_by_names = 0
    with people.tree_edit(conn):
        for text in sorted(keywords.values()):
            taxonomy.ensure_node(conn, text, tree=tree, edited=True)
        by_keywords = tree_scan.numbers(conn)["nodes"] - before["nodes"]
        for name in sorted(names.values()):
            node_id, made = taxonomy.ensure_node(conn, name, person=True, tree=tree, edited=True)
            made_by_names += 1 if made else 0
    # A listed person a photo's metadata names is its name's person once the person is there (a keyword of the photo is
    # found by the tree edit; a person field is not a keyword).
    loose = tree_scan.photos_listing_a_name_with_no_id(conn)
    rebuilt = people.rebuild(conn, loose) if loose and (names or keywords) else 0
    after = tree_scan.numbers(conn)
    return {
        "keywords": {"without_a_node": len(keywords), "uses": uses, "photos": photos, "left_a_persons_name": len(kept),
                     "left_under_a_person": len(scan.under), "skipped_by_root": scan.skipped,
                     "largest_missing_root": max((count for _name, count in scan.roots.values()), default=0)},
        "names": {"without_a_node": len(names), "faces": name_rows["faces"], "listed": name_rows["photo_people"],
                  "left_several_people": name_left["several"], "left_a_group": name_left["branch"],
                  "left_one_person_unlinked": name_left["one"]},
        "made": {"nodes": after["nodes"] - before["nodes"], "for_keywords": by_keywords,
                 "for_names": after["nodes"] - before["nodes"] - by_keywords, "persons_among_names": made_by_names},
        "linked": {"faces": after["faces"] - before["faces"], "listed_people": after["listed"] - before["listed"]},
        "keyword_rows_added": after["keyword_rows"] - before["keyword_rows"],
        "photos_listed_again": rebuilt,
        "reveal": {"keywords": sorted(keywords.values()), "names": sorted(names.values()),
                   "missing_roots": sorted(scan.roots.values(), key=lambda pair: (-pair[1], pair[0]))},
    }


def run(library, apply=False, skip_roots=()):
    """The report of making the nodes for the library's keywords and names: with `apply`, made, in one transaction (the caller
    has taken a backup); without, rehearsed in the same work and rolled back. Raises what the database raises: nothing is
    half made."""
    if apply:
        return db.write_with_connection(library.path, lambda conn: _run(conn, skip_roots), label="tree-fill")

    def rehearse(conn):
        raise _Rehearsed(_run(conn, skip_roots))
    try:
        db.write_with_connection(library.path, rehearse, label="tree-fill rehearsal")
    except _Rehearsed as done:
        return done.report
    raise AssertionError("a rehearsal ended without a report")
