"""Face rows for tests, written in plain SQL.

A face points at its photo by id (migration 4). A test that seeds faces names the photo
by path, as the photos a person sees are named; this makes the photo's row where the
test has not, holding the path and nothing else, as photos.ensure_row does. Plain SQL
rather than the store, so a fixture does not agree with the code it tests.
"""
import json

#: The faces with their photo's path: `SELECT p.path, f.name FROM ` + FACES_WITH_PATHS.
FACES_WITH_PATHS = "faces f JOIN photos p ON p.id = f.photo_id"


def photo_id(conn, photo_path):
    """The id of the photo row stored under exactly `photo_path`, made if there is none."""
    row = conn.execute("SELECT id FROM photos WHERE path = ?", (photo_path,)).fetchone()
    if row:
        return row[0]
    return conn.execute("INSERT INTO photos (path, tags, people, captions, raw_metadata)"
                        " VALUES (?, '[]', '[]', '[]', '{}')", (photo_path,)).lastrowid


def configured_model():
    """The model key search reads: the one the config names, as PhotoIndex uses it."""
    from tagpup import config
    from tagpup.store import embeddings
    return embeddings.model_key(**config.embedder_settings())


#: A photo's vectors with its path: `SELECT p.path, e.vector FROM ` + VECTORS_WITH_PATHS.
VECTORS_WITH_PATHS = "embeddings e JOIN photos p ON p.id = e.photo_id"


def add_vector(conn, photo_path, vector, model=None, mtime=None, size=None):
    """Keep `vector` (float32 bytes) as the photo's CLIP vector under `model`, the
    configured one unless given, stamped `mtime` and `size` (the photo row's, unless
    given). The photo's row is made if it has none. The caller commits."""
    photo = photo_id(conn, photo_path)
    if mtime is None and size is None:
        mtime, size = conn.execute("SELECT mtime, size FROM photos WHERE id = ?", (photo,)).fetchone()
    conn.execute("INSERT OR REPLACE INTO embeddings (photo_id, model, mtime, size, vector) VALUES (?, ?, ?, ?, ?)",
                 (photo, model or configured_model(), mtime, size, vector))


def add_face(conn, photo_path, box=(0, 0, 10, 10), **columns):
    """Insert a face into the photo at `photo_path`; `columns` are any others of faces
    (embedding, name, prob, name_source, excluded, excluded_reason, id). A box that is
    not a string is written as JSON. Returns the face's id. The caller commits."""
    values = dict(columns, photo_id=photo_id(conn, photo_path),
                  box=box if isinstance(box, str) else json.dumps(list(box)))
    names = sorted(values)
    return conn.execute("INSERT INTO faces (%s) VALUES (%s)" % (", ".join(names), ", ".join("?" * len(names))),
                        [values[name] for name in names]).lastrowid
