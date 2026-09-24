"""The suggestions table: what Suggest offered for each photo, kept by the photo's id.

It replaces a JSON file beside each library, keyed by path, that nothing but a rename
through TagPup's save kept in step: deleting a photo, removing its folder or relinking
it left its entry, and a photo renamed another way lost its suggestions and was
suggested for again (docs/findings.md, #64). A row goes with its photo, by a trigger,
whichever connection deletes it, and a rename moves nothing.

A row holds what the panel shows -- tags, people, title -- and the suggester's own
output (`raw`), from which a folder's consensus is taken again as photos are added.
`before_consensus` says `raw` is still as the suggester made it. A photo whose
suggestion failed has a row with its `error`, so the next run tries it again.
"""
import json
import time

from tagpup.core import paths

#: The columns of a row, in the order `entry` reads them.
COLUMNS = "p.path, s.tags, s.people, s.title, s.raw, s.before_consensus, s.error"


def entry(tags, people, title, raw, before_consensus, error):
    """A row as the page and the run hold it: {tags, people, title, raw_suggestions,
    raw_before_consensus[, error]} -- the shape the JSON file held."""
    found = {"tags": json.loads(tags or "[]"), "people": json.loads(people or "[]"), "title": title,
             "raw_suggestions": json.loads(raw) if raw else {"suggested_tags": []},
             "raw_before_consensus": bool(before_consensus)}
    if error is not None:
        found["error"] = error
    return found


def in_folder(conn, folder):
    """{path as stored: entry} of the photos under `folder`, at any depth, that have a
    row: what a run over the folder suggests for, its scan walking the folders below
    (docs/findings.md, #91)."""
    where, params = paths.sql_under("p.path", folder)
    found = {}
    for row in conn.execute(
            "SELECT " + COLUMNS + " FROM suggestions s JOIN photos p ON p.id = s.photo_id WHERE " + where, params):
        found[row[0]] = entry(*row[1:])
        # The suggester's output names the path it was made for; a photo renamed since
        # is under its row's path now (#90).
        if isinstance(found[row[0]]["raw_suggestions"], dict) and "path" in found[row[0]]["raw_suggestions"]:
            found[row[0]]["raw_suggestions"]["path"] = row[0]
    return found


def put(conn, photo_path, found, model=None):
    """Keep `found` (an entry) as what was suggested for the photo, replacing any before.
    A photo with no row gets one (photos.ensure_row). The caller commits."""
    from tagpup.store import photos   # photos imports this module's neighbours
    put_for(conn, photos.ensure_row(conn, photo_path), found, model)


def put_for(conn, photo_id, found, model=None):
    """put, for the photo whose row is `photo_id`. The caller commits."""
    conn.execute(
        "INSERT OR REPLACE INTO suggestions (photo_id, tags, people, title, raw, before_consensus, error,"
        " model, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (photo_id, json.dumps(found.get("tags") or []), json.dumps(found.get("people") or []), found.get("title"),
         json.dumps(found.get("raw_suggestions")) if found.get("raw_suggestions") is not None else None,
         1 if found.get("raw_before_consensus") else 0, found.get("error"), model,
         time.strftime("%Y-%m-%d %H:%M:%S")))


def offer(conn, photo_path, tags, people, title):
    """Change what is offered for a photo -- the tags, people and title a folder's
    consensus settled on -- leaving what the suggester made as it was. Returns rows
    changed. The caller commits."""
    where, params = paths.sql_equals("path", photo_path)
    return conn.execute(
        "UPDATE suggestions SET tags = ?, people = ?, title = ?"
        " WHERE photo_id IN (SELECT id FROM photos WHERE " + where + ")",
        (json.dumps(tags or []), json.dumps(people or []), title) + params).rowcount
