"""Who the library knows: the names the pages offer while a person is typed.

Both servers listed them with their own copy of this, asking the tag tree about each
person with a query of its own.
"""
import json
import os

from tagpup.core import vocabulary
from tagpup.store import db


def names(db_path, keywords_too=False, include_hidden=False):
    """The people the library knows, sorted: the names given to faces -- and, with
    `keywords_too`, the people photos' keywords name.

    A person whose every node in the tag tree is hidden from autocomplete, or sits in a
    hidden branch, is left out, unless `include_hidden`: that answers "does this person
    exist?", which a hidden person does -- asked of the shorter list, naming one of them
    offered to create them as someone new.
    """
    if not os.path.exists(db_path):
        return []
    conn = db.connect(db_path, timeout=30.0)
    try:
        people = {name for (name,) in conn.execute("SELECT DISTINCT name FROM faces WHERE name IS NOT NULL")}
        if keywords_too:
            for (people_json,) in conn.execute("SELECT people FROM photos"):
                try:
                    people.update(name for name in json.loads(people_json or "[]") if name)
                except Exception:
                    pass
        has_tree = conn.execute("SELECT name FROM sqlite_master WHERE type='table'"
                                " AND name='tag_taxonomy'").fetchone()
        if not include_hidden and has_tree:
            hidden = {tag for (tag,) in conn.execute(
                "SELECT tag FROM tag_taxonomy WHERE hidden_from_autocomplete = 1")}
            filed = {}
            for tag, name in conn.execute("SELECT tag, name FROM tag_taxonomy WHERE has_face = 1"):
                filed.setdefault(name, []).append(tag)
            people = {person for person in people
                      if not (filed.get(person) and all(vocabulary.hidden_by(tag, hidden)
                                                        for tag in filed[person]))}
    finally:
        conn.close()
    return sorted(person for person in people if person)
