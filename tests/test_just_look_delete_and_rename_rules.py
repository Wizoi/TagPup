"""The Just-look review's findings #520-#525 (docs/findings.md).

#520  Delete sends only an existing regular photo FILE to the Recycle Bin, and says the truth about
      a network share, where Windows deletes for good (checked here by test on a UNC path to local
      storage: SHFileOperation reports success and the Bin holds nothing).
#521  A folder with a row directly in it is held whatever the ignored list says.
#523  The file-only path checks a file's tail, not its whole picture, except for a rotate.
#524  Smart Rename splits held from not-held once; a mixed one that fails part-way says what changed.
#525  The failure says how to recover.
"""
import ctypes
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import damaged_photos  # noqa: E402
import library_dump  # noqa: E402
import test_just_look_edits as jl  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import images, recycle_bin  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.services import settings as settings_service  # noqa: E402


def refuses_to_send(*_args):
    raise AssertionError("something was sent to the Recycle Bin")


def unc_of(path):
    """\\\\localhost\\C$\\... for a local file, or None when the administrative share is not there."""
    unc = "\\\\localhost\\" + path[0] + "$" + path[2:]
    return unc if os.path.exists(unc) else None


class DeleteSendsOnlyPhotoFiles(jl.Base):
    def setUp(self):
        super().setUp()
        self.text = os.path.join(self.lighthouse, "notes.txt")
        with open(self.text, "w") as handle:
            handle.write("not a photo")
        self.held_text = os.path.join(self.regatta, "notes.txt")
        with open(self.held_text, "w") as handle:
            handle.write("not a photo")
        self.before = library_dump.dump(self.library.path)

    def short(self, path):
        buffer = ctypes.create_unicode_buffer(300)
        ctypes.windll.kernel32.GetShortPathNameW(path, buffer, 300)
        return buffer.value or path

    def refused(self, path):
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=refuses_to_send):
            return self.post("/photo/delete", {"path": path})

    def test_what_is_not_a_photo_file_is_refused_in_a_folder_the_library_does_not_hold(self):
        for path in (self.lighthouse, self.text, self.short(self.text), self.loose[0] + os.sep,
                     os.path.join(self.lighthouse, "gone.jpg")):
            reply = self.refused(path)
            self.assertIn(reply.status_code, (400, 409), (path, reply.data))
            self.assertFalse(reply.get_json()["success"])
        self.assertTrue(os.path.isdir(self.lighthouse) and os.path.exists(self.text))
        self.assertEqual(3, len([n for n in os.listdir(self.lighthouse) if n.endswith(".jpg")]))
        self.assert_library_unchanged()

    def test_and_in_a_folder_it_holds(self):
        for path in (self.regatta, self.held_text, self.short(self.held_text), self.held[0] + os.sep):
            reply = self.refused(path)
            self.assertIn(reply.status_code, (400, 409), (path, reply.data))
        self.assertTrue(os.path.isdir(self.regatta) and os.path.exists(self.held_text))
        self.assertEqual(2, self.rows("SELECT COUNT(*) FROM photos")[0][0])

    def test_the_recycle_bin_function_refuses_a_directory_and_a_text_file_itself(self):
        folder = os.path.join(self.home.root, "Keep")
        os.makedirs(folder)
        text = os.path.join(folder, "keep.txt")
        with open(text, "w") as handle:
            handle.write("x")
        for path in (folder, text, os.path.join(folder, "missing.jpg")):
            with self.assertRaises(ValueError, msg=path):
                recycle_bin.send_to_recycle_bin(path)
        self.assertTrue(os.path.exists(text))

    def test_a_photo_file_still_goes(self):
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=lambda p: os.remove(p) or True):
            self.assertEqual(200, self.post("/photo/delete", {"path": self.loose[0]}).status_code)
            self.assertEqual(200, self.post("/photo/delete", {"path": self.held[0]}).status_code)


class ANetworkShareHasNoRecycleBin(jl.Base):
    def test_which_places_go_to_the_bin(self):
        self.assertFalse(recycle_bin.goes_to_bin("\\\\server\\share\\a.jpg"))
        self.assertFalse(recycle_bin.goes_to_bin("\\\\?\\UNC\\server\\share\\a.jpg"))
        self.assertTrue(recycle_bin.goes_to_bin(self.loose[0]))
        with mock.patch("ctypes.windll.kernel32.GetDriveTypeW", return_value=4, create=True):
            self.assertFalse(recycle_bin.goes_to_bin("Z:\\Photos\\a.jpg"), "a mapped network drive")

    def test_a_unc_path_to_local_storage_goes_through_this_pc_and_the_reply_says_where_it_restores_to(self):
        unc = unc_of(self.loose[0])
        if unc is None:
            self.skipTest("the administrative share \\\\localhost\\%s$ is not available" % self.loose[0][0])
        binned = []
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=lambda p: binned.append(p) or os.remove(p) or True):
            reply = self.post("/photo/delete", {"path": unc})
        self.assertEqual(200, reply.status_code, reply.data)
        body = reply.get_json()
        self.assertTrue(body["through_this_pc"], body)
        self.assertNotIn("permanently", body["message"])
        self.assertIn("TagPup deleted from shares", body["message"])
        self.assertEqual([recycle_bin.mirror_of(unc)], binned, "the copy, under the test home's Downloads, went to the Bin")
        self.assertFalse(os.path.exists(self.loose[0]))
        self.assert_library_unchanged()

    def test_a_local_delete_says_the_bin(self):
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=lambda p: os.remove(p) or True):
            body = self.post("/photo/delete", {"path": self.loose[0]}).get_json()
        self.assertFalse(body["through_this_pc"])
        self.assertIn("Recycle Bin", body["message"])

    def test_the_folder_membership_says_before_the_delete_whether_it_would_be_permanent(self):
        found = library_actions.membership(self.library, self.lighthouse)
        self.assertFalse(found["permanent_delete"])
        unc = unc_of(self.lighthouse)
        if unc is not None:
            self.assertTrue(library_actions.membership(self.library, unc)["permanent_delete"])


class AFolderWithRowsIsHeldWhateverIsIgnored(jl.Base):
    def setUp(self):
        super().setUp()
        settings_service.of(self.library)
        self.assertTrue(settings_service.change(self.library, {settings_service.IGNORED: self.regatta}).ok)
        self.before = library_dump.dump(self.library.path)

    def test_split_holds_it_and_an_ignored_folder_without_rows_stays_not_held(self):
        held, loose = library_actions.split(self.library, [self.held[0], self.scan])
        self.assertEqual(([self.held[0]], [self.scan]), (held, loose))
        ignored_empty = os.path.join(self.home.root, "Pictures", "Empty")
        photo = damaged_photos.whole_jpeg(os.path.join(ignored_empty, "e.jpg")) and os.path.join(ignored_empty, "e.jpg")
        self.assertTrue(settings_service.change(
            self.library, {settings_service.IGNORED: "%s\n%s" % (self.regatta, ignored_empty)}).ok)
        self.assertEqual(([], [photo]), library_actions.split(self.library, [photo]))

    def test_save_rename_rotate_and_delete_act_as_held(self):
        photo = self.held[0]
        body = self.post("/photo/save-metadata", {"path": photo, "title": "Start", "tags": ["A/B"]}).get_json()
        self.assertEqual((0, 1), (body["file_only"], body["with_rows"]), body)
        self.assertIn("A/B", self.rows("SELECT tags FROM photos WHERE path = ?", (photo,))[0][0])
        body = self.post("/photo/rotate", {"path": photo, "direction": "left"}).get_json()
        self.assertEqual((0, 1), (body["file_only"], body["with_rows"]), body)
        reply = self.post("/folder/rename-photos", {"folder_path": self.regatta, "grouping": "Regatta",
                                                   "photo_paths": self.held})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual((0, 2, 2), (reply.get_json()["file_only"], reply.get_json()["with_rows"],
                                     reply.get_json()["index_rows_moved"]))
        names = sorted(os.path.basename(row[0]) for row in self.rows("SELECT path FROM photos"))
        self.assertTrue(names[0].startswith("Regatta - 1") and names[1].startswith("Regatta - 2"), names)
        on_disk = sorted(n for n in os.listdir(self.regatta) if n.endswith(".jpg"))
        self.assertEqual(names, on_disk, "the rows do not follow the files")
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=lambda p: os.remove(p) or True):
            body = self.post("/photo/delete", {"path": os.path.join(self.regatta, names[0])}).get_json()
        self.assertEqual((0, 1), (body["file_only"], body["with_rows"]))
        self.assertEqual(1, self.rows("SELECT COUNT(*) FROM photos")[0][0], "the row outlived the file")


class TheFileOnlyCheckIsCheap(jl.Base):
    def test_a_save_a_tag_write_a_shift_and_a_rename_decode_nothing(self):
        with mock.patch.object(images, "zero_tail_if_whole", side_effect=AssertionError("decoded in full")), \
                mock.patch.object(images, "opened", side_effect=AssertionError("decoded in full")):
            self.assertEqual(200, self.post("/photo/save-metadata", {"path": self.loose[0], "title": "x",
                                                                    "tags": ["A/B"]}).status_code)
            self.assertEqual(200, self.post("/photos/bulk-tags", {"paths": self.loose, "add_tags": ["C/D"],
                                                                 "remove_tags": []}).status_code)
            self.assertEqual(200, self.post("/folder/time-shift", {"folder_path": self.lighthouse,
                                                                  "camera_model": "All Cameras",
                                                                  "shift_minutes": 5}).status_code)
            self.assertEqual(200, self.post("/folder/rename-photos", {
                "folder_path": self.lighthouse, "grouping": "Lighthouse", "photo_paths": self.loose}).status_code)

    def test_a_rotate_decodes_the_picture(self):
        with mock.patch.object(images, "zero_tail_if_whole", wraps=images.zero_tail_if_whole) as decoded:
            self.assertEqual(200, self.post("/photo/rotate", {"path": self.loose[0], "direction": "left"}).status_code)
        self.assertEqual(1, decoded.call_count)

    def test_a_zero_filled_png_and_an_empty_file_are_found_by_the_tail(self):
        from PIL import Image
        png = os.path.join(self.lighthouse, "zero filled.png")
        Image.new("RGB", (64, 64), "red").save(png)
        with open(png, "ab") as handle:
            handle.write(bytes(images.ZERO_TAIL + 10))
        empty = os.path.join(self.lighthouse, "empty.jpg")
        open(empty, "wb").close()
        for path in (png, empty):
            reply = self.post("/photo/save-metadata", {"path": path, "title": "x", "tags": []})
            self.assertEqual(409, reply.status_code, (path, reply.data))

    def test_a_whole_jpeg_with_camera_padding_passes(self):
        photo = self.loose[0]
        with open(photo, "ab") as handle:
            handle.write(bytes(16))
        self.assertEqual(200, self.post("/photo/save-metadata", {"path": photo, "title": "x", "tags": []}).status_code)


class ASmartRenameSplitsOnce(jl.Base):
    def test_one_split_and_the_folder_added_between_calls_leaves_no_orphan(self):
        real = library_actions.split
        calls = []

        def adding_after_the_first(library, photo_paths):
            calls.append(list(photo_paths))
            found = real(library, photo_paths)
            if len(calls) == 1:
                library_actions.record_added(library, [self.lighthouse])
            return found

        with mock.patch.object(library_actions, "split", adding_after_the_first):
            result = photo_actions.smart_rename(self.library, self.loose, "Lighthouse", "{grouping} - {index} - {caption}",
                                                jl.EXIFTOOL)
        self.assertEqual(1, len(calls), "Smart Rename split more than once")
        self.assertTrue(result.ok, result.message())
        self.assertEqual(0, result.details["with_rows"])
        self.assertEqual(3, result.details["file_only"])
        self.assertEqual(0, self.rows("SELECT COUNT(*) FROM changes WHERE operation LIKE 'smart rename%'")[0][0],
                         "a change was journaled for file-only photos")

    def test_the_second_part_failing_answers_what_changed_and_the_page_gets_the_new_names(self):
        from tagpup.files import names as file_names
        real = file_names.rename_all
        calls = []

        def second_fails(renames, aside=None):
            calls.append(renames)
            if len(calls) == 2:
                raise file_names.RenameFailed(OSError("the share went away"), [])
            return real(renames, aside=aside)

        with mock.patch.object(file_names, "rename_all", second_fails):
            reply = self.post("/folder/rename-photos", {"folder_path": self.regatta, "grouping": "Regatta",
                                                       "photo_paths": self.held + [self.scan]})
        self.assertEqual(500, reply.status_code, reply.data)
        body = reply.get_json()
        self.assertEqual(2, len(body["updated_paths"]), "what DID change is not answered")
        self.assertEqual(sorted(["Regatta - 1.jpg", "Regatta - 2.jpg"]),
                         sorted(os.path.basename(p["path"]) for p in body["updated_photos"] if "Scans" not in p["path"]))
        self.assertIn("run Smart Rename on the folder again", body["error"])
        self.assertIn("tmp_rename_", body["error"])
        # The server's folder cache holds the new names too.
        from tagpup.web import tagpup_routes
        cached = tagpup_routes.folders.of(self.library).get(paths.stored(self.regatta)) or {}
        self.assertTrue(all(os.path.exists(record["path"]) for record in cached.values()))


class TheDialogTellsTheTruth(unittest.TestCase):
    def test_the_note_does_not_say_nothing_changes(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "tagpup",
                               "membership.js"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("changes nothing", source)
        # Suggest runs (analysing in memory, 2026-10-03); only face naming stays off.
        self.assertIn("without adding them", source)
        self.assertIn("naming stays off", source)


if __name__ == "__main__":
    unittest.main()
