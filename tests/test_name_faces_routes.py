"""docs/findings.md, #789: the routes of "Name faces from tags", which both apps serve alike.

TagTuner's Folder Matches header and gear and TagPup's Organize folder view start the same job
(tagpup.jobs.naming_faces) through /api/name-faces/. These tests drive both apps over one library, with Flask's test
client (no port; the job's thread is joined): the question is a plan and writes nothing, Yes writes, a click while one runs
is told so, the work is refused while the library is indexed, and a name given through one app is read through the other.

"Only this folder" (docs/findings.md, #994) is served by the same routes: the plan the question shows is the folder's, Yes writes
it and nothing outside the folder, a folder with no photo is refused with the sentence, and the choice's counts are given.
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
SCOPE = "/harbour/api/name-faces/scope"
#: The writes of faces' names outside name_faces_routes.GUARDED: refused by TagTuner's blueprint, TagPup's photo_face_routes and
#: the tree's routes themselves (tuner_routes.naming_refusal).
NAMES_WRITTEN = ("/harbour/api/face/match", "/harbour/api/taxonomy/rename", "/harbour/api/taxonomy/delete-confirm")


def fill(case):
    """The photos of these tests: one face to name by its tag, one left for Identify Faces, one decided face to compare with."""
    case.single = case.photo("old_001.jpg", ["People/" + WREN])
    case.single_face = case.face(case.single)
    both = case.photo("old_002.jpg", ["People/" + WREN, "People/" + ODA])
    case.face(both, at(120))                    # like no one: left for Identify Faces
    elsewhere = case.photo("old_003.jpg", ["People/" + WREN])
    case.face(elsewhere, at(0), name=WREN, name_source="manual")     # Wren has a decided face to be compared with


class RoutesCase(Case):
    def setUp(self):
        super().setUp()
        self.addCleanup(naming_faces.forget, self.library)
        self.apps = {}
        for kind in ("tagpup", "tuner"):
            app = web.create_app(kind, startup=self.library)
            app.testing = True
            self.apps[kind] = app.test_client()
        fill(self)

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
        answered = self.apps["tagpup"].post(CONFIRM, json={"job": started["job"]})
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
        # #871: the plan was read, and a name given meanwhile would make it stale.
        import re
        writing = {"/api/sync": {"apply": True}, "/api/history/<int:change_id>/undo": {"apply": True}}
        guarded = {"/harbour" + re.sub(r"<[^>]+>", "1", path): writing.get(path, {}) for path in sorted(name_faces_routes.GUARDED)}
        with self.while_names_are_given():
            for kind, client in self.apps.items():
                for url, body in guarded.items():
                    with self.subTest(kind=kind, url=url):
                        answer = client.post(url, json=body)
                        self.assertEqual(409, answer.status_code, url)
                        self.assertEqual(tuner_routes.NAMING_REFUSAL, answer.get_json()["error"])
            # Reads are not refused: a sync's rehearsal, a photo's faces, the status of the job itself.
            self.assertNotEqual(409, self.apps["tuner"].post("/harbour/api/sync", json={}).status_code)
            self.assertNotEqual(409, self.apps["tuner"].post("/harbour/api/history/1/undo", json={}).status_code, "a rehearsal")
            self.assertEqual(200, self.apps["tagpup"].get("/harbour/api/photo-faces", query_string={"path": self.single}).status_code)
            self.assertEqual(200, self.apps["tuner"].get(CURRENT).status_code)
            # A face named by hand meanwhile, in either app (TagTuner's own writes, TagPup's face on the open photo), and the
            # tree's rename and delete, which write faces' names: refused in the same words. A node made names no face.
            for kind, client in self.apps.items():
                for url in NAMES_WRITTEN:
                    with self.subTest(kind=kind, url=url):
                        answer = client.post(url, json={"face_id": self.single_face, "person_name": WREN})
                        self.assertEqual((409, tuner_routes.NAMING_REFUSAL), (answer.status_code, answer.get_json()["error"]))
            self.assertIsNone(look(self.path, "SELECT name FROM faces WHERE id = ?", (self.single_face,))[0][0],
                              "the face named by hand was not named")
            made = self.apps["tagpup"].post("/harbour/api/taxonomy/create", json={"name": "Places/Harbour", "parent_id": None,
                                                                                    "has_face": 0})
            self.assertEqual(200, made.status_code, made.data)
        for url, body in list(guarded.items()) + [(url, {}) for url in NAMES_WRITTEN]:
            answer = self.apps["tagpup"].post(url, json=body)
            self.assertNotEqual(tuner_routes.NAMING_REFUSAL, (answer.get_json(silent=True) or {}).get("error"),
                                "and let go after: %s" % url)

    def test_an_applied_sync_does_not_run_while_names_are_given_however_it_is_asked(self):
        """The Activity page's Run now names the library in the body, not the URL, so no route guard sees it; the scheduled job
        and the folder watcher call the same runtime.sync. It refuses itself, and runs after."""
        from types import SimpleNamespace

        from tagpup import runtime
        from tagpup.jobs import recurring
        from tagpup.services import sync as sync_service
        from tagpup.web import lifecycle as web_lifecycle

        def run_now(name, library=None):
            job = recurring.JOBS.get(name)
            result = job.call(library, SimpleNamespace(given={"runtime": SimpleNamespace(sync=runtime.sync)}))
            ran.append(result)
            return SimpleNamespace(ran=True, run_id=1, library=library)

        ran = []
        tasks = {"recurring jobs": SimpleNamespace(registry=recurring.JOBS, start_job=run_now)}
        background = SimpleNamespace(task=tasks.get, busy=lambda: [])
        app = web.create_app("tagpup", startup=None, lifecycle=web_lifecycle.Lifecycle(background=background))
        app.testing = True
        page = app.test_client()      # as the page posts: no library in the URL, the library in the body
        asked = {"job": "sync", "library": self.library.name}
        real = sync_service.sync
        with mock.patch.object(sync_service, "sync", wraps=real) as service:
            with self.while_names_are_given():
                self.assertEqual(200, page.post("/api/activity/jobs/run", json=asked).status_code)
                refused = runtime.sync(self.library, folder=None, apply=True)         # what the watcher calls
                self.assertEqual(runtime.NAMING_SAYS, ran[-1].refused)
                self.assertEqual(runtime.NAMING_SAYS, refused.refused)
                self.assertEqual(0, service.call_count, "no sync was begun")
                self.assertFalse(runtime.sync(self.library, apply=False).refused, "a rehearsal reads")
            self.assertEqual(1, service.call_count)
            after = runtime.sync(self.library, apply=True)
            self.assertNotEqual(runtime.NAMING_SAYS, after.refused, "and runs when the names are written")
            self.assertEqual(2, service.call_count)

class FolderRoutes(RoutesCase):
    def setUp(self):
        super().setUp()
        self.run = os.path.join(self.folder, "Run")
        self.inside = self.face(self.photo(os.path.join("Run", "in_001.jpg"), ["People/" + WREN]), at(2))
        self.deeper = self.face(self.photo(os.path.join("Run", "Heat1", "in_002.jpg"), ["People/" + WREN]), at(4))

    def name_of(self, face_id):
        return look(self.path, "SELECT name FROM faces WHERE id = ?", (face_id,))[0][0]

    def asking(self, kind="tuner", **body):
        """Start a job, wait for its plan and give its status: the question as the page reads it."""
        answer = self.start(kind, **body)
        self.assertEqual(200, answer.status_code, answer.get_json())
        handle = answer.get_json()["status"]["job"]
        return self.apps[kind].get(STATUS, query_string={"job": handle}).get_json()["status"]


class StartingForAFolder(FolderRoutes):
    def yes_for_a_folder(self, kind):
        status = self.asking(kind, folder=self.run, only_folder=True)
        self.assertTrue(status["only_folder"])
        self.assertEqual(2, status["plan"]["faces"], "the two photos under Run, none of the folder beside it")
        answered = self.apps[kind].post(CONFIRM, json={"job": status["job"]})
        self.assertEqual(200, answered.status_code, answered.get_json())
        self.join(status["job"])
        self.assertEqual((WREN, WREN), (self.name_of(self.inside), self.name_of(self.deeper)))
        self.assertIsNone(self.name_of(self.single_face), "outside the folder")

    def test_tagtuner_asks_about_the_folder_and_yes_writes_only_there(self):
        self.yes_for_a_folder("tuner")

    def test_tagpup_asks_about_the_folder_and_yes_writes_only_there(self):
        self.yes_for_a_folder("tagpup")

    def test_a_whole_library_start_is_as_before(self):
        status = self.asking("tuner", folder=self.run)
        self.assertFalse(status["only_folder"])
        self.assertEqual(3, status["plan"]["faces"])

    def test_a_folder_with_no_photo_is_refused_with_the_sentence(self):
        answer = self.start("tuner", folder=os.path.join(self.folder, "Nowhere"), only_folder=True)
        self.assertEqual(400, answer.status_code)
        self.assertIn("holds no photo", answer.get_json()["error"])

    def test_only_this_folder_without_a_folder_is_refused(self):
        self.assertEqual(400, self.start("tuner", only_folder=True).status_code)

    def test_only_this_folder_must_be_exactly_true(self):
        status = self.asking("tuner", folder=self.run, only_folder="yes")
        self.assertFalse(status["only_folder"], "anything but true is the whole library")

    def test_a_question_for_the_other_scope_is_told_so_with_that_question(self):
        whole = self.asking("tuner")
        answer = self.apps["tagpup"].post(START, json={"folder": self.run, "only_folder": True})
        self.assertEqual(409, answer.status_code)
        self.assertEqual(whole["job"], answer.get_json()["job"]["job"])
        self.assertFalse(answer.get_json()["job"]["only_folder"])


class TheChoicesCounts(FolderRoutes):
    def test_both_apps_give_the_counts_of_the_folder_and_the_library(self):
        for kind in self.apps:
            with self.subTest(kind):
                found = self.apps[kind].get(SCOPE, query_string={"folder": self.run}).get_json()
                self.assertTrue(found["success"])
                self.assertEqual({"named": 0, "unnamed": 2, "photos": 2}, found["folder"])
                self.assertEqual({"named": 1, "unnamed": 4}, found["library"])
                self.assertIsNone(found["job"])

    def test_a_folder_with_no_photo_says_why(self):
        found = self.apps["tuner"].get(SCOPE, query_string={"folder": os.path.join(self.folder, "Nowhere")}).get_json()
        self.assertIsNone(found["folder"])
        self.assertIn("holds no photo", found["why"])
        self.assertIsNotNone(found["library"])

    def test_the_job_to_pick_up_comes_with_the_counts(self):
        status = self.asking("tuner", folder=self.run, only_folder=True)
        found = self.apps["tagpup"].get(SCOPE, query_string={"folder": self.run}).get_json()
        self.assertEqual(status["job"], found["job"]["job"])

    def test_only_this_pc_may_ask(self):
        answer = self.apps["tuner"].get(SCOPE, environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
        self.assertEqual(403, answer.status_code)


if __name__ == "__main__":
    unittest.main()
