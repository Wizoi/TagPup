"""What Suggest offered each photo, as the suggestion runs (tagpup.jobs.suggestions) read
and keep it: in the library, by the photo's id (tagpup.store.suggestions)."""
import os

from tagpup.store import db
from tagpup.store import suggestions as store


def saved_in(db_path, folder):
    """{photo as stored: entry} of what the library holds for the photos directly in
    `folder`; nothing for a library that is not there."""
    if not os.path.exists(db_path):
        return {}
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return store.in_folder(conn, folder)
    finally:
        conn.close()


def keep(db_path, photo, found, model_key=None):
    """Keep `found` as what was suggested for `photo`, through the write lock."""
    db.write_with_connection(db_path, lambda conn: store.put(conn, photo, found, model_key),
                             label="suggestions for %s" % os.path.basename(photo))


def offer(db_path, offered, label="folder consensus"):
    """Change what is offered for each photo in `offered`, [(photo, (tags, people,
    title))], leaving what the suggester made as it was. Returns rows changed."""
    return db.write_with_connection(
        db_path, lambda conn: sum(store.offer(conn, photo, tags, people, title)
                                  for photo, (tags, people, title) in offered), label=label)
