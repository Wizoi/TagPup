"""The derived tables follow every write (tagpup.store.derived; docs/ARCHITECTURE.md, phase 9a).

A write that changes a photo's keywords, path or metadata, deletes a photo, or edits the tag tree,
tells photo_tags, folders, photo_folder and photo_meta in the SAME transaction -- or the views read
rows that describe what a photo used to hold, as the index's rows once did after a bulk tag write
(CLAUDE.md). Each test makes a library the way the app does, writes through the production writer
(the index's record_indexed, the tag, save, move, forget and remove writers, the journal, the tree's
edits, the roots' adoption), and asks the derived tables what a rebuild from the photos would say:
`derived.problems` is empty, and the rows are the ones expected.

Fictional names only: the libraries are photographs of real people, many of them minors.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import photo_rows  # noqa: E402
from test_service_tags import TreeCase  # noqa: E402

from tagpup.services import tags  # noqa: E402
from tagpup.store import db, derived, journal, photos, schema, taxonomy  # noqa: E402

WINDOWS = os.name == "nt"


class WithALibrary(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.path = self.home.library("harbour.db")
        schema.ensure(self.path)
        self.conn = db.connect(self.path)
        self.addCleanup(self.conn.close)
        self.pictures = os.path.join(self.home.root, "Pictures")

    def where(self, folder, name):
        return os.path.join(self.pictures, folder, name)

    def tree(self, *tags):
        for tag in tags:
            taxonomy.add_node(self.conn, tag)
        self.conn.commit()

    def read(self, folder, name, tags=(), **fields):
        """A photo as the index records a read of it; its id. The caller commits."""
        return photo_rows.add_read(self.conn, self.where(folder, name), dict({"XMP:Subject": list(tags)}, **fields))

    def rows(self, sql, *params):
        return self.conn.execute(sql, params).fetchall()

    def tags_of(self, photo_id):
        return sorted(tag for (tag,) in self.rows(
            "SELECT t.tag FROM photo_tags pt JOIN tag_taxonomy t ON t.id = pt.tag_id WHERE pt.photo_id = ?", photo_id))

    def folder_of(self, photo_id):
        found = self.rows("SELECT f.path FROM photo_folder pf JOIN folders f ON f.id = pf.folder_id"
                          " WHERE pf.photo_id = ?", photo_id)
        return found[0][0] if found else None

    def folders(self):
        return sorted(path for (path,) in self.rows("SELECT path FROM folders"))

    def meta_of(self, photo_id):
        return self.rows("SELECT rating, make, model, latitude, longitude FROM photo_meta WHERE photo_id = ?",
                         photo_id)[0]

    def agrees(self):
        """Every table is what a rebuild from the photos would make it."""
        self.assertEqual([], derived.problems(self.conn))

    def dump(self):
        """Every derived row, a folder by its path: a folder pruned and made again is the same folder."""
        return {"photo_tags": self.rows("SELECT * FROM photo_tags ORDER BY 1, 2"),
                "folders": self.rows("SELECT parent_id IS NULL, path, name FROM folders ORDER BY path"),
                "photo_folder": self.rows("SELECT pf.photo_id, f.path FROM photo_folder pf JOIN folders f"
                                          " ON f.id = pf.folder_id ORDER BY 1"),
                "photo_meta": self.rows("SELECT * FROM photo_meta ORDER BY 1")}


class TheIndexRecordsAPhoto(WithALibrary):
    def test_the_rows_are_written_in_the_transaction_of_the_write_and_roll_back_with_it(self):
        self.tree("Trips/Coast")
        self.conn.execute("BEGIN IMMEDIATE")
        one = self.read("2024", "a.jpg", ["Trips/Coast"], **{"XMP:Rating": 4})
        self.assertEqual(["Trips/Coast"], self.tags_of(one))
        self.assertEqual(self.where("2024", ""), self.folder_of(one) + os.sep)
        self.assertEqual(4, self.meta_of(one)[0])
        other = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual(0, other.execute("SELECT COUNT(*) FROM photo_tags").fetchone()[0], "not before the commit")
        finally:
            other.close()
        self.conn.rollback()
        for table in derived.TABLES:
            self.assertEqual(0, self.rows("SELECT COUNT(*) FROM %s" % table)[0][0], table)

    def test_with_a_batch_or_without_the_rows_are_the_same(self):
        self.tree("Trips/Coast", "People/Rowan Thackeray")
        for number in range(5):
            self.read("2024", "plain%d.jpg" % number, ["Trips/Coast", "People/Rowan Thackeray", "Mystery"])
        self.conn.commit()
        self.conn.execute("BEGIN IMMEDIATE")
        batch = derived.Batch(self.conn)
        for number in range(5):
            path = self.where("2024", "batched%d.jpg" % number)
            photos.record_indexed(self.conn, path, photo_rows.as_read(
                path, {"XMP:Subject": ["Trips/Coast", "People/Rowan Thackeray", "Mystery"]}), batch=batch)
        self.conn.commit()
        self.agrees()
        counts = {tag: n for tag, n in self.rows("SELECT t.tag, COUNT(*) FROM photo_tags pt JOIN tag_taxonomy t"
                                                 " ON t.id = pt.tag_id GROUP BY t.tag")}
        self.assertEqual({"Trips/Coast": 10, "People/Rowan Thackeray": 10}, counts)

    def test_reading_a_photo_again_writes_what_changed_and_the_folder_keeps_its_id(self):
        self.tree("Trips/Coast", "Trips/Lake")
        one = self.read("2024", "a.jpg", ["Trips/Coast"], **{"XMP:Rating": 1})
        self.conn.commit()
        folder = self.rows("SELECT id FROM folders WHERE path = ?", os.path.dirname(self.where("2024", "a.jpg")))
        held = self.dump()
        self.read("2024", "a.jpg", ["Trips/Coast"], **{"XMP:Rating": 1})
        self.conn.commit()
        self.assertEqual(held, self.dump(), "the same read changes no derived row")
        self.read("2024", "a.jpg", ["Trips/Lake"], **{"XMP:Rating": 5, "EXIF:Make": "Harbourlight"})
        self.conn.commit()
        self.agrees()
        self.assertEqual(["Trips/Lake"], self.tags_of(one))
        self.assertEqual(5, self.meta_of(one)[0])
        self.assertEqual("Harbourlight", self.meta_of(one)[1])
        self.assertEqual(folder, self.rows("SELECT id FROM folders WHERE path = ?", os.path.dirname(self.where("2024", "a.jpg"))))

    def test_a_keyword_whose_node_is_made_after_the_photo_gets_its_row_when_the_node_is(self):
        one = self.read("2024", "a.jpg", ["Trips/Coast/Harbour"])
        self.conn.commit()
        self.assertEqual([], self.tags_of(one))
        taxonomy.add_node(self.conn, "Trips/Coast/Harbour")   # TagTuner's New Tag, or the indexer's tree save
        self.conn.commit()
        self.assertEqual(["Trips/Coast/Harbour"], self.tags_of(one))
        self.agrees()

    def test_the_trees_save_after_an_index_run_gives_every_photo_its_rows(self):
        """The indexer records the photos, then saves the tree with the tags it met
        (TagTaxonomy.save_to_db): the rows follow the tree's edit."""
        from tagpup.store.taxonomy import TagTaxonomy
        for number in range(6):
            self.read("2024", "p%d.jpg" % number, ["Trips/Coast", "Activity/Sailing"] if number % 2 else ["Pets/Dog"])
        self.conn.commit()
        self.assertEqual(0, self.rows("SELECT COUNT(*) FROM photo_tags")[0][0], "no node yet")
        tree = TagTaxonomy(self.path)
        tree.load()
        tree.add_tags(["Trips/Coast", "Activity/Sailing", "Pets/Dog"])
        tree.save()
        self.assertEqual(9, self.rows("SELECT COUNT(*) FROM photo_tags")[0][0])
        self.agrees()

    def test_a_batch_reads_the_tree_inside_its_transaction_so_a_tree_edited_meanwhile_is_the_one_used(self):
        self.tree("Trips/Coast")
        batch = derived.Batch(self.conn)   # made before the write; nothing read yet
        other = db.connect(self.path)
        try:
            taxonomy.add_node(other, "Trips/Lake")
            other.commit()   # another process's edit, before this write begins
        finally:
            other.close()
        self.conn.execute("BEGIN IMMEDIATE")
        path = self.where("2024", "a.jpg")
        photos.record_indexed(self.conn, path, photo_rows.as_read(path, {"XMP:Subject": ["Trips/Lake"]}), batch=batch)
        self.conn.commit()
        self.assertEqual(["Trips/Lake"], self.tags_of(photos.ensure_row(self.conn, path)))
        self.agrees()

    def test_two_libraries_sharing_a_folder_each_keep_their_own_ids(self):
        self.tree("Filler/One", "Trips/Coast")
        second_path = self.home.library("second.db")
        schema.ensure(second_path)
        second = db.connect(second_path)
        self.addCleanup(second.close)
        taxonomy.add_node(second, "Trips/Coast")   # the same tag, another id
        second.commit()
        for conn in (self.conn, second):
            photo_rows.add_read(conn, self.where("2024", "shared.jpg"), {"XMP:Subject": ["Trips/Coast"]})
            conn.commit()
        mine = self.rows("SELECT pt.tag_id FROM photo_tags pt")
        theirs = second.execute("SELECT pt.tag_id FROM photo_tags pt").fetchall()
        self.assertNotEqual(mine, theirs, "one tag, two libraries, two ids")
        self.assertEqual([], derived.problems(second))
        self.agrees()


class OtherWritersOfAPhoto(WithALibrary):
    def setUp(self):
        super().setUp()
        self.tree("Trips/Coast", "Trips/Lake", "Activity/Sailing")
        self.one = self.read("2024", "a.jpg", ["Trips/Coast"])
        self.conn.commit()
        self.photo = self.where("2024", "a.jpg")

    def test_a_tag_written_to_a_photo_follows(self):
        photos.record_tags(self.path, self.photo, ["Trips/Coast", "Activity/Sailing"])
        self.assertEqual(["Activity/Sailing", "Trips/Coast"], self.tags_of(self.one))
        photos.record_tags(self.path, self.photo, ["Trips/Lake"])
        self.assertEqual(["Trips/Lake"], self.tags_of(self.one))
        photos.record_tags(self.path, self.photo, [])
        self.assertEqual([], self.tags_of(self.one))
        self.agrees()

    def test_a_save_follows_its_keywords_and_its_metadata(self):
        os.makedirs(os.path.dirname(self.photo))
        with open(self.photo, "wb") as handle:
            handle.write(b"not really a photo")   # a save stamps the row with its file's
        raw = photo_rows.as_read(self.photo, {"XMP:Subject": ["Trips/Lake"], "XMP:Rating": 3})["raw_metadata"]
        photos.record_saved(self.path, self.photo, ["Trips/Lake"], [], raw)
        self.assertEqual(["Trips/Lake"], self.tags_of(self.one))
        self.assertEqual(3, self.meta_of(self.one)[0])
        self.agrees()

    def test_a_field_written_or_undone_follows(self):
        """What a change of photo files leaves in the row, forward and again in its undo
        (store.photos.follow_fields, called by tagpup.services.file_changes)."""
        before = self.dump()
        db.write_with_connection(self.path, lambda conn: photos.follow_fields(
            conn, self.photo, {"XMP:Subject": ["Trips/Coast", "Activity/Sailing"], "XMP:Rating": 5}))
        self.assertEqual(["Activity/Sailing", "Trips/Coast"], self.tags_of(self.one))
        self.assertEqual(5, self.meta_of(self.one)[0])
        self.agrees()
        db.write_with_connection(self.path, lambda conn: photos.follow_fields(
            conn, self.photo, {"XMP:Subject": ["Trips/Coast"], "XMP:Rating": None}))
        self.assertEqual(before, self.dump(), "the undo puts the derived rows back as they were")

    def test_a_read_back_after_the_file_changed_follows_its_metadata(self):
        """record_reads records what was read of the file's metadata -- not its keywords, which are
        the row's own column -- so the camera and the place follow, and the keyword rows stay."""
        raw = photo_rows.as_read(self.photo, {"XMP:Subject": ["Trips/Lake"], "EXIF:Make": "Harbourlight",
                                              "Composite:GPSLatitude": 48.5, "Composite:GPSLongitude": -122.4})
        photos.record_reads(self.path, [dict(raw, path=self.photo)])
        self.assertEqual(["Trips/Coast"], self.tags_of(self.one))
        self.assertEqual((None, "Harbourlight", None, 48.5, -122.4), self.meta_of(self.one))
        self.agrees()

    def test_a_photo_only_a_face_or_suggest_named_has_a_folder_and_nothing_else(self):
        path = self.where("2025", "unread.jpg")
        unread = photo_rows.add_unread(self.conn, path)
        self.conn.commit()
        self.assertEqual(os.path.dirname(path).lower(), self.folder_of(unread).lower())
        self.assertEqual([], self.tags_of(unread))
        self.assertEqual((None,) * 5, self.meta_of(unread))
        self.agrees()


class APhotoMoved(WithALibrary):
    def setUp(self):
        super().setUp()
        self.tree("Trips/Coast")
        self.a = self.read(os.path.join("2024", "Coast"), "a.jpg", ["Trips/Coast"])
        self.b = self.read(os.path.join("2024", "Coast"), "b.jpg")
        self.c = self.read(os.path.join("2024", "Lake"), "c.jpg")
        self.conn.commit()

    def test_a_rename_in_its_folder_changes_nothing_in_the_tree(self):
        held = self.dump()
        photos.move_rows(self.path, {self.where(os.path.join("2024", "Coast"), "a.jpg"):
                                     self.where(os.path.join("2024", "Coast"), "renamed.jpg")})
        self.assertEqual(held, self.dump())

    def test_a_move_to_another_folder_changes_its_folder_and_leaves_the_old_one_while_it_holds_another(self):
        before = len(self.folders())
        photos.move_rows(self.path, {self.where(os.path.join("2024", "Coast"), "a.jpg"):
                                     self.where(os.path.join("2024", "Lake"), "a.jpg")})
        self.assertEqual(os.path.join(self.pictures, "2024", "Lake").lower(), self.folder_of(self.a).lower())
        self.assertEqual(before, len(self.folders()), "Coast still holds b.jpg")
        self.agrees()

    def test_the_last_photo_leaving_a_folder_takes_its_row_and_the_ancestors_it_alone_held(self):
        photos.move_rows(self.path, {self.where(os.path.join("2024", "Lake"), "c.jpg"): self.where("2025", "c.jpg")})
        found = [path.lower() for path in self.folders()]
        self.assertNotIn(os.path.join(self.pictures, "2024", "Lake").lower(), found, "emptied: its row goes")
        self.assertIn(os.path.join(self.pictures, "2024", "Coast").lower(), found)
        self.assertIn(os.path.join(self.pictures, "2024").lower(), found, "an ancestor of a folder that still holds photos")
        self.assertIn(os.path.join(self.pictures, "2025").lower(), found)
        counts = {os.path.normcase(f["path"]): (f["direct"], f["recursive"]) for f in derived.folder_tree(self.conn)}
        self.assertEqual((1, 1), counts[os.path.normcase(os.path.join(self.pictures, "2025"))])
        self.assertEqual((0, 2), counts[os.path.normcase(os.path.join(self.pictures, "2024"))])
        self.assertEqual((0, 3), counts[os.path.normcase(self.pictures)])
        self.agrees()

    def test_names_shuffled_among_files_through_the_placeholders_leave_every_photo_in_its_folder(self):
        one, two = (self.where(os.path.join("2024", "Coast"), name) for name in ("a.jpg", "b.jpg"))
        moved, skipped = photos.move_rows(self.path, {one: two, two: one})
        self.assertEqual((2, []), (moved, skipped))
        self.agrees()


class APhotoDeleted(WithALibrary):
    def setUp(self):
        super().setUp()
        self.tree("Trips/Coast")
        self.ids = {name: self.read(folder, name, ["Trips/Coast"], **{"XMP:Rating": 2}) for folder, name in (
            (os.path.join("2024", "Coast"), "a.jpg"), (os.path.join("2024", "Coast"), "b.jpg"),
            (os.path.join("2024", "Lake"), "c.jpg"), ("2025", "d.jpg"))}
        self.conn.commit()

    def gone(self, name):
        for table in ("photo_tags", "photo_folder", "photo_meta"):
            self.assertEqual(0, self.rows("SELECT COUNT(*) FROM %s WHERE photo_id = ?" % table, self.ids[name])[0][0], table)

    def test_forgetting_a_photo_takes_its_rows_and_the_folder_it_emptied(self):
        photos.forget_photo(self.path, self.where("2025", "d.jpg"))
        self.gone("d.jpg")
        self.assertNotIn(os.path.join(self.pictures, "2025").lower(), [p.lower() for p in self.folders()])
        self.agrees()
        photos.forget_photo(self.path, self.where(os.path.join("2024", "Coast"), "a.jpg"))
        self.assertIn(os.path.join(self.pictures, "2024", "Coast").lower(), [p.lower() for p in self.folders()],
                      "still holds b.jpg")
        self.agrees()

    def test_removing_photos_by_path_in_the_index_transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        photos.remove(self.conn, [self.where(os.path.join("2024", "Lake"), "c.jpg")])
        self.conn.commit()
        self.gone("c.jpg")
        self.agrees()

    def test_removing_a_folder_takes_everything_under_it_and_only_that(self):
        self.conn.execute("BEGIN IMMEDIATE")
        result = photos.remove_under(self.conn, os.path.join(self.pictures, "2024"))
        self.conn.commit()
        self.assertEqual(3, result["photos_removed"])
        for name in ("a.jpg", "b.jpg", "c.jpg"):
            self.gone(name)
        self.assertEqual(1, self.rows("SELECT COUNT(*) FROM photo_folder")[0][0])
        self.assertEqual([self.pictures.lower(), os.path.join(self.pictures, "2025").lower()],
                         [p.lower() for p in self.folders() if p.lower().startswith(self.pictures.lower())])
        self.agrees()

    def test_a_photo_deleted_by_a_connection_that_never_heard_of_the_module_loses_its_rows_and_the_check_says_the_folders_stay(self):
        self.conn.execute("DELETE FROM photos WHERE id = ?", (self.ids["d.jpg"],))   # a trigger takes the rows
        self.conn.commit()
        self.gone("d.jpg")
        self.assertEqual([], derived.stale_tags(self.conn))
        photos_wrong, folders_wrong, missing, strays = derived.stale_folders(self.conn)
        self.assertEqual(([], [], 0), (photos_wrong, folders_wrong, missing))
        self.assertEqual(1, len(strays), "only the emptied folder is left, for the doctor to report and the store to prune")
        derived.prune(self.conn)
        self.conn.commit()
        self.agrees()


class TheTreeEdited(WithALibrary):
    def setUp(self):
        super().setUp()
        self.tree("Trips", "Trips/Coast", "Trips/Lake")
        self.a = self.read("2024", "a.jpg", ["Trips/Coast", "Trips/Lake"])
        self.b = self.read("2024", "b.jpg", ["Trips/Coast"])
        self.conn.commit()

    def test_a_node_deleted_takes_its_rows_and_makes_none_again(self):
        taxonomy.delete_branch(self.conn, "Trips/Coast")
        self.conn.commit()
        self.assertEqual([], self.tags_of(self.b))
        self.assertEqual(["Trips/Lake"], self.tags_of(self.a))
        self.assertEqual({"Trips/Coast": 2}, dict(derived.tags_without_a_node(self.conn)))
        self.agrees()

    def test_a_node_taken_out_and_put_back_gives_the_photos_their_rows_again(self):
        taxonomy.remove_node(self.conn, "Trips/Coast")
        self.conn.commit()
        self.assertEqual([], self.tags_of(self.b))
        taxonomy.add_node(self.conn, "Trips/Coast")   # a new id: the file still holds the keyword
        self.conn.commit()
        self.assertEqual(["Trips/Coast"], self.tags_of(self.b))
        self.agrees()

    def test_a_branch_moved_keeps_its_ids_and_the_photos_take_them_again_once_their_files_are_rewritten(self):
        ids = {tag: self.rows("SELECT id FROM tag_taxonomy WHERE tag = ?", tag)[0][0] for tag in ("Trips/Coast", "Trips/Lake")}
        taxonomy.move_branch(self.conn, "Trips/Coast", "Places/Coast")
        self.conn.commit()
        self.assertEqual(ids["Trips/Coast"], self.rows("SELECT id FROM tag_taxonomy WHERE tag = 'Places/Coast'")[0][0],
                         "ids are stable")
        self.assertEqual([], self.tags_of(self.b), "the photo's keyword text still says Trips/Coast, which is no node")
        self.agrees()
        for name, kept in (("a.jpg", ["Places/Coast", "Trips/Lake"]), ("b.jpg", ["Places/Coast"])):
            photos.record_tags(self.path, self.where("2024", name), kept)
        self.assertEqual([ids["Trips/Coast"]], [row[0] for row in self.rows(
            "SELECT tag_id FROM photo_tags WHERE photo_id = ?", self.b)])
        self.assertEqual(3, self.rows("SELECT COUNT(*) FROM photo_tags")[0][0])
        self.agrees()

    def test_a_flag_changed_rebuilds_nothing(self):
        held = self.dump()
        taxonomy.set_branch_flags(self.conn, "Trips", hidden=1)
        self.conn.commit()
        self.assertEqual(held, self.dump())

    def test_a_keyword_that_names_two_nodes_by_case_follows_the_one_that_remains(self):
        self.conn.execute("INSERT INTO tag_taxonomy (tag, name) VALUES ('trips/coast', 'coast')")
        self.conn.commit()
        taxonomy.remove_node(self.conn, "Trips/Coast")   # the exact match goes; the other now names it
        self.conn.commit()
        self.assertEqual(["trips/coast"], self.tags_of(self.b))
        self.agrees()


class RenamingATagInTagTuner(TreeCase):
    """services.tags.rename: the tree first, then the photos' keywords, each its own transaction
    (the rewrite of the files is stood in for, as TreeCase does, by the index's own record_tags)."""

    def test_the_photos_rows_are_the_same_nodes_when_it_is_done(self):
        crew = self.node("Crew")
        divers = self.node("Crew/Divers")
        one = self.id_of_photo(self.photo("one.jpg", ["Crew/Divers"]))
        two = self.id_of_photo(self.photo("two.jpg", ["Crew/Divers", "Crew"]))
        db.write_with_connection(self.lib.library.path, derived.rebuild_all)
        before = self.rows()
        self.assertEqual([(one, divers), (two, crew), (two, divers)], before)
        result = tags.rename(self.lib.library, divers, "Frogmen", exiftool_path=None)
        self.assertTrue(result.ok, result.message())
        self.assertEqual(divers, taxonomy.find(self.lib.library.path, "Crew/Frogmen")["id"], "the node keeps its id")
        self.assertEqual(before, self.rows(), "the photos' rows are the node's id, and so are as they were")
        conn = db.connect(db.readonly_uri(self.lib.library.path), uri=True)
        try:
            self.assertEqual([], derived.problems(conn))
            self.assertEqual({}, dict(derived.tags_without_a_node(conn)))
            self.assertEqual([one, two], derived.photos_under_tag(conn, "Crew/Frogmen"))
        finally:
            conn.close()

    def test_a_photo_that_could_not_be_rewritten_is_a_keyword_with_no_node_until_it_is(self):
        divers = self.node("Crew/Divers")
        one = self.id_of_photo(self.photo("one.jpg", ["Crew/Divers"]))
        stuck = self.photo("stuck.jpg", ["Crew/Divers"])
        db.write_with_connection(self.lib.library.path, derived.rebuild_all)
        self.unwritable.add(stuck)
        result = tags.rename(self.lib.library, divers, "Frogmen", exiftool_path=None)
        self.assertFalse(result.ok)
        conn = db.connect(db.readonly_uri(self.lib.library.path), uri=True)
        try:
            self.assertEqual([], derived.problems(conn), "what the rows say is what the files do")
            self.assertEqual({"Crew/Divers": 1}, dict(derived.tags_without_a_node(conn)))
            self.assertEqual([one], derived.photos_under_tag(conn, "Crew/Frogmen"))
        finally:
            conn.close()

    def id_of_photo(self, path):
        return self.lib.rows("SELECT id FROM photos WHERE path = ?", (path,))[0][0]

    def rows(self):
        conn = db.connect(db.readonly_uri(self.lib.library.path), uri=True)
        try:
            return conn.execute("SELECT photo_id, tag_id FROM photo_tags ORDER BY photo_id, tag_id").fetchall()
        finally:
            conn.close()


class TheJournalsWrites(WithALibrary):
    def setUp(self):
        super().setUp()
        self.tree("Trips/Coast", "Trips/Lake")
        self.a = self.read(os.path.join("2024", "Coast"), "a.jpg", ["Trips/Coast"], **{"XMP:Rating": 2})
        self.b = self.read(os.path.join("2024", "Lake"), "b.jpg", ["Trips/Lake"])
        self.conn.commit()
        self.held = self.dump()

    def test_a_change_of_keywords_is_followed_and_its_undo_puts_the_rows_back(self):
        applied = journal.apply(self.path, "retag", [journal.update(
            "photos", (self.a,), {"tags": '["Trips/Coast"]'}, {"tags": '["Trips/Lake", "Trips/Coast"]'})])
        self.assertTrue(applied.settled)
        self.assertEqual(["Trips/Coast", "Trips/Lake"], self.tags_of(self.a))
        self.agrees()
        journal.undo(self.path, applied.change_id)
        self.assertEqual(self.held, self.dump())

    def test_a_change_of_path_moves_the_photo_to_its_folder_and_the_undo_moves_it_back(self):
        new = self.where("2025", "a.jpg")
        applied = journal.apply(self.path, "move", [journal.update(
            "photos", (self.a,), {"path": self.where(os.path.join("2024", "Coast"), "a.jpg")}, {"path": new})])
        self.assertEqual(os.path.dirname(new).lower(), self.folder_of(self.a).lower())
        self.assertNotIn(os.path.join(self.pictures, "2024", "Coast").lower(), [p.lower() for p in self.folders()])
        self.agrees()
        journal.undo(self.path, applied.change_id)
        self.assertEqual(self.held["photo_tags"], self.dump()["photo_tags"])
        self.assertEqual(os.path.dirname(self.where(os.path.join("2024", "Coast"), "a.jpg")).lower(),
                         self.folder_of(self.a).lower())
        self.agrees()

    def test_a_photo_deleted_and_put_back_by_its_undo_has_its_rows_again(self):
        row = dict(zip(("path", "tags", "raw_metadata"), self.rows("SELECT path, tags, raw_metadata FROM photos WHERE id = ?", self.a)[0]))
        applied = journal.apply(self.path, "delete", [journal.delete("photos", (self.a,), {"path": row["path"]})])
        self.assertEqual([], self.tags_of(self.a))
        self.assertEqual(0, self.rows("SELECT COUNT(*) FROM photo_meta WHERE photo_id = ?", self.a)[0][0])
        self.agrees()
        journal.undo(self.path, applied.change_id)
        self.assertEqual(self.held, self.dump())

    def test_a_node_deleted_and_put_back_by_its_undo_gives_its_photos_their_rows(self):
        node = self.rows("SELECT id FROM tag_taxonomy WHERE tag = 'Trips/Lake'")[0][0]
        applied = journal.apply(self.path, "delete a node", [journal.delete("tag_taxonomy", (node,), {"tag": "Trips/Lake"})])
        self.assertEqual([], self.tags_of(self.b))
        self.agrees()
        journal.undo(self.path, applied.change_id)
        self.assertEqual(self.held, self.dump())

    def test_a_rehearsal_says_the_derived_rows_came_back(self):
        rehearsal = journal.rehearse(self.path, "retag", [journal.update(
            "photos", (self.a,), {"tags": '["Trips/Coast"]'}, {"tags": '["Trips/Lake"]'})])
        self.assertTrue(rehearsal.exact, rehearsal.differences)
        self.assertTrue(rehearsal.derived_exact)
        self.assertEqual(self.held, self.dump(), "a rehearsal writes nothing")

    def test_the_undo_of_a_change_made_before_the_derived_tables_existed_rebuilds_them(self):
        """A change made at schema 18 is undone at 19 (journal.schema_gap_blocker): the undo's own
        rebuild covers the photos it wrote."""
        from test_migrations import at_version
        from unittest import mock
        old = self.home.library("old.db")
        at_version(old, 18)
        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS[:18]), mock.patch.object(schema, "LATEST", 18):
            schema._current.clear()
            applied = journal.apply(old, "retag", [journal.update(
                "photos", (1,), {"tags": '["People/Wren Halloway"]'}, {"tags": '["Trips/Coast"]'})])
        schema._current.clear()
        self.assertEqual(["the tables the library views stand on"], schema.ensure(old))
        journal.undo(old, applied.change_id)
        conn = db.connect(db.readonly_uri(old), uri=True)
        try:
            self.assertEqual([], derived.problems(conn))
        finally:
            conn.close()


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheRootsAdoption(unittest.TestCase):
    """An adopted library's folders are those of an unconverted twin, by from_row; its undo makes
    them native again. (store/adoption.py rebuilds them in its own transaction.)"""

    def setUp(self):
        import roots_library as rl
        self.rl = rl
        self.home = own_home.for_test(self, prefix="derived_adopt_")
        self.twin = rl.Side(self.home, "twin", real=1, bulk=3, outside=1)
        self.adopted = rl.Side(self.home, "adopted", real=1, bulk=3, outside=1)

    def folders(self, side):
        """{folder: parent folder, each native and with the side's own folder taken out} and
        {photo: its folder}."""
        from tagpup.store import roots as store_roots
        conn = db.connect(db.readonly_uri(side.db_path), uri=True)
        try:
            roots = store_roots.roots_for(conn)
            from tagpup.core import paths
            native = {fid: (paths.from_row(path, roots), parent) for fid, parent, path in conn.execute(
                "SELECT id, parent_id, path FROM folders")}
            tree = {side.norm(path).lower(): (side.norm(native[parent][0]).lower() if parent else None)
                    for path, parent in native.values()}
            held = {side.norm(paths.from_row(photo, roots)).lower(): side.norm(native[fid][0]).lower()
                    for photo, fid in conn.execute("SELECT p.path, pf.folder_id FROM photos p JOIN photo_folder pf"
                                                   " ON pf.photo_id = p.id")}
            self.assertEqual([], derived.problems(conn))
            return tree, held
        finally:
            conn.close()

    def test_the_adopted_tree_is_the_twins_and_its_undo_makes_it_native_again(self):
        from tagpup.services import journal as journal_service
        twin_tree, twin_held = self.folders(self.twin)
        self.assertEqual(twin_tree, self.folders(self.adopted)[0], "unadopted, alike")
        result = self.adopted.adopt()
        self.assertTrue(result.ok, result.message())
        self.assertGreater(result.details["rehearsal"]["tables"]["photos"]["convert"], 0)
        tree, held = self.folders(self.adopted)
        self.assertEqual(twin_held, held, "each photo is in the same folder, by from_row")
        root = self.rl.NAME
        self.assertEqual(set(twin_tree), set(tree), "the same folders")
        top = self.adopted.norm(self.adopted.pictures).lower()
        self.assertIsNone(tree[top], "the root is a top of the adopted tree")
        self.assertEqual({k: v for k, v in twin_tree.items() if k != top}, {k: v for k, v in tree.items() if k != top},
                         "every other folder under the same parent")
        rows = self.adopted.rows("SELECT path FROM folders WHERE path LIKE '@%' ORDER BY path")
        self.assertTrue(rows and all(path.startswith("@" + root) for (path,) in rows))
        undone = journal_service.undo(self.adopted.library, result.details["change"], apply=True)
        self.assertIsNone(undone.refused, undone.refused)
        after = self.folders(self.adopted)
        self.assertEqual((twin_tree, twin_held), after, "native again, as the twin")
        self.assertEqual([], self.adopted.rows("SELECT path FROM folders WHERE path LIKE '@%'"))

    def test_a_crash_after_the_folders_are_rebuilt_leaves_the_library_as_it_was(self):
        from unittest import mock
        from tagpup.store import adoption
        before = self.adopted.dump()

        class Crash(BaseException):
            pass

        def reached(step):
            if step == "folders rebuilt":
                raise Crash(step)

        with mock.patch.object(adoption, "_reached", reached):
            with self.assertRaises(Crash):
                adoption.adopt(self.adopted.db_path, self.rl.NAME, self.rl.ADDRESS, [self.adopted.pictures])
        self.assertEqual(before, self.adopted.dump(), "one transaction: nothing of it was kept")


class OneWriterAtATime(WithALibrary):
    def test_a_writer_waits_for_a_rebuild_and_then_finds_the_rows_it_made(self):
        """The migration, the repair and every write take the library's write lock; a second process
        writing photos while a rebuild runs waits for it and then writes against its result."""
        import sqlite3
        self.tree("Trips/Coast")
        self.read("2024", "a.jpg", ["Trips/Coast"])
        self.conn.commit()
        self.conn.execute("BEGIN IMMEDIATE")
        derived.rebuild_all(self.conn)
        other = db.connect(self.path, timeout=0.2)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                other.execute("BEGIN IMMEDIATE")   # the rebuild holds the write lock
            self.conn.commit()
            other.execute("BEGIN IMMEDIATE")
            path = self.where("2024", "b.jpg")
            photos.record_indexed(other, path, photo_rows.as_read(path, {"XMP:Subject": ["Trips/Coast"]}))
            other.commit()
        finally:
            other.close()
        self.agrees()
        self.assertEqual(2, self.rows("SELECT COUNT(*) FROM photo_tags")[0][0])

    def test_a_rebuild_that_stops_half_way_leaves_the_tables_as_they_were(self):
        from unittest import mock
        self.tree("Trips/Coast")
        self.read("2024", "a.jpg", ["Trips/Coast"])
        self.read("2025", "b.jpg", ["Trips/Coast"])
        self.conn.commit()
        held = self.dump()
        with mock.patch.object(derived, "_write_folders", side_effect=RuntimeError("the power went")):
            with self.assertRaises(RuntimeError):
                db.write_with_connection(self.path, derived.rebuild_all)
        self.assertEqual(held, self.dump(), "photo_tags and photo_meta were rewritten in the transaction that rolled back")

    def test_the_repair_puts_right_what_a_writer_that_forgot_left(self):
        self.tree("Trips/Coast")
        one = self.read("2024", "a.jpg", ["Trips/Coast"])
        self.conn.commit()
        self.conn.execute("UPDATE photos SET tags = '[]' WHERE id = ?", (one,))   # a writer that did not refresh
        self.conn.commit()
        self.assertEqual([one], derived.stale_tags(self.conn))
        before, written, after = derived.repair(self.path)
        self.assertEqual(1, len(before))
        self.assertEqual(0, written["tag_rows"])
        self.assertEqual([], after)
        self.assertEqual([], self.tags_of(one))


if __name__ == "__main__":
    unittest.main()
