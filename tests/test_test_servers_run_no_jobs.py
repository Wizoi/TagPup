"""A server or MCP server a test starts as a process is told to run no recurring job
(TAGPUP_NO_JOBS; review of phase 8a, 7).

A server knows a test started it by the flag the tests set (tagpup.ml.under_test), which
a child inherits; a child started with an environment of its own, or a run that never
imports the tests package, would snapshot its library on a thread while the test ran.
The flag says it outright, whatever else the child inherits.
"""
import os
import re
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))

#: A test starting one of the programs that may run the recurring jobs.
STARTS_A_SERVER = re.compile(r"""tagpup_web\.py|TagTuner\.cmd|TagPup\.cmd|["']tagpup\.mcp["']""")
SPAWNS = re.compile(r"processes\.start\(|subprocess\.Popen\(")


class TheTestsServers(unittest.TestCase):
    def test_are_started_with_no_jobs(self):
        missing = []
        for name in sorted(os.listdir(TESTS)):
            if not (name.startswith("test_") and name.endswith(".py")) or name == os.path.basename(__file__):
                continue
            with open(os.path.join(TESTS, name), encoding="utf-8") as handle:
                source = handle.read()
            if SPAWNS.search(source) and STARTS_A_SERVER.search(source) and "TAGPUP_NO_JOBS" not in source:
                missing.append(name)
        self.assertEqual([], missing, "these start a server without TAGPUP_NO_JOBS=1")


if __name__ == "__main__":
    unittest.main()
