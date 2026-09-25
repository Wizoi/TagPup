"""A TagPup or TagTuner app for a test: a home of its own, a library made in it, and
Flask's test client -- no server, no port, no sleeps (docs/ARCHITECTURE.md, phase 5).

    app, home = web_client.app_for(self, "tagpup")
    client = app.test_client()
    client.get("/library/api/databases")

The library is `home.library("library.db")` unless `startup` names another; with
`startup=None` the app starts on none, as it will once #100 lands.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.web import app as web  # noqa: E402


def app_for(testcase, kind, startup="library.db", pages=None, runtime=None):
    """(the app, its home). The app is in testing mode: a route that raises, raises.
    `runtime` is the process's models the app is given (tagpup.runtime), a fake's."""
    home = own_home.for_test(testcase)
    library = None
    if startup:
        db_path = home.library(startup)
        library_actions.create(db_path)
        library = Library(db_path)
    app = web.create_app(kind, startup=library, pages=pages, runtime=runtime)
    app.testing = True
    return app, home
