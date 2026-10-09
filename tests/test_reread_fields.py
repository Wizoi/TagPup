"""Photos read before the lens fields were asked for are read again, for them (tagpup.services.reread_fields;
docs/findings.md, #1018 to #1022).

Rows are made as the OLD indexer made them -- a read of the fields asked for then, which hold no lens, and no
`TagPup:ReadGeneration` -- through photo_rows.as_read and the store's record_indexed; the files answer, through the
stand-in ExifTool of test_sync, what a file of that photo holds now, a lens among it. Fictional makers and lenses.

What is held to: the lens is found by search and shown on the panel's line once read; a dry run writes nothing and reads
only a sample; a second run reads nothing; an interrupted run keeps the chunks it wrote and the next takes up the rest; faces,
names, tags and people are as they were; a missing, unreadable or changed file is counted and left while the rest go on; no
file on a share nobody named is read; a row the app saved meanwhile is left for the next run.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_rows  # noqa: E402
from face_rows import add_face  # noqa: E402
from test_sync import THEN, Reads, SyncTestCase  # noqa: E402

from tagpup.core import fields, photo_meta  # noqa: E402
from tagpup.files import metadata  # noqa: E402
from tagpup.services import library_view, photos as photo_service, reread_fields  # noqa: E402
from tagpup.store import db, journal  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402

CAMERA = {"EXIF:Make": "Tidewater", "EXIF:Model": "Tidewater TX R6m2"}
LENS = {"EXIF:LensModel": "EF24-70mm f/2.8L II USM", "EXIF:LensMake": "Tidewater"}
OTHER_LENS = {"EXIF:LensModel": "Objectif Elan 35mm F1.4"}


def without_lens(answer):
    """What a read said before the lens fields were asked for."""
    return {key: value for key, value in answer.items() if "Lens" not in key}


class Rereads(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.first = self.photo(self.meet, "IMG_0001.jpg", **dict(CAMERA, **LENS, **{"XMP:Subject": ["Events/Invitational"]}))
        self.second = self.photo(self.meet, "IMG_0002.jpg", **dict(CAMERA, **OTHER_LENS))
        self.third = self.photo(self.trip, "IMG_0003.jpg", **CAMERA)   # holds no lens: read, and none found
        self.all = (self.first, self.second, self.third)
        self.indexed_before(*self.all)

    def indexed_before(self, *photo_paths):
        """Rows as the indexer recorded them before the lens fields and the generation were asked for and recorded."""
        conn = db.connect(self.db_path)
        try:
            for path in photo_paths:
                record = photo_rows.as_read(path, without_lens(self.truth[os.path.basename(path)]))
                del record["raw_metadata"][fields.READ_GENERATION_KEY]
                store_photos.record_indexed(conn, path, record)
            conn.commit()
        finally:
            conn.close()

    def run_it(self, **more):
        self.reads = Reads(self.truth)
        with mock.patch("tagpup.files.metadata.MetadataExtractor.batch_read", autospec=True,
                        side_effect=self.reads.batch_read):
            return reread_fields.reread_fields(self.library, "exiftool", **more)

    def raw(self, path):
        return json.loads(self.query("SELECT raw_metadata FROM photos WHERE path = ?", (path,))[0][0])

    def found(self, text):
        return library_view.ids(self.library, "search", json.dumps({"words": text}))["ids"]

    def id_of(self, path):
        return self.query("SELECT id FROM photos WHERE path = ?", (path,))[0][0]


class TheLensComesWithTheReread(Rereads):
    def test_a_lens_read_through_the_real_extraction_is_found_and_shown(self):
        self.assertEqual([], self.found("ef24-70"), "the lens was there before it was read")
        result = self.run_it(apply=True)
        self.assertTrue(result.ok, result.message())
        self.assertEqual(3, result.changed)
        self.assertEqual([self.id_of(self.first)], self.found("ef24-70"))
        self.assertEqual([self.id_of(self.second)], self.found("elan"))
        row = self.raw(self.first)
        self.assertEqual("EF24-70mm f/2.8L II USM", row["EXIF:LensModel"])
        self.assertEqual("EF24-70mm f/2.8L II USM", row["LensModel"], "the bare name, as a real read records it")
        # The page's record: the line shown is the camera, then the lens (web/tagpup/photo.js joins them with a dot).
        record = photo_service.page_record(self.first, {"raw_metadata": row})
        self.assertEqual(("Tidewater TX R6m2", "Tidewater EF24-70mm f/2.8L II USM"), (record["camera"], record["lens"]))
        self.assertEqual("", photo_service.page_record(self.third, {"raw_metadata": self.raw(self.third)})["lens"])

    def test_the_derived_tables_are_rebuilt_by_the_same_write_with_no_doctor_run(self):
        self.run_it(apply=True)
        words = self.query("SELECT COUNT(*) FROM search_gear WHERE search_gear MATCH ?", ('lens:"24-70"*',))[0][0]
        self.assertEqual(1, words)

    def test_a_row_records_the_generation_of_the_read_and_the_lensless_photo_is_not_read_again(self):
        self.run_it(apply=True)
        self.assertEqual(fields.READ_GENERATION, self.raw(self.third)[fields.READ_GENERATION_KEY])
        again = self.run_it(apply=True)
        self.assertEqual([], self.reads.read, "a photo read already was read again")
        self.assertEqual(0, again.changed)
        self.assertEqual(3, again.details["counts"]["current"])

    def test_the_change_is_one_journaled_change_and_undone_as_one(self):
        before = {path: self.raw(path) for path in self.all}
        result = self.run_it(apply=True)
        self.assertEqual(1, len(result.details["changes"]))
        journal.undo(self.db_path, result.details["changes"][0])
        self.assertEqual(before, {path: self.raw(path) for path in self.all})
        self.assertEqual([], self.found("ef24-70"))


class ADryRunWritesNothing(Rereads):
    def test_it_counts_reads_only_a_sample_and_estimates_the_rest(self):
        rows = self.query("SELECT id, raw_metadata, tags FROM photos ORDER BY id")
        result = self.run_it(sample=2)
        self.assertEqual(rows, self.query("SELECT id, raw_metadata, tags FROM photos ORDER BY id"))
        self.assertEqual(2, len(self.reads.read))
        counts = result.details["counts"]
        self.assertEqual((3, 0, 0, 3), (counts["rows"], counts["current"], counts["never_read"], counts["to_read"]))
        self.assertEqual(2, result.details["sample"]["read"])
        self.assertIsNotNone(result.details["estimate_seconds"])
        self.assertEqual([], result.details["changes"])
        self.assertEqual(0, result.changed)

    def test_a_library_with_nothing_to_read_reads_no_file(self):
        self.run_it(apply=True)
        result = self.run_it()
        self.assertEqual([], self.reads.read)
        self.assertEqual(0, result.attempted)


class WhatIsLeftAlone(Rereads):
    def test_faces_names_tags_and_people_are_as_they_were(self):
        conn = db.connect(self.db_path)
        try:
            add_face(conn, self.first, [0, 0, 10, 10], name="Rowan Thackeray", name_source="manual")
            tags_before = conn.execute("SELECT id, tags FROM photos ORDER BY id").fetchall()
            faces_before = conn.execute("SELECT photo_id, name, name_source, box FROM faces").fetchall()
            conn.commit()
        finally:
            conn.close()
        self.run_it(apply=True)
        self.assertEqual(faces_before, self.query("SELECT photo_id, name, name_source, box FROM faces"))
        self.assertEqual(tags_before, self.query("SELECT id, tags FROM photos ORDER BY id"))

    def test_a_row_whose_people_lack_a_name_its_face_gives_is_left_for_refresh_rows(self):
        conn = db.connect(self.db_path)
        try:
            add_face(conn, self.first, [0, 0, 10, 10], name="Rowan Thackeray", name_source="manual")
            conn.commit()
        finally:
            conn.close()
        people_before = self.query("SELECT photo_id, name FROM photo_people ORDER BY photo_id, position")
        result = self.run_it(apply=True)
        self.assertEqual(people_before, self.query("SELECT photo_id, name FROM photo_people ORDER BY photo_id, position"))
        self.assertEqual(1, result.details["counts"]["disagrees"])
        self.assertEqual(2, result.changed)
        self.assertNotIn(fields.READ_GENERATION_KEY, self.raw(self.first))

    def test_tags_captions_and_the_stamp_are_never_replaced_when_the_file_and_the_row_disagree(self):
        self.truth["IMG_0002.jpg"] = dict(self.truth["IMG_0002.jpg"], **{"XMP:Subject": ["Weather/Rain"],
                                                                         "IPTC:ObjectName": "A new caption"})
        before = self.query("SELECT tags, captions, mtime, size, raw_metadata FROM photos WHERE path = ?", (self.second,))
        result = self.run_it(apply=True)
        self.assertEqual(before, self.query("SELECT tags, captions, mtime, size, raw_metadata FROM photos WHERE path = ?",
                                            (self.second,)))
        self.assertEqual(1, result.details["counts"]["disagrees"])
        self.assertEqual({"tags", "captions"}, {"tags", "captions"} & set(result.details["fields"]))
        self.assertEqual(2, result.changed)

    def test_only_the_raw_metadata_of_a_row_that_agrees_is_written(self):
        before = self.query("SELECT tags, captions, mtime, size FROM photos ORDER BY id")
        result = self.run_it(apply=True)
        self.assertEqual(before, self.query("SELECT tags, captions, mtime, size FROM photos ORDER BY id"))
        self.assertEqual(3, result.details["fields"]["raw_metadata"])
        self.assertNotIn("tags", result.details["fields"])

    def test_a_row_never_read_is_not_read(self):
        stub = os.path.join(self.trip, "IMG_0004.jpg")
        with open(stub, "wb") as handle:
            handle.write(b"jpeg")
        conn = db.connect(self.db_path)
        try:
            photo_rows.add_unread(conn, stub)
            conn.commit()
        finally:
            conn.close()
        result = self.run_it(apply=True)
        self.assertNotIn(stub, self.reads.read)
        self.assertEqual(1, result.details["counts"]["never_read"])
        self.assertEqual({}, self.raw(stub))

    def test_a_missing_an_unreadable_and_a_changed_file_are_counted_and_the_rest_are_read(self):
        os.remove(self.second)
        self.truth["IMG_0003.jpg"] = {"ExifTool:Error": "File format error"}
        changed = self.photo(self.trip, "IMG_0005.jpg", **dict(CAMERA, **LENS))
        self.indexed_before(changed)
        with open(changed, "wb") as handle:
            handle.write(b"jpeg, edited elsewhere")
        os.utime(changed, (THEN + 100, THEN + 100))
        before = self.query("SELECT mtime, size, raw_metadata FROM photos WHERE path = ?", (changed,))
        result = self.run_it(apply=True)
        counts = result.details["counts"]
        self.assertEqual((1, 1, 1), (counts["missing"], counts["unreadable"], counts["changed"]))
        self.assertEqual(1, result.changed, "only the photo that could be read was written")
        self.assertEqual([self.id_of(self.first)], self.found("ef24-70"))
        self.assertEqual(before, self.query("SELECT mtime, size, raw_metadata FROM photos WHERE path = ?", (changed,)),
                         "a changed file's row was stamped with the file's new stamp, hiding the change from sync")
        self.assertNotIn(changed, self.reads.read)
        self.assertEqual(3, result.details["remaining"], "the three left are still to be read when they can be")
        self.assertTrue(result.ok, result.message())

    def test_a_file_that_changed_between_looking_and_reading_is_left_for_sync(self):
        real = Reads.batch_read

        def edited_meanwhile(reads, extractor, file_paths, people=None):
            records = real(reads, extractor, file_paths, people)
            os.utime(self.first, (THEN + 500, THEN + 500))
            return [dict(record, mtime=THEN + 500) if record["path"] == self.first else record for record in records]

        with mock.patch.object(Reads, "batch_read", edited_meanwhile):
            result = self.run_it(apply=True)
        self.assertEqual(2, result.changed)
        self.assertEqual(1, result.details["counts"]["changed_while_read"])
        self.assertNotIn(fields.READ_GENERATION_KEY, self.raw(self.first))

    def test_a_row_saved_in_the_app_while_the_files_were_read_is_left_for_the_next_run(self):
        real = Reads.batch_read

        def saved_meanwhile(reads, extractor, file_paths, people=None):
            records = real(reads, extractor, file_paths, people)
            conn = db.connect(self.db_path)
            conn.execute("UPDATE photos SET tags = ? WHERE path = ?", (json.dumps(["Weather/Rain"]), self.second))
            conn.commit()
            conn.close()
            return records

        with mock.patch.object(Reads, "batch_read", saved_meanwhile):
            result = self.run_it(apply=True)
        self.assertEqual(2, result.changed)
        self.assertEqual(1, len(result.skipped))
        self.assertEqual(["Weather/Rain"], json.loads(self.query("SELECT tags FROM photos WHERE path = ?", (self.second,))[0][0]))
        again = self.run_it(apply=True)
        self.assertEqual([self.second], self.reads.read, "the skipped row is read by the next run, and no other")
        # Its tags are the app's save now, and differ from the file's: left to sync and refresh_rows, never overwritten.
        self.assertEqual(0, again.changed)
        self.assertEqual(1, again.details["counts"]["disagrees"])
        self.assertEqual(["Weather/Rain"], json.loads(self.query("SELECT tags FROM photos WHERE path = ?", (self.second,))[0][0]))

    def many_unreadable(self):
        many = [self.photo(self.trip, "IMG_%04d.jpg" % number, **CAMERA) for number in range(10, 24)]
        self.indexed_before(*many)
        for path in many + list(self.all):
            self.truth[os.path.basename(path)] = {}
        return many

    def test_exiftool_that_cannot_start_stops_a_run_that_has_read_nothing(self):
        self.many_unreadable()
        with mock.patch.object(reread_fields.metadata, "exiftool_starts", return_value=(False, "not found")):
            result = self.run_it(apply=True, chunk=5)
        self.assertFalse(result.ok)
        self.assertIn("cannot be started", result.message())
        self.assertEqual(0, result.changed)
        self.assertEqual(10, len(self.reads.read), "it went on through the rest after the files read none: two chunks of 5")

    def test_files_that_are_only_unreadable_are_counted_and_the_run_does_not_fail(self):
        self.many_unreadable()
        with mock.patch.object(reread_fields.metadata, "exiftool_starts", return_value=(True, None)):
            result = self.run_it(apply=True, chunk=14)
        self.assertTrue(result.ok, result.message())
        self.assertEqual(17, result.details["counts"]["unreadable"])
        self.assertEqual(0, result.changed)

    def test_a_row_found_damaged_before_is_not_read_again(self):
        damaged = self.photo(self.trip, "IMG_0006.jpg", **CAMERA)
        conn = db.connect(self.db_path)
        try:
            record = photo_rows.as_read(damaged, {"ExifTool:Error": "File format error"})
            store_photos.record_indexed(conn, damaged, record)
            conn.commit()
        finally:
            conn.close()
        result = self.run_it(apply=True)
        self.assertNotIn(damaged, self.reads.read)
        self.assertEqual(1, result.details["counts"]["damaged"])
        self.assertTrue(result.ok)

    def test_a_place_gone_before_the_run_is_said_and_nothing_is_read(self):
        self.many_unreadable()
        for path in self.all:
            os.remove(path)
        for number in range(10, 24):
            os.remove(os.path.join(self.trip, "IMG_%04d.jpg" % number))
        result = self.run_it(apply=True)
        self.assertFalse(result.ok)
        self.assertIn("unplugged", result.message())
        self.assertEqual([], self.reads.read)

    def test_a_place_that_goes_while_the_run_reads_stops_it_after_that_chunk(self):
        many = self.many_unreadable()
        everything = many + list(self.all)
        calls = []

        def unplugged(reads, extractor, file_paths, people=None):
            calls.append(list(file_paths))
            for path in everything:
                if os.path.exists(path):
                    os.remove(path)
            return [metadata.MetadataExtractor._empty(path, "gone") for path in file_paths]

        with mock.patch.object(Reads, "batch_read", unplugged):
            result = self.run_it(apply=True, chunk=10)
        self.assertEqual(1, len(calls), "the other chunks were read as well")
        self.assertFalse(result.ok)
        self.assertIn("unplugged", result.message())


class ASharePartWay(Rereads):
    def test_a_share_that_stops_answering_part_way_leaves_the_rest_of_its_rows(self):
        answers = []

        def drops_after_planning(location, call, seconds, away_seconds=None):
            answers.append(location)
            return ("ok", call()) if len(answers) <= 3 else ("away", None)

        def unreadable_now(reads, extractor, file_paths, people=None):
            reads.read.extend(file_paths)
            return [metadata.MetadataExtractor._empty(path, "timed out") for path in file_paths]

        with mock.patch.object(reread_fields.shares, "on_a_network_drive", return_value=True), \
                mock.patch.object(reread_fields.shares, "bounded", side_effect=drops_after_planning), \
                mock.patch.object(Reads, "batch_read", unreadable_now):
            result = self.run_it(apply=True, shares_named=True, chunk=1)
        self.assertEqual(1, len(self.reads.read), "after the first chunk's file did not answer, no other was read")
        self.assertEqual(3, result.details["counts"]["share_away"])
        self.assertEqual(4, len(answers), "three looks to plan, one to ask whether the unreadable file was there")
        self.assertEqual(0, result.changed)


class ADottedPlaceholderIsFound(Rereads):
    def test_a_lens_named_by_its_range_with_pointless_zeros_is_found_by_the_range(self):
        placeholder = self.photo(self.trip, "IMG_0030.jpg", **dict(CAMERA, **{"EXIF:LensModel": "24.0-70.0 mm"}))
        self.indexed_before(placeholder)
        self.run_it(apply=True)
        self.assertIn(self.id_of(placeholder), self.found("24-70"))
        self.assertIn(self.id_of(placeholder), self.found("24.0-70.0"))
        self.assertNotIn(self.id_of(placeholder), self.found("f/2.8"), "an aperture is not a range")


class ResumingAndScope(Rereads):
    def test_an_interrupted_run_keeps_its_chunks_and_the_next_takes_up_the_rest(self):
        self.indexed_before(*[self.photo(self.trip, "IMG_%04d.jpg" % number, **dict(CAMERA, **LENS)) for number in (7, 8)])
        real = Reads.batch_read
        calls = []

        def stops_at_the_second_chunk(reads, extractor, file_paths, people=None):
            calls.append(list(file_paths))
            if len(calls) == 2:
                raise KeyboardInterrupt()
            return real(reads, extractor, file_paths, people)

        with mock.patch.object(Reads, "batch_read", stops_at_the_second_chunk):
            with self.assertRaises(KeyboardInterrupt):
                self.run_it(apply=True, chunk=2)
        done = self.query("SELECT COUNT(*) FROM photos WHERE raw_metadata LIKE '%ReadGeneration%'")[0][0]
        self.assertEqual(2, done, "the chunk written before the stop is kept, the one being read is not")
        self.assertEqual(1, len([c for c in journal.history(self.db_path) if c["operation"] == "reread_fields"]))
        result = self.run_it(apply=True, chunk=2)
        self.assertEqual(3, result.changed)
        self.assertEqual(3, len(self.reads.read), "only the rows not yet written are read")
        self.assertEqual(0, result.details["remaining"])
        self.assertEqual(3, len([c for c in journal.history(self.db_path) if c["operation"] == "reread_fields"]),
                         "one change a chunk written: 2, then 2 and 1")

    def test_a_folder_limits_it_and_a_folder_with_no_photo_is_refused(self):
        result = self.run_it(apply=True, folder=self.trip)
        self.assertEqual([self.third], self.reads.read)
        self.assertEqual(1, result.changed)
        self.assertNotIn(fields.READ_GENERATION_KEY, self.raw(self.first))
        nowhere = os.path.join(self.pictures, "2020 Nowhere")
        refused = self.run_it(folder=nowhere)
        self.assertIsNotNone(refused.refused)
        self.assertIn("holds no photo", refused.refused)

    def test_a_folder_beginning_as_another_is_not_it(self):
        side = os.path.join(self.pictures, "2025-11 Harbour2")
        os.makedirs(side)
        other = self.photo(side, "IMG_0009.jpg", **CAMERA)
        self.indexed_before(other)
        self.run_it(apply=True, folder=self.trip)
        self.assertNotIn(other, self.reads.read)


class NoShareIsTouchedUnnamed(Rereads):
    def test_photos_on_a_share_nobody_named_are_counted_and_not_read_or_looked_at(self):
        with mock.patch.object(reread_fields.shares, "on_a_network_drive", return_value=True), \
                mock.patch.object(reread_fields.shares, "bounded", side_effect=AssertionError("looked at a share")):
            result = self.run_it(apply=True)
        self.assertEqual([], self.reads.read)
        self.assertEqual(3, result.details["counts"]["on_shares"])
        self.assertEqual(0, result.changed)

    def test_naming_the_folder_or_the_shares_allows_them(self):
        with mock.patch.object(reread_fields.shares, "on_a_network_drive", return_value=True):
            by_folder = self.run_it(apply=True, folder=self.trip)
            by_flag = self.run_it(apply=True, shares_named=True)
        self.assertEqual(1, by_folder.changed)
        self.assertEqual(2, by_flag.changed)

    def test_a_share_that_does_not_answer_is_left_and_said(self):
        with mock.patch.object(reread_fields.shares, "on_a_network_drive", return_value=True), \
                mock.patch.object(reread_fields.shares, "bounded", return_value=("away", None)):
            result = self.run_it(apply=True, shares_named=True)
        self.assertEqual([], self.reads.read)
        self.assertEqual(3, result.details["counts"]["share_away"])


class TheCommand(Rereads):
    def invoke(self, *arguments):
        from click.testing import CliRunner

        import tagpup_cli
        self.reads = Reads(self.truth)
        with mock.patch("tagpup.files.metadata.MetadataExtractor.batch_read", autospec=True,
                        side_effect=self.reads.batch_read):
            return CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "reread-fields", *arguments])

    def test_a_dry_run_says_what_it_would_do_and_how_long_and_changes_nothing(self):
        said = self.invoke()
        self.assertEqual(0, said.exit_code, said.output)
        self.assertIn("3 photo(s) to read again.", said.output)
        self.assertIn("Timed 3 file(s)", said.output)
        self.assertIn("Nothing changed. --apply", said.output)
        self.assertEqual([], self.found("ef24-70"))

    def test_apply_writes_and_says_what_is_left(self):
        said = self.invoke("--apply")
        self.assertEqual(0, said.exit_code, said.output)
        self.assertIn("Wrote 3 row(s) in 1 change(s)", said.output)
        self.assertIn("Still to read: 0 photo(s).", said.output)
        self.assertEqual([self.id_of(self.first)], self.found("ef24-70"))

    def test_a_folder_with_no_photo_is_refused_with_a_sentence_and_a_failing_exit(self):
        said = self.invoke("--folder", os.path.join(self.pictures, "Nowhere"))
        self.assertEqual(1, said.exit_code)
        self.assertIn("holds no photo", said.output)


class ASaveKeepsTheGeneration(unittest.TestCase):
    def test_the_row_a_save_records_is_a_full_read(self):
        class Session:
            def get_tags(self, batch, tags=None):
                return [{"SourceFile": batch[0], "EXIF:LensModel": "EF24-70mm f/2.8L II USM"}]

        class Failed:
            def get_tags(self, batch, tags=None):
                return [{"SourceFile": batch[0], "ExifTool:Error": "File format error"}]

        class Empty:
            def get_tags(self, batch, tags=None):
                return []

        for session in (Failed(), Empty()):
            self.assertNotIn(fields.READ_GENERATION_KEY, metadata.raw_metadata(session, "x.jpg"), "a failed read-back is not a read")
        self.assertNotIn(fields.READ_GENERATION_KEY, metadata.raw_metadata(None, "x.jpg", {"SourceFile": "x.jpg", "Error": "e"}))
        raw = metadata.raw_metadata(Session(), "x.jpg")
        self.assertEqual(fields.READ_GENERATION, raw[fields.READ_GENERATION_KEY])
        from_record = metadata.raw_metadata(None, "x.jpg", {"SourceFile": "x.jpg", "EXIF:LensModel": "A", "EXIF:Software": "s"})
        self.assertEqual(fields.READ_GENERATION, from_record[fields.READ_GENERATION_KEY])
        self.assertNotIn("EXIF:Software", from_record)


class TheReadRecordsItsGeneration(unittest.TestCase):
    def test_the_lens_fields_are_asked_for_and_each_read_says_it_asked(self):
        for name in ("EXIF:LensModel", "LensModel", "XMP:LensModel", "EXIF:LensMake", "LensMake", "Composite:LensID", "LensID"):
            self.assertIn(name, fields.METADATA_FIELDS)
        raw = photo_rows.as_read("x.jpg", {"EXIF:LensModel": "EF24-70mm f/2.8L II USM"})["raw_metadata"]
        self.assertEqual(fields.READ_GENERATION, raw[fields.READ_GENERATION_KEY])
        self.assertEqual("EF24-70mm f/2.8L II USM", photo_meta.gear(raw).lens)

    def test_an_empty_answer_and_an_error_are_not_a_read(self):
        extractor = metadata.MetadataExtractor()
        self.assertEqual({}, extractor._structure("x.jpg", {}, None)["raw_metadata"])
        failed = extractor._structure("x.jpg", {"SourceFile": "x.jpg", "ExifTool:Error": "File format error"}, None)
        self.assertNotIn(fields.READ_GENERATION_KEY, failed["raw_metadata"])

    def test_generations(self):
        self.assertEqual(0, fields.read_generation({}))
        self.assertEqual(0, fields.read_generation({"XMP:Subject": ["a"]}), "only what writes recorded: never read")
        self.assertEqual(1, fields.read_generation({"SourceFile": "x", "XMP:Subject": ["a"], "Subject": ["a"]}))
        self.assertEqual(2, fields.read_generation({"SourceFile": "x", fields.READ_GENERATION_KEY: 2}))
        self.assertEqual(1, fields.read_generation({"SourceFile": "x", fields.READ_GENERATION_KEY: "2"}))
        self.assertEqual(0, fields.read_generation("not a dict"))


if __name__ == "__main__":
    unittest.main()
