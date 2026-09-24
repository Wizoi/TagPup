"""tagpup.core.result.Result: what a write did, kept apart from what it attempted."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.core.result import Result  # noqa: E402


class AResult(unittest.TestCase):
    def test_attempted_is_not_changed(self):
        result = Result(attempted=60)
        self.assertEqual(result.changed, 0)
        self.assertTrue(result.ok, "nothing failed, even though nothing changed")

    def test_a_skip_is_not_a_failure(self):
        result = Result(attempted=2, changed=1)
        result.skip("b.jpg", "already carries the tag")
        self.assertTrue(result.ok)
        self.assertEqual(result.skipped, [("b.jpg", "already carries the tag")])

    def test_each_failure_is_kept_and_read_out(self):
        result = Result(attempted=2)
        result.fail("a.jpg", RuntimeError("locked"))
        result.fail("b.jpg", "no such file")
        self.assertFalse(result.ok)
        self.assertEqual(result.message(), "locked; no such file")

    def test_a_refusal_is_not_ok_and_says_why(self):
        result = Result(attempted=1)
        result.refuse('A tag cannot contain "|"')
        self.assertFalse(result.ok)
        self.assertEqual(result.message(), 'A tag cannot contain "|"')

    def test_results_do_not_share_their_lists(self):
        first, second = Result(), Result()
        first.skip("a.jpg", "why")
        first.details["mtime"] = 1.0
        self.assertEqual((second.skipped, second.details), ([], {}))


if __name__ == "__main__":
    unittest.main()
