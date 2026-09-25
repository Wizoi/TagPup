"""The History dialog's routes, on both apps: the library's recent changes, and undoing
one -- rehearsed, then made (tagpup.web.history_routes; docs/findings.md, #266).

Undo existed only in the CLI and the MCP server; the pages had no way to it. The
changes here are made the way the app makes them: a settings change through the
settings service, and a change of photo files through the tagging service with the real
ExifTool, on a JPEG made here whose row is as the indexer makes it.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import web_client  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import settings, tagging  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()


class HistoryCase(unittest.TestCase):
    def apps(self):
        for kind in ("tagpup", "tuner"):
            app, home = web_client.app_for(self, kind)
            yield kind, app.test_client(), home, Library(home.library("library.db"))

    def listed(self, client):
        reply = client.get("/library/api/history")
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()

    def undo(self, client, change, apply):
        reply = client.post("/library/api/history/%d/undo" % change, json={"apply": apply})
        return reply.status_code, reply.get_json()


class TheHistoryRoutes(HistoryCase):
    def test_list_the_changes_newest_first_and_which_can_be_undone(self):
        for kind, client, _home, library in self.apps():
            with self.subTest(app=kind):
                settings.of(library)   # stamped, as the library's first read does
                changed = settings.change(library, {"candidates.tags": "harbour, regatta"})
                change = changed.details["change"]
                answer = self.listed(client)
                self.assertEqual("library", answer["library"])
                self.assertEqual(change, answer["changes"][0]["id"])
                newest = answer["changes"][0]
                self.assertEqual(("change settings", "applied", True),
                                 (newest["operation"], newest["status"], newest["undoable"]))
                self.assertEqual({"settings": {"update": 1}}, {t: a for t, a in newest["rows"].items()})
                stamps = [c for c in answer["changes"] if c["operation"] in settings.STAMPS]
                self.assertTrue(stamps and not any(c["undoable"] for c in stamps), answer["changes"])
                self.assertNotIn("values", json.dumps(answer))

    def test_an_undo_is_rehearsed_then_made(self):
        for kind, client, _home, library in self.apps():
            with self.subTest(app=kind):
                before = settings.of(library)["candidates.tags"]
                change = settings.change(library, {"candidates.tags": "harbour, regatta"}).details["change"]
                status, rehearsed = self.undo(client, change, apply=False)
                self.assertEqual(200, status, rehearsed)
                self.assertEqual((True, True, 0), (rehearsed["success"], rehearsed["dry_run"], rehearsed["changed"]))
                self.assertEqual("applied", self.listed(client)["changes"][0]["status"])
                status, undone = self.undo(client, change, apply=True)
                self.assertEqual(200, status, undone)
                self.assertEqual((True, False, 1), (undone["success"], undone["dry_run"], undone["changed"]))
                listed = {c["id"]: c for c in self.listed(client)["changes"]}
                self.assertEqual(("undone", False), (listed[change]["status"], listed[change]["undoable"]))
                self.assertEqual(before, settings.of(library)["candidates.tags"])
                # Undone already: refused, and nothing written.
                status, again = self.undo(client, change, apply=True)
                self.assertEqual(400, status, again)
                self.assertFalse(again["success"])
                self.assertTrue(again["refused"])

    def test_a_change_there_is_not_is_refused(self):
        for kind, client, _home, _library in self.apps():
            with self.subTest(app=kind):
                status, answer = self.undo(client, 999, apply=True)
                self.assertIn(status, (400, 404), answer)
                self.assertFalse(answer["success"])

    def test_a_limit_that_is_no_number_is_refused(self):
        for kind, client, _home, _library in self.apps():
            with self.subTest(app=kind):
                self.assertEqual(400, client.get("/library/api/history?limit=many").status_code)


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class UndoingAChangeOfPhotoFiles(HistoryCase):
    def test_puts_the_file_back_and_lets_the_folder_scans_go(self):
        from PIL import Image

        for kind, client, home, library in self.apps():
            with self.subTest(app=kind):
                folder = os.path.join(home.root, "Harbour Day " + kind)
                os.makedirs(folder)
                photo = os.path.join(folder, "a.jpg")
                Image.new("RGB", (16, 12), (90, 110, 130)).save(photo, "JPEG")
                with ExifToolSession(executable=EXIFTOOL) as et:
                    et.set_tags([photo], tags={"XMP:Subject": ["Beach"], "IPTC:Keywords": ["Beach"]},
                                params=["-overwrite_original"])
                conn = db.connect(library.path)
                try:
                    photo_rows.add_read(conn, photo, {"XMP:Subject": ["Beach"], "IPTC:Keywords": ["Beach"]})
                    conn.commit()
                finally:
                    conn.close()
                change = tagging.change_tags(library, [photo], ["Regatta"], [], EXIFTOOL).details["change"]
                newest = self.listed(client)["changes"][0]
                self.assertEqual((change, "add to all selected", {"done": 1}, True),
                                 (newest["id"], newest["operation"], newest["files"], newest["undoable"]))
                tagpup_routes.folders.of(library).put(folder, {"a": {"path": photo}})
                status, undone = self.undo(client, change, apply=True)
                self.assertEqual(200, status, undone)
                self.assertEqual((True, 1, []), (undone["success"], undone["changed"], undone["errors"]))
                with ExifToolSession(executable=EXIFTOOL) as et:
                    held = et.get_tags([photo], tags=["XMP:Subject"])[0].get("XMP:Subject")
                self.assertEqual("Beach", held)
                self.assertFalse(tagpup_routes.folders.of(library).get(folder), "the folder's scan was kept")


if __name__ == "__main__":
    unittest.main()
