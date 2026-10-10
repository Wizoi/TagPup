"""The derived tables the library views stand on (tagpup.store.derived; docs/ARCHITECTURE.md,
phase 9a): photo_tags (keywords as tag-tree node ids), folders and photo_folder (the folder tree),
photo_meta (rating, camera, place), made from the photos by rebuild_all and by migration 19.

Rows are made as the indexer makes them (tests/photo_rows.py: what a read gives, recorded by
store.photos.record_indexed), never through the module under test. Fictional names only: the
libraries are photographs of real people, many of them minors.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import migration_names  # noqa: E402
import photo_rows  # noqa: E402
from test_migrations import at_version  # noqa: E402

from tagpup.store import db, derived, photos, schema, taxonomy  # noqa: E402


class ALibrary(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self)
        self.home = home
        self.path = home.library("harbour.db")
        schema.ensure(self.path)
        self.conn = db.connect(self.path)
        self.addCleanup(self.conn.close)
        self.pictures = os.path.join(home.root, "Pictures")

    def tree(self, *tags):
        for tag in tags:
            taxonomy.add_path(self.conn, tag)
        self.conn.commit()

    def photo(self, folder, name, tags=(), **fields):
        """A photo read from `folder` under Pictures, as the index records it; its id."""
        path = os.path.join(self.pictures, folder, name)
        photo_rows.add_read(self.conn, path, dict({"XMP:Subject": list(tags)}, **fields))
        return photos.ensure_row(self.conn, path)

    def odd(self, folder, name, tags, raw=None):
        """A photo whose keywords are written as given -- a row an older writer made, which a read
        would have trimmed -- through the index's own writer; its id."""
        path = os.path.join(self.pictures, folder, name)
        photos.record_indexed(self.conn, path, {"tags": tags, "raw_metadata": raw or {}})
        return photos.ensure_row(self.conn, path)

    def rebuilt(self):
        derived.rebuild_all(self.conn)
        self.conn.commit()

    def rows(self, sql, *params):
        return self.conn.execute(sql, params).fetchall()

    def node(self, tag):
        return self.rows("SELECT id FROM tag_taxonomy WHERE tag = ?", tag)[0][0]

    def tags_of(self, photo_id):
        return sorted(tag for (tag,) in self.rows(
            "SELECT t.tag FROM photo_tags pt JOIN tag_taxonomy t ON t.id = pt.tag_id WHERE pt.photo_id = ?", photo_id))


class TheKeywordsOfAPhoto(ALibrary):
    def test_each_keyword_is_the_node_it_names(self):
        self.tree("Trips", "Trips/Coast", "Activity/Sailing")
        one = self.photo("2024", "a.jpg", ["Trips/Coast", "Activity/Sailing"])
        two = self.photo("2024", "b.jpg", ["Trips/Coast"])
        self.rebuilt()
        self.assertEqual(["Activity/Sailing", "Trips/Coast"], self.tags_of(one))
        self.assertEqual(["Trips/Coast"], self.tags_of(two))
        self.assertEqual([(one, self.node("Trips/Coast")), (one, self.node("Activity/Sailing")),
                          (two, self.node("Trips/Coast"))],
                         sorted(self.rows("SELECT photo_id, tag_id FROM photo_tags"),
                                key=lambda row: (row[0], row[1] != self.node("Trips/Coast"))))

    def test_a_keyword_with_no_node_gets_no_row_and_no_node_is_made(self):
        self.tree("Trips/Coast")
        nodes = self.rows("SELECT COUNT(*) FROM tag_taxonomy")
        one = self.photo("2024", "a.jpg", ["Trips/Coast", "Trips/Lakeside", "Mystery"])
        self.photo("2024", "b.jpg", ["Trips/Lakeside"])
        self.rebuilt()
        self.assertEqual(["Trips/Coast"], self.tags_of(one))
        self.assertEqual(nodes, self.rows("SELECT COUNT(*) FROM tag_taxonomy"), "indexing never changes the tree")
        self.assertEqual({"Trips/Lakeside": 2, "Mystery": 1}, dict(derived.tags_without_a_node(self.conn)))

    def test_a_keyword_that_differs_only_in_case_names_the_node(self):
        self.tree("People/Rowan Thackeray")
        one = self.photo("2024", "a.jpg", ["people/rowan thackeray"])
        self.rebuilt()
        self.assertEqual(["People/Rowan Thackeray"], self.tags_of(one))
        self.assertEqual({}, dict(derived.tags_without_a_node(self.conn)))

    def test_blanks_and_slashes_at_the_ends_and_the_other_separators_are_the_tags_spelling(self):
        self.tree("Trips/Coast/Harbour")
        for number, spelled in enumerate([" Trips/Coast/Harbour ", "/Trips/Coast/Harbour/", "Trips / Coast /Harbour",
                                          "Trips|Coast|Harbour", "Trips\\Coast\\Harbour", "TRIPS//COAST/harbour"]):
            one = self.odd("2024", "odd%d.jpg" % number, [spelled])
            self.rebuilt()
            self.assertEqual(["Trips/Coast/Harbour"], self.tags_of(one), repr(spelled))

    def test_a_bare_leaf_is_not_the_path_it_is_a_person_of(self):
        """Keywords are full paths; a bare name some old writer left is the people rule's to
        resolve (store.people), not a node of its own."""
        self.tree("People/Cora Ingersoll")
        bare = self.photo("2024", "a.jpg", ["Cora Ingersoll"])
        full = self.photo("2024", "b.jpg", ["People/Cora Ingersoll"])
        self.rebuilt()
        self.assertEqual([], self.tags_of(bare))
        self.assertEqual(["People/Cora Ingersoll"], self.tags_of(full))
        self.assertEqual({"Cora Ingersoll": 1}, dict(derived.tags_without_a_node(self.conn)))

    def test_a_keyword_held_twice_is_one_row(self):
        self.tree("Trips/Coast")
        one = self.odd("2024", "a.jpg", ["Trips/Coast", "Trips/Coast", "trips/coast", " Trips/Coast"])
        self.rebuilt()
        self.assertEqual(["Trips/Coast"], self.tags_of(one))

    def test_a_photo_without_keywords_or_with_damaged_ones_has_no_rows_and_nothing_fails(self):
        self.tree("Trips/Coast")
        none = self.photo("2024", "none.jpg")
        empty = self.odd("2024", "empty.jpg", [])
        damaged = [self.odd("2024", "d%d.jpg" % n, []) for n in range(4)]
        for photo_id, text in zip(damaged, (None, "{not json", '{"a": 1}', '[1, null, ["x"], "Trips/Coast"]')):
            self.conn.execute("UPDATE photos SET tags = ? WHERE id = ?", (text, photo_id))
        self.rebuilt()
        self.assertEqual([], self.tags_of(none) + self.tags_of(empty) + self.tags_of(damaged[0])
                         + self.tags_of(damaged[1]) + self.tags_of(damaged[2]))
        self.assertEqual(["Trips/Coast"], self.tags_of(damaged[3]), "the one text among the rest")
        self.assertEqual([], derived.stale_tags(self.conn))

    def test_two_nodes_that_differ_only_in_case_the_exact_spelling_wins_and_the_lowest_id_else(self):
        self.tree("Places/harbour")
        self.conn.execute("INSERT INTO tag_taxonomy (tag, name) VALUES ('Places/Harbour', 'Harbour')")
        self.conn.commit()
        exact = self.photo("2024", "a.jpg", ["Places/Harbour"])
        either = self.photo("2024", "b.jpg", ["PLACES/HARBOUR"])
        self.rebuilt()
        self.assertEqual(self.node("Places/Harbour"), self.rows("SELECT tag_id FROM photo_tags WHERE photo_id = ?", exact)[0][0])
        self.assertEqual(self.node("Places/harbour"), self.rows("SELECT tag_id FROM photo_tags WHERE photo_id = ?", either)[0][0])

    def test_a_node_deleted_takes_its_rows_and_the_keyword_is_reported_not_made_again(self):
        self.tree("Trips/Coast", "Trips/Lake")
        one = self.photo("2024", "a.jpg", ["Trips/Coast", "Trips/Lake"])
        self.rebuilt()
        self.conn.execute("DELETE FROM tag_taxonomy WHERE tag = 'Trips/Coast'")   # a trigger, on any connection
        self.conn.commit()
        self.assertEqual(["Trips/Lake"], self.tags_of(one))
        self.assertEqual({"Trips/Coast": 1}, dict(derived.tags_without_a_node(self.conn)))
        self.assertEqual(0, self.rows("SELECT COUNT(*) FROM tag_taxonomy WHERE tag = 'Trips/Coast'")[0][0])

    def test_a_photo_deleted_takes_its_rows_whatever_connection_deletes_it(self):
        self.tree("Trips/Coast")
        one = self.photo("2024", "a.jpg", ["Trips/Coast"], **{"XMP:Rating": 3})
        self.rebuilt()
        self.conn.execute("DELETE FROM photos WHERE id = ?", (one,))
        self.conn.commit()
        for table in ("photo_tags", "photo_folder", "photo_meta"):
            self.assertEqual(0, self.rows("SELECT COUNT(*) FROM %s" % table)[0][0], table)


class AKeywordAndEverythingUnderIt(ALibrary):
    def setUp(self):
        super().setUp()
        self.tree("People/Rowan", "People/Rowan/Swim Team", "People/Rowanne", "People/Rowan Quay", "People",
                  "Club_A", "Club_A/Juniors", "ClubXA", "ClubXA/Juniors", "Club%A", "Peoples")
        self.ids = {tag: self.photo("2024", "%d.jpg" % n, [tag]) for n, tag in enumerate(
            ["People/Rowan", "People/Rowan/Swim Team", "People/Rowanne", "People/Rowan Quay", "Club_A/Juniors",
             "ClubXA/Juniors", "Club%A", "Peoples"])}
        self.both = self.photo("2024", "both.jpg", ["People/Rowan", "People/Rowan/Swim Team"])
        self.rebuilt()

    def test_the_node_and_the_nodes_under_it_and_no_other(self):
        sql, params = derived.under("People/Rowan")
        found = sorted(tag for (tag,) in self.rows(
            "SELECT tag FROM tag_taxonomy WHERE id IN (%s)" % sql, *params))
        self.assertEqual(["People/Rowan", "People/Rowan/Swim Team"], found,
                         "not People/Rowanne or People/Rowan Quay, which only start the same")

    def test_the_photos_are_each_one_once_by_id(self):
        wanted = sorted([self.ids["People/Rowan"], self.ids["People/Rowan/Swim Team"], self.both])
        self.assertEqual(wanted, derived.photos_under_tag(self.conn, "People/Rowan"))
        self.assertEqual(3, derived.count_under_tag(self.conn, "People/Rowan"), "the photo with both counts once")
        self.assertEqual(wanted[1:], derived.photos_under_tag(self.conn, "People/Rowan", limit=2, offset=1))
        self.assertEqual([self.ids["People/Rowan/Swim Team"], self.both],
                         derived.photos_under_tag(self.conn, "People/Rowan/Swim Team"))

    def test_an_underscore_or_a_percent_in_a_tag_is_a_plain_character(self):
        self.assertEqual([self.ids["Club_A/Juniors"]], derived.photos_under_tag(self.conn, "Club_A"))
        self.assertEqual([self.ids["Club%A"]], derived.photos_under_tag(self.conn, "Club%A"))
        self.assertEqual([self.ids["ClubXA/Juniors"]], derived.photos_under_tag(self.conn, "ClubXA"))

    def test_a_root_is_everything_under_it_and_not_a_longer_name(self):
        found = derived.photos_under_tag(self.conn, "People")
        self.assertNotIn(self.ids["Peoples"], found)
        self.assertEqual(5, len(found))

    def test_a_tag_that_is_no_node_has_no_photos(self):
        self.assertEqual([], derived.photos_under_tag(self.conn, "Nowhere"))
        self.assertEqual(0, derived.count_under_tag(self.conn, "Nowhere"))

    def test_a_range_over_the_tree_is_where_the_tree_is_now(self):
        """The nodes keep their ids when the tree moves them, and the range is over the tree as it
        is: the node's new path and its descendants' (tests/test_derived_follow_writes.py has the
        photos' side of a rename)."""
        wanted = {self.node("People/Rowan"), self.node("People/Rowan/Swim Team")}
        taxonomy.move_branch(self.conn, "People/Rowan", "Family/Rowan")
        self.conn.commit()
        sql, params = derived.under("Family/Rowan")
        self.assertEqual(wanted, {row[0] for row in self.rows(sql, *params)})
        sql, params = derived.under("People/Rowan")
        self.assertEqual([], self.rows(sql, *params))

    def test_the_plans_search_the_indexes_and_scan_nothing(self):
        sql, params = derived.under("People/Rowan")
        plans = {
            "ids": ("EXPLAIN QUERY PLAN " + sql, params),
            "photos": ("EXPLAIN QUERY PLAN SELECT pt.photo_id FROM photo_tags pt WHERE pt.tag_id IN (%s)"
                       " GROUP BY pt.photo_id ORDER BY pt.photo_id LIMIT ? OFFSET ?" % sql, params + (10, 0)),
            "count": ("EXPLAIN QUERY PLAN SELECT COUNT(DISTINCT pt.photo_id) FROM photo_tags pt"
                      " WHERE pt.tag_id IN (%s)" % sql, params),
        }
        for name, (query, bound) in plans.items():
            details = [row[3] for row in self.conn.execute(query, bound)]
            self.assertTrue(any("SEARCH" in line for line in details), (name, details))
            self.assertFalse([line for line in details if line.startswith("SCAN")], (name, details))


class TheFolderTree(ALibrary):
    def ancestors(self, folder):
        found = []
        while True:
            found.append(folder)
            parent = os.path.dirname(folder)
            if parent == folder:
                return found
            folder = parent

    def test_a_folder_has_a_row_for_every_ancestor_to_the_top_of_its_spelling(self):
        one = self.photo(os.path.join("2024", "Coast"), "a.jpg")
        self.rebuilt()
        folder = os.path.join(self.pictures, "2024", "Coast")
        self.assertEqual(sorted(self.ancestors(folder), key=str.lower),
                         sorted((path for (path,) in self.rows("SELECT path FROM folders")), key=str.lower))
        (found,) = self.rows("SELECT f.path FROM photo_folder pf JOIN folders f ON f.id = pf.folder_id"
                             " WHERE pf.photo_id = ?", one)
        self.assertEqual(folder, found[0])
        for _id, parent_id, path, name in self.rows("SELECT id, parent_id, path, name FROM folders"):
            above = os.path.dirname(path)
            if above == path:
                self.assertIsNone(parent_id, "the top has no parent")
            else:
                self.assertEqual(above.lower(), self.rows("SELECT path FROM folders WHERE id = ?", parent_id)[0][0].lower())
            self.assertEqual(os.path.basename(path) or path, name)

    def test_a_folder_holding_no_photo_at_or_below_it_has_no_row(self):
        self.photo("2024", "a.jpg")
        self.rebuilt()
        paths_held = [path for (path,) in self.rows("SELECT path FROM folders")]
        self.assertNotIn(os.path.join(self.pictures, "2025"), paths_held)
        self.assertIn(self.pictures, paths_held, "an ancestor of a folder with photos")

    def test_spellings_that_differ_only_in_case_are_one_folder(self):
        self.photo("2024", "a.jpg")
        self.photo("2024", "b.jpg")
        path = os.path.join(self.pictures.upper(), "2024", "c.jpg")
        photos.record_indexed(self.conn, path, {})
        self.rebuilt()
        self.assertEqual(1, self.rows("SELECT COUNT(*) FROM folders WHERE path = ?", os.path.join(self.pictures, "2024"))[0][0])
        self.assertEqual(3, self.rows("SELECT COUNT(*) FROM photo_folder")[0][0])
        self.assertEqual(1, self.rows("SELECT COUNT(DISTINCT folder_id) FROM photo_folder")[0][0])

    def test_the_counts_each_folder_holds_directly_and_with_everything_below(self):
        for folder, count in (("2024", 3), (os.path.join("2024", "Coast"), 2), (os.path.join("2024", "Lake"), 1),
                              ("2025", 4)):
            for n in range(count):
                self.photo(folder, "p%d.jpg" % n)
        self.rebuilt()
        tree = {os.path.normcase(found["path"]): found for found in derived.folder_tree(self.conn)}
        at = lambda *parts: tree[os.path.normcase(os.path.join(self.pictures, *parts))]  # noqa: E731
        self.assertEqual((3, 6), (at("2024")["direct"], at("2024")["recursive"]))
        self.assertEqual((2, 2), (at("2024", "Coast")["direct"], at("2024", "Coast")["recursive"]))
        self.assertEqual((0, 10), (at()["direct"], at()["recursive"]))
        self.assertEqual(10, sum(found["direct"] for found in tree.values()), "every photo is directly in one folder")
        for found in tree.values():
            self.assertEqual(derived.recursive_count(self.conn, found["path"]), found["recursive"],
                             "the roll-up and the range of photos.path say the same")

    def test_the_recursive_count_is_one_seek_of_the_paths_index(self):
        from tagpup.store import roots as store_roots
        where, params = store_roots.sql_under(self.conn, "path", self.pictures)
        plan = [row[3] for row in self.conn.execute("EXPLAIN QUERY PLAN SELECT COUNT(*) FROM photos WHERE " + where, params)]
        self.assertTrue(any("SEARCH" in line for line in plan), plan)
        self.assertFalse([line for line in plan if line.startswith("SCAN")], plan)

    def test_a_photo_with_no_folder_to_name_is_in_none(self):
        self.photo("2024", "a.jpg")
        self.conn.execute("INSERT INTO photos (path, tags, captions, raw_metadata) VALUES ('loose.jpg', '[]', '[]', '{}')")
        self.rebuilt()
        self.assertEqual(1, self.rows("SELECT COUNT(*) FROM photo_folder")[0][0])
        self.assertEqual(([], [], 0, []), derived.stale_folders(self.conn))


class WhatAPhotoSaysOfItself(ALibrary):
    def test_a_row_for_every_photo_with_what_the_metadata_says(self):
        rated = self.photo("2024", "a.jpg", **{"XMP:Rating": 4, "EXIF:Make": "Harbourlight", "EXIF:Model": "HL 400",
                                               "Composite:GPSLatitude": -33.5, "Composite:GPSLongitude": 151.25})
        bare = self.photo("2024", "b.jpg")
        self.rebuilt()
        self.assertEqual([(4, "Harbourlight", "HL 400", None, None, -33.5, 151.25)],
                         self.rows("SELECT rating, make, model, width, height, latitude, longitude FROM photo_meta"
                                   " WHERE photo_id = ?", rated))
        self.assertEqual([(None,) * 7],
                         self.rows("SELECT rating, make, model, width, height, latitude, longitude FROM photo_meta"
                                   " WHERE photo_id = ?", bare))
        self.assertEqual(2, self.rows("SELECT COUNT(*) FROM photo_meta")[0][0])

    def test_a_row_never_read_or_damaged_says_nothing(self):
        path = os.path.join(self.pictures, "2024", "unread.jpg")
        self.conn.execute("INSERT INTO photos (path, tags, captions, raw_metadata) VALUES (?, '[]', '[]', '{{x')", (path,))
        self.rebuilt()
        self.assertEqual([(None,) * 7], self.rows("SELECT rating, make, model, width, height, latitude, longitude"
                                                  " FROM photo_meta"))


class WhatRebuildingDoes(ALibrary):
    def test_it_is_the_same_whenever_it_is_done_and_says_what_it_wrote(self):
        self.tree("Trips/Coast")
        self.photo("2024", "a.jpg", ["Trips/Coast"], **{"XMP:Rating": 2})
        self.photo(os.path.join("2024", "Lake"), "b.jpg", ["Trips/Coast"])
        first = derived.rebuild_all(self.conn)
        self.conn.commit()
        held = {table: self.rows("SELECT * FROM %s ORDER BY 1, 2" % table) for table in derived.TABLES}
        self.assertEqual(2, first["photos"])
        self.assertEqual(2, first["tag_rows"])
        self.assertEqual(2, first["in_a_folder"])
        self.assertEqual(2, first["meta_rows"])
        again = derived.rebuild_all(self.conn)
        self.conn.commit()
        self.assertEqual(first, again)
        self.assertEqual(held, {table: self.rows("SELECT * FROM %s ORDER BY 1, 2" % table) for table in derived.TABLES},
                         "a folder's id is kept while the folder is")

    def test_it_puts_right_what_is_wrong_and_the_rules_say_so_first(self):
        self.tree("Trips/Coast")
        one = self.photo("2024", "a.jpg", ["Trips/Coast"], **{"XMP:Rating": 2})
        self.rebuilt()
        self.assertEqual([], derived.problems(self.conn))
        self.conn.execute("DELETE FROM photo_tags")
        self.conn.execute("UPDATE photo_meta SET rating = 5")
        self.conn.execute("UPDATE folders SET parent_id = NULL")
        self.conn.execute("INSERT INTO photo_folder (photo_id, folder_id) VALUES (777, 1)")
        self.conn.commit()
        self.assertEqual([one], derived.stale_tags(self.conn))
        self.assertEqual([one], derived.stale_meta(self.conn))
        photos_wrong, folders_wrong, missing, strays = derived.stale_folders(self.conn)
        self.assertEqual([777], photos_wrong)
        self.assertTrue(folders_wrong)
        self.assertEqual(3, len(derived.problems(self.conn)))
        before, written, after = derived.repair(self.path)
        self.assertEqual(3, len(before))
        self.assertEqual(1, written["tag_rows"])
        self.assertEqual([], after)
        self.assertEqual([], derived.problems(self.conn))

    def test_a_tag_added_to_the_tree_after_a_photo_was_read_is_found_by_a_rebuild(self):
        one = self.photo("2024", "a.jpg", ["Trips/Coast"])
        self.rebuilt()
        self.assertEqual([], self.tags_of(one))
        self.tree("Trips/Coast")
        self.assertEqual([one], derived.stale_tags(self.conn))
        self.rebuilt()
        self.assertEqual(["Trips/Coast"], self.tags_of(one))


class TheMigration(unittest.TestCase):
    def library_at(self, version):
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        at_version(path, version)
        return home, path

    def seed(self, path):
        """Photos as the indexer recorded them at version 18, and a tree."""
        conn = db.connect(path)
        try:
            for tag in ("Trips/Coast", "People/Rowan Thackeray"):
                taxonomy.add_path(conn, tag)
            root = os.path.dirname(path)
            for n in range(6):
                photo_rows.add_read(conn, os.path.join(root, "Pictures", "2024" if n % 2 else "2025", "p%d.jpg" % n),
                                    {"XMP:Subject": ["Trips/Coast", "Unknown/Thing"] if n % 3 else [],
                                     "XMP:Rating": n})
            conn.commit()
        finally:
            conn.close()

    def look(self, path, sql):
        conn = db.connect(db.readonly_uri(path), uri=True)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def test_a_library_at_18_gets_the_tables_filled_from_its_rows_and_changes_nothing_else(self):
        _home, path = self.library_at(18)
        self.seed(path)
        # The columns that were there: a later migration (21) adds one to faces.
        columns = {table: ", ".join(row[1] for row in self.look(path, "PRAGMA table_info(%s)" % table))
                   for table in ("photos", "tag_taxonomy", "faces", "settings")}
        before = {table: self.look(path, "SELECT %s FROM %s ORDER BY 1" % (columns[table], table)) for table in columns}
        total = len(before["photos"])
        lists = [json.loads(tags or "[]") for (tags,) in self.look(path, "SELECT tags FROM photos")]
        self.assertGreater(total, 6, "the six seeded, and the two the older library holds")
        schema._current.clear()
        self.assertEqual(migration_names.after(18), schema.ensure(path))
        self.assertEqual(before, {table: self.look(path, "SELECT %s FROM %s ORDER BY 1" % (columns[table], table))
                                  for table in before})
        conn = db.connect(db.readonly_uri(path), uri=True)
        try:
            self.assertEqual([], derived.problems(conn))
            self.assertEqual(total, conn.execute("SELECT COUNT(*) FROM photo_meta").fetchone()[0])
            self.assertEqual(total, conn.execute("SELECT COUNT(*) FROM photo_folder").fetchone()[0])
            nodes = {tag for (tag,) in conn.execute("SELECT tag FROM tag_taxonomy")}
            self.assertEqual(sum(1 for tags in lists for tag in set(tags) if tag in nodes),
                             conn.execute("SELECT COUNT(*) FROM photo_tags").fetchone()[0])
            self.assertEqual({"Unknown/Thing": 4}, dict(derived.tags_without_a_node(conn)))
        finally:
            conn.close()

    def test_it_is_recorded_in_the_journal_as_additive_and_needs_no_backup(self):
        home, path = self.library_at(18)
        self.seed(path)
        schema._current.clear()
        schema.ensure(path)
        entry = [e for e in journal_history(path) if e["operation"].startswith("migration 19")][0]
        self.assertEqual("additive", entry["summary"]["kind"])
        self.assertIn("nothing that was there changed", entry["summary"]["checks"])
        self.assertIsNone(entry["summary"].get("backup"))

    def test_interrupted_part_way_it_leaves_the_library_at_18_and_a_second_run_does_it(self):
        _home, path = self.library_at(18)
        self.seed(path)
        schema._current.clear()
        with mock.patch.object(derived, "rebuild_all", side_effect=RuntimeError("the power went")):
            with self.assertRaises(RuntimeError):
                schema.ensure(path)
        self.assertEqual([(18,)], self.look(path, "SELECT MAX(version) FROM schema_version"))
        names = {name for (name,) in self.look(path, "SELECT name FROM sqlite_master")}
        self.assertFalse(names & {"photo_tags", "folders", "photo_folder", "photo_meta", "derived_go_with_their_photo",
                                  "photo_tags_go_with_their_node", "idx_photo_tags_tag"}, "all or nothing")
        schema._current.clear()
        self.assertEqual(migration_names.after(18), schema.ensure(path))
        self.assertEqual([(schema.LATEST,)], self.look(path, "SELECT MAX(version) FROM schema_version"))

    def test_a_failed_check_rolls_it_back(self):
        _home, path = self.library_at(18)
        self.seed(path)
        schema._current.clear()
        with mock.patch.object(derived, "problems", return_value=["1 photo(s) are wrong"]):
            with self.assertRaises(schema.CheckFailed) as failed:
                schema.ensure(path)
        self.assertIn("derived tables agree with the photos", str(failed.exception))
        self.assertEqual([(18,)], self.look(path, "SELECT MAX(version) FROM schema_version"))

    def test_a_snapshot_from_before_it_is_migrated_again_when_opened(self):
        home, path = self.library_at(18)
        self.seed(path)
        snapshot = home.library("snapshot-18.db")
        conn = db.connect(path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        import shutil
        shutil.copyfile(path, snapshot)
        schema._current.clear()
        schema.ensure(path)
        shutil.copyfile(snapshot, path)   # restored over the migrated library
        schema._current.clear()
        self.assertEqual(migration_names.after(18), schema.ensure(path))
        self.assertEqual(self.look(path, "SELECT COUNT(*) FROM photos")[0][0],
                         self.look(path, "SELECT COUNT(*) FROM photo_meta")[0][0])

    def test_two_openers_apply_it_once(self):
        _home, path = self.library_at(18)
        self.seed(path)
        schema._current.clear()
        first = schema.ensure(path)
        schema._current.clear()
        second = schema.ensure(path)
        self.assertEqual((migration_names.after(18), []), (first, second))

    def test_a_library_made_new_has_the_tables_empty(self):
        home = own_home.for_test(self)
        path = home.library("fresh.db")
        schema.ensure(path)
        for table in derived.TABLES:
            self.assertEqual([(0,)], self.look(path, "SELECT COUNT(*) FROM %s" % table), table)

    def test_nothing_is_written_to_a_library_that_has_not_the_tables_yet(self):
        _home, path = self.library_at(18)
        self.seed(path)
        conn = db.connect(path)
        try:
            self.assertFalse(derived.present(conn))
            self.assertEqual(0, derived.refresh_photos(conn, [1, 2, 3]))
            self.assertEqual(0, derived.record(conn, 1, "x", [], {}))
            self.assertEqual((0, 0), derived.rebuild_folders(conn))
            self.assertIsNone(derived.tree_before(conn))
            self.assertEqual(0, derived.follow_tree(conn, None))
            self.assertEqual(0, derived.follow_nodes(conn, ["Trips"]))
        finally:
            conn.close()


def journal_history(path):
    from tagpup.store import journal
    return journal.history(path, limit=100)


if __name__ == "__main__":
    unittest.main()
