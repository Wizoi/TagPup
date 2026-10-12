"""`tree-fill`: the one-time command that gives a library's file keywords their nodes (owner, 2026-10-10, "every tag has an id").

Indexing now gives a keyword it reads a node when it is first seen (tagpup.store.taxonomy.ensure_node). A library indexed before
holds some with none: file keywords no node of the tag tree is (photo_index: 201 of them, 888 uses on 722 photos). This makes their
nodes, in ONE transaction, by the same function: a keyword its own path (every level above it), a bare one a node of its own at the
top, never a person (owner, 2026-10-11: a keyword is its path). The owner then merges them into the right branch in the tag view of
the library, with the tag merge, which moves faces and photo lists by id. A name a face or a person field holds is not made here:
nothing links a name to a person, and every writer gives a name its node (the live libraries hold none without).

Left as they are, and counted: a keyword under a person that faces or photos use (a node would turn the person into a group). Run
twice, the second finds nothing to make. `skip_roots` leaves out the keywords of a root the owner removed on purpose (photo_index:
197 of its 200 keywords share one root the tree does not have).

Not journaled: the tag tree is not (`undo` cannot take it back); the command line takes a backup first. A rehearsal (no
`apply`) runs the same work in the library's write lock and rolls it back, so its counts are what an apply would change.
"""

from tagpup.store import db, derived, people, taxonomy, tree_scan

#: How many keywords sharing one root the tree lacks (and at least half of all) the command says so prominently for.
SHARED_ROOT_MIN = 10


class _Rehearsed(Exception):
    """Ends the transaction of a rehearsal, which is rolled back with the report."""

    def __init__(self, report):
        super().__init__("rehearsed")
        self.report = report


def _run(conn, skip_roots=()):
    """The work, in the caller's transaction: its report."""
    tree = derived.Tree.read(conn)
    scan = tree_scan.keywords(conn, tree, skip_roots)
    keywords, uses, photos = scan.found, scan.uses, scan.photos
    before = tree_scan.numbers(conn)
    with people.tree_edit(conn):
        for text in sorted(keywords.values()):
            taxonomy.ensure_node(conn, text, tree=tree, edited=True)
    after = tree_scan.numbers(conn)
    return {
        "keywords": {"without_a_node": len(keywords), "uses": uses, "photos": photos,
                     "left_under_a_person": len(scan.under), "skipped_by_root": scan.skipped,
                     "largest_missing_root": max((count for _name, count in scan.roots.values()), default=0)},
        "made": {"nodes": after["nodes"] - before["nodes"]},
        "keyword_rows_added": after["keyword_rows"] - before["keyword_rows"],
        "reveal": {"keywords": sorted(keywords.values()),
                   "missing_roots": sorted(scan.roots.values(), key=lambda pair: (-pair[1], pair[0]))},
    }


def run(library, apply=False, skip_roots=()):
    """The report of making the nodes for the library's keywords: with `apply`, made, in one transaction (the caller
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
