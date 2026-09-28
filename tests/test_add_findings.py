"""tools/add_findings.py numbers `| ? |` rows after the last finding and sets statuses."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import add_findings  # noqa: E402

TABLE = """| # | Found | Finding | Status |
| :-- | :-- | :-- | :-- |
| 1 | 2026-09-20 | A thing. | open |
| 2 | 2026-09-21 | Another. | fixed: abc |

After the table.
"""


class AddFindings(unittest.TestCase):
    def test_rows_are_numbered_after_the_last_in_order(self):
        rows = add_findings.rows_in("intro\n| ? | 2026-09-28 | First. | open |\n| ? | 2026-09-28 | Second. | decided |\n")
        text, given = add_findings.add(TABLE, rows)
        self.assertEqual([3, 4], given)
        self.assertIn("| 2 | 2026-09-21 | Another. | fixed: abc |\n| 3 | 2026-09-28 | First. | open |\n"
                      "| 4 | 2026-09-28 | Second. | decided |\n\nAfter the table.", text)

    def test_a_status_replaces_the_last_column(self):
        text, given = add_findings.add(TABLE, [], [(1, "fixed: def")])
        self.assertIn("| 1 | 2026-09-20 | A thing. | fixed: def |", text)
        self.assertEqual([], given)

    def test_a_missing_row_changes_nothing(self):
        with self.assertRaises(ValueError):
            add_findings.add(TABLE, [], [(9, "fixed")])


if __name__ == "__main__":
    unittest.main()
