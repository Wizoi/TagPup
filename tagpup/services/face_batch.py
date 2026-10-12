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
from tagpup.store import face_batches, faces

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


def follower(row, state):
    """What the row of a file must do for its photo's faces in the transaction that marks it `state` ('done', or 'undone' going
    back): a function of the connection, or None for a file that is not a photo's. It finds no faces of its own in a change that
    holds none."""
    if row.is_rename or row.photo_id is None:
        return None
    forward = state == "done"

    def follow(conn):
        work = _live.get(row.change_id)
        pools = forward and work is not None and work.op in (NAME, EXCLUDE)
        before = faces.fingerprint(conn) if pools else None
        done = face_batches.follow(conn, row.change_id, row.photo_id, forward)
        if done is None or work is None or not forward:
            return
        work.applied.extend(done.written)
        work.left.extend(done.left)
        if pools and done.unpooled:
            work.accounted.append((done.unpooled, before, faces.fingerprint(conn)))
    return follow


def tidy(conn, change_id):
    """The change is finished (file_journal.finish): the records of faces whose photo was never written are removed."""
    face_batches.tidy(conn, change_id)
