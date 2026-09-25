"""What both apps serve alike for the gear (docs/ARCHITECTURE.md, phase 7.6): the tag
tree's routes, which the shared tag editor calls from either page, and /api/apps, where
each page learns the other app's address for its library.

The tree's routes were TagPup's alone; TagTuner's page could not edit the tree. The
editor is now one module both pages open, so both apps answer its routes -- one
blueprint, registered by the factory for each, like the picker.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402

import tagpup_web  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web import tagpup_routes, taxonomy_routes, tuner_routes  # noqa: E402

TREE_ROUTES = ("/api/taxonomy/tree", "/api/taxonomy/create", "/api/taxonomy/update",
               "/api/taxonomy/delete-check", "/api/taxonomy/delete-confirm", "/api/taxonomy/rename")


def routes_of(app):
    return {rule.rule: app.view_functions[rule.endpoint] for rule in app.url_map.iter_rules()}


class BothAppsServeTheTree(unittest.TestCase):
    def test_each_app_has_the_trees_routes_from_one_blueprint(self):
        for kind in ("tagpup", "tuner"):
            found = routes_of(web.create_app(kind))
            for route in TREE_ROUTES:
                self.assertIn(route, found, "%s does not serve %s" % (kind, route))
                self.assertEqual(taxonomy_routes.__name__, found[route].__module__,
                                 "%s's %s is not the shared blueprint's" % (kind, route))

    def test_an_edit_made_from_tagtuner_is_the_librarys(self):
        tuner, home = web_client.app_for(self, "tuner")
        tagpup = web.create_app("tagpup", startup=tuner.config["STARTUP_LIBRARY"])
        tagpup.testing = True
        made = tuner.test_client().post("/library/api/taxonomy/create",
                                        json={"name": "Places/Harbour", "parent_id": None, "has_face": 0})
        self.assertEqual(200, made.status_code, made.data)
        self.assertEqual("Places/Harbour", made.get_json()["tag"])
        for app in (tuner, tagpup):
            tags = [node["tag"] for node in app.test_client().get("/library/api/taxonomy/tree").get_json()]
            self.assertIn("Places/Harbour", tags, app.config["APP_KIND"])

    def test_a_rename_from_tagtuner_forgets_tagpups_scans(self):
        # TagPup's cached scans are the process's: a rename made from TagTuner's page
        # rewrote the photos they describe as well.
        tuner, _home = web_client.app_for(self, "tuner")
        library = tuner.config["STARTUP_LIBRARY"]
        client = tuner.test_client()
        node = client.post("/library/api/taxonomy/create",
                           json={"name": "Places/Harbour", "parent_id": None, "has_face": 0}).get_json()
        cache = tagpup_routes.folders.of(library)
        self.addCleanup(tagpup_routes.folders.forget, library)
        cache.put("D:/Library/2020", {"d:/library/2020/a.jpg": {"tags": ["Places/Harbour"]}})

        renamed = client.post("/library/api/taxonomy/rename", json={"tag_id": node["id"], "new_name": "Quay"})

        self.assertEqual(200, renamed.status_code, renamed.data)
        self.assertEqual(0, renamed.get_json()["photos_affected"])
        self.assertIsNone(cache.get("D:/Library/2020"), "TagPup's scan still says what the photos held")

    def test_a_refusal_is_the_shape_both_pages_read(self):
        tuner, _home = web_client.app_for(self, "tuner")
        reply = tuner.test_client().post("/library/api/taxonomy/create", json={"name": "Places|Harbour"})
        self.assertEqual(400, reply.status_code)
        self.assertEqual(False, reply.get_json()["success"])


class RefusedWhileClustering(unittest.TestCase):
    """A rename or a delete of the tree writes faces' names, which cluster-faces rewrites
    while it runs. TagTuner's own writes were refused meanwhile; the tree's routes were
    not, on either port, once both apps served them."""

    def clustering(self, kind):
        """The app, its client, and a node Places/Harbour, with the library's faces
        being clustered."""
        app, _home = web_client.app_for(self, kind)
        library = app.config["STARTUP_LIBRARY"]
        client = app.test_client()
        node = client.post("/library/api/taxonomy/create",
                           json={"name": "Places/Harbour", "parent_id": None, "has_face": 0}).get_json()
        flag = tuner_routes.clustering.of(library)
        flag.set()
        self.addCleanup(tuner_routes.clustering.forget, library)
        self.addCleanup(flag.clear)
        return client, node

    def tags(self, client):
        return [n["tag"] for n in client.get("/library/api/taxonomy/tree").get_json()]

    def test_both_apps_refuse_a_rename_and_a_delete_as_tagtuner_refuses_its_writes(self):
        for kind in ("tagpup", "tuner"):
            with self.subTest(kind):
                client, node = self.clustering(kind)
                renamed = client.post("/library/api/taxonomy/rename",
                                      json={"tag_id": node["id"], "new_name": "Quay"})
                deleted = client.post("/library/api/taxonomy/delete-confirm",
                                      json={"tag_id": node["id"], "action": "remove"})
                for reply in (renamed, deleted):
                    self.assertEqual(409, reply.status_code, reply.data)
                    self.assertEqual({"success": False,
                                      "error": "Server is currently clustering faces. Please try again later."},
                                     reply.get_json())
                self.assertIn("Places/Harbour", self.tags(client), "the refused edit was made anyway")

    def test_the_refusal_is_tagtuners_own(self):
        client, node = self.clustering("tuner")
        own = client.post("/library/api/face/unmatch", json={"face_id": 1})
        tree = client.post("/library/api/taxonomy/rename", json={"tag_id": node["id"], "new_name": "Quay"})
        self.assertEqual((own.status_code, own.get_json()), (tree.status_code, tree.get_json()))

    def test_edits_that_write_no_names_are_not_refused(self):
        client, _node = self.clustering("tagpup")
        made = client.post("/library/api/taxonomy/create",
                           json={"name": "Activity", "parent_id": None, "has_face": 0})
        self.assertEqual(200, made.status_code, made.data)
        self.assertEqual(200, client.get("/library/api/taxonomy/tree").status_code)


class TagTunersRewritesForgetTagPupsScans(unittest.TestCase):
    """TagTuner's tag merge and person rename rewrite photos, as the tree's rename does;
    TagPup's cached scans of the library went on describing them as they were."""

    def cached(self):
        tuner, _home = web_client.app_for(self, "tuner")
        library = tuner.config["STARTUP_LIBRARY"]
        cache = tagpup_routes.folders.of(library)
        self.addCleanup(tagpup_routes.folders.forget, library)
        cache.put("D:/Library/2020", {"d:/library/2020/a.jpg": {"tags": ["Places/Harbour"]}})
        return tuner.test_client(), cache

    def test_a_merge_applied_forgets_them(self):
        client, cache = self.cached()
        reply = client.post("/library/api/tags/merge",
                            json={"from": "Places/Harbour", "into": "Places/Quay", "apply": True})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertIsNone(cache.get("D:/Library/2020"), "TagPup's scan still says what the photos held")

    def test_a_merge_not_applied_leaves_them(self):
        client, cache = self.cached()
        reply = client.post("/library/api/tags/merge", json={"from": "Places/Harbour", "into": "Places/Quay"})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertIsNotNone(cache.get("D:/Library/2020"), "a dry run forgot the scans")

    def test_a_person_rename_forgets_them(self):
        client, cache = self.cached()
        reply = client.post("/library/api/person/rename",
                            json={"old_name": "Hazel Brookmire", "new_name": "Hazel Quillane"})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertIsNone(cache.get("D:/Library/2020"), "TagPup's scan still says what the photos held")

    def test_one_helper_forgets_them(self):
        import inspect
        for module in (taxonomy_routes, tuner_routes):
            source = inspect.getsource(module)
            self.assertNotIn("folders.of(", source, module.__name__)
            self.assertIn("tagpup_routes.forget_scans(", source, module.__name__)


PORTS = tagpup_web.PORTS


def served(testcase, kind, ports=PORTS):
    """An app told the process's ports, as tagpup_web.main makes them."""
    app, _home = web_client.app_for(testcase, kind)
    told = web.create_app(kind, startup=app.config["STARTUP_LIBRARY"], ports=ports)
    told.testing = True
    return told


class WhereEachAppIs(unittest.TestCase):
    def test_both_apps_say_where_each_page_is_for_this_library(self):
        for kind in ("tagpup", "tuner"):
            reply = served(self, kind).test_client().get(
                "/library/api/apps", headers={"Host": "localhost:%d" % PORTS[kind]})
            self.assertEqual(200, reply.status_code, kind)
            self.assertEqual({"this": kind, "apps": {
                "tagpup": "http://localhost:%d/library/" % PORTS["tagpup"],
                "tuner": "http://localhost:%d/library/" % PORTS["tuner"],
            }}, reply.get_json())

    def test_the_ports_are_the_ones_the_process_serves(self):
        moved = served(self, "tagpup", ports={"tagpup": 18090, "tuner": 18080})
        apps = moved.test_client().get("/library/api/apps", headers={"Host": "127.0.0.1:18090"}).get_json()["apps"]
        self.assertEqual("http://127.0.0.1:18080/library/", apps["tuner"])
        self.assertEqual("http://127.0.0.1:18090/library/", apps["tagpup"])

    def test_the_library_is_the_one_the_url_names(self):
        app = served(self, "tuner")
        app.test_client().post("/library/api/databases/create", json={"db_name": "second"})
        apps = app.test_client().get("/second/api/apps").get_json()["apps"]
        self.assertTrue(apps["tagpup"].endswith(":%d/second/" % PORTS["tagpup"]), apps)

    def test_an_ipv6_host_is_bracketed(self):
        apps = served(self, "tuner").test_client().get(
            "/library/api/apps", headers={"Host": "[::1]:8080", "Origin": "http://[::1]:8080"}).get_json()["apps"]
        self.assertEqual("http://[::1]:%d/library/" % PORTS["tagpup"], apps["tagpup"])

    def test_an_app_told_no_ports_names_no_page(self):
        app, _home = web_client.app_for(self, "tuner")
        self.assertEqual({}, app.test_client().get("/library/api/apps").get_json()["apps"])

    def test_the_launcher_tells_both_apps_its_ports(self):
        import inspect
        self.assertIn("ports=ports", inspect.getsource(tagpup_web.main))


class TheSharedStylesheets(unittest.TestCase):
    def test_each_page_is_served_the_gears_and_the_editors_look(self):
        for kind in ("tagpup", "tuner"):
            app, _home = web_client.app_for(self, kind)
            for name in ("gear", "tag-editor"):
                reply = app.test_client().get("/library/common/%s.css" % name)
                self.assertEqual(200, reply.status_code, (kind, name))
                self.assertEqual("text/css", reply.mimetype)
                self.assertIn("no-store", reply.headers["Cache-Control"])
            self.assertEqual(404, app.test_client().get("/library/common/..%2Fstyle.css").status_code)


if __name__ == "__main__":
    unittest.main()
