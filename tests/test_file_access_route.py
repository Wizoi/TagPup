"""GET /api/file-access/check (tagpup.web.activity_routes): this PC only; the data folder and the places
of the library's roots (every library's when none is named); `refresh` asks again; a library that is not
there is a 404; and the check itself is read-only (tests/test_file_access.py).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup import config as tagpup_config  # noqa: E402
from tagpup.services import file_access  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web import activity_routes  # noqa: E402

ANSWER = {"checked_at": "2026-10-03 10:00:00", "findings": [], "facts": {}, "cached": False}


class FileAccessRoute(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="file_access_route_")
        for name in ("harbour", "regatta"):
            library_actions.create(self.home.library(name + ".db"))
        app = web.create_app("tagpup", ports={"tagpup": 8090, "tuner": 8080})
        app.testing = True
        self.client = app.test_client()

    def listing(self, library, machine=None):
        place = {"harbour": r"D:\Fictional\Harbour", "regatta": r"E:\Fictional\Regatta"}[library.name.lower()]
        return {"roots": [{"name": "photos", "locations": [place]}, {"name": "unplaced", "locations": []}]}

    def asked(self, url, **kwargs):
        with mock.patch.object(file_access, "check", return_value=ANSWER) as check, \
                mock.patch.object(activity_routes.roots_service, "listing", self.listing):
            reply = self.client.get(url, **kwargs)
        return reply, check

    def test_it_checks_the_data_folder_and_every_librarys_places(self):
        reply, check = self.asked("/api/file-access/check")
        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
        self.assertEqual(ANSWER, reply.get_json())
        folder, places = check.call_args.args
        self.assertEqual(tagpup_config.data_dir(), folder)
        self.assertEqual({r"D:\Fictional\Harbour", r"E:\Fictional\Regatta"}, set(places))
        self.assertFalse(check.call_args.kwargs["refresh"])

    def test_a_library_named_restricts_the_places_and_refresh_asks_again(self):
        reply, check = self.asked("/api/file-access/check?library=Harbour&refresh=1")
        self.assertEqual(200, reply.status_code)
        self.assertEqual([r"D:\Fictional\Harbour"], check.call_args.args[1])
        self.assertTrue(check.call_args.kwargs["refresh"])

    def test_a_library_that_is_not_there_is_a_404_and_nothing_is_checked(self):
        reply, check = self.asked("/api/file-access/check?library=nowhere")
        self.assertEqual(404, reply.status_code)
        check.assert_not_called()

    def test_another_address_is_refused(self):
        reply, check = self.asked("/api/file-access/check", environ_base={"REMOTE_ADDR": "192.168.1.20"})
        self.assertEqual(403, reply.status_code)
        check.assert_not_called()

    def test_a_library_whose_places_cannot_be_read_still_gets_the_data_folder_checked(self):
        def broken(library, machine=None):
            raise OSError("the map is unreadable")

        with mock.patch.object(file_access, "check", return_value=ANSWER) as check, \
                mock.patch.object(activity_routes.roots_service, "listing", broken):
            reply = self.client.get("/api/file-access/check")
        self.assertEqual(200, reply.status_code)
        self.assertEqual([], check.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
