"""The index reads the next photos ahead while the card is on this one (docs/AI_PIPELINE.md, phase 1b;
tagpup.core.read_ahead, tagpup_cli.py's index).

Two threads decode the photos after the one the models are on, a few ahead, within a bound on the bytes they hold;
the models, the identity written into a file and every write of the library stay on the CLI's own thread, one photo
after the other. What a person lives through, and what must not change:

* a folder indexed reading ahead records what the same folder indexed one photo at a time records: each photo its own
  vector and its own faces, the same rows, the same files written;
* a damaged photo in the middle of the window is reported and not written to, and the photos around it are indexed;
* a photo that decodes slowly holds nothing else up: the next ones are read meanwhile, and it is still recorded in its
  turn;
* an index stopped half way (Ctrl+C) leaves nothing half written, no lock held and no thread behind, and the next index
  finishes the folder;
* two libraries indexed at once each record their own folder;
* a turned photo: CLIP is shown it upright, the detector as stored, and its boxes stay in the picture as stored;
* a 193 MP photo among ordinary ones is read alone: what is read ahead never holds more than the bound, but for one
  photo larger than it.

CLIP and the face models are stood in for (no weights load under test, tests/test_no_test_loads_a_model.py): the
stand-ins answer from the picture they are handed, so a photo given another photo's picture would show. Pillow and
ExifTool are real, and the library is made by the picker's Create in a home of its own.
"""
import os
import shutil
import sys
import threading
import time
import unittest
from unittest import mock

import numpy as np
from click.testing import CliRunner
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import damaged_photos  # noqa: E402
import own_home  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core import read_ahead  # noqa: E402
from tagpup.core.fields import read_document_id  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import images  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.ml import clip, faces as face_models  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db  # noqa: E402

ORIENTATION = 0x0112
COLOURS = [(200, 30, 30), (30, 200, 30), (30, 30, 200), (200, 200, 30), (30, 200, 200), (200, 30, 200),
           (120, 60, 10), (10, 60, 120)]
PHOTOS = len(COLOURS)


def colour_of(picture):
    """The picture's mean colour, rounded: what tells one photo's picture from another's."""
    return tuple(int(round(value)) for value in np.asarray(picture).reshape(-1, 3).mean(axis=0))


class StandIns:
    """CLIP and the face models stood in for, on the real ClipModel and FaceModel: CLIP's input is the picture's size
    and colour, its vector those numbers; the detector finds one face at the top left of the picture as stored, its
    vector the picture's colour. Each picture each was handed is noted, and the threads that handed them."""

    def __init__(self, testcase):
        self.shown, self.detected, self.card_threads = [], [], set()
        self.lock = threading.Lock()
        stand_ins = self

        def picture_input(model, img):
            return img.size, colour_of(img)

        def embed_input(model, image_input):
            stand_ins.card_threads.add(threading.current_thread().name)
            (width, height), colour = image_input
            with stand_ins.lock:
                stand_ins.shown.append(image_input)
            return [width / 100.0, height / 100.0] + [value / 255.0 for value in colour] + [0.0, 0.0, 1.0]

        def detect_and_embed(model, picture):
            stand_ins.card_threads.add(threading.current_thread().name)
            if isinstance(picture, face_models.NotDetected):
                return picture
            height, width = picture.pixels.shape[:2]
            with stand_ins.lock:
                stand_ins.detected.append((picture.path, picture.stored.size, (width, height)))
            colour = colour_of(picture.stored)
            return [{"box": [0, 0, min(width, 16), min(height, 16)], "embedding": [c / 255.0 for c in colour] + [0.0] * 509,
                     "name": None, "crop_image": images.crop_jpeg(picture.stored.crop((0, 0, 16, 16))), "prob": 0.99}]
        for patcher in (mock.patch("torch.cuda.is_available", return_value=False),
                        mock.patch.object(clip.ClipModel, "_init_model"),
                        mock.patch.object(clip.ClipModel, "picture_input", picture_input),
                        mock.patch.object(clip.ClipModel, "embed_input", embed_input),
                        mock.patch.object(face_models.FaceModel, "_init_models"),
                        mock.patch.object(face_models.FaceModel, "detect_and_embed", detect_and_embed)):
            patcher.start()
            testcase.addCleanup(patcher.stop)


def photo(path, colour, size=(64, 48), orientation=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    extra = {}
    if orientation:
        exif = Image.Exif()
        exif[ORIENTATION] = orientation
        extra["exif"] = exif.tobytes()
    Image.new("RGB", size, colour).save(path, "JPEG", quality=95, **extra)
    return path


def numbers(values):
    """Numbers as compared here: floats to four places, whether kept as float32 or made as float64."""
    return tuple(round(float(value), 4) for value in values)


def recorded(library_path):
    """{file name: (document id given, vector, [(box, face vector's first three)], detection found)} as the library
    holds it."""
    conn = db.connect(db.readonly_uri(library_path), uri=True)
    try:
        rows = {}
        for photo_id, path, document_id in conn.execute("SELECT id, path, document_id FROM photos"):
            vector = conn.execute("SELECT vector FROM embeddings WHERE photo_id = ?", (photo_id,)).fetchone()
            found = [(box, numbers(np.frombuffer(blob, dtype=np.float32)[:3]))
                     for box, blob in conn.execute("SELECT box, embedding FROM faces WHERE photo_id = ? ORDER BY id",
                                                   (photo_id,))]
            detected = conn.execute("SELECT found FROM faces_detected WHERE photo_id = ?", (photo_id,)).fetchone()
            rows[os.path.basename(path)] = (document_id is not None,
                                            numbers(np.frombuffer(vector[0], dtype=np.float32)) if vector
                                            else None, found, detected and detected[0])
        return rows
    finally:
        conn.close()


@unittest.skipIf(own_home.installed_exiftool() is None, "ExifTool not installed")
class AnIndexReadingAhead(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="read_ahead_")
        self.models = StandIns(self)

    def library(self, name):
        path = self.home.library(name)
        self.assertEqual(1, library_actions.create(path).changed)
        return path

    def folder(self, name, count=PHOTOS):
        folder = os.path.join(self.home.root, name)
        for number in range(count):
            photo(os.path.join(folder, "p%02d.jpg" % number), COLOURS[number % len(COLOURS)])
        return folder

    def index(self, library, folder, code=0):
        with mock.patch.object(tagpup_cli, "_cluster_after_index"):
            result = CliRunner().invoke(tagpup_cli.cli, ["--db", library, "index", folder])
        self.assertEqual(code, result.exit_code, result.output)
        return result

    def test_reading_ahead_records_what_one_photo_at_a_time_records(self):
        serial_library, ahead_library = self.library("serial.db"), self.library("ahead.db")
        serial_folder = self.folder("Regatta")
        ahead_folder = os.path.join(self.home.root, "Regatta again")
        shutil.copytree(serial_folder, ahead_folder)
        with mock.patch.object(tagpup_cli, "READ_THREADS", 1), mock.patch.object(tagpup_cli, "READ_AHEAD", 0):
            self.index(serial_library, serial_folder)
        self.index(ahead_library, ahead_folder)
        serial, ahead = recorded(serial_library), recorded(ahead_library)
        self.assertEqual(len(COLOURS), len(ahead))
        self.assertEqual(serial, ahead)
        for name, (given, vector, found, detected) in ahead.items():
            with self.subTest(photo=name):
                colour = colour_of(images.opened(os.path.join(ahead_folder, name), upright=True))
                self.assertTrue(given, "the photo was not given its identity")
                self.assertEqual(numbers(c / 255.0 for c in colour), vector[2:5],
                                 "the photo was recorded with another photo's vector")
                self.assertEqual(vector[2:5], found[0][1], "the photo was recorded with another photo's face")
                self.assertEqual(1, detected)
        # The models ran on the CLI's own thread, never on a thread of the read-ahead.
        self.assertEqual({threading.main_thread().name}, self.models.card_threads)

    def test_a_damaged_photo_in_the_window_is_reported_and_the_photos_around_it_are_indexed(self):
        library = self.library("harbour.db")
        folder = self.folder("Harbour", count=6)
        cut = damaged_photos.truncated(os.path.join(folder, "p02 cut short.jpg"))
        zeros = damaged_photos.all_zeros(os.path.join(folder, "p03 zeros.jpg"))
        before = {path: open(path, "rb").read() for path in (cut, zeros)}
        result = self.index(library, folder)
        rows = recorded(library)
        self.assertEqual({"p%02d.jpg" % n for n in range(6)}, set(rows))
        self.assertIn("2 photo(s) could not be read", result.output)
        for path in (cut, zeros):
            with open(path, "rb") as handle:
                self.assertEqual(before[path], handle.read(), "a damaged photo was written to")
        for name, (given, vector, found, _detected) in rows.items():
            self.assertTrue(given)
            self.assertEqual(vector[2:5], found[0][1], "%s took a neighbour's picture" % name)

    def test_a_photo_that_decodes_slowly_holds_up_nothing_and_is_recorded_in_its_turn(self):
        library = self.library("meadow.db")
        folder = self.folder("Meadow", count=6)
        slow = os.path.join(folder, "p01.jpg")
        opened, reads = images.opened, []

        def opened_slowly(path, upright):
            started = time.perf_counter()
            if path == slow and upright:
                time.sleep(0.6)
            picture = opened(path, upright)
            reads.append((os.path.basename(path), upright, started, time.perf_counter()))
            return picture
        with mock.patch.object(images, "opened", opened_slowly):
            self.index(library, folder)
        slow_start, slow_end = next((s, e) for name, up, s, e in reads if name == "p01.jpg" and up)
        meanwhile = {name for name, up, s, e in reads if name != "p01.jpg" and slow_start < s < slow_end}
        self.assertIn("p02.jpg", meanwhile, "nothing was read while the slow photo decoded")
        rows = recorded(library)
        self.assertEqual(6, len(rows))
        for name, (_given, vector, found, _detected) in rows.items():
            self.assertEqual(vector[2:5], found[0][1], "%s took a neighbour's picture" % name)
        # In its turn: the card's work went photo by photo in the folder's order.
        order = [os.path.basename(path) for path, _stored, _seen in self.models.detected]
        self.assertEqual(sorted(order), order)

    def test_an_index_stopped_half_way_leaves_nothing_half_written_and_the_next_one_finishes(self):
        library = self.library("jetty.db")
        folder = self.folder("Jetty")
        give, given = tagpup_cli.IdentityWriter.give, []

        def give_then_stop(writer, meta, *args, **kwargs):
            if len(given) == 3:
                raise KeyboardInterrupt()
            given.append(os.path.basename(meta["path"]))
            return give(writer, meta, *args, **kwargs)
        with mock.patch.object(tagpup_cli.IdentityWriter, "give", give_then_stop):
            self.index(library, folder, code=1)
        # Nothing of the batch was recorded -- it is written whole every 100 photos, or at the end --: a photo the
        # card reached keeps the vector made of it, which is a row (PhotoEmbeddings.keep), and no photo past the stop
        # has even that; no face is recorded.
        reached = set(given) | {"p03.jpg"}
        rows = recorded(library)
        self.assertLessEqual(set(rows), reached)
        self.assertFalse(any(found for _given, _vector, found, _detected in rows.values()))
        written = {name for name in os.listdir(folder) if self.has_identity(os.path.join(folder, name))}
        self.assertEqual(set(given), written, "a photo past the stop was written to")
        self.assertEqual([], os.listdir(Library(library).locks), "a photo's lock was left held")
        for thread in threading.enumerate():
            if thread.name.startswith("read ahead"):
                thread.join(5)
        self.assertFalse([t for t in threading.enumerate() if t.name.startswith("read ahead")],
                         "a read-ahead thread was left behind")
        self.index(library, folder)
        rows = recorded(library)
        self.assertEqual(len(COLOURS), len(rows))
        for name, (given_one, vector, found, _detected) in rows.items():
            self.assertTrue(given_one)
            self.assertEqual(vector[2:5], found[0][1], "%s took a neighbour's picture" % name)

    def has_identity(self, path):
        with ExifToolSession(executable=own_home.installed_exiftool()) as et:
            return bool(read_document_id(et.get_tags([path], tags=["XMP-xmpMM:DocumentID"])[0]))

    def test_two_libraries_indexed_at_once_each_record_their_own_folder(self):
        jobs = [(self.library("north.db"), self.folder("North")), (self.library("south.db"), self.folder("South", 5))]
        problems = []

        def run(library, folder):
            try:
                with mock.patch.object(tagpup_cli, "_cluster_after_index"):
                    tagpup_cli.cli.main(args=["--db", library, "index", folder], standalone_mode=False)
            except BaseException as problem:   # noqa: BLE001 - said below
                problems.append(problem)
        threads = [threading.Thread(target=run, args=job) for job in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
        self.assertEqual([], problems)
        for (library, folder), count in zip(jobs, (len(COLOURS), 5)):
            rows = recorded(library)
            self.assertEqual({"p%02d.jpg" % n for n in range(count)}, set(rows))
            for name, (_given, vector, found, _detected) in rows.items():
                self.assertEqual(vector[2:5], found[0][1], "%s took a neighbour's picture" % name)

    def test_a_turned_photo_is_shown_to_clip_upright_and_detected_as_stored(self):
        library = self.library("quay.db")
        folder = os.path.join(self.home.root, "Quay")
        turned = photo(os.path.join(folder, "turned.jpg"), (40, 90, 160), size=(80, 60), orientation=6)
        self.index(library, folder)
        self.assertEqual([(60, 80)], [size for size, _colour in self.models.shown])
        self.assertEqual([(turned, (80, 60), (80, 60))], self.models.detected)
        _given, _vector, found, _detected = recorded(library)["turned.jpg"]
        self.assertEqual("[0, 0, 16, 16]", found[0][0])


class ThePhotosReadAhead(unittest.TestCase):
    """The read-ahead itself, with photos of the shapes the live libraries hold: a 193 MP panorama among 10 MP photos
    (photo_index's largest is 192.9 MP, its median 10.0; docs/AI_PIPELINE.md, section 7)."""

    TEN = (3648, 2736)
    PANORAMA = (17000, 11350)

    def run_ahead(self, sizes, read=None, threads=2, ahead=3):
        held, seen = [], []
        reader = {}

        def read_one(item, begun, reserve):
            if read is not None:
                read(item)
            size = tagpup_cli.read_ahead_bytes(*sizes[item])
            reserve(size)
            held.append((item, size, reader["it"]._used))
            return item
        reader["it"] = read_ahead.ReadAhead(range(len(sizes)), lambda item: {}, read_one, threads=threads,
                                            ahead=ahead, budget=tagpup_cli.READ_AHEAD_BYTES)
        with reader["it"] as photos:
            for item, _begun, result in photos:
                try:
                    seen.append(result.result())
                except LookupError:
                    seen.append(None)
                time.sleep(0.01)    # the card's work on it
        return held, seen

    def test_a_193_mp_photo_is_read_alone_and_the_bound_holds_for_the_rest(self):
        sizes = [self.TEN] * 5 + [self.PANORAMA] + [self.TEN] * 4
        held, seen = self.run_ahead(sizes)
        self.assertEqual(list(range(len(sizes))), seen)
        big = tagpup_cli.read_ahead_bytes(*self.PANORAMA)
        self.assertGreater(big, tagpup_cli.READ_AHEAD_BYTES)
        for item, size, used in held:
            with self.subTest(photo=item):
                self.assertTrue(used <= tagpup_cli.READ_AHEAD_BYTES or used == big,
                                "%d bytes held at once" % used)
        self.assertEqual(big, next(used for item, size, used in held if item == 5), "the panorama shared the bound")
        self.assertGreater(max(used for item, size, used in held if item != 5), tagpup_cli.read_ahead_bytes(*self.TEN),
                           "the ordinary photos were not read ahead at all")

    def test_a_photo_gone_before_its_read_is_its_own_error_and_the_rest_come_in_order(self):
        def read(item):
            if item == 2:
                raise LookupError("gone")
        held, seen = self.run_ahead([self.TEN] * 6, read=read)
        self.assertEqual([0, 1, None, 3, 4, 5], seen)

    def test_closed_part_way_its_threads_end(self):
        names = set()

        def read(item):
            names.add(threading.current_thread().name)
        reader = read_ahead.ReadAhead(range(50), lambda item: {}, lambda item, begun, reserve: (read(item), reserve(1)),
                                      threads=2, ahead=3, budget=10)
        with reader as photos:
            for item, _begun, _result in photos:
                if item == 3:
                    break
        for thread in threading.enumerate():
            if thread.name in names:
                thread.join(5)
        self.assertFalse([t for t in threading.enumerate() if t.name in names])


if __name__ == "__main__":
    unittest.main()
