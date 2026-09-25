"""The CLI's read-only commands never migrate a library (docs/findings.md, #243).

`stats`, `list-index` and `search` stopped stamping settings, but PhotoIndex.load still
ran schema.ensure, so a look at a library one migration behind applied the migration.
A look now opens the library read-only and says it is behind.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from click.testing import CliRunner  # noqa: E402
from test_migrations import at_version  # noqa: E402

from tagpup.store import db, schema  # noqa: E402
from tagpup_cli import cli  # noqa: E402


class ALookAtALibraryBehind(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self)
        self.db_path = home.library("harbour.db")
        at_version(self.db_path, schema.LATEST - 1)

    def version(self):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return schema.version(conn)
        finally:
            conn.close()

    def look(self, *command):
        with mock.patch("tagpup.ml.clip.ClipModel._init_model"), \
                mock.patch("tagpup.ml.clip.ClipModel.embed_text", return_value=[0.1] * 512):
            result = CliRunner().invoke(cli, ["--db", self.db_path] + list(command))
        self.assertEqual(0, result.exit_code, result.output)
        return " ".join(result.output.split())

    def test_stats_list_index_and_search_leave_it_behind_and_say_so(self):
        for command in (["stats"], ["list-index"], ["search", "harbour"]):
            with self.subTest(command=command[0]):
                output = self.look(*command)
                self.assertEqual(schema.LATEST - 1, self.version())
                self.assertIn("a look does not apply them", output)

    def test_stats_still_counts_the_photos(self):
        self.assertIn("Total Indexed Photos: 2", self.look("stats"))

    def test_a_look_at_no_library_makes_none(self):
        missing = os.path.join(os.path.dirname(self.db_path), "nobody.db")
        result = CliRunner().invoke(cli, ["--db", missing, "stats"])
        self.assertIn("No photo index found", result.output)
        self.assertFalse(os.path.exists(missing))


if __name__ == "__main__":
    unittest.main()
