"""docs/findings.md, #789: the routes of "Name faces from tags", which both apps serve alike.

TagTuner's Folder Matches header and gear and TagPup's Organize folder view start the same job
(tagpup.jobs.naming_faces) through /api/name-faces/. These tests drive both apps over one library, with Flask's test
client (no port; the job's thread is joined): the question is a plan and writes nothing, Yes writes, a click while one runs
is told so, the work is refused while the library is indexed, and a name given through one app is read through the other.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_faces_from_tags import ODA, WREN, Case, at, look  # noqa: E402

from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.jobs import naming_faces  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web import name_faces_routes, tuner_routes  # noqa: E402

START, STATUS, CONFIRM, CANCEL, CURRENT = ("/harbour/api/name-faces/" + name
                                           for name in ("start", "status", "confirm", "cancel", "current"))


class RoutesCase(Case):
    def setUp(self):
        super().setUp()
        self.addCleanup(naming_faces.forget, self.library)
        self.apps = {}
        for kind in ("tagpup", "tuner"):
            app = web.create_app(kind, startup=self.library)
            app.testing = True
            self.apps[kind] = app.test_client()
        self.single = self.photo("old_001.jpg", ["People/" + WREN])
        self.single_face = self.face(self.single)
        both = self.photo("old_002.jpg", ["People/" + WREN, "People/" + ODA])
        self.face(both, at(120))                    # like no one: left for Identify Faces
        elsewhere = self.photo("old_003.jpg", ["People/" + WREN])
        self.face(elsewhere, at(0), name=WREN, name_source="manual")     # Wren has a decided face to be compared with

    def join(self, handle):
        job = naming_faces._held(self.library)[handle]
        job.thread.join(60)
        self.assertFalse(job.thread.is_alive())

    def start(self, kind="tuner", **body):
        answer = self.apps[kind].post(START, json=body)
        if answer.status_code == 200:
            self.join(answer.get_json()["status"]["job"])
        return answer


class StartingAndAnswering(RoutesCase):
    def test_start_reads_the_plan_and_writes_nothing(self):
        for kind in self.apps:
            with self.subTest(kind):
                naming_faces.forget(self.library)
                answer = self.start(kind)
                self.assertEqual(200, answer.status_code, answer.get_json())
                status = answer.get_json()["status"]
                self.join(status["job"])
                found = self.apps[kind].get(STATUS, query_string={"job": status["job"]}).get_json()["status"]
                self.assertEqual("asking", found["state"])
                self.assertEqual(1, found["plan"]["faces"])
                self.assertIsNone(look(self.path, "SELECT name FROM faces WHERE id = ?", (self.single_face,))[0][0])

    def test_yes_writes_and_the_other_app_reads_the_name(self):
        started = self.start("tuner").get_json()["status"]
        # TagPup, on the same library, sees the job TagTuner started and answers it.
        seen = self.apps["tagpup"].get(CURRENT).get_json()["status"]
        self.assertEqual(started["job"], seen["job"])
        answered = self.apps["tagpup"].post(CONFIRM, json={"job": started["job"], "group": False})
        self.assertEqual(200, answered.status_code, answered.get_json())
        self.join(started["job"])
        done = self.apps["tuner"].get(STATUS, query_string={"job": started["job"]}).get_json()["status"]
        self.assertEqual("done", done["state"], done["message"])
        self.assertEqual(1, done["applied"]["changed"])
        faces = self.apps["tagpup"].get("/harbour/api/photo-faces", query_string={"path": self.single}).get_json()["faces"]
        self.assertEqual([WREN], [face["name"] for face in faces], "a name given from one app is read by the other")

    def test_the_folder_the_page_has_open_is_counted(self):
        started = self.start("tagpup", folder=self.folder).get_json()["status"]
        self.apps["tagpup"].post(CONFIRM, json={"job": started["job"]})
        self.join(started["job"])
        done = self.apps["tagpup"].get(STATUS, query_string={"job": started["job"]}).get_json()["status"]
        self.assertEqual({"named": 1, "unnamed": 2}, done["in_folder"]["before"])
        self.assertEqual({"named": 2, "unnamed": 1}, done["in_folder"]["after"])

    def test_no_to_the_question_writes_nothing(self):
        started = self.start("tuner").get_json()["status"]
        cancelled = self.apps["tuner"].post(CANCEL, json={"job": started["job"]}).get_json()
        self.assertEqual("cancelled", cancelled["status"]["state"])
        self.assertIsNone(look(self.path, "SELECT name FROM faces WHERE id = ?", (self.single_face,))[0][0])
        self.assertEqual(409, self.apps["tuner"].post(CONFIRM, json={"job": started["job"]}).status_code)

    def test_an_answer_for_a_job_nobody_started_is_a_404_and_a_bad_number_a_400(self):
        self.assertEqual(404, self.apps["tuner"].post(CONFIRM, json={"job": 9999}).status_code)
        self.assertEqual(400, self.apps["tuner"].post(CONFIRM, json={"job": "x"}).status_code)
        self.assertEqual(404, self.apps["tuner"].get(STATUS, query_string={"job": 9999}).status_code)

    def test_a_remote_address_is_turned_away_it_works_on_the_whole_library(self):
        answer = self.apps["tuner"].post(START, json={}, environ_overrides={"REMOTE_ADDR": "192.168.1.9"})
        self.assertEqual(403, answer.status_code)
        self.assertEqual({}, naming_faces._held(self.library))


class TwoClicksAndBusyLibraries(RoutesCase):
    def test_a_second_start_while_one_plans_is_a_409_naming_the_running_job(self):
        import threading

        from tagpup.services import faces_from_tags
        gate, reached = threading.Event(), threading.Event()
        real = faces_from_tags.plan

        def slow(library, on_step=None):
            reached.set()
            gate.wait(30)
            return real(library, on_step=on_step)
        with mock.patch.object(faces_from_tags, "plan", slow):
            first = self.apps["tuner"].post(START, json={}).get_json()["status"]
            self.assertTrue(reached.wait(30))
            second = self.apps["tagpup"].post(START, json={})
            self.assertEqual(409, second.status_code)
            self.assertEqual(first["job"], second.get_json()["job"]["job"])
            self.assertFalse(second.get_json()["success"])
            gate.set()
            self.join(first["job"])

    def test_a_library_being_indexed_refuses_it(self):
        with mock.patch.object(indexing_jobs.queue_for(self.library), "busy", return_value=True):
            answer = self.apps["tuner"].post(START, json={})
        self.assertEqual(409, answer.status_code)
        self.assertIn("index run", answer.get_json()["error"])

    def test_faces_being_clustered_refuses_it(self):
        with tuner_routes.while_clustering(self.library):
            answer = self.apps["tagpup"].post(START, json={})
        self.assertEqual(409, answer.status_code)
        self.assertIn("clustered", answer.get_json()["error"])

    def while_names_are_given(self):
        """Run `with` this: the job is writing (its change blocked at the write) and the gate lets it finish after."""
        import contextlib
        import threading

        from tagpup.services import faces_from_tags

        @contextlib.contextmanager
        def blocked():
            gate, reached = threading.Event(), threading.Event()
            real = faces_from_tags.faces_from_tags

            def slow(*args, **more):
                reached.set()
                gate.wait(30)
                return real(*args, **more)
            started = self.start("tuner").get_json()["status"]
            with mock.patch.object(faces_from_tags, "faces_from_tags", slow):
                self.apps["tuner"].post(CONFIRM, json={"job": started["job"]})
                self.assertTrue(reached.wait(30))
                try:
                    yield started["job"]
                finally:
                    gate.set()
                    self.join(started["job"])
        return blocked()

    def test_the_writes_that_would_be_written_over_are_refused_while_names_are_given(self):
        # #871: the plan was read, and grouping commits a name for every face it read.
        import json
        guarded = {"/harbour" + path: {} for path in sorted(name_faces_routes.GUARDED) if path != "/api/sync"}
        guarded["/harbour/api/sync"] = {"apply": True}
        with self.while_names_are_given():
            for kind, client in self.apps.items():
                for url, body in guarded.items():
                    with self.subTest(kind=kind, url=url):
                        answer = client.post(url, json=body)
                        self.assertEqual(409, answer.status_code, url)
                        self.assertEqual(tuner_routes.NAMING_REFUSAL, answer.get_json()["error"])
            # Reads are not refused: a sync's rehearsal, a photo's faces, the status of the job itself.
            self.assertNotEqual(409, self.apps["tuner"].post("/harbour/api/sync", json={}).status_code)
            self.assertEqual(200, self.apps["tagpup"].get("/harbour/api/photo-faces", query_string={"path": self.single}).status_code)
            self.assertEqual(200, self.apps["tuner"].get(CURRENT).status_code)
            # TagTuner's own writes were already refused, now in the same words.
            refused = self.apps["tuner"].post("/harbour/api/face/match", json={"face_id": self.single_face, "name": WREN})
            self.assertEqual(409, refused.status_code)
            self.assertEqual(tuner_routes.NAMING_REFUSAL, refused.get_json()["error"])
            json.dumps(refused.get_json())
        for url, body in guarded.items():
            answer = self.apps["tagpup"].post(url, json=body)
            self.assertNotEqual(tuner_routes.NAMING_REFUSAL, (answer.get_json(silent=True) or {}).get("error"),
                                "and let go after: %s" % url)

    def test_the_faces_are_held_against_writes_while_the_names_are_given(self):
        seen = []
        started = self.start("tuner").get_json()["status"]
        real = naming_faces.faces_from_tags.faces_from_tags

        def watching(*args, **more):
            seen.append(tuner_routes.clustering.of(self.library).is_set())
            return real(*args, **more)
        with mock.patch.object(naming_faces.faces_from_tags, "faces_from_tags", watching):
            self.apps["tuner"].post(CONFIRM, json={"job": started["job"]})
            self.join(started["job"])
        self.assertEqual([True], seen)
        self.assertFalse(tuner_routes.clustering.of(self.library).is_set(), "and let go after")


if __name__ == "__main__":
    unittest.main()
