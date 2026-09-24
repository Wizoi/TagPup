"""TagTuner's buckets -- Unknown Faces, Ungrouped, Excluded -- are named once, in
tagpup.core.vocabulary.BUCKETS, and nobody can be given one of those names: a person
called "Excluded" opened the excluded faces instead of their own (docs/findings.md, #68).
"""
import os
import re
import unittest

from tagpup.core import vocabulary

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NobodyIsCalledABucket(unittest.TestCase):
    def test_a_bucket_name_is_refused_whatever_the_case(self):
        for name in ("Excluded", "ungrouped", "Unknown Faces", " UNMATCHED "):
            self.assertIsNotNone(vocabulary.problem_with_name(name), name)
        self.assertIsNone(vocabulary.problem_with_name("Excluded Ferris"))

    def test_the_page_names_them_as_the_server_does(self):
        with open(os.path.join(ROOT, "gui", "app.js"), encoding="utf-8") as f:
            page = f.read()
        found = re.search(r"const BUCKET = Object\.freeze\(\{(.*?)\}\)", page)
        self.assertIsNotNone(found, "the page's copy of the bucket names moved")
        copy = dict(re.findall(r"(\w+):\s*'([^']*)'", found.group(1)))
        self.assertEqual({key.upper(): name for key, name in vocabulary.BUCKETS.items()}, copy)

    def test_the_page_spells_them_nowhere_else(self):
        with open(os.path.join(ROOT, "gui", "app.js"), encoding="utf-8") as f:
            lines = f.read().splitlines()
        spelled = [n for n, line in enumerate(lines, 1)
                   if re.search(r"'(Unknown Faces|Ungrouped|Excluded)'", line) and "const BUCKET" not in line]
        self.assertEqual([], spelled)


if __name__ == "__main__":
    unittest.main()
