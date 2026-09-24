"""Lifecycle tests for the TagPup tag taxonomy management endpoints.

These endpoints are the most destructive surface in the app: `taxonomy/rename` and
`taxonomy/delete-confirm` do not merely edit database rows, they rewrite keyword
metadata inside the user's actual photo files via ExifTool, and the change cascades
to every descendant tag. A silent defect here corrupts a library irreversibly.

Each test therefore asserts on BOTH sides of the write:
  * the `tag_taxonomy` / `photos` rows in SQLite, and
  * the keywords actually present in the JPEG on disk, read back through ExifTool.

Tests that mutate photo files are skipped when ExifTool is unavailable, so the pure
database behaviours still run in a bare environment.
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

from tagpup_server import start_server as start_tagpup_server, set_active_db_path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from free_port import free_port  # noqa: E402


def _exiftool_path():
    import configparser

    config = configparser.ConfigParser(interpolation=None)
    config_path = os.path.join(WORKSPACE_DIR, "config.ini")
    default = os.path.join(
        os.environ.get("USERPROFILE", ""), r"AppData\Local\Programs\ExifTool\exiftool.exe"
    )
    if os.path.exists(config_path):
        config.read(config_path, encoding="utf-8")
        default = os.path.expandvars(config.get("paths", "exiftool", fallback=default))
    return default if os.path.exists(default) else None


EXIFTOOL = _exiftool_path()
requires_exiftool = unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")


class TaxonomyTestBase(unittest.TestCase):
    """Boots one TagPup server against a scratch database, reset between tests."""

    TEST_PORT = free_port()
    TEST_DB = os.path.join(WORKSPACE_DIR, "data", "test_taxonomy_lifecycle.db")

    @classmethod
    def setUpClass(cls):
        # Its own port: subclasses inherit the attribute, and a port
        # already held by the last class's server is refused.
        cls.TEST_PORT = free_port()
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
        self.tmpdir = tempfile.mkdtemp(prefix="tagpup_tax_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.addCleanup(set_active_db_path, None)
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute("DELETE FROM tag_taxonomy")
        conn.execute("DELETE FROM photos")
        # A rename reaches every face of the person, so one left by an earlier test,
        # or an earlier run whose database could not be removed, is renamed too.
        conn.execute("DELETE FROM faces")
        conn.commit()
        conn.close()

    # ---------- helpers ----------

    def post(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.TEST_PORT}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def get(self, path):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.TEST_PORT}{path}", timeout=30
        ) as r:
            return json.loads(r.read().decode("utf-8"))

    def create_tag(self, name, parent_id=None, has_face=0):
        """Create a tag through the route. `has_face` flags a new root as holding faces:
        nothing else does, whatever its name (docs/findings.md, #66)."""
        status, body = self.post(
            "/api/taxonomy/create",
            {"name": name, "parent_id": parent_id, "has_face": has_face},
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(body.get("success"), body)
        return body["id"], body["tag"]

    def taxonomy_rows(self):
        conn = sqlite3.connect(self.TEST_DB)
        rows = conn.execute(
            "SELECT tag, name, has_face, hidden_from_autocomplete FROM tag_taxonomy"
        ).fetchall()
        conn.close()
        return {r[0]: {"name": r[1], "has_face": r[2], "hidden": r[3]} for r in rows}

    def tag_id(self, tag_path):
        conn = sqlite3.connect(self.TEST_DB)
        row = conn.execute(
            "SELECT id FROM tag_taxonomy WHERE tag = ?", (tag_path,)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row, f"tag {tag_path!r} not in taxonomy")
        return row[0]

    def make_photo(self, filename, tags):
        """Create a real JPEG on disk with `tags` written into its keywords, plus a DB row."""
        from PIL import Image

        path = os.path.join(self.tmpdir, filename)
        Image.new("RGB", (32, 32), (120, 140, 160)).save(path, "JPEG")

        flat, hierarchical = [], []
        for t in tags:
            flat.append(t)
            if "/" in t:
                hierarchical.append(t)
                flat.extend(t.split("/"))
        flat = sorted(set(flat))
        hierarchical = sorted(set(hierarchical))

        if EXIFTOOL:
            import exiftool

            with exiftool.ExifToolHelper(executable=EXIFTOOL) as et:
                et.set_tags(
                    [path],
                    tags={
                        "XMP:Subject": flat,
                        "IPTC:Keywords": flat,
                        "XMP:HierarchicalSubject": hierarchical,
                    },
                    params=["-overwrite_original"],
                )

        raw_meta = {"XMP:Subject": flat, "XMP:HierarchicalSubject": hierarchical}
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                path,
                os.path.getmtime(path),
                os.path.getsize(path),
                json.dumps(tags),
                json.dumps([]),
                json.dumps([]),
                json.dumps(raw_meta),
            ),
        )
        conn.commit()
        conn.close()
        return path

    def db_tags(self, photo_path):
        conn = sqlite3.connect(self.TEST_DB)
        row = conn.execute(
            "SELECT tags FROM photos WHERE path = ?", (photo_path,)
        ).fetchone()
        conn.close()
        return json.loads(row[0]) if row and row[0] else []

    def disk_keywords(self, photo_path):
        """Read hierarchical + flat keywords back out of the actual file."""
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


class TestTaxonomyCreate(TaxonomyTestBase):
    def test_creates_root_tag(self):
        _, tag = self.create_tag("Activity")
        self.assertEqual(tag, "Activity")
        self.assertIn("Activity", self.taxonomy_rows())

    def test_creates_child_under_parent(self):
        parent_id, _ = self.create_tag("Activity")
        _, tag = self.create_tag("Hiking", parent_id=parent_id)
        self.assertEqual(tag, "Activity/Hiking")
        rows = self.taxonomy_rows()
        self.assertIn("Activity/Hiking", rows)
        # Leaf name must be the bare name, never the full path.
        self.assertEqual(rows["Activity/Hiking"]["name"], "Hiking")

    def test_a_child_two_levels_down_is_one_new_node(self):
        """Under Crew/Divers, "Jane" also made Crew/Divers/Crew, Crew/Divers/Crew/Divers
        and Crew/Divers/Crew/Divers/Jane, and the reply named the last of them."""
        root_id, _ = self.create_tag("Crew")
        mid_id, _ = self.create_tag("Divers", parent_id=root_id)
        jane_id, tag = self.create_tag("Jane", parent_id=mid_id)
        self.assertEqual(tag, "Crew/Divers/Jane")
        self.assertEqual(sorted(self.taxonomy_rows()), ["Crew", "Crew/Divers", "Crew/Divers/Jane"])
        self.assertEqual(jane_id, self.tag_id("Crew/Divers/Jane"))

    def test_a_child_typed_as_its_whole_path_is_not_doubled(self):
        root_id, _ = self.create_tag("Crew")
        mid_id, _ = self.create_tag("Divers", parent_id=root_id)
        _, tag = self.create_tag("Crew/Divers/Jane", parent_id=mid_id)
        self.assertEqual(tag, "Crew/Divers/Jane")
        self.assertEqual(sorted(self.taxonomy_rows()), ["Crew", "Crew/Divers", "Crew/Divers/Jane"])

    def test_a_child_may_be_a_path_below_its_parent(self):
        root_id, _ = self.create_tag("Crew")
        _, tag = self.create_tag("Divers/Jane", parent_id=root_id)
        self.assertEqual(tag, "Crew/Divers/Jane")
        rows = self.taxonomy_rows()
        self.assertEqual(sorted(rows), ["Crew", "Crew/Divers", "Crew/Divers/Jane"])
        self.assertEqual(rows["Crew/Divers"]["name"], "Divers")

    def test_builds_missing_intermediate_ancestors(self):
        """Creating 'People/Smith/Jane' must materialise every ancestor node."""
        _, tag = self.create_tag("People/Smith/Jane")
        self.assertEqual(tag, "People/Smith/Jane")
        rows = self.taxonomy_rows()
        for expected in ("People", "People/Smith", "People/Smith/Jane"):
            self.assertIn(expected, rows, f"missing ancestor {expected}")

    def test_a_root_holds_faces_only_when_created_so(self):
        """docs/findings.md, #66: a root named People, Family, Friends or Pets was made
        holding faces for its name. Only the tree says which roots do."""
        for root in ("People", "Family", "Friends", "Pets"):
            self.create_tag(root)
        self.create_tag("Crew", has_face=1)
        rows = self.taxonomy_rows()
        for root in ("People", "Family", "Friends", "Pets"):
            self.assertEqual(rows[root]["has_face"], 0, f"{root} was flagged for its name")
        self.assertEqual(rows["Crew"]["has_face"], 1, "Crew was asked to hold faces")

    def test_children_inherit_has_face_from_face_root(self):
        """Subnodes of a face root must carry has_face=1 or clustering skips them."""
        parent_id, _ = self.create_tag("People", has_face=1)
        self.create_tag("Jane Doe", parent_id=parent_id)
        rows = self.taxonomy_rows()
        self.assertEqual(rows["People/Jane Doe"]["has_face"], 1)

    def test_non_face_children_stay_unflagged(self):
        parent_id, _ = self.create_tag("Activity")
        self.create_tag("Hiking", parent_id=parent_id)
        self.assertEqual(self.taxonomy_rows()["Activity/Hiking"]["has_face"], 0)

    def test_creating_existing_tag_is_idempotent(self):
        first_id, _ = self.create_tag("Activity")
        second_id, _ = self.create_tag("Activity")
        self.assertEqual(first_id, second_id)
        self.assertEqual(len(self.taxonomy_rows()), 1)

    def test_rejects_empty_name(self):
        status, body = self.post("/api/taxonomy/create", {"name": "   "})
        self.assertEqual(status, 400)
        self.assertFalse(body.get("success"))

    def test_rejects_unknown_parent(self):
        status, _ = self.post(
            "/api/taxonomy/create", {"name": "Orphan", "parent_id": 999999}
        )
        self.assertEqual(status, 404)


class TestTaxonomyUpdate(TaxonomyTestBase):
    def test_has_face_propagates_to_all_descendants(self):
        root_id, _ = self.create_tag("Crew")
        mid_id, _ = self.create_tag("Divers", parent_id=root_id)
        self.create_tag("Jane", parent_id=mid_id)

        status, body = self.post("/api/taxonomy/update", {"id": root_id, "has_face": 1})
        self.assertEqual(status, 200, body)

        rows = self.taxonomy_rows()
        for tag in ("Crew", "Crew/Divers", "Crew/Divers/Jane"):
            self.assertEqual(rows[tag]["has_face"], 1, f"{tag} not propagated")

    def test_hidden_flag_propagates_to_all_descendants(self):
        root_id, _ = self.create_tag("Internal")
        self.create_tag("Secret", parent_id=root_id)
        self.post("/api/taxonomy/update", {"id": root_id, "hidden_from_autocomplete": 1})
        rows = self.taxonomy_rows()
        self.assertEqual(rows["Internal"]["hidden"], 1)
        self.assertEqual(rows["Internal/Secret"]["hidden"], 1)

    def test_sibling_branches_are_untouched(self):
        a_id, _ = self.create_tag("BranchA")
        self.create_tag("Leaf", parent_id=a_id)
        b_id, _ = self.create_tag("BranchB")
        self.create_tag("Leaf", parent_id=b_id)

        self.post("/api/taxonomy/update", {"id": a_id, "has_face": 1})
        rows = self.taxonomy_rows()
        self.assertEqual(rows["BranchA/Leaf"]["has_face"], 1)
        self.assertEqual(rows["BranchB/Leaf"]["has_face"], 0, "sibling branch was modified")

    def test_only_the_tags_under_it_are_its_branch(self):
        """docs/findings.md, #36: LIKE 'Club_A/%' also matched ClubXA/..., and club_a/...:
        LIKE reads `_` as any character and ignores case."""
        club_id, _ = self.create_tag("Club_A")
        self.create_tag("Relay", parent_id=club_id)
        for other in ("ClubXA", "club_a"):
            other_id, _ = self.create_tag(other)
            self.create_tag("Relay", parent_id=other_id)

        self.post("/api/taxonomy/update", {"id": club_id, "has_face": 1, "hidden_from_autocomplete": 1})
        rows = self.taxonomy_rows()
        self.assertEqual({tag: (row["has_face"], row["hidden"]) for tag, row in rows.items()}, {
            "Club_A": (1, 1), "Club_A/Relay": (1, 1),
            "ClubXA": (0, 0), "ClubXA/Relay": (0, 0),
            "club_a": (0, 0), "club_a/Relay": (0, 0),
        })

    def test_rejects_unknown_tag(self):
        status, _ = self.post("/api/taxonomy/update", {"id": 999999, "has_face": 1})
        self.assertEqual(status, 404)

    def test_rejects_missing_id(self):
        status, _ = self.post("/api/taxonomy/update", {"has_face": 1})
        self.assertEqual(status, 400)


class TestTaxonomyDeleteCheck(TaxonomyTestBase):
    def test_reports_unused_tag(self):
        tag_id, _ = self.create_tag("Activity")
        _, body = self.post("/api/taxonomy/delete-check", {"tag_id": tag_id})
        self.assertFalse(body["used"])
        self.assertEqual(body["count"], 0)

    def test_counts_photos_using_the_tag(self):
        parent_id, _ = self.create_tag("Activity")
        child_id, _ = self.create_tag("Hiking", parent_id=parent_id)
        self.make_photo("a.jpg", ["Activity/Hiking"])
        self.make_photo("b.jpg", ["Activity/Hiking"])
        self.make_photo("c.jpg", ["Holidays/Christmas"])

        _, body = self.post("/api/taxonomy/delete-check", {"tag_id": child_id})
        self.assertTrue(body["used"])
        self.assertEqual(body["count"], 2)

    def test_counts_photos_using_descendant_tags(self):
        """Deleting a parent must account for photos tagged only with its children."""
        parent_id, _ = self.create_tag("Activity")
        self.create_tag("Hiking", parent_id=parent_id)
        self.make_photo("a.jpg", ["Activity/Hiking"])

        _, body = self.post("/api/taxonomy/delete-check", {"tag_id": parent_id})
        self.assertTrue(body["used"])
        self.assertEqual(body["count"], 1)

    def test_rejects_unknown_tag(self):
        status, _ = self.post("/api/taxonomy/delete-check", {"tag_id": 999999})
        self.assertEqual(status, 404)


class TestTaxonomyDeleteConfirm(TaxonomyTestBase):
    def test_deletes_unused_tag_node(self):
        tag_id, _ = self.create_tag("Activity")
        status, body = self.post(
            "/api/taxonomy/delete-confirm", {"tag_id": tag_id, "action": "remove"}
        )
        self.assertEqual(status, 200, body)
        self.assertNotIn("Activity", self.taxonomy_rows())

    def test_deleting_parent_cascades_to_children(self):
        parent_id, _ = self.create_tag("Activity")
        self.create_tag("Hiking", parent_id=parent_id)
        self.post("/api/taxonomy/delete-confirm", {"tag_id": parent_id, "action": "remove"})
        rows = self.taxonomy_rows()
        self.assertNotIn("Activity", rows)
        self.assertNotIn("Activity/Hiking", rows, "orphaned child left behind")

    @requires_exiftool
    def test_remove_action_strips_tag_from_photo_on_disk_and_in_db(self):
        parent_id, _ = self.create_tag("Activity")
        child_id, _ = self.create_tag("Hiking", parent_id=parent_id)
        photo = self.make_photo("a.jpg", ["Activity/Hiking", "Holidays/Christmas"])

        status, body = self.post(
            "/api/taxonomy/delete-confirm", {"tag_id": child_id, "action": "remove"}
        )
        self.assertEqual(status, 200, body)

        self.assertNotIn("Activity/Hiking", self.db_tags(photo))
        self.assertIn("Holidays/Christmas", self.db_tags(photo), "unrelated tag was lost")

        on_disk = self.disk_keywords(photo)
        self.assertNotIn("Activity/Hiking", on_disk, "tag still written in the file")
        self.assertIn("Holidays/Christmas", on_disk, "unrelated tag stripped from file")

    @requires_exiftool
    def test_move_action_remaps_tag_on_disk_and_in_db(self):
        parent_id, _ = self.create_tag("Activity")
        child_id, _ = self.create_tag("Hiking", parent_id=parent_id)
        photo = self.make_photo("a.jpg", ["Activity/Hiking"])

        status, body = self.post(
            "/api/taxonomy/delete-confirm",
            {"tag_id": child_id, "action": "move", "target_tag": "Trips/Hiking"},
        )
        self.assertEqual(status, 200, body)

        self.assertIn("Trips/Hiking", self.db_tags(photo))
        self.assertNotIn("Activity/Hiking", self.db_tags(photo))
        self.assertIn("Trips/Hiking", self.disk_keywords(photo))
        # The move target must exist in the taxonomy afterwards.
        self.assertIn("Trips/Hiking", self.taxonomy_rows())

    def test_move_without_target_is_rejected(self):
        parent_id, _ = self.create_tag("Activity")
        child_id, _ = self.create_tag("Hiking", parent_id=parent_id)
        self.make_photo("a.jpg", ["Activity/Hiking"])
        status, _ = self.post(
            "/api/taxonomy/delete-confirm", {"tag_id": child_id, "action": "move"}
        )
        self.assertEqual(status, 400)

    def test_rejects_missing_action(self):
        tag_id, _ = self.create_tag("Activity")
        status, _ = self.post("/api/taxonomy/delete-confirm", {"tag_id": tag_id})
        self.assertEqual(status, 400)


class TestTaxonomyRename(TaxonomyTestBase):
    def test_renames_leaf_node(self):
        parent_id, _ = self.create_tag("Activity")
        child_id, _ = self.create_tag("Hiking", parent_id=parent_id)

        status, body = self.post(
            "/api/taxonomy/rename", {"tag_id": child_id, "new_name": "Trekking"}
        )
        self.assertEqual(status, 200, body)

        rows = self.taxonomy_rows()
        self.assertIn("Activity/Trekking", rows)
        self.assertNotIn("Activity/Hiking", rows)
        self.assertEqual(rows["Activity/Trekking"]["name"], "Trekking")

    def test_rename_cascades_to_descendant_paths(self):
        root_id, _ = self.create_tag("Outdoors")
        mid_id, _ = self.create_tag("Hiking", parent_id=root_id)
        self.create_tag("Cascades", parent_id=mid_id)

        self.post("/api/taxonomy/rename", {"tag_id": root_id, "new_name": "Nature"})

        rows = self.taxonomy_rows()
        for expected in ("Nature", "Nature/Hiking", "Nature/Hiking/Cascades"):
            self.assertIn(expected, rows, f"descendant not re-pathed: {expected}")
        self.assertNotIn("Outdoors/Hiking/Cascades", rows)

    def test_rename_to_existing_path_is_rejected(self):
        parent_id, _ = self.create_tag("Activity")
        self.create_tag("Hiking", parent_id=parent_id)
        other_id, _ = self.create_tag("Biking", parent_id=parent_id)

        status, body = self.post(
            "/api/taxonomy/rename", {"tag_id": other_id, "new_name": "Hiking"}
        )
        self.assertEqual(status, 400, body)
        rows = self.taxonomy_rows()
        self.assertIn("Activity/Biking", rows, "rejected rename still mutated the tree")

    def test_rename_to_same_name_is_a_noop(self):
        parent_id, _ = self.create_tag("Activity")
        child_id, _ = self.create_tag("Hiking", parent_id=parent_id)
        status, body = self.post(
            "/api/taxonomy/rename", {"tag_id": child_id, "new_name": "Hiking"}
        )
        self.assertEqual(status, 200, body)
        self.assertIn("Activity/Hiking", self.taxonomy_rows())

    @requires_exiftool
    def test_rename_rewrites_photo_tags_on_disk_and_in_db(self):
        parent_id, _ = self.create_tag("Activity")
        child_id, _ = self.create_tag("Hiking", parent_id=parent_id)
        photo = self.make_photo("a.jpg", ["Activity/Hiking", "Holidays/Christmas"])

        self.post("/api/taxonomy/rename", {"tag_id": child_id, "new_name": "Trekking"})

        self.assertIn("Activity/Trekking", self.db_tags(photo))
        self.assertNotIn("Activity/Hiking", self.db_tags(photo))
        self.assertIn("Holidays/Christmas", self.db_tags(photo), "unrelated tag was lost")

        on_disk = self.disk_keywords(photo)
        self.assertIn("Activity/Trekking", on_disk, "rename never reached the file")
        self.assertNotIn("Activity/Hiking", on_disk, "old tag still in the file")

    @requires_exiftool
    def test_rename_preserves_descendant_suffix_in_photo_tags(self):
        """A photo tagged with a grandchild must keep its full path under the new root."""
        root_id, _ = self.create_tag("Outdoors")
        mid_id, _ = self.create_tag("Hiking", parent_id=root_id)
        self.create_tag("Cascades", parent_id=mid_id)
        photo = self.make_photo("a.jpg", ["Outdoors/Hiking/Cascades"])

        self.post("/api/taxonomy/rename", {"tag_id": root_id, "new_name": "Nature"})

        self.assertIn("Nature/Hiking/Cascades", self.db_tags(photo))
        self.assertIn("Nature/Hiking/Cascades", self.disk_keywords(photo))

    def test_renaming_a_person_also_renames_their_resolved_faces(self):
        """Faces store the bare leaf name, so the rename must reach them too."""
        people_id, _ = self.create_tag("People", has_face=1)
        person_id, _ = self.create_tag("Jane Doe", parent_id=people_id)
        photo = self.make_photo("a.jpg", ["People/Jane Doe"])

        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name) VALUES (?, '[]', ?, ?)",
            (photo, b"", "Jane Doe"),
        )
        conn.execute(
            "UPDATE photos SET people = ? WHERE path = ?", ('["Jane Doe"]', photo)
        )
        conn.commit()
        conn.close()

        status, body = self.post(
            "/api/taxonomy/rename", {"tag_id": person_id, "new_name": "Jane Smith"}
        )
        self.assertEqual(status, 200, body)

        conn = sqlite3.connect(self.TEST_DB)
        face_names = {r[0] for r in conn.execute("SELECT name FROM faces").fetchall()}
        people = conn.execute(
            "SELECT people FROM photos WHERE path = ?", (photo,)
        ).fetchone()[0]
        conn.close()
        self.assertIn("Jane Smith", face_names, "resolved faces kept the old name")
        self.assertNotIn("Jane Doe", face_names)
        self.assertIn("Jane Smith", people)

    def test_renaming_a_person_renames_their_faces_whatever_the_case(self):
        """docs/findings.md, #38: TagTuner's rename of a person caught a face named in
        another case and this one did not, so the two renames of one person disagreed."""
        people_id, _ = self.create_tag("People", has_face=1)
        person_id, _ = self.create_tag("Jane Doe", parent_id=people_id)
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute("INSERT INTO faces (photo_path, box, embedding, name)"
                     " VALUES ('D:/case.jpg', '[]', ?, 'jane doe')", (b"",))
        conn.commit()
        conn.close()

        status, body = self.post("/api/taxonomy/rename", {"tag_id": person_id, "new_name": "Jane Smith"})
        self.assertEqual(status, 200, body)
        conn = sqlite3.connect(self.TEST_DB)
        names = [r[0] for r in conn.execute("SELECT name FROM faces WHERE photo_path = 'D:/case.jpg'")]
        conn.close()
        self.assertEqual(names, ["Jane Smith"])

    def test_renaming_a_keyword_tag_does_not_touch_faces(self):
        """Only face categories propagate to the faces table."""
        parent_id, _ = self.create_tag("Activity")
        child_id, _ = self.create_tag("Hiking", parent_id=parent_id)
        photo = self.make_photo("a.jpg", ["Activity/Hiking"])

        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name) VALUES (?, '[]', ?, ?)",
            (photo, b"", "Hiking"),
        )
        conn.commit()
        conn.close()

        self.post("/api/taxonomy/rename", {"tag_id": child_id, "new_name": "Trekking"})

        conn = sqlite3.connect(self.TEST_DB)
        face_names = {r[0] for r in conn.execute("SELECT name FROM faces").fetchall()}
        conn.close()
        self.assertIn("Hiking", face_names, "a keyword rename leaked into the faces table")

    def test_rejects_unknown_tag(self):
        status, _ = self.post(
            "/api/taxonomy/rename", {"tag_id": 999999, "new_name": "Whatever"}
        )
        self.assertEqual(status, 404)

    def test_rejects_empty_new_name(self):
        tag_id, _ = self.create_tag("Activity")
        status, _ = self.post("/api/taxonomy/rename", {"tag_id": tag_id, "new_name": "  "})
        self.assertEqual(status, 400)


class TestTaxonomyTree(TaxonomyTestBase):
    def test_tree_reports_hierarchy_and_flags(self):
        root_id, _ = self.create_tag("People", has_face=1)
        self.create_tag("Jane Doe", parent_id=root_id)
        self.post("/api/taxonomy/update", {"id": root_id, "hidden_from_autocomplete": 1})

        tree = self.get("/api/taxonomy/tree")
        nodes = tree if isinstance(tree, list) else tree.get("tree", tree.get("nodes", []))
        flat = {}

        def walk(items):
            for node in items:
                flat[node.get("tag")] = node
                walk(node.get("children", []) or [])

        walk(nodes)
        self.assertIn("People", flat)
        self.assertEqual(flat["People"].get("has_face"), 1)
        self.assertEqual(flat["People"].get("hidden_from_autocomplete"), 1)
        self.assertIn("People/Jane Doe", flat)

    def test_tree_reports_photo_usage_counts(self):
        parent_id, _ = self.create_tag("Activity")
        self.create_tag("Hiking", parent_id=parent_id)
        self.make_photo("a.jpg", ["Activity/Hiking"])
        self.make_photo("b.jpg", ["Activity/Hiking"])

        tree = self.get("/api/taxonomy/tree")
        nodes = tree if isinstance(tree, list) else tree.get("tree", tree.get("nodes", []))
        flat = {}

        def walk(items):
            for node in items:
                flat[node.get("tag")] = node
                walk(node.get("children", []) or [])

        walk(nodes)
        self.assertEqual(flat["Activity/Hiking"].get("usage_count"), 2)
        # Usage rolls up: a photo tagged with the child also counts toward the parent.
        self.assertEqual(flat["Activity"].get("usage_count"), 2)

    def test_face_matching_turned_off_stays_off_when_the_tree_is_read_again(self):
        """docs/findings.md, #40: reading the tree set every node under a root named
        Pets back to holding faces, so the switch the page shows on a root undid
        nothing below it."""
        # Another face root: the last one may not be switched off (#66).
        self.create_tag("People", has_face=1)
        root_id, _ = self.create_tag("Pets", has_face=1)
        self.create_tag("Biscuit", parent_id=root_id)
        self.post("/api/taxonomy/update", {"id": root_id, "has_face": 0})

        self.get("/api/taxonomy/tree")
        rows = self.taxonomy_rows()
        self.assertEqual((rows["Pets"]["has_face"], rows["Pets/Biscuit"]["has_face"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
