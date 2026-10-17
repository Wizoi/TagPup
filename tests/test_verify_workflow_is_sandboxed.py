"""verify_workflow.py works on copies of the photos, and on ports nobody else has.

It copied the database and then indexed the --folder it was given -- somebody's real
photos -- and indexing writes an identity into every photo that lacks one. Its
servers took fixed ports, so it could answer another run's requests, or a test
suite's.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import verify_workflow  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402


class VerifyWorkflowIsSandboxed(unittest.TestCase):
    def setUp(self):
        # verify_workflow copies the library into the data folder as
        # test_verify_workflow.db: the checkout's, until this had a home of its own.
        self.home = own_home.for_test(self, "tagpup_verify_")
        self.tmp = tempfile.mkdtemp(prefix="verify_sandbox_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source = os.path.join(self.tmp, "library.db")
        index = PhotoIndex(self.source)
        index.load()
        index.close()
        self.folder = os.path.join(self.tmp, "Regatta")
        os.makedirs(os.path.join(self.folder, "Heats"))
        for name in ("a.jpg", os.path.join("Heats", "b.jpg")):
            with open(os.path.join(self.folder, name), "wb") as handle:
                handle.write(b"a photo")

    def test_the_run_and_its_server_have_a_home_of_their_own_deleted_after(self):
        """#817: not the owner's checkout: no records, logs, Downloads or recurring jobs there."""
        from sandbox import environment
        seen = {}

        def run_checks(report, work_db, args):
            seen["home"] = verify_workflow.tagpup_config.home()
            seen["work_db"] = work_db
            seen["server_env"] = environment(verify_workflow.SANDBOX_HOME, TAGPUP_NO_JOBS="1")

        argv = ["verify_workflow.py", "--source", self.source, "--no-index"]
        owner = verify_workflow.tagpup_config.home()
        with patch.object(sys, "argv", argv), \
                patch.object(verify_workflow, "run_checks", run_checks), \
                patch.object(verify_workflow, "indexers_running", lambda: []), \
                patch.object(verify_workflow.time, "sleep", lambda s: None), \
                patch.object(verify_workflow, "start_servers", lambda work_db: None):
            verify_workflow.main()
        self.assertNotEqual(os.path.normcase(owner), os.path.normcase(seen["home"]))
        self.assertTrue(os.path.normcase(seen["work_db"]).startswith(os.path.normcase(seen["home"])))
        for name in ("TAGPUP_SERVERS", "TAGPUP_DOWNLOADS"):
            self.assertTrue(seen["server_env"][name].startswith(seen["home"]), name)
        self.assertEqual("1", seen["server_env"]["TAGPUP_NO_JOBS"])
        self.assertFalse(os.path.exists(seen["home"]), "the sandbox home was left behind")

    def test_the_checks_are_given_a_copy_of_the_folder_and_free_ports(self):
        seen = {}

        def run_checks(report, work_db, args):
            seen["folder"] = args.folder
            seen["ports"] = (verify_workflow.TUNER_PORT, verify_workflow.TAGPUP_PORT)
            seen["copied"] = sorted(
                os.path.relpath(os.path.join(root, f), args.folder)
                for root, _, names in os.walk(args.folder) for f in names)

        argv = ["verify_workflow.py", "--source", self.source, "--folder", self.folder, "--no-index"]
        with patch.object(sys, "argv", argv), \
                patch.object(verify_workflow, "run_checks", run_checks), \
                patch.object(verify_workflow, "indexers_running", lambda: []), \
                patch.object(verify_workflow.time, "sleep", lambda s: None), \
                patch.object(verify_workflow, "start_servers", lambda work_db: None):
            verify_workflow.main()

        self.assertNotEqual(os.path.abspath(self.folder), os.path.abspath(seen["folder"]),
                            "the checks were given the real folder")
        self.assertEqual(["Heats" + os.sep + "b.jpg", "a.jpg"], seen["copied"])
        self.assertNotIn(9401, seen["ports"])
        self.assertNotIn(9402, seen["ports"])
        self.assertFalse(os.path.exists(seen["folder"]), "the copy was left behind")


if __name__ == "__main__":
    unittest.main()
