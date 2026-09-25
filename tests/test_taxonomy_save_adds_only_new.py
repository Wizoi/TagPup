"""Saving a taxonomy adds the paths this process added -- not everything it once loaded.

An indexer loads the taxonomy when it starts and saves it as it finds new keywords.
save_to_db inserted every path in memory that the table lacked, so a tag deleted or
renamed in TagPup or TagTuner while the indexer ran came back the next time it saved:
the old name reappeared beside the new one, and a deleted tag undid itself.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db as tagpup_db  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from taxonomy import TagTaxonomy  # noqa: E402


class SavingAddsOnlyWhatWasAdded(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "lib.db")
        index = PhotoIndex(self.db_path)
        index.load()
        index.close()
        seed = TagTaxonomy(db_path=self.db_path)
        seed.add_tags(["Activity/Rowing", "People/Rowan Thackeray"])
        seed.save()

    def tearDown(self):
        self.tmp.cleanup()

    def tags(self):
        conn = tagpup_db.connect(self.db_path)
        try:
            return {row[0] for row in conn.execute("SELECT tag FROM tag_taxonomy")}
        finally:
            conn.close()

    def test_a_tag_deleted_elsewhere_stays_deleted(self):
        indexer = TagTaxonomy(db_path=self.db_path)
        indexer.load()

        # Meanwhile, somebody deletes a tag in the app.
        conn = tagpup_db.connect(self.db_path)
        conn.execute("DELETE FROM tag_taxonomy WHERE tag = 'Activity/Rowing'")
        conn.commit()
        conn.close()

        indexer.add_tags(["Activity/Sailing"])
        indexer.save()

        tags = self.tags()
        self.assertIn("Activity/Sailing", tags)
        self.assertNotIn("Activity/Rowing", tags, "the deleted tag came back")

    def test_a_tag_renamed_elsewhere_does_not_come_back_under_its_old_name(self):
        indexer = TagTaxonomy(db_path=self.db_path)
        indexer.load()

        conn = tagpup_db.connect(self.db_path)
        conn.execute("UPDATE tag_taxonomy SET tag = 'People/Rowan Thackeray-Vale',"
                     " name = 'Rowan Thackeray-Vale' WHERE tag = 'People/Rowan Thackeray'")
        conn.commit()
        conn.close()

        indexer.add_tags(["Activity/Sailing"])
        indexer.save()

        self.assertNotIn("People/Rowan Thackeray", self.tags())

    def test_a_taxonomy_that_was_never_loaded_still_saves_everything(self):
        fresh = TagTaxonomy(db_path=self.db_path)
        fresh.paths = {"Places/Harbour"}
        fresh.save()
        self.assertIn("Places/Harbour", self.tags())


if __name__ == "__main__":
    unittest.main()
