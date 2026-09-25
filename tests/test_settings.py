"""A library's settings are its own: stamped once, changed only through the journal, and
what the runtime builds its models from (docs/ARCHITECTURE.md, phase 7.6).

They lived in config.ini, one file for the machine, which nothing in the pages showed:
a library opened on another machine, or with the file edited, was read with settings
it was not made with, and nothing said so. Now a new library is stamped with the
defaults, a library in use is stamped once from the config.ini beside it (so nothing
changes for it), and a change is a journaled change -- in the library's history, and
undoable -- refused through the validator.

The libraries here are made as the apps make them (tagpup.services.libraries.create) or
as a library from before the settings were in it stands (its tables migrated, no
settings rows), never through the stamping under test.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import own_home  # noqa: E402

from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings  # noqa: E402
from tagpup.store import db, schema  # noqa: E402

#: An old home's config.ini that differs from the defaults in every setting it names --
#: and names two that are no settings of a library's.
OLD_CONFIG = {
    "paths": {"exiftool": "C:/Tools/exiftool-12.exe", "data_dir": "elsewhere", "default_db": "harbour"},
    "model": {"name": "ViT-B-32", "pretrained": "openai", "preserve_full_frame": "false",
              "max_aspect_ratio": "2", "force_image_size": ""},
    "candidates": {"tags": "Kayak, Lighthouse"},
    "faces": {"min_face_size": "32", "confidence_threshold": "0.9", "mtcnn_thresholds": "0.5, 0.6, 0.7"},
    "renaming": {"format": "{index} ~ {grouping} ~ {caption}"},
}


def settings_rows(library):
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return dict(conn.execute("SELECT key, value FROM settings").fetchall())
    finally:
        conn.close()


def history(library):
    return journal_service.history(library, limit=100)["changes"]


class ALibrary(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)

    def new_library(self, name="harbour.db"):
        """A library as the picker's Create makes it."""
        path = self.home.library(name)
        self.assertEqual(1, library_actions.create(path).changed)
        return Library(path)

    def library_in_use(self, name="harbour.db"):
        """A library from before the settings were in it: its tables brought up to date,
        and no settings."""
        path = self.home.library(name)
        schema.ensure(path)
        return Library(path)


class Stamping(ALibrary):
    def test_a_new_library_holds_the_defaults_whatever_config_ini_says(self):
        self.home.write_old_config(OLD_CONFIG)
        library = self.new_library()
        self.assertEqual(settings_rows(library), settings.DEFAULTS)
        [stamp] = history(library)
        self.assertEqual(stamp["operation"], settings.WITH_DEFAULTS)
        self.assertEqual(stamp["rows"], {"settings": {"insert": len(settings.DEFAULTS)}})

    def test_a_library_in_use_is_stamped_once_from_config_ini(self):
        self.home.write_old_config(OLD_CONFIG)
        library = self.library_in_use()
        self.assertEqual({}, settings_rows(library))

        found = runtimes.library_settings(library)

        self.assertTrue(found.stamped)
        self.assertEqual(found.embedder, {"model_name": "ViT-B-32", "pretrained": "openai",
                                          "preserve_full_frame": False, "max_aspect_ratio": 2.0,
                                          "force_image_size": None})
        self.assertEqual(found.faces, {"min_face_size": 32, "confidence_threshold": 0.9,
                                       "mtcnn_thresholds": [0.5, 0.6, 0.7]})
        self.assertEqual(found.candidate_words, ["Kayak", "Lighthouse"])
        self.assertEqual(found.rename_format, "{index} ~ {grouping} ~ {caption}")
        self.assertEqual(found.exiftool, "C:/Tools/exiftool-12.exe")
        self.assertNotIn("paths.data_dir", settings_rows(library), "where the libraries are is not a setting")
        [stamp] = history(library)
        self.assertEqual(stamp["operation"], settings.FROM_CONFIG)
        self.assertEqual(stamp["summary"]["refused"], [])

        # Once: the file edited afterwards changes nothing for the library.
        self.home.write_old_config({"model": {"name": "ViT-L-14"}})
        self.assertEqual(runtimes.library_settings(library).embedder["model_name"], "ViT-B-32")
        self.assertEqual(1, len(history(library)))

    def test_without_config_ini_a_library_in_use_gets_the_defaults(self):
        library = self.library_in_use()
        self.assertEqual(runtimes.library_settings(library).values, settings.DEFAULTS)
        self.assertEqual([settings.WITH_DEFAULTS], [c["operation"] for c in history(library)])

    def test_a_value_the_validator_refuses_is_stamped_as_its_default(self):
        self.home.write_old_config({"faces": {"min_face_size": "twenty", "confidence_threshold": "0.9"}})
        library = self.library_in_use()
        found = runtimes.library_settings(library)
        self.assertEqual(found["faces.min_face_size"], settings.DEFAULTS["faces.min_face_size"])
        self.assertEqual(found["faces.confidence_threshold"], "0.9")
        self.assertEqual(history(library)[0]["summary"]["refused"], ["faces.min_face_size"])

    def test_a_look_writes_nothing(self):
        # The MCP server's inspections and the doctor read a library without writing.
        self.home.write_old_config(OLD_CONFIG)
        library = self.library_in_use()
        found = runtimes.peek_settings(library)
        self.assertFalse(found.stamped)
        self.assertEqual(found.embedder["model_name"], "ViT-B-32")
        self.assertEqual({}, settings_rows(library))
        self.assertEqual([], history(library))

    def test_a_library_stamped_meanwhile_is_left_as_it_is(self):
        library = self.new_library()
        result = settings.stamp(library, {"model.name": "ViT-B-32"})
        self.assertEqual(0, result.changed)
        self.assertEqual(settings_rows(library), settings.DEFAULTS)


class Changing(ALibrary):
    def test_a_change_is_in_the_history_and_is_undone(self):
        library = self.new_library()
        result = settings.change(library, {"renaming.format": "{grouping} {index}", "candidates.tags": "Kayak"})
        self.assertIsNone(result.refused)
        self.assertEqual(2, result.changed)
        self.assertFalse(result.details["locked"])
        self.assertEqual(settings_rows(library)["renaming.format"], "{grouping} {index}")
        newest = history(library)[0]
        self.assertEqual((newest["id"], newest["operation"]), (result.details["change"], settings.CHANGE))
        self.assertEqual(newest["rows"], {"settings": {"update": 2}})

        rehearsal = journal_service.undo(library, result.details["change"])
        self.assertTrue(rehearsal.details["rehearsal"]["exact"], rehearsal.details)
        undone = journal_service.undo(library, result.details["change"], apply=True)
        self.assertIsNone(undone.refused)
        self.assertEqual(settings_rows(library), settings.DEFAULTS)

    def test_a_locked_setting_says_so(self):
        library = self.new_library()
        result = settings.change(library, {"model.name": "ViT-B-32", "model.pretrained": "openai"})
        self.assertEqual(2, result.changed)
        self.assertTrue(result.details["locked"])

    def test_values_are_kept_as_the_validator_reads_them(self):
        library = self.new_library()
        settings.change(library, {"model.preserve_full_frame": False, "faces.min_face_size": 40})
        rows = settings_rows(library)
        self.assertEqual((rows["model.preserve_full_frame"], rows["faces.min_face_size"]), ("false", "40"))

    def test_the_same_value_writes_nothing(self):
        library = self.new_library()
        result = settings.change(library, {"faces.min_face_size": settings.DEFAULTS["faces.min_face_size"]})
        self.assertEqual(0, result.changed)
        self.assertIsNone(result.refused)
        self.assertEqual(1, len(history(library)))


class Refusing(ALibrary):
    """Each refusal writes nothing, and says why in the validator's words."""

    CASES = [
        ({"faces.min_face_size": "twenty"}, "The smallest face is a whole number of pixels."),
        ({"faces.min_face_size": "0"}, "The smallest face must be between 1 and 2000."),
        ({"faces.confidence_threshold": "1.5"}, "The confidence must be between 0 and 1."),
        ({"faces.mtcnn_thresholds": "0.6, 0.7"}, "Face detection takes three thresholds, one for each stage."),
        ({"model.preserve_full_frame": "maybe"}, "Keep the full frame is true or false."),
        ({"model.max_aspect_ratio": "9"}, "The widest aspect ratio must be between 1 and 4."),
        ({"model.force_image_size": "big"}, "The image size is a whole number of pixels."),
        ({"model.name": " "}, "The CLIP model cannot be empty."),
        ({"renaming.format": "{grouping} - {caption}"},
         "The rename format must hold {index}, or every photo is given the same name."),
        ({"candidates.tags": "Kayak, Sea|Boats"},
         'A tag cannot contain "|": other programs read it as a break between levels. Use "/" instead.'),
        ({"paths.exiftool": "C:/Tools/exif\ttool.exe"},
         "ExifTool's path cannot contain a tab, a line break or another control character."),
        ({"paths.data_dir": "elsewhere"}, "There is no setting called paths.data_dir."),
        ({}, "Say which settings to change."),
        # One bad value refuses the good one beside it.
        ({"renaming.format": "{index}", "faces.min_face_size": "-1"}, "The smallest face must be between 1 and 2000."),
    ]

    def test_each_refusal(self):
        library = self.new_library()
        for values, message in self.CASES:
            with self.subTest(values=values):
                result = settings.change(library, values)
                self.assertEqual(result.refused, message)
                self.assertEqual(0, result.changed)
                self.assertEqual(settings_rows(library), settings.DEFAULTS)
        self.assertEqual(1, len(history(library)), "a refusal was journaled")

    def test_a_library_never_stamped(self):
        library = self.library_in_use()
        result = settings.change(library, {"faces.min_face_size": "40"})
        self.assertIn("not been stamped", result.refused)
        self.assertEqual({}, settings_rows(library))

    def test_every_refusal_message_is_the_validators(self):
        for values, message in self.CASES:
            for key, value in values.items():
                if key in validation.SETTINGS and validation.problem(validation.setting_kind(key), value):
                    with self.subTest(key=key):
                        self.assertEqual(validation.problem(validation.setting_kind(key), value), message)


class FakeModel:
    def __init__(self, settings):
        self.settings = dict(settings)


class TheRuntimeKeysModelsBySettings(ALibrary):
    def setUp(self):
        super().setUp()
        self.built = {"clip": [], "faces": []}
        self.runtime = Runtime(build_clip=lambda s: self.build("clip", s), build_faces=lambda s: self.build("faces", s))
        self.harbour = self.new_library("harbour.db")
        self.meadow = self.new_library("meadow.db")
        self.orchard = self.new_library("orchard.db")
        settings.change(self.meadow, {"model.name": "ViT-B-32", "model.pretrained": "openai",
                                      "faces.min_face_size": "40"})

    def build(self, kind, settings_given):
        model = FakeModel(settings_given)
        self.built[kind].append(model)
        return model

    def test_two_libraries_on_one_model_share_it(self):
        self.assertIs(self.runtime.clip(self.harbour), self.runtime.clip(self.orchard))
        self.assertIs(self.runtime.faces(self.harbour), self.runtime.faces(self.orchard))
        self.assertEqual(1, len(self.built["clip"]))
        self.assertEqual(1, len(self.built["faces"]))

    def test_a_library_on_another_gets_its_own(self):
        harbour, meadow = self.runtime.clip(self.harbour), self.runtime.clip(self.meadow)
        self.assertIsNot(harbour, meadow)
        self.assertEqual(harbour.settings["model_name"], "ViT-H-14")
        self.assertEqual(meadow.settings["model_name"], "ViT-B-32")
        self.assertEqual(self.runtime.faces(self.meadow).settings["min_face_size"], 40)
        self.assertEqual(self.runtime.faces(self.harbour).settings["min_face_size"], 20)
        self.assertNotEqual(self.runtime.model_key(self.harbour), self.runtime.model_key(self.meadow))

    def test_a_changed_setting_is_seen_by_the_next_ask(self):
        self.runtime.clip(self.harbour)
        settings.change(self.harbour, {"model.name": "ViT-L-14"})
        self.assertEqual(self.runtime.clip(self.harbour).settings["model_name"], "ViT-L-14")
        self.assertEqual(2, len(self.built["clip"]))

    def test_each_librarys_photo_index_is_under_its_model(self):
        self.assertEqual(self.runtime.photo_index(self.meadow).model, self.runtime.model_key(self.meadow))
        before = self.runtime.photo_index(self.harbour)
        self.assertEqual(before.model, self.runtime.model_key(self.harbour))
        settings.change(self.harbour, {"model.force_image_size": "224"})
        after = self.runtime.photo_index(self.harbour)
        self.assertEqual(after.model, self.runtime.model_key(self.harbour))
        self.assertNotEqual(before.model, after.model)
        after.close()
        self.runtime.photo_index(self.meadow).close()

    def test_a_run_gets_its_librarys_words_and_models(self):
        from unittest import mock
        settings.change(self.meadow, {"candidates.tags": "Kayak, Lighthouse"})
        with mock.patch("tagpup.services.suggester.model_for_run") as model_for_run:
            self.runtime.begin(self.meadow)
        photo_index, clip, faces, words = model_for_run.call_args.args
        self.assertEqual(words, ["Kayak", "Lighthouse"])
        self.assertEqual(clip.settings["model_name"], "ViT-B-32")
        self.assertEqual(faces.settings["min_face_size"], 40)
        photo_index.close()

    def test_warming_up_builds_each_set_once_and_stamps_nothing(self):
        loaded = []
        FakeModel.load = lambda model: loaded.append(model)
        FakeModel.embed_text = lambda model, text: None
        self.addCleanup(delattr, FakeModel, "load")
        self.addCleanup(delattr, FakeModel, "embed_text")
        unstamped = self.library_in_use("quarry.db")
        self.runtime.warm_up([self.harbour, self.meadow, self.orchard, unstamped])
        self.assertEqual(2, len(self.built["clip"]))
        self.assertEqual(2, len(self.built["faces"]))
        self.assertEqual({}, settings_rows(unstamped))


if __name__ == "__main__":
    unittest.main()
