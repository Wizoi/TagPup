"""The page's cached scan of a folder is read again once the library's photos changed, whoever changed them.

The server keeps what the page was last shown of a folder (tagpup.web.tagpup_routes.FolderCache) and was told
of its own writes only. A CLI `sync --apply`, an MCP undo or a snapshot restore in another process rewrote the
photos' rows and the open page's next scan still described them as they had been. The cache is kept with the
library's photos counter as it was before the scan, which the triggers move for a write from any process
(tagpup.store.generations), and handed back only while it has not moved.
"""
import os
import sys
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_rows  # noqa: E402
import web_client  # noqa: E402

from tagpup.store import db  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402


class AScanKeptWhileTheLibraryMoves(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.db_path = self.home.library("library.db")
        self.folder = os.path.join(self.home.root, "Photos", "Regatta")
        os.makedirs(self.folder)
        self.photo = os.path.join(self.folder, "one.jpg")
        Image.new("RGB", (8, 8)).save(self.photo, "JPEG")
        self.record(["Boats/Dinghy"])

    def record(self, subjects):
        """Another process-like writer: its own connection, the row as the indexer records what it read."""
        def write(conn):
            photo_rows.hold(conn, self.folder)
            photo_rows.add_read(conn, self.photo, {"XMP:Subject": subjects})
        db.write_with_connection(self.db_path, write)

    def scan(self):
        reply = self.client.get("/library/api/folder/scan", query_string={"path": self.folder})
        self.assertEqual(200, reply.status_code, reply.data)
        return [record["tags"] for record in reply.get_json()]

    def test_a_scan_is_kept_while_nothing_moved(self):
        first = self.scan()
        self.assertEqual([["Boats/Dinghy"]], first)
        kept = self.cache().get(self.folder)
        self.assertIsNotNone(kept)
        self.assertIs(kept, self.cache().get(self.folder))

    def test_a_write_from_another_connection_makes_the_next_scan_read_again(self):
        self.assertEqual([["Boats/Dinghy"]], self.scan())
        self.record(["Boats/Keelboat"])
        self.assertIsNone(self.cache().get(self.folder), "the scan was handed back after the photos changed")
        self.assertEqual([["Boats/Keelboat"]], self.scan())

    def cache(self):
        from tagpup.core.library import Library
        return tagpup_routes.folders.of(Library(self.db_path))


if __name__ == "__main__":
    unittest.main()
