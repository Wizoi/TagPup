"""A test's photo row is made by the code that makes the real one (tests/photo_rows.py: add_read, add_unread), not by a
bare `INSERT INTO photos`, which makes no folder row: the folders reader (store.folders.with_rows) reads the derived
folders, so such a row is in no folder, a state no indexer leaves (docs/findings.md, #1505).

The files below predate that rule and seed a row by hand. By design, for each: none reads a folder list, a sync or the
folder watcher through such a row (swept 2026-10-16; the three that did, a scan, a folder removal and a sync, were moved to
add_read, and test_folder_watcher and the subfolder counts before them), or it rebuilds the derived tables after seeding
(derived.rebuild_all), so a bare INSERT there is harmless. It is a ratchet: a file may not be added, and one that no longer has a bare INSERT
must be taken off the list, so it only shrinks.
"""
import glob
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BARE = "INSERT INTO photos"

#: The test files and helpers that still seed a photo row by hand: a count of such statements in each.
STILL_BARE = {
    "face_rows.py": 1, "journal_library.py": 1, "service_fixture.py": 1, "test_compact_gives_back_free_space.py": 1,
    "test_database_axis_guard.py": 1, "test_database_context_isolation.py": 1, "test_dedupe_faces_keeps_decisions.py": 1,
    "test_derived_tables.py": 1, "test_derived_writers.py": 1, "test_doctor.py": 3,
    "test_face_click_reads_named_faces_once.py": 1, "test_identify_cache_keeps_others_writes.py": 1,
    "test_identify_cache_sees_every_change.py": 1, "test_inspect_uses_the_stores_mtime_rule.py": 1,
    "test_journal_undo_refusals.py": 1, "test_maintenance.py": 3, "test_mcp_photo_against_file.py": 4,
    "test_mcp_server.py": 2, "test_mcp_write_tools.py": 3, "test_partial_writes_leave_rows_unread.py": 1,
    "test_paths.py": 1, "test_paths_roots.py": 1, "test_person_rename_reaches_the_files.py": 1, "test_refresh_rows.py": 1,
    "test_relink_checks_the_destination.py": 1, "test_roots_at_scale.py": 1, "test_roots_sample.py": 1, "test_schema.py": 2,
    "test_snapshots.py": 1, "test_suggest_index_stays_current.py": 1, "test_suggest_reads_people_once.py": 1,
    "test_tag_writes_keep_the_index_true.py": 1, "test_taxonomy_edits_report_what_they_wrote.py": 1,
    "test_tuner_reads.py": 1, "test_web_app.py": 1,
}


class PhotoRowsAreMadeLikeTheIndexersTest(unittest.TestCase):
    def test_no_new_file_seeds_a_photo_row_by_hand_and_the_list_only_shrinks(self):
        found = {}
        for path in glob.glob(os.path.join(HERE, "*.py")):
            name = os.path.basename(path)
            if name == os.path.basename(__file__):
                continue
            with open(path, encoding="utf-8") as handle:
                count = handle.read().count(BARE)
            if count:
                found[name] = count
        self.assertEqual({}, {name: count for name, count in found.items() if count > STILL_BARE.get(name, 0)},
                         "seed a photo row with photo_rows.add_read (or add_unread), as the indexer makes it")
        self.assertEqual([], sorted(name for name in STILL_BARE if name not in found or found[name] < STILL_BARE[name]),
                         "these have fewer bare INSERTs than listed: lower or remove them in STILL_BARE")


if __name__ == "__main__":
    unittest.main()
