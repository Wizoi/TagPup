"""Renaming a person in TagTuner reaches the photo files, and says how many it rewrote.

Renaming someone to a name that already had a tag -- merging two spellings of one
person -- renamed the faces and the index's people lists, then skipped the tree and the
files ("target already exists; leave the tree alone"). The files kept the old path, and
the next scan of those folders brought the old name straight back. And whatever the
files did, the reply was a bare "success".
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.test_taxonomy_lifecycle import EXIFTOOL, requires_exiftool

import own_home  # noqa: E402
import tuner_client  # noqa: E402
from exiftool_session import ExifToolSession  # noqa: E402
from index import PhotoIndex  # noqa: E402

from tagpup.store import db, people  # noqa: E402

OLD, NEW = "Rowan Thackeray", "Rowan Thackeray-Vale"


@requires_exiftool
class PersonRenameReachesTheFiles(unittest.TestCase):
    def setUp(self):
        # The route finds ExifTool through the settings, so the test has settings of
        # its own, never the checkout's.
        own_home.for_test(self)
        self.dir = tempfile.mkdtemp(prefix="person_rename_")
        self.db_path = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db_path)
        index.load()
        index.close()
        self.execute("INSERT INTO tag_taxonomy (id, tag, name, parent_id, has_face) VALUES"
                     " (1, 'People', 'People', NULL, 1),"
                     " (2, 'People/%s', '%s', 1, 1)" % (OLD, OLD))
        self.requests = tuner_client.Requests(tuner_client.app_on(self.db_path))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def execute(self, sql, params=()):
        conn = db.connect(self.db_path)
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def rows(self, sql, params=()):
        conn = db.connect(self.db_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def add_photo(self, name, writable=True):
        path = os.path.join(self.dir, name)
        tags = ["People/" + OLD]
        if writable:
            from PIL import Image

            Image.new("RGB", (8, 8)).save(path)
            with ExifToolSession(executable=EXIFTOOL) as et:
                et.set_tags([path], tags={"XMP:HierarchicalSubject": tags, "XMP:Subject": [OLD]},
                            params=["-overwrite_original"])
        else:
            with open(path, "wb") as handle:
                handle.write(b"not a picture")
        self.execute("INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)"
                     " VALUES (?, 1.0, 1, ?, '[]', '{}')", (path, json.dumps(tags)))
        self.execute("INSERT INTO faces (photo_id, box, name, name_source) VALUES ((SELECT id FROM photos WHERE path = ?), '[0,0,1,1]', ?, 'manual')",
                     (path, OLD))
        # Its people as the store keeps them: the keyword and the face both name OLD.
        conn = db.connect(self.db_path)
        try:
            people.rebuild(conn)
            conn.commit()
        finally:
            conn.close()
        return path

    def hierarchical(self, path):
        with ExifToolSession(executable=EXIFTOOL) as et:
            found = et.get_tags([path], tags=["XMP:HierarchicalSubject"])[0]
        value = found.get("XMP:HierarchicalSubject", [])
        return value if isinstance(value, list) else [value]

    def rename(self):
        status, reply = self.requests.post("/api/person/rename", {"old_name": OLD, "new_name": NEW})
        self.assertEqual(200, status, reply)
        return reply

    def test_renaming_into_an_existing_person_rewrites_the_files(self):
        self.execute("INSERT INTO tag_taxonomy (id, tag, name, parent_id, has_face)"
                     " VALUES (3, 'People/%s', '%s', 1, 1)" % (NEW, NEW))
        photo = self.add_photo("regatta.jpg")

        reply = self.rename()

        self.assertTrue(reply["success"], reply)
        written = self.hierarchical(photo)
        self.assertIn("People/" + NEW, written)
        self.assertNotIn("People/" + OLD, written, "the file still names the old person")
        tree = {r[0] for r in self.rows("SELECT tag FROM tag_taxonomy")}
        self.assertNotIn("People/" + OLD, tree)

    def test_it_reports_the_photos_it_could_not_rewrite(self):
        self.add_photo("regatta.jpg")
        self.add_photo("broken.jpg", writable=False)

        reply = self.rename()

        self.assertEqual(2, reply["photos_affected"], reply)
        self.assertEqual(1, reply["photos_rewritten"], reply)
        self.assertIn("warning", reply)


if __name__ == "__main__":
    unittest.main()
