"""Merge a person's bare tag into the People/<name> tag that means the same thing.

Indexing used to record every person it found as a bare root tag, beside the
hierarchical keyword that already named them, so one person existed twice: as
"Josephine Sandoval" and as "People/Josephine Sandoval". Both did the same job, which made
the Add Person list offer a choice nobody could make correctly.

That source is fixed -- people now enter the taxonomy under a people root -- but the
pairs it already made are still there, and it ran on every index, so a library may
have accumulated many. This merges them.

Only the taxonomy is touched. photos.tags is reported and left alone: it holds what the
file holds, and a photo file legitimately carries both a hierarchy and, in some
libraries, a flat leaf. Rewriting those rows only made the database disagree with the
file until the next index put it back. photos.people is left alone for the same reason:
leaf names are its correct content.

scripts/merge_duplicate_person_tags.py and the MCP server's tool both call
`merge_duplicate_person_tags`, on the maintenance scaffold (tagpup.services.maintenance).
"""
from tagpup.core import vocabulary
from tagpup.services import maintenance
from tagpup.store import db
from tagpup.store import photos as store_photos
from tagpup.store import taxonomy as store_taxonomy


def leaf_of(tag):
    return vocabulary.key(vocabulary.leaf_of(tag))


def find_duplicates(conn):
    """{bare tag: the People path naming the same person} in the tree of the library open
    on `conn`, which has one."""
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
    return {
        tag: pathed[leaf_of(tag)]
        for tag in tags
        if "/" not in tag and leaf_of(tag) in pathed and tag.strip().lower() not in people_roots
    }


def _plan(library):
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        if not store_taxonomy.tree_exists(conn):
            return maintenance.Plan(refused="no tag_taxonomy table in the library %s" % library.name)
        duplicates = find_duplicates(conn)
        node_ids = store_taxonomy.node_ids(conn)
        # Reported for information only; these rows are not changed.
        affected = [(photo_id, photo_path) for photo_id, photo_path, photo_tags in store_photos.tags_by_photo(conn)
                    if any(t in duplicates for t in photo_tags)]
    finally:
        conn.close()
    bare = sorted(duplicates)
    return maintenance.Plan(
        size=len(duplicates),
        counts={"duplicates": len(duplicates), "affected_photos": len(affected)},
        ids={"duplicates": [node_ids[tag] for tag in bare if tag in node_ids],
             "affected_photos": [photo_id for photo_id, _path in affected]},
        reveal={"duplicates": [(tag, duplicates[tag]) for tag in bare],
                "affected_photos": [photo_path for _id, photo_path in affected]},
        work=bare)


def remove_nodes(library, tags):
    """Take the nodes `tags` out of the tree, under the library's write lock. Returns how
    many were removed, not how many were asked for."""
    def delete(conn):
        return sum(store_taxonomy.remove_node(conn, tag) for tag in tags)

    return db.write_with_connection(library.path, delete, label="merge duplicate person tags")


def _write(library, planned, result):
    result.changed = remove_nodes(library, planned.work)


def _remaining(library):
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return {"duplicates": len(find_duplicates(conn))}
    finally:
        conn.close()


def merge_duplicate_person_tags(library, apply=False):
    """Plan, and with `apply` make, the removal of every bare tag in `library`'s tree that
    duplicates a People path naming the same person. Photos are counted, not changed. A
    Result, on the maintenance scaffold: `changed` is tree nodes removed; refused when
    the library has no tree."""
    return maintenance.run(library, "merge-person-tags", _plan, _write, apply=apply,
                           remaining=_remaining)
