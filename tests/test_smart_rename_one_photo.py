"""Smart Rename of the open photo, from the box beside its name (scenario F1; DECISIONS 2026-11-05).

The owner opens a photo, finds the grouping its name holds in the box beside the name, types another and presses Smart
Rename: the photo keeps its number and takes the new grouping, its row follows the file, and History can undo it. A
photo whose name holds no number takes the next after the highest the folder gives that grouping. A name another file
holds is refused with a sentence and nothing is written (a folder's rename moves that file aside; one photo's does not).
A second click, which the page sends behind the first under the photo's new name, changes nothing. (A clip:
tests/test_a_clip_renamed.py.)
(A share that does not answer: tests/test_a_share_asleep.py, the route among every folder refusal.) Real ExifTool on
JPEGs made here; the rows are the indexer's (photo_rows.add_read). Run against the trunk before the route: each fails
(404).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_stale_record_precondition as S  # noqa: E402


PRESERVED = "XMP-xmpMM:PreservedFileName"


@unittest.skipIf(S.EXIFTOOL is None, "ExifTool not installed")
class SmartRenameOfOnePhoto(S.Fresh):
    def rename(self, path, grouping):
        return self.post("/photo/smart-rename", {"path": path, "grouping": grouping})

    def parts(self, path):
        return self.api("/photo/smart-rename-parts", query_string={"path": path}).get_json()

    def names(self):
        return sorted(os.listdir(self.folder))

    def rows_at(self, path):
        return self.rows("SELECT COUNT(*) FROM photos WHERE path = ?", (path,))[0][0]

    def rows(self, sql, params=()):
        conn = S.db.connect(S.db.readonly_uri(self.library.path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def contents(self):
        found = {}
        for name in self.names():
            with open(os.path.join(self.folder, name), "rb") as handle:
                found[name] = handle.read()
        return found

    def changes(self):
        return self.rows("SELECT COUNT(*) FROM changes")[0][0]

    def test_a_new_grouping_keeps_the_photos_number_and_its_row_follows(self):
        path, _ = self.make("Regatta - 07 - Start.jpg", 1, ["Trips/Coast"], caption="Start")
        self.assertEqual({"grouping": "Regatta", "number": "07"}, self.parts(path), "the box is filled from the name")
        reply = self.rename(path, "Harbour Day")
        body = reply.get_json()
        new = os.path.join(self.folder, "Harbour Day - 07 - Start.jpg")
        self.assertEqual((200, True, new), (reply.status_code, body["renamed"], body["new_path"]), reply.data)
        self.assertEqual(["Harbour Day - 07 - Start.jpg"], self.names())
        self.assertEqual((0, 1), (self.rows_at(path), self.rows_at(new)), "the row did not follow the file")
        self.assertEqual(["Regatta - 07 - Start.jpg"], list(S.in_file(new, PRESERVED).values())[1:],
                         "the name it had before its first rename was not kept")
        self.assertEqual(os.stat(new).st_size, body["size"], "the reply's stamp is not the file's")

    def test_a_name_with_no_number_takes_the_next_the_folder_gives_the_grouping(self):
        self.make("Harbour - 01.jpg", 2)
        self.make("harbour - 03 - Finish.jpg", 3, caption="Finish")   # compared as Windows compares names
        self.make("Regatta - 09.jpg", 4)                                # another grouping's number is not counted
        path, _ = self.make("IMG_0412.jpg", 5)
        self.assertEqual({"grouping": "", "number": None}, self.parts(path))
        reply = self.rename(path, "Harbour")
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(os.path.join(self.folder, "Harbour - 04.jpg"), reply.get_json()["new_path"])
        self.assertNotIn("IMG_0412.jpg", self.names())

    def test_a_name_another_file_holds_is_refused_and_nothing_is_written(self):
        path, _ = self.make("Regatta - 05.jpg", 6)
        other, _ = self.make("Harbour - 05.jpg", 7)
        before = self.contents()
        changes = self.changes()
        reply = self.rename(path, "Harbour")
        self.assertEqual(400, reply.status_code, reply.data)
        self.assertIn("Another file in the folder is already named Harbour - 05.jpg", reply.get_json()["error"])
        self.assertEqual(before, self.contents(), "a file was written or moved")
        self.assertEqual((1, 1, changes), (self.rows_at(path), self.rows_at(other), self.changes()))

    def test_a_second_click_sent_under_the_new_name_changes_nothing(self):
        path, _ = self.make("Regatta - 2.jpg", 8)
        first = self.rename(path, "Harbour").get_json()
        changes = self.changes()
        new = first["new_path"]
        stamp = os.stat(new)
        again = self.rename(new, "Harbour")
        body = again.get_json()
        self.assertEqual((200, False, new), (again.status_code, body["renamed"], body["new_path"]), again.data)
        self.assertEqual(["Harbour - 2.jpg"], self.names())
        self.assertEqual((stamp.st_mtime, changes), (os.stat(new).st_mtime, self.changes()), "the file was written again")
        gone = self.rename(path, "Harbour")
        self.assertEqual(400, gone.status_code, "the old name, sent again, is not there any more")
        self.assertIn("is not in its folder any more", gone.get_json()["error"])



if __name__ == "__main__":
    unittest.main()
