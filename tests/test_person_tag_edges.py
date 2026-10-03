"""The edges of filing a suggested person (docs/findings.md, the final check of #555): a name with
surrounding spaces is filed as the page's chip files it (trimmed), and a name with nothing in it is
not written, so one empty name cannot refuse the Apply All of a whole folder."""
import unittest

from tagpup.core import suggesting, vocabulary


class ATrimmedName(unittest.TestCase):
    def test_a_name_with_surrounding_spaces_is_filed_trimmed(self):
        self.assertEqual(vocabulary.person_tag("  Wren Okafor  ", [], ["People"]), "People/Wren Okafor")

    def test_a_name_the_tree_files_once_is_that_path_whatever_the_spaces(self):
        self.assertEqual(vocabulary.person_tag("Wren Okafor ", ["Family/Wren Okafor"], ["People"]),
                         "Family/Wren Okafor")

    def test_a_path_is_trimmed_too(self):
        self.assertEqual(vocabulary.person_tag(" Family/Wren Okafor ", [], ["People"]), "Family/Wren Okafor")


class AnEmptyName(unittest.TestCase):
    def test_nothing_in_the_name_is_filed_nowhere(self):
        for name in ("", "   ", None):
            self.assertIsNone(vocabulary.person_tag(name, [], ["People"]), repr(name))

    def test_an_empty_name_is_not_offered_by_apply_all(self):
        entry = {"tags": [{"tag": "Trips/Regatta", "score": 0.9}],
                 "people": [{"name": "", "score": 0.9}, {"name": "  ", "score": 0.9},
                            {"name": "Wren Okafor", "score": 0.9}]}
        filed = lambda name: vocabulary.person_tag(name, [], ["People"]) or name   # noqa: E731
        self.assertEqual(suggesting.offered_tags(entry, 0.0, filed), ["Trips/Regatta", "People/Wren Okafor"])


if __name__ == "__main__":
    unittest.main()
