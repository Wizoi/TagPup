"""A folder renamed outside the apps: its rows follow it (tagpup.services.folder_moves).

Made on disk after the rows were made as the indexer makes them (tests/photo_rows): a
folder renamed by appending to its name, photos renamed inside it as well, a rename
into a folder that already has rows, two folders holding the same photos, a drive that
is not there, two libraries holding one folder. ExifTool is stood in for.
"""
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import add_face  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import folder_moves  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as settings_service  # noqa: E402
from tagpup.store import added_folders, db  # noqa: E402

THEN = 1_700_000_000
DATE = "2026:01:31 08:%02d:00"


class RenamedFolders(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="folder_moves_")
        self.pictures = os.path.join(self.home.root, "Pictures")
        os.makedirs(self.pictures)
        self.db_path = self.home.library("library.db")
        library_actions.create(self.db_path)
        self.library = Library(self.db_path)
        #: {file name: what ExifTool answers for it}
        self.truth = {}
        self.reads = []

    # ---- making things ----------------------------------------------------------------

    def photo(self, folder, name, number, dated=True, doc=None, size=None):
        """A file of its own size, at THEN, and what reading it answers."""
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(b"x" * (size or 100 + number))
        os.utime(path, (THEN, THEN))
        fields = {}
        if dated:
            fields["EXIF:DateTimeOriginal"] = DATE % number
        if doc:
            fields["XMP:DocumentID"] = doc
        self.truth[name] = fields
        return path

    def index(self, *photo_paths, library=None):
        """Rows as the indexer records a read of each; ids by path."""
        conn = db.connect((library or self.library).path)
        try:
            ids = {path: photo_rows.add_read(conn, path, self.truth[os.path.basename(path)])
                   for path in photo_paths}
            conn.commit()
        finally:
            conn.close()
        return ids

    def face(self, photo_path, name="Rowan Thackeray"):
        conn = db.connect(self.db_path)
        try:
            add_face(conn, photo_path, box="[1,2,3,4]", name=name, name_source="manual")
            conn.commit()
        finally:
            conn.close()

    def meet(self, name, count=5, **kwargs):
        folder = os.path.join(self.pictures, name)
        return [self.photo(folder, "IMG_%04d.jpg" % n, n, **kwargs) for n in range(1, count + 1)]

    def rename(self, old, new):
        os.rename(os.path.join(self.pictures, old), os.path.join(self.pictures, new))

    def relink(self, apply=False, only=None, library=None):
        reads = self.reads

        class Session:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_tags(inner, batch, tags=None):
                reads.extend(batch)
                return [{"SourceFile": path.replace(os.sep, "/"), **self.truth.get(os.path.basename(path), {})}
                        for path in batch]

        with mock.patch("tagpup.files.exiftool_session.ExifToolSession", Session):
            return folder_moves.relink(library or self.library, "exiftool", apply=apply, only=only)

    def query(self, sql, params=(), path=None):
        conn = db.connect(db.readonly_uri(path or self.db_path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def paths_by_id(self):
        return dict(self.query("SELECT id, path FROM photos"))

    # ---- the cases --------------------------------------------------------------------

    def test_a_folder_renamed_by_adding_to_its_name_follows_with_its_faces(self):
        old = self.meet("2026-01-31 - Parkrun #347", dated=False)
        ids = self.index(*old)
        self.face(old[0])
        self.rename("2026-01-31 - Parkrun #347", "2026-01-31 - Parkrun #347 Harbour")

        dry = self.relink()
        self.assertEqual((1, 0, 5), (dry.details["counts"]["relink"], dry.details["counts"]["propose"],
                                     dry.details["counts"]["rows_matched"]))
        self.assertEqual(set(old), set(self.paths_by_id().values()), "a dry run wrote")

        done = self.relink(apply=True)
        self.assertEqual(5, done.changed)
        moved = self.paths_by_id()
        self.assertEqual(set(ids.values()), set(moved), "photo ids were not kept")
        for path, photo_id in ids.items():
            self.assertTrue(os.path.exists(moved[photo_id]), "a row names no file")
            self.assertEqual(os.path.basename(path), os.path.basename(moved[photo_id]))
        self.assertEqual("Harbour", os.path.basename(os.path.dirname(moved[ids[old[0]]]))[-7:])
        self.assertEqual([("Rowan Thackeray",)],
                         self.query("SELECT name FROM faces WHERE photo_id = ?", (ids[old[0]],)))
        self.assertEqual("relink_folders", self.query("SELECT operation FROM changes")[-1][0])
        self.assertEqual(0, self.relink().details["counts"]["gone_folders"], "a second run found work")

    def test_photos_renamed_inside_the_renamed_folder_are_matched_by_document_id(self):
        old = [self.photo(os.path.join(self.pictures, "2026-02-07 - Parkrun #348"), "IMG_%04d.jpg" % n, n,
                          doc="xmp.did:%04d" % n) for n in range(1, 5)]
        ids = self.index(*old)
        folder = os.path.join(self.pictures, "2026-02-07 - Parkrun #348")
        shutil.move(folder, folder + " Wet")
        for n in range(1, 5):
            os.rename(os.path.join(folder + " Wet", "IMG_%04d.jpg" % n),
                      os.path.join(folder + " Wet", "Race %d.jpg" % n))
            self.truth["Race %d.jpg" % n] = self.truth["IMG_%04d.jpg" % n]
        done = self.relink(apply=True)
        self.assertEqual(4, done.changed)
        self.assertEqual(4, done.details["counts"]["by_document_id"])
        self.assertEqual(sorted(ids.values()), sorted(self.paths_by_id()))
        self.assertTrue(all(os.path.exists(p) for p in self.paths_by_id().values()))

    def test_photos_renamed_without_a_document_id_are_matched_by_size_and_date_taken(self):
        old = self.meet("2026-02-14 - Parkrun #349", count=4)
        ids = self.index(*old)
        self.rename("2026-02-14 - Parkrun #349", "2026-02-14 - Parkrun #349 Park")
        folder = os.path.join(self.pictures, "2026-02-14 - Parkrun #349 Park")
        for n in range(1, 5):
            os.rename(os.path.join(folder, "IMG_%04d.jpg" % n), os.path.join(folder, "Finish %d.jpg" % n))
            self.truth["Finish %d.jpg" % n] = self.truth["IMG_%04d.jpg" % n]
        done = self.relink(apply=True)
        self.assertEqual((4, 4), (done.changed, done.details["counts"]["by_content"]))
        self.assertEqual(sorted(ids.values()), sorted(self.paths_by_id()))

    def test_a_name_alone_is_not_evidence(self):
        old = self.meet("2026-02-21 - Parkrun", count=3)
        self.index(*old)
        self.rename("2026-02-21 - Parkrun", "2026-02-21 - Elsewhere")
        # The same names, other pictures: other sizes and other times.
        for n, path in enumerate(old, 1):
            new = os.path.join(self.pictures, "2026-02-21 - Elsewhere", os.path.basename(path))
            with open(new, "wb") as handle:
                handle.write(b"y" * (900 + n))
            self.truth[os.path.basename(path)] = {"EXIF:DateTimeOriginal": "2020:01:01 00:00:%02d" % n}
        done = self.relink(apply=True)
        self.assertEqual(0, done.changed)
        self.assertEqual(set(old), set(self.paths_by_id().values()))

    def test_two_candidates_holding_the_same_photos_are_left_and_reported(self):
        old = self.meet("2026-02-28 - Parkrun", count=4)
        self.index(*old)
        self.rename("2026-02-28 - Parkrun", "2026-02-28 - Parkrun A")
        for n in range(1, 5):
            self.photo(os.path.join(self.pictures, "2026-02-28 - Parkrun B"), "IMG_%04d.jpg" % n, n)
        done = self.relink(apply=True)
        self.assertEqual(0, done.changed)
        self.assertEqual((0, 4), (done.details["counts"]["relink"], done.details["counts"]["ambiguous_rows"]))
        self.assertEqual(set(old), set(self.paths_by_id().values()))

    def test_a_thin_match_is_a_proposal_until_the_owner_confirms_it(self):
        old = self.meet("2026-03-07 - Parkrun", count=10)
        ids = self.index(*old)
        self.rename("2026-03-07 - Parkrun", "2026-03-07 - Parkrun Hills")
        folder = os.path.join(self.pictures, "2026-03-07 - Parkrun Hills")
        for n in range(1, 8):
            os.remove(os.path.join(folder, "IMG_%04d.jpg" % n))
        dry = self.relink(apply=True)
        self.assertEqual((0, 1, 0), (dry.details["counts"]["relink"], dry.details["counts"]["propose"], dry.changed))
        said = self.relink(apply=True, only=(os.path.join(self.pictures, "2026-03-07 - Parkrun"), folder))
        self.assertEqual(3, said.changed)
        moved = self.paths_by_id()
        self.assertEqual(3, sum(1 for p in moved.values() if p.startswith(folder)))
        self.assertEqual(sorted(ids.values()), sorted(moved), "the rows left missing were removed")

    def test_a_row_already_at_the_new_name_is_left_where_it_is(self):
        old = self.meet("2026-03-14 - Parkrun", count=5)
        self.index(*old)
        self.rename("2026-03-14 - Parkrun", "2026-03-14 - Parkrun Quay")
        new = os.path.join(self.pictures, "2026-03-14 - Parkrun Quay", "IMG_0001.jpg")
        photo_id = self.index(new)[new]
        self.face(new, name="Sam Okafor")
        done = self.relink(apply=True)
        # The file with a row is the destination of nothing: its photo is not matched.
        self.assertEqual(4, done.changed)
        self.assertEqual([("Sam Okafor",)], self.query("SELECT name FROM faces WHERE photo_id = ?", (photo_id,)))
        self.assertEqual(1, self.query("SELECT COUNT(*) FROM photos WHERE path = ?", (new,))[0][0])

    def test_a_folder_on_a_drive_that_is_not_there_is_not_looked_for(self):
        free = [letter for letter in "QRSTUVWXYZ" if not os.path.exists(letter + ":" + os.sep)]
        if not free:
            self.skipTest("every drive letter in Q to Z is in use")
        folder = os.path.join(free[0] + ":" + os.sep, "Pictures", "2026-03-21 - Parkrun")
        conn = db.connect(self.db_path)
        try:
            photo_rows.add_read(conn, os.path.join(folder, "IMG_0001.jpg"), {})
            conn.commit()
        finally:
            conn.close()
        # No parent of it is there: it looks as an unplugged drive always did, rows kept and none relinked.
        done = self.relink(apply=True)
        counts = done.details["counts"]
        self.assertEqual((0, 0, 1), (done.changed, counts["gone_folders"], counts["rows_unreachable"]))
        self.assertEqual(1, len(self.paths_by_id()))

    def test_a_folder_gone_with_its_parent_follows_the_parent_renamed_too(self):
        old = self.meet(os.path.join("Trip", "2026-04-11 - Parkrun"), count=4)
        ids = self.index(*old)
        self.rename("Trip", "Trip 2026")
        # The unit is the renamed Trip: nothing beside it begins with a date, so it is not looked for.
        done = self.relink(apply=True)
        self.assertEqual((0, 1), (done.changed, done.details["counts"]["none"]))
        self.assertEqual(sorted(ids.values()), sorted(self.paths_by_id()))

    def test_two_libraries_holding_one_folder_each_follow_it(self):
        old = self.meet("2026-03-28 - Parkrun", count=4)
        self.index(*old)
        second = Library(self.home.library("second.db"))
        library_actions.create(second.path)
        self.index(*old, library=second)
        self.rename("2026-03-28 - Parkrun", "2026-03-28 - Parkrun Pier")
        self.assertEqual(4, self.relink(apply=True).changed)
        self.assertEqual(4, self.relink(apply=True, library=second).changed)
        for path in (self.db_path, second.path):
            self.assertTrue(all(os.path.exists(p) for (p,) in self.query("SELECT path FROM photos", path=path)))

    def test_a_name_without_a_date_is_not_looked_for(self):
        old = self.meet("Parkrun", count=3)
        self.index(*old)
        self.rename("Parkrun", "Parkrun Pier")
        done = self.relink(apply=True)
        self.assertEqual(0, done.changed)
        self.assertEqual([], self.reads, "a file was read with no candidate")

    def test_the_library_roots_and_added_folders_follow(self):
        old = self.meet("2026-04-04 - Parkrun", count=3)
        self.index(*old)
        folder = os.path.join(self.pictures, "2026-04-04 - Parkrun")
        conn = db.connect(self.db_path)
        try:
            added_folders.record(conn, folder, subfolders=True)
            conn.commit()
        finally:
            conn.close()
        changed = settings_service.change(self.library, {"library.roots": folder})
        self.assertTrue(changed.ok, changed.message())
        self.rename("2026-04-04 - Parkrun", "2026-04-04 - Parkrun Dawn")
        new = os.path.join(self.pictures, "2026-04-04 - Parkrun Dawn")
        done = self.relink(apply=True)
        self.assertEqual(4, done.changed)
        self.assertEqual(1, done.details["added_followed"])
        self.assertEqual([new], [os.path.normpath(p) for p in settings_service.of(self.library).roots])
        self.assertEqual([(new, 1)], self.query("SELECT path, subfolders FROM added_folders"))

    def test_the_command_is_a_dry_run_that_counts_and_names_folders_only_on_request(self):
        from click.testing import CliRunner

        from tagpup_cli import cli
        old = self.meet("2026-04-18 - Parkrun", count=3)
        self.index(*old)
        self.rename("2026-04-18 - Parkrun", "2026-04-18 - Parkrun Lake")

        def invoke(*args):
            reads = self.reads

            class Session:
                def __init__(self, *a, **k):
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def get_tags(inner, batch, tags=None):
                    reads.extend(batch)
                    return [{"SourceFile": path.replace(os.sep, "/"), **self.truth.get(os.path.basename(path), {})}
                            for path in batch]

            with mock.patch("tagpup.files.exiftool_session.ExifToolSession", Session):
                return CliRunner().invoke(cli, ["--db", self.db_path, "relink-folders", *args])

        plain = invoke()
        self.assertEqual(0, plain.exit_code, plain.output)
        self.assertIn("1 folder(s) to relink", plain.output)
        self.assertNotIn("Parkrun", plain.output)
        self.assertEqual(set(old), set(self.paths_by_id().values()), "the dry run wrote")
        self.assertIn("Parkrun Lake", invoke("--reveal").output)
        self.assertEqual(2, invoke("--from", old[0]).exit_code, "--from without --to")
        applied = invoke("--apply")
        self.assertEqual(0, applied.exit_code, applied.output)
        self.assertIn("Wrote 3 row(s)", applied.output)
        self.assertTrue(all(os.path.exists(p) for p in self.paths_by_id().values()))


if __name__ == "__main__":
    unittest.main()
