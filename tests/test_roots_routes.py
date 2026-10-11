"""TagTuner's Roots routes, and what a library whose root this machine does not place is told
(tagpup.web.tuner_routes, tagpup.web.roots_gate; docs/ARCHITECTURE.md, "Roots and machines").

Flask's test client: no port, no thread of the test's own, no sleep. The library is made as the roots
tests make it and adopted by the explicit command; the places are real folders. What the owner is
told, in a sentence and not a traceback, is asserted for each refusal; a running server's next request
after a change reads from the new place and no row has changed.
"""
import json
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import roots_library as rl  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web import roots_gate  # noqa: E402

WINDOWS = os.name == "nt"
LIBRARY = "photo_index"


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class RootsRoutes(unittest.TestCase):
    KIND = "tuner"

    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_routes_")
        self.side = rl.Side(self.home, LIBRARY, real=6, bulk=0, outside=2)
        self.assertIsNone(self.side.adopt().refused)
        self.copy = os.path.join(self.home.root, "Copy", "Pictures")
        shutil.copytree(self.side.pictures, self.copy, copy_function=shutil.copy2)
        self.app = web.create_app(self.KIND, startup=self.side.library)
        self.app.testing = True
        self.client = self.app.test_client()
        roots_gate.forget(self.side.library)

    def get(self, path, **kwargs):
        return self.client.get("/%s%s" % (LIBRARY, path), **kwargs)

    def post(self, path, body=None, **kwargs):
        return self.client.post("/%s%s" % (LIBRARY, path), data=json.dumps(body or {}),
                                content_type="application/json", **kwargs)

    def rows(self):
        return json.dumps(self.side.dump(leave_out=("job_runs",)), default=repr, sort_keys=True)


class TheList(RootsRoutes):
    def test_it_lists_each_root_with_its_place_and_where_writes_go(self):
        found = self.get("/api/roots").get_json()
        self.assertEqual(LIBRARY, found["library"])
        root = found["roots"][0]
        self.assertEqual(("pictures", self.side.pictures, True, 18), (root["name"], root["active"], root["mapped"],
                                                                      root["rows"]))
        self.assertEqual("Tags and renames are written to files at %s." % self.side.pictures, root["writes_to"])
        self.assertNotIn("verifying", root)
        self.assertNotIn("last_verify", root)
        self.assertEqual([], found["busy"])

    def test_a_library_with_no_roots_answers_cleanly(self):
        plain = rl.Side(self.home, "plain", real=1, bulk=0, outside=0)
        reply = self.client.get("/plain/api/roots")
        self.assertEqual(200, reply.status_code)
        found = reply.get_json()
        self.assertEqual([], found["roots"])
        answer = self.client.post("/plain/api/roots/change-location", content_type="application/json",
                                  data=json.dumps({"root": "pictures", "location": self.copy}))
        self.assertEqual(404, answer.status_code)
        self.assertIn("no root called pictures", answer.get_json()["error"])
        self.assertIsNotNone(plain)

    def test_verify_and_change_back_are_gone(self):
        for path in ("/api/roots/verify", "/api/roots/verify-cancel", "/api/roots/change-back"):
            self.assertIn(self.post(path, {"root": "pictures"}).status_code, (404, 405), path)

    def test_it_answers_this_pc_only(self):
        for method, path in (("get", "/api/roots"), ("post", "/api/roots/change-location")):
            reply = getattr(self.client, method)("/%s%s" % (LIBRARY, path), environ_overrides={"REMOTE_ADDR": "10.0.0.7"})
            self.assertEqual(403, reply.status_code, path)

    def test_a_map_that_cannot_be_read_is_said_in_the_list(self):
        with open(config.machine_roots_path(), "w", encoding="utf-8") as handle:
            handle.write("{ nope")
        found = self.get("/api/roots").get_json()
        self.assertIn("machine_roots.json", found["problem"])


class ChangingTheLocation(RootsRoutes):
    def places(self):
        return list(config.machine_roots()["pictures"])

    def test_a_request_is_a_dry_run_unless_it_says_otherwise(self):
        before_rows, before_map = self.rows(), open(config.machine_roots_path(), "rb").read()
        reply = self.post("/api/roots/change-location", {"root": "pictures", "location": self.copy})
        found = reply.get_json()
        self.assertEqual(200, reply.status_code, found)
        self.assertTrue(found["dry_run"])
        self.assertEqual(0, found["changed"])
        self.assertEqual(18, found["verify"]["matches"])
        self.assertIn(self.copy, found["writes_to"])
        self.assertEqual(before_rows, self.rows())
        self.assertEqual(before_map, open(config.machine_roots_path(), "rb").read())

    def test_confirming_moves_it_and_no_row_changes(self):
        before = self.rows()
        found = self.post("/api/roots/change-location", {"root": "pictures", "location": self.copy,
                                                         "dry_run": False, "from": self.side.pictures}).get_json()
        self.assertTrue(found["success"], found)
        self.assertEqual(1, found["changed"])
        self.assertEqual([self.copy, self.side.pictures], self.places())
        self.assertEqual(before, self.rows(), "moving a root changed a row")
        listed = self.get("/api/roots").get_json()["roots"][0]
        self.assertEqual((self.copy, self.side.pictures), (listed["active"], listed["previous"]))
        self.assertIn(self.copy, listed["writes_to"])

    def test_a_running_servers_next_request_reads_from_the_new_place(self):
        """No restart: the same server, the same library, a request before and after."""
        before = self.get("/api/photos").get_json()
        self.assertTrue(before)
        self.assertTrue(all(photo["path"].lower().startswith(self.side.pictures.lower()) for photo in before))
        image_before = self.get("/api/photo-file", query_string={"path": before[0]["path"]})
        self.assertEqual(200, image_before.status_code)
        self.post("/api/roots/change-location", {"root": "pictures", "location": self.copy, "dry_run": False})
        after = self.get("/api/photos").get_json()
        self.assertEqual(len(before), len(after))
        self.assertTrue(all(photo["path"].lower().startswith(self.copy.lower()) for photo in after), after[:1])
        self.assertEqual(200, self.get("/api/photo-file", query_string={"path": after[0]["path"]}).status_code)
        self.post("/api/roots/change-location", {"root": "pictures", "location": self.side.pictures,
                                                   "dry_run": False})
        again = self.get("/api/photos").get_json()
        self.assertTrue(all(photo["path"].lower().startswith(self.side.pictures.lower()) for photo in again))

    def test_a_poor_result_is_a_sentence_and_override_changes_it(self):
        other = os.path.join(self.home.root, "Other", "Pictures")
        rl.make_jpeg(os.path.join(other, rl.FOLDERS[0], "IMG_1001.jpg"))
        reply = self.post("/api/roots/change-location", {"root": "pictures", "location": other, "dry_run": False})
        self.assertEqual(400, reply.status_code)
        self.assertIn("another folder", reply.get_json()["error"])
        self.assertEqual([self.side.pictures], self.places())
        ok = self.post("/api/roots/change-location", {"root": "pictures", "location": other, "dry_run": False,
                                                      "override": True})
        self.assertEqual(200, ok.status_code, ok.get_json())
        self.assertEqual(other, self.places()[0])

    def test_nothing_moves_while_an_index_run_is_queued_and_the_answer_says_which(self):
        before = open(config.machine_roots_path(), "rb").read()
        with mock.patch.object(indexing_jobs.IndexQueue, "busy", lambda queue: True):
            reply = self.post("/api/roots/change-location", {"root": "pictures", "location": self.copy,
                                                             "dry_run": False})
            listed = self.get("/api/roots").get_json()
        self.assertEqual(409, reply.status_code)
        self.assertIn("an index run is running or queued", reply.get_json()["error"])
        self.assertIn("an index run is running or queued", listed["busy"])
        self.assertEqual(before, open(config.machine_roots_path(), "rb").read())

    def test_a_second_tab_with_a_stale_view_is_told_the_map_moved(self):
        self.post("/api/roots/change-location", {"root": "pictures", "location": self.copy, "dry_run": False})
        third = os.path.join(self.home.root, "Third", "Pictures")
        shutil.copytree(self.side.pictures, third, copy_function=shutil.copy2)
        reply = self.post("/api/roots/change-location", {"root": "pictures", "location": third, "dry_run": False,
                                                         "from": self.side.pictures})
        self.assertEqual(409, reply.status_code)
        self.assertIn("changed since you looked", reply.get_json()["error"])
        self.assertEqual(self.copy, self.places()[0])

    def test_confirm_twice_is_one_change_and_the_second_says_so(self):
        body = {"root": "pictures", "location": self.copy, "dry_run": False, "from": self.side.pictures}
        first = self.post("/api/roots/change-location", body).get_json()
        second = self.post("/api/roots/change-location", body).get_json()
        self.assertEqual((1, 0), (first["changed"], second["changed"]))
        self.assertTrue(second["success"])
        self.assertTrue(second["unchanged"])
        self.assertEqual([self.copy, self.side.pictures], self.places())

    def test_a_location_that_is_not_a_place_is_a_sentence(self):
        for location, word in (("Pictures", "absolute"), (os.path.join(self.home.root, "Nowhere"), "cannot be reached"),
                               (None, "absolute")):
            reply = self.post("/api/roots/change-location", {"root": "pictures", "location": location,
                                                             "dry_run": False})
            self.assertEqual(400, reply.status_code, location)
            self.assertIn(word, reply.get_json()["error"])
            self.assertNotIn("Traceback", reply.get_json()["error"])
        self.assertEqual([self.side.pictures], self.places())


class TwoLibrariesOnOneRoot(RootsRoutes):
    def setUp(self):
        super().setUp()
        self.second = rl.Side(self.home, "second", real=2, bulk=0, outside=0, share=self.side)
        self.assertIsNone(self.second.adopt().refused)

    def test_the_list_says_who_shares_the_root(self):
        mine = self.get("/api/roots").get_json()["roots"][0]
        theirs = self.client.get("/second/api/roots").get_json()["roots"][0]
        self.assertEqual(["second"], mine["shared_with"])
        self.assertEqual([LIBRARY], theirs["shared_with"])

    def test_a_run_queued_in_the_other_library_holds_the_move_up(self):
        before = open(config.machine_roots_path(), "rb").read()
        queue = indexing_jobs.queue_for(self.second.library)
        self.addCleanup(indexing_jobs.forget, self.second.library)
        with mock.patch.object(type(queue), "busy", lambda q: q is queue):
            reply = self.post("/api/roots/change-location", {"root": "pictures", "location": self.copy,
                                                             "dry_run": False})
        self.assertEqual(409, reply.status_code)
        self.assertIn("in second, which uses this root too", reply.get_json()["error"])
        self.assertEqual(before, open(config.machine_roots_path(), "rb").read())


class WhatAMachineThatDoesNotPlaceTheRootIsTold(RootsRoutes):
    def lose_the_map(self):
        os.remove(config.machine_roots_path())
        roots_gate.forget(self.side.library)

    def test_a_photo_request_is_a_409_naming_the_file_and_the_line_to_add(self):
        self.lose_the_map()
        reply = self.get("/api/photos")
        self.assertEqual(409, reply.status_code)
        found = reply.get_json()
        self.assertTrue(found["roots_problem"])
        self.assertIn("machine_roots.json", found["error"])
        self.assertIn('"pictures"', found["error"])
        self.assertEqual("1", reply.headers["X-TagPup-Roots-Problem"])

    def test_the_pages_and_roots_still_open(self):
        self.lose_the_map()
        self.assertEqual(200, self.client.get("/api/databases").status_code)
        listed = self.get("/api/roots").get_json()
        self.assertFalse(listed["roots"][0]["mapped"])
        self.assertIsNone(listed["roots"][0]["active"])
        self.assertEqual(200, self.get("/api/history").status_code)

    def test_the_folder_picker_the_roots_dialog_uses_is_not_gated(self):
        """Browse... names the place the root is to be: it must work while the root has none."""
        self.lose_the_map()
        with mock.patch("tagpup.web.tuner_routes.desktop.ask_for_folder", return_value=self.copy):
            reply = self.get("/api/browse-folder")
        self.assertEqual(200, reply.status_code)
        self.assertEqual(self.copy, reply.get_json()["path"])

    def test_it_can_be_placed_from_the_roots_dialog_and_the_library_opens_again(self):
        """A root with no place: Change location names one, and the next request works."""
        self.lose_the_map()
        reply = self.post("/api/roots/change-location", {"root": "pictures", "location": self.copy,
                                                         "dry_run": False})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual(200, self.get("/api/photos").status_code)

    def test_a_map_that_is_not_valid_is_the_same_sentence(self):
        with open(config.machine_roots_path(), "w", encoding="utf-8") as handle:
            handle.write("{ nope")
        roots_gate.forget(self.side.library)
        reply = self.get("/api/photos")
        self.assertEqual(409, reply.status_code)
        self.assertIn("machine_roots.json", reply.get_json()["error"])

    def test_a_library_with_no_roots_is_never_gated(self):
        rl.Side(self.home, "plain", real=1, bulk=0, outside=0)
        self.assertEqual(200, self.client.get("/plain/api/photos").status_code)


class TheOtherApp(RootsRoutes):
    KIND = "tagpup"

    def test_tagpups_api_says_the_same_and_the_picker_still_answers(self):
        os.remove(config.machine_roots_path())
        roots_gate.forget(self.side.library)
        reply = self.get("/api/taxonomy/tree")
        self.assertEqual(409, reply.status_code)
        self.assertIn("machine_roots.json", reply.get_json()["error"])
        self.assertEqual(200, self.client.get("/api/databases").status_code)


if __name__ == "__main__":
    unittest.main()
