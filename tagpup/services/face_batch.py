"""The faces of a bulk assignment, handed to the change of photo files that carries it (docs/ARCHITECTURE.md, "One batch model";
tagpup.store.face_batches says how they are recorded and followed; tagpup.services.face_assignment plans them).

`Work` is what a plan decided about the faces: for each photo, the faces to write (their decision columns as read, and as they are
to be) and the person to put on the photo or take off it. `file_changes.write_fields` takes it as `alongside`: it records the faces
in the plan's transaction, includes a photo whose file needs no write (the person is on it already) so that its faces are written
all the same, and runs `follower` in the transaction that marks each file `done` (or `undone`): rotation does the same for a
photo's face boxes (tagpup.services.rotation).

Because the faces are written inside that transaction, a photo and its faces never disagree: forward, settled after a crash, or
undone from History. The only thing kept in memory is for the page that asked: which faces were written and which left (`applied`,
`left`), and the fingerprints either side of each write that took faces out of identity work (`accounted`), so the cached Identify
Faces grids lose those faces instead of being built again (tagpup.jobs.identify.GridCache.forget_faces). A change settled by another
process has neither; nothing depends on them.
"""
import threading

from tagpup.core import paths
from tagpup.store import db, face_batches, faces, person_ids

NAME, UNNAME, EXCLUDE, GUESS = "name", "unname", "exclude", "guess"

#: What the journal calls the change and the strip names the batch, never a person.
DESCRIPTIONS = {NAME: "assign faces to a person", UNNAME: "take the names off faces", EXCLUDE: "ignore faces",
                GUESS: "re-examine a folder"}

_live = {}            # {change id: Work} of the changes this process planned and is carrying out
_guard = threading.Lock()


class Work:
    def __init__(self, op):
        self.op = op
        self._photos = {}          # {paths.key(path): (path, [(face id, old, new)])}
        self.add = {}              # {paths.key(path): [tag]}: the tags a photo is to be given
        self.remove = {}           # {paths.key(path): [person]}: the people a photo is to lose (a Ref), when no face carries them
        self.people = []           # the people the faces are to be given, as person_ids.wire has them
        self.applied, self.left = [], []
        self.accounted = []

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

    def runs(self):
        """[(face ids, fingerprint before, fingerprint after)] of the faces that left identity work, consecutive writes joined while
        nothing else wrote between them (each began at the fingerprint the one before left): what GridCache.forget_faces takes."""
        joined = []
        for ids, before, after in self.accounted:
            if joined and joined[-1][2] == before:
                joined[-1] = (joined[-1][0] + ids, joined[-1][1], after)
            else:
                joined.append((list(ids), before, after))
        return joined


class Follower:
    """What the row of a file must do for its photo's faces in the transaction that marks it `state` ('done', or 'undone' going
    back). `start(conn)` is called first thing in that transaction, before the file's row is recorded (recording can name a guess,
    which the cached grids must not take for the write's own); then the follower is called with the connection."""

    def __init__(self, row, state):
        self.row, self.forward = row, state == "done"
        self.before = None

    def _work(self):
        work = _live.get(self.row.change_id)
        return work if work is not None and self.forward and work.op in (NAME, EXCLUDE) else None

    def start(self, conn):
        if self._work() is not None:
            self.before = faces.fingerprint(conn)

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
        if self.before is not None and done.unpooled:
            work.accounted.append((done.unpooled, self.before, faces.fingerprint(conn)))


def follower(row, state):
    """A Follower for the row of a file, or None for a file that is not a photo's. It finds no faces of its own in a change that
    holds none."""
    return None if row.is_rename or row.photo_id is None else Follower(row, state)


def tidy(conn, change_id):
    """The change is finished (file_journal.finish): the records of faces whose photo was never written are removed."""
    face_batches.tidy(conn, change_id)
