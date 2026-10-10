"""The routes of the names to review (docs/SPEC_TAGTUNER.md, /api/names-to-review): both apps serve them alike, a read writes nothing,
a choice is a rehearsal until `apply`, a stale person is a 404 the page tells the owner about, and a name settled in another tab is a 400.

Flask's test client: no port, no thread. The library is the one of tests/people_by_id.py (two cousins called Sam, a pet and a friend
called Max); the names are faces named as an older version or a copied library leaves them, a name and no id.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from people_by_id import SAM_I, WREN, TwoSams, look, write  # noqa: E402
from test_name_review import QUILL, name_without_a_person  # noqa: E402

from tagpup.web import app as web  # noqa: E402

LIST, RESOLVE = "/harbour/api/names-to-review", "/harbour/api/names-to-review/resolve"


class Routes(TwoSams, unittest.TestCase):
    def setUp(self):
        self.make_library()
        write(self.path, lambda conn: name_without_a_person(conn, [self.faces[0], self.faces[2]], QUILL))
        write(self.path, lambda conn: name_without_a_person(conn, [self.faces[1]], "Sam", None))
        self.clients = {}
        for kind in ("tagpup", "tuner"):
            app = web.create_app(kind, startup=self.library)
            app.testing = True
            self.clients[kind] = app.test_client()

    def post(self, kind="tuner", **body):
        return self.clients[kind].post(RESOLVE, json=body)

    def test_both_apps_list_the_same_names_and_a_read_writes_nothing(self):
        before = look(self.path, "SELECT COUNT(*) FROM changes")
        answers = {kind: client.get(LIST).get_json() for kind, client in self.clients.items()}
        self.assertEqual(answers["tagpup"], answers["tuner"])
        found = answers["tuner"]
        self.assertTrue(found["success"])
        self.assertEqual(2, found["count"])
        self.assertEqual({QUILL, "Sam"}, {entry["name"] for entry in found["entries"]})
        self.assertEqual({"count": 2, "success": True}, self.clients["tuner"].get(LIST, query_string={"count": "1"}).get_json())
        self.assertEqual(before, look(self.path, "SELECT COUNT(*) FROM changes"))

    def test_a_choice_is_a_rehearsal_until_apply_and_then_one_change(self):
        group = self.node("Friends")
        rehearsal = self.post(key=QUILL, action="make", group_id=group)
        self.assertEqual(200, rehearsal.status_code, rehearsal.get_json())
        self.assertFalse(rehearsal.get_json()["applied"])
        self.assertIn("Friends/Wren Quill", rehearsal.get_json()["sentence"])
        self.assertIsNone(self.node("Friends/Wren Quill"))
        done = self.post("tagpup", key=QUILL, action="make", group_id=group, apply=True)
        self.assertEqual(200, done.status_code, done.get_json())
        said = done.get_json()
        self.assertEqual((True, 2), (said["applied"], said["changed"]))
        self.assertIsNotNone(said["change"])
        self.assertEqual(1, self.clients["tuner"].get(LIST).get_json()["count"], "the other app sees what the first did")

    def test_link_to_one_of_two_people_called_alike_is_by_id(self):
        done = self.post(key="Sam", action="link", person_id=self.node(SAM_I), apply=True)
        self.assertEqual(200, done.status_code, done.get_json())
        self.assertEqual((self.node(SAM_I), "Sam"), self.face(self.faces[1])[:2])

    def test_a_person_or_group_that_is_gone_is_a_404_the_page_tells_the_owner_about(self):
        gone = self.post(key=QUILL, action="link", person_id=99999, apply=True)
        self.assertEqual(404, gone.status_code)
        self.assertFalse(gone.get_json()["success"])
        self.assertIn("reload", gone.get_json()["error"])
        self.assertEqual(404, self.post(key=QUILL, action="make", group_id=99999, apply=True).status_code)
        self.assertEqual((None, QUILL, "manual"), self.face(self.faces[0]))

    def test_a_name_settled_in_another_tab_is_a_400_and_nothing_is_done_twice(self):
        wren = self.node(WREN)
        self.assertEqual(200, self.post(key=QUILL, action="link", person_id=wren, apply=True).status_code)
        again = self.post("tagpup", key=QUILL, action="unname", apply=True)
        self.assertEqual(400, again.status_code)
        self.assertIn("another window", again.get_json()["error"])
        self.assertEqual((wren, "Wren Halloway", "manual"), self.face(self.faces[0]))

    def test_a_choice_that_does_not_fit_is_refused_and_a_bad_request_is_a_400(self):
        self.assertEqual(400, self.post(key=QUILL, action="melt", apply=True).status_code)
        self.assertEqual(400, self.post(action="unname", apply=True).status_code)
        self.assertEqual(400, self.post(key=QUILL, action="link", person_id="abc", apply=True).status_code)
        self.assertEqual(400, self.post(key="Sam", action="make", group_id=self.node("Friends"), apply=True).status_code)

    def test_a_choice_is_refused_while_the_faces_are_being_clustered(self):
        from tagpup.web import tuner_routes
        with tuner_routes.while_clustering(self.library):
            refused = self.post(key=QUILL, action="unname", apply=True)
            self.assertEqual(409, refused.status_code)
            self.assertEqual(200, self.post(key=QUILL, action="unname").status_code, "a rehearsal reads")
        self.assertEqual((None, QUILL, "manual"), self.face(self.faces[0]))

    def test_activity_says_how_many_wait_and_where_to_open_them(self):
        app = web.create_app("tagpup", startup=self.library, ports={"tagpup": 8090, "tuner": 8080})
        app.testing = True
        found = app.test_client().get("/api/activity/attention").get_json()
        mine = next(each for each in found["libraries"] if each["name"] == "harbour")
        self.assertEqual((2, 2), (mine["names_to_review"], found["names_to_review"]))
        self.assertEqual("http://localhost:8080/harbour/?names-to-review=1", mine["names_url"])
        self.post(key=QUILL, action="dismiss", apply=True)
        self.post(key="Sam", action="dismiss", apply=True)
        found = app.test_client().get("/api/activity/attention").get_json()
        self.assertEqual((0, None), (found["names_to_review"], found["libraries"][0]["names_url"]))

    def test_activity_says_so_when_the_names_could_not_be_counted_it_never_says_none(self):
        from unittest import mock
        app = web.create_app("tagpup", startup=self.library, ports={"tagpup": 8090, "tuner": 8080})
        app.testing = True
        with mock.patch("tagpup.services.name_review.count", side_effect=RuntimeError("the library is locked")):
            found = app.test_client().get("/api/activity/attention").get_json()
        mine = next(each for each in found["libraries"] if each["name"] == "harbour")
        self.assertIn("could not be counted: the library is locked", mine["names_error"])
        self.assertEqual((0, None), (mine["names_to_review"], mine["names_url"]))

    def test_it_answers_this_pc_only(self):
        far = self.clients["tuner"].get(LIST, environ_overrides={"REMOTE_ADDR": "10.1.2.3"})
        self.assertEqual(403, far.status_code)


if __name__ == "__main__":
    unittest.main()
