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
from index import PhotoIndex  # noqa: E402
from tagpup_server import TagPupHTTPRequestHandler, set_active_db_path, get_active_db_path  # noqa: E402

from tagpup.core.library import Library as PathLibrary  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402


class Library:
    """A scratch library: a folder of photos and a database, removed afterwards.

    The tables are made the way the app makes them (PhotoIndex). This harness wrote its
    own, and its tag tree lacked a column every library has, so code reading that
    column failed here and nowhere else.
    """

    def __init__(self, testcase, name="library"):
        self.root = tempfile.mkdtemp(prefix="tagpup_harness_")
        self.photos = os.path.join(self.root, "Photos")
        os.makedirs(self.photos)
        self.db_path = os.path.join(self.root, name + ".db")
        index = PhotoIndex(db_path=self.db_path)
        index.load()
        index.close()
        tagpup_server.invalidate_people_cache()
        set_active_db_path(self.db_path)
        self.registry_key = get_active_db_path()
        set_active_db_path(None)
        testcase.addCleanup(self.close)

    def suggestion_runs(self):
        """This library's suggestion runs (tagpup.jobs.suggestions)."""
        return suggestion_jobs.runs_for(PathLibrary(self.db_path))

    def save_suggestions(self, found):
        """Keep {photo: entry} as what Suggest offered each photo, in this library
        (tagpup.store.suggestions) -- where the page's Apply All reads it."""
        from tagpup.store import suggestions as saved

        conn = tagpup_db.connect(self.db_path)
        try:
            for photo, entry in found.items():
                saved.put(conn, photo, entry)
            conn.commit()
        finally:
            conn.close()

    def close(self):
        tagpup_server.invalidate_people_cache()
        TagPupHTTPRequestHandler._db_folder_cache_registry.pop(self.registry_key, None)
        suggestion_jobs.forget(PathLibrary(self.db_path))
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
