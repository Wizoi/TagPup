"""A lock on one byte of a file: held while the process lives, let go by the system when it ends however it ends.

The installer's lock (install.lock), the hand-over's, the always-on process's, and the graphics card's turn
(tagpup.ml.gpu: card.lock, and the tickets of its queue) are all this one lock. Two copies of the code, one in
tagpup.supervisor and one in tagpup.ml.gpu, said the same thing twice (docs/findings.md, #762); tests/test_gpu_single_owner.py
fails any other module that calls msvcrt.locking or fcntl.flock.

    held = byte_lock.Lock(path)
    if held.acquire(wait=5):          # False when another process holds it
        try: ...
        finally: held.release()

    fd = byte_lock.try_lock(path)     # an open handle with the byte locked, or None; os.close(fd) lets it go
"""
import os
import time


def lock(fd):
    """Lock the first byte of `fd`, or raise OSError when another handle has it."""
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock(fd):
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def try_lock(path):
    """An open handle on `path` with its byte locked, or None when another has it."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    try:
        lock(fd)
    except OSError:
        os.close(fd)
        return None
    return fd


class Lock:
    """An exclusive lock on a file, held while the process lives: the operating system
    lets go of it when the process ends, however it ends."""

    def __init__(self, path):
        self.path = path
        self._fd = None

    def acquire(self, wait=0.0):
        """Take it, waiting up to `wait` seconds. False when another holds it."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        deadline = time.monotonic() + wait
        while True:
            fd = try_lock(self.path)
            if fd is not None:
                self._fd = fd
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

    def release(self):
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            unlock(fd)
        except OSError:
            pass
        os.close(fd)
