"""Behavioural tests for the TagPup GUI server's photo and folder endpoints.

`test_stability.py` covers the TagTuner server in depth; the TagPup server -- which owns
folder scanning, metadata writes, bulk tagging and suggestion application -- had almost
no coverage. Every endpoint here mutates either the SQLite index or the user's photo
files, so assertions check the observable result, not just the HTTP status.

Tests that touch photo files are skipped when ExifTool is unavailable.
"""
import os
import sys
import json
import time
import shutil
import sqlite3
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from tagpup_server import (
    start_server as start_tagpup_server,
    TagPupHTTPRequestHandler,
    set_active_db_path,
    normalize_path,
)
from tests.test_taxonomy_lifecycle import EXIFTOOL, requires_exiftool


class TagPupAPITestBase(unittest.TestCase):
    TEST_PORT = 9955
    TEST_DB = os.path.join(WORKSPACE_DIR, "data", "test_tagpup_api.db")

    @classmethod
    def setUpClass(cls):
        from index import PhotoIndex

        pi = PhotoIndex(db_path=cls.TEST_DB)
        pi.load()
        pi.close()

        cls.server_thread = threading.Thread(
            target=start_tagpup_server,
            kwargs={
                "port": cls.TEST_PORT,
                "db_path": cls.TEST_DB,
                "gui_dir": os.path.join(WORKSPACE_DIR, "gui_tagpup"),
            },
            daemon=True,
        )
        cls.server_thread.start()
        time.sleep(1.0)

    @classmethod
    def tearDownClass(cls):
        set_active_db_path(None)
        for path in (cls.TEST_DB, cls.TEST_DB.replace(".db", "_taxonomy.json")):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="tagpup_api_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.addCleanup(set_active_db_path, None)
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute("DELETE FROM photos")
        conn.execute("DELETE FROM faces")
        conn.execute("DELETE FROM tag_taxonomy")
        conn.commit()
        conn.close()
        set_active_db_path(self.TEST_DB)
        TagPupHTTPRequestHandler.folder_cache.clear()
        set_active_db_path(None)

    # ---------- helpers ----------

    def post(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.TEST_PORT}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def get(self, path):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.TEST_PORT}{path}", timeout=60
        ) as r:
            return json.loads(r.read().decode("utf-8"))

    def make_photo(self, filename, tags=(), title=None):
        from PIL import Image

        path = os.path.join(self.tmpdir, filename)
        Image.new("RGB", (32, 32), (90, 110, 130)).save(path, "JPEG")

        flat, hierarchical = [], []
        for t in tags:
            flat.append(t)
            if "/" in t:
                hierarchical.append(t)
                flat.extend(t.split("/"))
        flat = sorted(set(flat))
        hierarchical = sorted(set(hierarchical))

        if EXIFTOOL and (flat or title):
            import exiftool

            params = {
                "XMP:Subject": flat,
                "IPTC:Keywords": flat,
                "XMP:HierarchicalSubject": hierarchical,
            }
            if title:
                params["XMP:Description"] = title
            with exiftool.ExifToolHelper(executable=EXIFTOOL) as et:
                et.set_tags([path], tags=params, params=["-overwrite_original"])

        raw_meta = {"XMP:Subject": flat, "XMP:HierarchicalSubject": hierarchical}
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                path,
                os.path.getmtime(path),
                os.path.getsize(path),
                json.dumps(list(tags)),
                json.dumps([]),
                json.dumps([title] if title else []),
                json.dumps(raw_meta),
            ),
        )
        conn.commit()
        conn.close()
        return path

    def disk_keywords(self, photo_path):
        import exiftool

        with exiftool.ExifToolHelper(executable=EXIFTOOL) as et:
            meta = et.get_metadata([photo_path])[0]
        out = set()
        for key in ("XMP:Subject", "IPTC:Keywords", "XMP:HierarchicalSubject"):
            val = meta.get(key, [])
            if isinstance(val, str):
                val = [val]
            out.update(val or [])
        return out

    def disk_field(self, photo_path, field):
        import exiftool

        with exiftool.ExifToolHelper(executable=EXIFTOOL) as et:
            return et.get_metadata([photo_path])[0].get(field)


class TestFolderScan(TagPupAPITestBase):
    def test_scan_lists_images_with_metadata(self):
        self.make_photo("a.jpg", ["Activity/Hiking"])
        self.make_photo("b.jpg", [])
        data = self.get(
            f"/api/folder/scan?path={urllib.parse.quote(self.tmpdir)}&force=true"
        )
        self.assertEqual(len(data), 2)
        by_name = {rec["filename"]: rec for rec in data}
        self.assertIn("a.jpg", by_name)
        self.assertIn("b.jpg", by_name)
        for rec in data:
            for key in ("path", "filename", "tags", "people", "mtime", "size"):
                self.assertIn(key, rec, f"scan record missing {key}")

    def test_scan_ignores_non_image_files(self):
        self.make_photo("a.jpg", [])
        with open(os.path.join(self.tmpdir, "notes.txt"), "w") as f:
            f.write("not an image")
        data = self.get(
            f"/api/folder/scan?path={urllib.parse.quote(self.tmpdir)}&force=true"
        )
        self.assertEqual([r["filename"] for r in data], ["a.jpg"])

    def test_scan_of_empty_folder_returns_empty_list(self):
        data = self.get(
            f"/api/folder/scan?path={urllib.parse.quote(self.tmpdir)}&force=true"
        )
        self.assertEqual(data, [])

    def test_scan_rejects_missing_path(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/folder/scan")
        self.assertEqual(ctx.exception.code, 400)

    def test_scan_rejects_nonexistent_folder(self):
        bogus = os.path.join(self.tmpdir, "does_not_exist")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get(f"/api/folder/scan?path={urllib.parse.quote(bogus)}&force=true")
        self.assertEqual(ctx.exception.code, 400)


class TestSaveMetadata(TagPupAPITestBase):
    @requires_exiftool
    def test_saves_title_to_file(self):
        photo = self.make_photo("a.jpg", ["Activity/Hiking"])
        status, body = self.post(
            "/api/photo/save-metadata",
            {"path": photo, "title": "A day out", "tags": ["Activity/Hiking"]},
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(body.get("success"), body)
        self.assertEqual(self.disk_field(photo, "XMP:Description"), "A day out")

    @requires_exiftool
    def test_saves_tags_to_file_as_flat_and_hierarchical(self):
        photo = self.make_photo("a.jpg", [])
        self.post(
            "/api/photo/save-metadata",
            {"path": photo, "title": "", "tags": ["Activity/Hiking"]},
        )
        on_disk = self.disk_keywords(photo)
        self.assertIn("Activity/Hiking", on_disk, "hierarchical form missing")
        self.assertIn("Hiking", on_disk, "flat leaf missing")
        self.assertIn("Activity", on_disk, "flat ancestor missing")

    @requires_exiftool
    def test_clearing_tags_removes_them_from_file(self):
        photo = self.make_photo("a.jpg", ["Activity/Hiking"])
        self.post("/api/photo/save-metadata", {"path": photo, "title": "", "tags": []})
        self.assertNotIn("Activity/Hiking", self.disk_keywords(photo))

    @requires_exiftool
    def test_saves_date_taken(self):
        photo = self.make_photo("a.jpg", [])
        status, body = self.post(
            "/api/photo/save-metadata",
            {"path": photo, "title": "", "tags": [], "date_taken": "2021-07-04 12:30:00"},
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(
            str(self.disk_field(photo, "EXIF:DateTimeOriginal")), "2021:07:04 12:30:00"
        )

    def test_rejects_missing_path(self):
        status, _ = self.post("/api/photo/save-metadata", {"title": "x", "tags": []})
        self.assertEqual(status, 400)

    def test_rejects_nonexistent_file(self):
        status, _ = self.post(
            "/api/photo/save-metadata",
            {"path": os.path.join(self.tmpdir, "ghost.jpg"), "title": "x", "tags": []},
        )
        self.assertEqual(status, 400)


class TestBulkTags(TagPupAPITestBase):
    def _scan(self):
        """Populate folder_cache the way the UI does before bulk operations."""
        return self.get(
            f"/api/folder/scan?path={urllib.parse.quote(self.tmpdir)}&force=true"
        )

    @requires_exiftool
    def test_adds_tag_across_selection(self):
        a = self.make_photo("a.jpg", [])
        b = self.make_photo("b.jpg", [])
        self._scan()

        status, body = self.post(
            "/api/photos/bulk-tags", {"paths": [a, b], "add_tags": ["Trips/Texas"]}
        )
        self.assertEqual(status, 200, body)
        for photo in (a, b):
            self.assertIn("Trips/Texas", self.disk_keywords(photo))

    @requires_exiftool
    def test_removes_tag_across_selection(self):
        a = self.make_photo("a.jpg", ["Trips/Texas"])
        b = self.make_photo("b.jpg", ["Trips/Texas"])
        self._scan()

        self.post(
            "/api/photos/bulk-tags", {"paths": [a, b], "remove_tags": ["Trips/Texas"]}
        )
        for photo in (a, b):
            self.assertNotIn("Trips/Texas", self.disk_keywords(photo))

    @requires_exiftool
    def test_adding_a_tag_preserves_existing_tags(self):
        """A bulk add must never clobber keywords the photo already carries."""
        photo = self.make_photo("a.jpg", ["Holidays/Christmas"])
        self._scan()

        self.post(
            "/api/photos/bulk-tags", {"paths": [photo], "add_tags": ["Trips/Texas"]}
        )
        on_disk = self.disk_keywords(photo)
        self.assertIn("Trips/Texas", on_disk)
        self.assertIn("Holidays/Christmas", on_disk, "pre-existing tag was destroyed")

    @requires_exiftool
    def test_adding_a_tag_preserves_existing_tags_without_a_warm_cache(self):
        """Same guarantee when the folder was never scanned in this session.

        The cache is cleared on restart and by taxonomy edits, so a bulk write must
        fall back to the indexed tags rather than starting from an empty set.
        """
        photo = self.make_photo("a.jpg", ["Holidays/Christmas"])
        # Deliberately do NOT scan: folder_cache stays cold.

        self.post(
            "/api/photos/bulk-tags", {"paths": [photo], "add_tags": ["Trips/Texas"]}
        )
        on_disk = self.disk_keywords(photo)
        self.assertIn("Trips/Texas", on_disk)
        self.assertIn(
            "Holidays/Christmas",
            on_disk,
            "pre-existing tag destroyed because the folder cache was cold",
        )

    def test_rejects_empty_selection(self):
        status, _ = self.post("/api/photos/bulk-tags", {"paths": [], "add_tags": ["X"]})
        self.assertEqual(status, 400)


class TestFolderAutoApply(TagPupAPITestBase):
    def _seed_suggestions(self, photo_path, tags_with_scores):
        """Inject a completed suggestion run into the active database's registry."""
        set_active_db_path(self.TEST_DB)
        TagPupHTTPRequestHandler.suggest_status[normalize_path(self.tmpdir)] = {
            "status": "completed",
            "completed": 1,
            "total": 1,
            "suggestions": {
                photo_path: {
                    "tags": [{"tag": t, "score": s} for t, s in tags_with_scores],
                    "people": [],
                    "title": None,
                    "raw_suggestions": {
                        "suggested_tags": [
                            {"tag": t, "score": s} for t, s in tags_with_scores
                        ]
                    },
                }
            },
        }
        set_active_db_path(None)

    @requires_exiftool
    def test_applies_suggestions_above_threshold(self):
        photo = self.make_photo("a.jpg", [])
        self.get(f"/api/folder/scan?path={urllib.parse.quote(self.tmpdir)}&force=true")
        self._seed_suggestions(photo, [("Trips/Texas", 0.9)])

        status, body = self.post(
            "/api/folder/auto-apply", {"folder_path": self.tmpdir, "threshold": 0.75}
        )
        self.assertEqual(status, 200, body)
        self.assertIn("Trips/Texas", self.disk_keywords(photo))

    @requires_exiftool
    def test_skips_suggestions_below_threshold(self):
        photo = self.make_photo("a.jpg", [])
        self.get(f"/api/folder/scan?path={urllib.parse.quote(self.tmpdir)}&force=true")
        self._seed_suggestions(photo, [("Trips/Texas", 0.30)])

        self.post("/api/folder/auto-apply", {"folder_path": self.tmpdir, "threshold": 0.75})
        self.assertNotIn("Trips/Texas", self.disk_keywords(photo))

    @requires_exiftool
    def test_auto_apply_preserves_existing_tags(self):
        """Applying a suggestion must add to the photo's keywords, not replace them."""
        photo = self.make_photo("a.jpg", ["Holidays/Christmas"])
        self.get(f"/api/folder/scan?path={urllib.parse.quote(self.tmpdir)}&force=true")
        self._seed_suggestions(photo, [("Trips/Texas", 0.9)])

        self.post("/api/folder/auto-apply", {"folder_path": self.tmpdir, "threshold": 0.75})
        on_disk = self.disk_keywords(photo)
        self.assertIn("Trips/Texas", on_disk)
        self.assertIn("Holidays/Christmas", on_disk, "pre-existing tag was destroyed")

    def test_rejects_folder_without_suggestions(self):
        status, _ = self.post(
            "/api/folder/auto-apply", {"folder_path": self.tmpdir, "threshold": 0.75}
        )
        self.assertEqual(status, 400)

    def test_rejects_invalid_folder(self):
        status, _ = self.post(
            "/api/folder/auto-apply",
            {"folder_path": os.path.join(self.tmpdir, "nope"), "threshold": 0.75},
        )
        self.assertEqual(status, 400)


class TestAutocompleteEndpoints(TagPupAPITestBase):
    def _add_tag(self, tag, has_face=0, hidden=0):
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT OR REPLACE INTO tag_taxonomy (tag, parent_id, name, has_face, hidden_from_autocomplete)"
            " VALUES (?, NULL, ?, ?, ?)",
            (tag, tag.split("/")[-1], has_face, hidden),
        )
        conn.commit()
        conn.close()

    def test_tags_endpoint_lists_visible_tags(self):
        self._add_tag("Activity")
        self._add_tag("Trips")
        tags = self.get("/api/tags")
        self.assertIn("Activity", tags)
        self.assertIn("Trips", tags)

    def test_tags_endpoint_hides_tags_marked_hidden(self):
        self._add_tag("Activity")
        self._add_tag("Author", hidden=1)
        tags = self.get("/api/tags")
        self.assertIn("Activity", tags)
        self.assertNotIn("Author", tags, "hidden tag leaked into autocomplete")

    def test_people_endpoint_lists_resolved_face_names(self):
        photo = self.make_photo("a.jpg", [])
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name) VALUES (?, ?, ?, ?)",
            (photo, "[]", b"", "Jane Doe"),
        )
        conn.commit()
        conn.close()
        self.assertIn("Jane Doe", self.get("/api/people"))


class TestPhotoRotate(TagPupAPITestBase):
    @requires_exiftool
    def test_rotate_left_and_right_are_accepted(self):
        photo = self.make_photo("a.jpg", [])
        for direction in ("left", "right"):
            status, body = self.post(
                "/api/photo/rotate", {"path": photo, "direction": direction}
            )
            self.assertEqual(status, 200, body)

    def test_rejects_invalid_direction(self):
        photo = self.make_photo("a.jpg", [])
        status, _ = self.post(
            "/api/photo/rotate", {"path": photo, "direction": "sideways"}
        )
        self.assertEqual(status, 400)

    def test_rejects_nonexistent_file(self):
        status, _ = self.post(
            "/api/photo/rotate",
            {"path": os.path.join(self.tmpdir, "ghost.jpg"), "direction": "left"},
        )
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()


class TestAutocompleteFolder(TagPupAPITestBase):
    def test_returns_nothing_when_the_path_parameter_is_absent_or_blank(self):
        # parse_qs drops blank values, so both spellings take the "no path" branch.
        self.assertEqual(self.get("/api/autocomplete-folder"), [])
        self.assertEqual(self.get("/api/autocomplete-folder?path="), [])

    def test_lists_drives_for_a_whitespace_path(self):
        drives = self.get("/api/autocomplete-folder?path=%20")
        self.assertTrue(drives, "no drives offered")
        self.assertTrue(all(d.endswith((":\\", ":/")) for d in drives), drives)

    def test_expands_a_bare_drive_letter(self):
        self.assertEqual(self.get("/api/autocomplete-folder?path=C"), ["C:\\"])
        self.assertEqual(self.get("/api/autocomplete-folder?path=C:"), ["C:\\"])

    def test_suggests_child_folders_of_a_typed_path(self):
        os.makedirs(os.path.join(self.tmpdir, "Alpha"))
        os.makedirs(os.path.join(self.tmpdir, "Beta"))
        typed = self.tmpdir + os.sep
        results = self.get(f"/api/autocomplete-folder?path={urllib.parse.quote(typed)}")
        joined = " ".join(results)
        self.assertIn("Alpha", joined)
        self.assertIn("Beta", joined)

    def test_filters_suggestions_by_typed_prefix(self):
        os.makedirs(os.path.join(self.tmpdir, "Alpha"))
        os.makedirs(os.path.join(self.tmpdir, "Beta"))
        typed = os.path.join(self.tmpdir, "Al")
        results = self.get(f"/api/autocomplete-folder?path={urllib.parse.quote(typed)}")
        joined = " ".join(results)
        self.assertIn("Alpha", joined)
        self.assertNotIn("Beta", joined)

    def test_does_not_suggest_files(self):
        os.makedirs(os.path.join(self.tmpdir, "Alpha"))
        self.make_photo("photo.jpg", [])
        typed = self.tmpdir + os.sep
        results = self.get(f"/api/autocomplete-folder?path={urllib.parse.quote(typed)}")
        self.assertNotIn("photo.jpg", " ".join(results))


class TestOpenExplorer(TagPupAPITestBase):
    def test_launches_explorer_with_list_arguments(self):
        """Arguments must stay a list; a shell string here would be injectable."""
        from unittest.mock import patch

        photo = self.make_photo("a.jpg", [])
        with patch("subprocess.Popen") as mock_popen:
            status, body = self.post("/api/photo/open-explorer", {"path": photo})

        self.assertEqual(status, 200, body)
        self.assertTrue(mock_popen.called, "explorer was never launched")
        args, kwargs = mock_popen.call_args
        self.assertIsInstance(args[0], list, "command was not passed as an argument list")
        self.assertEqual(args[0][0], "explorer.exe")
        self.assertFalse(kwargs.get("shell", False), "command was run through a shell")
        self.assertIn(os.path.basename(photo), args[0][1])

    def test_rejects_missing_path(self):
        status, _ = self.post("/api/photo/open-explorer", {})
        self.assertEqual(status, 400)

    def test_rejects_nonexistent_file(self):
        status, _ = self.post(
            "/api/photo/open-explorer",
            {"path": os.path.join(self.tmpdir, "ghost.jpg")},
        )
        self.assertEqual(status, 400)


class TestBrowseFolder(TagPupAPITestBase):
    """The native picker runs in an isolated subprocess; only that boundary is mocked."""

    def test_returns_the_folder_chosen_in_the_dialog(self):
        from unittest.mock import patch, MagicMock

        result = MagicMock()
        result.stdout = self.tmpdir + "\n"
        result.stderr = ""

        with patch("subprocess.run", return_value=result) as mock_run:
            body = self.get("/api/browse-folder")

        self.assertEqual(body["path"], self.tmpdir)
        self.assertTrue(mock_run.called)
        cmd = mock_run.call_args[0][0]
        self.assertIsInstance(cmd, list, "picker was not launched as an argument list")
        self.assertEqual(cmd[1], "-c", "picker must run in an isolated interpreter")

    def test_cancelled_dialog_returns_an_empty_path(self):
        from unittest.mock import patch, MagicMock

        result = MagicMock()
        result.stdout = "\n"
        result.stderr = ""
        with patch("subprocess.run", return_value=result):
            self.assertEqual(self.get("/api/browse-folder")["path"], "")

    def test_picker_failure_is_reported_not_swallowed(self):
        from unittest.mock import patch

        with patch("subprocess.run", side_effect=OSError("no display")):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get("/api/browse-folder")
        self.assertEqual(ctx.exception.code, 500)


class TestPhotoFaces(TagPupAPITestBase):
    """TagPup exposes the faces on a photo so tagging can show what is unidentified."""

    def add_face(self, photo, seed, name=None, box=(0, 0, 50, 50), excluded=0, reason=None):
        import numpy as np

        rng = np.random.default_rng(seed)
        vec = rng.standard_normal(512).astype("float32")
        vec = vec / np.linalg.norm(vec)
        conn = sqlite3.connect(self.TEST_DB)
        cur = conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name, prob, excluded, excluded_reason)"
            " VALUES (?, ?, ?, ?, 0.99, ?, ?)",
            (photo, json.dumps(list(box)), vec.tobytes(), name, excluded, reason),
        )
        fid = cur.lastrowid
        conn.commit()
        conn.close()
        return fid

    def test_reports_no_faces_for_an_unknown_photo(self):
        body = self.get(f"/api/photo-faces?path={urllib.parse.quote('D:/nope.jpg')}")
        self.assertEqual(body["faces"], [])

    def test_lists_the_faces_on_a_photo(self):
        photo = self.make_photo("a.jpg", [])
        a = self.add_face(photo, 1, name="Jane Doe")
        b = self.add_face(photo, 2, box=(60, 0, 110, 50))

        body = self.get(f"/api/photo-faces?path={urllib.parse.quote(photo)}")
        self.assertEqual(body["total"], 2)
        self.assertEqual({f["id"] for f in body["faces"]}, {a, b})
        self.assertEqual(body["unmatched"], 1)

    def test_suggests_a_name_for_an_unidentified_face(self):
        """The suggestion comes from resolved faces elsewhere in the library."""
        known = self.make_photo("known.jpg", [])
        self.add_face(known, 10, name="Jane Doe")

        target = self.make_photo("target.jpg", [])
        face = self.add_face(target, 10)  # same seed: identical embedding

        body = self.get(f"/api/photo-faces?path={urllib.parse.quote(target)}")
        entry = next(f for f in body["faces"] if f["id"] == face)
        self.assertEqual(entry["suggestion"], "Jane Doe")
        self.assertGreater(entry["similarity"], 0.9)

    def test_a_named_face_carries_no_suggestion(self):
        photo = self.make_photo("a.jpg", [])
        self.add_face(photo, 1, name="Jane Doe")
        body = self.get(f"/api/photo-faces?path={urllib.parse.quote(photo)}")
        self.assertIsNone(body["faces"][0]["suggestion"])

    def test_excluded_faces_are_reported_but_not_counted_as_unidentified(self):
        photo = self.make_photo("a.jpg", [])
        self.add_face(photo, 1, excluded=1, reason="stranger")

        body = self.get(f"/api/photo-faces?path={urllib.parse.quote(photo)}")
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["unmatched"], 0, "an excluded face counted as work to do")
        self.assertTrue(body["faces"][0]["excluded"])
        self.assertEqual(body["faces"][0]["excluded_reason"], "stranger")

    def test_an_excluded_face_is_never_used_as_a_suggestion(self):
        known = self.make_photo("known.jpg", [])
        self.add_face(known, 20, name="Jane Doe", excluded=1)

        target = self.make_photo("target.jpg", [])
        self.add_face(target, 20)

        body = self.get(f"/api/photo-faces?path={urllib.parse.quote(target)}")
        self.assertIsNone(body["faces"][0]["suggestion"])

    def test_named_faces_are_listed_before_unidentified_ones(self):
        photo = self.make_photo("a.jpg", [])
        self.add_face(photo, 2, box=(60, 0, 110, 50))
        self.add_face(photo, 1, name="Jane Doe")

        body = self.get(f"/api/photo-faces?path={urllib.parse.quote(photo)}")
        self.assertIsNotNone(body["faces"][0]["name"], "unidentified face sorted first")

    def test_rejects_a_missing_path(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/photo-faces")
        self.assertEqual(ctx.exception.code, 400)


class TestTagPupFaceCrop(TagPupAPITestBase):
    @requires_exiftool
    def test_serves_a_crop_and_caches_it(self):
        import numpy as np

        photo = self.make_photo("a.jpg", [])
        rng = np.random.default_rng(1)
        vec = (rng.standard_normal(512).astype("float32"))
        vec = vec / np.linalg.norm(vec)
        conn = sqlite3.connect(self.TEST_DB)
        cur = conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, NULL, 0.9)",
            (photo, json.dumps([0, 0, 20, 20]), vec.tobytes()),
        )
        face_id = cur.lastrowid
        conn.commit()
        conn.close()

        req = urllib.request.Request(
            f"http://127.0.0.1:{self.TEST_PORT}/api/face-crop?id={face_id}"
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            self.assertEqual(r.status, 200)
            self.assertTrue(r.headers.get("Content-Type").startswith("image/"))
            self.assertGreater(len(r.read()), 0)

        conn = sqlite3.connect(self.TEST_DB)
        cached = conn.execute(
            "SELECT crop_image FROM faces WHERE id = ?", (face_id,)
        ).fetchone()[0]
        conn.close()
        self.assertTrue(cached, "crop was not cached back into the row")

    def test_rejects_an_unknown_face(self):
        status, _ = 0, None
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{self.TEST_PORT}/api/face-crop?id=999999", timeout=30
            )
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 404)

    def test_rejects_a_non_numeric_id(self):
        status = 0
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{self.TEST_PORT}/api/face-crop?id=abc", timeout=30
            )
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 400)
