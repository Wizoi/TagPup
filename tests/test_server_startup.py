"""tagpup_web.py, started as a person starts it: one process, both pages answering.

In a home of its own: started plainly, it opened -- and made, when missing -- the
checkout's data/photo_index.db, and logged into its data/logs.

`--db` with a name no library may have serves nothing and says why: the launcher once logged "making
one", made nothing, and served a library that was not there, every request failing with nothing naming
the cause (Lifted: test_launcher_refuses_a_library_it_cannot_make; mutation: ignoring the refused Result
in tagpup_web.main fails the first test of TheLauncherAndAName).
"""
import logging
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
from tagpup.core import processes  # noqa: E402
import own_home  # noqa: E402
from free_port import free_port  # noqa: E402

import tagpup_web  # noqa: E402


class TestServerStartup(unittest.TestCase):
    def test_both_pages_answer_from_one_process(self):
        home = own_home.for_test(self, "tagpup_startup_")
        tagpup_port, tuner_port = free_port(), free_port()
        proc = processes.start(
            [sys.executable, os.path.join(PROJECT_ROOT, "tagpup_web.py"),
             "--tagpup-port", str(tagpup_port), "--tuner-port", str(tuner_port)],
            env=dict(os.environ, TAGPUP_HOME=home.root, TAGPUP_NO_MODEL_WEIGHTS="1", TAGPUP_NO_JOBS="1"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=PROJECT_ROOT)
        try:
            deadline = time.time() + 60
            while time.time() < deadline:
                if proc.poll() is not None:
                    self.fail("the server exited before it was ready:\n" + proc.stderr.read()[-3000:])
                try:
                    with socket.create_connection(("127.0.0.1", tuner_port), timeout=1):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                self.fail("the server never came up")

            for port, title in ((tagpup_port, "TagPup"), (tuner_port, "TagTuner")):
                with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=5.0) as response:
                    self.assertEqual(200, response.status)
                    self.assertIn(title, response.read().decode("utf-8"), port)
            self.assertTrue(os.path.exists(os.path.join(home.root, "data", "logs", "tagpup_web.log")))
            # No library was opened, let alone made, for a request that named none (#100).
            self.assertFalse(os.path.exists(os.path.join(home.root, "data", "photo_index.db")))
        finally:
            processes.kill_tree(proc.pid)
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()

    def test_tag_embedding_cache(self):
        # Add scripts directory to path to import index
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
        from tagpup.services.search import PhotoIndex

        test_db = own_home.for_test(self).library("test_tag_emb_cache.db")

        photo_index = PhotoIndex(db_path=test_db)
        self.assertTrue(photo_index.load())

        # Initially, get_tag_embedding should return None
        cached = photo_index.get_tag_embedding("test_tag", "prompt", "model", "pretrained")
        self.assertIsNone(cached)

        # Save embedding
        test_emb = [0.1, 0.2, 0.3, 0.4]
        photo_index.save_tag_embedding("test_tag", "prompt", "model", "pretrained", test_emb)

        # Retrieve embedding
        retrieved = photo_index.get_tag_embedding("test_tag", "prompt", "model", "pretrained")
        self.assertIsNotNone(retrieved)
        self.assertEqual(len(retrieved), len(test_emb))
        for a, b in zip(retrieved, test_emb):
            self.assertAlmostEqual(a, b, places=5)

        # Querying with different params should return None
        self.assertIsNone(photo_index.get_tag_embedding("test_tag", "diff_prompt", "model", "pretrained"))
        self.assertIsNone(photo_index.get_tag_embedding("test_tag", "prompt", "diff_model", "pretrained"))
        self.assertIsNone(photo_index.get_tag_embedding("test_tag", "prompt", "model", "diff_pretrained"))


class TheLauncherAndAName(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        for patcher in (mock.patch.dict(os.environ, {"TAGPUP_WEB_NO_WARMUP": "1"}),
                        mock.patch.object(tagpup_web.logs, "to_file", return_value="(no log file)")):
            patcher.start()
            self.addCleanup(patcher.stop)
        serve = mock.patch.object(tagpup_web.web, "serve")
        self.serve = serve.start()
        self.addCleanup(serve.stop)

    def test_a_name_the_rule_refuses_is_not_served(self):
        with self.assertLogs("tagpup_web", level="ERROR") as logged:
            code = tagpup_web.main(["--db", "Harbour Photos", "--tagpup-port", "1", "--tuner-port", "2"])
        self.assertNotEqual(0, code)
        self.serve.assert_not_called()
        self.assertEqual([], os.listdir(self.home.data), "a library was made")
        self.assertTrue(any("letters, numbers, underscores and hyphens" in line for line in logged.output),
                        logged.output)

    def test_a_name_it_may_make_is_made_and_served(self):
        logging.getLogger("tagpup_web").setLevel(logging.INFO)
        self.assertEqual(0, tagpup_web.main(["--db", "harbour", "--tagpup-port", "1", "--tuner-port", "2"]))
        self.serve.assert_called_once()
        self.assertTrue(os.path.exists(self.home.library("harbour.db")))


if __name__ == "__main__":
    unittest.main()
