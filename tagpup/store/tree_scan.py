"""What `tagpup.services.tree_fill` reads of a library, to know which names and keywords hold no node: the counts it reports, the
keywords of the photos that name no node, the names faces and listed people hold with no id, and the photos listing them.
Reads only.
"""
import collections

from tagpup.core import vocabulary
from tagpup.store import derived, person_ids, taxonomy


def numbers(conn):
    """The counts a tree-fill changes: nodes, faces and listed people with a person, and photo keyword rows."""
    return {
        "nodes": conn.execute("SELECT COUNT(*) FROM tag_taxonomy").fetchone()[0],
        "faces": conn.execute("SELECT COUNT(*) FROM faces WHERE tag_id IS NOT NULL").fetchone()[0],
        "listed": conn.execute("SELECT COUNT(*) FROM photo_people WHERE tag_id IS NOT NULL").fetchone()[0],
        "keyword_rows": conn.execute("SELECT COUNT(*) FROM photo_tags").fetchone()[0],
    }


Keywords = collections.namedtuple("Keywords", "found uses photos left under skipped roots")


def keywords(conn, tree, known, skip_roots=()):
    """The keywords no node is, as Keywords: `found` {fold: the keyword as the first photo spells it} of those a node can be made
    for, their `uses` and `photos`, `left` {fold} of the bare ones that are a person's name, `under` {fold} of those that would go
    under a person faces or photos use (a node would turn the person into a group), `skipped` how many were left out because their
    root is in `skip_roots` (folded names), and `roots` {fold of a root the tree lacks: [the root as spelled, keywords under it]}.
    `tree` is the derived.Tree, `known` the person_ids.People."""
    found, left, under, roots, skipped = {}, set(), set(), {}, 0
    skip = {derived.keyword(root) for root in skip_roots}
    uses, photos = 0, 0
    for (tags_json,) in conn.execute("SELECT tags FROM photos WHERE tags IS NOT NULL AND tags != '[]'"):
        here = False
        for text in set(derived.keywords_of(tags_json)):
            path = vocabulary.normalize(text)
            if not path or tree.find(text) is not None:
                continue
            fold = derived.keyword(text)
            if vocabulary.SEPARATOR not in path and known.why_not(path) in (None, "several"):
                left.add(fold)
                continue
            root = vocabulary.segments(path)[0]
            if derived.keyword(root) in skip:
                skipped += 1 if fold not in found else 0
                found.setdefault(fold, None)
                continue
            if fold not in found and taxonomy.under_a_used_person(conn, tree, text):
                under.add(fold)
                continue
            if fold in under:
                continue
            if fold not in found:
                if tree.find(root) is None:
                    roots.setdefault(derived.keyword(root), [root, set()])[1].add(fold)
            found.setdefault(fold, text)
            uses, here = uses + 1, True
        photos += 1 if here else 0
    found = {fold: text for fold, text in found.items() if text is not None}
    return Keywords(found, uses, photos, left, under, skipped, {fold: [name, len(each)] for fold, (name, each) in roots.items()})


def names(conn, known):
    """({fold: the name as the rows spell it} of the names that hold no id and no person is called, the rows of them by table,
    {why: how many names} of those left: "several" people have it, a "branch" does, "one" person has it but the rows are linked
    to nobody)."""
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


def photos_listing_a_name_with_no_id(conn):
    """The ids of the photos that list a person by a name alone."""
    return [photo_id for (photo_id,) in conn.execute(
        "SELECT DISTINCT photo_id FROM photo_people WHERE tag_id IS NULL AND name IS NOT NULL AND name != ''")]
