"""tests/tiers.py names test files that exist, each in one tier.

A rename of a scenario file would otherwise leave its old name behind and move it to the
fast tier without a word.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import tiers  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


class TiersNameRealFiles(unittest.TestCase):
    def test_every_name_is_a_test_file(self):
        for name in sorted(tiers.SLOW | tiers.SCENARIO):
            self.assertTrue(os.path.exists(os.path.join(HERE, name + ".py")), name + " is not a test file")

    def test_no_file_is_in_two_tiers(self):
        self.assertEqual(set(), set(tiers.SLOW) & set(tiers.SCENARIO))

    def test_tier_of_says_which(self):
        self.assertEqual("slow", tiers.tier_of(sorted(tiers.SLOW)[0]))
        self.assertEqual("scenario", tiers.tier_of(sorted(tiers.SCENARIO)[0]))
        self.assertEqual("fast", tiers.tier_of("test_fictional_anything"))


if __name__ == "__main__":
    unittest.main()
