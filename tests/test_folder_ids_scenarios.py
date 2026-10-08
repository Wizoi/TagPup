"""Folder ids where the owner lives (tagpup.services.folder_ids): the CLI's `folder-ids mark`, a sync that meets a
marked folder renamed, a library with a root and two places for it, a library behind this version, the doctor, and
the migration. Fictional names only.
"""
import json
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import roots_library  # noqa: E402
from click.testing import CliRunner  # noqa: E402
from test_folder_ids import THEN, Case, slurp  # noqa: E402
from test_migrations import at_version, backups  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.files import folder_marker  # noqa: E402
from tagpup.services import folder_ids, sync  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.store import added_folders, db, journal, schema  # noqa: E402
from tagpup.store import folder_ids as store  # noqa: E402
from tagpup_cli import cli  # noqa: E402


class Said:
    """What the CLI printed, on one line."""

    def __init__(self, result):
        self.code = result.exit_code
        self.text = " ".join(result.output.split())


class TheCommand(Case):

    def run_cli(self, *args, code=0, library=None):
        result = CliRunner().invoke(cli, ["--db", (library or self.library).path] + list(args))
        self.assertEqual(code, result.exit_code, result.output)
        return " ".join(result.output.split())

    def test_a_dry_run_counts_and_names_nothing_and_says_the_stamp_cannot_be_taken_back(self):
        self.meet("2026-01-31 Parkrun")
        said = self.run_cli("folder-ids", "mark")
        self.assertIn("1 folder(s) hold photos directly: 0 already marked, 1 to mark", said)
        self.assertIn("also stamps the library with an identifier of its own", said)
        self.assertIn("Nothing changed. --apply writes 1 marker file(s) and records 1 id(s).", said)
        self.assertNotIn("Parkrun", said)
        self.assertIsNone(self.identity())

    def test_names_come_only_with_reveal(self):
        made = self.meet("2026-01-31 Parkrun")
        with open(folder_marker.location(os.path.dirname(made[0])), "wb") as handle:
            handle.write(b"my own notes\n")
        self.assertNotIn("Parkrun", self.run_cli("folder-ids", "mark"))
        self.assertIn("malformed: " + os.path.dirname(made[0]), self.run_cli("folder-ids", "mark", "--reveal"))

    def test_apply_writes_and_says_what_it_wrote_and_how_to_undo_it(self):
        self.meet("2026-01-31 Parkrun")
        said = self.run_cli("folder-ids", "mark", "--apply")
        self.assertIn("Wrote 1 marker file(s); recorded 1 id(s). Recorded as change", said)
        self.assertIn("undo", said)
        self.assertEqual(1, len(self.ids()))
        self.assertIn("1 already marked", self.run_cli("folder-ids", "mark"))

    def test_a_copy_of_the_library_file_is_refused_with_exit_status_1(self):
        self.meet("2026-01-31 Parkrun")
        self.run_cli("folder-ids", "mark", "--apply")
        shutil.copyfile(self.db_path, self.home.library("trial.db"))
        said = self.run_cli("folder-ids", "mark", code=1)
        self.assertIn("Refused:", said)
        self.assertIn("trial.db", said)

    def test_a_library_behind_this_version_is_not_migrated_by_a_dry_run(self):
        old = self.home.library("older.db")
        at_version(old, schema.LATEST - 1)
        said = self.run_cli("folder-ids", "mark", library=Library(old))
        self.assertIn("0 already marked, 0 to mark", said)
        self.assertEqual(schema.LATEST - 1, self.version(old))

    def version(self, path):
        conn = db.connect(db.readonly_uri(path), uri=True)
        try:
            return schema.version(conn)
        finally:
            conn.close()

    def test_relink_folders_apply_with_no_folder_gone_is_not_an_error(self):
        # It raised KeyError('changed'): the scaffold left the counts out when there was nothing to write.
        self.meet("2026-01-31 Parkrun")
        said = self.run_cli("relink-folders", "--apply")
        self.assertIn("Wrote 0 row(s)", said)

    def test_without_a_subcommand_it_says_what_there_is(self):
        said = self.run_cli("folder-ids")
        self.assertIn("mark", said)


class WhenSyncMeetsAMovedFolder(Case):

    def sync(self, apply=True, queued=None, folder=None):
        truth = self.truth

        class Session:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_tags(inner, batch, tags=None):
                return [{"SourceFile": path.replace(os.sep, "/"), **truth.get(os.path.basename(path), {})}
                        for path in batch]

        def queue(folders):
            if queued is not None:
                queued.extend(folders)
            return Result(changed=len(folders))
        with mock.patch("tagpup.files.exiftool_session.ExifToolSession", Session):
            return sync.sync(self.library, folder, apply, "exiftool", queue, roots=[self.pictures])

    def test_a_marked_folder_renamed_and_its_photos_renamed_is_followed_and_never_indexed_as_new(self):
        made = [self.photo(self.at("2026-02-07 Parkrun"), "IMG_%04d.jpg" % n, n, doc="xmp.did:%04d" % n)
                for n in range(1, 4)]
        ids_before = self.index(*made)
        folder_ids.mark(self.library, apply=True)
        folder = self.at("2026-02-07 Parkrun")
        shutil.move(folder, self.at("A different name entirely"))
        for n in range(1, 4):
            os.rename(os.path.join(self.at("A different name entirely"), "IMG_%04d.jpg" % n),
                      os.path.join(self.at("A different name entirely"), "Finish %d.jpg" % n))
            self.truth["Finish %d.jpg" % n] = self.truth["IMG_%04d.jpg" % n]
        queued = []
        dry = self.sync(apply=False)
        self.assertEqual(1, dry.details["folder_markers"]["counts"]["followed"])
        self.assertEqual(1, dry.details["folders_marked"])
        self.assertEqual(set(made), set(self.paths_by_id().values()), "a dry run wrote")
        done = self.sync(queued=queued)
        self.assertTrue(done.ok, done.message())
        self.assertEqual(set(ids_before.values()), set(self.paths_by_id()))
        self.assertTrue(all(os.path.exists(p) for p in self.paths_by_id().values()))
        self.assertEqual({self.at("A different name entirely")}, set(self.ids()))
        self.assertEqual([], [each for each in queued if "A different name entirely" in each],
                         "the followed folder was indexed as if it were new")
        self.assertIsNone(done.details["folder_markers"]["error"])

    def renamed_and_edited(self, folder_name, new_name, count=3):
        """A marked folder of photos with no DocumentID (97% of the real rows have none), renamed, its photos renamed
        and touched, as an editor leaves them: no name, size and modified time pairs a row with a file, and the size
        and Date Taken still do."""
        made = [self.photo(self.at(folder_name), "IMG_%04d.jpg" % n, n) for n in range(1, count + 1)]
        ids = self.index(*made)
        self.face(made[0])
        folder_ids.mark(self.library, apply=True)
        os.rename(self.at(folder_name), self.at(new_name))
        for n in range(1, count + 1):
            old = os.path.join(self.at(new_name), "IMG_%04d.jpg" % n)
            new = os.path.join(self.at(new_name), "Finish %d.jpg" % n)
            os.rename(old, new)
            os.utime(new, (THEN + 60, THEN + 60))
            self.truth["Finish %d.jpg" % n] = self.truth["IMG_%04d.jpg" % n]
        return made, ids

    def check_followed(self, made, ids, new_name, queued):
        self.assertEqual(set(ids.values()), set(self.paths_by_id()), "photo ids were not kept")
        self.assertTrue(all(os.path.exists(p) for p in self.paths_by_id().values()), "a row names no file")
        self.assertEqual(3, len([p for p in self.paths_by_id().values() if new_name in p]))
        self.assertEqual([("Rowan Thackeray",)], self.query("SELECT name FROM faces"), "the named face went")
        self.assertEqual({self.at(new_name)}, set(self.ids()))
        self.assertEqual([], [each for each in queued if new_name in each], "the followed folder was queued as new")

    def test_the_sync_of_the_folder_a_folder_was_renamed_in_follows_it_without_a_document_id(self):
        # What the watcher runs for a renamed folder: the sync of its parent, not the whole library.
        made, ids = self.renamed_and_edited("2026-02-07 Parkrun", "Renamed and edited")
        queued = []
        done = self.sync(queued=queued, folder=self.pictures)
        self.assertTrue(done.ok, done.message())
        self.assertEqual(1, done.details["folder_markers"]["counts"]["followed"])
        self.check_followed(made, ids, "Renamed and edited", queued)

    def test_the_whole_sync_does_the_same_without_a_document_id(self):
        made, ids = self.renamed_and_edited("2026-02-07 Parkrun", "Renamed and edited")
        queued = []
        self.sync(queued=queued)
        self.check_followed(made, ids, "Renamed and edited", queued)

    def moved_into_a_held_folder(self):
        """A marked folder moved to another parent, renamed and touched photos (no DocumentID), the new parent a folder
        the library holds (added with its subfolders): the sync of that folder sees nothing missing or moved."""
        made = [self.photo(self.at("2026-02-07 Parkrun"), "IMG_%04d.jpg" % n, n) for n in range(1, 4)]
        ids = self.index(*made)
        self.face(made[0])
        folder_ids.mark(self.library, apply=True)
        archive = os.path.join(self.home.root, "Archive")
        os.makedirs(archive)
        conn = db.connect(self.db_path)
        try:
            added_folders.record(conn, archive, subfolders=True)
            conn.commit()
        finally:
            conn.close()
        there = os.path.join(archive, "2026-02-07 Parkrun")
        shutil.move(self.at("2026-02-07 Parkrun"), there)
        for n in range(1, 4):
            new = os.path.join(there, "Finish %d.jpg" % n)
            os.rename(os.path.join(there, "IMG_%04d.jpg" % n), new)
            os.utime(new, (THEN + 60, THEN + 60))
            self.truth["Finish %d.jpg" % n] = self.truth["IMG_%04d.jpg" % n]
        return made, ids, archive, there

    def check_moved(self, ids, there, queued):
        self.assertEqual(set(ids.values()), set(self.paths_by_id()), "fresh rows were made beside the old ones")
        self.assertTrue(all(os.path.exists(p) for p in self.paths_by_id().values()), "a row names no file")
        self.assertEqual([("Rowan Thackeray",)], self.query("SELECT name FROM faces"), "the named face went")
        self.assertEqual({there}, set(self.ids()))
        self.assertEqual([], [each for each in queued if "Parkrun" in each], "the moved folder was queued as new")

    def test_a_folder_moved_into_a_folder_the_library_holds_is_followed_when_the_destination_is_synced_first(self):
        _made, ids, archive, there = self.moved_into_a_held_folder()
        queued = []
        done = self.sync(queued=queued, folder=archive)
        self.assertEqual((0, 0), (done.details["counts"]["missing"], done.details["counts"]["moved"]),
                         "this sync sees nothing missing or moved")
        self.assertEqual(1, done.details["folder_markers"]["counts"]["followed"])
        self.check_moved(ids, there, queued)

    def test_the_same_when_the_source_is_synced_first_and_finds_nothing_to_lose(self):
        _made, ids, archive, there = self.moved_into_a_held_folder()
        queued = []
        first = self.sync(queued=queued, folder=self.pictures)
        self.assertEqual(1, first.details["folder_markers"]["counts"]["not_found"])
        self.assertEqual(set(ids.values()), set(self.paths_by_id()))
        self.assertEqual({self.at("2026-02-07 Parkrun")}, set(self.ids()), "its id moved though nothing was found")
        done = self.sync(queued=queued, folder=archive)
        self.assertEqual(1, done.details["folder_markers"]["counts"]["followed"])
        self.check_moved(ids, there, queued)

    def test_the_cli_says_what_was_left_and_what_was_not_found(self):
        self.moved_into_a_held_folder()
        said = " ".join(CliRunner().invoke(cli, ["--db", self.db_path, "sync", "--folder", self.pictures]).output.split())
        self.assertIn("1 not found", said)

    def test_a_library_that_marked_nothing_is_not_touched(self):
        self.meet("2026-02-07 Parkrun")
        self.rename("2026-02-07 Parkrun", "Renamed")
        done = self.sync()
        self.assertEqual((0, None), (done.details["folders_marked"], done.details["folder_markers"]))
        self.assertEqual([], self.query("SELECT id FROM changes WHERE operation = 'follow_folder_markers'"))

    def test_a_failure_in_following_is_a_warning_and_the_sync_stands(self):
        made = self.meet("2026-02-07 Parkrun")
        folder_ids.mark(self.library, apply=True)
        self.rename("2026-02-07 Parkrun", "Renamed")
        with mock.patch.object(folder_ids, "follow", side_effect=RuntimeError("the share went")):
            done = self.sync()
        self.assertTrue(done.ok)
        self.assertTrue(any("the share went" in line for line in done.details["warnings"]))
        self.assertTrue(made)

    def test_the_cli_says_marked_folders_were_followed_and_points_at_mark_when_none_are(self):
        self.meet("2026-02-07 Parkrun")
        self.rename("2026-02-07 Parkrun", "Renamed")
        said = " ".join(CliRunner().invoke(cli, ["--db", self.db_path, "sync"]).output.split())
        self.assertIn("`folder-ids mark` (a dry run)", said)

    def test_relink_folders_follows_the_marked_folders_first_and_exactly(self):
        made = self.meet("2026-02-07 Parkrun")
        folder_ids.mark(self.library, apply=True)
        self.rename("2026-02-07 Parkrun", "Something else")
        runner = CliRunner().invoke(cli, ["--db", self.db_path, "relink-folders", "--apply"])
        said = " ".join(runner.output.split())
        self.assertEqual(0, runner.exit_code, (runner.output, repr(runner.exception)))
        self.assertIn("1 marked folder(s) gone from disk: 1 followed by their markers (exact)", said)
        self.assertTrue(all(os.path.exists(p) for p in self.paths_by_id().values()))
        self.assertTrue(made)


class WithARoot(unittest.TestCase):
    """A library that holds its photos as `@name/rel`, as photo_index's adopted ones do."""

    def setUp(self):
        import own_home
        self.home = own_home.for_test(self, prefix="folder_ids_root_")
        self.side = roots_library.Side(self.home, "rooted", real=2, bulk=3, outside=1)

    def raw(self):
        return {row[0]: row[1] for row in self.side.rows("SELECT id, path FROM folder_ids")}

    def follow(self, trees):
        truth = {}

        class Session:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_tags(inner, batch, tags=None):
                return [{"SourceFile": path.replace(os.sep, "/"), **truth.get(os.path.basename(path), {})}
                        for path in batch]
        with mock.patch("tagpup.files.exiftool_session.ExifToolSession", Session):
            return folder_ids.follow(self.side.library, (), trees, None, apply=True, exiftool_path="exiftool")

    def test_ids_are_recorded_in_the_form_the_rows_are_and_follow_a_rename(self):
        self.assertTrue(self.side.adopt().ok)
        done = folder_ids.mark(self.side.library, apply=True)
        self.assertTrue(done.ok, done.message())
        self.assertEqual(4, done.changed)
        raw = sorted(self.raw().values())
        self.assertEqual(["@pictures/2024 Harbour", "@pictures/2024 Regatta", "@pictures/Trips/2025 Coast"],
                         [path for path in raw if path.startswith("@")])
        self.assertEqual(1, len([path for path in raw if not path.startswith("@")]), "a folder under no root")
        old = os.path.join(self.side.pictures, "2024 Regatta")
        os.rename(old, old + " Final")
        followed = self.follow([self.side.pictures, self.side.outside_folder])
        self.assertTrue(followed.ok, followed.message())
        self.assertEqual(2, followed.details["changed"]["relinked"], "the two photos on disk")
        self.assertIn("@pictures/2024 Regatta Final", self.raw().values())
        self.assertNotIn("@pictures/2024 Regatta", self.raw().values())
        raw_photos = self.side.raw_paths()
        self.assertEqual(2, len([p for p in raw_photos if p.startswith("@pictures/2024 Regatta Final/")]))
        self.assertEqual(3, len([p for p in raw_photos if p.startswith("@pictures/2024 Regatta/")]),
                         "the rows of photos no longer on disk are kept where they were")
        undone = journal_service.undo(self.side.library, followed.details["change"], apply=True)
        self.assertTrue(undone.ok, undone.message())
        self.assertIn("@pictures/2024 Regatta", self.raw().values())

    def test_a_root_kept_in_two_places_holds_one_folder_not_a_copy(self):
        self.assertTrue(self.side.adopt().ok)
        mirror = os.path.join(self.home.root, "Mirror", "Pictures")
        shutil.copytree(self.side.pictures, mirror)
        folder_ids.mark(self.side.library, apply=True)
        shutil.rmtree(mirror)
        shutil.copytree(self.side.pictures, mirror)             # the mirror carries the markers too
        with open(config.machine_roots_path(), "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "roots": {"pictures": [self.side.pictures, mirror]}}, handle)
        for place in (self.side.pictures, mirror):
            os.rename(os.path.join(place, "2024 Harbour"), os.path.join(place, "2024 Harbour Day"))
        followed = self.follow([self.side.pictures, mirror])
        self.assertTrue(followed.ok, followed.message())
        self.assertEqual(0, followed.details["counts"]["ambiguous"], "the mirror was taken for a copy")
        self.assertEqual(1, followed.details["counts"]["followed"])
        self.assertIn("@pictures/2024 Harbour Day", self.raw().values())


class ThroughTheWatcher(Case):
    """The always-on process: the folder's notifications settle and its parent is synced (tagpup.jobs.watching)."""

    def test_a_marked_folder_renamed_and_edited_is_followed_by_the_sync_the_watcher_runs(self):
        import time

        from tagpup.files import images
        from tagpup.jobs import watching
        made = [self.photo(self.at("2026-02-07 Parkrun"), "IMG_%04d.jpg" % n, n) for n in range(1, 4)]
        ids = self.index(*made)
        self.face(made[0])
        folder_ids.mark(self.library, apply=True)
        synced, queued = [], []
        truth = self.truth

        class Session:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_tags(inner, batch, tags=None):
                return [{"SourceFile": path.replace(os.sep, "/"), **truth.get(os.path.basename(path), {})}
                        for path in batch]

        def run(library, folder):
            result = sync.sync(library, folder, True, "exiftool",
                               lambda folders: queued.extend(folders) or Result(changed=len(folders)),
                               roots=[self.pictures])
            synced.append((folder, result))
            return result

        with mock.patch("tagpup.files.exiftool_session.ExifToolSession", Session):
            watcher = watching.Watcher(lambda: [self.library], lambda library: [self.pictures], run, images.is_photo,
                                       debounce=0.5, recheck=0.5, tick=0.05)
            self.addCleanup(watcher.stop, 20)
            watcher.start()
            deadline = time.time() + 30
            while not (synced and watcher.watched()) and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual([None], [folder for folder, _r in synced], "the catch-up at start")
            del synced[:], queued[:]
            os.rename(self.at("2026-02-07 Parkrun"), self.at("Renamed and edited"))
            for n in range(1, 4):
                old = os.path.join(self.at("Renamed and edited"), "IMG_%04d.jpg" % n)
                new = os.path.join(self.at("Renamed and edited"), "Finish %d.jpg" % n)
                os.rename(old, new)
                os.utime(new, (THEN + 60, THEN + 60))
                self.truth["Finish %d.jpg" % n] = self.truth["IMG_%04d.jpg" % n]
            deadline = time.time() + 30
            while time.time() < deadline and not any(
                    r.details.get("folder_markers") and r.details["folder_markers"]["counts"].get("followed")
                    for _f, r in synced):
                time.sleep(0.05)
            time.sleep(1.5)
        self.assertTrue(synced, "the watcher synced nothing")
        self.assertTrue(all(folder is not None for folder, _r in synced), "a whole sync, not a folder's")
        self.assertEqual(set(ids.values()), set(self.paths_by_id()))
        self.assertTrue(all(os.path.exists(p) for p in self.paths_by_id().values()), "a row names no file")
        self.assertEqual([("Rowan Thackeray",)], self.query("SELECT name FROM faces"))
        self.assertEqual({self.at("Renamed and edited")}, set(self.ids()))
        self.assertEqual([], [each for each in queued if "Renamed and edited" in each],
                         "the renamed folder was queued as new")


class DoctorAndMigration(Case):

    def test_the_doctor_names_a_second_library_carrying_the_identity(self):
        self.meet("2026-01-31 Parkrun")
        folder_ids.mark(self.library, apply=True)
        lines = []
        self.assertEqual(0, roots_library.doctor().report(self.db_path, out=lines.append))
        self.assertTrue(any(line.startswith("library identity, 1 folder(s) marked") and line.endswith("ok")
                            for line in lines), lines)
        shutil.copyfile(self.db_path, self.home.library("trial.db"))
        lines = []
        self.assertEqual(1, roots_library.doctor().report(self.db_path, out=lines.append))
        self.assertTrue(any("carried by 1 other library file(s): trial.db" in line for line in lines), lines)

    def test_the_doctor_says_nothing_of_an_unstamped_library(self):
        self.meet("2026-01-31 Parkrun")
        lines = []
        roots_library.doctor().report(self.db_path, out=lines.append)
        self.assertFalse(any("library identity" in line for line in lines))

    def test_migration_26_adds_two_empty_tables_and_stamps_nothing(self):
        path = self.home.library("harbour.db")
        at_version(path, 25)
        applied = schema.ensure(path)
        self.assertEqual(1, len(applied))
        self.assertEqual([], backups(path), "an additive migration takes no backup")
        conn = db.connect(db.readonly_uri(path), uri=True)
        try:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM folder_ids").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM library_identity").fetchone()[0])
            self.assertIsNone(store.identity(conn))
        finally:
            conn.close()

    def test_the_new_tables_do_not_block_the_undo_of_an_older_change(self):
        # A change made at schema 25 is still undoable at 26: the migration adds tables no earlier change recorded.
        self.assertIsNone(journal.schema_gap_blocker(25, 26))
        self.assertIsNone(journal.schema_gap_blocker(24, 26))

    def test_a_trial_copy_of_a_library_that_was_never_marked_is_nobodys_twin(self):
        self.meet("2026-01-31 Parkrun")
        shutil.copyfile(self.db_path, self.home.library("trial.db"))
        self.assertEqual([], store.twins(self.db_path, self.identity()))
        self.assertTrue(folder_ids.mark(self.library).ok)

    def test_the_stamp_is_not_part_of_the_changes_undo(self):
        self.meet("2026-01-31 Parkrun")
        done = folder_ids.mark(self.library, apply=True)
        stamped = self.identity()
        undone = journal_service.undo(self.library, done.details["change"], apply=True)
        self.assertTrue(undone.ok, undone.message())
        self.assertEqual(stamped, self.identity(), "an identifier handed out in files was taken back")
        self.assertEqual(slurp(folder_marker.location(self.at("2026-01-31 Parkrun"))).split()[0].decode(), stamped)


if __name__ == "__main__":
    unittest.main()
