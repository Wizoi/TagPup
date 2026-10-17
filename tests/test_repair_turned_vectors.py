"""Phase 1f of the AI pipeline (#1529): the vectors of photos turned by their Orientation were made before CLIP saw
photos upright, and their stamps still match, so they were never redone. `repair-vectors` makes them again.

Scenarios: the owner looks (a dry run writes nothing), repairs (only the turned photos' vectors change, a photo that
is not turned is untouched to the byte), repairs again (nothing more), is interrupted (the next run resumes by what
the table holds), turns or edits a photo between the look and the repair (embedded as it now is and stamped so, or left for
the next look: never a vector of another picture), has a share that does not answer (named, its photos skipped), a
failure that is not the file's (counted, the run goes on) and two libraries (each repaired by its own run). Rows through tests/photo_rows.py, vectors
through the store that owns them, a real JPEG with an Orientation on disk; CLIP is a stand-in that answers the
upright vector."""
import contextlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
from click.testing import CliRunner  # noqa: E402
from PIL import Image  # noqa: E402
from view_library import ViewLibrary, make_jpeg  # noqa: E402

from tagpup.files import shares  # noqa: E402
from tagpup.services import embedding_repair, search  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402
from tagpup_cli import cli  # noqa: E402

SETTINGS = {"model_name": "stand-in", "pretrained": "clip", "preserve_full_frame": True, "max_aspect_ratio": 2.0,
            "force_image_size": None}
MODEL = store_embeddings.model_key(**SETTINGS)
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

    settings = SETTINGS

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

    def embeddings(self, library, photo_index):
        return search.PhotoEmbeddings(self.clip_, photo_index)

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
        self.assertEqual((1, 0), (counts["replaced"], counts["not_kept"]))
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

    def test_a_photo_edited_since_the_dry_run_is_left_for_the_next_look(self):
        self.repair(apply=False)
        path = self.vl.path_of(self.turned)
        make_turned(path, orientation=6)
        os.utime(path, (1_000_000_000, 1_000_000_000))   # another file now: its stamp is not the vector's
        counts = self.repair()
        self.assertEqual((0, 0, 1), (counts["replaced"], counts["to_recompute"], counts["stale_stamp"]))
        self.assertEqual(OLD.tobytes(), self.vector_of(self.vl, self.turned))

    def test_a_photo_turned_between_the_look_and_the_embed_is_embedded_as_it_now_is_and_stamped_so(self):
        path = self.vl.path_of(self.turned)
        looked = embedding_repair._turned

        def look_then_turn(*args):
            found = looked(*args)
            make_turned(path, orientation=6)
            os.utime(path, (1_000_000_000, 1_000_000_000))
            return found

        with mock.patch.object(embedding_repair, "_turned", look_then_turn):
            counts = self.repair()
        self.assertEqual((1, 0), (counts["replaced"], counts["not_kept"]))
        held = store_embeddings.get(self.vl.conn, path, MODEL)
        self.assertEqual(store_embeddings.stamp_of(path), (held.mtime, held.size))
        self.assertEqual(UPRIGHT.tobytes(), held.vector)

    def test_a_photo_that_changes_while_it_is_embedded_keeps_a_vector_stamped_before_so_the_next_look_redoes_it(self):
        path = self.vl.path_of(self.turned)
        self.runtime.clip_.during = lambda _path: os.utime(path, (1_000_000_000, 1_000_000_000))
        counts = self.repair()
        self.assertEqual((0, 1), (counts["replaced"], counts["not_kept"]))
        held = store_embeddings.get(self.vl.conn, path, MODEL)
        self.assertNotEqual(store_embeddings.stamp_of(path), (held.mtime, held.size))

    def test_a_failure_that_is_not_the_files_is_counted_by_reason_and_the_run_goes_on(self):
        second = self.photo(self.vl, "second.jpg", turned=True)
        asked = []

        def out_of_memory_once(path):
            asked.append(path)
            if len(asked) == 1:
                raise MemoryError("CUDA out of memory")

        self.runtime.clip_.during = out_of_memory_once
        counts = self.repair()
        self.assertEqual((1, 1, {"MemoryError": 1}), (counts["unreadable"], counts["replaced"], counts["reasons"]))
        self.assertEqual({OLD.tobytes(), UPRIGHT.tobytes()},
                         {self.vector_of(self.vl, self.turned), self.vector_of(self.vl, second)})

    def test_a_share_that_does_not_answer_is_named_and_its_photos_are_skipped_not_dropped(self):
        asked = []

        def late(path):
            asked.append(path)
            return shares.UNANSWERED

        with mock.patch.object(shares, "on_a_network_drive", return_value=True), \
                mock.patch.object(shares, "share_of", return_value="server"), \
                mock.patch.object(shares, "stamp_of", late):
            counts = self.repair(apply=False)
        self.assertEqual((2, 2, ["server"], 1), (counts["photos"], counts["skipped_away"], counts["away"], len(asked)))
        self.assertEqual((0, 0, 0), (counts["missing"], counts["failed"], counts["no_header"]))

    def test_two_libraries_are_each_repaired_by_their_own_run(self):
        other = ViewLibrary(self, name="other", home=self.vl.home)
        theirs = self.photo(other, "turned.jpg", turned=True)
        self.repair()
        self.assertEqual(OLD.tobytes(), self.vector_of(other, theirs))
        self.repair(other)
        self.assertEqual(UPRIGHT.tobytes(), self.vector_of(other, theirs))

    def test_the_command_says_what_it_would_do_and_what_it_did(self):
        with mock.patch("tagpup_cli.get_runtime", return_value=self.runtime):
            dry = CliRunner().invoke(cli, ["--db", self.vl.path, "repair-vectors"])
            self.assertEqual(0, dry.exit_code, dry.output)
            said = " ".join(dry.output.split())
            self.assertIn("1 turned, of which 1 hold a vector", said)
            self.assertIn("--apply would make 1 vector(s) again. Nothing was written.", said)
            done = CliRunner().invoke(cli, ["--db", self.vl.path, "repair-vectors", "--apply"])
        self.assertEqual(0, done.exit_code, done.output)
        said = " ".join(done.output.split())
        self.assertIn("Replaced 1 (read back as written)", said)
        self.assertIn("restart it to search by the new ones", said)


if __name__ == "__main__":
    unittest.main()
