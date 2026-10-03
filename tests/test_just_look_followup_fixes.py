"""The follow-up review's findings #528-#531 (docs/findings.md).

#528  The file-only check is at parity with the held path: it refuses an empty file or a zero-filled tail by
      images.zero_tail_of's own rule, and nothing else -- not what follows the end of the picture.
#529  goes_to_bin is right for a SUBST drive, a volume mounted in a folder, a \\\\?\\ spelling, a link to a share
      and every drive type but a fixed disk.
#530  The long name of a file is judged, and the reason for a permanent delete is the true one.
#531  Any exception after the held part of a Smart Rename committed answers what did change.
"""
import ctypes
import os
import random
import string
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import damaged_photos  # noqa: E402
import test_just_look_edits as jl  # noqa: E402

from tagpup.core import processes  # noqa: E402
from tagpup.files import images, recycle_bin  # noqa: E402
from tagpup.services import file_only  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402


def whole_jpeg_bytes(path, seed=3):
    return damaged_photos.whole_jpeg(path, seed=seed)


class TheCheckIsAtParityWithTheHeldPath(jl.Base):
    """Each variant is a photo that decodes in Pillow and that the held path writes without any check."""

    def variants(self):
        """{name: path}, each made in the Lighthouse folder from a whole JPEG or PNG."""
        from PIL import Image
        made = {}

        def jpeg(name, tail=b"", **save):
            path = os.path.join(self.lighthouse, name)
            if save:
                Image.frombytes("RGB", (160, 120), random.Random(5).randbytes(160 * 120 * 3)).save(path, "JPEG", **save)
            else:
                whole_jpeg_bytes(path, seed=len(made) + 40)
            with open(path, "ab") as handle:
                handle.write(tail)
            made[name] = path

        jpeg("samsung trailer.jpg", b"\x00\x00\x00SEFH\x00\x00\x00\x01" + b"SEFT" * 40 + b"\x01\x02\x03")
        jpeg("motion photo.jpg", random.Random(9).randbytes(3 * 1024 * 1024) + b"ftypmp42")
        jpeg("ff padding.jpg", b"\xff" * 700)
        jpeg("zeros 4097.jpg", bytes(4097))
        jpeg("zeros 65535.jpg", bytes(65535))
        jpeg("progressive.jpg", progressive=True, quality=90)
        png = os.path.join(self.lighthouse, "trailing bytes.png")
        Image.new("RGB", (32, 32), "blue").save(png)
        with open(png, "ab") as handle:
            handle.write(b"trailer written by a camera app")
        made["trailing bytes.png"] = png
        return made

    def test_every_one_is_written(self):
        for name, path in self.variants().items():
            reply = self.post("/photo/save-metadata", {"path": path, "title": "x", "tags": ["A/B"]})
            self.assertEqual(200, reply.status_code, (name, reply.data))
            self.assertEqual(["A/B"], jl.tags_in(path), name)
        self.assert_library_unchanged()

    def test_a_jpeg_with_an_exif_thumbnail(self):
        from PIL import Image
        thumb = os.path.join(self.home.root, "thumb.jpg")
        Image.new("RGB", (16, 12), "red").save(thumb, "JPEG")
        photo = self.loose[0]
        with jl.ExifToolSession(executable=jl.EXIFTOOL) as et:
            et.execute("-ThumbnailImage<=" + thumb, "-overwrite_original", photo)
            held = et.get_tags([photo], tags=["ThumbnailImage"])[0]
        self.assertIn("ThumbnailImage", " ".join(held), "the fixture holds no thumbnail")
        self.assertEqual(200, self.post("/photo/save-metadata", {"path": photo, "title": "x", "tags": []}).status_code)

    def test_a_zero_filled_tail_and_an_empty_file_are_refused(self):
        filled = os.path.join(self.lighthouse, "zero filled.jpg")
        whole_jpeg_bytes(filled)
        with open(filled, "ab") as handle:
            handle.write(bytes(images.ZERO_TAIL + 5000))
        empty = os.path.join(self.lighthouse, "empty.jpg")
        open(empty, "wb").close()
        for path in (filled, empty):
            self.assertEqual(409, self.post("/photo/save-metadata", {"path": path, "title": "x",
                                                                    "tags": []}).status_code, path)

    def test_the_two_checks_agree_on_a_table_of_tails(self):
        tails = [b"", b"\xff\xd9", bytes(1), bytes(4096), bytes(4097), bytes(images.ZERO_TAIL - 1),
                 bytes(images.ZERO_TAIL), bytes(images.ZERO_TAIL + 1), bytes(3 * images.ZERO_TAIL),
                 b"\x01" + bytes(images.ZERO_TAIL - 1), bytes(images.ZERO_TAIL - 1) + b"\x01",
                 b"\xff" * (images.ZERO_TAIL + 10), b"x" * 70000]
        for n, tail in enumerate(tails):
            path = os.path.join(self.lighthouse, "tail %d.jpg" % n)
            whole_jpeg_bytes(path, seed=n)
            with open(path, "ab") as handle:
                handle.write(tail)
            refused_by_held_rule = images.zero_tail_of(path) >= images.ZERO_TAIL
            self.assertEqual(refused_by_held_rule, bool(file_only.unwritable([path])), (n, len(tail)))


class WhichPlacesGoToTheBin(unittest.TestCase):
    def judge(self, path, real=None, mount="C:\\", kind=3, device="\\Device\\HarddiskVolume3"):
        with mock.patch.object(recycle_bin, "_real", return_value=real or path), \
                mock.patch.object(recycle_bin, "_mount_point", return_value=mount), \
                mock.patch.object(recycle_bin, "_drive_type", return_value=kind), \
                mock.patch.object(recycle_bin, "_dos_device", return_value=device):
            return recycle_bin.no_bin_reason(path)

    def test_a_fixed_local_disk_with_a_real_volume_goes_to_the_bin(self):
        self.assertIsNone(self.judge("C:\\Photos\\a.jpg"))

    def test_every_drive_type_but_a_fixed_disk_does_not(self):
        reasons = {4: recycle_bin.NETWORK, 2: recycle_bin.REMOVABLE, 5: recycle_bin.NO_BIN, 6: recycle_bin.NO_BIN,
                   0: recycle_bin.NO_BIN, 1: recycle_bin.NO_BIN}
        for kind, reason in reasons.items():
            self.assertEqual(reason, self.judge("Z:\\a.jpg", mount="Z:\\", kind=kind), kind)

    def test_a_subst_drive_is_not_a_bin_drive(self):
        self.assertEqual(recycle_bin.SUBST, self.judge("Z:\\a.jpg", mount="Z:\\", device="\\??\\C:\\Temp\\x"))

    def test_a_volume_mounted_in_a_folder_is_judged_by_its_mount_point(self):
        self.assertEqual(recycle_bin.REMOVABLE, self.judge("C:\\mnt\\usb\\a.jpg", mount="C:\\mnt\\usb\\", kind=2))
        self.assertIsNone(self.judge("C:\\mnt\\disk\\a.jpg", mount="C:\\mnt\\disk\\", kind=3))

    def test_a_link_to_a_share_is_judged_by_where_it_leads(self):
        self.assertEqual(recycle_bin.NETWORK, self.judge("C:\\links\\a.jpg", real="\\\\server\\share\\a.jpg"))

    def test_the_extended_prefix_is_looked_through(self):
        self.assertEqual(recycle_bin.NETWORK, recycle_bin.no_bin_reason("\\\\?\\UNC\\server\\share\\a.jpg"))
        self.assertIsNone(self.judge("\\\\?\\C:\\Photos\\a.jpg", real="\\\\?\\C:\\Photos\\a.jpg"))

    def test_when_unsure_it_says_permanent(self):
        with mock.patch.object(recycle_bin, "_mount_point", side_effect=OSError("no")):
            self.assertEqual(recycle_bin.NO_BIN, recycle_bin.no_bin_reason("C:\\Photos\\a.jpg"))
        self.assertEqual(recycle_bin.NO_BIN, self.judge("C:\\a.jpg", device="\\Device\\Strange"))
        self.assertEqual(recycle_bin.NO_BIN, self.judge("C:\\a.jpg", device=None))


class ARealSubstDrive(jl.Base):
    def test_a_file_on_it_is_permanent_though_its_drive_is_a_fixed_disk(self):
        letter = next((c for c in reversed(string.ascii_uppercase) if not os.path.exists(c + ":\\")), None)
        started = processes.run(["subst", letter + ":", self.lighthouse], capture_output=True, text=True)
        if started.returncode != 0:
            self.skipTest("subst is not available: %s" % (started.stderr or started.stdout).strip())
        try:
            on_subst = letter + ":\\" + os.path.basename(self.loose[0])
            self.assertTrue(os.path.isfile(on_subst))
            self.assertEqual(recycle_bin.SUBST, recycle_bin.no_bin_reason(on_subst))
            self.assertFalse(recycle_bin.goes_to_bin(on_subst))
            self.assertTrue(recycle_bin.goes_to_bin(self.loose[0]), "the same file by its own path")
            body = self.post("/photo/delete", {"path": on_subst}).get_json()
            self.assertTrue(body["permanent"], body)
            self.assertIn("substituted", body["message"])
        finally:
            processes.run(["subst", letter + ":", "/D"], capture_output=True, text=True)


class TheNameIsJudgedLong(jl.Base):
    def test_the_8_3_name_of_a_longer_extension_is_refused(self):
        real = os.path.join(self.lighthouse, "holiday.jpgold")
        with open(real, "wb") as handle:
            handle.write(b"x")
        buffer = ctypes.create_unicode_buffer(300)
        ctypes.windll.kernel32.GetShortPathNameW(real, buffer, 300)
        if not buffer.value or buffer.value.lower() == real.lower():
            self.skipTest("this volume makes no 8.3 names")
        self.assertTrue(buffer.value.lower().endswith(".jpg"), buffer.value)
        self.assertIsNotNone(recycle_bin.problem(buffer.value))
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=AssertionError("sent")):
            reply = self.post("/photo/delete", {"path": buffer.value})
        self.assertIn(reply.status_code, (400, 409), reply.data)
        self.assertTrue(os.path.exists(real))


class TheReasonIsTheTrueOne(jl.Base):
    PHRASES = {recycle_bin.NETWORK: "network share", recycle_bin.REMOVABLE: "removable drive",
               recycle_bin.SUBST: "substituted", recycle_bin.NO_BIN: "without a Recycle Bin"}

    def test_the_reply_names_each_reason(self):
        for reason, phrase in self.PHRASES.items():
            with mock.patch.object(recycle_bin, "no_bin_reason", return_value=reason), \
                    mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=lambda p: True):
                body = self.post("/photo/delete", {"path": self.loose[0]}).get_json()
            self.assertTrue(body["permanent"])
            self.assertIn(phrase, body["message"], reason)
            if reason != recycle_bin.NETWORK:
                self.assertNotIn("network share", body["message"], reason)
            self.assertEqual(reason, body["permanent_reason"])

    def test_the_membership_answer_names_it_before_the_delete(self):
        for reason in self.PHRASES:
            with mock.patch.object(recycle_bin, "no_bin_reason", return_value=reason):
                found = library_actions.membership(self.library, self.lighthouse)
            self.assertTrue(found["permanent_delete"])
            self.assertEqual(reason, found["permanent_reason"])
        with mock.patch.object(recycle_bin, "no_bin_reason", return_value=None):
            found = library_actions.membership(self.library, self.lighthouse)
        self.assertEqual((False, None), (found["permanent_delete"], found["permanent_reason"]))


class AnyFailureAfterTheHeldPartAnswersWhatChanged(jl.Base):
    def rename(self):
        return self.post("/folder/rename-photos", {"folder_path": self.regatta, "grouping": "Regatta",
                                                  "photo_paths": self.held + [self.scan]})

    def test_whatever_the_second_part_raises(self):
        for error in (OSError("the share went away"), KeyError("x")):
            with mock.patch.object(file_only, "rename", side_effect=error):
                reply = self.rename()
            self.assertEqual(500, reply.status_code, (error, reply.data))
            body = reply.get_json()
            self.assertEqual(2, len(body["updated_paths"]), (error, body))
            shown = sorted(os.path.basename(p["path"]) for p in body["updated_photos"] if "Scans" not in p["path"])
            self.assertTrue(all(name.startswith("Regatta - ") for name in shown), shown)
            self.assertEqual(["scan_01.jpg"], os.listdir(self.scans))
            # put the files back for the next round
            for old, new in body["updated_paths"].items():
                os.rename(new, old)
                from tagpup.store import db
                db.write_with_connection(self.library.path, lambda conn, o=old, n=new: conn.execute(
                    "UPDATE photos SET path = ? WHERE path = ?", (o, n)))
            tagpup_routes.folders.forget(self.library)

    def test_a_folder_that_cannot_be_read_again_is_a_plain_500_and_the_cache_is_dropped(self):
        folder = tagpup_routes.folders.of(self.library)
        folder.put(self.regatta, {"stale": {"path": self.held[0]}})
        with mock.patch.object(file_only, "rename", side_effect=OSError("gone")), \
                mock.patch.object(tagpup_routes.photo_actions, "read_folder", side_effect=OSError("share gone")):
            reply = self.rename()
        self.assertEqual(500, reply.status_code, reply.data)
        self.assertNotIn("updated_paths", reply.get_json())
        self.assertIsNone(folder.get(self.regatta), "a cache of the old names was kept")


if __name__ == "__main__":
    unittest.main()
