"""Call a TagPup server handler directly, against a scratch library.

No server, no port: a handler instance is made without a socket and given a request
body, and what it would have sent is kept. Several test modules run at once in this
repository, and a fixed port is something they would fight over.
"""
import os
import shutil
import sys
import tempfile
import time

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import db as tagpup_db  # noqa: E402
import tagpup_server  # noqa: E402
from tagpup_server import TagPupHTTPRequestHandler, set_active_db_path, get_active_db_path  # noqa: E402

SCHEMA = (
    """CREATE TABLE photos (
        path TEXT PRIMARY KEY, mtime REAL, size INTEGER, tags TEXT, people TEXT,
        captions TEXT, raw_metadata TEXT, embedding BLOB, document_id TEXT)""",
    """CREATE TABLE faces (
        id INTEGER PRIMARY KEY AUTOINCREMENT, photo_path TEXT, box TEXT, embedding BLOB,
        name TEXT, crop_image BLOB, prob REAL, name_source TEXT,
        excluded INTEGER DEFAULT 0, excluded_reason TEXT)""",
    """CREATE TABLE tag_taxonomy (
        id INTEGER PRIMARY KEY, tag TEXT, name TEXT, parent_id INTEGER, has_face INTEGER)""",
    """CREATE TABLE embedding_cache (
        path TEXT PRIMARY KEY, mtime REAL, size INTEGER, model_name TEXT, pretrained TEXT,
        preserve_full_frame INTEGER, max_aspect_ratio REAL, force_image_size INTEGER,
        embedding BLOB)""",
)


class Library:
    """A scratch library: a folder of photos and a database, removed afterwards."""

    def __init__(self, testcase, name="library"):
        self.root = tempfile.mkdtemp(prefix="tagpup_harness_")
        self.photos = os.path.join(self.root, "Photos")
        os.makedirs(self.photos)
        self.db_path = os.path.join(self.root, name + ".db")
        conn = tagpup_db.connect(self.db_path)
        for statement in SCHEMA:
            conn.execute(statement)
        conn.commit()
        conn.close()
        tagpup_server.invalidate_people_cache()
        set_active_db_path(self.db_path)
        self.registry_key = get_active_db_path()
        set_active_db_path(None)
        testcase.addCleanup(self.close)

    def close(self):
        tagpup_server.invalidate_people_cache()
        for registry in (TagPupHTTPRequestHandler._db_folder_cache_registry,
                         TagPupHTTPRequestHandler._db_suggest_status_registry,
                         TagPupHTTPRequestHandler._db_suggest_threads_registry):
            registry.pop(self.registry_key, None)
        getattr(TagPupHTTPRequestHandler, "_suggestions_loaded", set()).discard(self.registry_key)
        set_active_db_path(None)
        # Windows can hold a file a moment after ExifTool has let go of it.
        for _attempt in range(20):
            shutil.rmtree(self.root, ignore_errors=True)
            if not os.path.exists(self.root):
                return
            time.sleep(0.25)
        raise AssertionError("Could not remove %s" % self.root)

    def rows(self, sql, params=()):
        conn = tagpup_db.connect(self.db_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def execute(self, sql, params=()):
        conn = tagpup_db.connect(self.db_path)
        try:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def handler(self, exiftool):
        return FakeHandler(self.db_path, exiftool)


class FakeHandler(TagPupHTTPRequestHandler):
    """A handler with no socket: the body is given, the reply is kept."""

    def __init__(self, db_path, exiftool):  # noqa: D107 -- no socket, on purpose
        self.db_path = db_path
        self._exiftool = exiftool
        self.body = None
        self.reply = None
        self.status = None
        set_active_db_path(db_path)

    def get_exiftool_path(self):
        return self._exiftool

    def read_json_body(self):
        return self.body

    def send_json(self, data):
        self.status, self.reply = 200, data

    def send_json_error(self, status, message):
        self.status, self.reply = status, {"error": message}

    def call(self, method, body=None, *args):
        """Run a handler method; returns (status, reply)."""
        self.body = body
        set_active_db_path(self.db_path)
        getattr(self, method)(*args)
        return self.status, self.reply
