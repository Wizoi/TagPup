"""`tagpup_cli write` reads the library's people once for the run, not once per photo.

scripts/writer.py made each photo's caption from the library's face roots, and read
them from the tag tree again for every photo in the suggestions file: a query per item
for an answer that is the same for the whole run. The tree is seeded as the library
stores it.
"""
import json
import os
import sys
import unittest
from unittest import mock

from click.testing import CliRunner

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
from tagpup.store import db, schema  # noqa: E402
from tagpup.store import taxonomy as store_taxonomy  # noqa: E402


class TheCliWriteReadsTheTreeOnce(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self)
        self.library = home.library("crew.db")
        schema.ensure(self.library)
        conn = db.connect(self.library)
        conn.execute("INSERT INTO tag_taxonomy (id, tag, name, parent_id, has_face)"
                     " VALUES (1, 'Crew', 'Crew', NULL, 1)")
        conn.commit()
        conn.close()
        suggestions = []
        for i in range(3):
            path = os.path.join(home.root, "photo_%d.jpg" % i)
            open(path, "wb").close()
            suggestions.append({"path": path, "suggested_tags": [{"tag": "Crew/Tamsin Oakes", "score": 0.9}]})
        self.suggestions = os.path.join(home.root, "suggestions.json")
        with open(self.suggestions, "w", encoding="utf-8") as handle:
            json.dump(suggestions, handle)

    def test_a_preview_of_three_photos_reads_the_tree_once(self):
        from tagpup_cli import cli

        with mock.patch.object(store_taxonomy, "people_vocabulary",
                               wraps=store_taxonomy.people_vocabulary) as read_people:
            result = CliRunner().invoke(cli, ["--db", self.library, "write", self.suggestions])

        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual(3, result.output.count('Caption to set: "Tamsin Oakes"'), result.output)
        self.assertEqual(1, read_people.call_count)


if __name__ == "__main__":
    unittest.main()
