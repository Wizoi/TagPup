"""The page tests that run against the real apps, not a hand-written fake (contract table, rows 12 and 22).

tests/page_server.py starts both apps over a home of this test's own and runs the node tests named below, whose `fetch` goes
to them (tests/frontend/harness.mjs, `realLibrary`). Each library is made by a seed here, with the code that makes the real
rows. Under a plain `node --test` those node tests skip themselves.
"""
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_exiftool  # noqa: E402
import migration_names  # noqa: E402
import page_server  # noqa: E402
import photo_rows  # noqa: E402
from test_faces_from_tags import Case  # noqa: E402
from test_name_faces_routes import fill  # noqa: E402
from test_organize_faces import ODA, WREN  # noqa: E402
from test_organize_faces import Case as OrganizeCase  # noqa: E402

from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db, schema  # noqa: E402
from tagpup.web import libraries as web_libraries  # noqa: E402


def names_faces(library):
    case = Case()
    case.make_library(library.path)
    fill(case)
    return {"folder": case.folder, "photo": case.single}


def holding(library):
    """The organize tests' case over `library`, whose folder (a folder of its own) it holds."""
    case = OrganizeCase()
    case.path, case.folder = library.path, os.path.splitext(library.path)[0] + " Pictures"
    case.plant_tree()
    db.write_with_connection(library.path, lambda conn: photo_rows.hold(conn, case.folder))
    return case


def tagged_photos(files):
    """a.jpg holds Beach, b.jpg Portrait, c.jpg nothing; ExifTool answers the same for their files."""
    def seed(library):
        case = holding(library)
        for name, tags in (("a.jpg", ["Beach"]), ("b.jpg", ["Portrait"]), ("c.jpg", [])):
            files.keep_tags(case.photo(name, tags), tags)
        return {"folder": case.folder}
    return seed


def organized_photo(files, only=None):
    """a.jpg holds three unnamed faces (the second alike Wren's, the third Oda's), or `only` of them; r1 and r2 hold their
    decided faces."""
    def seed(library):
        case = holding(library)
        photo = case.photo("a.jpg", ["Trips"], size=(400, 300))
        looks = [((40, 30, 240, 230), 60), ((100, 50, 140, 90), 3), ((200, 60, 260, 120), 40)]
        mine = [case.face(photo, box=box, degrees=degrees) for box, degrees in looks[:only]]
        case.face(case.photo("r1.jpg"), name=WREN, name_source="manual", degrees=0)
        case.face(case.photo("r2.jpg"), name=ODA, name_source="manual", degrees=45)
        files.keep_tags(photo, ["Trips"])
        return {"folder": case.folder, "photo": photo, "faces": mine}
    return seed


def unknown_faces(library):
    """Five unnamed faces: three alike Wren's decided face (a cluster that offers her name), two alike each other."""
    case = holding(library)
    photos = [case.photo("p%d.jpg" % n) for n in range(5)]
    mine = [case.face(photo, degrees=degrees) for photo, degrees in zip(photos, (0, 2, 4, 90, 92))]
    case.face(case.photo("r1.jpg"), name=WREN, name_source="manual", degrees=1)
    return {"faces": mine, "photos": photos}


class PagesAgainstTheRealServer(unittest.TestCase):
    def test_name_faces_from_tags(self):
        page_server.PageServers(self, {"names_faces": names_faces}).run_node("name-faces.test.mjs")

    def test_tagging_a_photo_and_reading_it_back(self):
        page_server.PageServers(self, {"tagged_photos": tagged_photos(fake_exiftool.standing_in(self))}).run_node("tagging-flow.test.mjs")

    def test_naming_a_face_in_the_organize_view(self):
        files = fake_exiftool.standing_in(self)
        page_server.PageServers(self, {"organized_photo": organized_photo(files), "one_face": organized_photo(files, 1)}).run_node(
            "organize-faces.test.mjs")

    def test_a_library_view_opened_while_its_library_migrates(self):
        """The new version's migration of a library runs as the page opens a view of it (I4 under an open page, #1225):
        the step waits until the server has answered a request for the library 503, so the page is refused at least once
        and has to wait it out; then the library is migrated and the view is served."""
        files, release, refused, migrations = fake_exiftool.standing_in(self), threading.Event(), [], []

        def slowly(conn):
            self.assertTrue(release.wait(60), "no request for the library was answered 503 while it migrated")
            conn.execute("CREATE TABLE IF NOT EXISTS %s (id INTEGER PRIMARY KEY)" % migration_names.STEP_TABLE)
        step = migration_names.step(apply=slowly, touches=(migration_names.STEP_TABLE,))
        answer = web_libraries.LibraryFromUrl._refuse_updating

        def refusing(environ, start_response):
            refused.append(environ.get("PATH_INFO"))
            release.set()
            return answer(environ, start_response)
        self.enterContext(mock.patch.object(web_libraries.LibraryFromUrl, "_refuse_updating", staticmethod(refusing)))

        def migrating(library):
            facts = tagged_photos(files)(library)     # at the baseline, then the code is given a step it has not run
            self.enterContext(migration_names.ahead(step))
            migrations.append((library, library_actions.bring_up_to_date_in_background([library])))
            return facts

        page_server.PageServers(self, {"migrating": migrating}).run_node("scenario-write-through-a-server-update.test.mjs")
        library, thread = migrations[0]
        thread.join(60)
        self.assertTrue(refused, "the page was never told the library was being brought up to date")
        self.assertEqual(step.version, schema.version_of(library.path))

    def test_identify_faces_ignore_assign_and_undo(self):
        page_server.PageServers(self, {"unknown_faces": unknown_faces}).run_node("tuner-undo-puts-faces-back.test.mjs")


if __name__ == "__main__":
    unittest.main()
