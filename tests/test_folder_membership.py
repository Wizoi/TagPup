"""Whether a library holds a folder: one answer, which every entry point asks.

With kr-track selected, the owner opened a folder another library held, on a network
share outside kr-track's root folders, and Suggest made 25 rows for its photos in
kr-track without asking (2026-09-28). The page is to ask first -- "Add this folder to
kr-track?" -- saying how many photos the folder holds, whether it is outside the
library's roots, and which other library already holds them. This is what it asks:
tagpup.services.libraries.membership, and GET /api/folder/membership.

A folder is a library's when the library holds a photo directly in it
(tagpup.store.photos.holds_folder): a photo in a folder under it does not make it so.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import web_client  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402


def make_photos(folder, *names):
    os.makedirs(folder, exist_ok=True)
    made = []
    for name in names:
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(b"jpeg")
        made.append(path)
    return made


def index(db_path, photo_paths):
    """Record each photo as the indexer does after reading it."""
    conn = db.connect(db_path)
    try:
        for path in photo_paths:
            photo_rows.add_read(conn, path, {"XMP:Subject": ["Trips/Harbour"]})
        conn.commit()
    finally:
        conn.close()


class Libraries:
    """Two libraries in one home: harbour holds Regatta's own photos, and quayside every
    photo of the share's Lighthouse folder. Regatta's Scans subfolder is held by none."""

    def make(self, root):
        self.regatta = os.path.join(root, "Pictures", "Regatta")
        self.scans = os.path.join(self.regatta, "Scans")
        self.lighthouse = os.path.join(root, "Share", "Lighthouse")
        held = make_photos(self.regatta, "regatta_01.jpg", "regatta_02.jpg")
        make_photos(self.scans, "scan_01.jpg")
        shared = make_photos(self.lighthouse, "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.jpg")
        self.harbour = Library(self.home.library("harbour.db"))
        self.quayside = Library(self.home.library("quayside.db"))
        for library in (self.harbour, self.quayside):
            library_actions.create(library.path)
        index(self.harbour.path, held)
        index(self.quayside.path, shared)


class AFolderIsHeld(Libraries, unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.make(self.home.root)

    def holds(self, library, folder):
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            return store_photos.holds_folder(conn, folder)
        finally:
            conn.close()

    def test_only_by_a_photo_directly_in_it(self):
        self.assertTrue(self.holds(self.harbour, self.regatta))
        self.assertTrue(self.holds(self.harbour, self.regatta.upper()), "another spelling of the folder")
        self.assertFalse(self.holds(self.harbour, self.scans), "a folder under a held one is not held")
        self.assertFalse(self.holds(self.harbour, os.path.dirname(self.regatta)),
                         "a photo in a folder under it does not make a folder held")
        self.assertFalse(self.holds(self.harbour, self.regatta + " 2"), "a sibling whose name starts the same")
        self.assertFalse(self.holds(self.harbour, self.lighthouse))

    def test_the_folders_not_held_are_named_once_each(self):
        photos = [os.path.join(self.regatta, "regatta_01.jpg"), os.path.join(self.scans, "scan_01.jpg"),
                  os.path.join(self.scans, "scan_02.jpg"), os.path.join(self.lighthouse, "IMG_0001.jpg")]
        self.assertEqual(sorted([self.scans, self.lighthouse]),
                         sorted(library_actions.not_held(self.harbour, photos)))
        self.assertEqual([], library_actions.not_held(self.quayside, photos[3:]))

    def test_a_library_not_on_disk_holds_nothing(self):
        gone = Library(self.home.library("gone.db"))
        self.assertEqual([self.lighthouse],
                         library_actions.not_held(gone, [os.path.join(self.lighthouse, "IMG_0001.jpg")]))
        self.assertFalse(os.path.exists(gone.path), "asking made the library")


class Membership(Libraries, unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.make(self.home.root)

    def test_a_folder_another_library_holds(self):
        found = library_actions.membership(self.harbour, self.lighthouse, roots=[os.path.dirname(self.regatta)],
                                           others=[self.harbour, self.quayside])
        self.assertEqual({
            "library": "harbour", "folder": self.lighthouse, "photos": 3, "photos_held": 0,
            "photos_not_held": 3, "folders_not_held": 1, "first_not_held": self.lighthouse,
            "has_roots": True, "under_roots": False, "ignored": False,
            "others": [{"library": "quayside", "photos": 3}],
        }, found)

    def test_a_held_folder_with_a_subfolder_not_held(self):
        found = library_actions.membership(self.harbour, self.regatta, roots=[self.regatta], ignored=[self.scans],
                                           others=[self.quayside])
        self.assertEqual((3, 2, 1, 1, self.scans), (found["photos"], found["photos_held"], found["photos_not_held"],
                                                   found["folders_not_held"], found["first_not_held"]))
        self.assertEqual((True, True, False, []), (found["has_roots"], found["under_roots"], found["ignored"],
                                                  found["others"]))

    def test_an_ignored_folder_says_so(self):
        found = library_actions.membership(self.harbour, self.scans, roots=[self.regatta], ignored=[self.scans])
        self.assertEqual((True, True, 0), (found["under_roots"], found["ignored"], found["photos_held"]))

    def test_a_library_without_roots(self):
        found = library_actions.membership(self.quayside, self.lighthouse)
        self.assertEqual((False, False, 3, 0, 0), (found["has_roots"], found["under_roots"], found["photos_held"],
                                                   found["photos_not_held"], found["folders_not_held"]))


class TheRoute(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.folder = os.path.join(self.home.root, "Share", "Lighthouse")
        shared = make_photos(self.folder, "IMG_0001.jpg", "IMG_0002.jpg")
        other = self.home.library("quayside.db")
        library_actions.create(other)
        index(other, shared)

    def test_says_what_this_library_and_the_others_hold(self):
        reply = self.client.get("/library/api/folder/membership", query_string={"path": self.folder})
        self.assertEqual(200, reply.status_code, reply.data)
        found = reply.get_json()
        self.assertEqual(("library", 2, 0, 2), (found["library"], found["photos"], found["photos_held"],
                                                found["photos_not_held"]))
        self.assertEqual([{"library": "quayside", "photos": 2}], found["others"])
        self.assertFalse(found["has_roots"])

    def test_refuses_what_is_not_a_folder(self):
        self.assertEqual(400, self.client.get("/library/api/folder/membership").status_code)
        missing = os.path.join(self.home.root, "Nowhere")
        self.assertEqual(400, self.client.get("/library/api/folder/membership",
                                              query_string={"path": missing}).status_code)

    def test_asking_writes_nothing(self):
        path = self.home.library("library.db")
        before = os.path.getmtime(path)
        self.client.get("/library/api/folder/membership", query_string={"path": self.folder})
        conn = db.connect(db.readonly_uri(path), uri=True)
        try:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0])
        finally:
            conn.close()
        self.assertEqual(before, os.path.getmtime(path))


if __name__ == "__main__":
    unittest.main()
