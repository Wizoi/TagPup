"""A measurement sandbox runs the server from its own copy of the code, and only that.

Moving the foundation modules into tagpup/ broke both measurement tools without a test
noticing: they copy a fixed list of folders into the sandbox, the list did not include
tagpup/, and the sandbox server could no longer import its database module. It exited
before it was ready, and every measurement would have failed.

So this snapshots the code the way the tools do and imports both servers there, in an
interpreter that cannot see the repository (`-I`: no working directory, no PYTHONPATH).
It fails if the snapshot is missing anything the servers import, and it fails if they
load a module from anywhere but the sandbox.
"""
import os
import subprocess
import sys
import tempfile
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import code_snapshot  # noqa: E402
import measure_identify_faces  # noqa: E402

PROBE = """\
import sys
sys.path.insert(0, %r)
import tuner_server, tagpup_server
import tagpup.store.db
print(tagpup.store.db.__file__)
"""


class SandboxHasAllTheCode(unittest.TestCase):
    def test_both_servers_import_from_the_sandbox_alone(self):
        sandbox = tempfile.mkdtemp(prefix="tagpup_sandbox_code_")
        self.addCleanup(measure_identify_faces.remove_sandbox, sandbox)
        code_snapshot.copy_code(sandbox)

        result = subprocess.run(
            [sys.executable, "-I", "-c", PROBE % os.path.join(sandbox, "scripts")],
            cwd=sandbox, capture_output=True, text=True, timeout=300)

        self.assertEqual(result.returncode, 0, "the sandbox cannot run its server:\n"
                         + result.stderr[-3000:])
        loaded = os.path.normcase(os.path.abspath(result.stdout.strip().splitlines()[-1]))
        self.assertTrue(loaded.startswith(os.path.normcase(os.path.abspath(sandbox)) + os.sep),
                        "the sandbox server loaded code from outside it: %s" % loaded)


if __name__ == "__main__":
    unittest.main()
