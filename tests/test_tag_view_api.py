"""The word-tag endpoints: what is in the vocabulary, and what each tag touches.

TagTuner curates faces because a wrong name spreads -- a tagged photo is what the
suggester learns the next photo from. Word tags spread the same way and had no view at
all, so a misspelling could sit in the vocabulary for months, be suggested, be applied,
and become its own source. Finding one took a database query and an ExifTool sweep.

These cover the two reads that make the question answerable, and the merge that makes
it fixable. The merge defaults to a dry run, and the tests below hold it to that: it
rewrites photo files, and every destructive script in this repo has earned its dry run.
"""
import json
import os
import sqlite3
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_tuner_server_api import TunerAPITestBase


class TagViewTestBase(TunerAPITestBase):
    def seed(self, photos, taxonomy=(), embeddings=()):
        """Put photos, a taxonomy and cached embeddings into the test database."""
        conn = sqlite3.connect(self.TEST_DB)
        cur = conn.cursor()
        for path, tags in photos:
            cur.execute(
                "INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, "
                "captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (path, 1000.0, 10, json.dumps(tags), json.dumps([]),
                 json.dumps([]), json.dumps({})),
            )
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_taxonomy'")
        if cur.fetchone():
            cur.execute("DELETE FROM tag_taxonomy")
            for tag, has_face in taxonomy:
                cur.execute(
                    "INSERT INTO tag_taxonomy (tag, name, parent_id, has_face) "
                    "VALUES (?, ?, ?, ?)",
                    (tag, tag.split("/")[-1], None, 1 if has_face else 0),
                )
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tag_embeddings'")
        if cur.fetchone():
            cur.execute("DELETE FROM tag_embeddings")
            for tag in embeddings:
                cur.execute(
                    "INSERT INTO tag_embeddings (tag, prompt, model_name, pretrained, "
                    "embedding) VALUES (?, ?, ?, ?, ?)",
                    (tag, "a photo of %s" % tag, "m", "p", b"\x00"),
                )
        conn.commit()
        conn.close()

    def tags(self, query=""):
        return self.get("/api/tags/list" + query)


class TestWhatIsInTheVocabulary(TagViewTestBase):
    def test_a_tag_is_listed_with_how_many_photos_carry_it(self):
        self.seed([("D:/a.jpg", ["Cross Country"]), ("D:/b.jpg", ["Cross Country"])])
        found = {t["tag"]: t for t in self.tags()["tags"]}
        self.assertEqual(found["Cross Country"]["count"], 2)

    def test_people_are_left_out_by_default(self):
        # The point of this view is the tags TagTuner could not previously reach.
        self.seed(
            [("D:/a.jpg", ["People/Rowan Thackeray", "Cross Country"])],
            taxonomy=[("People", True), ("People/Rowan Thackeray", True),
                      ("Cross Country", False)],
        )
        listed = {t["tag"] for t in self.tags()["tags"]}
        self.assertIn("Cross Country", listed)
        self.assertNotIn("People/Rowan Thackeray", listed)

    def test_people_can_be_asked_for(self):
        self.seed(
            [("D:/a.jpg", ["People/Rowan Thackeray"])],
            taxonomy=[("People", True), ("People/Rowan Thackeray", True)],
        )
        listed = {t["tag"] for t in self.tags("?people=1")["tags"]}
        self.assertIn("People/Rowan Thackeray", listed)

    def test_a_flat_tag_is_marked_flat(self):
        self.seed([("D:/a.jpg", ["Kentridge"])], taxonomy=[("Kentridge", False)])
        found = {t["tag"]: t for t in self.tags()["tags"]}
        self.assertTrue(found["Kentridge"]["flat"])

    def test_a_cached_embedding_is_reported(self):
        # Where a retired tag goes on living: the files can be clean and the taxonomy
        # clean, and zero-shot matching still knows the name.
        self.seed([("D:/a.jpg", ["Kentridge"])], embeddings=["Kentridge"])
        found = {t["tag"]: t for t in self.tags()["tags"]}
        self.assertTrue(found["Kentridge"]["has_embedding"])

    def test_a_tag_on_no_photo_is_still_listed(self):
        self.seed([("D:/a.jpg", ["Kentridge"])],
                  taxonomy=[("Kentridge", False), ("Regatta", False)])
        found = {t["tag"]: t for t in self.tags()["tags"]}
        self.assertIn("Regatta", found)
        self.assertEqual(found["Regatta"]["count"], 0)


class TestTheBuckets(TagViewTestBase):
    def test_used_once_collects_the_long_tail(self):
        # Where typos hide: a tag used once has never been confirmed by a second photo.
        self.seed([
            ("D:/a.jpg", ["Cross Country", "Regata"]),
            ("D:/b.jpg", ["Cross Country"]),
        ])
        self.assertEqual(self.tags()["buckets"]["used_once"], ["Regata"])

    def test_unused_collects_tags_no_photo_carries(self):
        self.seed([("D:/a.jpg", ["Kentridge"])],
                  taxonomy=[("Kentridge", False), ("Regatta", False)])
        self.assertEqual(self.tags()["buckets"]["unused"], ["Regatta"])

    def test_flat_collects_tags_with_no_path(self):
        self.seed([("D:/a.jpg", ["Kentridge", "Activity/Rowing"])],
                  taxonomy=[("Kentridge", False), ("Activity/Rowing", False)])
        self.assertEqual(self.tags()["buckets"]["flat"], ["Kentridge"])

    def test_a_person_who_lost_their_path_is_called_out(self):
        # Not a word tag -- a person in the form the keyword convention forbids, which
        # is exactly what somebody opening this view wants told.
        self.seed(
            [("D:/a.jpg", ["Rowan Thackeray", "Cross Country"])],
            taxonomy=[("People", True), ("People/Rowan Thackeray", True)],
        )
        result = self.tags()
        self.assertEqual(result["buckets"]["people_without_a_path"], ["Rowan Thackeray"])
        self.assertNotIn("Rowan Thackeray", {t["tag"] for t in result["tags"]})


class TestThePhotosBehindATag(TagViewTestBase):
    def test_it_returns_the_photos_carrying_the_tag(self):
        self.seed([
            ("D:/a.jpg", ["Cross Country"]),
            ("D:/b.jpg", ["Cross Country"]),
            ("D:/c.jpg", ["Regatta"]),
        ])
        result = self.get("/api/tags/photos?tag=Cross%20Country")
        self.assertEqual(result["total"], 2)
        self.assertEqual({p["filename"] for p in result["photos"]}, {"a.jpg", "b.jpg"})

    def test_a_tag_nobody_carries_returns_nothing_rather_than_failing(self):
        self.seed([("D:/a.jpg", ["Cross Country"])])
        self.assertEqual(self.get("/api/tags/photos?tag=Regatta")["total"], 0)

    def test_a_missing_tag_parameter_is_refused(self):
        status, _body = self.get_with_status("/api/tags/photos")
        self.assertEqual(status, 400)

    def test_a_partial_match_does_not_count(self):
        # "Cross" must not pull in "Cross Country".
        self.seed([("D:/a.jpg", ["Cross Country"])])
        self.assertEqual(self.get("/api/tags/photos?tag=Cross")["total"], 0)


class TestMergingATag(TagViewTestBase):
    def plan(self, **body):
        return self.post("/api/tags/merge", body)

    def test_it_is_a_dry_run_unless_told_otherwise(self):
        self.seed([("D:/a.jpg", ["Regata"])], embeddings=["Regata"])
        status, body = self.plan(**{"from": "Regata", "into": "Regatta"})
        self.assertEqual(status, 200)
        self.assertFalse(body["applied"], "the merge wrote without being asked to")

    def test_the_plan_says_what_it_would_touch(self):
        self.seed(
            [("D:/a.jpg", ["Regata"]), ("D:/b.jpg", ["Regata"]), ("D:/c.jpg", ["Other"])],
            taxonomy=[("Regata", False)],
            embeddings=["Regata"],
        )
        _status, body = self.plan(**{"from": "Regata", "into": "Regatta"})
        self.assertEqual(body["photos"], 2)
        self.assertEqual(body["embeddings_to_drop"], 1)
        self.assertEqual(body["taxonomy_rows_to_drop"], 1)

    def test_it_reports_photos_that_already_carry_the_target(self):
        # Those photos end up with one tag where they had two, which is the merge
        # working, but it changes the count somebody is about to see.
        self.seed([("D:/a.jpg", ["Regata", "Regatta"]), ("D:/b.jpg", ["Regata"])])
        _status, body = self.plan(**{"from": "Regata", "into": "Regatta"})
        self.assertEqual(body["photos"], 2)
        self.assertEqual(body["photos_already_carrying_the_target"], 1)

    def test_merging_a_tag_into_itself_is_refused(self):
        self.seed([("D:/a.jpg", ["Regatta"])])
        status, _body = self.plan(**{"from": "Regatta", "into": "Regatta"})
        self.assertEqual(status, 400)

    def test_a_merge_with_no_target_is_refused(self):
        self.seed([("D:/a.jpg", ["Regata"])])
        status, _body = self.plan(**{"from": "Regata"})
        self.assertEqual(status, 400)

    def test_retiring_needs_no_target(self):
        self.seed([("D:/a.jpg", ["Regata"])])
        status, body = self.plan(**{"from": "Regata", "retire": True})
        self.assertEqual(status, 200)
        self.assertTrue(body["retire_only"])

    def test_applying_drops_the_cached_embedding(self):
        # The fault that kept a corrected tag alive: files clean, taxonomy clean, and
        # zero-shot matching still reading the name out of tag_embeddings.
        self.seed([("D:/gone.jpg", ["Regata"])],
                  taxonomy=[("Regata", False)], embeddings=["Regata"])
        _status, body = self.plan(**{"from": "Regata", "retire": True, "apply": True})
        self.assertTrue(body["applied"])

        conn = sqlite3.connect(self.TEST_DB)
        try:
            left = conn.execute(
                "SELECT COUNT(*) FROM tag_embeddings WHERE tag = 'Regata'").fetchone()[0]
            tax = conn.execute(
                "SELECT COUNT(*) FROM tag_taxonomy WHERE tag = 'Regata'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(left, 0, "the embedding survived, so it would still be suggested")
        self.assertEqual(tax, 0, "the taxonomy row survived, so it would still be offered")


if __name__ == "__main__":
    unittest.main()
