"""Edges of the tag order the pages and the server share (tagpup.core.vocabulary.tag_sort_key), found
in the review of the alphabetical lists (docs/findings.md): a keyword read from a file can hold any
text, and one with a run of more than 4,300 digits must not fail every list that sorts tags."""
import unittest

from tagpup.core import vocabulary
from tagpup.store import library_view as store_library_view


class ALongRunOfDigits(unittest.TestCase):
    def test_a_run_of_thousands_of_digits_sorts_without_failing(self):
        long = "a" + "1" * 5000
        tags = [long, "a2", "a10", "Zoo", "apple"]
        ordered = sorted(tags, key=vocabulary.tag_sort_key)
        self.assertEqual(ordered, ["a2", "a10", long, "apple", "Zoo"])

    def test_two_long_runs_compare_as_numbers(self):
        smaller, larger = "x" + "9" * 4400, "x" + "1" + "0" * 4400
        self.assertEqual(sorted([larger, smaller], key=vocabulary.tag_sort_key), [smaller, larger])


class DigitsKeepTheOrderOfNumbers(unittest.TestCase):
    def test_numbers_are_in_order_whatever_their_leading_zeros(self):
        tags = ["Trip 10", "Trip 007", "Trip 3", "Trip 7", "Trip 0", "Trip 100"]
        ordered = sorted(tags, key=vocabulary.tag_sort_key)
        self.assertEqual([t for t in ordered if t != "Trip 7" and t != "Trip 007"],
                         ["Trip 0", "Trip 3", "Trip 10", "Trip 100"])
        self.assertLess(ordered.index("Trip 3"), ordered.index("Trip 7"))
        self.assertLess(ordered.index("Trip 007"), ordered.index("Trip 10"))

    def test_non_ascii_decimal_digits_count_as_their_numbers(self):
        arabic_indic_three = "٣"
        self.assertLess(vocabulary.tag_sort_key("Trip " + arabic_indic_three),
                        vocabulary.tag_sort_key("Trip 10"))


class ThePeopleOfTheNavigator(unittest.TestCase):
    def test_equal_counts_are_in_the_alphabet_not_in_binary_order(self):
        class Conn:
            def execute(self, sql, params=()):
                class Rows:
                    def fetchall(self_inner):
                        return [("Zoe Abbott", 4), ("anh Tran", 4), ("Wen Zhao", 9)]
                return Rows()
        self.assertEqual([name for name, _ in store_library_view.people_counts(Conn())],
                         ["Wen Zhao", "anh Tran", "Zoe Abbott"])


if __name__ == "__main__":
    unittest.main()
