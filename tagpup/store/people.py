"""Who the library knows: the names the pages offer while a person is typed, and
renaming somebody in the places that hold a bare name.

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


def rename(conn, old, new):
    """Faces named `old` are named `new`, and so is `old` in each photo's list of
    people, listed once. Names match exactly. The caller commits. Returns (faces
    renamed, photos changed).

    Both apps did this with a copy of their own. TagTuner matched faces whatever their
    case, with LOWER(name), which no index serves. Both found the photos with LIKE over
    the JSON text, which spells a name past ASCII with escapes, so a name with an
    accent was not found (docs/findings.md, #38). Both spellings are looked for here.
    """
    faces = conn.execute("UPDATE faces SET name = ? WHERE name = ?", (new, old)).rowcount
    spellings = sorted({old, json.dumps(old)[1:-1]})
    rows = conn.execute("SELECT rowid, people FROM photos WHERE "
                        + " OR ".join("instr(people, ?) > 0" for _ in spellings),
                        spellings).fetchall()
    changed = 0
    for rowid, people_json in rows:
        try:
            people = json.loads(people_json or "[]")
        except (TypeError, ValueError):
            continue
        if old not in people:
            continue
        renamed = list(dict.fromkeys(new if person == old else person for person in people))
        conn.execute("UPDATE photos SET people = ? WHERE rowid = ?", (json.dumps(renamed), rowid))
        changed += 1
    return faces, changed
