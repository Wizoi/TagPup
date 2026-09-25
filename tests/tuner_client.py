"""A TagTuner app for a test, over Flask's test client: no server, no port, no sleeps
(docs/ARCHITECTURE.md, phase 5).

The old tests started a socket server per class on a free port and slept a second for
it to bind, or built a handler with no socket and called its methods by name. Both are
one app here, on a library the test made:

    app = tuner_client.app_on(db_path)
    status, body = tuner_client.Requests(app).post("/api/face/match", {...})

A request naming no library goes to the one the app was started on, so the paths are
the ones the page asks for. What the server keeps of a library between requests --
the Identify Faces caches, the clustering flag, the index queue -- outlives the rows a
test wrote, so `forget` drops it between tests.
"""
import os
import sys

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web import tuner_routes  # noqa: E402


def app_on(db_path):
    """The TagTuner app started on the library at `db_path`. In testing mode: a route
    that raises, raises."""
    app = web.create_app("tuner", startup=Library(db_path))
    app.testing = True
    return app


def forget(db_path):
    """Drop what the server keeps of this library: its cached grids and queue, any
    grid's progress, the clustering flag, and the index queue."""
    library = Library(db_path)
    tuner_routes.identify_cache.forget(library)
    tuner_routes.identify_progress.forget(library)
    tuner_routes.clustering.forget(library)
    indexing_jobs.forget(library)


def grid_cache(db_path):
    """The library's Identify Faces cache (tagpup.jobs.identify.GridCache)."""
    return tuner_routes.identify_cache.of(Library(db_path))


def _answer(reply):
    """What a reply said: its JSON, or its text for an error page."""
    return reply.get_json() if reply.is_json else reply.get_data(as_text=True)


class Requests:
    """Requests to an app, answering as the old urllib helpers did."""

    def __init__(self, app):
        self.client = app.test_client()

    def post(self, path, body=None, data=None):
        """(status, the JSON reply or the error text). `data` sends raw bytes instead
        of `body`: a malformed request."""
        if data is not None:
            reply = self.client.post(path, data=data, content_type="application/json")
        else:
            reply = self.client.post(path, json=body if body is not None else {})
        return reply.status_code, _answer(reply)

    def get(self, path):
        """The JSON a GET answers; a refusal fails the test with its status."""
        reply = self.client.get(path)
        assert reply.status_code == 200, "GET %s answered %d: %s" % (path, reply.status_code, _answer(reply))
        return reply.get_json()

    def get_with_status(self, path):
        """Like get(), but keeps the status so a refusal can be asserted on."""
        reply = self.client.get(path)
        return reply.status_code, _answer(reply)

    def get_raw(self, path):
        """(status, content type, bytes): an image, or an error page."""
        reply = self.client.get(path)
        return reply.status_code, reply.headers.get("Content-Type"), reply.data
