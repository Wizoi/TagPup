"""A library named in the URL that does not exist is refused, not created.

Both apps take the library from the first part of the path -- /kr-track/api/tags --
and pointed the request at data/<name>.db whether or not it existed. The first
handler to open it created it: a typo in the address bar made an empty library,
which then sat in the library list. Making one is what Create is for.
"""
import os
import sys
import unittest
import uuid

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import web_client  # noqa: E402
from taxonomy import TagTaxonomy  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web import state  # noqa: E402


def which_library(app):
    """A route of the test's own, so a test can ask any app which library a request
    was served from."""
    @app.get("/api/which-library")
    def which():
        return {"path": state.require().path}
    return app.test_client()


class AMistypedLibraryIsNotCreated(unittest.TestCase):
    def setUp(self):
        self.name = "no_such_library_" + uuid.uuid4().hex[:8]

    def check(self, kind):
        app, home = web_client.app_for(self, kind)
        created = home.library(self.name + ".db")
        reply = app.test_client().get("/%s/api/tags" % self.name)
        self.assertEqual(404, reply.status_code, "the request went ahead against a library that does not exist")
        self.assertFalse(os.path.exists(created), "a library was created by a typo")

    def test_tagpup_refuses_it(self):
        self.check("tagpup")

    def test_tagtuner_refuses_it(self):
        self.check("tuner")

    def test_asking_who_the_people_roots_are_creates_nothing(self):
        home = own_home.for_test(self, "tagpup_mistyped_")
        created = home.library(self.name + ".db")
        roots = TagTaxonomy(db_path=created).people_roots()
        self.assertIn("people", roots)
        self.assertFalse(os.path.exists(created))

    def test_a_library_that_exists_is_routed_to(self):
        # It relied on the checkout's data/ holding the startup library, and skipped
        # when it did not. Here the startup library and another both exist in the home.
        app, home = web_client.app_for(self, "tagpup", startup="harbour.db")
        client = which_library(app)
        for name in ("harbour", "regatta"):
            open(home.library(name + ".db"), "wb").close()
            reply = client.get("/%s/api/which-library" % name)
            self.assertEqual(200, reply.status_code, name)
            self.assertEqual(Library(reply.get_json()["path"]), Library(home.library(name + ".db")))


class TheStartupLibraryIsTheFileTheServerStartedOn(unittest.TestCase):
    """A request with no library in its URL, or naming the startup library, went to
    data/<its name>.db rather than to the file the server was started on.

    They are the same file when the library is in data/, as the launchers arrange. A
    server started on a library anywhere else served -- and created -- an empty namesake
    in data/ instead. Found when tests moved their libraries out of the checkout.
    """

    def setUp(self):
        self.home = own_home.for_test(self, "tagpup_routing_")
        self.library = os.path.join(self.home.root, "elsewhere", "harbour.db")
        os.makedirs(os.path.dirname(self.library))
        open(self.library, "wb").close()

    def check(self, url):
        for kind in ("tagpup", "tuner"):
            app = web.create_app(kind, startup=Library(self.library))
            app.testing = True
            reply = which_library(app).get(url)
            self.assertEqual(200, reply.status_code, kind)
            self.assertEqual(Library(reply.get_json()["path"]), Library(self.library),
                             "%s served %s" % (kind, reply.get_json()["path"]))
        self.assertFalse(os.path.exists(self.home.library("harbour.db")),
                         "an empty namesake was made in data/")

    def test_with_no_library_in_the_url(self):
        self.check("/api/which-library")

    def test_naming_the_startup_library(self):
        self.check("/harbour/api/which-library")


if __name__ == "__main__":
    unittest.main()
