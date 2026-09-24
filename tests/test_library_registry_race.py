"""Two threads opening the same library's state at once share one copy of it.

Each server keeps its per-library state -- saved suggestions, folder caches, index
queues -- in a registry keyed by library, and made a library's entry with
"if missing: registry[key] = {}". Two request threads reaching that line together
each made one, and whatever the first had put in its copy was lost.

A race cannot be timed in a test, so the registry here misses its key once, which is
what the second thread saw.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import tagpup_server  # noqa: E402
import tuner_server  # noqa: E402


class MissesOnce(dict):
    """A registry whose membership test answers as it did before another thread wrote."""

    def __init__(self):
        super().__init__()
        self.missed = False

    def __contains__(self, key):
        if not self.missed:
            self.missed = True
            return False
        return super().__contains__(key)


class LibraryRegistryRace(unittest.TestCase):
    def check(self, module):
        registry = MissesOnce()
        module.set_active_db_path("data/regatta.db")
        try:
            # The other thread got there first and stored something.
            registry[module.get_active_db_path()] = {"folder": "state"}
            state = module.DatabaseIsolatedDict(registry)
            self.assertEqual("state", state.get("folder"), "the other thread's entry was replaced")
        finally:
            module.set_active_db_path(None)

    def test_tagpup(self):
        self.check(tagpup_server)

    def test_tagtuner(self):
        self.check(tuner_server)


if __name__ == "__main__":
    unittest.main()
