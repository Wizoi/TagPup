"""A photo's title and its caption are two values (scenario F1; DECISIONS 2026-11-06, THE TITLE AND THE CAPTION ARE TWO
VALUES).

The owner types a title in the photo panel: it is saved to the title fields and never to the Description fields, which
hold the photo's caption, its extended information. A save is the own writer's patch in place (4d of
docs/PLAN_OWN_METADATA.md): a title saved writes XMP:Title alone and leaves IPTC:ObjectName as it was, the copy the
reader takes only while XMP holds none (DECISIONS 2026-11-03, A KEYWORD OR CAPTION SAVE WRITES THE XMP COPY ONLY;
fields.XMP_ONLY); a title or caption cleared writes every copy (#2433), and an undo writing a caption back writes its XMP
copy alone, IPTC's left cleared (docs/domains/files.md, "Known, by this design"). A photo with a Description and no title
shows the Description as its title, and the first save moves it into the title, leaving no copy behind; a caption that
differs from the title is never written. A cleared title is cleared, and the next save is accepted (#2581: a photo
titled only in its title fields was refused as changed on disk after its title was cleared). Undo puts the old title
back and writes no caption; Smart Rename names a photo by its title; the page and the index show the same title.
Each fails on the trunk before this change (the save wrote the Description fields and showed the Description first).
The page's title is the file's by that one rule whatever order the row stores its captions in (#2582).
Real ExifTool on JPEGs made here; rows as the indexer makes them (photo_rows.add_read); fictional names.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_stale_record_precondition as S  # noqa: E402

from tagpup.core import fields  # noqa: E402

TITLE_FIELDS = tuple(fields.title_fields(""))
CAPTION_FIELDS = tuple(fields.caption_fields(""))


def title_held(title, iptc=""):
    """The title fields a save of `title` leaves: XMP:Title written, IPTC:ObjectName holding what it held (`iptc`)."""
    return {"XMP:Title": title, "IPTC:ObjectName": iptc}


@unittest.skipIf(S.EXIFTOOL is None, "ExifTool not installed")
class ATitleAndACaption(S.Fresh):
    def made(self, name, seed, held, tags=("Trips/Coast",)):
        """A photo whose file holds `tags` and the fields `held` ({field: text}), and the row the indexer makes of it."""
        path = os.path.join(self.folder, name)
        S.damaged_photos.whole_jpeg(path, seed=seed)
        S.write_into(path, Subject=list(tags), **held)
        conn = S.db.connect(self.library.path)
        try:
            read = {field: value for field, value in S.in_file(path, *held).items() if field in held}
            photo_id = S.photo_rows.add_read(conn, path, {"XMP:Subject": list(tags), **read})
            conn.commit()
        finally:
            conn.close()
        return path, photo_id

    def held(self, path):
        """{field: the text the file holds, "" for none} of every title and caption field."""
        found = S.in_file(path, *TITLE_FIELDS, *CAPTION_FIELDS)
        return {field: str((fields.field_values(found.get(field)) or [""])[0]) for field in TITLE_FIELDS + CAPTION_FIELDS}

    def shown_by_the_index(self, photo_id):
        conn = S.db.connect(S.db.readonly_uri(self.library.path), uri=True)
        try:
            captions, = conn.execute("SELECT captions FROM photos WHERE id = ?", (photo_id,)).fetchone()
        finally:
            conn.close()
        return (json.loads(captions or "[]") or [""])[0]

    def saved(self, reply):
        """The save succeeded, and the record the page goes on with after its reply (edits.takeStamp): the reply as the
        page reads it (page_writes.saved), and its photo's stamp and base."""
        body = self.assertSaved(reply)
        return body, {"stamp": {"mtime": body["mtime"], "size": body["size"]}, "base": body["base"]}

    def test_a_title_is_saved_to_the_title_fields_and_a_caption_that_differs_is_never_written(self):
        caption = {field: "The whole crew rowing out past the harbour wall" for field in CAPTION_FIELDS}
        path, photo_id = self.made("a.jpg", 61, {"XMP:Title": "Pier at noon", "IPTC:ObjectName": "Pier at noon", **caption})
        record = self.record(photo_id)
        self.assertEqual("Pier at noon", record["title"], "the title is shown, not the caption")
        body, after = self.saved(self.save(record, title="Pier at dusk", tags=record["tags"] + ["Trips/Lakes"]))
        self.assertEqual({**title_held("Pier at dusk", iptc="Pier at noon"), **caption}, self.held(path))
        self.assertEqual("Pier at dusk", body["base"]["title"])
        # Two saves in a row: the second goes from the first's reply, and is accepted.
        self.saved(self.save(record, path=body["new_path"], title="Pier at dusk",
                             tags=record["tags"] + ["Trips/Lakes", "Activity/Rowing"], **after))
        self.assertEqual({**title_held("Pier at dusk", iptc="Pier at noon"), **caption}, self.held(path))
        again = self.record(photo_id)
        self.assertEqual(("Pier at dusk", "Pier at dusk"), (again["title"], self.shown_by_the_index(photo_id)),
                         "the page and the index show the same title")

    def test_a_photo_with_a_title_keeps_a_description_equal_to_it_when_its_title_is_saved_or_changed(self):
        """#2596: a photo with a title of its own keeps its Description, even one equal to the title (25 photo_index and
        51 kr-track photos, counted 2026-11-08): the save is not the sweep. With the guard (fields.holds_title in
        tagging._caption_moved) taken out, the Description was cleared as if it were shown as the title."""
        caption = {field: "Pier at noon" for field in CAPTION_FIELDS}
        titled = {"XMP:Title": "Pier at noon", "IPTC:ObjectName": "Pier at noon"}
        path, photo_id = self.made("a.jpg", 69, {**titled, **caption})
        record = self.record(photo_id)
        body, after = self.saved(self.save(record, tags=["Trips/Coast", "Trips/Lakes"]))
        self.assertEqual({**titled, **caption}, self.held(path), "the same title saved with a tag")
        self.saved(self.save(record, path=body["new_path"], title="Pier at dusk", tags=["Trips/Coast", "Trips/Lakes"],
                             **after))
        self.assertEqual({**title_held("Pier at dusk", iptc="Pier at noon"), **caption}, self.held(path),
                         "a changed title")

    def test_a_description_without_a_title_is_shown_as_the_title_and_the_first_save_moves_it(self):
        moved = {"XMP:Description": "Harbour at dawn", "IPTC:Caption-Abstract": "Harbour at dawn",
                 "EXIF:ImageDescription": "Harbour at dawn"}
        path, photo_id = self.made("a.jpg", 62, {**moved, "EXIF:XPComment": "Taken from the end of the pier"})
        record = self.record(photo_id)
        self.assertEqual("Harbour at dawn", record["title"])
        body, after = self.saved(self.save(record, tags=record["tags"] + ["Trips/Lakes"]))
        self.assertEqual({**title_held("Harbour at dawn"), **{field: "" for field in moved},
                          "EXIF:XPComment": "Taken from the end of the pier"}, self.held(path),
                         "moved into the title, no copy left in the caption; a caption that differs is kept")
        self.assertEqual(("Harbour at dawn", "Harbour at dawn"), (self.record(photo_id)["title"], body["base"]["title"]))
        self.saved(self.save(record, path=body["new_path"], title="Harbour at dawn", tags=record["tags"], **after))

    def test_a_title_changed_or_cleared_over_a_description_shown_as_the_title_moves_it_out_too(self):
        """#2583: one rule whatever title is saved: the Description shown as the title leaves every caption field
        holding it. A cleared title came back as the Description; a changed one left the old Description behind."""
        moved = {"XMP:Description": "Harbour at dawn", "IPTC:Caption-Abstract": "Harbour at dawn"}
        kept = {"EXIF:XPComment": "Taken from the end of the pier"}
        changed, changed_id = self.made("b.jpg", 63, {**moved, **kept})
        self.saved(self.save(self.record(changed_id), title="Harbour at noon"))
        self.assertEqual({"XMP:Title": "Harbour at noon", **kept},
                         {field: text for field, text in self.held(changed).items() if text})
        self.assertEqual("Harbour at noon", self.record(changed_id)["title"])
        cleared, cleared_id = self.made("c.jpg", 68, {**moved, **kept})
        earlier = self.last_change()
        record = self.record(cleared_id)
        body, after = self.saved(self.save(record, title=""))
        self.assertEqual(kept, {field: text for field, text in self.held(cleared).items() if text})
        self.assertEqual(("", ""), (body["base"]["title"], self.record(cleared_id)["title"]), "it stays cleared")
        self.saved(self.save(record, path=body["new_path"], title="", tags=["Trips/Coast", "Trips/Lakes"], **after))
        # Undo of both saves, the latest first, puts the Description back: the page shows it as the title again. The
        # undo writes the caption's XMP copy alone, IPTC's left as the save cleared it (fields.XMP_ONLY).
        for change in range(self.last_change(), earlier, -1):
            undone = self.client.post("/library/api/history/%d/undo" % change, json={"apply": True})
            self.assertEqual(200, undone.status_code, undone.data)
        self.assertEqual({**{field: "" for field in TITLE_FIELDS + CAPTION_FIELDS}, "XMP:Description": "Harbour at dawn",
                          **kept}, self.held(cleared))
        self.assertEqual("Harbour at dawn", self.record(cleared_id)["title"])

    def last_change(self):
        conn = S.db.connect(S.db.readonly_uri(self.library.path), uri=True)
        try:
            return conn.execute("SELECT COALESCE(MAX(id), 0) FROM changes").fetchone()[0]
        finally:
            conn.close()

    def test_a_cleared_title_is_cleared_and_the_next_save_is_accepted(self):
        """#2581: titled only in XMP:Title and IPTC:ObjectName, the title cleared was written to the Description fields,
        the title stayed, the reply's base said none, and the next save was refused as changed on disk (409)."""
        path, photo_id = self.made("a.jpg", 64, {"XMP:Title": "Pier at noon", "IPTC:ObjectName": "Pier at noon"})
        record = self.record(photo_id)
        body, after = self.saved(self.save(record, title=""))
        self.assertEqual({field: "" for field in TITLE_FIELDS + CAPTION_FIELDS}, self.held(path))
        self.assertEqual(("", ""), (body["base"]["title"], self.record(photo_id)["title"]), "a reload shows no title")
        self.saved(self.save(record, path=body["new_path"], title="", tags=record["tags"] + ["Trips/Lakes"], **after))
        self.assertEqual(["Trips/Coast", "Trips/Lakes"], sorted(S.tags_in(path)))

    def test_undo_of_a_title_save_puts_the_old_title_back_and_writes_no_caption(self):
        held = {"XMP:Title": "Pier at noon", "IPTC:ObjectName": "Pier at noon",
                "XMP:Description": "The whole crew rowing out past the harbour wall"}
        path, photo_id = self.made("a.jpg", 65, held)
        before = self.held(path)
        self.saved(self.save(self.record(photo_id), title="Pier at dusk"))
        self.assertEqual("Pier at dusk", self.held(path)["XMP:Title"])
        conn = S.db.connect(S.db.readonly_uri(self.library.path), uri=True)
        try:
            change, = conn.execute("SELECT MAX(id) FROM changes").fetchone()
        finally:
            conn.close()
        undone = self.client.post("/library/api/history/%d/undo" % change, json={"apply": True})
        self.assertEqual(200, undone.status_code, undone.data)
        self.assertEqual(before, self.held(path))
        self.assertEqual("Pier at noon", self.record(photo_id)["title"])

    def test_a_row_stored_with_its_description_first_shows_the_title_and_its_save_is_accepted(self):
        """#2582: a row the indexer stored before the title came first holds `captions` [Description, Title]; the page
        showed the Description as the title while the save compared the file's Title, and refused the save (409)."""
        held = {"XMP:Title": "Pier at noon", "IPTC:ObjectName": "Pier at noon",
                "XMP:Description": "The whole crew rowing out past the harbour wall"}
        path, photo_id = self.made("a.jpg", 67, held)
        conn = S.db.connect(self.library.path)
        try:   # the order the indexer stored before DECISIONS 2026-11-06 (8,263 rows of one library, counted 2026-11-06)
            conn.execute("UPDATE photos SET captions = ? WHERE id = ?",
                         (json.dumps([held["XMP:Description"], held["XMP:Title"]]), photo_id))
            conn.commit()
        finally:
            conn.close()
        record = self.record(photo_id)
        self.assertEqual("Pier at noon", record["title"], "the page shows the file's title, not the stored first")
        body, _after = self.saved(self.save(record, tags=record["tags"] + ["Trips/Lakes"]))
        self.assertEqual(["Trips/Coast", "Trips/Lakes"], sorted(S.tags_in(path)))
        self.assertEqual({**{field: "Pier at noon" for field in TITLE_FIELDS}, "XMP:Description": held["XMP:Description"]},
                         {field: text for field, text in self.held(path).items() if text})
        self.assertEqual("Pier at noon", body["base"]["title"])

    def test_smart_rename_names_a_photo_by_its_title(self):
        path, _photo_id = self.made("Regatta - 07.jpg", 66, {"XMP:Title": "Pier at noon",
                                                             "XMP:Description": "A long day on the water"})
        reply = self.post("/photo/smart-rename", {"path": path, "grouping": "Harbour Day"})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual("Harbour Day - 07 - Pier at noon.jpg", os.path.basename(reply.get_json()["new_path"]))


if __name__ == "__main__":
    unittest.main()
