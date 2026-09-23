"""repair_bare_person_tags.py tells the index everything it wrote, not just the tags.

It rewrote each photo's keywords and then set only the tags column, so the row's
raw_metadata still held the bare name -- which the next re-derivation from
raw_metadata brings back -- and its mtime and size no longer matched the file, so
every scan re-read the photo.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db  # noqa: E402
import exiftool_session  # noqa: E402
import repair_bare_person_tags  # noqa: E402
from index import PhotoIndex  # noqa: E402


class FakeSession:
    """Reads keywords from a dict, and 'writes' by appending to the file."""
    subjects = {}

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_tags(self, files, tags=None):
        return [{"SourceFile": f, "XMP:Subject": list(self.subjects.get(f, []))} for f in files]

    def set_tags(self, files, tags=None, params=None):
        for f in files:
            self.subjects[f] = list(tags.get("XMP:Subject", []))
            with open(f, "ab") as handle:
                handle.write(b"written")

    def execute(self, *args):
        with open(args[-1], "ab") as handle:
            handle.write(b"cleared")


class RepairTellsTheIndex(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="repair_index_")
        self.photo = os.path.join(self.dir, "Meet - 01.jpg")
        with open(self.photo, "wb") as handle:
            handle.write(b"jpeg")
        os.utime(self.photo, (1_000_000, 1_000_000))
        self.db = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db)
        index.load()
        raw = {"XMP:Subject": ["Rowan Thackeray", "Activity/Running"],
               "IPTC:Keywords": ["Rowan Thackeray", "Activity/Running"]}
        index.conn.execute(
            "INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata)"
            " VALUES (?, ?, ?, ?, '[]', '[]', ?)",
            (self.photo, 1_000_000.0, 4, json.dumps(["Rowan Thackeray", "Activity/Running"]),
             json.dumps(raw)))
        index.conn.commit()
        index.close()
        FakeSession.subjects = {self.photo: ["Rowan Thackeray", "Activity/Running"]}

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_raw_metadata_and_stat_follow_the_repair(self):
        changes = [{"path": self.photo}]
        person_paths = {"rowan thackeray": "People/Rowan Thackeray"}
        with mock.patch.object(exiftool_session, "ExifToolSession", FakeSession):
            written, _missing, _unchanged, failed = repair_bare_person_tags.apply_changes(
                self.db, changes, person_paths, {"people"})
        self.assertEqual((written, failed), (1, []))

        conn = db.connect(db.readonly_uri(self.db), uri=True)
        tags, raw_json, mtime, size = conn.execute(
            "SELECT tags, raw_metadata, mtime, size FROM photos").fetchone()
        conn.close()
        raw = json.loads(raw_json)
        self.assertIn("People/Rowan Thackeray", json.loads(tags))
        for field in ("XMP:Subject", "IPTC:Keywords"):
            self.assertNotIn("Rowan Thackeray", raw.get(field, []), field)
        stat = os.stat(self.photo)
        self.assertEqual((mtime, size), (stat.st_mtime, stat.st_size))


if __name__ == "__main__":
    unittest.main()
