"""Every route answers this PC only, or is listed here with why it may answer another address (Integration; #2745).

The server listens on this PC's loopback (DECISIONS 2026-09-26); `tagpup_web.py --listen lan`, phase 10's, listens on
every interface, and then what is this PC's alone is refused to any other address by one hook, tagpup.web.security.guard,
over one list, security.THIS_PC_ONLY. The rule used to be written at each place that needed it (a helper called at the
top of ten routes and three blueprints' hooks, each by its own test of the path), so a route added without it served
paths and faces to the network. Now a route is in THIS_PC_ONLY by its path, or in OPEN below with the reason, or it fails
here; and a request from another address reaches no route of THIS_PC_ONLY by any method, OPTIONS and HEAD too, under
any library the URL names.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import web_client  # noqa: E402

from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.web import security  # noqa: E402

ELSEWHERE = "192.168.1.20"
THIS_PC = ("127.0.0.1", "::1", "::ffff:127.0.0.1")
#: The startup library of web_client.app_for, and a second library of the same home: a page switching between them.
LIBRARIES = ("", "/library", "/meadow")

A_LIBRARYS_PAGES = ("one library's own pages and what they ask, answered to whoever the server answers: who may, once "
                    "it listens beyond this PC, is phase 10's logins' to decide (DECISIONS 2026-09-26)")

#: The routes that answer another address, by why. Keyed as Flask names the route.
OPEN = {
    "the page's own code and style, the same for every library: no photo, path or name":
        "/ /index.html /style.css /<name>.js /common/<name>.css /common/<name>.js "
        "/mobile.html /mobile.css /manifest.webmanifest /mobile-icon-192.png /mobile-icon-512.png",
    "where each app listens, its ports: no data of a library": "/api/apps",
    "the version answering and whether it takes work, which a launcher asks before it drains": "/api/server",
    "acts on this PC's own desktop (a folder dialog, the photo viewer, Explorer): answered as the library's pages are":
        "/api/browse-folder /api/photo/open /api/photo/open-explorer",
    A_LIBRARYS_PAGES: (
        "/api/autocomplete-folder /api/databases /api/databases/create /api/face-crop /api/face-matches "
        "/api/face-matches-unmatched /api/face/match /api/face/unmatch /api/faces/exclude /api/faces/excluded "
        "/api/faces/match-bulk /api/faces/restore /api/faces/unmatch-bulk /api/folder/add /api/folder/auto-apply "
        "/api/folder/automatch /api/folder/index-active /api/folder/index-cancel /api/folder/index-start "
        "/api/folder/index-status /api/folder/indexed /api/folder/membership /api/folder/on-disk "
        "/api/folder/remove "
        "/api/folder/rename-photos /api/folder/scan /api/folder/subfolders /api/folder/suggest-cancel "
        "/api/folder/suggest-start /api/folder/suggest-status /api/history "
        "/api/history/<int:change_id>/undo /api/people /api/people-face-samples /api/people-faces "
        "/api/people-with-counts /api/person-faces /api/person/rename /api/photo-details /api/photo-faces "
        "/api/photo-faces/suggestions /api/photo-file /api/photo/automatch /api/photo/delete /api/photo/rotate "
        "/api/photo/smart-rename /api/photo/smart-rename-parts /api/photo/unmatch-all "
        "/api/photos /api/photos/write /api/rules /api/settings /api/sync /api/sync/review "
        "/api/sync/review/ignore /api/sync/review/include /api/tags /api/tags/list /api/tags/merge /api/tags/photos "
        "/api/taxonomy/create /api/taxonomy/delete-check /api/taxonomy/delete-confirm /api/taxonomy/rename "
        "/api/taxonomy/tree /api/taxonomy/update /api/unmatched-faces/build-status /api/unmatched-faces/people "
        "/api/unmatched-faces/person-matches"),
}
OPEN_ROUTES = [route for routes in OPEN.values() for route in routes.split()]


def unclassified(rules):
    """The rules neither this PC's alone nor listed open."""
    return sorted(rule for rule in rules if security.this_pc_only(rule) is None and rule not in OPEN_ROUTES)


def url_of(rule):
    """A path the rule matches: each variable part a 1."""
    return re.sub(r"<[^>]+>", "1", rule)


class Base(unittest.TestCase):
    def apps(self):
        """Both apps on one home holding two libraries, each route's view replaced by one that says it was reached."""
        made = []
        for kind in ("tagpup", "tuner"):
            app, home = web_client.app_for(self, kind)
            library_actions.create(home.library("meadow.db"))
            app.reached = []
            for endpoint in list(app.view_functions):
                app.view_functions[endpoint] = (lambda app=app, endpoint=endpoint, **_: app.reached.append(endpoint) or "reached")
            made.append(app)
        return made

    def rules(self, app):
        return {rule.rule: rule for rule in app.url_map.iter_rules()}

    def ask(self, app, method, path, address, library=""):
        return app.test_client().open(library + path, method=method, environ_base={"REMOTE_ADDR": address})


class EveryRouteIsClassified(Base):
    def test_each_is_this_pcs_alone_or_open_with_its_reason(self):
        for app in self.apps():
            self.assertEqual([], unclassified(self.rules(app)),
                             "add each to tagpup.web.security.THIS_PC_ONLY, or to OPEN here with why another address may ask")

    def test_the_lists_name_routes_there_are_each_once(self):
        found = set()
        for app in self.apps():
            found |= set(self.rules(app))
        self.assertEqual([], sorted(set(OPEN_ROUTES) - found), "open, but no such route")
        self.assertEqual(len(OPEN_ROUTES), len(set(OPEN_ROUTES)), "a route listed open twice")
        self.assertEqual([], [rule for rule in OPEN_ROUTES if security.this_pc_only(rule)], "both open and this PC's")
        self.assertEqual([], [prefix for prefix, _ in security.THIS_PC_ONLY
                              if not any(security.this_pc_only(rule) and rule.startswith(prefix) for rule in found)],
                         "this PC's, but no such route")
        self.assertTrue(all(reason.strip() for reason in OPEN), "every open route has its reason")

    def test_a_route_added_without_a_class_fails_here_and_one_added_under_a_listed_path_is_refused(self):
        app = self.apps()[0]
        app.add_url_rule("/api/added-later", "added_later", lambda: "added")
        app.add_url_rule("/api/library/added-later", "library_added_later", lambda: "added")
        self.assertEqual(["/api/added-later"], unclassified(self.rules(app)))
        reply = self.ask(app, "GET", "/api/library/added-later", ELSEWHERE, "/library")
        self.assertEqual((403, "The library's views answer this PC only"), (reply.status_code, reply.get_json()["error"]))
        self.assertEqual(b"added", self.ask(app, "GET", "/api/library/added-later", "::1", "/library").data)


class AnotherAddress(Base):
    def test_reaches_no_route_of_this_pcs_by_any_method_under_any_library(self):
        for app in self.apps():
            for rule, route in self.rules(app).items():
                sentence = security.this_pc_only(rule)
                if sentence is None:
                    continue
                for library in LIBRARIES:
                    for method in sorted(route.methods):
                        with self.subTest(app=app.name, rule=rule, method=method, library=library):
                            reply = self.ask(app, method, url_of(rule), ELSEWHERE, library)
                            self.assertEqual(403, reply.status_code)
                            if method != "HEAD":
                                self.assertEqual({"success": False, "error": sentence}, reply.get_json())
            self.assertEqual([], app.reached, "a request from another address reached a route")

    def test_is_answered_by_an_open_route(self):
        for app in self.apps():
            for rule in self.rules(app):
                if security.this_pc_only(rule) is None:
                    with self.subTest(app=app.name, rule=rule):
                        self.assertNotEqual(403, self.ask(app, "OPTIONS", url_of(rule), ELSEWHERE, "/library").status_code)


class ThisPc(Base):
    def test_by_each_of_its_addresses_reaches_every_route_of_its_own(self):
        for app in self.apps():
            for rule, route in self.rules(app).items():
                if security.this_pc_only(rule) is None:
                    continue
                method = sorted(route.methods - {"HEAD", "OPTIONS"})[0]
                for address in THIS_PC:
                    with self.subTest(app=app.name, rule=rule, address=address):
                        reached = len(app.reached)
                        reply = self.ask(app, method, url_of(rule), address, "/meadow")
                        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
                        self.assertEqual(reached + 1, len(app.reached))


if __name__ == "__main__":
    unittest.main()
