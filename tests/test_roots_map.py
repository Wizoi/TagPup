"""The machine's map of the roots, and the one way the app writes it (tagpup.config;
docs/ARCHITECTURE.md, "Roots and machines").

`machine_roots.json` in the TagPup home says where this machine keeps each root. The app writes
it only when an adoption finds the root missing from it: whole, to a temporary name and renamed
over, one editor at a time across processes, and never a root another library put there.
"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402

from tagpup import config  # noqa: E402

WINDOWS = os.name == "nt"


class TheMapFile(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_map_")

    def test_adding_a_root_keeps_the_others_and_is_idempotent(self):
        folder = os.path.join(self.home.root, "Pictures")
        self.assertTrue(config.add_machine_root("Pictures", folder))
        self.assertFalse(config.add_machine_root("pictures", folder.upper()))
        self.assertTrue(config.add_machine_root("scans", os.path.join(self.home.root, "Scans")))
        self.assertEqual({"pictures", "scans"}, set(config.machine_roots()))

    def test_two_editors_at_once_both_keep_their_roots(self):
        """Two libraries adopting two roots at the same moment: each reads the map as the one
        before left it, so neither root is lost to the other's rename."""
        names = ["root%d" % n for n in range(12)]
        failures, gate = [], threading.Barrier(len(names))

        def add(name):
            gate.wait()
            try:
                config.add_machine_root(name, os.path.join(self.home.root, name))
            except BaseException as problem:
                failures.append(problem)

        threads = [threading.Thread(target=add, args=(name,)) for name in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual([], failures)
        self.assertEqual(set(names), set(config.machine_roots()))
        self.assertEqual([], [n for n in os.listdir(self.home.root) if n.endswith((".tmp", ".lock"))])

    def test_a_lock_a_live_editor_holds_is_waited_for_and_one_a_crash_left_is_taken_over(self):
        path = config.machine_roots_path()
        lock = path + ".lock"
        with open(lock, "w", encoding="utf-8"):
            pass
        with self.assertRaises(config.MachineMapError) as raised:
            with config._edit_lock(path, wait=0.2):
                pass
        self.assertIn("being edited by another process", str(raised.exception))
        old = time.time() - 120
        os.utime(lock, (old, old))
        with config._edit_lock(path, wait=0.2):
            pass
        self.assertFalse(os.path.exists(lock))

    @unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
    def test_a_root_may_not_be_added_inside_another(self):
        config.add_machine_root("pictures", os.path.join(self.home.root, "Pictures"))
        with open(config.machine_roots_path(), "rb") as handle:
            before = handle.read()
        with self.assertRaises(config.MachineMapError):
            config.add_machine_root("scans", os.path.join(self.home.root, "Pictures"))
        with open(config.machine_roots_path(), "rb") as handle:
            self.assertEqual(before, handle.read())


if __name__ == "__main__":
    unittest.main()
