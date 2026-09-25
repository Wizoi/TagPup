"""tagpup.config: one reading of config.ini, relative to one home, for every program.

Each case here was a difference between two of the 26 places that read the file:
where it was, what a relative path meant, what a missing value meant, and which
ExifTool to run.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from tagpup import config  # noqa: E402


class WithAHome(unittest.TestCase):
    """Each test gets a TAGPUP_HOME of its own, and runs from another folder entirely."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tagpup_home_")
        self.elsewhere = tempfile.mkdtemp(prefix="tagpup_cwd_")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.addCleanup(shutil.rmtree, self.elsewhere, True)
        patcher = mock.patch.dict(os.environ, {"TAGPUP_HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        previous = os.getcwd()
        os.chdir(self.elsewhere)
        self.addCleanup(os.chdir, previous)

    def write_config(self, text):
        with open(config.config_path(self.home), "w", encoding="utf-8") as handle:
            handle.write(text)


class WhereThingsAre(WithAHome):
    def test_home_is_tagpup_home(self):
        self.assertEqual(os.path.normcase(config.home()), os.path.normcase(self.home))
        self.assertEqual(os.path.dirname(config.config_path()), config.home())

    def test_without_tagpup_home_it_is_the_code_folder(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(os.path.normcase(config.home()), os.path.normcase(WORKSPACE_DIR))

    def test_a_relative_data_dir_is_under_home_not_the_working_directory(self):
        # The servers resolved it against the code, the launchers and the CLI against
        # the working directory, and runner.py ignored it and used "data".
        self.write_config("[paths]\ndata_dir = libraries\n")
        self.assertEqual(config.data_dir(), os.path.join(self.home, "libraries"))
        self.assertEqual(config.library_path("family.db"),
                         os.path.join(self.home, "libraries", "family.db"))

    def test_an_absolute_data_dir_is_used_as_it_is(self):
        self.write_config("[paths]\ndata_dir = %s\n" % self.elsewhere)
        self.assertEqual(config.data_dir(), self.elsewhere)

    def test_nothing_configured_means_the_defaults(self):
        self.assertEqual(config.data_dir(), os.path.join(self.home, "data"))
        self.assertEqual(config.rename_format(), "{grouping} - {index} - {caption}")
        self.assertEqual(config.candidate_tags()[:3], ["Landscape", "Portrait", "Nature"])

    def test_the_defaults_are_the_example(self):
        """config.ini is not in git. Without one, the settings must be the project's: a
        different model makes the CLI's index clear every embedding in the library."""
        import configparser

        example = configparser.ConfigParser(interpolation=None)
        example.read(os.path.join(WORKSPACE_DIR, "config.example.ini"), encoding="utf-8")
        written = {section: dict(example[section]) for section in example.sections()}
        written["paths"].pop("exiftool")   # the platform decides: config.default_exiftool()
        self.assertEqual(written, config.DEFAULTS)


class WhatTheSettingsSay(WithAHome):
    def test_the_file_is_read_as_utf8(self):
        # metadata.py read it as cp1252: "–" came back as "â€“".
        self.write_config("[renaming]\nformat = {grouping} – {index} – {caption}\n")
        self.assertEqual(config.rename_format(), "{grouping} – {index} – {caption}")

    def test_percent_signs_are_not_interpolation(self):
        # The default ConfigParser, which metadata.py used, raises on a lone "%".
        self.write_config("[paths]\nexiftool = %USERPROFILE%\\Tools\\exiftool.exe\n")
        self.assertEqual(config.load().get("paths", "exiftool"),
                         "%USERPROFILE%\\Tools\\exiftool.exe")

    def test_embedder_settings_are_what_clip_embedder_takes(self):
        self.write_config("[model]\nname = ViT-H-14\npretrained = laion2b_s32b_b79k\n"
                          "preserve_full_frame = true\nmax_aspect_ratio = 1.4\n"
                          "force_image_size = 512\n")
        self.assertEqual(config.embedder_settings(), {
            "model_name": "ViT-H-14",
            "pretrained": "laion2b_s32b_b79k",
            "preserve_full_frame": True,
            "max_aspect_ratio": 1.4,
            "force_image_size": 512,
        })

    def test_no_forced_image_size_is_none(self):
        self.write_config("[model]\nforce_image_size =\n")
        self.assertIsNone(config.embedder_settings()["force_image_size"])

    def test_candidate_tags_are_trimmed_and_in_order(self):
        self.write_config("[candidates]\ntags = Beach , Snow,, Harbour\n")
        self.assertEqual(config.candidate_tags(), ["Beach", "Snow", "Harbour"])

    def test_face_thresholds_that_do_not_parse_get_the_default(self):
        self.write_config("[faces]\nmin_face_size = 32\nmtcnn_thresholds = high, higher\n")
        settings = config.face_settings()
        self.assertEqual(settings["min_face_size"], 32)
        self.assertEqual(settings["mtcnn_thresholds"], [0.6, 0.7, 0.7])


class WhichExifTool(WithAHome):
    def test_the_configured_one_when_it_exists(self):
        tool = os.path.join(self.home, "exiftool.exe")
        open(tool, "w").close()
        self.write_config("[paths]\nexiftool = %s\n" % tool)
        self.assertEqual(config.exiftool_path(), tool)

    def test_one_on_path_when_the_configured_one_is_missing(self):
        # The servers used the configured path regardless and failed; only the CLI
        # looked on PATH.
        self.write_config("[paths]\nexiftool = %s\n" % os.path.join(self.home, "gone.exe"))
        with mock.patch("shutil.which", return_value=r"C:\Tools\exiftool.exe"):
            self.assertEqual(config.exiftool_path(), r"C:\Tools\exiftool.exe")

    def test_the_configured_one_when_there_is_none_anywhere(self):
        missing = os.path.join(self.home, "gone.exe")
        self.write_config("[paths]\nexiftool = %s\n" % missing)
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(config.exiftool_path(), missing, "the error should name it")


class NothingWritesTheSettings(WithAHome):
    """The app kept "the library chosen last" in config.ini and wrote the file on every
    choice (docs/findings.md, #100). Now a library is the URL's, and the browser
    remembers; config.ini is one installation's, written by its owner alone."""

    def test_there_is_no_library_to_remember(self):
        self.assertFalse(hasattr(config, "remember_library"))
        self.assertFalse(hasattr(config, "default_db"))
        self.assertNotIn("default_db", config.DEFAULTS["paths"])

    def test_a_file_written_for_a_sandbox_has_lf_line_endings(self):
        config.write_file({"paths": {"data_dir": "data"}}, folder=self.home)
        with open(config.config_path(), "rb") as handle:
            self.assertNotIn(b"\r\n", handle.read())

    def test_a_config_file_can_be_written_for_another_home(self):
        sandbox = tempfile.mkdtemp(prefix="tagpup_sandbox_")
        self.addCleanup(shutil.rmtree, sandbox, True)
        config.write_file({"paths": {"data_dir": "measured"}}, folder=sandbox)
        self.assertEqual(config.read_file(sandbox).get("paths", "data_dir"), "measured")
        self.assertFalse(os.path.exists(config.config_path()), "it wrote this home too")


class TheRenameSyncReadsTheConfiguredFormat(WithAHome):
    """metadata.sync_title_to_filename read config.ini from the working directory.

    Started from anywhere but the repository, it fell back to the default format and
    named the file differently from Smart Rename.
    """

    def test_it_uses_the_format_in_home_from_any_working_directory(self):
        from tagpup.files import metadata

        self.write_config("[renaming]\nformat = {index} ~ {grouping} ~ {caption}\n")
        photos = tempfile.mkdtemp(prefix="tagpup_photos_")
        self.addCleanup(shutil.rmtree, photos, True)
        photo = os.path.join(photos, "Harbour - 007 - Boats.jpg")
        open(photo, "wb").close()

        session = mock.MagicMock()
        session.__enter__.return_value.get_tags.return_value = [
            {"XMP-xmpMM:PreservedFileName": "IMG_0007.jpg"}]
        with mock.patch("tagpup.files.metadata.ExifToolSession", return_value=session):
            renamed = metadata.sync_title_to_filename(photo, "Gulls", r"C:\Tools\exiftool.exe",
                                                     config.rename_format())

        self.assertEqual(os.path.basename(renamed), "007 ~ Harbour ~ Gulls.jpg")
        self.assertTrue(os.path.exists(renamed))


if __name__ == "__main__":
    unittest.main()
