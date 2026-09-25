"""The settings dialog's routes, on both apps: the library the URL names, its settings as
the dialog is made from them, and a change refused through the validator or journaled
(tagpup.web.settings_routes; docs/ARCHITECTURE.md, phase 7.6)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402

from tagpup.core import validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings  # noqa: E402
from tagpup.store import db, schema  # noqa: E402


def rows(path):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return dict(conn.execute("SELECT key, value FROM settings").fetchall())
    finally:
        conn.close()


class TheSettingsRoutes(unittest.TestCase):
    def apps(self):
        for kind in ("tagpup", "tuner"):
            app, home = web_client.app_for(self, kind)
            library_actions.create(home.library("meadow.db"))
            yield kind, app.test_client(), home

    def test_they_answer_the_library_in_the_url_with_its_declarations(self):
        for kind, client, home in self.apps():
            with self.subTest(app=kind):
                settings.change(Library(home.library("meadow.db")), {"faces.min_face_size": "40"},
                                acknowledged=["faces"])
                reply = client.get("/meadow/api/settings")
                self.assertEqual(200, reply.status_code, reply.data)
                answer = reply.get_json()
                self.assertEqual(answer["library"], "meadow")
                self.assertTrue(answer["stamped"])
                shown = {s["key"]: s for group in answer["groups"] for s in group["settings"]}
                self.assertEqual(set(shown), set(validation.SETTINGS))
                self.assertEqual(shown["faces.min_face_size"]["value"], "40")
                for key, declared in validation.SETTINGS.items():
                    for field in ("label", "type", "default", "info", "locked", "consequences"):
                        self.assertEqual(shown[key][field], declared[field], (key, field))
                    self.assertEqual(shown[key]["kind"], validation.setting_kind(key))
                self.assertEqual(client.get("/library/api/settings").get_json()["groups"][1]["settings"][0]["value"],
                                 settings.DEFAULTS["faces.min_face_size"], "another library's value")
                groups = {g["name"]: g for g in answer["groups"]}
                self.assertTrue(groups["clip"]["locked"] and groups["faces"]["locked"] and groups["exiftool"]["locked"])
                self.assertFalse(groups["suggest"]["locked"] or groups["renaming"]["locked"])

    def test_a_change_is_journaled_on_that_library_alone(self):
        for kind, client, home in self.apps():
            with self.subTest(app=kind):
                reply = client.post("/meadow/api/settings", json={"values": {"renaming.format": "{index} {grouping}",
                                                                           "model.name": "ViT-B-32"},
                                                                "acknowledged": ["clip"]})
                self.assertEqual(200, reply.status_code, reply.data)
                answer = reply.get_json()
                self.assertEqual((answer["success"], answer["changed"], answer["locked"]), (True, 2, True))
                meadow = Library(home.library("meadow.db"))
                self.assertEqual(rows(meadow.path)["model.name"], "ViT-B-32")
                self.assertEqual(rows(home.library("library.db")), settings.DEFAULTS)
                newest = journal_service.history(meadow)["changes"][0]
                self.assertEqual((newest["id"], newest["operation"]), (answer["change"], settings.CHANGE))

    def test_a_refusal_is_the_validators_and_writes_nothing(self):
        for kind, client, home in self.apps():
            with self.subTest(app=kind):
                reply = client.post("/meadow/api/settings", json={"values": {"faces.confidence_threshold": "2"}})
                self.assertEqual(400, reply.status_code)
                self.assertEqual(reply.get_json(), {"success": False, "error": "The confidence must be between 0 and 1."})
                self.assertEqual(400, client.post("/meadow/api/settings", json={"faces": 1}).status_code)
                self.assertEqual(400, client.post("/meadow/api/settings",
                                                  json={"values": {"paths.data_dir": "x"}}).status_code)
                self.assertEqual(rows(home.library("meadow.db")), settings.DEFAULTS)
                self.assertEqual(1, len(journal_service.history(Library(home.library("meadow.db")))["changes"]))

    def test_a_locked_change_is_refused_unless_the_body_acknowledges_its_group(self):
        """The lock was the page's alone: POST /api/settings took any values. Found in
        review of f127e47."""
        for kind, client, home in self.apps():
            with self.subTest(app=kind):
                reply = client.post("/meadow/api/settings", json={"values": {"model.name": "ViT-B-32"}})
                self.assertEqual(400, reply.status_code)
                error = reply.get_json()["error"]
                self.assertIn(validation.SETTING_GROUPS["clip"]["title"], error)
                self.assertIn(validation.SETTING_GROUPS["clip"]["consequences"][0], error)
                self.assertEqual(rows(home.library("meadow.db")), settings.DEFAULTS)
                self.assertEqual(400, client.post("/meadow/api/settings", json={
                    "values": {"model.name": "ViT-B-32"}, "acknowledged": "clip, faces"}).status_code)
                self.assertEqual(1, len(journal_service.history(Library(home.library("meadow.db")))["changes"]))
                reply = client.post("/meadow/api/settings", json={"values": {"model.name": "ViT-B-32"},
                                                                   "acknowledged": ["clip"]})
                self.assertEqual(200, reply.status_code, reply.data)
                self.assertEqual(rows(home.library("meadow.db"))["model.name"], "ViT-B-32")

    def test_after_a_change_the_runtime_lets_go_of_what_the_library_no_longer_uses(self):
        from unittest import mock
        runtime = mock.Mock()
        app, home = web_client.app_for(self, "tuner", runtime=runtime)
        client = app.test_client()
        reply = client.post("/library/api/settings", json={"values": {"model.name": "ViT-B-32"},
                                                            "acknowledged": ["clip"]})
        self.assertEqual(200, reply.status_code, reply.data)
        runtime.settings_changed.assert_called_once_with(Library(home.library("library.db")))
        runtime.settings_changed.reset_mock()
        client.post("/library/api/settings", json={"values": {"model.name": "ViT-B-32"}, "acknowledged": ["clip"]})
        runtime.settings_changed.assert_not_called()

    def test_a_library_in_use_is_stamped_from_config_ini_when_its_settings_are_asked(self):
        for kind, client, home in self.apps():
            with self.subTest(app=kind):
                schema.ensure(home.library("quarry.db"))
                home.write_old_config({"candidates": {"tags": "Kayak, Lighthouse"}})
                shown = client.get("/quarry/api/settings").get_json()
                words = [s for g in shown["groups"] for s in g["settings"] if s["key"] == "candidates.tags"][0]
                self.assertEqual(words["value"], "Kayak, Lighthouse")
                self.assertEqual(rows(home.library("quarry.db"))["candidates.tags"], "Kayak, Lighthouse")

    def test_the_file_routes_use_the_librarys_exiftool(self):
        from unittest import mock
        from tagpup.web import state
        for kind, client, home in self.apps():
            with self.subTest(app=kind):
                named = os.path.join(home.root, "exiftool-own.exe")
                open(named, "w").close()
                settings.change(Library(home.library("meadow.db")), {"paths.exiftool": named},
                                acknowledged=["exiftool"])
                app = client.application
                with app.test_request_context():
                    self.assertEqual(state.exiftool(Library(home.library("meadow.db"))), named)
                    with mock.patch("tagpup.config.default_exiftool", return_value=named + ".missing"), \
                            mock.patch("shutil.which", return_value=None):
                        self.assertEqual(state.exiftool(Library(home.library("library.db"))), named + ".missing")


if __name__ == "__main__":
    unittest.main()
