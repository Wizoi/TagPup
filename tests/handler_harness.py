"""A scratch library, and the TagPup app serving it through Flask's test client.

No server, no port, no sleeps: the app is made for the library and asked through the
client, and what it answered is returned (docs/ARCHITECTURE.md, phase 5). Several test
modules run at once in this repository, and a fixed port is something they would fight
over. This drove the old server's handler methods without a socket; the routes are
Flask views now, and a test asks them by URL.
"""
import os
import sys

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402

from tagpup.core.library import Library as PathLibrary  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.store import db as tagpup_db  # noqa: E402
from tagpup.store import schema  # noqa: E402
from tagpup.store import taxonomy as store_taxonomy  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402


class Library:
    """A scratch library: a folder of photos and a database in a home of the test's
    own, and the TagPup app started on it. Removed afterwards.

    The tables are made the way the app makes them (tagpup.store.schema, which a
    request opening a library runs), and nothing else: this harness wrote its own, and
    its tag tree lacked a column every library has, so code reading that column failed
    here and nowhere else. The tree is not seeded either: tests file their own people,
    with the ids they choose.
    """

    def __init__(self, testcase, name="library"):
        self.home = own_home.for_test(testcase, "tagpup_harness_")
        self.root = self.home.root
        self.photos = os.path.join(self.root, "Photos")
        os.makedirs(self.photos)
        self.db_path = self.home.library(name + ".db")
        schema.ensure(self.db_path)
        self.library = PathLibrary(self.db_path)
        self.app = web.create_app("tagpup", startup=self.library)
        self.app.testing = True
        self.client = self.app.test_client()
        store_taxonomy.forget_people_paths()
        testcase.addCleanup(self.close)

    # ---- Asking the app ------------------------------------------------------------------

    def get(self, path, query=None):
        """GET a route of this library's app; returns (status, the JSON it answered)."""
        reply = self.client.get(path, query_string=query)
        return reply.status_code, reply.get_json()

    def post(self, path, body):
        """POST a JSON body to a route; returns (status, the JSON it answered)."""
        reply = self.client.post(path, json=body)
        return reply.status_code, reply.get_json()

    # ---- What the server keeps for it --------------------------------------------------------

    def folders(self):
        """This library's folder cache (tagpup.web.tagpup_routes.folders)."""
        return tagpup_routes.folders.of(self.library)

    def suggestion_runs(self):
        """This library's suggestion runs (tagpup.jobs.suggestions)."""
        return suggestion_jobs.runs_for(self.library)

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
        """Forget what the process kept for this library, and let the home go."""
        store_taxonomy.forget_people_paths()
        tagpup_routes.folders.forget(self.library)
        suggestion_jobs.forget(self.library)
        indexing_jobs.forget(self.library)
        self.home.close()

    # ---- The rows ------------------------------------------------------------------------

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
