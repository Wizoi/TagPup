"""The faces of a bulk assignment, handed to the change of photo files that carries it (docs/ARCHITECTURE.md, "One batch model";
tagpup.store.face_batches says how they are recorded and followed; tagpup.services.face_assignment plans them).

`Work` is what a plan decided about the faces: for each photo, the faces to write (their decision columns as read, and as they are
to be) and the person to put on the photo or take off it. `file_changes.write_fields` takes it as `alongside`: it records the faces
in the plan's transaction, includes a photo whose file needs no write (the person is on it already) so that its faces are written
all the same, and runs `follower` in the transaction that marks each file `done` (or `undone`): rotation does the same for a
photo's face boxes (tagpup.services.rotation).

Because the faces are written inside that transaction, a photo and its faces never disagree: forward, settled after a crash, or
undone from History. The only thing kept in memory is for the page that asked: which faces were written and which left (`applied`,
`left`). A change settled by another process has neither; nothing depends on them.

`Unpooled` is the other thing kept in memory, and only when a caller asks for it (write_fields' `unpooled`): the faces each file's
record took out of identity work, with the fingerprints of the faces table either side of that record, so that the cached
Identify Faces grids and New Person's pool lose those faces instead of being read again
(tagpup.jobs.identify.GridCache.forget_faces). `PoolFollower` counts them, in the transaction that marks the file done, by
comparing the photo's faces before and after everything that transaction did -- the tag's guesses, a bulk assignment's faces
-- so nothing that writes faces need know of the cache. Only a record that committed is handed on; a write nobody asked of
reports nothing, and the fingerprint's generation leaves what was cached before it stale, never wrongly trimmed.
"""
import threading

from tagpup.core import paths
from tagpup.store import db, face_batches, faces, person_ids

NAME, UNNAME, EXCLUDE, GUESS = "name", "unname", "exclude", "guess"

#: What the journal calls the change and the strip names the batch, never a person.
DESCRIPTIONS = {NAME: "assign faces to a person", UNNAME: "take the names off faces", EXCLUDE: "ignore faces",
                GUESS: "re-examine a folder"}

_live = {}            # {change id: Work} of the changes this process planned and is carrying out
_asked = {}           # {change id: Unpooled} of the changes whose caller asked which faces left identity work
_guard = threading.Lock()


class Work:
    def __init__(self, op):
        self.op = op
        self._photos = {}          # {paths.key(path): (path, [(face id, old, new)])}
        self.add = {}              # {paths.key(path): [tag]}: the tags a photo is to be given
        self.remove = {}           # {paths.key(path): [person]}: the people a photo is to lose (a Ref), when no face carries them
        self.people = []           # the people the faces are to be given: the ids of their nodes
        self.applied, self.left = [], []

    def face(self, path, face_id, old, new):
        self._photos.setdefault(paths.key(path), (path, []))[1].append((face_id, old, new))

    def photos(self):
        return [path for path, _faces in self._photos.values()]

    def face_ids(self):
        return [face_id for _path, found in self._photos.values() for face_id, _old, _new in found]

    def wants(self, path):
        return paths.key(path) in self._photos

    def record(self, conn, change_id, planned):
        """In the plan's transaction: record the faces of the photos that are in the change."""
        face_batches.record(conn, change_id, [intent for path in planned for intent in self._photos[paths.key(path)][1]])
        with _guard:
            _live[change_id] = self

    def verify(self, library):
        """Under the lock of changes of photo files, before a file is read: the people the faces are to be given are still people
        (a node merged away, renamed to a group, or two people now called alike since the click). A sentence refusing the whole,
        nothing written, else None. A person gone after this is the follower's: the tag is written and no face is named."""
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            known = person_ids.read(conn)
            for person in self.people:
                person_ids.target(conn, person, known)
        except person_ids.PersonProblem as problem:
            return "%s Nothing was changed." % problem
        finally:
            conn.close()
        return None

    def release(self):
        """The change is over for this process (finished, refused or raised): nothing is kept of it."""
        with _guard:
            for change_id in [each for each, work in _live.items() if work is self]:
                del _live[change_id]


class Follower:
    """What the row of a file must do for its photo's faces in the transaction that marks it `state` ('done', or 'undone' going
    back): it is called with the connection."""

    def __init__(self, row, state):
        self.row, self.forward = row, state == "done"

    def __call__(self, conn):
        row = self.row
        done = face_batches.follow(conn, row.change_id, row.photo_id, self.forward)
        if done is None:
            return
        work = _live.get(row.change_id)
        if not self.forward:
            row.faces_left = len(done.left)
            if work is not None:      # taken out of its change as it ran (_read_back): those faces were not written after all
                work.applied = [face_id for face_id in work.applied if face_id not in set(done.written)]
            return
        if work is None:
            return
        work.applied.extend(done.written)
        work.left.extend(done.left)


def follower(row, state):
    """A Follower for the row of a file, or None for a file that is not a photo's. It finds no faces of its own in a change that
    holds none."""
    return None if row.is_rename or row.photo_id is None else Follower(row, state)


class Unpooled:
    """A caller's ask: which faces its writes took out of identity work (named or ruled out), for the cached grids. Handed to
    file_changes.write_fields, which `watch`es the change it plans and `release`s it when the write is over; each file of the
    change recorded done adds its span (PoolFollower); a caller that writes faces itself adds its own (`extend`). `runs()` is
    what GridCache.forget_faces takes. Memory only, the caller's: a crash loses it with the cache."""

    def __init__(self):
        self._spans = []           # [(face ids, fingerprint before, fingerprint after)], in the order they were committed

    def watch(self, change_id):
        """Change `change_id`'s records count the faces they take out of identity work, for this ask."""
        with _guard:
            _asked[change_id] = self

    def release(self):
        """The write is over (finished, refused or raised): its records count nothing more."""
        with _guard:
            for change_id in [each for each, asked in _asked.items() if asked is self]:
                del _asked[change_id]

    def extend(self, spans):
        """Add committed writes' spans, (face ids, fingerprint before, fingerprint after) each, in the order committed."""
        self._spans.extend(spans)

    def runs(self):
        """[(face ids, fingerprint before, fingerprint after)] of the faces that left identity work, consecutive writes joined while
        nothing else wrote between them (each began at the fingerprint the one before left), a write that took no face out
        joining the chain all the same; the runs that took none out are left out."""
        joined = []
        for ids, before, after in self._spans:
            if joined and joined[-1][2] == before:
                joined[-1] = (joined[-1][0] + list(ids), joined[-1][1], after)
            else:
                joined.append((list(ids), before, after))
        return [run for run in joined if run[0]]


class PoolFollower:
    """The faces of a photo that the transaction marking its file done took out of identity work, counted for the caller who
    asked (Unpooled): `start(conn)` first thing in that transaction, which it begins IMMEDIATE so that nobody else writes between
    the two fingerprints (as tagpup.store.faces.accounted_write); called last, after every other follower; `committed()` once
    the transaction committed, so a transaction run again hands on only the attempt that committed. The span is the photo's faces
    in the pool before and not after: a guess the tag made and a bulk assignment gave back meanwhile is in neither."""

    def __init__(self, row, unpooled):
        self.row, self.unpooled = row, unpooled
        self._before = self._span = None

    def start(self, conn):
        self._span = None
        db.begin(conn, immediate=True)
        self._before = (faces.fingerprint(conn), faces.pooled_of(conn, self.row.photo_id))

    def __call__(self, conn):
        fingerprint, pooled = self._before
        now = faces.pooled_of(conn, self.row.photo_id)
        self._span = ([face_id for face_id, there in pooled.items() if there and not now.get(face_id)], fingerprint,
                      faces.fingerprint(conn))

    def committed(self):
        if self._span is not None:
            self.unpooled.extend([self._span])


def pool_follower(row, state):
    """A PoolFollower for the row of a file recorded done, when the caller of its change asked (Unpooled.watch); else None."""
    if state != "done" or row.is_rename or row.photo_id is None:
        return None
    with _guard:
        asked = _asked.get(row.change_id)
    return None if asked is None else PoolFollower(row, asked)


def photos_kept(library, change_id):
    """The ids of the photos change `change_id` of `library` holds for their faces as well as their files (what `Work.wants` said
    when it was planned), read from the journal: for a settle, which has no Work. Raises when the library cannot be read."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return face_batches.photos_of(conn, change_id)
    finally:
        conn.close()


def tidy(conn, change_id):
    """The change is finished (file_journal.finish): the records of faces whose photo was never written are removed."""
    face_batches.tidy(conn, change_id)
