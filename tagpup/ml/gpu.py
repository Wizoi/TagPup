"""The graphics card: one process at a time on this machine has a model on it.

Adding a folder started its index (the CLI's `index`, a child of the server) and Suggest
(in the server) on the same photos at once; each loaded its own ViT-H-14 onto a 10 GB
card, the card sat full at 100%, and both stayed at 0 of 165 for over half an hour
(docs/findings.md, #750). Nothing said which process the card was for. Now a process
takes a turn on the card before it loads or runs a model on it, and holds it while it
has one there:

    hold = card().hold("indexing Regatta (harbour)", cancelled=stop.is_set, report=say)
    try:
        ...                                 # load and run the models
    finally:
        hold.release()

- **One process at a time.** The turn is a byte lock on `card.lock` in the user's own
  folder (`folder()`: %LOCALAPPDATA%\\TagPup\\gpu, or TAGPUP_GPU_LOCK), the same lock
  install.lock is (tagpup.core.byte_lock): the system lets go of it when its process
  ends, however it ends. Within a process the threads share it: a second hold joins the
  first.
- **In order.** A process waiting holds a ticket in `queue/`, a file it keeps locked
  (one whose lock can be taken belongs to a process that has ended, and is cleared), and
  takes the turn only when no earlier ticket is alive.
- **Never silently.** The holder says what it is doing and since when (`card.json`); a
  waiter hands `report` the line (tagpup.core.gpu_turns.waiting_line) each time it
  changes, and stops at once, raising Cancelled, when `cancelled()` says so.
- **Kept, but given up.** A process that keeps its models between runs (the web server,
  `keep_when_idle`) keeps its turn while no run holds it, and gives it up -- `on_release`
  unloads its models first -- as soon as another process waits. A process waiting has
  no model on the card: it loads only once it has the turn.
- **Only for the card.** A model on the CPU takes no turn (`on_the_card`): the lock is
  about the card's memory, and two runs on the CPU share the cores as any two programs do.
"""
import atexit
import itertools
import json
import logging
import os
import shutil
import tempfile
import threading
import time

from tagpup.core import byte_lock
from tagpup.core.gpu_turns import Cancelled, waiting_line

logger = logging.getLogger(__name__)

#: The folder the lock, the holder's description and the queue are in, when not the user's
#: own (a test's home).
ENV = "TAGPUP_GPU_LOCK"

#: How often a waiter looks again, and how often a process keeping its turn while idle
#: looks for another waiting (seconds).
POLL = 0.5
YIELD_POLL = 1.0

LOCK, HOLDER, QUEUE = "card.lock", "card.json", "queue"

#: How a ticket is named: when it was taken (nanoseconds, so names sort in order), the
#: process, and a number of the process's own.
_TICKET = "%020d-%d-%d.ticket"


_test_folder = None


def folder():
    """Where the turns are kept: TAGPUP_GPU_LOCK, else %LOCALAPPDATA%\\TagPup\\gpu -- the
    user's own folder, which every TagPup process of this user on this machine shares,
    whatever home it serves. In a test run without TAGPUP_GPU_LOCK, a folder of the test
    process's own, deleted when it ends: a test never waits for the owner's index, nor
    holds it up."""
    found = os.environ.get(ENV)
    if found:
        return found
    from tagpup.ml import under_test
    if under_test():
        global _test_folder
        if _test_folder is None:
            _test_folder = tempfile.mkdtemp(prefix="tagpup_gpu_test_")
            atexit.register(_remove_test_folder, _test_folder)
        return _test_folder
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "TagPup", "gpu")


def _remove_test_folder(where):
    """At a test process's exit: end its turn, whatever still holds it -- an open card.lock
    cannot be deleted -- and delete the folder."""
    _card.close()
    shutil.rmtree(where, True)


def on_the_card(device):
    """Does a model on `device` ("cuda", "cuda:0", "cpu", None) run on the graphics card?"""
    return str(device or "").lower().startswith("cuda")


def _alive(path):
    """Is the ticket at `path` held by a waiter? One whose lock can be taken is a dead
    waiter's, and is cleared."""
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return False
    except OSError:
        return True   # being made or cleared this moment: looked at again next time
    try:
        try:
            byte_lock.lock(fd)
        except OSError:
            return True
        byte_lock.unlock(fd)
    finally:
        os.close(fd)
    try:
        os.remove(path)
    except OSError:
        pass
    return False


def read_holder(where=None):
    """What the process holding the card says of itself -- {"what", "since", "pid"} -- or
    None when it said nothing readable."""
    try:
        with open(os.path.join(where or folder(), HOLDER), encoding="utf-8") as handle:
            found = json.load(handle)
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def others_waiting(where=None):
    """Is a process waiting for the card in `where` (its folder, by default folder())?"""
    queue = os.path.join(where or folder(), QUEUE)
    try:
        names = sorted(os.listdir(queue))
    except OSError:
        return False
    return any(_alive(os.path.join(queue, name)) for name in names)


class Hold:
    """One holder's share of this process's turn: release() it when done (or use `with`)."""

    def __init__(self, card, what):
        self._card, self.what = card, what
        self._released = False

    def release(self):
        if self._released:
            return
        self._released = True
        self._card._release(self.what)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


class NoHold:
    """What a model off the card takes: nothing."""
    what = None

    def release(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Card:
    """This process's turn on the graphics card. One per process (card()); a test makes its
    own on a folder of its own.

    `keep_when_idle`: once no holder is left, the turn is kept -- the models stay loaded
    for the next run -- until another process waits, when `on_release()` is called (it
    unloads the models) and the turn is given up. Otherwise `on_release()` is called and
    the turn given up as the last holder lets go. `idle_what` is what the holder says of
    itself meanwhile."""

    def __init__(self, where=None, poll=POLL, yield_poll=YIELD_POLL):
        self.where = where
        self.poll = poll
        self.yield_poll = yield_poll
        self.keep_when_idle = False
        self.on_release = None
        self.idle_what = "models kept ready for the next run (process %d)" % os.getpid()
        self._cond = threading.Condition()
        #: The locked handle while this process has the turn, and the folder it is in.
        self._fd = None
        self._held_in = None
        self._since = None
        #: What each holder in this process is doing, one entry per hold.
        self._users = []
        #: A thread of this process is waiting for the turn; the turn is being given up.
        self._taking = False
        self._yielding = False
        #: Taken by ensure() (a model loaded outside any hold): kept until given up.
        self._pinned = False
        self._said = None
        self._line = None
        self._watcher = None
        self._numbers = itertools.count(1)

    # ---- What a holder asks ------------------------------------------------------------

    def _folder(self):
        return self.where or folder()

    def holds(self):
        """Has this process the turn now?"""
        with self._cond:
            return self._fd is not None and not self._yielding

    def hold(self, what, cancelled=None, report=None):
        """Take a turn for `what` -- "indexing Regatta (harbour)" -- waiting for it in order
        behind any process before it, or join this process's: a Hold. `report(line)` hears
        the waiting line each time it changes; `cancelled()` true stops the wait at once,
        raising Cancelled, and nothing was run."""
        said = [None]

        def say(line):
            if report is not None and line != said[0]:
                said[0] = line
                try:
                    report(line)
                except Exception as e:
                    logger.warning("Could not say that %s waits for the graphics card: %s", what, e)

        with self._cond:
            while True:
                if cancelled is not None and cancelled():
                    raise Cancelled("%s was cancelled while it waited for the graphics card" % what)
                if self._fd is not None and not self._yielding:
                    self._users.append(what)
                    self._describe()
                    return Hold(self, what)
                if not self._taking and not self._yielding:
                    self._taking = True
                    break
                # Another thread of this process is waiting, or the turn is being given up.
                say(self._line or waiting_line(read_holder(self._folder())))
                self._cond.wait(self.poll)
        try:
            fd, where = self._take(what, cancelled, say)
        except BaseException:
            with self._cond:
                self._taking = False
                self._cond.notify_all()
            raise
        with self._cond:
            self._fd, self._held_in, self._since = fd, where, time.time()
            self._taking = False
            self._line = None
            self._users.append(what)
            self._describe()
            self._cond.notify_all()
        logger.info("Took the graphics card for %s.", what)
        return Hold(self, what)

    def try_hold(self, what):
        """A Hold for `what` only if no one has to wait for it -- the card is free, or this
        process has it, and no process waits -- else None. For work worth doing only then:
        the warm-up of the web server's models."""
        with self._cond:
            if self._fd is not None and not self._yielding:
                self._users.append(what)
                self._describe()
                return Hold(self, what)
            if self._taking or self._yielding:
                return None
            where = self._folder()
            os.makedirs(os.path.join(where, QUEUE), exist_ok=True)
            if others_waiting(where):
                return None
            fd = byte_lock.try_lock(os.path.join(where, LOCK))
            if fd is None:
                return None
            self._fd, self._held_in, self._since = fd, where, time.time()
            self._users.append(what)
            self._describe()
        return Hold(self, what)

    def ensure(self, what):
        """Have the turn before a model is loaded onto the card: at once when this process
        has it, else waited for (each change of the waiting line logged) and kept until it
        is given up -- the models' own check (tagpup.ml.clip, tagpup.ml.faces), for a load
        no run took a turn for."""
        with self._cond:
            if self._fd is not None and not self._yielding:
                return
        hold = self.hold(what, report=lambda line: logger.info("%s", line))
        with self._cond:
            self._pinned = True
        hold.release()

    def release_if_idle(self):
        """Give the turn up now if no holder is left: the models it was kept for were let go
        (tagpup.runtime's idle release). Whether it was given up."""
        with self._cond:
            if self._fd is None or self._users or self._yielding or self._taking:
                return False
            self._yielding = True
        self._let_go()
        return True

    def close(self):
        """End this process's turn now, whatever holds it, unloading nothing: for a test's
        cleanup and a test process's exit, where the folder it is in is to be deleted."""
        with self._cond:
            fd, self._fd = self._fd, None
            self._held_in = self._since = self._said = None
            self._users = []
            self._pinned = self._yielding = False
            if fd is not None:
                try:
                    byte_lock.unlock(fd)
                except OSError:
                    pass
                os.close(fd)
            self._cond.notify_all()

    # ---- Taking and giving up ---------------------------------------------------------

    def _take(self, what, cancelled, say):
        """Wait in order for the lock: (handle, folder)."""
        where = self._folder()
        queue = os.path.join(where, QUEUE)
        os.makedirs(queue, exist_ok=True)
        name = _TICKET % (time.time_ns(), os.getpid(), next(self._numbers))
        ticket = os.path.join(queue, name)
        handle = os.open(ticket, os.O_RDWR | os.O_CREAT | os.O_EXCL)
        try:
            # A waiter looking at the queue may have it locked this moment, to see whether
            # it is alive.
            for _ in range(40):
                try:
                    byte_lock.lock(handle)
                    break
                except OSError:
                    time.sleep(0.05)
            while True:
                if cancelled is not None and cancelled():
                    raise Cancelled("%s was cancelled while it waited for the graphics card" % what)
                if self._first(queue, name):
                    fd = byte_lock.try_lock(os.path.join(where, LOCK))
                    if fd is not None:
                        return fd, where
                line = waiting_line(read_holder(where))
                with self._cond:
                    self._line = line
                say(line)
                time.sleep(self.poll)
        finally:
            try:
                os.close(handle)
            except OSError:
                pass
            try:
                os.remove(ticket)
            except OSError:
                pass

    @staticmethod
    def _first(queue, mine):
        """Is no ticket before `mine` alive?"""
        try:
            names = sorted(os.listdir(queue))
        except OSError:
            return True
        for name in names:
            if name >= mine:
                return True
            if _alive(os.path.join(queue, name)):
                return False
        return True

    def _describe(self):
        """Say what this process holds the card for, in card.json. Under the lock."""
        if self._fd is None:
            return
        doing = list(dict.fromkeys(self._users))
        what = "; ".join(doing) if doing else self.idle_what
        if what == self._said:
            return
        self._said = what
        path = os.path.join(self._held_in, HOLDER)
        record = {"what": what, "since": self._since, "pid": os.getpid()}
        temporary = "%s.%d.tmp" % (path, os.getpid())
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(record, handle)
            os.replace(temporary, path)
        except OSError as e:
            # A waiter reading it this moment: said again at the next change.
            self._said = None
            logger.debug("Could not say what the graphics card is held for: %s", e)
            try:
                os.remove(temporary)
            except OSError:
                pass

    def _release(self, what):
        with self._cond:
            try:
                self._users.remove(what)
            except ValueError:
                pass
            if self._users or self._fd is None:
                self._describe()
                return
            if self._pinned or self.keep_when_idle:
                self._describe()
                if self.keep_when_idle:
                    self._watch()
                return
            self._yielding = True
        self._let_go()

    def _let_go(self):
        """Unload (on_release) and give the turn up. Called with `_yielding` set, outside the
        lock: a hold asked for meanwhile waits for it, then queues behind any process waiting."""
        try:
            if self.on_release is not None:
                self.on_release()
        except Exception as e:
            logger.error("Could not let the models go before giving up the graphics card: %s", e)
        with self._cond:
            fd, where = self._fd, self._held_in
            self._fd = self._held_in = self._since = None
            self._said = None
            self._pinned = False
            if fd is not None:
                try:
                    os.remove(os.path.join(where, HOLDER))
                except OSError:
                    pass
                try:
                    byte_lock.unlock(fd)
                except OSError:
                    pass
                os.close(fd)
            self._yielding = False
            self._cond.notify_all()
        logger.info("Gave up the graphics card.")

    def _watch(self):
        """While the turn is kept idle, give it up as soon as another process waits. Under
        the lock."""
        if self._watcher is not None and self._watcher.is_alive():
            return
        self._watcher = threading.Thread(target=self._watching, name="GraphicsCardTurnThread", daemon=True)
        self._watcher.start()

    def _watching(self):
        while True:
            time.sleep(self.yield_poll)
            with self._cond:
                if self._fd is None:
                    return
                if self._users or self._yielding or self._taking:
                    continue
                where = self._held_in
            if not others_waiting(where):
                continue
            with self._cond:
                if self._fd is None or self._users or self._yielding or self._taking:
                    continue
                self._yielding = True
            logger.info("Another process waits for the graphics card: letting this one's models go.")
            self._let_go()
            return


_card = Card()


def card():
    """This process's turn on the graphics card."""
    return _card
