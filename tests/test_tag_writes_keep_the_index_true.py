"""Every write of a photo's keywords leaves its index row saying what the file says.

Three ways it did not:

* The row recorded XMP:Subject and XMP:HierarchicalSubject but not IPTC:Keywords, which
  the file also carries and metadata.extract_tags also reads. A tag removed in bulk
  stayed in raw_metadata, and came back the next time anything re-derived tags from
  it -- renaming a different tag on the same photo, for one.
* The row kept the file's old mtime and size. Writing keywords changes both, and the
  folder scan only trusts a row whose mtime and size match the file, so every photo
  tagged in bulk was re-read with ExifTool on every scan afterwards.
* TagTuner's bulk writers wrote the files and never told the index at all.

Rows are seeded the way the indexer writes them -- native paths, and raw_metadata in
the shape real rows have, bare aliases and all -- never through the code under test.
ExifTool is replaced by a stand-in that changes the file the way a real write does.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import db as tagpup_db
import tagpup_server
import tuner_server
from index import PhotoIndex
from metadata import extract_tags
from tagpup_server import TagPupHTTPRequestHandler
from tuner_server import TunerHTTPRequestHandler
from tagpup.core.library import Library
from tagpup.services import tagging

KEYWORDS = ["Beach", "Cross Country", "People/Rowan Thackeray"]
HIERARCHICAL = ["People/Rowan Thackeray"]


def raw_metadata_as_scanned(keywords, hierarchical):
    """raw_metadata the way the scan stores it: every keyword field, and the bare
    aliases ExifTool reports beside the grouped names."""
    return {
        "IPTC:Keywords": list(keywords),
        "Keywords": list(keywords),
        "XMP:Subject": list(keywords),
        "Subject": list(keywords),
        "XMP:HierarchicalSubject": list(hierarchical),
        "HierarchicalSubject": list(hierarchical),
    }


def exiftool_that_writes():
    """ExifToolHelper stand-in whose writes change the file the way a real one does:
    new bytes, so a new size, and a new mtime. Returns (helper class, et).

    It reads back what it holds, too: the bulk writers start from the keywords in the
    file. A file it has not written holds what photo() seeds by default."""
    et = MagicMock()
    held = {}

    def touch(path):
        with open(path, "ab") as f:
            f.write(b" keywords rewritten")
        st = os.stat(path)
        os.utime(path, (st.st_atime, st.st_mtime + 120))

    def fields(path):
        scanned = raw_metadata_as_scanned(KEYWORDS, HIERARCHICAL)
        return held.setdefault(path, {k: v for k, v in scanned.items() if ":" in k})

    def set_tags(files, tags=None, params=None):
        for p in files:
            fields(p).update({k: v for k, v in (tags or {}).items() if v not in ([], "")})
            touch(p)

    def execute(*args):
        for arg in args:
            if arg.startswith("-") and arg.endswith("="):
                fields(args[-1]).pop(arg[1:-1], None)
        touch(args[-1])

    et.set_tags.side_effect = set_tags
    et.execute.side_effect = execute
    et.get_tags.side_effect = lambda files, tags=None, params=None: [
        dict(fields(p), SourceFile=p) for p in files]
    helper = MagicMock()
    helper.return_value.__enter__.return_value = et
    helper.return_value.__exit__.return_value = False
    return helper, et


class IndexCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tagpup_tag_writes_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.folder = os.path.join(self.tmp, "Meet Photos")
        os.makedirs(self.folder)
        self.db_path = os.path.join(self.tmp, "tag_writes.db")
        index = PhotoIndex(db_path=self.db_path)
        index.load()
        index.close()

        def forget_state():
            for module, handler in ((tagpup_server, TagPupHTTPRequestHandler),
                                    (tuner_server, TunerHTTPRequestHandler)):
                module.set_active_db_path(self.db_path)
                handler.folder_cache.clear()
                module.set_active_db_path(None)
            tagpup_server.set_active_db_path(self.db_path)
            TagPupHTTPRequestHandler.suggest_status.clear()
            tagpup_server.set_active_db_path(None)
            tagpup_server.invalidate_people_cache()
        self.addCleanup(forget_state)
        tagpup_server.invalidate_people_cache()

    def photo(self, name="IMG_0001.jpg", keywords=KEYWORDS, hierarchical=HIERARCHICAL):
        """A file on disk and its index row, as the indexer leaves them."""
        path = os.path.join(self.folder, name)
        with open(path, "wb") as f:
            f.write(b"not really a jpeg")
        stored = os.path.abspath(path)
        st = os.stat(stored)
        conn = tagpup_db.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata)"
                " VALUES (?, ?, ?, ?, '[]', '[]', ?)",
                (stored, st.st_mtime, st.st_size, json.dumps(list(keywords)),
                 json.dumps(raw_metadata_as_scanned(keywords, hierarchical))))
            conn.commit()
        finally:
            conn.close()
        return stored

    def row(self, stored):
        conn = tagpup_db.connect(self.db_path)
        try:
            found = conn.execute(
                "SELECT tags, raw_metadata, mtime, size FROM photos WHERE path = ?",
                (stored,)).fetchone()
        finally:
            conn.close()
        return {"tags": json.loads(found[0] or "[]"), "raw": json.loads(found[1] or "{}"),
                "mtime": found[2], "size": found[3]}

    def assertRowMatchesTheFile(self, stored):
        row, st = self.row(stored), os.stat(stored)
        self.assertEqual(row["size"], st.st_size, "the row kept the file's old size")
        self.assertAlmostEqual(row["mtime"], st.st_mtime, delta=0.05,
                               msg="the row kept the file's old mtime, so every scan re-reads it")

    def call(self, handler_cls, module, method, body):
        handler = handler_cls.__new__(handler_cls)
        handler.db_path = self.db_path
        sent = {}
        handler.read_json_body = lambda: body
        handler.send_json = lambda data: sent.setdefault("json", data)
        handler.send_json_error = lambda code, message: sent.setdefault("error", (code, message))
        handler.get_exiftool_path = lambda: "exiftool"
        module.set_active_db_path(self.db_path)
        getattr(handler, method)()
        self.assertNotIn("error", sent, sent.get("error"))
        return sent["json"]


class TestARemovedTagStaysRemoved(IndexCase):
    def test_re_deriving_from_raw_metadata_does_not_bring_it_back(self):
        stored = self.photo()
        kept = ["Beach", "People/Rowan Thackeray"]
        tagpup_server.record_tags_in_index(self.db_path, stored, kept, kept, HIERARCHICAL)

        self.assertNotIn("Cross Country", extract_tags(self.row(stored)["raw"]),
                         "raw_metadata still carries the removed tag")

    def test_a_bulk_remove_survives_renaming_another_tag(self):
        stored = self.photo()
        helper, _ = exiftool_that_writes()
        with patch("exiftool_session.ExifToolSession", helper):
            self.call(TagPupHTTPRequestHandler, tagpup_server, "handle_post_photos_bulk_tags",
                      {"paths": [stored], "add_tags": [], "remove_tags": ["Cross Country"]})
            self.assertNotIn("Cross Country", self.row(stored)["tags"])

            # Renaming "Beach" re-derives this photo's tags from its raw_metadata.
            recorded = tagging.replace_tag(
                Library(self.db_path), [stored], "Beach", "Places/Beach", "exiftool").changed

        tags = self.row(stored)["tags"]
        self.assertIn("Places/Beach", tags)
        self.assertNotIn("Cross Country", tags, "the renamed photo got its removed tag back")
        self.assertEqual(recorded, 1)

    def test_clearing_every_tag_leaves_no_keyword_field_behind(self):
        stored = self.photo()
        tagpup_server.record_tags_in_index(self.db_path, stored, [], [], [])
        self.assertEqual(extract_tags(self.row(stored)["raw"]), [])

    def test_the_writer_and_the_recorder_name_the_same_fields(self):
        # One list, two consumers: whatever write_keyword_fields writes is what the
        # index is told, so a field added to one cannot be missing from the other.
        et = MagicMock()
        tagpup_server.write_keyword_fields(et, "photo.jpg", ["Beach"])
        written = set(et.set_tags.call_args.kwargs["tags"])
        cleared = {arg[1:-1] for arg in et.execute.call_args.args
                   if arg.startswith("-") and arg.endswith("=")}
        self.assertEqual(written | cleared, set(tagpup_server.keyword_fields(["Beach"], [])))
        self.assertEqual(cleared, {"XMP:HierarchicalSubject"})


class TestTheRowKeepsTheFilesNewStat(IndexCase):
    def test_bulk_tagging(self):
        stored = self.photo()
        helper, _ = exiftool_that_writes()
        with patch("exiftool_session.ExifToolSession", helper):
            self.call(TagPupHTTPRequestHandler, tagpup_server, "handle_post_photos_bulk_tags",
                      {"paths": [stored], "add_tags": ["Relay"], "remove_tags": []})
        self.assertRowMatchesTheFile(stored)

    def test_renaming_a_tag(self):
        stored = self.photo()
        helper, _ = exiftool_that_writes()
        with patch("exiftool_session.ExifToolSession", helper):
            tagging.replace_tag(
                Library(self.db_path), [stored], "Beach", "Places/Beach", "exiftool")
        self.assertRowMatchesTheFile(stored)


class TestBulkWritersTellTheIndex(IndexCase):
    # These ran against TagTuner's copies of the two routes, which its page never
    # called and which are gone; the same checks now hold TagPup's to them.
    def test_bulk_tags(self):
        stored = self.photo()
        helper, _ = exiftool_that_writes()
        with patch("exiftool_session.ExifToolSession", helper):
            self.call(TagPupHTTPRequestHandler, tagpup_server, "handle_post_photos_bulk_tags",
                      {"paths": [stored], "add_tags": ["Relay"], "remove_tags": ["Cross Country"]})

        row = self.row(stored)
        # A cold folder cache starts from the indexed tags; it used to start from
        # nothing and write only the added tag, erasing the rest.
        self.assertEqual(sorted(row["tags"]), ["Beach", "People/Rowan Thackeray", "Relay"])
        self.assertNotIn("Cross Country", extract_tags(row["raw"]))
        self.assertRowMatchesTheFile(stored)

    def test_save_metadata_records_every_field_and_the_new_stat(self):
        stored = self.photo()
        helper, _ = exiftool_that_writes()
        with patch("exiftool_session.ExifToolSession", helper), \
                patch("metadata.sync_title_to_filename", side_effect=lambda p, t, e: p):
            self.call(TagPupHTTPRequestHandler, tagpup_server, "handle_post_photo_save_metadata",
                      {"path": stored, "title": "", "tags": ["Beach"]})

        row = self.row(stored)
        self.assertEqual(extract_tags(row["raw"]), ["Beach"])
        self.assertRowMatchesTheFile(stored)

    def test_no_tuner_handler_writes_keyword_fields_its_own_way(self):
        # The guard. Each of these built its own ExifTool parameters, and each went
        # wrong in its own way; write_keyword_fields is the one writer.
        with open(os.path.join(WORKSPACE_DIR, "scripts", "tuner_server.py"),
                  encoding="utf-8") as f:
            source = f.read()
        for field in ('["XMP:Subject"]', '["IPTC:Keywords"]', '["XMP:HierarchicalSubject"]'):
            self.assertFalse("params" + field in source,
                             "tuner_server writes %s itself instead of through "
                             "write_keyword_fields" % field)


if __name__ == "__main__":
    unittest.main()
