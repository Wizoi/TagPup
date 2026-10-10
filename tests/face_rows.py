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
    return conn.execute("INSERT INTO photos (path, tags, captions, raw_metadata)"
                        " VALUES (?, '[]', '[]', '{}')", (photo_path,)).lastrowid


#: A photo's people with its path: `SELECT p.path, pp.name FROM ` + PEOPLE_WITH_PATHS.
PEOPLE_WITH_PATHS = "photo_people pp JOIN photos p ON p.id = pp.photo_id"


def add_people(conn, photo_path, names, source="keyword"):
    """List `names` as the people of the photo at `photo_path`, after any it lists, as
    though its keywords (or, with source "face", its faces) named them. For a test of
    what reads a photo's people; a test of what writes them goes through the store. The
    photo's row is made if it has none. The caller commits."""
    photo = photo_id(conn, photo_path)
    start = conn.execute("SELECT COALESCE(MAX(position) + 1, 0) FROM photo_people WHERE photo_id = ?",
                         (photo,)).fetchone()[0]
    from tagpup.store import person_ids   # the person a name is, as people.rebuild writes it
    rows = []
    for n, name in enumerate(names):
        try:
            tag_id = person_ids.target(conn, name)[0]
        except person_ids.PersonProblem:
            tag_id = None
        rows.append((photo, start + n, name, source, tag_id))
    conn.executemany("INSERT INTO photo_people (photo_id, position, name, source, tag_id) VALUES (?, ?, ?, ?, ?)", rows)


def people_of(conn, photo_path):
    """The people of the photo stored at exactly `photo_path`, in order."""
    return [name for (name,) in conn.execute(
        "SELECT pp.name FROM " + PEOPLE_WITH_PATHS + " WHERE p.path = ? ORDER BY pp.position", (photo_path,))]


def configured_model():
    """The model key search reads in a library made in a test's home: the defaults' (a
    new library is stamped with them; tagpup.services.settings), as PhotoIndex uses it."""
    from tagpup.services import settings
    from tagpup.store import embeddings
    return embeddings.model_key(**settings.LibrarySettings(dict(settings.DEFAULTS)).embedder)


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


def give_their_person(conn, face_id):
    """A face a test has just given a `name` by SQL is given the person the tree says it is, as every writer that names a face does
    (tagpup.store.person_ids.target: the one node called so; none for a name no node, or two, is called). Unresolved names are no
    longer linked by whatever rebuilds the photo next (person_ids.People.settle): a fixture that names a face states who. The
    caller commits."""
    from tagpup.store import person_ids   # a test helper reads the tree as the writers do
    row = conn.execute("SELECT name, tag_id FROM faces WHERE id = ?", (face_id,)).fetchone()
    if row is None or not row[0] or row[1] is not None:
        return
    try:
        found, name = person_ids.target(conn, row[0])
    except person_ids.PersonProblem:
        return
    if found is not None:
        conn.execute("UPDATE faces SET tag_id = ?, name = ? WHERE id = ?", (found, name, face_id))


def add_face(conn, photo_path, box=(0, 0, 10, 10), **columns):
    """Insert a face into the photo at `photo_path`; `columns` are any others of faces
    (embedding, name, prob, name_source, excluded, excluded_reason, id). A box that is
    not a string is written as JSON. A face given a `name` is given the person the tree says it is, as every writer that names a
    face does (tagpup.store.person_ids.target: the id of the one node called so; none for a name no node, or two, is called).
    Returns the face's id. The caller commits."""
    values = dict(columns, photo_id=photo_id(conn, photo_path),
                  box=box if isinstance(box, str) else json.dumps(list(box)))
    if values.get("name") and "tag_id" not in values:
        from tagpup.store import person_ids   # a test helper reads the tree as the writers do
        try:
            values["tag_id"] = person_ids.target(conn, values["name"])[0]
        except person_ids.PersonProblem:
            values["tag_id"] = None
    names = sorted(values)
    return conn.execute("INSERT INTO faces (%s) VALUES (%s)" % (", ".join(names), ", ".join("?" * len(names))),
                        [values[name] for name in names]).lastrowid
