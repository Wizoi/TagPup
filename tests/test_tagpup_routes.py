"""The TagPup app's photo and folder routes, asked through Flask's test client (kind unit: no server, no port, no sleep).

What a route answers to a request it must refuse (400 and 404) and what it lists (a scan, the autocomplete lists). What a
write leaves in a photo's file is the services' scenarios': tests/test_tag_a_photo_read_it_back_and_undo.py (save, add, remove,
clear, Date Taken, a deep tag whole), test_suggest_then_apply_all.py (Apply All), test_photo_open.py (Explorer),
test_service_page_images.py (a photo's faces, a face's crop), test_service_indexing.py (the indexer's progress text); the pages
against the real routes are test_pages_against_the_real_server.py's.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from handler_harness import Library  # noqa: E402
from test_taxonomy_lifecycle import EXIFTOOL, requires_exiftool  # noqa: E402

from face_rows import add_face  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.store import db  # noqa: E402


class TagPupAPITestBase(unittest.TestCase):
    def setUp(self):
        self.lib = Library(self, "tagpup_api")
        self.TEST_DB = self.lib.db_path
        self.tmpdir = tempfile.mkdtemp(prefix="tagpup_api_", dir=self.lib.root)

    # ---------- helpers ----------

    def post(self, path, body):
        return self.lib.post(path, body)

    def get(self, path, query=None):
        """The JSON a GET answers; a failed request raises with its status."""
        reply = self.lib.client.get(path, query_string=query)
        if reply.status_code != 200:
            raise Failed(reply.status_code)
        return reply.get_json()

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
            params = {
                "XMP:Subject": flat,
                "IPTC:Keywords": flat,
                "XMP:HierarchicalSubject": hierarchical,
            }
            if title:
                params["XMP:Description"] = title
            with ExifToolSession(executable=EXIFTOOL) as et:
                et.set_tags([path], tags=params, params=["-overwrite_original"])

        raw_meta = {"XMP:Subject": flat, "XMP:HierarchicalSubject": hierarchical}
        self.lib.execute(
            "INSERT OR REPLACE INTO photos (path, mtime, size, tags, captions, raw_metadata)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                path,
                os.path.getmtime(path),
                os.path.getsize(path),
                json.dumps(list(tags)),
                json.dumps([title] if title else []),
                json.dumps(raw_meta),
            ),
        )
        return path


class Failed(Exception):
    """A GET that was not answered 200: `code` is the status."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class TestFolderScan(TagPupAPITestBase):
    def scan(self):
        return self.get("/api/folder/scan", {"path": self.tmpdir, "force": "true"})

    def test_scan_lists_images_with_metadata(self):
        self.make_photo("a.jpg", ["Activity/Hiking"])
        self.make_photo("b.jpg", [])
        data = self.scan()
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
        self.assertEqual([r["filename"] for r in self.scan()], ["a.jpg"])

    def test_scan_of_empty_folder_returns_empty_list(self):
        self.assertEqual(self.scan(), [])

    def test_scan_rejects_missing_path(self):
        with self.assertRaises(Failed) as ctx:
            self.get("/api/folder/scan")
        self.assertEqual(ctx.exception.code, 400)

    def test_scan_rejects_nonexistent_folder(self):
        bogus = os.path.join(self.tmpdir, "does_not_exist")
        with self.assertRaises(Failed) as ctx:
            self.get("/api/folder/scan", {"path": bogus, "force": "true"})
        self.assertEqual(ctx.exception.code, 400)


class TestRefusals(TagPupAPITestBase):
    def test_a_write_without_a_file_or_a_selection_is_a_400_and_a_missing_folder_or_suggestions_too(self):
        ghost = os.path.join(self.tmpdir, "ghost.jpg")
        for route, body in (("/api/photo/save-metadata", {"title": "x", "tags": []}),
                            ("/api/photo/save-metadata", {"path": ghost, "title": "x", "tags": []}),
                            ("/api/photos/bulk-tags", {"paths": [], "add_tags": ["X"]}),
                            ("/api/folder/auto-apply", {"folder_path": self.tmpdir}),
                            ("/api/folder/auto-apply", {"folder_path": os.path.join(self.tmpdir, "nope")})):
            self.assertEqual(400, self.post(route, body)[0], (route, body))


class TestAutocompleteEndpoints(TagPupAPITestBase):
    def _add_tag(self, tag, has_face=0, hidden=0):
        self.lib.execute(
            "INSERT OR REPLACE INTO tag_taxonomy (tag, parent_id, name, has_face, hidden_from_autocomplete)"
            " VALUES (?, NULL, ?, ?, ?)",
            (tag, tag.split("/")[-1], has_face, hidden),
        )

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

    def test_people_endpoint_lists_the_people_faces_are_named(self):
        photo = self.make_photo("a.jpg", [])
        conn = db.connect(self.TEST_DB)
        try:
            add_face(conn, photo, name="Jane Doe")   # as a writer names a face: the name gets its node
            conn.commit()
        finally:
            conn.close()
        self.assertIn("Jane Doe", [each["name"] for each in self.get("/api/people")])


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


class TestAutocompleteFolder(TagPupAPITestBase):
    def test_returns_nothing_when_the_path_parameter_is_absent_or_blank(self):
        # A blank value and no value both take the "no path" branch.
        self.assertEqual(self.get("/api/autocomplete-folder"), [])
        self.assertEqual(self.get("/api/autocomplete-folder", {"path": ""}), [])

    def test_lists_drives_for_a_whitespace_path(self):
        drives = self.get("/api/autocomplete-folder", {"path": " "})
        self.assertTrue(drives, "no drives offered")
        self.assertTrue(all(d.endswith((":\\", ":/")) for d in drives), drives)

    def test_expands_a_bare_drive_letter(self):
        self.assertEqual(self.get("/api/autocomplete-folder", {"path": "C"}), ["C:\\"])
        self.assertEqual(self.get("/api/autocomplete-folder", {"path": "C:"}), ["C:\\"])

    def test_suggests_child_folders_of_a_typed_path(self):
        os.makedirs(os.path.join(self.tmpdir, "Alpha"))
        os.makedirs(os.path.join(self.tmpdir, "Beta"))
        results = self.get("/api/autocomplete-folder", {"path": self.tmpdir + os.sep})
        joined = " ".join(results)
        self.assertIn("Alpha", joined)
        self.assertIn("Beta", joined)

    def test_filters_suggestions_by_typed_prefix(self):
        os.makedirs(os.path.join(self.tmpdir, "Alpha"))
        os.makedirs(os.path.join(self.tmpdir, "Beta"))
        results = self.get("/api/autocomplete-folder", {"path": os.path.join(self.tmpdir, "Al")})
        joined = " ".join(results)
        self.assertIn("Alpha", joined)
        self.assertNotIn("Beta", joined)

    def test_does_not_suggest_files(self):
        os.makedirs(os.path.join(self.tmpdir, "Alpha"))
        self.make_photo("photo.jpg", [])
        results = self.get("/api/autocomplete-folder", {"path": self.tmpdir + os.sep})
        self.assertNotIn("photo.jpg", " ".join(results))


class TestBrowseFolder(TagPupAPITestBase):
    """The native picker runs in an isolated subprocess; only that boundary is mocked."""

    def test_returns_the_folder_chosen_in_the_dialog(self):
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
        result = MagicMock()
        result.stdout = "\n"
        result.stderr = ""
        with patch("subprocess.run", return_value=result):
            self.assertEqual(self.get("/api/browse-folder")["path"], "")

    def test_picker_failure_is_reported_not_swallowed(self):
        with patch("subprocess.run", side_effect=OSError("no display")):
            with self.assertRaises(Failed) as ctx:
                self.get("/api/browse-folder")
        self.assertEqual(ctx.exception.code, 500)


class TestFaceRoutes(TagPupAPITestBase):
    def test_a_face_request_without_its_id_or_path_is_refused_and_an_unknown_face_is_not_found(self):
        with self.assertRaises(Failed) as ctx:
            self.get("/api/photo-faces")
        self.assertEqual(400, ctx.exception.code)
        for asked, status in ((999999, 404), ("abc", 400)):
            self.assertEqual(status, self.lib.client.get("/api/face-crop", query_string={"id": asked}).status_code)


if __name__ == "__main__":
    unittest.main()
