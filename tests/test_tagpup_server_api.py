"""Behavioural tests for the TagPup app's photo and folder endpoints.

`test_stability.py` covers the TagTuner server in depth; the TagPup server -- which owns
folder scanning, metadata writes, bulk tagging and suggestion application -- had almost
no coverage. Every endpoint here mutates either the SQLite index or the user's photo
files, so assertions check the observable result, not just the HTTP status.

Tests that touch photo files are skipped when ExifTool is unavailable. The app is asked
through Flask's test client: no server, no port, no sleep.
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

from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.store import db as tagpup_store_db  # noqa: E402
from tagpup.store import suggestions as saved_suggestions  # noqa: E402


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

    def disk_keywords(self, photo_path):
        with ExifToolSession(executable=EXIFTOOL) as et:
            meta = et.get_metadata([photo_path])[0]
        out = set()
        for key in ("XMP:Subject", "IPTC:Keywords", "XMP:HierarchicalSubject"):
            val = meta.get(key, [])
            if isinstance(val, str):
                val = [val]
            out.update(val or [])
        return out

    def disk_field(self, photo_path, field):
        with ExifToolSession(executable=EXIFTOOL) as et:
            return et.get_metadata([photo_path])[0].get(field)


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
    def test_saves_a_tag_whole_rather_than_in_pieces(self):
        """A tag's levels are the keyword; its fragments are not keywords.

        This used to write the path AND each of its segments, so
        "Family/Immediate/Cora Ingersoll" became four keywords. In a library whose
        keywords are full paths -- 18,364 of 18,502 values in this one, none bare --
        that buries a deliberate hierarchy under its own pieces, and the bare leaf is
        the form that gave one person two entries in the Add Person list.
        """
        photo = self.make_photo("a.jpg", [])
        self.post(
            "/api/photo/save-metadata",
            {"path": photo, "title": "", "tags": ["Activity/Hiking"]},
        )
        on_disk = self.disk_keywords(photo)
        self.assertIn("Activity/Hiking", on_disk, "the tag itself is missing")
        self.assertNotIn("Hiking", on_disk, "the leaf was scattered beside the path")
        self.assertNotIn("Activity", on_disk, "the root was scattered beside the path")

    @requires_exiftool
    def test_a_deep_tag_keeps_every_level(self):
        photo = self.make_photo("deep.jpg", [])
        self.post(
            "/api/photo/save-metadata",
            {"path": photo, "title": "",
             "tags": ["Family/Immediate/Cora Ingersoll", "Activity/Running"]},
        )
        on_disk = self.disk_keywords(photo)
        self.assertIn("Family/Immediate/Cora Ingersoll", on_disk, "levels were flattened")
        self.assertIn("Activity/Running", on_disk)
        for fragment in ("Family", "Immediate", "Cora Ingersoll", "Running"):
            self.assertNotIn(fragment, on_disk, "%r was written as its own keyword" % fragment)

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
        """Populate the folder cache the way the UI does before bulk operations."""
        return self.get("/api/folder/scan", {"path": self.tmpdir, "force": "true"})

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
        start from what the file holds rather than from an empty set.
        """
        photo = self.make_photo("a.jpg", ["Holidays/Christmas"])
        # Deliberately do NOT scan: the folder cache stays cold.

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
        """Keep a finished suggestion for the photo in the library, as a run would."""
        conn = tagpup_store_db.connect(self.TEST_DB)
        try:
            saved_suggestions.put(conn, photo_path, {
                "tags": [{"tag": t, "score": s} for t, s in tags_with_scores],
                "people": [],
                "title": None,
                "raw_suggestions": {
                    "suggested_tags": [
                        {"tag": t, "score": s} for t, s in tags_with_scores
                    ]
                },
            })
            conn.commit()
        finally:
            conn.close()

    @requires_exiftool
    def test_applies_suggestions_above_threshold(self):
        photo = self.make_photo("a.jpg", [])
        self.get("/api/folder/scan", {"path": self.tmpdir, "force": "true"})
        self._seed_suggestions(photo, [("Trips/Texas", 0.9)])

        status, body = self.post(
            "/api/folder/auto-apply", {"folder_path": self.tmpdir, "threshold": 0.75}
        )
        self.assertEqual(status, 200, body)
        self.assertIn("Trips/Texas", self.disk_keywords(photo))

    @requires_exiftool
    def test_skips_suggestions_below_threshold(self):
        photo = self.make_photo("a.jpg", [])
        self.get("/api/folder/scan", {"path": self.tmpdir, "force": "true"})
        self._seed_suggestions(photo, [("Trips/Texas", 0.30)])

        self.post("/api/folder/auto-apply", {"folder_path": self.tmpdir, "threshold": 0.75})
        self.assertNotIn("Trips/Texas", self.disk_keywords(photo))

    @requires_exiftool
    def test_auto_apply_preserves_existing_tags(self):
        """Applying a suggestion must add to the photo's keywords, not replace them."""
        photo = self.make_photo("a.jpg", ["Holidays/Christmas"])
        self.get("/api/folder/scan", {"path": self.tmpdir, "force": "true"})
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

    def test_people_endpoint_lists_resolved_face_names(self):
        photo = self.make_photo("a.jpg", [])
        self.lib.execute(
            "INSERT INTO faces (photo_id, box, embedding, name) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?)",
            (photo, "[]", b"", "Jane Doe"),
        )
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


class TestOpenExplorer(TagPupAPITestBase):
    def test_launches_explorer_selecting_the_file_without_a_shell(self):
        """Never through a shell, and with the path quoted after the switch.

        An argument list can't express what Explorer needs: list2cmdline quotes the
        whole "/select,<path>" for a path with a space, and Explorer does not parse
        that as a selection. So the command is one string handed straight to
        CreateProcess -- no shell, so nothing in it is interpreted -- built from a
        path that must exist, and a Windows path cannot contain a quote.
        """
        photo = self.make_photo("a b.jpg", [])
        with patch("subprocess.Popen") as mock_popen:
            status, body = self.post("/api/photo/open-explorer", {"path": photo})

        self.assertEqual(status, 200, body)
        self.assertTrue(mock_popen.called, "explorer was never launched")
        args, kwargs = mock_popen.call_args
        self.assertFalse(kwargs.get("shell", False), "command was run through a shell")
        self.assertEqual(args[0], 'explorer.exe /select,"%s"' % os.path.abspath(photo))

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


class TestPhotoFaces(TagPupAPITestBase):
    """TagPup exposes the faces on a photo so tagging can show what is unidentified."""

    def add_face(self, photo, seed, name=None, box=(0, 0, 50, 50), excluded=0, reason=None):
        import numpy as np

        rng = np.random.default_rng(seed)
        vec = rng.standard_normal(512).astype("float32")
        vec = vec / np.linalg.norm(vec)
        return self.lib.execute(
            "INSERT INTO faces (photo_id, box, embedding, name, prob, excluded, excluded_reason)"
            " VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, ?, 0.99, ?, ?)",
            (photo, json.dumps(list(box)), vec.tobytes(), name, excluded, reason),
        )

    def faces_of(self, photo):
        return self.get("/api/photo-faces", {"path": photo})

    def test_reports_no_faces_for_an_unknown_photo(self):
        body = self.faces_of("D:/nope.jpg")
        self.assertEqual(body["faces"], [])
        self.assertEqual((body["total"], body["unmatched"]), (0, 0))

    def test_lists_the_faces_on_a_photo(self):
        photo = self.make_photo("a.jpg", [])
        a = self.add_face(photo, 1, name="Jane Doe")
        b = self.add_face(photo, 2, box=(60, 0, 110, 50))

        body = self.faces_of(photo)
        self.assertEqual(body["total"], 2)
        self.assertEqual({f["id"] for f in body["faces"]}, {a, b})
        self.assertEqual(body["unmatched"], 1)

    def test_suggests_a_name_for_an_unidentified_face(self):
        """The suggestion comes from resolved faces elsewhere in the library."""
        known = self.make_photo("known.jpg", [])
        self.add_face(known, 10, name="Jane Doe")

        target = self.make_photo("target.jpg", [])
        face = self.add_face(target, 10)  # same seed: identical embedding

        body = self.faces_of(target)
        entry = next(f for f in body["faces"] if f["id"] == face)
        self.assertEqual(entry["suggestion"], "Jane Doe")
        self.assertGreater(entry["similarity"], 0.9)

    def face_at(self, photo, vec, name=None):
        return self.lib.execute(
            "INSERT INTO faces (photo_id, box, embedding, name, prob, excluded)"
            " VALUES ((SELECT id FROM photos WHERE path = ?), '[0, 0, 50, 50]', ?, ?, 0.99, 0)",
            (photo, vec.tobytes(), name))

    def test_a_face_only_as_alike_as_strangers_are_is_offered_nobody(self):
        """0.6 alike is below the floor every screen offers a name from: two strangers in
        three reached the 0.5 this used (tagpup.core.clustering, docs/findings.md #75)."""
        import numpy as np
        from tagpup.core import clustering

        known, target = np.zeros(512, dtype="float32"), np.zeros(512, dtype="float32")
        known[0] = 1.0
        target[0], target[1] = 0.6, 0.8
        self.face_at(self.make_photo("known.jpg", []), known, name="Jane Doe")
        photo = self.make_photo("target.jpg", [])
        face = self.face_at(photo, target)

        body = self.faces_of(photo)
        entry = next(f for f in body["faces"] if f["id"] == face)
        self.assertLess(0.6, clustering.OFFER_A_NAME)
        self.assertIsNone(entry["suggestion"])

    def test_a_named_face_carries_no_suggestion(self):
        photo = self.make_photo("a.jpg", [])
        self.add_face(photo, 1, name="Jane Doe")
        self.assertIsNone(self.faces_of(photo)["faces"][0]["suggestion"])

    def test_excluded_faces_are_reported_but_not_counted_as_unidentified(self):
        photo = self.make_photo("a.jpg", [])
        self.add_face(photo, 1, excluded=1, reason="stranger")

        body = self.faces_of(photo)
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["unmatched"], 0, "an excluded face counted as work to do")
        self.assertTrue(body["faces"][0]["excluded"])
        self.assertEqual(body["faces"][0]["excluded_reason"], "stranger")

    def test_an_excluded_face_is_never_used_as_a_suggestion(self):
        known = self.make_photo("known.jpg", [])
        self.add_face(known, 20, name="Jane Doe", excluded=1)

        target = self.make_photo("target.jpg", [])
        self.add_face(target, 20)

        self.assertIsNone(self.faces_of(target)["faces"][0]["suggestion"])

    def test_named_faces_are_listed_before_unidentified_ones(self):
        photo = self.make_photo("a.jpg", [])
        self.add_face(photo, 2, box=(60, 0, 110, 50))
        self.add_face(photo, 1, name="Jane Doe")

        self.assertIsNotNone(self.faces_of(photo)["faces"][0]["name"], "unidentified face sorted first")

    def test_rejects_a_missing_path(self):
        with self.assertRaises(Failed) as ctx:
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
        face_id = self.lib.execute(
            "INSERT INTO faces (photo_id, box, embedding, name, prob) VALUES ((SELECT id FROM photos WHERE path = ?), ?, ?, NULL, 0.9)",
            (photo, json.dumps([0, 0, 20, 20]), vec.tobytes()),
        )

        reply = self.lib.client.get("/api/face-crop", query_string={"id": face_id})
        self.assertEqual(reply.status_code, 200)
        self.assertTrue(reply.headers.get("Content-Type").startswith("image/"))
        self.assertGreater(len(reply.data), 0)

        cached = self.lib.rows(
            "SELECT (SELECT jpeg FROM face_crops c WHERE c.face_id = faces.id) FROM faces WHERE id = ?", (face_id,)
        )[0][0]
        self.assertTrue(cached, "crop was not cached back into the row")

    def test_rejects_an_unknown_face(self):
        self.assertEqual(self.lib.client.get("/api/face-crop", query_string={"id": 999999}).status_code, 404)

    def test_rejects_a_non_numeric_id(self):
        self.assertEqual(self.lib.client.get("/api/face-crop", query_string={"id": "abc"}).status_code, 400)


class TestIndexerNoiseFiltering(unittest.TestCase):
    """Library chatter must not reach the progress bar.

    Reported from the running app: huggingface_hub prints

        Warning: You are sending unauthenticated requests to the HF Hub...

    as a bare line with no prefix, so it passed the logging-format filter, stuck on
    the progress bar and read as though something had gone wrong. These match the
    *shapes* chatter arrives in -- blacklisting one library's wording only waits for
    the next library to add some.
    """

    def summarize(self, line):
        from tagpup.services.indexing import summarize_indexer_line
        return summarize_indexer_line(line)

    def test_the_bare_huggingface_warning_is_dropped(self):
        self.assertIsNone(self.summarize(
            "Warning: You are sending unauthenticated requests to the HF Hub. "
            "Please set a HF_TOKEN to enable higher rate limits and faster downloads."
        ))

    def test_the_logger_form_of_the_same_warning_is_dropped(self):
        self.assertIsNone(self.summarize(
            "WARNING:huggingface_hub.utils._http:Warning: You are sending "
            "unauthenticated requests to the HF Hub."
        ))

    def test_timestamped_library_logging_is_dropped(self):
        self.assertIsNone(self.summarize(
            "2026-09-19 22:55:30,914 [INFO] httpx - HTTP Request: HEAD https://x"
        ))

    def test_every_logging_level_in_the_logger_form_is_dropped(self):
        for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            self.assertIsNone(
                self.summarize("%s:some.module:a message" % level), level
            )

    def test_a_warnings_warn_source_line_is_dropped(self):
        self.assertIsNone(self.summarize(
            r"C:\x\y.py:42: UserWarning: something happened"
        ))

    def test_the_echoed_warn_statement_is_dropped(self):
        self.assertIsNone(self.summarize("  warnings.warn("))

    def test_a_bare_traceback_frame_is_dropped(self):
        self.assertIsNone(self.summarize('  File "C:/x/y.py", line 3, in f'))

    def test_the_progress_bar_still_gets_through(self):
        self.assertEqual(
            self.summarize("Generating embeddings:  42%|####  | 21/50 [00:12<00:16]"),
            "Generating embeddings: 42% (21/50)",
        )

    def test_the_indexer_own_console_output_still_gets_through(self):
        for line in ("Scanning directory: D:/Training/Pictures",
                     "Found 68083 image(s) total.",
                     "Indexing successfully completed!"):
            self.assertEqual(self.summarize(line), line)

    def test_a_very_long_message_is_trimmed_rather_than_cut_off_mid_screen(self):
        long_line = "Indexing " + ("x" * 300)
        out = self.summarize(long_line)
        self.assertLessEqual(len(out), 90)
        self.assertTrue(out.endswith("…"), out[-5:])

    def test_a_message_that_fits_is_left_alone(self):
        self.assertEqual(self.summarize("Found 42 image(s)."), "Found 42 image(s).")


class TestIndexerProgressText(unittest.TestCase):
    """What the indexing progress bar is allowed to say.

    The indexer's stdout carries console output, tqdm bars and library logging. Showing
    every line put things like "2026-09-19 21:31:50,515 [INFO] root - Instantiating..."
    in the progress bar, where it means nothing to the person watching and is too long
    to read anyway.
    """

    def summarize(self, line):
        from tagpup.services.indexing import summarize_indexer_line

        return summarize_indexer_line(line)

    def test_library_logging_is_suppressed(self):
        for line in (
            "2026-09-19 21:31:50,515 [INFO] root - Instantiating model from config",
            "2026-09-19 21:31:50,515 [WARNING] httpx - HTTP Request: HEAD https://x/y",
            "2026-09-19 07:39:12,235 [INFO] tagpup_cli.index - Loaded 56959 entries",
        ):
            self.assertIsNone(self.summarize(line), f"log line leaked: {line[:50]}")

    def test_a_progress_bar_becomes_readable_progress(self):
        out = self.summarize("Generating embeddings:  71%|#######1  | 35/49 [01:16<01:03,  4.52s/it]")
        self.assertEqual(out, "Generating embeddings: 71% (35/49)")

    def test_console_output_is_passed_through(self):
        for line in ("Found 49 image(s) total.",
                     "Indexing 49 image(s) (24 already tagged, 25 untagged)."):
            self.assertEqual(self.summarize(line), line)

    def test_blank_lines_are_ignored(self):
        self.assertIsNone(self.summarize(""))
        self.assertIsNone(self.summarize("   \n"))
        self.assertIsNone(self.summarize(None))

    def test_only_the_newest_tqdm_frame_is_kept(self):
        """tqdm redraws with carriage returns; the last frame is the current one."""
        out = self.summarize(
            "Generating embeddings:  10%|#  | 5/49 [00:10<01:00]\r"
            "Generating embeddings:  20%|##  | 10/49 [00:20<01:00]"
        )
        self.assertIn("20%", out)
        self.assertNotIn("10%", out)

    def test_the_percentage_is_still_parseable_for_the_bar(self):
        """The caller scrapes a percent out of the message to drive the bar width."""
        import re

        out = self.summarize("Generating embeddings:  45%|####  | 22/49 [00:30<00:40]")
        self.assertIsNotNone(re.search(r"(\d+)%", out))
        self.assertEqual(re.search(r"(\d+)%", out).group(1), "45")


if __name__ == "__main__":
    unittest.main()
