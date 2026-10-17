"""The one model of a batch of photo files: a journaled change, written a chunk at a time, one at a time in a process (docs/ARCHITECTURE.md,
"One batch model"; DECISIONS 2026-10-11).

A batch is the change `file_changes.write_fields` plans: its files' states in `change_files` (planned, writing, done, conflict) ARE
its progress, so there is no table and no thread of its own; the request that asked for it stays synchronous and is its place in line.

* **Claim.** More than CHUNK photos are a batch (`claim`). One runs at a time in a process, whichever library: two libraries can hold
  one folder, and two batches planned from the files before the other wrote them. The next waits its turn, at most MOST_WAITING of
  them; one more is refused, saying how many are ahead. A batch still waiting when the server restarts is lost, by design: it had
  written nothing. Single saves and small writes are not batches; they wait on the lock of changes of photo files as always.
* **Chunks.** Every CHUNK files `file_changes` lets a writer that waits in and asks the batch whether it is to stop; a batch asked to
  stop (Cancel, a hand-over to a new version) ends after its chunk with what it wrote applied and undoable. A batch asked to stop
  while it waits never starts.
* **Cap.** MAX_BATCH photos, in `write_fields`, for every caller but the tag tree's rewrites (rename, delete, merge, a person's
  rename): the owner chose no cap for those, and they have this progress and this Cancel.
* **Seen.** `listing` is the batches running or waiting in a library: this process's, and a running change of another process
  (the CLI) read from the journal, its files counted by state and its owner alive. A change whose owner is gone is not running; the next
  open settles it (file_changes.settle).
"""
import contextlib
import itertools
import logging
import threading
import time

from tagpup.core.result import Conflict, Refused
from tagpup.store import db, file_journal

logger = logging.getLogger(__name__)

#: Files written between two looks at whether to stop and whether another writer waits.
CHUNK = 25
#: The most photos one batch names (not the tag tree's rewrites).
MAX_BATCH = 5_000
#: How many batches wait behind the one running before another is refused.
MOST_WAITING = 8

QUEUED, RUNNING, STOPPING = "queued", "running", "stopping"
#: What a sentence naming the batches under way says they are.
WORD = "photo batch"


class Batch:
    def __init__(self, library, operation, photos):
        self.ticket = next(_tickets)
        self.library, self.operation, self.photos = library, operation, photos
        self.stop = threading.Event()
        self.started = None
        self.change_id = None

    @property
    def state(self):
        if self.started is None:
            return QUEUED
        return STOPPING if self.stop.is_set() else RUNNING


_tickets = itertools.count(1)
_closing = False      # the server is moving to a new version: a batch that claims now is stopped before it begins
_line = []            # the batches of this process, the running one first
_turn = threading.Condition()
_held = threading.local()


def current():
    """The batch this thread is carrying out, or None."""
    return getattr(_held, "batch", None)


@contextlib.contextmanager
def claim(library, operation, photos, wait=True):
    """Wait for this process's turn to carry out a batch of `photos` photos, and hold it. Conflict, nothing begun, when MOST_WAITING
    are waiting already; Refused when it was cancelled while it waited. Inside a batch's own claim it is that batch. Without `wait`
    (a caller holding the lock of changes of photo files, which the batch ahead needs) any batch ahead is a Conflict at once."""
    if current() is not None:
        yield current()
        return
    batch = Batch(library, operation, photos)
    with _turn:
        if _line and not wait:
            raise Conflict("A batch of photo files is running; try again when it has finished.")
        if len(_line) > MOST_WAITING:
            raise Conflict("%d photo batches are ahead of this one (one running, %d waiting). Try again when one has finished."
                           % (len(_line), len(_line) - 1))
        _line.append(batch)
        if _closing:
            batch.stop.set()
        try:
            while _line[0] is not batch and not batch.stop.is_set():
                _turn.wait(1.0)
            if batch.stop.is_set():
                raise Refused("The batch was cancelled before it began; nothing was written.")
        except BaseException:
            _line.remove(batch)
            _turn.notify_all()
            raise
        batch.started = time.time()
    _held.batch = batch
    try:
        yield batch
    finally:
        _held.batch = None
        with _turn:
            _line.remove(batch)
            _turn.notify_all()


def _stop(wanted):
    stopped = 0
    with _turn:
        for batch in _line:
            if wanted(batch):
                batch.stop.set()
                stopped += 1
        _turn.notify_all()
    return stopped


def cancel(library, ticket):
    """Ask this process's batch `ticket` of `library` to stop (a waiting one never starts). False when there is none."""
    return bool(_stop(lambda batch: batch.ticket == ticket and batch.library.key == library.key))


def stop_all():
    """Ask every batch of this process to stop, at its chunk, and stop any that claims until `reopen`: a hand-over to a new
    version."""
    global _closing
    _closing = True
    return _stop(lambda batch: True)


def reopen():
    """The server takes work again (a drain given up): batches begin as usual."""
    global _closing
    _closing = False


def sentences():
    """What a drain and the launcher say of the batches under way here: ['2 photo batches: 1 running, 1 waiting (3,000 photos)'], or []."""
    with _turn:
        here = list(_line)
    if not here:
        return []
    waiting = sum(1 for batch in here if batch.started is None)
    count = len(here)
    return ["%d %s%s (%s photos in all, %d waiting)" % (count, WORD, "" if count == 1 else "es",
                                                        "{:,}".format(sum(batch.photos for batch in here)), waiting)]


def running_here():
    """How many batches this process has, running or waiting."""
    with _turn:
        return len(_line)


def _row(batch, position, counts):
    states = counts or {}
    return {"ticket": batch.ticket, "id": batch.change_id, "operation": batch.operation, "library": batch.library.name,
            "photos": batch.photos, "done": states.get("done", 0), "conflict": states.get("conflict", 0),
            "planned": states.get("planned", 0) + states.get("writing", 0), "started": batch.started, "state": batch.state,
            "position": position, "here": True}


def listing(library):
    """The batches running or waiting in `library`, any process: [{ticket (this process's, to cancel by; None for another's), id (the
    journal's change, None until planned), operation, library, photos, done, conflict, planned, started, state, position, here}].
    Never raises for a library that cannot be read: the journal's batches are then left out and logged."""
    with _turn:
        here = list(_line)
    mine = [(position, batch) for position, batch in enumerate(here) if batch.library.key == library.key]
    counts = {}
    try:
        changes = [change for change in file_journal.unfinished(library.path)
                   if change.owner and not change.undoing and file_journal.owner_alive(change.owner)]
        if changes:
            conn = db.connect(db.readonly_uri(library.path), uri=True)
            try:
                counts = file_journal.counts(conn, [change.id for change in changes])
            finally:
                conn.close()
    except Exception as problem:
        logger.warning("Could not read the batches under way in %s: %s", library.name, problem)
        changes = []
    ours = {batch.change_id for _position, batch in mine if batch.change_id}
    rows = [_row(batch, position, counts.get(batch.change_id)) for position, batch in mine]
    for change in changes:
        total = sum(counts.get(change.id, {}).values())
        if change.id in ours or total <= CHUNK:
            continue
        states = counts[change.id]
        rows.append({"ticket": None, "id": change.id, "operation": change.operation, "library": library.name, "photos": total,
                     "done": states.get("done", 0), "conflict": states.get("conflict", 0),
                     "planned": states.get("planned", 0) + states.get("writing", 0), "started": None,
                     "state": RUNNING, "position": 0, "here": False})
    return rows


def is_this_process(owner):
    """Is `owner` ('host:pid:start', as a run names its process) this process?"""
    return owner == file_journal.owner()


def process_alive(owner):
    """Is the process that owned a run -- 'host:pid:start' -- still going, and not this one? A run of this process that nothing
    in this process runs is a run that was lost (a test of a restart; a thread that died)."""
    return bool(owner) and owner != file_journal.owner() and file_journal.owner_alive(owner)

