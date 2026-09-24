"""canonicalize_paths rewrites under the library's write lock, like every other write.

It rewrites paths across photos, faces and embedding_cache in one transaction, through
its own autocommit connection -- beside the app's writes, with nothing to keep them apart.
"""
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import canonicalize_paths  # noqa: E402
from index import PhotoIndex  # noqa: E402


class CanonicalizeTakesTheWriteLock(unittest.TestCase):
    def test_apply_runs_inside_the_lock(self):
        tmp = tempfile.mkdtemp(prefix="canonicalize_lock_")
        self.addCleanup(shutil.rmtree, tmp, True)
        db = os.path.join(tmp, "lib.db")
        index = PhotoIndex(db)
        index.load()
        index.close()

        held = []
        real_apply = canonicalize_paths.apply

        @contextmanager
        def writing(target, label="database write"):
            held.append(True)
            yield
            held.pop()

        def apply(*args):
            self.assertTrue(held, "apply ran without the write lock")
            return real_apply(*args)

        with mock.patch.object(canonicalize_paths.tagpup_db, "writing", writing), \
                mock.patch.object(canonicalize_paths.tagpup_db, "backup", return_value="(skipped)"), \
                mock.patch.object(canonicalize_paths, "apply", side_effect=apply) as spy:
            canonicalize_paths.main(["--db", db, "--apply"])
        self.assertTrue(spy.called)


if __name__ == "__main__":
    unittest.main()
