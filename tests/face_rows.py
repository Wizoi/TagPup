"""Face rows for tests, written in plain SQL.

A face points at its photo by id (migration 4). A test that seeds faces names the photo
by path, as the photos a person sees are named; this makes the photo's row where the
test has not, holding the path and nothing else, as photos.ensure_row does. Plain SQL
rather than the store, so a fixture does not agree with the code it tests.
"""
import json

#: The faces with their photo's path: `SELECT p.path, f.name FROM ` + FACES_WITH_PATHS.
FACES_WITH_PATHS = "faces f JOIN photos p ON p.id = f.photo_id"

#: The key FaceNet's vectors are kept under (`face_embeddings.model`): the faces.model a library made in a test's home holds by
#: default, and the key every vector of the three live libraries is under.
FACENET = "mtcnn-facenet"

#: A face's vector under FaceNet's key, as `v`: `SELECT f.id, v.vector FROM faces f` + VECTOR_OF_FACE.
VECTOR_OF_FACE = " LEFT JOIN face_embeddings v ON v.face_id = f.id AND v.model = '%s'" % FACENET


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
    ids = [person_of(conn, name) for name in names]   # first: making a node lists the people its photos' keywords name
    start = conn.execute("SELECT COALESCE(MAX(position) + 1, 0) FROM photo_people WHERE photo_id = ?",
                         (photo,)).fetchone()[0]
    rows = []
    for n, name in enumerate(names):
        rows.append((photo, start + n, name, source, ids[n]))
    conn.executemany("INSERT INTO photo_people (photo_id, position, name, source, tag_id) VALUES (?, ?, ?, ?, ?)", rows)


def person_of(conn, name):
    """The id of the person a name is: the one person called it, or -- for a name no node is called -- a person made at the top
    of the tree, as the tree's New tag makes one there (tagpup.services.tags.create with has_face; the store's face writers make no
    node). Raises ValueError for a name no writer can give a node (two people have it, a group's, a tag's path): such a row is a
    shape nothing real makes."""
    from tagpup.store import person_ids, taxonomy   # a test helper reads the tree as the writers do
    conn = getattr(conn, "connection", conn)   # a test may hold a cursor
    try:
        found = person_ids.target(conn, name)[0]
    except person_ids.PersonProblem as problem:
        raise ValueError("no writer lists %r as a person: %s" % (name, problem)) from problem
    if found is None:
        found = taxonomy.add_node(conn, name.strip(), root_has_face=1) if "/" not in name else None
    if found is None:
        raise ValueError("no writer can make a node of %r" % (name,))
    return found


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


def add_face(conn, photo_path, box=(0, 0, 10, 10), embedding=None, model=FACENET, **columns):
    """Insert a face into the photo at `photo_path`; `columns` are any others of faces
    (name, prob, name_source, excluded, excluded_reason, id). A box that is
    not a string is written as JSON. Its `embedding`, if given (float32 bytes), is its vector in `face_embeddings` under `model`,
    FaceNet's unless given, as the indexer files it. A face given a `name` is given the person the tree says it is, as every
    writer that names a face does (tagpup.store.person_ids.target: the id of the one node called so; none for a name no node, or
    two, is called). Returns the face's id. The caller commits."""
    values = dict(columns, photo_id=photo_id(conn, photo_path),
                  box=box if isinstance(box, str) else json.dumps(list(box)))
    if values.get("name") and "tag_id" not in values:
        values["tag_id"] = person_of(conn, values["name"])
    names = sorted(values)
    face_id = conn.execute("INSERT INTO faces (%s) VALUES (%s)" % (", ".join(names), ", ".join("?" * len(names))),
                           [values[name] for name in names]).lastrowid
    if embedding is not None:
        add_vector_of_face(conn, face_id, embedding, model)
    return face_id


def add_vector_of_face(conn, face_id, vector, model=FACENET):
    """Keep `vector` (float32 bytes) as the face's under `model`, FaceNet's unless given, replacing one it had. The caller
    commits."""
    conn.execute("INSERT OR REPLACE INTO face_embeddings (face_id, model, vector) VALUES (?, ?, ?)", (face_id, model, vector))
