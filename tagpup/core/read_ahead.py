"""Reading ahead: the next items read on a few threads while the caller works on the current one, handed back in
their order, within a bound on the bytes the reads say they hold (docs/AI_PIPELINE.md, phase 1b).

The indexer (tagpup_cli.py's index) decodes each photo on the processor and then runs the models on the graphics
card; one after the other, the card waited for every decode. Here the decodes of the next photos run on threads of
their own while the caller's thread keeps the card, the writes and their order. Threads, not processes: Pillow's
decode and torch's processor work let go of the GIL, and a process pool on Windows starts a Python for each worker.

Each item begins in the caller's thread as it enters the window (`begin(item)`: what must be done there, a lock taken
or a question of a connection the caller holds); None from it means there is nothing to read, and the item is handed
back as it is when its turn comes. Its read runs on a worker (`read(item, begun, reserve)`), which calls `reserve(n)`
once, with the bytes it is about to hold, before it holds them. Reservations are granted in the items' order, and one
is always granted when nothing else is held: the item the caller waits for never waits behind a later one, and a
single item larger than the bound is read alone. What an item reserved is given back when the caller asks for the
next one, so what the reads ahead and the item in the caller's hands reserved is at most `budget` bytes together, or
one item. The bound is on those reservations -- each read's own estimate of what it holds --, not on the process's
memory: what a read holds beyond its estimate, and what the caller makes of an item, are outside it.

The threads are daemons: closed (the `with` block left, Ctrl+C, an error), the window is given up, a read under way
finishes on its own and is thrown away, and nothing waits for it. A read writes nothing: what a read made is the
caller's to use or to drop, so a run stopped at any point has left nothing half done by a read.
"""
import collections
import threading
from concurrent import futures

#: How long the caller's thread waits at a time for a read: short, so that Ctrl+C is seen at once (a wait with no
#: limit is not interrupted on Windows).
_WAIT = 0.25


class Closed(Exception):
    """The read-ahead was closed while a read waited for its reservation."""


class ReadAhead:
    """`items` read ahead, `ahead` at most beyond the one in the caller's hands, by `threads` daemon threads, within
    `budget` bytes. Iterated, it gives (item, begun, result) in the items' order: `begun` is what `begin` gave (None:
    no read), `result` a Future whose result() is what `read` gave, or raises what it raised. Used as a context
    manager, it is closed on leaving."""

    def __init__(self, items, begin, read, *, threads, ahead, budget):
        self._items, self._begin, self._read = items, begin, read
        self._ahead, self._budget = ahead, budget
        self._cond = threading.Condition()
        self._tasks = collections.deque()
        self._turn = 0           # the next ticket that may reserve
        self._held = {}          # ticket -> bytes reserved
        self._used = 0
        self._closed = False
        self._threads = [threading.Thread(target=self._work, name="read ahead %d" % number, daemon=True)
                         for number in range(threads)]
        for thread in self._threads:
            thread.start()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        with self._cond:
            self._closed = True
            self._tasks.clear()
            self._cond.notify_all()

    def __iter__(self):
        items = iter(self._items)
        window = collections.deque()
        tickets = 0
        try:
            while True:
                # The item handed back next, and `ahead` beyond it while the caller works on that one.
                while sum(1 for entry in window if entry[2] is not None) <= self._ahead:
                    item = next(items, _END)
                    if item is _END:
                        break
                    begun = self._begin(item)
                    if begun is None:
                        window.append((item, None, None, None))
                        continue
                    result = futures.Future()
                    with self._cond:
                        self._tasks.append((tickets, item, begun, result))
                        self._cond.notify_all()
                    window.append((item, begun, result, tickets))
                    tickets += 1
                if not window:
                    return
                item, begun, result, ticket = window.popleft()
                if result is not None:
                    while not result.done():
                        futures.wait([result], timeout=_WAIT)
                yield item, begun, result
                # Let go of what the caller has finished with before anything else is read: held here, it stayed
                # alive outside the bound.
                item = begun = result = None
                if ticket is not None:
                    self._give_back(ticket)
        finally:
            self.close()

    def _give_back(self, ticket):
        with self._cond:
            self._used -= self._held.pop(ticket, 0)
            self._cond.notify_all()

    def _reserve(self, ticket, size):
        with self._cond:
            while not self._closed and not (self._turn == ticket and (self._used == 0
                                                                       or self._used + size <= self._budget)):
                self._cond.wait()
            if self._closed:
                raise Closed()
            self._turn += 1
            self._used += size
            self._held[ticket] = size
            self._cond.notify_all()

    def _work(self):
        while True:
            with self._cond:
                while not self._closed and not self._tasks:
                    self._cond.wait()
                if self._closed:
                    return
                task = self._tasks.popleft()
            if not self._read_one(*task):
                return
            # Nothing of the read is held past here: its locals were _read_one's. Kept in this loop, a worker held its
            # last photo's pictures through the whole of its next read, outside the bound.
            task = None

    def _read_one(self, ticket, item, begun, result):
        """Read one item and hand what it gave, or raised, to its Future. False when the read-ahead was closed while
        it waited for its turn."""
        reserved = []

        def reserve(size):
            self._reserve(ticket, size)
            reserved.append(size)
        try:
            value = self._read(item, begun, reserve)
        except BaseException as error:   # handed to the caller, whatever it is
            outcome = (False, error)
        else:
            outcome = (True, value)
        if not reserved:
            # A read that failed before it reserved still takes its turn, or the items after it wait for ever.
            try:
                reserve(0)
            except Closed:
                return False
        if outcome[0]:
            result.set_result(outcome[1])
        else:
            result.set_exception(outcome[1])
        return True


_END = object()
