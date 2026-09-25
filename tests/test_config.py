"""tagpup.config: the home, where the libraries are, the machine's ExifTool, and the one
reading of an old config.ini, for stamping a library that holds no settings yet.

Each case here was a difference between two of the 26 places that read config.ini:
where it was, what a relative path meant, what a missing value meant, and which
ExifTool to run. The settings are each library's now (tagpup.services.settings;
tests/test_settings.py); what is left is the machine's.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "tests"))

import own_home  # noqa: E402
from tagpup import config  # noqa: E402


class WithAHome(unittest.TestCase):
    """Each test gets a TAGPUP_HOME of its own, and runs from another folder entirely."""

    def setUp(self):
        self.home = own_home.for_test(self)
        self.elsewhere = tempfile.mkdtemp(prefix="tagpup_cwd_")
        self.addCleanup(shutil.rmtree, self.elsewhere, True)
        previous = os.getcwd()
        os.chdir(self.elsewhere)
        self.addCleanup(os.chdir, previous)


class WhereThingsAre(WithAHome):
    def test_home_is_tagpup_home(self):
        self.assertEqual(os.path.normcase(config.home()), os.path.normcase(self.home.root))

    def test_without_tagpup_home_it_is_the_code_folder(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(os.path.normcase(config.home()), os.path.normcase(WORKSPACE_DIR))

    def test_the_libraries_are_in_data_in_the_home(self):
        self.assertEqual(config.data_dir(), os.path.join(self.home.root, "data"))
        self.assertEqual(config.library_path("family.db"), os.path.join(self.home.root, "data", "family.db"))

    def test_where_the_libraries_are_is_not_a_setting(self):
        # An old config.ini naming another data folder is not followed: a library is
        # found in data/ in its home, by every program alike.
        self.home.write_old_config({"paths": {"data_dir": self.elsewhere}})
        self.assertEqual(config.data_dir(), os.path.join(self.home.root, "data"))


class WhatAnOldConfigSays(WithAHome):
    def test_none_without_one(self):
        self.assertIsNone(config.config_ini())

    def test_each_setting_by_section_and_key(self):
        self.home.write_old_config({"model": {"name": "ViT-B-32"}, "faces": {"min_face_size": "32"}})
        self.assertEqual(config.config_ini(), {"model.name": "ViT-B-32", "faces.min_face_size": "32"})

    def test_the_file_is_read_as_utf8(self):
        # metadata.py read it as cp1252: "–" came back as "â€“".
        self.home.write_old_config({"renaming": {"format": "{grouping} – {index} – {caption}"}})
        self.assertEqual(config.config_ini()["renaming.format"], "{grouping} – {index} – {caption}")

    def test_percent_signs_are_not_interpolation(self):
        # The default ConfigParser, which metadata.py used, raises on a lone "%".
        tool = "%USERPROFILE%" + os.sep + "Tools" + os.sep + "exiftool.exe"
        self.home.write_old_config({"paths": {"exiftool": tool}})
        self.assertEqual(config.config_ini()["paths.exiftool"], tool)

    def test_an_exiftool_where_the_installer_puts_it_is_left_to_be_found(self):
        installed = os.path.join(self.elsewhere, "exiftool.exe")
        open(installed, "w").close()
        with mock.patch.object(config, "default_exiftool", return_value=installed):
            self.home.write_old_config({"paths": {"exiftool": installed.upper()}})
            self.assertEqual(config.config_ini()["paths.exiftool"], "")
            other = os.path.join(self.home.root, "exiftool.exe")
            open(other, "w").close()
            self.home.write_old_config({"paths": {"exiftool": other}})
            self.assertEqual(config.config_ini()["paths.exiftool"], other, "another program is kept")

    def test_another_folders(self):
        other = own_home.OwnHome()
        self.addCleanup(other.close)
        other.write_old_config({"model": {"name": "ViT-L-14"}})
        self.assertEqual(config.config_ini(other.root), {"model.name": "ViT-L-14"})


class WhichExifTool(WithAHome):
    def test_the_named_one_when_it_exists(self):
        tool = os.path.join(self.home.root, "exiftool.exe")
        open(tool, "w").close()
        self.assertEqual(config.exiftool_path(tool), tool)

    def test_one_on_path_when_the_named_one_is_missing(self):
        # The servers used the configured path regardless and failed; only the CLI
        # looked on PATH.
        with mock.patch("shutil.which", return_value=r"C:\Tools\exiftool.exe"):
            self.assertEqual(config.exiftool_path(os.path.join(self.home.root, "gone.exe")), r"C:\Tools\exiftool.exe")

    def test_the_named_one_when_there_is_none_anywhere(self):
        missing = os.path.join(self.home.root, "gone.exe")
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(config.exiftool_path(missing), missing, "the error should name it")

    def test_none_named_is_where_the_installer_puts_it(self):
        installed = os.path.join(self.home.root, "exiftool.exe")
        open(installed, "w").close()
        with mock.patch.object(config, "default_exiftool", return_value=installed):
            self.assertEqual(config.exiftool_path(""), installed)
            self.assertEqual(config.exiftool_path(None), installed)


class NothingWritesTheSettings(WithAHome):
    """The app kept "the library chosen last" in config.ini and wrote the file on every
    choice (docs/findings.md, #100). Now a library is the URL's, and the browser
    remembers; and nothing writes a config.ini at all."""

    def test_there_is_no_library_to_remember_nor_a_file_to_write(self):
        for gone in ("remember_library", "default_db", "write_file"):
            self.assertFalse(hasattr(config, gone), gone)


if __name__ == "__main__":
    unittest.main()
