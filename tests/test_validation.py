"""What may be set has one owner, tagpup.core.validation, and the pages are held to it.

The cases in tests/validation_cases.json run here against the registry and in
tests/frontend/validation.test.mjs against web/common/validate.js, which applies the
rules /api/rules publishes: the page tests are served tests/validation_rules.json as
that answer, and this holds the file to what the server publishes. So a rule changed
here and not there, or a check that one language reads differently, fails one side.
"""
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from tagpup.core import validation, vocabulary  # noqa: E402
import web_client  # noqa: E402

CASES = os.path.join(ROOT, "tests", "validation_cases.json")
PUBLISHED = os.path.join(ROOT, "tests", "validation_rules.json")

REGENERATE = ('.venv/Scripts/python.exe -c "import json; from tagpup.core import validation; '
              "open('tests/validation_rules.json', 'w', encoding='utf-8', newline='\\n')"
              '.write(json.dumps(validation.published(), indent=1) + chr(10))"')


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def value_of(case_value):
    """A case's value: {"repeat": text, "times": n} is the text n times over."""
    if isinstance(case_value, dict):
        return case_value["repeat"] * case_value["times"]
    return case_value


def messages(rules):
    """Every message the rules can give, nested ones included."""
    for rule in rules:
        if "message" in rule:
            yield rule["message"]
        yield from messages(rule.get("each", []))


class TheCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load(CASES)

    def test_each_is_answered_as_it_says(self):
        for kind, value, expected in self.cases["cases"]:
            with self.subTest(kind=kind, value=value):
                self.assertEqual(validation.problem(kind, value_of(value)), expected)

    def test_every_kind_has_cases_allowing_and_refusing(self):
        for kind in validation.KINDS:
            answers = [expected for k, _, expected in self.cases["cases"] if k == kind]
            with self.subTest(kind=kind):
                self.assertIn(None, answers, "no case %s allows" % kind)
                self.assertTrue(any(answers), "no case %s refuses" % kind)

    def test_every_refusal_is_reached(self):
        # Each message is one rule; a rule no case reaches is one the pages were never
        # checked against.
        said = {expected for _, _, expected in self.cases["cases"] if expected}
        for kind, declared in validation.KINDS.items():
            for message in messages(declared["rules"]):
                pattern = re.escape(message).replace(re.escape("{value}"), ".+")
                with self.subTest(kind=kind, message=message):
                    self.assertTrue(any(re.fullmatch(pattern, s) for s in said), "no case reaches it")

    def test_an_allowed_tag_is_set_in_its_one_spelling(self):
        for typed, stored in self.cases["spelled"]:
            with self.subTest(tag=typed):
                self.assertIsNone(validation.problem("tag", typed))
                self.assertEqual(vocabulary.normalize(typed), stored)

    def test_a_misspelt_kind_allows_nothing(self):
        with self.assertRaises(KeyError):
            validation.problem("tags", "Places/Harbour")


class WhatIsPublished(unittest.TestCase):
    def test_the_page_tests_are_served_what_the_server_publishes(self):
        self.assertEqual(load(PUBLISHED), validation.published(),
                         "tests/validation_rules.json is out of date; write it again with\n" + REGENERATE)

    def test_the_rules_are_data(self):
        # What the page is sent: nothing but JSON's own types.
        published = validation.published()
        self.assertEqual(json.loads(json.dumps(published)), published)
        self.assertRegex(published["version"], r"^[0-9a-f]{12}$")

    def test_the_version_follows_the_rules(self):
        before = validation.published()["version"]
        rules = validation.KINDS["grouping"]["rules"]
        rules.append({"rule": "forbid_text", "text": "#", "message": "No hashes."})
        try:
            self.assertNotEqual(validation.published()["version"], before)
        finally:
            rules.pop()
        self.assertEqual(validation.published()["version"], before)

    def test_every_check_a_rule_names_is_one_both_languages_have(self):
        with open(os.path.join(ROOT, "web", "common", "validate.js"), encoding="utf-8") as handle:
            source = handle.read()
        named = set()

        def walk(rules):
            for rule in rules:
                named.add(rule["rule"])
                walk(rule.get("each", []))

        for declared in validation.KINDS.values():
            walk(declared["rules"])
        for name in sorted(named):
            with self.subTest(check=name):
                self.assertTrue(name in validation.CHECKS or name in ("optional", "list"))
                self.assertRegex(source, r"\b%s\b\s*[:(]|'%s'" % (name, name))


class TheSettings(unittest.TestCase):
    """Each setting a library holds is declared once: its type, range, default, whether it
    is locked and what changing it does, and its info text -- what the settings dialog
    is made from (docs/ARCHITECTURE.md, phase 7.6)."""

    #: The settings config.ini held, which a library in use is stamped from; where the
    #: libraries are (paths.data_dir) is not one.
    HELD = {"paths.exiftool", "model.name", "model.pretrained", "model.preserve_full_frame",
            "model.max_aspect_ratio", "model.force_image_size", "candidates.tags", "faces.min_face_size",
            "faces.confidence_threshold", "faces.mtcnn_thresholds", "renaming.format"}

    def test_every_setting_is_declared(self):
        self.assertEqual(set(validation.SETTINGS), self.HELD)

    def test_every_default_is_allowed(self):
        for key, value in validation.setting_defaults().items():
            with self.subTest(setting=key):
                self.assertIsNone(validation.problem(validation.setting_kind(key), value))

    def test_each_has_a_type_a_label_and_info(self):
        for key, declared in validation.SETTINGS.items():
            with self.subTest(setting=key):
                self.assertIn(declared["type"], ("text", "path", "boolean", "integer", "number", "list"))
                self.assertTrue(declared["label"].strip())
                self.assertTrue(declared["info"].strip())
                self.assertIn(declared["group"], validation.SETTING_GROUPS)

    def test_the_locked_ones_are_those_with_consequences(self):
        locked = {key for key, declared in validation.SETTINGS.items() if declared["locked"]}
        self.assertEqual(locked, {"model.name", "model.pretrained", "model.preserve_full_frame",
                                  "model.max_aspect_ratio", "model.force_image_size", "faces.min_face_size",
                                  "faces.confidence_threshold", "faces.mtcnn_thresholds", "paths.exiftool"})
        for key, declared in validation.SETTINGS.items():
            with self.subTest(setting=key):
                self.assertEqual(declared["locked"], bool(declared["consequences"]))


class BothAppsPublishThem(unittest.TestCase):
    def test_on_a_library_and_without_one(self):
        for kind in ("tagpup", "tuner"):
            app, _home = web_client.app_for(self, kind)
            client = app.test_client()
            for url in ("/library/api/rules", "/api/rules"):
                with self.subTest(app=kind, url=url):
                    reply = client.get(url)
                    self.assertEqual(reply.status_code, 200)
                    self.assertEqual(reply.get_json(), validation.published())

    def test_an_app_started_on_no_library_still_answers(self):
        app, _home = web_client.app_for(self, "tuner", startup=None)
        self.assertEqual(app.test_client().get("/api/rules").get_json(), validation.published())


if __name__ == "__main__":
    unittest.main()
