"""The table of names the owner set aside from the names-to-review list, and the groups a person can be made under
(docs/ARCHITECTURE.md, "People by id, stage 2", "Names to review").

The list itself is a derived view (`person_ids.review_pairs`), never stored. The one table is `name_review_dismissals`
(migration 28): `name_key` (the name's `vocabulary.key`), `rows_seen` (the rows of faces and listed people the name held when it was
set aside) and `decided` (when). A name set aside stays hidden until it holds MORE rows than `rows_seen`: a name that gains rows
is new work. It is a preference, not a change of any photo or face, so it is not journaled; "show again" removes the row.

Every function does nothing on a library older than migration 28. The caller commits.
"""
import time

from tagpup.core import vocabulary
from tagpup.store import person_ids, taxonomy

TABLE = "name_review_dismissals"


def present(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE,)).fetchone() is not None


def dismissed(conn):
    """{name key: rows seen when it was set aside}."""
    if not present(conn):
        return {}
    return {key: rows for key, rows in conn.execute("SELECT name_key, rows_seen FROM %s" % TABLE)}


def dismiss(conn, key, rows, when=None):
    """Set the name whose key is `key` aside, holding `rows` rows now. Returns 1, or 0 on a library without the table."""
    if not present(conn):
        return 0
    conn.execute("INSERT INTO %s (name_key, rows_seen, decided) VALUES (?, ?, ?)"
                 " ON CONFLICT(name_key) DO UPDATE SET rows_seen = excluded.rows_seen, decided = excluded.decided" % TABLE,
                 (key, int(rows), when or time.strftime("%Y-%m-%d %H:%M:%S")))
    return 1


def forget(conn, key):
    """Take `key` off the set-aside list (show it again, or it was settled). Returns rows removed."""
    if not present(conn):
        return 0
    return conn.execute("DELETE FROM %s WHERE name_key = ?" % TABLE, (key,)).rowcount


def rows_of(review):
    """Every row that holds the name: faces, the people a face listed, and those a keyword listed (counted apart)."""
    return review.faces + review.listed + review.from_keyword


def split(reviews, aside):
    """(waiting, set aside, stale group rows) of `reviews` (person_ids.review_pairs): THE rule of what the owner has to settle. A name
    set aside stays hidden until it holds more rows than when it was (`aside`: dismissed()); a group tag's listed-only rows are not a
    name to settle (#986: the rebuild drops them, `tools/doctor.py --rebuild-derived`) and are counted apart; a FACE named after a group
    is a name ("branch")."""
    waiting, hidden, stale = [], [], 0
    for review in reviews:
        if review.why == "branch" and not review.faces:
            stale += review.listed + review.from_keyword
        elif review.key in aside and rows_of(review) <= aside[review.key]:
            hidden.append(review)
        else:
            waiting.append(review)
    return waiting, hidden, stale


def waiting(conn):
    """(the Reviews waiting for the owner, the Reviews set aside, stale group rows) of the library on `conn`: reads only."""
    return split(person_ids.review_pairs(conn), dismissed(conn))


def groups(conn):
    """[{id, tag, name, root}] of the places a person can be made: the roots that hold faces and the groups under them (a face
    node with a node under it), by tag. A person is not one -- a person has no tags under them."""
    if not taxonomy.tree_exists(conn):
        return []
    parents = {parent for (parent,) in conn.execute("SELECT DISTINCT parent_id FROM tag_taxonomy WHERE parent_id IS NOT NULL")}
    found = [{"id": node_id, "tag": tag, "name": name, "root": vocabulary.SEPARATOR not in tag}
             for node_id, tag, name in conn.execute("SELECT id, tag, name FROM tag_taxonomy WHERE has_face = 1")
             if tag and (vocabulary.SEPARATOR not in tag or node_id in parents)]
    return sorted(found, key=lambda group: vocabulary.tag_sort_key(group["tag"]))


def tag_exists(conn, tag):
    """Is `tag` a node of the library's tag tree already?"""
    return taxonomy.tree_exists(conn) and conn.execute("SELECT 1 FROM tag_taxonomy WHERE tag = ?", (tag,)).fetchone() is not None


def group(conn, group_id):
    """The group `groups` lists with this id, or None (a person, a node gone, a tag that holds no faces)."""
    for each in groups(conn):
        if each["id"] == group_id:
            return each
    return None


