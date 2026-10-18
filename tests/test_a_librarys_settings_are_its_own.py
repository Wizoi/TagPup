"""A library's settings are its own (scenario D6): stamped once with the defaults, changed in the gear dialog, undone in
History, and read by the next run (docs/ARCHITECTURE.md, phase 7.6).

They lived in config.ini, one file for the machine; a library is now stamped with the defaults when it is made or first
opened holding none, a config.ini beside it is never read, and a change is a journaled change -- in the library's history,
undoable, refused through the validator, and for the model, face and ExifTool settings acknowledged group by group. The
libraries are made as the apps make them (tagpup.services.libraries.create) or as a library from before the settings were
in it stands (its tables migrated, no settings rows); the dialog is the real route on both apps.

Lifted: test_settings (the Data owner's 26 tests; its 12 runtime and model tests are Views', in test_the_runtime_keeps_models_by_settings.py),
test_settings_routes (7). Breaks that fail the kept scenario: tagpup.services.settings.change() skipping the journal
(`journal.apply` -> a bare write) fails the undo in History; `unacknowledged` returning [] fails the locked change being
refused. Findings: review of f127e47 (the lock was the page's alone; a stamp undone was stamped again; the CLI's looks stamped).
Not tested, no instance: a setting an earlier version stored that this one no longer declares (0 of 3 live libraries
hold one, counted 2026-10-15; the test said they did). A copy of a stamped library holding its settings is a file copy.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402

from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings  # noqa: E402
from tagpup.store import db, schema  # noqa: E402

#: An old home's config.ini differing from the defaults in what the owner changes below. Nothing reads it.
OLD_CONFIG = {"model": {"name": "ViT-L-14", "pretrained": "openai"}, "candidates": {"tags": "Harbour, Sailing"},
              "renaming": {"format": "{index} ~ {grouping} ~ {caption}"}}


def rows(library):
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return dict(conn.execute("SELECT key, value FROM settings").fetchall())
    finally:
        conn.close()


def history(library):
    return journal_service.history(library, limit=100)["changes"]


class Model:
    def __init__(self, given):
        self.settings, self.unloaded = dict(given), 0

    def unload(self):
        self.unloaded += 1


class TheGearDialog(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime(build_clip=Model, build_faces=Model)
        self.app, self.home = web_client.app_for(self, "tuner", runtime=self.runtime)
        self.client = self.app.test_client()
        self.addCleanup(self.runtime.forget, Library(self.home.library("quarry.db")))
        self.quarry = Library(self.home.library("quarry.db"))
        schema.ensure(self.quarry.path)   # a library from before the settings were in it: no settings rows
        self.other = Library(self.home.library("library.db"))
        self.url = "/quarry/api/settings"

    def post(self, values, acknowledged=None, url=None):
        body = {"values": values}
        if acknowledged is not None:
            body["acknowledged"] = acknowledged
        return self.client.post(url or self.url, json=body)

    def words_of_the_next_run(self):
        with mock.patch("tagpup.services.suggester.model_for_run") as model_for_run:
            self.runtime.begin(self.quarry).end()
        _index, clip, _faces, words = model_for_run.call_args.args
        return words, clip.settings["model_name"]

    def test_stamped_once_changed_in_the_gear_undone_in_history_and_read_by_the_next_run(self):
        # Opened holding none, whatever the old config.ini says: the gear's first ask stamps the defaults, once.
        self.home.write_old_config(OLD_CONFIG)
        self.assertEqual({}, rows(self.quarry))
        self.assertTrue(self.client.get(self.url).get_json()["stamped"])
        self.client.get(self.url)
        self.assertEqual(settings.STAMPED, rows(self.quarry))
        self.assertEqual([settings.WITH_DEFAULTS], [c["operation"] for c in history(self.quarry)])
        default_words, default_model = self.words_of_the_next_run()
        old_clip = self.runtime.clip(self.quarry)

        # A change of the model is locked: refused naming the group and what it means, until it is acknowledged.
        values = {"model.name": "ViT-B-32", "candidates.tags": "Kayak, Lighthouse", "model.preserve_full_frame": False}
        refused = self.post(values)
        self.assertEqual(400, refused.status_code)
        for text in (validation.SETTING_GROUPS["clip"]["title"], validation.SETTING_GROUPS["clip"]["consequences"][0]):
            self.assertIn(text, refused.get_json()["error"])
        self.assertEqual(400, self.post(values, acknowledged="clip, faces").status_code)
        self.assertEqual((settings.STAMPED, 1), (rows(self.quarry), len(history(self.quarry))), "a refusal writes nothing")
        done = self.post(values, acknowledged=["clip"])
        self.assertEqual(200, done.status_code, done.data)
        answer = done.get_json()
        self.assertEqual((True, 3, True), (answer["success"], answer["changed"], answer["locked"]))
        held = rows(self.quarry)
        self.assertEqual(("ViT-B-32", "false"), (held["model.name"], held["model.preserve_full_frame"]), "as the validator reads them")
        newest = history(self.quarry)[0]
        self.assertEqual((answer["change"], settings.CHANGE), (newest["id"], newest["operation"]))
        self.assertEqual(settings.STAMPED, rows(self.other), "the other library is its own")
        self.assertEqual(1, old_clip.unloaded, "the runtime lets go of the model no library uses")
        self.assertEqual(0, self.post(values, acknowledged=["clip"]).get_json()["changed"], "the same values write nothing")
        self.assertEqual(2, len(history(self.quarry)))
        self.assertEqual((["Kayak", "Lighthouse"], "ViT-B-32"), self.words_of_the_next_run())

        # Undone in History: rehearsed, then applied; the next run reads the old words again.
        undo = "/quarry/api/history/%d/undo" % answer["change"]
        self.assertTrue(self.client.post(undo, json={}).get_json()["dry_run"])
        self.assertEqual(3, self.client.post(undo, json={"apply": True}).get_json()["changed"])
        self.assertEqual(settings.STAMPED, rows(self.quarry))
        self.assertEqual((default_words, default_model), self.words_of_the_next_run())

        # The stamp itself cannot be undone: the next read would stamp it again, again.
        stamp = history(self.quarry)[-1]
        for apply in (False, True):
            refusal = self.client.post("/quarry/api/history/%d/undo" % stamp["id"], json={"apply": apply})
            self.assertEqual(400, refusal.status_code)
            self.assertEqual("A library's first settings cannot be undone; change them instead.", refusal.get_json()["refused"])
        self.assertEqual(settings.STAMPED, rows(self.quarry))
        self.assertIn("stamp settings from config.ini", settings.STAMPS, "2 of the 3 live journals hold one, counted 2026-10-15")

    def test_each_app_shows_its_own_settings_of_the_library_in_the_url(self):
        tagpup = web_client.app_for(self, "tagpup")
        for kind, client, library in (("tuner", self.client, self.other), ("tagpup", tagpup[0].test_client(), None)):
            with self.subTest(app=kind):
                if library is None:
                    library = Library(tagpup[1].library("library.db"))
                settings.change(library, {"faces.min_face_size": "40"}, acknowledged=["faces"])
                answer = client.get("/library/api/settings").get_json()
                shown = {s["key"]: s for group in answer["groups"] for s in group["settings"]}
                # Each app's gear shows its own features' settings (owner, 2026-09-25).
                mine = {key for key, d in validation.SETTINGS.items() if validation.SETTING_GROUPS[d["group"]]["app"] == kind}
                self.assertEqual((library.name, True, mine), (answer["library"], answer["stamped"], set(shown)))
                for key in mine:
                    for field in ("label", "type", "default", "info", "locked", "consequences"):
                        self.assertEqual(shown[key][field], validation.SETTINGS[key][field], (key, field))
                groups = {g["name"]: g for g in answer["groups"]}
                if kind == "tuner":
                    self.assertEqual("40", shown["faces.min_face_size"]["value"])
                    self.assertEqual({"clip", "face-model", "faces", "exiftool", "library"}, set(groups))
                    # The library's folders change nothing already made: not locked.
                    self.assertEqual({"clip", "face-model", "faces", "exiftool"},
                                     {name for name, g in groups.items() if g["locked"]})
                else:
                    self.assertEqual({"suggest", "renaming"}, set(groups))
                    self.assertFalse(any(g["locked"] for g in groups.values()))

    def test_the_file_routes_use_the_librarys_own_exiftool(self):
        from tagpup.web import state
        named = os.path.join(self.home.root, "exiftool-own.exe")
        open(named, "w").close()
        self.client.get(self.url)
        self.assertEqual(200, self.post({"paths.exiftool": named}, acknowledged=["exiftool"]).status_code)
        with self.app.test_request_context():
            self.assertEqual(named, state.exiftool(self.quarry))
            with mock.patch("tagpup.config.default_exiftool", return_value=named + ".missing"), \
                    mock.patch("shutil.which", return_value=None):
                self.assertEqual(named + ".missing", state.exiftool(self.other))


class WhatEachLibraryHolds(unittest.TestCase):
    def setUp(self):
        from own_home import for_test
        self.home = for_test(self)

    def test_a_library_holding_none_some_or_all(self):
        whole = Library(self.home.library("whole.db"))
        self.assertEqual(1, library_actions.create(whole.path).changed)
        self.assertEqual(settings.STAMPED, rows(whole))
        [stamp] = history(whole)
        self.assertEqual((settings.WITH_DEFAULTS, {"settings": {"insert": len(settings.STAMPED)}}), (stamp["operation"], stamp["rows"]))
        # Holding some (1 of the 3 live libraries lacks a key added since it was stamped): the defaults for the rest, no stamp.
        partial = Library(self.home.library("partial.db"))
        library_actions.create(partial.path)
        settings.change(partial, {"candidates.tags": "Kayak"})
        conn = db.connect(partial.path)
        try:
            conn.execute("DELETE FROM settings WHERE key IN ('renaming.format', 'faces.min_face_size')")
            conn.commit()
        finally:
            conn.close()
        before = history(partial)
        for read in (runtimes.library_settings, runtimes.peek_settings):
            found = read(partial)
            self.assertEqual(("Kayak", settings.DEFAULTS["renaming.format"], settings.DEFAULTS["faces.min_face_size"], True),
                             (found["candidates.tags"], found["renaming.format"], found["faces.min_face_size"], found.stamped))
        self.assertEqual((before, False), (history(partial), "renaming.format" in rows(partial)))
        # Stamped meanwhile by another process: left as it is.
        self.assertEqual(0, settings.stamp(whole, {"model.name": "ViT-B-32"}).changed)
        self.assertEqual(settings.STAMPED, rows(whole))
        # A look writes nothing, not even a library that is not there; a read in use stamps two libraries each for itself.
        one, two = Library(self.home.library("one.db")), Library(self.home.library("two.db"))
        for library in (one, two):
            schema.ensure(library.path)
        self.home.write_old_config(OLD_CONFIG)
        looked = runtimes.peek_settings(one)
        self.assertEqual((False, settings.DEFAULTS, {}, []), (looked.stamped, looked.values, rows(one), history(one)))
        missing = Library(self.home.library("nowhere.db"))
        self.assertEqual((False, False), (runtimes.peek_settings(missing).stamped, os.path.exists(missing.path)))
        self.assertEqual(settings.DEFAULTS, runtimes.library_settings(one).values)
        self.assertEqual(({}, 1), (rows(two), len(history(one))))

    # What the validator says, in its words; each refusal writes nothing and is not journaled.
    CASES = [
        ({"faces.min_face_size": "twenty"}, "The smallest face is a whole number of pixels."),
        ({"faces.min_face_size": "0"}, "The smallest face must be between 1 and 2000."),
        ({"faces.confidence_threshold": "1.5"}, "The confidence must be between 0 and 1."),
        ({"faces.mtcnn_thresholds": "0.6, 0.7"}, "Face detection takes three thresholds, one for each stage."),
        ({"model.preserve_full_frame": "maybe"}, "Keep the full frame is true or false."),
        ({"model.max_aspect_ratio": "9"}, "The widest aspect ratio must be between 1 and 4."),
        ({"model.name": " "}, "The CLIP model cannot be empty."),
        ({"renaming.format": "{grouping} - {caption}"},
         "The rename format must hold {index}, or every photo is given the same name."),
        ({"candidates.tags": "Kayak, Sea|Boats"},
         'A tag cannot contain "|": other programs read it as a break between levels. Use "/" instead.'),
        ({"paths.exiftool": "C:/Tools/exif\ttool.exe"},
         "ExifTool's path cannot contain a tab, a line break or another control character."),
        ({"paths.data_dir": "elsewhere"}, "There is no setting called paths.data_dir."),
        ({}, "Say which settings to change."),
        ({"renaming.format": "{index}", "faces.min_face_size": "-1"}, "The smallest face must be between 1 and 2000."),
    ]

    def test_each_refusal_says_why_in_the_validators_words_and_writes_nothing(self):
        library = Library(self.home.library("harbour.db"))
        library_actions.create(library.path)
        for values, message in self.CASES:
            with self.subTest(values=values):
                result = settings.change(library, values)
                self.assertEqual((message, 0), (result.refused, result.changed))
        self.assertEqual((settings.STAMPED, 1), (rows(library), len(history(library))))
        # Locked groups: each is named until acknowledged, a name that is no group is refused, an unchanged one needs nothing.
        result = settings.change(library, {"model.name": "ViT-B-32", "faces.min_face_size": "40", "candidates.tags": "Kayak"})
        self.assertEqual(["clip", "faces"], [g["group"] for g in result.details["unacknowledged"]])
        partly = settings.change(library, {"model.name": "ViT-B-32", "faces.min_face_size": "40"}, acknowledged=["clip"])
        self.assertEqual(["faces"], [g["group"] for g in partly.details["unacknowledged"]])
        unknown = settings.change(library, {"model.name": "ViT-B-32"}, acknowledged=["clip", "everything"])
        self.assertEqual("There is no group of settings called everything to acknowledge.", unknown.refused)
        self.assertEqual(1, settings.change(library, {"candidates.tags": "Kayak", "model.name": settings.DEFAULTS["model.name"]}).changed)
        self.assertEqual(2, len(history(library)), "the stamp and that one change")
        never = Library(self.home.library("never.db"))
        schema.ensure(never.path)
        self.assertIn("not been stamped", settings.change(never, {"faces.min_face_size": "40"}).refused)

    def test_a_reset_names_its_stamp_for_the_library_it_replaced(self):
        from click.testing import CliRunner

        import tagpup_cli
        library = Library(self.home.library("harbour.db"))
        library_actions.create(library.path)
        settings.change(library, {"candidates.tags": "Kayak, Lighthouse"})
        empty = os.path.join(self.home.root, "Empty")
        os.makedirs(empty)
        with mock.patch("tagpup.runtime._build_clip", side_effect=lambda s: mock.Mock(settings=dict(s))):
            result = CliRunner().invoke(tagpup_cli.cli, ["--db", library.path, "index", "--reset", empty], catch_exceptions=False)
        self.assertEqual(0, result.exit_code, result.output)
        [stamp] = history(library)
        self.assertEqual(settings.FROM_REPLACED, stamp["operation"])
        self.assertEqual("Kayak, Lighthouse", rows(library)["candidates.tags"])


if __name__ == "__main__":
    unittest.main()
