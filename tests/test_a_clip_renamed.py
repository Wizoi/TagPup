"""A clip renamed as a photo is (C3; DECISIONS 2026-11-08, A5 cut A): Smart Rename of a day's selection, the Smart
Rename button beside one clip's name, and a Save that renames a Smart-Renamed clip after its title. Before its first
rename a clip keeps the name it had in its XMP-xmpMM:PreservedFileName, patched into its XMP in place by the journaled
write of its tags and title (field_values.writes names "name"; ExifTool is never asked to rewrite it), and the rename
itself moves no byte.

What the owner lives through: 557 photo_index folders hold clips and photos together (counted 2026-11-08), so a day's
selection renamed holds both, and the photos and clips are numbered together in the order taken, and undone from History
as the photos are. A clip the patch cannot place (two XMP boxes here; a clip over 4 GB holding no XMP) keeps its
name and its bytes, its number unused, with a plain sentence; the photos beside it are renamed. A rename stopped after the names were kept, run
again, renames with the name each had. A second library holding the folder, its row still at the clip's first name,
finds the clip again by its PreservedFileName (relink_photos). A clip of a folder the library does not hold (Just look)
is not written and keeps its name; the photo beside it is renamed.

A clip renamed a second time keeps the first name it kept, and writes no second one.

Real ffmpeg clips and the real ExifTool (tests/test_a_clip_tagged_through_the_journal.ALibraryHoldingAClip), Flask's
test client in a home of its own. Skipped, saying so, without ffmpeg or ExifTool. Fictional names only.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exiftool_fields  # noqa: E402
import corpus  # noqa: E402
import test_a_clip_patched_in_place as patched  # noqa: E402
import test_a_clip_tagged_through_the_journal as tagged  # noqa: E402
from view_library import make_jpeg  # noqa: E402

from tests import page_writes  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.files import field_values, images, names  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import file_changes, journal, relink_photos  # noqa: E402

EXIFTOOL = tagged.EXIFTOOL


class AClipRenamed(tagged.ALibraryHoldingAClip):

    def setUp(self):
        super().setUp()
        command = field_values._command

        def not_a_clip(et, photo_path, *rest):
            if images.is_clip(photo_path):
                raise AssertionError("ExifTool asked to rewrite a clip")
            return command(et, photo_path, *rest)

        rewrite = mock.patch.object(field_values, "_command", side_effect=not_a_clip)
        rewrite.start()
        self.addCleanup(rewrite.stop)

    def kept_name(self, path):
        with ExifToolSession(executable=EXIFTOOL) as et:
            return names.preserved_name(et.get_tags([path], tags=names.RENAME_FIELDS)[0])

    def path_of(self, photo_id):
        return self.vl.rows("SELECT path FROM photos WHERE id = ?", photo_id)[0][0]

    def listed(self, folder=None):
        return sorted(os.listdir(folder or self.folder))

    def renamed(self, photo_paths, grouping="Harbour Day", folder=None):
        return self.post("folder/rename-photos", {"folder_path": folder or self.folder, "grouping": grouping,
                                                  "photo_paths": photo_paths})

    def changes_of(self, photo_id):
        return [change for change, in self.vl.rows(
            "SELECT DISTINCT change_id FROM change_files WHERE photo_id = ? ORDER BY change_id", photo_id)]

    def test_a_days_selection_of_a_photo_and_a_clip_is_renamed_together_and_undone_from_history(self):
        photo, clip = self.index(self.photo_path, self.clip_path)
        before = self.listed()
        reply = self.renamed([self.photo_path, self.clip_path])
        self.assertEqual(200, reply.status_code, reply.get_json())
        moved = {paths.key(old): new for old, new in reply.get_json()["updated_paths"].items()}
        clip_now = moved[paths.key(self.clip_path)]
        self.assertTrue(os.path.basename(clip_now).startswith("Harbour Day"), clip_now)
        self.assertTrue(os.path.basename(moved[paths.key(self.photo_path)]).startswith("Harbour Day"))
        self.assertEqual(sorted(os.path.basename(new) for new in moved.values()), self.listed())
        self.assertEqual("jetty.mp4", self.kept_name(clip_now), "the clip keeps the name it had, in its XMP")
        self.assertEqual(paths.key(clip_now), paths.key(self.path_of(clip)), "its row follows the file")
        kept, renaming = self.changes_of(clip)
        self.assertEqual([kept, renaming], self.changes_of(photo), "one change of each step, the photo's and the clip's")
        undone = journal.undo(self.vl.library, renaming, apply=True, fields=exiftool_fields.opened(self, EXIFTOOL))
        self.assertEqual((2, []), (undone.changed, undone.errors))
        self.assertEqual(before, self.listed(), "both have their names back")
        self.assertEqual(paths.key(self.clip_path), paths.key(self.path_of(clip)))
        undone = journal.undo(self.vl.library, kept, apply=True, fields=exiftool_fields.opened(self, EXIFTOOL))
        self.assertEqual((2, []), (undone.changed, undone.errors))
        self.assertEqual("", self.kept_name(self.clip_path), "and the name it kept goes with its undo")

    def test_the_button_beside_a_clips_name_renames_it_and_a_save_renames_it_after_its_title(self):
        clip, = self.index(self.clip_path)
        reply = self.post("photo/smart-rename", {"path": self.clip_path, "grouping": "Harbour"})
        self.assertEqual(200, reply.status_code, reply.get_json())
        numbered = page_writes.saved(reply.get_json())["new_path"]
        self.assertTrue(os.path.basename(numbered).startswith("Harbour - 1"), numbered)
        self.assertEqual("jetty.mp4", self.kept_name(numbered))
        saved = self.post("photos/write", page_writes.save(self.vl.library, **{"path": numbered, "tags": [], "title": "Jetty at dusk"}))
        self.assertEqual((200, True), (saved.status_code, saved.get_json()["success"]), saved.get_json())
        titled = page_writes.saved(saved.get_json())["new_path"]
        self.assertTrue(os.path.basename(titled).startswith("Harbour - 1"), titled)
        self.assertIn("Jetty at dusk", os.path.basename(titled), "renamed after the title saved")
        self.assertEqual(sorted(["pier.jpg", os.path.basename(titled)]), self.listed())
        self.assertEqual(paths.key(titled), paths.key(self.path_of(clip)))
        self.assertEqual("jetty.mp4", self.kept_name(titled), "the first name is kept through every rename")

    def test_a_clip_the_patch_cannot_place_keeps_its_name_and_the_photo_beside_it_is_renamed(self):
        with open(self.clip_path, "ab") as handle:
            handle.write(patched.xmp_box(patched.OLD) + patched.xmp_box(patched.OLD))
        _photo, clip = self.index(self.photo_path, self.clip_path)
        clip_bytes = self.read_bytes(self.clip_path)
        reply = self.renamed([self.photo_path, self.clip_path])
        self.assertFalse(reply.get_json()["success"])
        self.assertIn(images.CLIP_NOT_PATCHED, page_writes.why(reply.get_json()))
        listed = self.listed()
        self.assertIn("jetty.mp4", listed, "the clip keeps its name: renamed without its first name it is lost to it")
        self.assertNotIn("pier.jpg", listed, "the photo beside it is renamed, its number taken")
        self.assertTrue(any(name.startswith("Harbour Day - 1") and name.endswith(".jpg") for name in listed), listed)
        self.assertEqual(clip_bytes, self.read_bytes(self.clip_path))
        self.assertEqual(paths.key(self.clip_path), paths.key(self.path_of(clip)))
        lone = self.post("photo/smart-rename", {"path": self.clip_path, "grouping": "Harbour"})
        self.assertEqual(400, lone.status_code, "a refusal, as a Just-look clip's is, not a 500")
        self.assertIn(images.CLIP_NOT_PATCHED, lone.get_json()["error"])
        self.assertEqual(clip_bytes, self.read_bytes(self.clip_path))

    def test_a_clip_renamed_a_second_time_keeps_the_first_name_it_kept(self):
        clip, = self.index(self.clip_path)
        first = self.renamed([self.clip_path], grouping="Harbour Day")
        self.assertEqual(200, first.status_code, first.get_json())
        once = first.get_json()["updated_paths"][self.clip_path]
        kept_then = self.changes_of(clip)
        self.assertEqual(2, len(kept_then), "the name kept, then the rename")
        second = self.renamed([once], grouping="Quay Day")
        self.assertEqual(200, second.status_code, second.get_json())
        twice = {paths.key(old): new for old, new in second.get_json()["updated_paths"].items()}[paths.key(once)]
        self.assertNotEqual(paths.key(once), paths.key(twice))
        self.assertTrue(os.path.basename(twice).startswith("Quay Day"), twice)
        self.assertEqual("jetty.mp4", self.kept_name(twice), "the first name, not the one it had the second time")
        with ExifToolSession(executable=EXIFTOOL) as et:
            every = et.execute("-a", "-s3", "-XMP-xmpMM:PreservedFileName", twice).split()
        every = [value for value in every if value != "XMP"]   # ExifTool's group line, not a value
        self.assertEqual(["jetty.mp4"], every, "no second PreservedFileName")
        self.assertEqual(3, len(self.changes_of(clip)), "only the rename is a change the second time: no name kept again")
        lone = self.post("photo/smart-rename", {"path": twice, "grouping": "Cove"})
        self.assertEqual(200, lone.status_code, lone.get_json())
        self.assertEqual("jetty.mp4", self.kept_name(lone.get_json()["new_path"]))

    def test_a_rename_stopped_after_the_names_were_kept_renames_with_them_when_run_again(self):
        self.index(self.photo_path, self.clip_path)
        rename = file_changes.rename
        with mock.patch.object(file_changes, "rename", side_effect=RuntimeError("the server stopped")):
            stopped = self.renamed([self.photo_path, self.clip_path])
        self.assertNotEqual(200, stopped.status_code)
        self.assertEqual(["jetty.mp4", "pier.jpg"], self.listed(), "nothing renamed")
        self.assertEqual("jetty.mp4", self.kept_name(self.clip_path), "the name was kept first")
        with mock.patch.object(file_changes, "rename", side_effect=rename):
            reply = self.renamed([self.photo_path, self.clip_path])
        self.assertEqual(200, reply.status_code, reply.get_json())
        clip_now = {paths.key(old): new for old, new in reply.get_json()["updated_paths"].items()}[paths.key(self.clip_path)]
        self.assertEqual("jetty.mp4", self.kept_name(clip_now))

    def test_a_second_library_holding_the_folder_finds_the_renamed_clip_by_the_name_it_kept(self):
        self.index(self.clip_path)
        reply = self.post("photo/smart-rename", {"path": self.clip_path, "grouping": "Harbour"})
        self.assertEqual(200, reply.status_code, reply.get_json())
        new_path = page_writes.saved(reply.get_json())["new_path"]
        with field_values.opened(EXIFTOOL) as fields:
            lookup, by_identity = relink_photos.claims_of([new_path], fields)
        pairs, unmatched = relink_photos.pair([paths.stored(self.clip_path)], lookup, by_identity, {}, set())
        self.assertEqual(([(paths.key(self.clip_path), paths.key(new_path))], []),
                         ([(paths.key(old), paths.key(new)) for old, new in pairs], unmatched))

    def test_a_clip_of_a_folder_the_library_does_not_hold_keeps_its_name_and_the_photo_beside_it_is_renamed(self):
        loose = os.path.join(self.home.root, "Elsewhere")
        os.makedirs(loose)
        photo_path, clip_path = os.path.join(loose, "slip.jpg"), os.path.join(loose, "slipway.mp4")
        make_jpeg(photo_path, size=(32, 32))
        corpus.clip(clip_path, ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-t", "1",
                                "-pix_fmt", "yuv420p"], tagged.TOOLS.ffmpeg)
        clip_bytes = self.read_bytes(clip_path)
        reply = self.renamed([photo_path, clip_path], folder=loose)
        self.assertFalse(reply.get_json()["success"])
        self.assertIn(images.CLIP_NOT_EDITED, page_writes.why(reply.get_json()))
        listed = self.listed(loose)
        self.assertIn("slipway.mp4", listed, "the clip keeps its name: no journal to settle its patch")
        self.assertNotIn("slip.jpg", listed, "the photo beside it is renamed")
        self.assertEqual(clip_bytes, self.read_bytes(clip_path))
        lone = self.post("photo/smart-rename", {"path": clip_path, "grouping": "Harbour"})
        self.assertEqual((400, images.CLIP_NOT_EDITED), (lone.status_code, lone.get_json()["error"]))


if __name__ == "__main__":
    unittest.main()
