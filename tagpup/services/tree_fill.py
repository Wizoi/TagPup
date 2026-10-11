"""`tree-fill`: the one-time command that gives a library's names and keywords their nodes (owner, 2026-10-10, "every tag has an
id").

Indexing now gives a keyword it reads, and a name a face or a photo is given, a node when it is first seen
(tagpup.store.taxonomy.ensure_node). A library indexed before holds some with none: file keywords no node of the tag tree
is (photo_index: 201 of them, 888 uses on 722 photos) and names faces and listed people hold with no id (kr-track 12, photo_index
4). This makes their nodes, in ONE transaction, by the same function, and links the rows: a keyword its own path (every level
above it), a name a node directly under the tree's root marked as a person. The owner then merges them into the right branch in
the tag view of the library, with the tag merge, which moves faces and photo lists by id.

Left as they are, and counted: a name two people have, a name a group has, and a bare keyword that is a person's name (their
keyword, not a tag of its own). Run twice, the second finds nothing to make.

Not journaled: the tag tree is not (`undo` cannot take it back); the command line takes a backup first. A rehearsal (no
`apply`) runs the same work in the library's write lock and rolls it back, so its counts are what an apply would change.
"""
import collections

from tagpup.core import vocabulary
from tagpup.store import db, derived, people, person_ids, taxonomy

class _Rehearsed(Exception):
    """Ends the transaction of a rehearsal, which is rolled back with the report."""

    def __init__(self, report):
        super().__init__("rehearsed")
        self.report = report


def _numbers(conn):
    """The counts an apply changes."""
    return {
        "nodes": conn.execute("SELECT COUNT(*) FROM tag_taxonomy").fetchone()[0],
        "faces": conn.execute("SELECT COUNT(*) FROM faces WHERE tag_id IS NOT NULL").fetchone()[0],
        "listed": conn.execute("SELECT COUNT(*) FROM photo_people WHERE tag_id IS NOT NULL").fetchone()[0],
        "keyword_rows": conn.execute("SELECT COUNT(*) FROM photo_tags").fetchone()[0],
    }


def _keywords(conn, tree, known):
    """({fold: the keyword as the first photo spells it} of the keywords to make nodes for, the photos' and uses' counts
    of them, [the keywords left: bare ones that are a person's name])."""
    found, left = {}, set()
    uses, photos = 0, 0
    for (tags_json,) in conn.execute("SELECT tags FROM photos WHERE tags IS NOT NULL AND tags != '[]'"):
        here = False
        for text in set(derived.keywords_of(tags_json)):
            path = vocabulary.normalize(text)
            if not path or tree.find(text) is not None:
                continue
            if vocabulary.SEPARATOR not in path and known.why_not(path) in (None, "several"):
                left.add(derived.keyword(text))
                continue
            found.setdefault(derived.keyword(text), text)
            uses, here = uses + 1, True
        photos += 1 if here else 0
    return found, uses, photos, left


def _names(conn, known):
    """({fold: the name as the rows spell it} of the names with no id and no person to give one, rows of them in faces and in
    photo_people; {why: how many names} of those left: "several" people have it, a "branch" does, "one" person has it but the
    rows are linked to nobody)."""
    found, rows, kinds = {}, collections.Counter(), {}
    for table in person_ids.TABLES:
        for name, count in conn.execute(
                "SELECT name, COUNT(*) FROM %s WHERE tag_id IS NULL AND name IS NOT NULL AND name != '' GROUP BY name" % table):
            rows[table] += count
            why = known.why_not(name) or "one"
            kinds.setdefault(vocabulary.key(name), why)
            if why == "none":
                found.setdefault(vocabulary.key(name), name)
    return found, rows, collections.Counter(why for why in kinds.values() if why != "none")


def _run(conn):
    """The work, in the caller's transaction: its report."""
    tree, known = derived.Tree.read(conn), person_ids.read(conn)
    keywords, uses, photos, kept = _keywords(conn, tree, known)
    names, name_rows, name_left = _names(conn, known)
    before = _numbers(conn)
    made_by_names = 0
    with people.tree_edit(conn):
        for text in sorted(keywords.values()):
            taxonomy.ensure_node(conn, text, tree=tree, edited=True)
        by_keywords = conn.execute("SELECT COUNT(*) FROM tag_taxonomy").fetchone()[0] - before["nodes"]
        for name in sorted(names.values()):
            node_id, made = taxonomy.ensure_node(conn, name, person=True, tree=tree, edited=True)
            made_by_names += 1 if made else 0
    # A listed person a photo's metadata names is its name's person once the person is there (a keyword of the photo is
    # found by the tree edit; a person field is not a keyword).
    loose = [photo_id for (photo_id,) in conn.execute(
        "SELECT DISTINCT photo_id FROM photo_people WHERE tag_id IS NULL AND name IS NOT NULL AND name != ''")]
    rebuilt = people.rebuild(conn, loose) if loose and (names or keywords) else 0
    after = _numbers(conn)
    return {
        "keywords": {"without_a_node": len(keywords), "uses": uses, "photos": photos, "left_a_persons_name": len(kept)},
        "names": {"without_a_node": len(names), "faces": name_rows["faces"], "listed": name_rows["photo_people"],
                  "left_several_people": name_left["several"], "left_a_group": name_left["branch"],
                  "left_one_person_unlinked": name_left["one"]},
        "made": {"nodes": after["nodes"] - before["nodes"], "for_keywords": by_keywords,
                 "for_names": after["nodes"] - before["nodes"] - by_keywords, "persons_among_names": made_by_names},
        "linked": {"faces": after["faces"] - before["faces"], "listed_people": after["listed"] - before["listed"]},
        "keyword_rows_added": after["keyword_rows"] - before["keyword_rows"],
        "photos_listed_again": rebuilt,
        "reveal": {"keywords": sorted(keywords.values()), "names": sorted(names.values())},
    }


def run(library, apply=False):
    """The report of making the nodes for the library's keywords and names: with `apply`, made, in one transaction (the caller
    has taken a backup); without, rehearsed in the same work and rolled back. Raises what the database raises: nothing is
    half made."""
    if apply:
        return db.write_with_connection(library.path, _run, label="tree-fill")

    def rehearse(conn):
        raise _Rehearsed(_run(conn))
    try:
        db.write_with_connection(library.path, rehearse, label="tree-fill rehearsal")
    except _Rehearsed as done:
        return done.report
    raise AssertionError("a rehearsal ended without a report")
