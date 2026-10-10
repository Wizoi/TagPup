"""tagpup.services.refresh_rows records what stale rows' files actually hold.

The refresh as the MCP server calls it (a Result: details["counts"], ["changed"], `skipped`,
`errors`), over a library whose rows disagree with their files in the ways the indexer never
noticed. No backup is taken; the change is the journal's.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.services import refresh_rows  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402


class RefreshFixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="refresh_")
        self.db = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db)
        index.load()
        self.files = {}
        for name in ("stale_keywords", "stale_stat", "fine", "path_only"):
            path = os.path.join(self.dir, name + ".jpg")
            with open(path, "wb") as handle:
                handle.write(b"jpeg")
            os.utime(path, (1_000_000, 1_000_000))
            self.files[name] = path

        def row(name, tags, raw, captions=(), mtime=1_000_000.0, size=4):
            index.conn.execute(
                "INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (self.files[name], mtime, size, json.dumps(tags), json.dumps(list(captions)),
                 json.dumps(raw)))

        # A bulk remove left the old keyword behind in IPTC:Keywords, and the file was written after the row
        # was: mtime disagrees.
        row("stale_keywords", ["Activity/Running"],
            {"XMP:Subject": ["Activity/Running"], "IPTC:Keywords": ["Activity/Running", "Beach"]}, mtime=999.0)
        # The file was written after the row was: mtime disagrees.
        row("stale_stat", ["Activity/Running"], {"XMP:Subject": ["Activity/Running"]},
            mtime=999.0)
        row("fine", ["Activity/Running"], {"XMP:Subject": ["Activity/Running"],
                                           "Subject": ["Activity/Running"]})
        # Made by Suggest: the path and nothing else, no stamp.
        row("path_only", [], {}, mtime=None, size=None)
        index.conn.commit()
        index.close()

        # What the files really hold, as the extractor would report them.
        self.truth = {
            self.files["stale_keywords"]: {"XMP:Subject": ["Activity/Running"],
                                           "IPTC:Keywords": ["Activity/Running"]},
            self.files["stale_stat"]: {"XMP:Subject": ["Activity/Running"]},
            self.files["fine"]: {"XMP:Subject": ["Activity/Running"]},
            self.files["path_only"]: {"XMP:Subject": ["Activity/Running"],
                                      "IPTC:Keywords": ["Activity/Running"],
                                      "EXIF:DateTimeOriginal": "2026:09:23 10:15:00"},
        }

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def fake_batch_read(self, extractor, paths, people=None):
        from tagpup.files.metadata import MetadataExtractor
        self.read.extend(paths)
        return [MetadataExtractor._structure(extractor, p, dict(self.truth[p]), people)
                for p in paths]

    def refresh(self, exiftool="exiftool.exe", **kwargs):
        """The refresh, called as the MCP server calls it, reading the fixture's `truth`."""
        from tagpup.files.metadata import MetadataExtractor
        self.read = []
        with mock.patch.object(MetadataExtractor, "batch_read", autospec=True, side_effect=self.fake_batch_read):
            return refresh_rows.refresh_rows(Library(self.db), exiftool, **kwargs)

    def photo_id(self, path):
        conn = db.connect(db.readonly_uri(self.db), uri=True)
        try:
            where, params = paths.sql_equals("path", path)
            return conn.execute("SELECT id FROM photos WHERE " + where, params).fetchone()[0]
        finally:
            conn.close()

    def backups(self):
        folder = Library(self.db).backups
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def ids(self, *names):
        return sorted(self.photo_id(self.files[n]) for n in names)

    def rows(self):
        conn = db.connect(db.readonly_uri(self.db), uri=True)
        try:
            return {p: (json.loads(t), json.loads(c), json.loads(r), m)
                    for p, t, c, r, m in conn.execute(
                        "SELECT path, tags, captions, raw_metadata, mtime FROM photos")}
        finally:
            conn.close()


class RefreshRows(RefreshFixture):
    def test_dry_run_changes_nothing(self):
        before = self.rows()
        result = self.refresh()
        self.assertTrue(result.details["dry_run"])
        self.assertEqual(self.rows(), before)

    def test_only_rows_that_disagree_with_their_file_are_read(self):
        self.refresh()
        self.assertEqual(sorted(self.read), sorted(
            self.files[n] for n in ("stale_keywords", "stale_stat", "path_only")))

    def test_apply_records_what_the_files_hold(self):
        result = self.refresh(apply=True)
        self.assertEqual({"from_files": 3}, result.details["changed"])
        rows = self.rows()
        self.assertNotIn("Beach", rows[self.files["stale_keywords"]][2]["IPTC:Keywords"])
        self.assertEqual(rows[self.files["stale_stat"]][3], os.stat(self.files["stale_stat"]).st_mtime)
        # And a second run finds nothing left to do.
        self.assertEqual(0, self.refresh().details["counts"]["stale"])

    def test_a_row_never_read_is_read_and_dated(self):
        self.refresh(apply=True)
        conn = db.connect(db.readonly_uri(self.db), uri=True)
        try:
            raw, taken = conn.execute("SELECT raw_metadata, taken FROM photos WHERE path = ?",
                                      (self.files["path_only"],)).fetchone()
        finally:
            conn.close()
        self.assertEqual(json.loads(raw)["DateTimeOriginal"], "2026:09:23 10:15:00")
        self.assertTrue(taken and taken.startswith("2026"), taken)

    def test_the_extractor_lists_each_caption_once(self):
        from tagpup.core.vocabulary import extract_captions
        meta = {"IPTC:ObjectName": "Harbour at dusk", "ObjectName": "Harbour at dusk",
                "XMP:Title": "Harbour at dusk", "Title": "Harbour at dusk",
                "XMP:Description": "Boats coming in", "Description": "Boats coming in"}
        self.assertEqual(extract_captions(meta), ["Boats coming in", "Harbour at dusk"])

    def test_a_dry_run_plans_by_id_and_changes_nothing(self):
        before = self.rows()
        result = self.refresh()
        self.assertEqual(before, self.rows())
        self.assertEqual([], self.backups())
        self.assertEqual((3, True, True), (result.details["rehearsal"]["rows"], result.details["rehearsal"]["exact"],
                                           result.details["rehearsal"]["derived_exact"]))
        self.assertEqual(self.ids("stale_keywords", "stale_stat", "path_only"),
                         sorted(result.details["ids"]["to_write"]))
        self.assertEqual((3, 0), (result.attempted, result.changed))

    def test_an_apply_records_a_change_and_counts_rows_changed(self):
        result = self.refresh(apply=True)
        self.assertEqual([], self.backups())
        self.assertIsInstance(result.details["change"], int)
        self.assertEqual((3, 3), (result.attempted, result.changed))
        self.assertEqual({"from_files": 3}, result.details["changed"])
        self.assertEqual([], result.skipped)

    def test_the_librarys_people_are_read_once_a_run_not_once_a_photo(self):
        # The old script read the tree's people again for every batch and every photo read,
        # each on a connection of its own.
        from tagpup.store import taxonomy
        with mock.patch.object(taxonomy, "people_vocabulary", wraps=taxonomy.people_vocabulary) as read:
            self.refresh()
        self.assertEqual(3, len(self.read), "the fixture has three files to read")
        self.assertLessEqual(read.call_count, 2)

    def test_a_row_saved_after_the_read_is_skipped_by_id_and_the_rest_written(self):
        real = self.fake_batch_read

        def read_then_the_app_saves(extractor, paths, people=None):
            records = real(extractor, paths, people)
            self_conn = db.connect(self.db)
            self_conn.execute("UPDATE photos SET mtime = 2000000.0 WHERE path = ?", (self.files["stale_stat"],))
            self_conn.commit()
            self_conn.close()
            return records

        self.fake_batch_read = read_then_the_app_saves
        before = self.rows()
        result = self.refresh(apply=True)
        # The saved row is skipped, by id, and keeps its save; the other two are written
        # (the whole change was refused, and a second run read every file again). A
        # second run reads the skipped one again.
        self.assertEqual((3, 2, None), (result.attempted, result.changed, result.refused))
        self.assertEqual([("photos %d" % self.ids("stale_stat")[0], "not what the plan read: mtime changed")],
                         result.skipped)
        self.assertEqual(2000000.0, self.rows()[self.files["stale_stat"]][3], "the app's save is kept")
        for name in ("stale_keywords", "path_only"):
            self.assertNotEqual(before[self.files[name]], self.rows()[self.files[name]], name)


if __name__ == "__main__":
    unittest.main()
