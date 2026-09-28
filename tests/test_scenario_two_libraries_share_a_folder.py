"""Owner scenario: two libraries that each hold the same folder on disk.

The owner keeps more than one library, and both can hold the same folder -- a shared
drive of an event both cover, each having indexed it on its own. Nothing about a write
in one, or the watcher's or sync's re-reading of the shared files in the other, should
let one library's tags, rows or tree reach the other's; a library that does not hold a
folder is refused Suggest there, and refused a write there too (test_rows_only_in_folders_added.py,
test_writes_only_in_folders_held.py already cover that half of this story).

ExifTool is stood in for, as test_service_change_tags.py and test_sync.py do; the
photo files and both libraries' rows are real.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from test_service_replace_tag import exiftool  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.files.metadata import MetadataExtractor  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import sync, tagging  # noqa: E402
from tagpup.store import db  # noqa: E402


class Reads:
    """What sync's stand-in ExifTool answers, as test_sync.py's does."""

    def __init__(self, truth):
        self.truth = truth

    def batch_read(self, extractor, file_paths, people=None):
        assert not extractor.mint_identities, "sync must never write to a photo"
        return [MetadataExtractor._structure(extractor, path, dict(self.truth.get(os.path.basename(path), {})),
                                             people) for path in file_paths]

    def session(self, *args, **kwargs):
        reads = self

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_tags(self, batch, tags=None):
                return [{"SourceFile": path.replace(os.sep, "/"), **reads.truth.get(os.path.basename(path), {})}
                        for path in batch]

        return Session()


class SharedFolderTwoLibraries(unittest.TestCase):
    """Both alpha and beta hold Pictures/2025-10 Meet, each having indexed it on its
    own; alpha alone holds Pictures/Alpha Only."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="shared_folder_")
        self.pictures = os.path.join(self.home.root, "Pictures")
        self.meet = os.path.join(self.pictures, "2025-10 Meet")
        self.alpha_only = os.path.join(self.pictures, "Alpha Only")
        os.makedirs(self.meet)
        os.makedirs(self.alpha_only)
        self.alpha_path = self.home.library("alpha.db")
        self.beta_path = self.home.library("beta.db")
        library_actions.create(self.alpha_path)
        library_actions.create(self.beta_path)
        self.alpha = Library(self.alpha_path)
        self.beta = Library(self.beta_path)
        self.truth = {}

    def photo(self, folder, name, **fields):
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(b"jpeg")
        self.truth[name] = dict(fields)
        return path

    def indexed(self, db_path, *photo_paths):
        conn = db.connect(db_path)
        try:
            for path in photo_paths:
                photo_rows.add_read(conn, path, self.truth[os.path.basename(path)])
            conn.commit()
        finally:
            conn.close()

    def tags_of(self, db_path, photo_path):
        conn = db.connect(db.readonly_uri(db_path), uri=True)
        try:
            row = conn.execute("SELECT tags FROM photos WHERE path = ?", (photo_path,)).fetchone()
            return json.loads(row[0]) if row else None
        finally:
            conn.close()

    def tree_tags(self, db_path):
        conn = db.connect(db.readonly_uri(db_path), uri=True)
        try:
            return {row[0] for row in conn.execute("SELECT tag FROM tag_taxonomy")}
        finally:
            conn.close()

    def run_sync(self, library, **kwargs):
        reads = Reads(self.truth)
        with mock.patch("tagpup.files.metadata.MetadataExtractor.batch_read", autospec=True,
                        side_effect=reads.batch_read), \
                mock.patch("tagpup.files.exiftool_session.ExifToolSession", side_effect=reads.session):
            return sync.sync(library, apply=True, exiftool_path="exiftool", **kwargs)

    def test_a_write_through_one_library_never_reaches_the_others_tree_or_rows(self):
        meet_photo = self.photo(self.meet, "meet_01.jpg")
        self.indexed(self.alpha_path, meet_photo)
        self.indexed(self.beta_path, meet_photo)

        files = {meet_photo: []}
        session, _et = exiftool(files)
        with mock.patch("tagpup.files.exiftool_session.ExifToolSession", session):
            result = tagging.change_tags(self.alpha, [meet_photo], ["Trips/Meet"], [], "exiftool")
        self.assertEqual((1, 1), (result.attempted, result.changed))

        self.assertEqual(["Trips/Meet"], self.tags_of(self.alpha_path, meet_photo))
        self.assertNotIn("Trips/Meet", self.tree_tags(self.alpha_path))

        # beta was never asked, and shares no cache with alpha's request.
        self.assertEqual([], self.tags_of(self.beta_path, meet_photo))
        self.assertNotIn("Trips/Meet", self.tree_tags(self.beta_path))

    def test_betas_sync_of_the_shared_file_grows_no_tree_and_touches_only_what_it_holds(self):
        meet_photo = self.photo(self.meet, "meet_01.jpg")
        alpha_private = self.photo(self.alpha_only, "private.jpg")
        self.indexed(self.alpha_path, meet_photo, alpha_private)
        self.indexed(self.beta_path, meet_photo)

        # alpha writes the shared file; on disk, beta's row now describes it wrongly.
        files = {meet_photo: []}
        session, _et = exiftool(files)
        with mock.patch("tagpup.files.exiftool_session.ExifToolSession", session):
            tagging.change_tags(self.alpha, [meet_photo], ["Trips/Meet"], [], "exiftool")
        later = os.stat(meet_photo).st_mtime + 10
        os.utime(meet_photo, (later, later))
        self.truth["meet_01.jpg"] = {"XMP:Subject": ["Trips/Meet"]}

        self.run_sync(self.beta, roots=[self.pictures])

        # beta caught up on the file it holds...
        self.assertEqual(["Trips/Meet"], self.tags_of(self.beta_path, meet_photo))
        # ...but sync alone grew no tree node in either library (only indexing does)...
        self.assertNotIn("Trips/Meet", self.tree_tags(self.beta_path))
        self.assertNotIn("Trips/Meet", self.tree_tags(self.alpha_path))
        # ...and made no row for a folder beta never held, alpha's private one.
        self.assertIsNone(self.tags_of(self.beta_path, alpha_private))


if __name__ == "__main__":
    unittest.main()
