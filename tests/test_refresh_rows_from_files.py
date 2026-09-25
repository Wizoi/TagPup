"""scripts/refresh_rows_from_files.py records what stale rows' files actually hold."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db  # noqa: E402
import refresh_rows_from_files as refresh  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402


class RefreshRowsFromFiles(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="refresh_")
        self.db = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db)
        index.load()
        self.files = {}
        for name in ("garbled", "stale_keywords", "stale_stat", "fine"):
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

        # Stored from a correct file with the wrong decoding.
        row("garbled", [], {"IPTC:ObjectName": "Parade in MÃ¼nster"},
            captions=["Parade in MÃ¼nster"])
        # A bulk remove left the old keyword behind in IPTC:Keywords.
        row("stale_keywords", ["Activity/Running"],
            {"XMP:Subject": ["Activity/Running"], "IPTC:Keywords": ["Activity/Running", "Beach"]})
        # The file was written after the row was: mtime disagrees.
        row("stale_stat", ["Activity/Running"], {"XMP:Subject": ["Activity/Running"]},
            mtime=999.0)
        row("fine", ["Activity/Running"], {"XMP:Subject": ["Activity/Running"]})
        # Right about its file, but indexed when every caption was listed twice.
        self.files["twice"] = os.path.join(self.dir, "twice.jpg")
        with open(self.files["twice"], "wb") as handle:
            handle.write(b"jpeg")
        os.utime(self.files["twice"], (1_000_000, 1_000_000))
        row("twice", [], {"IPTC:ObjectName": "Harbour at dusk", "ObjectName": "Harbour at dusk"},
            captions=["Harbour at dusk", "Harbour at dusk"])
        index.conn.commit()
        index.close()

        # What the files really hold, as the extractor would report them.
        self.truth = {
            self.files["garbled"]: {"IPTC:ObjectName": "Parade in Münster"},
            self.files["stale_keywords"]: {"XMP:Subject": ["Activity/Running"],
                                           "IPTC:Keywords": ["Activity/Running"]},
            self.files["stale_stat"]: {"XMP:Subject": ["Activity/Running"]},
            self.files["fine"]: {"XMP:Subject": ["Activity/Running"]},
        }

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def fake_batch_read(self, extractor, paths, db_path=None):
        from metadata import MetadataExtractor, PeopleVocabulary
        self.assertFalse(extractor.mint_identities, "the refresh must never write to a photo")
        self.read.extend(paths)
        people = PeopleVocabulary.load(db_path)
        return [MetadataExtractor._structure(extractor, p, dict(self.truth[p]), people)
                for p in paths]

    def run_script(self, *extra):
        self.read = []
        with mock.patch("metadata.MetadataExtractor.batch_read", autospec=True,
                        side_effect=self.fake_batch_read), \
                mock.patch.object(refresh.tagpup_db, "backup", return_value="(skipped in test)"), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            refresh.main(["--db", self.db, *extra])
        return out.getvalue()

    def rows(self):
        conn = db.connect(db.readonly_uri(self.db), uri=True)
        try:
            return {p: (json.loads(t), json.loads(c), json.loads(r), m)
                    for p, t, c, r, m in conn.execute(
                        "SELECT path, tags, captions, raw_metadata, mtime FROM photos")}
        finally:
            conn.close()

    def test_dry_run_changes_nothing(self):
        before = self.rows()
        out = self.run_script()
        self.assertIn("Dry run", out)
        self.assertEqual(self.rows(), before)

    def test_only_rows_that_disagree_with_their_file_are_read(self):
        self.run_script()
        self.assertEqual(sorted(self.read), sorted(
            self.files[n] for n in ("garbled", "stale_keywords", "stale_stat")))

    def test_apply_records_what_the_files_hold(self):
        out = self.run_script("--apply")
        self.assertIn("rows changed from their files: 3", out)
        self.assertIn("rows with repeated captions removed: 1", out)
        rows = self.rows()
        # Whatever the indexer's extraction makes of the file (it currently lists
        # each caption field twice); what matters is that the garbling is gone.
        captions = rows[self.files["garbled"]][1]
        self.assertIn("Parade in Münster", captions)
        self.assertFalse(any(refresh.MOJIBAKE.search(c) for c in captions))
        self.assertNotIn("Beach", rows[self.files["stale_keywords"]][2]["IPTC:Keywords"])
        self.assertEqual(rows[self.files["stale_stat"]][3], os.stat(self.files["stale_stat"]).st_mtime)
        # And a second run finds nothing left to do.
        again = self.run_script()
        self.assertIn("rows that may not describe their file: 0", again)
        self.assertIn("rows listing a caption more than once: 0", again)

    def test_repeated_captions_are_fixed_from_the_row_without_reading_the_file(self):
        self.run_script("--apply")
        self.assertNotIn(self.files["twice"], self.read)
        self.assertEqual(self.rows()[self.files["twice"]][1], ["Harbour at dusk"])

    def test_the_extractor_lists_each_caption_once(self):
        from metadata import extract_captions
        meta = {"IPTC:ObjectName": "Harbour at dusk", "ObjectName": "Harbour at dusk",
                "XMP:Title": "Harbour at dusk", "Title": "Harbour at dusk",
                "XMP:Description": "Boats coming in", "Description": "Boats coming in"}
        self.assertEqual(extract_captions(meta), ["Boats coming in", "Harbour at dusk"])


if __name__ == "__main__":
    unittest.main()
