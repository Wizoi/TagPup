"""Scenario P8, from the command line: the owner runs `face-pass` on a library and a folder or two
(tagpup_cli.py, over tagpup.services.face_pass), the new model stood in for (tests/stand_in_models.NewFaces) and handed
out by a real Runtime (Runtime.look_at_faces), as on the owner's machine.

What the owner lives through:
* a dry run, the default, says what it would do, folder by folder, in counts, and writes nothing, no snapshot either;
* --apply takes one snapshot before its first write, and a second --apply the same day writes nothing and takes none;
  an --apply that finds nothing to write (every photo done, or no face found) takes no snapshot;
* Ctrl+C half way stops after the photo under way (that photo written whole), and a run again finishes the folder;
  Ctrl+C before the first photo stops it too, saying so, never that the folder holds no photo; a dry run stopped
  says nothing was written;
* two folders: one change of the journal each, one snapshot for the run; a folder given inside another is refused;
* `undo` of a folder whose new faces were named since is refused, naming the first ten reasons and how many more;
* the weights missing: nothing is done, and it says what to fetch.

Photos are real files (Pillow), rows as the index makes them (tests/photo_rows.py), faces as it stores them
(tests/face_rows.py), the library made by the picker's Create, in a home of the test's own (tests/own_home.py)."""
import os
import signal
import sys
import time
import unittest
from unittest import mock

from click.testing import CliRunner
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import add_face, person_of  # noqa: E402
from stand_in_models import NewFaces  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.ml import gpu  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.store import snapshots as store_snapshots  # noqa: E402

#: The journal's own record, and the counters every write moves.
BOOKKEEPING = ("changes", "change_rows", "generations", "sqlite_sequence")


class CtrlCAt(NewFaces):
    """The new model, and the owner pressing Ctrl+C while it looks at `at`."""

    def __init__(self, found, at):
        super().__init__(found)
        self.at = at

    def detect_and_embed(self, picture):
        detected = super().detect_and_embed(picture)
        if os.path.basename(picture.path) == self.at:
            signal.raise_signal(signal.SIGINT)
        return detected


class CtrlCBeforeTheFirstPhoto(NewFaces):
    """The new model, and the owner pressing Ctrl+C while its weights are checked: before any photo is looked at."""

    def missing(self):
        signal.raise_signal(signal.SIGINT)
        return None


class Missing(NewFaces):
    def missing(self):
        return "The face models' weights are not all in the checkpoints folder."


class FacePassFromTheCommandLine(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="face_pass_cli_")
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.regatta = self.folder("Harbour Regatta")
        self.picnic = self.folder("Cove Picnic")
        self.card = gpu.Card(os.path.join(self.home.root, "card"), poll=0.05)
        self.addCleanup(self.card.close)

    def folder(self, name):
        path = os.path.join(self.home.root, name)
        os.makedirs(path)
        return path

    def photo(self, folder, name):
        path = os.path.join(folder, name)
        Image.new("RGB", (400, 300), (90, 120, 150)).save(path)
        conn = db.connect(self.db_path)
        try:
            photo_id = photo_rows.add_unread(conn, path)
            conn.commit()
        finally:
            conn.close()
        return photo_id, path

    def face(self, path, box, name=None):
        conn = db.connect(self.db_path)
        try:
            columns = {"name": name, "name_source": "manual"} if name else {}
            face_id = add_face(conn, path, box, bytes(32), prob=0.99, **columns)
            conn.commit()
        finally:
            conn.close()
        return face_id

    def three_photos(self, folder):
        """Three photos, a face named in the first; what the new model finds in each."""
        found = {}
        for number, name in enumerate(("first.jpg", "second.jpg", "third.jpg")):
            _photo, path = self.photo(folder, name)
            if number == 0:
                self.face(path, [100, 50, 200, 170], name="Rowan Thackeray")
            found[name] = [((102, 52, 198, 172), 0.9, number), ((250, 40, 330, 120), 0.8, number + 10)]
        return found

    def cli(self, model, *args):
        runtime = Runtime(build_clip=lambda settings: None, build_faces=lambda settings: model, card=self.card)
        self.addCleanup(runtime.forget, Library(self.db_path))
        with mock.patch.object(tagpup_cli, "get_runtime", return_value=runtime):
            return CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path] + list(args))

    def query(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def dump(self):
        tables = [name for (name,) in self.query("SELECT name FROM sqlite_master WHERE type = 'table'")
                  if name not in BOOKKEEPING]
        return {table: sorted(map(repr, self.query('SELECT * FROM "%s"' % table))) for table in tables}

    def changes(self):
        return [change_id for (change_id,) in self.query(
            "SELECT id FROM changes WHERE operation = ? AND undone IS NULL ORDER BY id", ("face pass (%s)" % NewFaces.key,))]

    def dailies(self):
        return store_snapshots.listed(self.db_path, store_snapshots.DAILY)

    def test_a_dry_run_says_what_it_would_do_in_counts_and_writes_nothing(self):
        found = self.three_photos(self.regatta)
        before = self.dump()
        ran = self.cli(NewFaces(found), "face-pass", self.regatta)
        self.assertEqual(0, ran.exit_code, ran.output)
        self.assertIn("3 photo(s) looked at, 0 done before", ran.output)
        self.assertIn("1 old face(s) given the new vector, 5 new face(s) (unnamed), 0 old face(s) unmatched", ran.output)
        self.assertIn("Nothing changed. --apply writes it", ran.output)
        self.assertNotIn(self.regatta, ran.output, "a path only with --reveal")
        self.assertEqual(before, self.dump())
        self.assertEqual([], self.changes())
        self.assertEqual([], self.dailies(), "a dry run takes no snapshot")
        revealed = self.cli(NewFaces(found), "face-pass", self.regatta, "--reveal")
        self.assertIn(self.regatta, revealed.output)

    def test_apply_takes_one_snapshot_and_writes_and_a_second_apply_writes_nothing(self):
        found = self.three_photos(self.regatta)
        ran = self.cli(NewFaces(found), "face-pass", self.regatta, "--apply")
        self.assertEqual(0, ran.exit_code, ran.output)
        self.assertIn("Took a snapshot of the library first (daily", ran.output)
        (change_id,) = self.changes()
        self.assertIn("Written: change(s) %d." % change_id, ran.output)
        self.assertEqual(1, len(self.dailies()))
        written = self.dump()
        again = self.cli(NewFaces(found), "face-pass", self.regatta, "--apply")
        self.assertEqual(0, again.exit_code, again.output)
        self.assertNotIn("snapshot", again.output, "a run that writes nothing says nothing of a snapshot")
        self.assertIn("0 photo(s) looked at, 3 done before", again.output)
        self.assertIn("Held already: change(s) %d of this folder stand" % change_id, again.output)
        self.assertEqual(written, self.dump(), "a second run writes nothing")
        self.assertEqual([change_id], self.changes())
        self.assertEqual(1, len(self.dailies()), "one snapshot a day")

    def test_an_apply_with_nothing_to_write_takes_no_snapshot(self):
        self.three_photos(self.regatta)
        before = self.dump()
        ran = self.cli(NewFaces({}), "face-pass", self.regatta, "--apply")
        self.assertEqual(0, ran.exit_code, ran.output)
        self.assertIn("3 with no face found", ran.output)
        self.assertNotIn("snapshot", ran.output)
        self.assertEqual(before, self.dump())
        self.assertEqual([], self.dailies(), "no snapshot for a run that writes nothing: the oldest stays")

    def test_a_snapshot_from_late_yesterday_is_not_todays_the_run_takes_one(self):
        found = self.three_photos(self.regatta)
        midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        store_snapshots.take(self.db_path, now=midnight - 60)
        ran = self.cli(NewFaces(found), "face-pass", self.regatta, "--apply")
        self.assertEqual(0, ran.exit_code, ran.output)
        self.assertIn("Took a snapshot of the library first (daily", ran.output)
        self.assertEqual(2, len(self.dailies()))

    def test_ctrl_c_half_way_stops_after_the_photo_under_way_and_a_run_again_finishes_the_folder(self):
        found = self.three_photos(self.regatta)
        model = CtrlCAt(found, "second.jpg")
        stopped = self.cli(model, "face-pass", self.regatta, "--apply")
        self.assertEqual(1, stopped.exit_code, stopped.output)
        self.assertIn("Stopping after this photo", stopped.output)
        self.assertIn("Stopped: what was written stays written", stopped.output)
        self.assertEqual(["first.jpg", "second.jpg"], model.asked, "nothing after the photo under way was looked at")
        self.assertIn("2 photo(s) looked at", stopped.output)
        self.assertEqual(4, self.query("SELECT COUNT(*) FROM face_embeddings WHERE model = ?", (NewFaces.key,))[0][0],
                         "the photo under way was written whole")
        self.assertEqual(signal.default_int_handler, signal.getsignal(signal.SIGINT), "Ctrl+C is the default again")
        resumed = NewFaces(found)
        again = self.cli(resumed, "face-pass", self.regatta, "--apply")
        self.assertEqual(0, again.exit_code, again.output)
        self.assertEqual(["third.jpg"], resumed.asked)
        self.assertIn("1 photo(s) looked at, 2 done before", again.output)
        self.assertEqual(6, self.query("SELECT COUNT(*) FROM face_embeddings WHERE model = ?", (NewFaces.key,))[0][0])
        self.assertEqual(2, len(self.changes()), "a folder resumed is two changes, each undone alone")
        self.assertEqual(1, len(self.dailies()))

    def test_ctrl_c_in_a_dry_run_says_nothing_was_written(self):
        found = self.three_photos(self.regatta)
        before = self.dump()
        stopped = self.cli(CtrlCAt(found, "second.jpg"), "face-pass", self.regatta)
        self.assertEqual(1, stopped.exit_code, stopped.output)
        self.assertIn("Stopped: nothing was written; run it again to go on.", stopped.output)
        self.assertNotIn("stays written", stopped.output)
        self.assertEqual(before, self.dump())

    def test_ctrl_c_before_the_first_photo_says_it_stopped_and_writes_nothing(self):
        found = self.three_photos(self.regatta)
        before = self.dump()
        model = CtrlCBeforeTheFirstPhoto(found)
        stopped = self.cli(model, "face-pass", self.regatta, "--apply")
        self.assertEqual(1, stopped.exit_code, stopped.output)
        self.assertIn("Stopped: nothing was written; run it again to go on.", stopped.output)
        self.assertNotIn("holds no photo", stopped.output)
        self.assertEqual([], model.asked)
        self.assertEqual(before, self.dump())
        self.assertEqual([], self.dailies(), "no snapshot for a run stopped before its first write")

    def test_two_folders_each_their_own_change_one_snapshot_and_a_folder_inside_another_refused(self):
        found = self.three_photos(self.regatta)
        found.update({"beach.jpg": [((20, 20, 90, 90), 0.9, 30)]})
        self.photo(self.picnic, "beach.jpg")
        ran = self.cli(NewFaces(found), "face-pass", self.regatta, self.picnic, "--apply")
        self.assertEqual(0, ran.exit_code, ran.output)
        self.assertIn("Folder 1:", ran.output)
        self.assertIn("Folder 2:", ran.output)
        self.assertIn("2 folder(s), 4 photo(s) looked at", ran.output)
        self.assertEqual(2, len(self.changes()))
        self.assertEqual(1, len(self.dailies()), "one snapshot a run, never one a folder")
        self.assertEqual(1, ran.output.count("Took a snapshot of the library first"), ran.output)
        inner = self.folder(os.path.join("Harbour Regatta", "Boats"))
        before = self.dump()
        refused = self.cli(NewFaces(found), "face-pass", self.regatta, inner, "--apply")
        self.assertEqual(1, refused.exit_code, refused.output)
        self.assertIn("one is under the other", refused.output)
        self.assertEqual(before, self.dump())

    def test_undo_of_a_folder_whose_new_faces_were_named_since_names_ten_reasons_and_how_many_more(self):
        _photo, path = self.photo(self.regatta, "crowd.jpg")
        crowd = {"crowd.jpg": [((10 + 33 * i, 10, 40 + 33 * i, 40), 0.9, i) for i in range(11)]}
        ran = self.cli(NewFaces(crowd), "face-pass", self.regatta, "--apply")
        self.assertEqual(0, ran.exit_code, ran.output)
        (change_id,) = self.changes()
        conn = db.connect(self.db_path)
        person_of(conn, "Maren Oakhollow")
        store_faces.name(conn, [face_id for (face_id,) in self.query("SELECT id FROM faces")], "Maren Oakhollow")
        conn.commit()
        conn.close()
        before = self.dump()
        undo = self.cli(NewFaces(crowd), "undo", str(change_id), "--apply")
        self.assertEqual(1, undo.exit_code, undo.output)
        self.assertEqual(10, undo.output.count("is not what change %d left" % change_id), undo.output)
        self.assertIn("and 1 more", undo.output)
        self.assertEqual(before, self.dump(), "a refused undo writes nothing")

    def test_the_weights_missing_nothing_is_done_and_it_says_what_to_fetch(self):
        self.three_photos(self.regatta)
        before = self.dump()
        ran = self.cli(Missing(), "face-pass", self.regatta, "--apply")
        self.assertEqual(1, ran.exit_code, ran.output)
        self.assertIn("Not run: The face models' weights are not all in", ran.output)
        self.assertEqual(before, self.dump())
        self.assertEqual([], self.dailies(), "no snapshot for a run that does nothing")


if __name__ == "__main__":
    unittest.main()
