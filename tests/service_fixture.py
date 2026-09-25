"""The fixture every service test uses: a temporary library with a photos folder.

The schema is made the way the app makes it (PhotoIndex, until the store owns the
schema), never written out by hand: a test against a hand-made table agrees with the
test, not with the library. No server is imported; a service is called directly.
"""
import json
import os
import shutil
import sys
import tempfile
import time

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from face_rows import add_face  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.store import db  # noqa: E402


class TempLibrary:
    """A library and a folder of photos in a temporary folder, removed afterwards."""

    def __init__(self, testcase):
        self.root = tempfile.mkdtemp(prefix="tagpup_service_")
        self.photos = os.path.join(self.root, "Photos")
        os.makedirs(self.photos)
        self.library = Library(os.path.join(self.root, "library.db"))
        index = PhotoIndex(db_path=self.library.path)
        index.load()
        index.close()
        testcase.addCleanup(self.remove)

    def photo(self, name, content=b"not really a photo"):
        """A file in the photos folder. Returns its path as the index stores it."""
        path = os.path.abspath(os.path.join(self.photos, name))
        with open(path, "wb") as handle:
            handle.write(content)
        return path

    def add_row(self, path, mtime=1.0, size=1, tags=()):
        self.execute("INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)"
                     " VALUES (?, ?, ?, ?, '[]', '{}')", (path, mtime, size, json.dumps(list(tags))))

    def add_face(self, path, box, name=None, crop=b"crop"):
        """A face in the photo at `path`, which gets a row holding only the path if it
        has none. Returns the face's id."""
        conn = db.connect(self.library.path)
        try:
            face_id = add_face(conn, path, box, name=name)
            if crop is not None:
                conn.execute("INSERT INTO face_crops (face_id, jpeg) VALUES (?, ?)", (face_id, crop))
            conn.commit()
        finally:
            conn.close()
        return face_id

    def execute(self, sql, params=()):
        conn = db.connect(self.library.path)
        try:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def rows(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def remove(self):
        # Windows can hold a file a moment after the last connection lets go of it.
        for _attempt in range(20):
            shutil.rmtree(self.root, ignore_errors=True)
            if not os.path.exists(self.root):
                return
            time.sleep(0.25)
        raise AssertionError("Could not remove %s" % self.root)
