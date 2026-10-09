"""docs/findings.md, #987: the routes of "Name faces from tags" with "Only this folder", served alike by both apps.

Flask's test client over one library (tests/test_name_faces_routes.py's RoutesCase): the plan the question shows is the
folder's, Yes writes it and nothing outside the folder, grouping is refused for a folder, a folder with no photo is refused with
the sentence, and the choice's counts are given.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_name_faces_routes import CONFIRM, START, STATUS, RoutesCase  # noqa: E402
from test_faces_from_tags import WREN, at, look  # noqa: E402

SCOPE = "/harbour/api/name-faces/scope"


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
        answered = self.apps[kind].post(CONFIRM, json={"job": status["job"], "group": False})
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
        self.assertFalse(status["only_folder"], "anything but true is the whole library, as group is")

    def test_grouping_is_refused_for_a_folder_and_the_question_stays_open(self):
        status = self.asking("tuner", folder=self.run, only_folder=True)
        answer = self.apps["tuner"].post(CONFIRM, json={"job": status["job"], "group": True})
        self.assertEqual(400, answer.status_code)
        self.assertIn("cannot be limited to a folder", answer.get_json()["error"])
        self.assertIsNone(self.name_of(self.inside))
        self.assertEqual(200, self.apps["tuner"].post(CONFIRM, json={"job": status["job"], "group": False}).status_code)
        self.join(status["job"])

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
