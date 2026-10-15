"""Every test file has one domain owner (tests/owners.py), and the map only gets better.

A test file added without an owner is named here; a scenario file names scenarios that exist; a file named for an owner
(data_, people_, files_, views_, apps_) is that owner's; the tier file (tests/tiers.py) and the map agree about which files
run a flow; the files nobody could place (kind unmapped) only go down in number; and a finding a test cites (#NNNN) is
not dropped from the tests unless its number is in owners.RETIRED -- a finding is a bug that cost time, and the test that
cites it is its memory (the freeze is tests/findings_cited.txt).
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import owners  # noqa: E402
import tiers  # noqa: E402
from shipped_sources import ROOT  # noqa: E402

TESTS = os.path.join(ROOT, "tests")
FRONTEND = os.path.join(TESTS, "frontend")
#: Files nobody could place by what they import or how they are named (owners.py, kind unmapped). Lower it when one is placed.
UNMAPPED_TODAY = 4
CITES = re.compile(r"#(\d{2,4})\b")


def test_files():
    """tests/test_*.py by module name, tests/frontend/*.test.mjs by file name."""
    return ({n[:-3] for n in os.listdir(TESTS) if n.startswith("test_") and n.endswith(".py")}
            | {n for n in os.listdir(FRONTEND) if n.endswith(".test.mjs")})


def cited(names):
    found = set()
    for name in names:
        path = os.path.join(TESTS, name + ".py") if name.startswith("test_") else os.path.join(FRONTEND, name)
        with open(path, encoding="utf-8") as handle:
            found |= set(CITES.findall(handle.read()))
    return found


class EveryTestFileHasOneOwner(unittest.TestCase):
    def test_every_test_file_is_in_the_map_and_the_map_names_only_test_files(self):
        here = test_files()
        self.assertEqual([], sorted(here - set(owners.FILES)), "add the file to _FILES in tests/owners.py, under its owner")
        self.assertEqual([], sorted(set(owners.FILES) - here), "owners.py names a test file that is not there")
        for name, (owner, kind, ids) in owners.FILES.items():
            with self.subTest(name):
                self.assertIn(owner, owners.OWNERS)
                self.assertIn(kind, owners.KINDS)
                prefix = re.match(r"(data|people|files|views|apps)_", name)
                self.assertTrue(not prefix or prefix.group(1) == owner, "a file named for an owner is that owner's")

    def test_a_scenario_file_names_scenarios_that_exist_and_are_its_owners(self):
        for name, (owner, kind, ids) in owners.FILES.items():
            with self.subTest(name):
                self.assertEqual(kind == "scenario", bool(ids), "a scenario file names its ids, and only it does")
                for sid in ids:
                    self.assertEqual(owner, owners.SCENARIOS[sid][0])
        self.assertEqual({o for o, _s in owners.SCENARIOS.values()}, set(owners.OWNERS))

    def test_the_tier_file_and_the_map_agree_about_the_files_that_run_a_flow(self):
        long = tiers.SCENARIO | tiers.SLOW
        self.assertEqual([], sorted(n for n in long if owners.FILES[n][1] != "scenario"),
                         "a file the tiers call a scenario or slow is kind scenario in owners.py (name its scenario)")

    def test_the_files_nobody_placed_only_go_down(self):
        unmapped = sorted(n for n, (_o, kind, _i) in owners.FILES.items() if kind == "unmapped")
        self.assertEqual(UNMAPPED_TODAY, len(unmapped), "unmapped files now %s: place one and lower UNMAPPED_TODAY; "
                                                         "a new file is placed by its owner, not left here" % unmapped)

    def test_a_finding_a_test_cites_is_not_dropped_unless_retired(self):
        with open(os.path.join(TESTS, "findings_cited.txt"), encoding="utf-8") as handle:
            frozen = set(handle.read().split())
        lost = frozen - cited(test_files()) - {str(n) for n in owners.RETIRED}
        self.assertEqual([], sorted(lost, key=int), "tests no longer cite these findings; put the number in owners.RETIRED "
                                                    "if its test is truly redundant (and say why in the commit)")


if __name__ == "__main__":
    unittest.main()
