"""A tag or a name that breaks the rules is refused where it is set, by both servers.

Nothing stopped one before. A tag holding "|" was written into a photo file, where
some programs read it as a break between levels and others as part of a name. The
rules themselves are tagpup.core.validation's, tested with the cases the pages share
(test_validation.py); this checks that every way in asks them, and that a refusal
writes nothing.

Only what is being set is checked. A photo already holding a bad tag from another
program still saves, and can lose it.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from face_rows import add_face  # noqa: E402
from handler_harness import Library  # noqa: E402
import tuner_client  # noqa: E402

from tagpup.store import db as tagpup_db  # noqa: E402

PIPE = 'A tag cannot contain "|": other programs read it as a break between levels. Use "/" instead.'


def fake_exiftool(holds):
    """An ExifTool session whose file holds `holds`, and which records every write."""
    et = MagicMock()
    et.get_tags.return_value = [{"XMP:Subject": list(holds)}]
    session = MagicMock()
    session.return_value.__enter__.return_value = et
    session.return_value.__exit__.return_value = False
    return session, et


class TagPupCase(unittest.TestCase):
    def setUp(self):
        self.lib = Library(self, "rules")
        self.db_path = self.lib.db_path
        self.photo = os.path.join(self.lib.root, "IMG_0100.jpg")
        with open(self.photo, "wb") as f:
            f.write(b"not really a jpeg")

    def call(self, path, body):
        """Ask one route as the page would; return (status, what it sent: the JSON, or
        an error's message)."""
        status, reply = self.lib.post(path, body)
        return status, reply if status == 200 else reply["error"]

    def rows(self, sql, *params):
        return self.lib.rows(sql, params)


class TagPupRefuses(TagPupCase):
    def test_saving_a_photo_with_a_new_bad_tag_writes_nothing(self):
        session, et = fake_exiftool(["Places/Harbour"])
        with patch("tagpup.files.exiftool_session.ExifToolSession", session):
            status, reply = self.call("/api/photo/save-metadata",
                                      {"path": self.photo, "title": "Harbour at dusk",
                                       "tags": ["Places/Harbour", "People|Rowan Thackeray"]})
        self.assertEqual((status, reply), (400, PIPE))
        et.set_tags.assert_not_called()
        et.execute.assert_not_called()

    def test_a_bad_tag_the_photo_already_holds_does_not_stop_a_save(self):
        # From another program. Refusing it would leave the photo unsaveable.
        session, et = fake_exiftool(["Legacy|Keyword"])
        with patch("tagpup.files.exiftool_session.ExifToolSession", session), \
                patch("tagpup.files.metadata.sync_title_to_filename", side_effect=lambda p, *rest: p):
            status, reply = self.call("/api/photo/save-metadata",
                                      {"path": self.photo, "title": "Harbour at dusk",
                                       "tags": ["Legacy|Keyword", "Places/Harbour"]})
        self.assertEqual(status, 200, reply)
        et.set_tags.assert_called()

    def test_adding_a_bad_tag_to_many_photos_writes_nothing(self):
        session, et = fake_exiftool([])
        with patch("tagpup.files.exiftool_session.ExifToolSession", session):
            status, reply = self.call("/api/photos/bulk-tags",
                                      {"paths": [self.photo], "add_tags": ["Places//Harbour"],
                                       "remove_tags": []})
        self.assertEqual(status, 400)
        self.assertIn("empty level", reply)
        session.assert_not_called()

    def test_removing_a_bad_tag_is_still_allowed(self):
        session, et = fake_exiftool(["Legacy|Keyword"])
        with patch("tagpup.files.exiftool_session.ExifToolSession", session):
            status, reply = self.call("/api/photos/bulk-tags",
                                      {"paths": [self.photo], "add_tags": [],
                                       "remove_tags": ["Legacy|Keyword"]})
        self.assertEqual(status, 200, reply)

    def test_creating_a_bad_tag_creates_nothing(self):
        status, reply = self.call("/api/taxonomy/create", {"name": "People/Rowan\tThackeray"})
        self.assertEqual(status, 400)
        self.assertIn("control character", reply)
        self.assertEqual(self.rows("SELECT tag FROM tag_taxonomy"), [])

    def test_renaming_a_tag_takes_one_level(self):
        status, _ = self.call("/api/taxonomy/create", {"name": "Activity/Hiking"})
        self.assertEqual(status, 200)
        (node_id,), = self.rows("SELECT id FROM tag_taxonomy WHERE tag = 'Activity/Hiking'")

        status, reply = self.call("/api/taxonomy/rename", {"tag_id": node_id, "new_name": "Trail/Running"})
        self.assertEqual((status, reply),
                         (400, 'A name cannot contain "/": it separates the levels of a tag.'))
        self.assertEqual(sorted(t for (t,) in self.rows("SELECT tag FROM tag_taxonomy")),
                         ["Activity", "Activity/Hiking"])


def subjects_written(et):
    """Every XMP:Subject value the session was asked to write."""
    written = []
    for call in et.set_tags.call_args_list:
        tags = call.kwargs.get("tags") or (call.args[1] if len(call.args) > 1 else {})
        written.extend(tags.get("XMP:Subject") or [])
    return written


class ANewTagIsWrittenInItsOneSpelling(TagPupCase):
    """As the tag tree spells it. A typed "People / Rowan" was written with its spaces,
    beside the tree's People/Rowan -- two tags where there should be one."""

    def test_a_photo_save(self):
        session, et = fake_exiftool(["Places/Harbour"])
        with patch("tagpup.files.exiftool_session.ExifToolSession", session), \
                patch("tagpup.files.metadata.sync_title_to_filename", side_effect=lambda p, *rest: p):
            status, reply = self.call("/api/photo/save-metadata",
                                      {"path": self.photo, "title": "",
                                       "tags": ["Places/Harbour", "People / Rowan Thackeray"]})
        self.assertEqual(status, 200, reply)
        self.assertEqual(subjects_written(et), ["Places/Harbour", "People/Rowan Thackeray"])

    def test_a_tag_the_photo_already_holds_is_left_as_it_is(self):
        # Rewriting what another program wrote is not this save's business.
        session, et = fake_exiftool(["Legacy / Keyword"])
        with patch("tagpup.files.exiftool_session.ExifToolSession", session), \
                patch("tagpup.files.metadata.sync_title_to_filename", side_effect=lambda p, *rest: p):
            status, reply = self.call("/api/photo/save-metadata",
                                      {"path": self.photo, "title": "",
                                       "tags": ["Legacy / Keyword"]})
        self.assertEqual(status, 200, reply)
        self.assertEqual(subjects_written(et), ["Legacy / Keyword"])

    def test_adding_to_many_photos(self):
        session, et = fake_exiftool([])
        with patch("tagpup.files.exiftool_session.ExifToolSession", session):
            status, reply = self.call("/api/photos/bulk-tags",
                                      {"paths": [self.photo], "add_tags": [" Places / Harbour "],
                                       "remove_tags": []})
        self.assertEqual(status, 200, reply)
        self.assertEqual(subjects_written(et), ["Places/Harbour"])


class TunerCase(unittest.TestCase):
    """The same rules asked of TagTuner's app, on the same scratch library."""

    def setUp(self):
        self.lib = Library(self, "rules")
        self.db_path = self.lib.db_path
        self.photo = os.path.join(self.lib.root, "IMG_0100.jpg")
        with open(self.photo, "wb") as f:
            f.write(b"not really a jpeg")
        self.requests = tuner_client.Requests(tuner_client.app_on(self.db_path))
        self.addCleanup(tuner_client.forget, self.db_path)

    def call(self, path, body):
        """Ask one route as the page would; return (status, what it sent: the JSON, or
        an error's message)."""
        status, reply = self.requests.post(path, body)
        return status, reply["error"] if status != 200 and isinstance(reply, dict) else reply

    def rows(self, sql, *params):
        return self.lib.rows(sql, params)


class ANewTagIsWrittenInItsOneSpellingByTagTuner(TunerCase):
    def test_merging_into_a_tag(self):
        status, plan = self.call("/api/tags/merge", {"from": "Kentridge", "into": "School / Kentridge"})
        self.assertEqual(status, 200, plan)
        self.assertEqual(plan["into"], "School/Kentridge")


class TagTunerRefuses(TunerCase):
    def seed_face(self, name=None):
        conn = tagpup_db.connect(self.db_path)
        try:
            face_id = add_face(conn, os.path.abspath(self.photo), box="[]", name=name, prob=1.0)
            conn.commit()
        finally:
            conn.close()
        return face_id

    def test_naming_a_face_with_a_path_names_nobody(self):
        face_id = self.seed_face()
        status, reply = self.call("/api/face/match", {"face_id": face_id, "person_name": "People/Rowan Thackeray"})
        self.assertEqual(status, 400)
        self.assertIn('contain "/"', reply)
        self.assertEqual(self.rows("SELECT name FROM faces WHERE id = ?", face_id), [(None,)])

    def test_naming_many_faces_is_held_to_the_same_rule(self):
        face_id = self.seed_face()
        status, reply = self.call("/api/faces/match-bulk", {"face_ids": [face_id], "person_name": "Rowan|Thackeray"})
        self.assertEqual(status, 400)
        self.assertIn('contain "|"', reply)
        self.assertEqual(self.rows("SELECT name FROM faces WHERE id = ?", face_id), [(None,)])

    def test_renaming_a_person_is_held_to_the_same_rule(self):
        face_id = self.seed_face("Rowan Thackeray")
        status, reply = self.call("/api/person/rename",
                                  {"old_name": "Rowan Thackeray", "new_name": "Rowan\nThackeray"})
        self.assertEqual(status, 400)
        self.assertIn("control character", reply)
        self.assertEqual(self.rows("SELECT name FROM faces WHERE id = ?", face_id),
                         [("Rowan Thackeray",)])

    def test_merging_into_a_bad_tag_is_refused_before_the_plan(self):
        status, reply = self.call("/api/tags/merge", {"from": "Activity/Hiking", "into": "Activity|Trail"})
        self.assertEqual((status, reply), (400, PIPE))


if __name__ == "__main__":
    unittest.main()
