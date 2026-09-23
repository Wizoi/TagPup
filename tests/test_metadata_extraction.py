"""Unit tests for metadata parsing, people resolution and caption derivation.

Everything the index stores comes through these functions, so a defect here is
persisted into the database and then written back into photo files by later edits.
They take plain dictionaries -- the shape ExifTool returns -- so no image, no ExifTool
and no model is needed.

People resolution is the subtle part: a name can arrive as an explicit PersonInImage
tag, as the leaf of a hierarchical tag under a face-matching root, or as a flat tag
that the taxonomy marks as a person. All three paths are covered, including the
database-backed ones using a temporary taxonomy.
"""
import os
import sys
import sqlite3
import shutil
import tempfile
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from metadata import (
    clean_metadata_value,
    extract_tags,
    extract_people,
    extract_captions,
    get_people_roots,
    parse_year_from_metadata,
    build_photo_ui_record,
    sanitize_filename,
)
from writer import derive_caption_from_tags


class TestCleanMetadataValue(unittest.TestCase):
    def test_passes_scalars_through(self):
        self.assertEqual(clean_metadata_value("hello"), "hello")
        self.assertEqual(clean_metadata_value(42), 42)

    def test_none_stays_none(self):
        self.assertIsNone(clean_metadata_value(None))

    def test_collapses_single_element_lists(self):
        self.assertEqual(clean_metadata_value(["only"]), "only")

    def test_keeps_multi_element_lists(self):
        self.assertEqual(clean_metadata_value(["a", "b"]), ["a", "b"])

    def test_drops_none_entries_and_empties_become_none(self):
        self.assertIsNone(clean_metadata_value([None, None]))

    def test_unwraps_exiftool_value_structs(self):
        self.assertEqual(clean_metadata_value({"value": "inner"}), "inner")

    def test_decodes_bytes(self):
        self.assertEqual(clean_metadata_value(b"caption"), "caption")

    def test_survives_undecodable_bytes(self):
        self.assertIsInstance(clean_metadata_value(b"\xff\xfe bad"), str)


class TestExtractTags(unittest.TestCase):
    def test_reads_both_namespaced_and_bare_keys(self):
        self.assertEqual(extract_tags({"XMP:Subject": ["Alpha"]}), ["Alpha"])
        self.assertEqual(extract_tags({"Subject": ["Beta"]}), ["Beta"])
        self.assertEqual(extract_tags({"IPTC:Keywords": ["Gamma"]}), ["Gamma"])

    def test_accepts_a_bare_string_as_well_as_a_list(self):
        self.assertEqual(extract_tags({"XMP:Subject": "Alpha"}), ["Alpha"])

    def test_deduplicates_across_keys(self):
        meta = {"XMP:Subject": ["Alpha"], "IPTC:Keywords": ["Alpha"]}
        self.assertEqual(extract_tags(meta), ["Alpha"])

    def test_drops_flat_fragments_of_a_hierarchical_tag(self):
        """Windows writes both 'Trips/Texas' and its parts; only the path is meaningful."""
        meta = {
            "XMP:Subject": ["Trips", "Texas", "Holidays"],
            "XMP:HierarchicalSubject": ["Trips/Texas"],
        }
        tags = extract_tags(meta)
        self.assertIn("Trips/Texas", tags)
        self.assertNotIn("Trips", tags)
        self.assertNotIn("Texas", tags)
        self.assertIn("Holidays", tags, "an unrelated flat tag was discarded")

    def test_ignores_empty_values(self):
        self.assertEqual(extract_tags({"XMP:Subject": ["", None, "Alpha"]}), ["Alpha"])

    def test_missing_keys_yield_no_tags(self):
        self.assertEqual(extract_tags({}), [])


class TestExtractCaptions(unittest.TestCase):
    def test_collects_captions_from_every_known_field(self):
        meta = {
            "IPTC:Caption-Abstract": "abstract",
            "XMP:Description": "description",
            "XMP:Title": "title",
        }
        captions = extract_captions(meta)
        for expected in ("abstract", "description", "title"):
            self.assertIn(expected, captions)

    def test_caption_abstract_is_preferred_first(self):
        """build_photo_ui_record takes captions[0] as the title, so order matters."""
        meta = {"XMP:Description": "description", "IPTC:Caption-Abstract": "abstract"}
        self.assertEqual(extract_captions(meta)[0], "abstract")

    def test_blank_captions_are_dropped(self):
        self.assertEqual(extract_captions({"XMP:Description": "   "}), [])


class TestExtractPeopleWithoutADatabase(unittest.TestCase):
    """Default face roots are family/friends/people when no taxonomy is available."""

    def test_reads_person_in_image(self):
        people = extract_people({"XMP:PersonInImage": ["Jane Doe"]}, [], db_path="")
        self.assertEqual(people, ["Jane Doe"])

    def test_reads_region_names(self):
        people = extract_people({"XMP:RegionName": ["Jane Doe"]}, [], db_path="")
        self.assertEqual(people, ["Jane Doe"])

    def test_takes_the_leaf_of_a_hierarchical_family_tag(self):
        people = extract_people({}, ["Family/Immediate/Jane Doe"], db_path="")
        self.assertEqual(people, ["Jane Doe"])

    def test_handles_backslash_and_pipe_separators(self):
        self.assertEqual(extract_people({}, [r"Family\Jane Doe"], db_path=""), ["Jane Doe"])
        self.assertEqual(extract_people({}, ["Friends|Bob Roe"], db_path=""), ["Bob Roe"])

    def test_ignores_non_people_hierarchies(self):
        self.assertEqual(extract_people({}, ["Trips/Texas"], db_path=""), [])

    def test_ignores_a_bare_root_with_no_leaf(self):
        self.assertEqual(extract_people({}, ["Family"], db_path=""), [])

    def test_no_database_means_no_taxonomy_resolution(self):
        """Without a database there must be no implicit fallback to another library.

        extract_people used to default to data/photo_index.db, so a caller working on
        one database silently resolved people against the default library's taxonomy.
        """
        self.assertEqual(extract_people({}, ["Some Person"], db_path=""), [])
        self.assertEqual(extract_people({}, ["Some Person"]), [])

    def test_deduplicates_names_from_different_sources(self):
        people = extract_people(
            {"XMP:PersonInImage": ["Jane Doe"]}, ["Family/Jane Doe"], db_path=""
        )
        self.assertEqual(people, ["Jane Doe"])


class TestExtractPeopleWithATaxonomy(unittest.TestCase):
    """Custom face roots and flat person tags require the taxonomy database."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="tagpup_meta_")
        os.close(fd)
        self.addCleanup(self._remove_db)

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """CREATE TABLE tag_taxonomy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tag TEXT UNIQUE,
                parent_id INTEGER,
                name TEXT,
                has_face INTEGER DEFAULT 0,
                hidden_from_autocomplete INTEGER DEFAULT 0
            )"""
        )
        conn.commit()
        conn.close()

    def _remove_db(self):
        try:
            os.remove(self.db_path)
        except Exception:
            pass

    def add_tag(self, tag, name, has_face, parent_id=None):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, ?, ?, ?)",
            (tag, parent_id, name, has_face),
        )
        conn.commit()
        conn.close()

    def test_default_roots_are_always_present(self):
        roots = get_people_roots(db_path=self.db_path)
        for expected in ("family", "friends", "people"):
            self.assertIn(expected, roots)

    def test_a_custom_root_marked_as_a_face_category_is_honoured(self):
        self.add_tag("Crew", "Crew", has_face=1)
        self.assertIn("crew", get_people_roots(db_path=self.db_path))

    def test_a_root_not_marked_as_a_face_category_is_not(self):
        self.add_tag("Trips", "Trips", has_face=0)
        self.assertNotIn("trips", get_people_roots(db_path=self.db_path))

    def test_people_are_extracted_under_a_custom_face_root(self):
        self.add_tag("Crew", "Crew", has_face=1)
        people = extract_people({}, ["Crew/Jane Doe"], db_path=self.db_path)
        self.assertEqual(people, ["Jane Doe"])

    def test_a_flat_tag_marked_as_a_person_resolves_to_that_person(self):
        """'Cora Ingersoll' with no hierarchy still counts if the taxonomy says it is a person."""
        self.add_tag("People", "People", has_face=1)
        self.add_tag("People/Cora Ingersoll", "Cora Ingersoll", has_face=1)
        people = extract_people({}, ["Cora Ingersoll"], db_path=self.db_path)
        self.assertEqual(people, ["Cora Ingersoll"])

    def test_a_face_root_category_never_resolves_as_a_person(self):
        """"Family" is a category; a photo tagged with it gains no person named Family."""
        self.add_tag("Family", "Family", has_face=1)
        self.assertEqual(extract_people({}, ["Family"], db_path=self.db_path), [])

    def test_a_person_under_a_face_root_still_resolves(self):
        self.add_tag("Family", "Family", has_face=1)
        self.add_tag("Family/Jane Doe", "Jane Doe", has_face=1)
        self.assertEqual(
            extract_people({}, ["Jane Doe"], db_path=self.db_path), ["Jane Doe"]
        )

    def test_a_flat_tag_that_is_not_a_person_is_ignored(self):
        self.add_tag("Trips/Texas", "Texas", has_face=0)
        self.assertEqual(extract_people({}, ["Texas"], db_path=self.db_path), [])

    def test_an_existing_connection_is_reused_instead_of_opening_another(self):
        """Opening a second connection inside a write transaction deadlocks SQLite."""
        self.add_tag("Crew", "Crew", has_face=1)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("BEGIN EXCLUSIVE")
            conn.execute(
                "INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, ?, ?, ?)",
                ("Crew/Temp", None, "Temp", 1),
            )
            people = extract_people({}, ["Crew/Jane Doe"], conn=conn)
            self.assertEqual(people, ["Jane Doe"])
        finally:
            conn.rollback()
            conn.close()


class TestParseYearFromMetadata(unittest.TestCase):
    def test_prefers_the_exif_capture_date(self):
        meta = {"raw_metadata": {"EXIF:DateTimeOriginal": "2014:07:04 10:00:00"}}
        self.assertEqual(parse_year_from_metadata(meta), 2014)

    def test_falls_back_through_the_documented_key_order(self):
        meta = {"raw_metadata": {"EXIF:CreateDate": "2011:01:01 00:00:00"}}
        self.assertEqual(parse_year_from_metadata(meta), 2011)

    def test_rejects_implausible_years(self):
        meta = {"raw_metadata": {"EXIF:DateTimeOriginal": "0000:00:00 00:00:00"}}
        self.assertIsNone(parse_year_from_metadata(meta))

    def test_falls_back_to_a_year_in_the_filename(self):
        meta = {"path": r"D:\Library\misc\1998 birthday.jpg"}
        self.assertEqual(parse_year_from_metadata(meta), 1998)

    def test_falls_back_to_a_year_in_a_folder_name(self):
        meta = {"path": r"D:\Library\1998\Christmas\IMG_0001.jpg"}
        self.assertEqual(parse_year_from_metadata(meta), 1998)

    def test_the_filename_wins_over_a_folder(self):
        meta = {"path": r"D:\Library\1998\2001 party.jpg"}
        self.assertEqual(parse_year_from_metadata(meta), 2001)

    def test_the_nearest_folder_wins_over_a_distant_one(self):
        meta = {"path": r"D:\1990\2005 Trip\IMG_0001.jpg"}
        self.assertEqual(parse_year_from_metadata(meta), 2005)

    def test_exif_wins_over_the_path(self):
        meta = {
            "path": r"D:\Library\1998\IMG_0001.jpg",
            "raw_metadata": {"EXIF:DateTimeOriginal": "2014:07:04 10:00:00"},
        }
        self.assertEqual(parse_year_from_metadata(meta), 2014)

    def test_returns_none_when_nothing_is_datable(self):
        self.assertIsNone(parse_year_from_metadata({"path": r"D:\Library\IMG_0001.jpg"}))

    def test_tolerates_a_list_valued_date_tag(self):
        meta = {"raw_metadata": {"EXIF:DateTimeOriginal": ["2014:07:04 10:00:00"]}}
        self.assertEqual(parse_year_from_metadata(meta), 2014)

    def test_a_four_digit_number_that_is_not_a_year_is_skipped(self):
        meta = {"path": r"D:\Library\IMG_9999.jpg"}
        self.assertIsNone(parse_year_from_metadata(meta))


class TestBuildPhotoUIRecord(unittest.TestCase):
    def test_carries_the_core_fields_through(self):
        meta = {
            "tags": ["Trips/Texas"],
            "people": ["Jane Doe"],
            "captions": ["A trip"],
            "raw_metadata": {"EXIF:DateTimeOriginal": "2014:07:04 10:00:00"},
        }
        rec = build_photo_ui_record(r"D:\Library\a.jpg", meta, mtime=123.0, size=456)
        self.assertEqual(rec["filename"], "a.jpg")
        self.assertEqual(rec["tags"], ["Trips/Texas"])
        self.assertEqual(rec["people"], ["Jane Doe"])
        self.assertEqual(rec["title"], "A trip")
        self.assertEqual(rec["mtime"], 123.0)
        self.assertEqual(rec["size"], 456)
        self.assertEqual(rec["year"], "2014")

    def test_year_is_the_string_unknown_when_undatable(self):
        """Callers compare this field, so it must never be None or an int."""
        rec = build_photo_ui_record(r"D:\Library\a.jpg", {})
        self.assertEqual(rec["year"], "Unknown")
        self.assertIsInstance(rec["year"], str)

    def test_missing_captions_give_an_empty_title(self):
        self.assertEqual(build_photo_ui_record(r"D:\a.jpg", {})["title"], "")


class TestSanitizeFilename(unittest.TestCase):
    def test_replaces_every_reserved_character(self):
        cleaned = sanitize_filename('a<b>c:d"e/f\\g|h?i*j')
        for bad in '<>:"/\\|?*':
            self.assertNotIn(bad, cleaned)

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(sanitize_filename("  name  "), "name")

    def test_removes_unprintable_characters(self):
        self.assertEqual(sanitize_filename("na\x00me"), "name")

    def test_leaves_an_ordinary_name_alone(self):
        self.assertEqual(sanitize_filename("2014 - 01 - Beach day"), "2014 - 01 - Beach day")


class TestDeriveCaptionFromTags(unittest.TestCase):
    def test_no_tags_gives_no_caption(self):
        self.assertIsNone(derive_caption_from_tags([]))

    def test_a_single_person(self):
        self.assertEqual(derive_caption_from_tags(["Family/Jane Doe"]), "Jane Doe")

    def test_two_people_are_joined_with_and(self):
        caption = derive_caption_from_tags(["Family/Jane Doe", "Family/Bob Roe"])
        self.assertEqual(caption, "Bob Roe and Jane Doe")

    def test_three_people_use_an_oxford_comma(self):
        """Ordering follows the full tag path, so Family/* precedes Friends/*."""
        caption = derive_caption_from_tags(
            ["Family/Jane Doe", "Family/Bob Roe", "Friends/Amy Poe"]
        )
        self.assertEqual(caption, "Bob Roe, Jane Doe, and Amy Poe")

    def test_activity_follows_people_after_a_dash(self):
        caption = derive_caption_from_tags(["Family/Jane Doe", "Activity/Birthday"])
        self.assertEqual(caption, "Jane Doe - Birthday")

    def test_location_follows_after_a_comma(self):
        caption = derive_caption_from_tags(
            ["Family/Jane Doe", "Activity/Birthday", "Trips/Texas"]
        )
        self.assertEqual(caption, "Jane Doe - Birthday, Texas")

    def test_activity_alone_becomes_the_caption(self):
        self.assertEqual(derive_caption_from_tags(["Activity/Birthday"]), "Birthday")

    def test_location_alone_becomes_the_caption(self):
        self.assertEqual(derive_caption_from_tags(["Trips/Texas"]), "Texas")

    def test_other_tags_are_used_as_a_last_resort(self):
        self.assertEqual(derive_caption_from_tags(["Holidays/Christmas"]), "Christmas")

    def test_output_is_deterministic_regardless_of_input_order(self):
        a = derive_caption_from_tags(["Family/Jane Doe", "Family/Bob Roe"])
        b = derive_caption_from_tags(["Family/Bob Roe", "Family/Jane Doe"])
        self.assertEqual(a, b)

    def test_duplicate_tags_appear_once(self):
        caption = derive_caption_from_tags(["Family/Jane Doe", "Family/Jane Doe"])
        self.assertEqual(caption, "Jane Doe")


class TestBatchReadSurvivesOneBadFile(unittest.TestCase):
    """One unreadable file must not cost its batch-mates their metadata.

    ExifTool exits non-zero if any file in the batch is unreadable, and pyexiftool
    raises on that status. The handler used to hand every file in the batch an empty
    skeleton, so a single corrupt file blanked the tags, people and captions of up to
    499 good ones -- and indexed them as untagged. It also dropped their mtime and
    size, so change detection saw them as new on every later run.
    """

    def setUp(self):
        from metadata import MetadataExtractor

        self.tmpdir = tempfile.mkdtemp(prefix="meta_batch_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.paths = []
        for i in range(3):
            p = os.path.join(self.tmpdir, f"photo_{i}.jpg")
            with open(p, "wb") as fh:
                fh.write(b"x" * (100 + i))
            self.paths.append(p)
        self.bad = self.paths[1]
        self.extractor = MetadataExtractor()

    def _tags_for(self, path):
        return {"SourceFile": path, "XMP:Subject": ["Beach", "Sunset"]}

    def _fake_helper(self, fail_whole_batch=True):
        """An ExifTool stand-in that refuses any call containing the bad file."""
        outer = self

        class FakeHelper:
            def __init__(self, executable=None):
                self.running = False

            def run(self):
                self.running = True

            def terminate(self):
                self.running = False

            def __enter__(self):
                self.run()
                return self

            def __exit__(self, *exc):
                self.terminate()
                return False

            def get_tags(self, files, tags=None):
                if outer.bad in files:
                    raise RuntimeError("execute returned a non-zero exit status: 1")
                return [outer._tags_for(f) for f in files]

        return FakeHelper

    def test_the_good_files_keep_their_metadata(self):
        import metadata

        with mock.patch.object(metadata, "ExifToolSession", self._fake_helper()):
            results = self.extractor.batch_read(self.paths)

        self.assertEqual([r["path"] for r in results], self.paths)
        by_path = {r["path"]: r for r in results}
        for good in (self.paths[0], self.paths[2]):
            self.assertEqual(
                by_path[good]["tags"], ["Beach", "Sunset"],
                f"{os.path.basename(good)} lost its tags to a different file's failure",
            )

    def test_the_bad_file_is_the_only_one_blanked(self):
        import metadata

        with mock.patch.object(metadata, "ExifToolSession", self._fake_helper()):
            results = self.extractor.batch_read(self.paths)

        bad = next(r for r in results if r["path"] == self.bad)
        self.assertEqual(bad["tags"], [])
        self.assertEqual(bad["people"], [])
        self.assertEqual(bad["captions"], [])

    def test_every_file_keeps_its_stats_so_it_is_not_re_indexed_forever(self):
        import metadata

        with mock.patch.object(metadata, "ExifToolSession", self._fake_helper()):
            results = self.extractor.batch_read(self.paths)

        for r in results:
            stat = os.stat(r["path"])
            self.assertEqual(r["mtime"], stat.st_mtime, r["path"])
            self.assertEqual(r["size"], stat.st_size, r["path"])

    def test_a_clean_batch_still_reads_in_one_call(self):
        """The retry is the exception; a healthy batch must not pay for it."""
        import metadata

        calls = []
        fake = self._fake_helper()
        original = fake.get_tags

        def counting(self_, files, tags=None):
            calls.append(list(files))
            return original(self_, files, tags=tags)

        fake.get_tags = counting
        good_only = [self.paths[0], self.paths[2]]
        # Minting an identity is a write per photo that lacks one, and these fixtures
        # lack them all. That is a separate concern from whether the *read* was
        # retried, which is what this test is about; tests/test_photo_identity.py
        # covers the minting.
        reader = metadata.MetadataExtractor(mint_identities=False)
        with mock.patch.object(metadata, "ExifToolSession", fake):
            results = reader.batch_read(good_only)

        self.assertEqual(len(calls), 1, f"took {len(calls)} ExifTool calls for a clean batch")
        self.assertEqual([r["tags"] for r in results], [["Beach", "Sunset"]] * 2)

    def test_exiftool_failing_to_start_at_all_still_returns_a_row_per_file(self):
        import metadata

        class DeadHelper:
            def __init__(self, executable=None):
                raise OSError("exiftool not found")

        with mock.patch.object(metadata, "ExifToolSession", DeadHelper):
            results = self.extractor.batch_read(self.paths)

        self.assertEqual([r["path"] for r in results], self.paths)
        self.assertTrue(all(r["tags"] == [] for r in results))


if __name__ == "__main__":
    unittest.main()
