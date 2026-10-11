"""What `tagpup.services.tree_fill` reads of a library, to know which names and keywords hold no node: the counts it reports, the
keywords of the photos that name no node, the names faces and listed people hold with no id, and the photos listing them.
Reads only.
"""
import collections

from tagpup.core import vocabulary
from tagpup.store import derived, person_ids


def numbers(conn):
    """The counts a tree-fill changes: nodes, faces and listed people with a person, and photo keyword rows."""
    return {
        "nodes": conn.execute("SELECT COUNT(*) FROM tag_taxonomy").fetchone()[0],
        "faces": conn.execute("SELECT COUNT(*) FROM faces WHERE tag_id IS NOT NULL").fetchone()[0],
        "listed": conn.execute("SELECT COUNT(*) FROM photo_people WHERE tag_id IS NOT NULL").fetchone()[0],
        "keyword_rows": conn.execute("SELECT COUNT(*) FROM photo_tags").fetchone()[0],
    }


def keywords(conn, tree, known):
    """({fold: the keyword as the first photo spells it} of the keywords no node is, the uses and the photos of them,
    {fold} of those left: bare keywords that are a person's name). `tree` is the derived.Tree, `known` the person_ids.People."""
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
