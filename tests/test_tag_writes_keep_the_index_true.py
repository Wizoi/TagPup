"""Every write of a photo's keywords leaves its index row saying what the file says.

Three ways it did not:

* The row recorded XMP:Subject and XMP:HierarchicalSubject but not IPTC:Keywords, which
  the file also carries and metadata.extract_tags also reads. A tag removed in bulk
  stayed in raw_metadata, and came back the next time anything re-derived tags from
  it -- renaming a different tag on the same photo, for one.
* The row kept the file's old mtime and size. Writing keywords changes both, and the
  folder scan only trusts a row whose mtime and size match the file, so every photo
  tagged in bulk was re-read with ExifTool on every scan afterwards.
* TagTuner's bulk writers wrote the files and never told the index at all.

And the copy it reads: XMP's when the file holds keywords or a caption there, IPTC's only when XMP holds none
(DECISIONS 2026-11-03, XMP IS THE COPY THE INDEX READS; fields.KEYWORD_COPIES, CAPTION_COPIES), on real JPEGs
written by the real ExifTool (XmpIsTheCopyRead). Reading the union of both copies fails
test_a_keyword_left_only_in_iptc_is_no_tag_and_a_rename_does_not_bring_it_back and
test_the_caption_shown_is_xmps_and_a_save_from_it_is_not_refused; preferring an XMP packet over IPTC whatever it holds
fails test_a_photo_with_iptc_keywords_and_no_xmp_keywords_keeps_its_tags.

And a rename of a tag starts from the file, not the row (#30), and stops at a photo it cannot write, the photo keeping
its tag in its file and its row (XmpIsTheCopyRead). Lifted: test_service_replace_tag and test_service_change_tags,
whose stand-in ExifTool restated tagging.replace_tag and change_tags; their other cases are held through the routes
by tests/test_bulk_writes_start_from_the_file.py (a bulk add, a remove, Apply All, the run stopped at a photo ExifTool
refuses, #878), tests/test_taxonomy_lifecycle.py (a tag removed from the tree comes off its photos) and
tests/test_taxonomy_edits_report_what_they_wrote.py (a rename or delete that could not rewrite a photo keeps the node).

Rows are seeded the way the indexer writes them -- native paths, and raw_metadata in
the shape real rows have, bare aliases and all -- never through the code under test.
ExifTool is replaced by a stand-in that changes the file the way a real write does.
"""
import contextlib
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import exiftool_fields  # noqa: E402
import photo_rows  # noqa: E402
from handler_harness import Library  # noqa: E402
from test_file_journal import EXIFTOOL, FilesCase, field_of, keywords_of, requires_exiftool, write_outside  # noqa: E402
from test_a_file_holds_what_exiftool_kept import rewritten  # noqa: E402
import page_writes  # noqa: E402

from tagpup.core.fields import keyword_fields  # noqa: E402
from tagpup.core.vocabulary import extract_tags  # noqa: E402
from tagpup.files import exiftool_session  # noqa: E402
from tagpup.services import photos as photo_actions, tagging  # noqa: E402
from tagpup.store import db as tagpup_db  # noqa: E402

KEYWORDS = ["Beach", "Cross Country", "People/Rowan Thackeray"]
HIERARCHICAL = ["People/Rowan Thackeray"]


def raw_metadata_as_scanned(keywords, hierarchical):
    """raw_metadata the way the scan stores it: every keyword field, and the bare
    aliases ExifTool reports beside the grouped names."""
    return {
        "IPTC:Keywords": list(keywords),
        "Keywords": list(keywords),
        "XMP:Subject": list(keywords),
        "Subject": list(keywords),
        "XMP:HierarchicalSubject": list(hierarchical),
        "HierarchicalSubject": list(hierarchical),
    }


def exiftool_that_writes():
    """ExifToolHelper stand-in whose writes change the file the way a real one does:
    new bytes, so a new size, and a new mtime. Returns (helper class, et).

    It reads back what it holds, too: the bulk writers start from the keywords in the
    file. A file it has not written holds what photo() seeds by default."""
    et = MagicMock()
    held = {}

    def touch(path):
        with open(path, "ab") as f:
            f.write(b" keywords rewritten")
        st = os.stat(path)
        os.utime(path, (st.st_atime, st.st_mtime + 120))

    def fields(path):
        scanned = raw_metadata_as_scanned(KEYWORDS, HIERARCHICAL)
        return held.setdefault(path, {k: v for k, v in scanned.items() if ":" in k})

    def set_tags(files, tags=None, params=None):
        for p in files:
            fields(p).update({k: v for k, v in (tags or {}).items() if v not in ([], "")})
            # A field cleared in the same command (docs/findings.md, #271).
            for param in params or []:
                if param.startswith("-") and param.endswith("="):
                    fields(p).pop(param[1:-1], None)
            touch(p)

    def execute(*args):
        for arg in args:
            if arg.startswith("-") and arg.endswith("="):
                fields(args[-1]).pop(arg[1:-1], None)
        touch(args[-1])

    et.set_tags.side_effect = set_tags
    et.execute.side_effect = execute
    et.get_tags.side_effect = lambda files, tags=None, params=None: [
        dict(fields(p), SourceFile=p) for p in files]
    helper = MagicMock()
    helper.return_value.__enter__.return_value = et
    helper.return_value.__exit__.return_value = False
    return helper, et


class IndexCase(unittest.TestCase):
    def setUp(self):
        self.lib = Library(self, "tag_writes")
        self.folder = os.path.join(self.lib.root, "Meet Photos")
        os.makedirs(self.folder)
        self.db_path = self.lib.db_path
        self.library = self.lib.library

    def photo(self, name="IMG_0001.jpg", keywords=KEYWORDS, hierarchical=HIERARCHICAL):
        """A file on disk and its index row, as the indexer leaves them."""
        path = os.path.join(self.folder, name)
        with open(path, "wb") as f:
            f.write(b"not really a jpeg")
        stored = os.path.abspath(path)
        st = os.stat(stored)
        conn = tagpup_db.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)"
                " VALUES (?, ?, ?, ?, '[]', ?)",
                (stored, st.st_mtime, st.st_size, json.dumps(list(keywords)),
                 json.dumps(raw_metadata_as_scanned(keywords, hierarchical))))
            conn.commit()
        finally:
            conn.close()
        return stored

    def row(self, stored):
        conn = tagpup_db.connect(self.db_path)
        try:
            found = conn.execute(
                "SELECT tags, raw_metadata, mtime, size FROM photos WHERE path = ?",
                (stored,)).fetchone()
        finally:
            conn.close()
        return {"tags": json.loads(found[0] or "[]"), "raw": json.loads(found[1] or "{}"),
                "mtime": found[2], "size": found[3]}

    def assertRowMatchesTheFile(self, stored):
        row, st = self.row(stored), os.stat(stored)
        self.assertEqual(row["size"], st.st_size, "the row kept the file's old size")
        self.assertAlmostEqual(row["mtime"], st.st_mtime, delta=0.05,
                               msg="the row kept the file's old mtime, so every scan re-reads it")

    def call(self, body):
        """Ask the one entry point as the page would; a write that did not succeed fails the test."""
        status, reply = self.lib.post(page_writes.ROUTE, body)
        self.assertEqual((200, True), (status, reply.get("success")), reply)
        return reply


class TestARemovedTagStaysRemoved(IndexCase):
    def test_a_bulk_remove_survives_renaming_another_tag(self):
        stored = self.photo()
        helper, _ = exiftool_that_writes()
        with patch("tagpup.files.exiftool_session.ExifToolSession", helper):
            self.call(page_writes.tags({stored: {"remove": ["Cross Country"]}}))
            self.assertNotIn("Cross Country", self.row(stored)["tags"])

            # Renaming "Beach" re-derives this photo's tags from its raw_metadata.
            recorded = tagging.replace_tag(self.library, [stored], "Beach", "Places/Beach", exiftool_fields.opened(self, "exiftool")).changed

        tags = self.row(stored)["tags"]
        self.assertIn("Places/Beach", tags)
        self.assertNotIn("Cross Country", tags, "the renamed photo got its removed tag back")
        self.assertEqual(recorded, 1)

    def test_the_writer_and_the_recorder_name_the_same_fields(self):
        # One list, two consumers: whatever the journaled keyword write sets is what the index is
        # told, so a field added to one cannot be missing from the other.
        planned = tagging._keywords_plan(["Beach"]).after
        self.assertEqual(set(planned), set(keyword_fields(["Beach"], [])))
        self.assertEqual(planned["XMP:HierarchicalSubject"], [])


class TestTheRowKeepsTheFilesNewStat(IndexCase):
    def test_bulk_tagging(self):
        stored = self.photo()
        helper, _ = exiftool_that_writes()
        with patch("tagpup.files.exiftool_session.ExifToolSession", helper):
            self.call(page_writes.tags({stored: {"add": ["Relay"]}}))
        self.assertRowMatchesTheFile(stored)

    def test_renaming_a_tag(self):
        stored = self.photo()
        helper, _ = exiftool_that_writes()
        with patch("tagpup.files.exiftool_session.ExifToolSession", helper):
            tagging.replace_tag(self.library, [stored], "Beach", "Places/Beach", exiftool_fields.opened(self, "exiftool"))
        self.assertRowMatchesTheFile(stored)


class TestBulkWritersTellTheIndex(IndexCase):
    # These ran against TagTuner's copies of the two routes, which its page never
    # called and which are gone; the same checks now hold TagPup's to them.
    def test_bulk_tags(self):
        stored = self.photo()
        helper, _ = exiftool_that_writes()
        with patch("tagpup.files.exiftool_session.ExifToolSession", helper):
            self.call(page_writes.tags({stored: {"add": ["Relay"], "remove": ["Cross Country"]}}))

        row = self.row(stored)
        # A cold folder cache starts from the file; it used to start from nothing and
        # write only the added tag, erasing the rest.
        self.assertEqual(sorted(row["tags"]), ["Beach", "People/Rowan Thackeray", "Relay"])
        self.assertNotIn("Cross Country", extract_tags(row["raw"]))
        self.assertRowMatchesTheFile(stored)

    def test_save_metadata_records_every_field_and_the_new_stat(self):
        stored = self.photo()
        helper, _ = exiftool_that_writes()
        with patch("tagpup.files.exiftool_session.ExifToolSession", helper), \
                patch("tagpup.files.metadata.title_filename", side_effect=lambda p, *rest: p):
            self.call(page_writes.save(self.library, stored, title="", tags=["Beach"]))

        row = self.row(stored)
        self.assertEqual(extract_tags(row["raw"]), ["Beach"])
        self.assertRowMatchesTheFile(stored)

    def test_no_tuner_handler_writes_keyword_fields_its_own_way(self):
        # The guard. Each of these built its own ExifTool parameters, and each went
        # wrong in its own way; the journaled write is the one writer.
        for relative in (os.path.join("tagpup", "web", "tuner_routes.py"), os.path.join("tagpup", "services", "faces.py")):
            with open(os.path.join(WORKSPACE_DIR, relative), encoding="utf-8") as f:
                source = f.read()
            for field in ('["XMP:Subject"]', '["IPTC:Keywords"]', '["XMP:HierarchicalSubject"]'):
                self.assertFalse("params" + field in source,
                                 "%s writes %s itself instead of through the journaled write" % (relative, field))


@requires_exiftool
class XmpIsTheCopyRead(FilesCase):
    def made(self, name, written, read=None):
        """A JPEG another program wrote `written` into, and its row as the indexer records a read of it answering `read`
        (the same fields, under the keys ExifTool answers them by, unless given)."""
        from PIL import Image

        path = os.path.join(self.folder, name)
        Image.new("RGB", (16, 12), (90, 110, 130)).save(path, "JPEG")
        write_outside(path, written)
        conn = tagpup_db.connect(self.library.path)
        try:
            photo_rows.add_read(conn, path, written if read is None else read)
            conn.commit()
        finally:
            conn.close()
        return path

    def captions(self, path):
        return json.loads(self.rows("SELECT captions FROM photos WHERE path = ?", (path,))[0][0])

    def shown(self, path):
        """The title the page shows of the photo, from its row as the library view hands it to page_record."""
        captions, raw = self.rows("SELECT captions, raw_metadata FROM photos WHERE path = ?", (path,))[0]
        return photo_actions.page_record(path, {"captions": json.loads(captions or "[]"),
                                                "raw_metadata": json.loads(raw or "{}")})["title"]

    def test_a_keyword_left_only_in_iptc_is_no_tag_and_a_rename_does_not_bring_it_back(self):
        # What a save that patches XMP alone leaves when it takes a keyword off: IPTC still holds it. By either writer
        # of the rename: the own writer's patch writes XMP alone, IPTC keeping what it held, the old name with it
        # (fields.XMP_ONLY; DECISIONS 2026-11-03); ExifTool's rewrite writes every copy from XMP's.
        for patched in (True, False):
            with self.subTest(patched=patched), contextlib.nullcontext() if patched else rewritten():
                a = self.made("patched.jpg" if patched else "rewritten.jpg", {
                    "XMP:Subject": ["Beach"], "IPTC:Keywords": ["Beach", "Cross Country"],
                    "EXIF:XPKeywords": "Beach;Cross Country"})
                self.assertEqual(["Beach"], self.indexed(a))
                result = tagging.replace_tag(self.library, [a], "Beach", "Shore", exiftool_fields.opened(self, EXIFTOOL))
                self.assertEqual((1, []), (result.changed, result.errors))
                iptc = ["Beach", "Cross Country"] if patched else "Shore"
                self.assertEqual((["Shore"], ["Shore"], iptc),
                                 (self.indexed(a), keywords_of(a), field_of(a, "IPTC:Keywords")))

    def test_the_caption_shown_is_xmps_and_a_save_from_it_is_not_refused(self):
        a = self.made("a.jpg", {"XMP:Description": "Relay at the lake", "IPTC:Caption-Abstract": "Relay at dusk"})
        self.assertEqual(["Relay at the lake"], self.captions(a))
        shown = self.shown(a)
        self.assertEqual("Relay at the lake", shown)
        result = tagging.save_photo(self.library, a, "Relay at the lake, later", [], None, exiftool_fields.opened(self, EXIFTOOL), "",
                                    base={"tags": [], "title": shown})
        # The title is saved to the title fields; the Description it showed leaves the caption, and IPTC's caption,
        # another text, is kept (DECISIONS 2026-11-06; #2583).
        self.assertEqual((True, "Relay at the lake, later", None, "Relay at dusk"),
                         (result.ok, field_of(a, "XMP:Title"), field_of(a, "XMP:Description"),
                          field_of(a, "IPTC:Caption-Abstract")))
        # A blank XMP description holds none: 12,469 photo_index rows hold one beside an IPTC caption (counted
        # 2026-11-03), and show the IPTC caption.
        b = self.made("b.jpg", {"IPTC:Caption-Abstract": "Relay at dusk"},
                      {"XMP:Description": "", "IPTC:Caption-Abstract": "Relay at dusk"})
        self.assertEqual(["Relay at dusk"], self.captions(b))

    def test_a_rename_starts_from_what_the_file_holds_not_the_row(self):
        # #30: the write started from the index's copy of the tags. A keyword another program wrote since the read is
        # kept; a tag another program took off is not put back, its file not written, and its row says what the file holds.
        a = self.made("a.jpg", {"XMP:Subject": ["Beach", "Written Elsewhere"]}, {"XMP:Subject": ["Beach"]})
        b = self.made("b.jpg", {"XMP:Subject": ["Relay"]}, {"XMP:Subject": ["Beach", "Relay"]})
        before = os.stat(b).st_mtime_ns
        result = tagging.replace_tag(self.library, [a, b], "Beach", "Shore", exiftool_fields.opened(self, EXIFTOOL))
        self.assertEqual((1, [], ["does not carry the tag"]),
                         (result.changed, result.errors, [why for _path, why in result.skipped]))
        self.assertEqual((["Shore", "Written Elsewhere"],) * 2, (keywords_of(a), self.indexed(a)))
        self.assertEqual((["Relay"], ["Relay"], before), (keywords_of(b), self.indexed(b), os.stat(b).st_mtime_ns))

    def test_a_photo_a_rename_cannot_write_is_an_error_and_keeps_its_tag_in_its_file_and_row(self):
        # ExifTool's rewrite (a save the own writer refuses or whose change does not fit) refuses the write for a
        # scratch file in its way, which another write may own (file_changes.removed_scratch); a patch is not made
        # while one is there, so that rewrite's os.replace cannot land over it.
        a = self.made("a.jpg", {"XMP:Subject": ["Beach"]})
        with open(exiftool_session.scratch_of(a), "wb") as handle:
            handle.write(b"left by another write")
        result = tagging.replace_tag(self.library, [a], "Beach", "Shore", exiftool_fields.opened(self, EXIFTOOL))
        self.assertEqual((0, 1), (result.changed, len(result.errors)))
        self.assertEqual((["Beach"], ["Beach"]), (keywords_of(a), self.indexed(a)))

    def test_a_photo_with_iptc_keywords_and_no_xmp_keywords_keeps_its_tags(self):
        # 151 photo_index photos hold IPTC keywords and no XMP ones, 4 of them in a file with an XMP packet (2026-11-03).
        a = self.made("a.jpg", {"IPTC:Keywords": ["Beach", "Relay"], "XMP-xmpMM:DocumentID": "xmp.did:0001"},
                      {"IPTC:Keywords": ["Beach", "Relay"], "XMP:DocumentID": "xmp.did:0001"})
        self.assertEqual(["Beach", "Relay"], self.indexed(a))
        self.assertEqual("xmp.did:0001", field_of(a, "XMP-xmpMM:DocumentID"))


if __name__ == "__main__":
    unittest.main()
