"""Per-photo locks that keep two indexers off the same photo, across processes.

Each library's locks are in its own folder beside it (tagpup.core.library.Library.locks).
"""
import hashlib
import json
import logging
import os
import socket
import time

from tagpup.core import paths, processes

logger = logging.getLogger("tagpup_cli.index")


class PathLocker:
    """Stops two indexers working the same photo at once.

    The lock is a file created exclusively, which is atomic even across processes.
    The hazard is what happens when a holder dies without releasing: nothing else
    ever cleaned up another process's lock, so a killed run left its in-flight
    photos permanently unindexable. They were then skipped in silence by every
    later run -- 348 of them had accumulated over three months before anyone
    counted the photos actually in the index against the photos on disk.

    So a lock now records who holds it and since when, and a lock whose holder is
    gone is taken over rather than obeyed.
    """

    #: A lock older than this is assumed abandoned. Locks are released once per
    #: batch of 100 photos, so a live holder should never come close.
    MAX_LOCK_AGE_SECONDS = 6 * 60 * 60

    def __init__(self, lock_dir: str, max_age: float = None):
        # No default: "data/locks" meant the working directory's, and two indexers in
        # different folders then took their locks in different places.
        self.lock_dir = lock_dir
        os.makedirs(self.lock_dir, exist_ok=True)
        self.locked_paths = set()
        self.max_age = self.MAX_LOCK_AGE_SECONDS if max_age is None else max_age
        self.stolen = 0
        self._alive_cache = {}

    def _get_lock_path(self, path: str) -> str:
        # By key, so two spellings of one photo contend for one lock.
        path_hash = hashlib.md5(paths.key(path).encode('utf-8')).hexdigest()
        return os.path.join(self.lock_dir, f"{path_hash}.lock")

    def _write_lock(self, lock_file: str, path: str):
        with open(lock_file, "x", encoding="utf-8") as f:
            json.dump({
                "path": paths.stored(path),
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "acquired": time.time(),
            }, f)

    def _process_alive(self, pid: int) -> bool:
        """Best effort. When in doubt, say alive -- never steal a live lock."""
        if pid in self._alive_cache:
            return self._alive_cache[pid]
        alive = processes.is_alive(pid)
        self._alive_cache[pid] = alive
        return alive

    def _is_abandoned(self, lock_file: str) -> bool:
        """Is this lock's holder gone?"""
        try:
            age = time.time() - os.path.getmtime(lock_file)
        except OSError:
            return False
        if age > self.max_age:
            return True
        try:
            with open(lock_file, encoding="utf-8") as f:
                info = json.load(f)
        except Exception:
            # Locks written before this format carried only a path, so age is all
            # there is to go on -- and the age check above already had its say.
            return False
        if info.get("host") and info["host"] != socket.gethostname():
            return False  # another machine's lock; only age may retire it
        pid = info.get("pid")
        return bool(pid) and not self._process_alive(pid)

    def acquire(self, path: str) -> bool:
        """Take the lock for a photo. False means somebody live is working on it."""
        lock_file = self._get_lock_path(path)
        try:
            self._write_lock(lock_file, path)
            self.locked_paths.add(path)
            return True
        except FileExistsError:
            pass
        except Exception as e:
            logger.warning(f"Failed to create lock for {path}: {e}")
            return False

        if not self._is_abandoned(lock_file):
            return False

        # The holder is gone. Take it over rather than skipping the photo forever.
        try:
            os.remove(lock_file)
            self._write_lock(lock_file, path)
        except FileExistsError:
            return False  # somebody beat us to it, which is fine
        except Exception as e:
            logger.warning(f"Could not take over the abandoned lock for {path}: {e}")
            return False

        self.locked_paths.add(path)
        self.stolen += 1
        logger.info(f"Took over an abandoned lock for {path}")
        return True

    def release(self, path: str):
        """Release the lock for a specific photo path."""
        lock_file = self._get_lock_path(path)
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except Exception as e:
                logger.warning(f"Failed to remove lock for {path}: {e}")
        self.locked_paths.discard(path)

    def release_all(self):
        """Release all locks held by this process instance."""
        for path in list(self.locked_paths):
            self.release(path)
