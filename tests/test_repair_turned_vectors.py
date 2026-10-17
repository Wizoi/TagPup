"""Phase 1f of the AI pipeline (#1529): the vectors of photos turned by their Orientation were made before CLIP saw
photos upright, and their stamps still match, so they were never redone. `repair-vectors` makes them again.

Scenarios: the owner looks (a dry run writes nothing), repairs (only the turned photos' vectors change, a photo that
is not turned is untouched to the byte), repairs again (nothing more), is interrupted (the next run resumes by what
the table holds), turns or edits a photo between the look and the repair (that photo's conflict, never a vector of
another picture), and has two libraries (each repaired by its own run). Rows through tests/photo_rows.py, vectors
through the store that owns them, a real JPEG with an Orientation on disk; CLIP is a stand-in that answers the
upright vector."""
import contextlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
from click.testing import CliRunner  # noqa: E402
from PIL import Image  # noqa: E402
from view_library import ViewLibrary, make_jpeg  # noqa: E402

from tagpup.services import embedding_repair  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402
from tagpup_cli import cli  # noqa: E402

MODEL = "stand-in|clip"
OLD = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)      # the sideways picture
UPRIGHT = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)  # the picture as it is shown


def make_turned(path, orientation=8):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    exif = Image.Exif()
    exif[0x0112] = orientation
    Image.new("RGB", (800, 600), (90, 110, 130)).save(path, "JPEG", exif=exif.tobytes())
    return path


class StandInClip:
    """What the runtime's CLIP answers for a photo: the upright vector, unless `during` is set to run first."""

    def __init__(self):
        self.embedded = []
        self.during = None

    def load(self):
        pass

    def embed_image(self, path):
        self.embedded.append(path)
        if self.during:
            self.during(path)
        return UPRIGHT.tolist()


class StandInRuntime:
    def __init__(self):
        self.clip_ = StandInClip()
        self.turns = 0

    def model_key(self, library):
        return MODEL

    def clip(self, library):
        return self.clip_

    @contextlib.contextmanager
    def gpu_turn(self, what, models, **_):
        self.turns += 1
        yield


class RepairTurnedVectors(unittest.TestCase):
    def setUp(self):
        self.vl = ViewLibrary(self)
        self.turned = self.photo(self.vl, "turned.jpg", turned=True)
        self.plain = self.photo(self.vl, "plain.jpg")
        self.runtime = StandInRuntime()

    def photo(self, vl, name, turned=False, vector=OLD):
        path = os.path.join(vl.pictures, "Regatta", name)
        (make_turned if turned else make_jpeg)(path)
        photo_id = vl.photo("Regatta", name, at=path)
        stamp = store_embeddings.stamp_of(path)
        store_embeddings.put(vl.conn, path, MODEL, stamp[0], stamp[1], vector.tobytes())
        vl.conn.commit()
        return photo_id

    def vector_of(self, vl, photo_id):
        return db.connect(db.readonly_uri(vl.path), uri=True).execute(
            "SELECT vector FROM embeddings WHERE photo_id = ?", (photo_id,)).fetchone()[0]

    def repair(self, vl=None, apply=True):
        return embedding_repair.repair((vl or self.vl).library, self.runtime, apply=apply)

    def test_a_dry_run_counts_the_turned_photo_and_writes_nothing(self):
        counts = self.repair(apply=False)
        self.assertEqual((2, 1, 1), (counts["photos"], counts["turned"], counts["to_recompute"]))
        self.assertEqual(OLD.tobytes(), self.vector_of(self.vl, self.turned))
        self.assertEqual(0, self.runtime.turns)

    def test_the_turned_photos_vector_is_replaced_and_the_plain_one_is_untouched_to_the_byte(self):
        counts = self.repair()
        self.assertEqual((1, 1), (counts["replaced"], counts["verified"]))
        self.assertEqual(UPRIGHT.tobytes(), self.vector_of(self.vl, self.turned))
        self.assertEqual(OLD.tobytes(), self.vector_of(self.vl, self.plain))
        self.assertEqual(1, len(self.runtime.clip_.embedded))   # the plain photo was never embedded

    def test_a_second_run_changes_nothing_more(self):
        self.repair()
        counts = self.repair()
        self.assertEqual((0, 1), (counts["replaced"], counts["already_upright"]))
        self.assertEqual(UPRIGHT.tobytes(), self.vector_of(self.vl, self.turned))

    def test_a_run_killed_half_way_is_resumed_by_what_the_table_holds(self):
        second = self.photo(self.vl, "second.jpg", turned=True)
        clip, first = self.runtime.clip_, []

        def die_on_the_second(path):
            first.append(path)
            if len(first) == 2:
                raise KeyboardInterrupt

        clip.during = die_on_the_second
        with self.assertRaises(KeyboardInterrupt):
            self.repair()
        held = {self.vector_of(self.vl, self.turned), self.vector_of(self.vl, second)}
        self.assertEqual({OLD.tobytes(), UPRIGHT.tobytes()}, held)   # one replaced whole, one as it was
        clip.during = None
        counts = self.repair()
        self.assertEqual((1, 1), (counts["replaced"], counts["already_upright"]))
        self.assertEqual({UPRIGHT.tobytes()}, {self.vector_of(self.vl, self.turned), self.vector_of(self.vl, second)})

    def test_a_photo_edited_after_the_look_is_left_for_the_next_look(self):
        self.repair(apply=False)
        path = self.vl.path_of(self.turned)
        make_turned(path, orientation=6)
        os.utime(path, (1_000_000_000, 1_000_000_000))   # another file now: its stamp is not the vector's
        counts = self.repair()
        self.assertEqual((0, 0, 1), (counts["replaced"], counts["to_recompute"], counts["stale_stamp"]))
        self.assertEqual(OLD.tobytes(), self.vector_of(self.vl, self.turned))

    def test_a_photo_that_changes_while_it_is_embedded_is_a_conflict_never_an_overwrite(self):
        path = self.vl.path_of(self.turned)
        self.runtime.clip_.during = lambda _path: os.utime(path, (1_000_000_000, 1_000_000_000))
        counts = self.repair()
        self.assertEqual((0, 1), (counts["replaced"], counts["conflict"]))
        self.assertEqual(OLD.tobytes(), self.vector_of(self.vl, self.turned))

    def test_two_libraries_are_each_repaired_by_their_own_run(self):
        other = ViewLibrary(self, name="other", home=self.vl.home)
        theirs = self.photo(other, "turned.jpg", turned=True)
        self.repair()
        self.assertEqual(OLD.tobytes(), self.vector_of(other, theirs))
        self.repair(other)
        self.assertEqual(UPRIGHT.tobytes(), self.vector_of(other, theirs))

    def test_the_command_says_what_it_would_do_and_what_it_did(self):
        from unittest import mock
        with mock.patch("tagpup_cli.get_runtime", return_value=self.runtime):
            dry = CliRunner().invoke(cli, ["--db", self.vl.path, "repair-vectors"])
            self.assertEqual(0, dry.exit_code, dry.output)
            said = " ".join(dry.output.split())
            self.assertIn("1 turned, of which 1 hold a vector", said)
            self.assertIn("--apply would make 1 vector(s) again. Nothing was written.", said)
            done = CliRunner().invoke(cli, ["--db", self.vl.path, "repair-vectors", "--apply"])
        self.assertEqual(0, done.exit_code, done.output)
        self.assertIn("Replaced 1 (1 read back as written)", " ".join(done.output.split()))


if __name__ == "__main__":
    unittest.main()
