"""What `tagpup.services.tree_fill` reads of a library, to know which keywords hold no node: the counts it reports and the keywords of
the photos that name no node. Reads only.
"""
import collections

from tagpup.core import vocabulary
from tagpup.store import derived, taxonomy


def numbers(conn):
    """The counts a tree-fill changes: nodes and photo keyword rows."""
    return {
        "nodes": conn.execute("SELECT COUNT(*) FROM tag_taxonomy").fetchone()[0],
        "keyword_rows": conn.execute("SELECT COUNT(*) FROM photo_tags").fetchone()[0],
    }


Keywords = collections.namedtuple("Keywords", "found uses photos under skipped roots")


def keywords(conn, tree, skip_roots=()):
    """The keywords no node is, as Keywords: `found` {fold: the keyword as the first photo spells it} of those a node can be made
    for, their `uses` and `photos`, `under` {fold} of those that would go under a person faces or photos use (a node would turn
    the person into a group), `skipped` how many were left out because their root is in `skip_roots` (folded names), and `roots`
    {fold of a root the tree lacks: [the root as spelled, keywords under it]}. `tree` is the derived.Tree."""
    found, under, roots, skipped = {}, set(), {}, 0
    skip = {derived.keyword(root) for root in skip_roots}
    uses, photos = 0, 0
    for (tags_json,) in conn.execute("SELECT tags FROM photos WHERE tags IS NOT NULL AND tags != '[]'"):
        here = False
        for text in set(derived.keywords_of(tags_json)):
            path = vocabulary.normalize(text)
            if not path or tree.find(text) is not None:
                continue
            fold = derived.keyword(text)
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
    return Keywords(found, uses, photos, under, skipped, {fold: [name, len(each)] for fold, (name, each) in roots.items()})
