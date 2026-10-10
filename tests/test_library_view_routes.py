"""The library views' routes (tagpup.web.tagpup_routes: /api/library/navigator, /api/library/ids, /api/photo-thumb;
docs/SPEC_TAGPUP_GUI.md): Flask's test client, no port, no thread, no sleep, in a home of its own.

What the owner is told, in a sentence and not a traceback, is asserted for each refusal; the thumbnail's cache headers
are what let the browser keep it; every request goes through the Roots gate and ingress like the rest.
Photos are rows as the indexer records them; fictional names only.
"""
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import roots_library as rl  # noqa: E402
import web_client  # noqa: E402
from test_migrations import at_version  # noqa: E402
from view_library import ViewLibrary, make_jpeg  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import images, thumbs  # noqa: E402
from tagpup.services import damaged_photos  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web import libraries as web_libraries  # noqa: E402
from tagpup.web import roots_gate  # noqa: E402

REMOTE = {"REMOTE_ADDR": "10.0.0.7"}


class Routes(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.vl.tree("Trips/Coast", "Trips/Lakes", "People/Wren Halloway", face_root="People")
        self.a = self.vl.photo("2024 Coast", "a.jpg", taken="2024:06:01 10:00:00", tags=["Trips/Coast", "People/Wren Halloway"],
                               real=True, size=(900, 600))
        self.b = self.vl.photo("2024 Coast", "b.jpg", taken="2024:06:02 10:00:00", tags=["Trips/Lakes"], real=True)
        self.c = self.vl.photo(os.path.join("2024 Coast", "Day 2"), "c.jpg", taken="2023:01:02 10:00:00")
        self.d = self.vl.photo("Misc", "d.jpg")
        self.folder = os.path.join(self.vl.pictures, "2024 Coast")

    def get(self, path, **kwargs):
        return self.client.get("/library" + path, **kwargs)

    def view(self, **query):
        reply = self.get("/api/library/ids", query_string=query)
        return reply, reply.get_json()


class TheView(Routes):
    def test_all_is_ordered_by_date_taken_with_the_undated_last_and_has_a_total(self):
        reply, found = self.view(kind="all")
        self.assertEqual(200, reply.status_code)
        self.assertEqual([self.c, self.a, self.b, self.d], found["ids"])
        self.assertEqual((4, True), (found["total"], found["complete"]))
        self.assertEqual({"kind": "all", "value": None, "recursive": False}, found["source"])

    def test_a_json_answer_is_never_kept_by_the_browser(self):
        self.assertIn("no-store", self.view(kind="all")[0].headers["Cache-Control"])

    def test_a_folder_is_named_by_folder_and_recursive_takes_its_subfolders(self):
        _, found = self.view(kind="folder", folder=self.folder)
        self.assertEqual([self.a, self.b], found["ids"])
        _, found = self.view(kind="folder", folder=self.folder, recursive="true")
        self.assertEqual([self.c, self.a, self.b], found["ids"])
        _, found = self.view(kind="folder", folder=self.folder, recursive="false")
        self.assertEqual(2, found["total"])

    def test_a_folder_may_also_be_given_as_value(self):
        self.assertEqual([self.a, self.b], self.view(kind="folder", value=self.folder)[1]["ids"])

    def test_a_folder_typed_in_another_case_is_the_same_folder(self):
        if os.name != "nt":
            self.skipTest("a filesystem with case")
        self.assertEqual([self.a, self.b], self.view(kind="folder", folder=self.folder.lower())[1]["ids"])
        self.assertEqual([self.a, self.b], self.view(kind="folder", folder=self.folder.replace("\\", "/"))[1]["ids"])

    def test_a_keyword_a_person_a_year_and_a_month(self):
        self.assertEqual([self.a, self.b], self.view(kind="keyword", value="Trips")[1]["ids"])
        self.assertEqual([self.a], self.view(kind="person", value="Wren Halloway")[1]["ids"])
        self.assertEqual([self.a, self.b], self.view(kind="year", value="2024")[1]["ids"])
        self.assertEqual([self.a, self.b], self.view(kind="month", value="2024-06")[1]["ids"])
        self.assertEqual([self.c], self.view(kind="month", value="2023-01")[1]["ids"])

    def test_a_keyword_with_a_percent_or_a_quote_or_no_node_is_an_empty_page(self):
        for tag in ("Trips/%", "Trips/O'Brien", "No/Such/Tag", "Trips/_oast"):
            with self.subTest(tag=tag):
                reply, found = self.view(kind="keyword", value=tag)
                self.assertEqual((200, [], 0), (reply.status_code, found["ids"], found["total"]))

    def test_a_request_that_means_nothing_is_a_400_with_a_sentence(self):
        for query, said in ((dict(kind="nope"), "kind must be one of"), (dict(), "kind must be one of"),
                            (dict(kind="folder"), "needs a value"), (dict(kind="year", value="x"), "A year is"),
                            (dict(kind="month", value="2024-13"), "A month is"),
                            (dict(kind="keyword", value="   "), "needs a value")):
            with self.subTest(query=query):
                reply, found = self.view(**query)
                self.assertEqual(400, reply.status_code)
                self.assertFalse(found["success"])
                self.assertIn(said, found["error"])
                self.assertNotIn("Traceback", found["error"])

    def test_it_answers_this_pc_only(self):
        for path in ("/api/library/ids?kind=all", "/api/library/navigator?section=dates", "/api/photo-thumb?id=%d" % self.a):
            with self.subTest(path=path):
                self.assertEqual(403, self.get(path, environ_overrides=REMOTE).status_code)

    def test_a_library_no_photo_was_indexed_in_is_an_empty_list(self):
        ViewLibrary(self, "emptied", home=self.home)
        found = self.client.get("/emptied/api/library/ids?kind=all").get_json()
        self.assertEqual(([], 0), (found["ids"], found["total"]))


class TheNavigator(Routes):
    def nav(self, section):
        reply = self.get("/api/library/navigator", query_string={"section": section})
        return reply, reply.get_json()

    def test_each_section_answers_its_counts(self):
        _, found = self.nav("folders")
        self.assertIn(self.folder, [each["path"] for each in found["folders"]])
        _, found = self.nav("keywords")
        self.assertEqual(2, {each["tag"]: each["count"] for each in found["keywords"]}["Trips"])
        _, found = self.nav("people")
        self.assertEqual([("Wren Halloway", 1)], [(each["name"], each["count"]) for each in found["people"]])
        self.assertEqual({"people", "groups", "unfiled"}, set(found))
        _, found = self.nav("dates")
        self.assertEqual([2023, 2024], [each["year"] for each in found["years"]])
        self.assertEqual(1, found["undated"])

    def test_an_unknown_or_missing_section_is_a_400_with_a_sentence(self):
        for section in ("nope", "", None):
            reply = self.get("/api/library/navigator", query_string={"section": section} if section is not None else {})
            self.assertEqual(400, reply.status_code)
            self.assertIn("section must be one of", reply.get_json()["error"])


class TheThumbnail(Routes):
    def thumb(self, photo_id=None, **extra):
        headers = extra.pop("headers", None)
        query = dict(id=photo_id if photo_id is not None else self.a, **extra)
        return self.get("/api/photo-thumb", query_string=query, headers=headers)

    def stamp(self, photo_id=None):
        return self.vl.rows("SELECT mtime FROM photos WHERE id = ?", photo_id or self.a)[0][0]

    def test_it_is_a_jpeg_of_300_pixels(self):
        from io import BytesIO

        from PIL import Image
        reply = self.thumb()
        self.assertEqual((200, "image/jpeg"), (reply.status_code, reply.mimetype))
        picture = Image.open(BytesIO(reply.data))
        self.assertEqual((300, 200), picture.size)
        self.assertEqual("ok", reply.headers["X-TagPup-Thumb"])

    def test_a_card_s_url_is_kept_by_the_browser_for_a_year(self):
        card = self.get("/api/library/cards", query_string={"ids": "%d,%d" % (self.c, self.a)}).get_json()["cards"][1]
        reply = self.get(card["thumb"])
        self.assertEqual(200, reply.status_code)
        self.assertEqual("private, max-age=31536000, immutable", reply.headers["Cache-Control"])
        self.assertTrue(reply.headers["ETag"])

    def test_a_stamp_that_is_not_the_files_is_revalidated_not_kept(self):
        for extra in ({}, {"v": "12345.5"}, {"v": "banana"}):
            with self.subTest(extra=extra):
                reply = self.thumb(**extra)
                self.assertEqual("no-cache", reply.headers["Cache-Control"])
                self.assertTrue(reply.headers["ETag"])

    def test_a_browser_that_has_it_is_told_so_without_the_picture(self):
        first = self.thumb(v=repr(float(self.stamp())))
        again = self.thumb(v=repr(float(self.stamp())), headers={"If-None-Match": first.headers["ETag"]})
        self.assertEqual((304, b""), (again.status_code, again.data))
        stale = self.thumb(headers={"If-None-Match": '"someone-else"'})
        self.assertEqual(200, stale.status_code)

    def test_the_second_request_is_served_from_the_cache(self):
        self.thumb()
        with mock.patch.object(thumbs, "render", wraps=thumbs.render) as render:
            self.assertEqual(200, self.thumb().status_code)
        self.assertEqual(0, render.call_count)

    def test_a_file_that_changed_is_a_new_picture_with_a_new_etag(self):
        first = self.thumb()
        make_jpeg(self.vl.path_of(self.a), (200, 20, 20), (500, 500))
        os.utime(self.vl.path_of(self.a), (1_700_000_000, 1_700_000_000))
        second = self.thumb()
        self.assertNotEqual(first.headers["ETag"], second.headers["ETag"])
        self.assertNotEqual(first.data, second.data)

    def test_an_id_with_no_photo_is_a_404_in_a_sentence(self):
        reply = self.thumb(99999)
        self.assertEqual(404, reply.status_code)
        self.assertIn("There is no photo 99999 in this library", reply.get_json()["error"])

    def test_a_missing_or_malformed_id_is_a_400(self):
        self.assertEqual(400, self.get("/api/photo-thumb").status_code)
        for wanted in ("abc", "1.5", "-", "1e3"):
            self.assertEqual(400, self.thumb(wanted).status_code, wanted)

    def test_a_negative_or_huge_id_is_a_404_not_a_traceback(self):
        for wanted in (-1, 0, 2 ** 40, 2 ** 80):
            reply = self.thumb(wanted)
            self.assertIn(reply.status_code, (404, 400), wanted)

    def test_a_damaged_photo_is_a_placeholder_and_is_not_cached(self):
        stat = os.stat(self.vl.path_of(self.a))
        damaged_photos.remember(self.vl.library, [(self.vl.path_of(self.a), (stat.st_mtime, stat.st_size), "truncated", "x", 0)])
        reply = self.thumb()
        self.assertEqual((200, "damaged", "no-cache"), (reply.status_code, reply.headers["X-TagPup-Thumb"],
                                                        reply.headers["Cache-Control"]))
        self.assertNotIn("ETag", reply.headers)
        self.assertFalse(os.path.isdir(self.vl.library.thumbs) and os.listdir(self.vl.library.thumbs))

    def test_a_file_gone_is_the_last_picture_or_a_sentence(self):
        self.thumb()
        os.remove(self.vl.path_of(self.a))
        reply = self.thumb()
        self.assertEqual((200, "last known"), (reply.status_code, reply.headers["X-TagPup-Thumb"]))
        reply = self.thumb(self.d)
        self.assertEqual(404, reply.status_code)
        self.assertIn("is not there", reply.get_json()["error"])

    def test_a_file_that_cannot_be_reached_is_a_503_with_a_sentence_and_a_moment_to_wait(self):
        with mock.patch.object(images, "smaller_copy", side_effect=PermissionError(13, "Permission denied")):
            reply = self.thumb()
        self.assertEqual((503, "2"), (reply.status_code, reply.headers["Retry-After"]))
        self.assertIn("try again", reply.get_json()["error"])
        self.assertNotIn("X-TagPup-Updating", reply.headers, "not the server moving onto a new version")

    def test_a_cache_that_cannot_be_written_does_not_break_the_request(self):
        os.makedirs(os.path.dirname(self.vl.library.thumbs), exist_ok=True)
        with open(self.vl.library.thumbs, "w") as handle:
            handle.write("a file where the folder should be")
        reply = self.thumb(v=repr(float(self.stamp())))
        self.assertEqual(200, reply.status_code)
        self.assertEqual("no-cache", reply.headers["Cache-Control"], "nothing was kept, so nothing may be")
        self.assertNotIn("ETag", reply.headers)

    def test_the_library_in_the_url_chooses_whose_photo_it_is(self):
        other = ViewLibrary(self, "meadow", home=self.home)
        mine = self.vl.photo("X", "same.jpg", real=True, shade=(250, 0, 0), size=(100, 100))
        theirs = other.photo("X", "same.jpg", real=True, shade=(0, 0, 250), size=(100, 100))
        from io import BytesIO

        from PIL import Image
        red = Image.open(BytesIO(self.get("/api/photo-thumb", query_string={"id": mine}).data)).convert("RGB").getpixel((5, 5))
        blue = Image.open(BytesIO(self.client.get("/meadow/api/photo-thumb", query_string={"id": theirs}).data)
                          ).convert("RGB").getpixel((5, 5))
        self.assertGreater(red[0], 200)
        self.assertGreater(blue[2], 200)


class ALibraryThatIsNotReady(unittest.TestCase):
    def test_a_library_behind_is_a_sentence_and_never_a_traceback(self):
        home = own_home.for_test(self)
        path = home.library("behind.db")
        at_version(path, 19)   # before the indexes the views page by (migration 20)
        app = web.create_app("tagpup", startup=Library(path))
        app.testing = True
        client = app.test_client()
        with mock.patch.object(web_libraries.library_actions, "bring_up_to_date", side_effect=RuntimeError("locked")):
            for url in ("/behind/api/library/ids?kind=all", "/behind/api/library/navigator?section=folders",
                        "/behind/api/photo-thumb?id=1"):
                reply = client.get(url)
                self.assertIn(reply.status_code, (409, 404), url)
                if reply.status_code == 409:
                    self.assertIn("has not been brought up to date", reply.get_json()["error"])
            reply = client.get("/behind/api/library/ids?kind=all")
            self.assertEqual(409, reply.status_code)
            self.assertIn("behind", reply.get_json()["error"])
        reply = client.get("/behind/api/library/ids?kind=all")
        self.assertEqual(200, reply.status_code, "opened by the app as it opens any library, it is ready")


@unittest.skipUnless(os.name == "nt", "the spellings are Windows paths")
class ARootedLibrary(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="views_roots_")
        self.side = rl.Side(self.home, "harbour", real=3, bulk=1, outside=1)
        self.assertIsNone(self.side.adopt().refused)
        self.app = web.create_app("tagpup", startup=self.side.library)
        self.app.testing = True
        self.client = self.app.test_client()
        roots_gate.forget(self.side.library)
        self.folder = os.path.join(self.side.pictures, rl.FOLDERS[0])

    def test_a_folder_typed_at_the_previous_place_is_resolved_by_the_ingress(self):
        copy = os.path.join(self.home.root, "Copy", "Pictures")
        shutil.copytree(self.side.pictures, copy, copy_function=shutil.copy2)
        config.set_location(rl.NAME, copy)
        reply = self.client.get("/harbour/api/library/ids", query_string={"kind": "folder", "folder": self.folder})
        self.assertEqual(200, reply.status_code)
        found = reply.get_json()
        self.assertEqual(4, found["total"])
        cards = self.client.get("/harbour/api/library/cards",
                                query_string={"ids": ",".join(str(each) for each in found["ids"])}).get_json()["cards"]
        self.assertTrue(all(card["path"].startswith(copy) for card in cards), "the first place's spelling")
        self.assertEqual(rl.NAME, reply.headers["X-TagPup-Roots-Moved"])

    def test_a_thumbnail_by_id_is_untouched_by_the_ingress_and_survives_the_move(self):
        photo_id = self.side.rows("SELECT id FROM photos ORDER BY id")[0][0]   # the first real photo made
        first = self.client.get("/harbour/api/photo-thumb", query_string={"id": photo_id})
        self.assertEqual(200, first.status_code)
        copy = os.path.join(self.home.root, "Copy", "Pictures")
        shutil.copytree(self.side.pictures, copy, copy_function=shutil.copy2)
        config.set_location(rl.NAME, copy)
        with mock.patch.object(thumbs, "render", wraps=thumbs.render) as render:
            again = self.client.get("/harbour/api/photo-thumb", query_string={"id": photo_id})
        self.assertEqual((200, 0), (again.status_code, render.call_count))
        self.assertNotIn("X-TagPup-Roots-Moved", again.headers)
        self.assertEqual(first.headers["ETag"], again.headers["ETag"])

    def test_a_root_this_machine_does_not_place_is_the_gates_409_with_its_sentence(self):
        os.remove(config.machine_roots_path())
        roots_gate.forget(self.side.library)
        for url in ("/harbour/api/library/ids?kind=all", "/harbour/api/library/navigator?section=folders",
                    "/harbour/api/photo-thumb?id=1"):
            reply = self.client.get(url)
            self.assertEqual(409, reply.status_code, url)
            self.assertEqual("1", reply.headers["X-TagPup-Roots-Problem"])
            self.assertIn("machine_roots.json", reply.get_json()["error"])

    def test_a_library_with_no_roots_is_served_as_it_always_was(self):
        plain = rl.Side(self.home, "plain", real=1, bulk=0, outside=0)
        reply = self.client.get("/plain/api/library/ids?kind=all")
        self.assertEqual(200, reply.status_code)
        self.assertEqual(3, reply.get_json()["total"])
        self.assertIsNotNone(plain)


if __name__ == "__main__":
    unittest.main()
