"""A photo's faces are found at the library's detector settings (docs/AI_PIPELINE.md, phase 1a): with a cap on the
long side of the picture the detector looks at (faces.detect_max_side), a larger photo is detected at the cap and its
boxes come back in the picture as stored; crops and vectors are cut from the full picture, so a face found at both
sizes embeds to the same vector. Off -- the default, and every live library -- the detector is handed the picture as
before and every photo stays recorded under the detector name it was detected with.

The cap is an argument of each detection, not part of the face models: two libraries differing only in it share one
model, and a Suggest in one is not refused beside a run in the other, while each records its detections under its own
name (tagpup.services.faces.detector_of).

Phase 0 measured why (2026-10-16): MTCNN on the full picture is 69% of an index and peaked the card at 9,982 of
10,240 MiB on the largest photos. The scenarios: an owner turns the cap on in one library and not another, and Suggests
in both at once; the cap changes while a run is under way (the run keeps the cap it began with); a photo larger than
the cap, one smaller, a turned photo (Orientation 6: boxes stay in the picture as stored), a face at the edge (clamped).

The face model is tagpup.ml.faces.FaceModel as the runtime builds it from the library's settings; MTCNN and the
recogniser are stood in for (no weights load under test): the detector knows where the face is in the full picture
and answers in the coordinates of the picture it is handed, as MTCNN does.
"""
import json
import os
import sys
import types
import unittest
from unittest import mock

import numpy as np
from click.testing import CliRunner
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.ml import clip, faces as face_models  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import faces as face_records  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings  # noqa: E402
from tagpup.store import db, faces_detected  # noqa: E402

#: The detector name kr-track and renton_parkrun hold on all their faces_detected rows (176 and 146, counted
#: read-only 2026-10-16): the default face settings, spelled as faces_detected.detector spells them.
LIVE_DETECTOR = '{"confidence_threshold": 0.85, "min_face_size": 20, "mtcnn_thresholds": [0.6, 0.7, 0.7]}'

ORIENTATION = 0x0112


class Detector:
    """MTCNN stood in for: one face at `box` in a picture of `full` size, answered in the coordinates of the picture
    it is handed, with each picture's size noted."""

    def __init__(self, full, box=None):
        self.full, self.box = full, box
        self.handed = []

    def detect(self, picture):
        self.handed.append(picture.size)
        if self.box is None:
            return None, None
        across, down = picture.size[0] / self.full[0], picture.size[1] / self.full[1]
        x1, y1, x2, y2 = self.box
        return np.array([[x1 * across, y1 * down, x2 * across, y2 * down]], dtype=np.float32), np.array([0.99])


def recogniser(batch):
    """The recogniser stood in for: the vector is the first 512 numbers of the crop it is given, so two crops give one
    vector only when they are the same pixels."""
    return batch.reshape(len(batch), -1)[:, :512].clone()


def picture(path, size, face, orientation=None):
    """A grey JPEG of `size` with a patterned face-like patch at `face` (x1, y1, x2, y2), turned by `orientation`."""
    width, height = size
    pixels = np.full((height, width, 3), 120, dtype=np.uint8)
    if face:
        x1, y1, x2, y2 = face
        ys, xs = np.mgrid[y1:y2, x1:x2]
        pixels[y1:y2, x1:x2, 0] = (xs * 7 + ys * 3) % 256
        pixels[y1:y2, x1:x2, 1] = (xs * 5) % 256
        pixels[y1:y2, x1:x2, 2] = (ys * 11) % 256
    image = Image.fromarray(pixels)
    extra = {}
    if orientation:
        exif = Image.Exif()
        exif[ORIENTATION] = orientation
        extra["exif"] = exif.tobytes()
    image.save(path, "JPEG", quality=95, **extra)
    return path


class ALibrary(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="detector_cap_")
        self.runtime = Runtime()
        self.folder = os.path.join(self.home.root, "Regatta")
        os.makedirs(self.folder)

    def library(self, name, cap=None):
        """A library as the picker's Create makes it, its cap set in the gear (a journaled change) when given."""
        path = self.home.library(name)
        self.assertEqual(1, library_actions.create(path).changed)
        library = Library(path)
        if cap is not None:
            self.set_cap(library, cap)
        return library

    def set_cap(self, library, cap):
        self.assertEqual(1, settings.change(library, {"faces.detect_max_side": str(cap)}, acknowledged=["faces"]).changed)

    def detect(self, library, detector, photo):
        """`photo` detected as the index detects it in `library`: the face model the runtime builds from its settings
        (MTCNN and the recogniser stood in for), given the library's cap."""
        model = self.runtime.faces(library)
        model.device = "cpu"
        model.mtcnn, model.resnet = detector, recogniser
        return model.detect_and_embed_faces(photo, max_side=self.runtime.settings(library).detect_max_side)

    def detector_name(self, library):
        """The name `library` records a detection under, as the index and Suggest spell it."""
        return face_records.detector_of(self.runtime.faces(library), self.runtime.settings(library).detect_max_side)


class APhotoLargerThanTheCap(ALibrary):
    FULL, FACE = (2000, 1500), (500, 250, 700, 500)

    def setUp(self):
        super().setUp()
        self.photo = picture(os.path.join(self.folder, "quay.jpg"), self.FULL, self.FACE)

    def test_off_the_detector_is_handed_the_picture_as_stored_and_its_box_is_kept(self):
        detector = Detector(self.FULL, self.FACE)
        found = self.detect(self.library("harbour.db"), detector, self.photo)
        self.assertEqual([self.FULL], detector.handed)
        self.assertEqual([list(self.FACE)], [face["box"] for face in found])

    def test_on_it_is_detected_at_the_cap_and_the_face_comes_back_where_it_is_with_the_same_crop_and_vector(self):
        off = self.detect(self.library("harbour.db"), Detector(self.FULL, self.FACE), self.photo)
        detector = Detector(self.FULL, self.FACE)
        on = self.detect(self.library("meadow.db", cap=800), detector, self.photo)
        self.assertEqual([(800, 600)], detector.handed)
        self.assertEqual([list(self.FACE)], [face["box"] for face in on])
        # Cut from the full picture: a crop of the small one would be other pixels, and another vector.
        self.assertEqual(off[0]["crop_image"], on[0]["crop_image"])
        self.assertEqual(off[0]["embedding"], on[0]["embedding"])

    def test_a_face_at_the_edge_is_clamped_to_the_picture(self):
        detector = Detector(self.FULL, (1800, 1000, 2010, 1510))   # MTCNN's boxes run past the edge
        found = self.detect(self.library("meadow.db", cap=800), detector, self.photo)
        self.assertEqual([[1800, 1000, 2000, 1500]], [face["box"] for face in found])


class OtherPictures(ALibrary):
    def test_a_photo_within_the_cap_is_left_as_it_is(self):
        photo = picture(os.path.join(self.folder, "small.jpg"), (600, 450), (100, 100, 200, 220))
        detector = Detector((600, 450), (100, 100, 200, 220))
        found = self.detect(self.library("meadow.db", cap=800), detector, photo)
        self.assertEqual([(600, 450)], detector.handed)
        self.assertEqual([[100, 100, 200, 220]], [face["box"] for face in found])

    def test_a_turned_photo_is_detected_as_stored_and_keeps_its_box_as_stored(self):
        face = (500, 250, 700, 500)
        photo = picture(os.path.join(self.folder, "turned.jpg"), (2000, 1500), face, orientation=6)
        detector = Detector((2000, 1500), face)
        found = self.detect(self.library("meadow.db", cap=800), detector, photo)
        self.assertEqual([(800, 600)], detector.handed, "the detector saw the photo upright, not as stored")
        self.assertEqual([list(face)], [f["box"] for f in found])


class TheDetectorName(ALibrary):
    """faces_detected names a detection by the settings that made it, and Suggest detects again a photo recorded
    under another name (tagpup.store.faces_detected)."""

    def test_off_the_name_is_the_one_the_live_libraries_hold(self):
        library = self.library("harbour.db")
        self.assertIsNone(self.runtime.settings(library).detect_max_side)
        self.assertEqual(LIVE_DETECTOR, self.detector_name(library))

    def test_on_the_cap_is_in_the_name_and_a_photo_detected_without_it_is_detected_again(self):
        library = self.library("harbour.db")
        photo = picture(os.path.join(self.folder, "quay.jpg"), (64, 48), None)
        conn = db.connect(library.path)
        try:
            photo_rows.hold(conn, self.folder)
            photo_rows.add_read(conn, photo, {})
            conn.commit()
            face_records.record_batch(conn, {photo: []}, detector=self.detector_name(library))
        finally:
            conn.close()
        self.set_cap(library, 1600)
        capped = self.detector_name(library)
        self.assertEqual(dict(json.loads(LIVE_DETECTOR), detect_max_side=1600), json.loads(capped))
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            self.assertTrue(faces_detected.detected(conn, photo, LIVE_DETECTOR))
            self.assertFalse(faces_detected.detected(conn, photo, capped), "a photo detected without the cap was not detected again")
        finally:
            conn.close()


class TwoLibrariesDifferingOnlyInTheCap(ALibrary):
    """The owner turns the cap on in meadow and not in harbour, and Suggests in both: one set of face models serves
    them (the cap is each detection's argument), each run is handed its own library's cap, and each records its
    detections under its own name. A run keeps the cap it began with; the next run reads the new one."""

    def setUp(self):
        super().setUp()
        # CLIP stood in for (its weights are not the question here); the face models are the runtime's own.
        self.runtime = Runtime(build_clip=lambda given: types.SimpleNamespace())
        self.handed = []
        run = mock.patch("tagpup.services.suggester.model_for_run",
                         lambda *args, **kwargs: self.handed.append(kwargs["detect_max_side"]) or object())
        run.start()
        self.addCleanup(run.stop)

    def test_they_share_one_face_model_and_a_suggest_in_one_is_not_refused_beside_a_run_in_the_other(self):
        harbour, meadow = self.library("harbour.db"), self.library("meadow.db", cap=2048)
        shared = self.runtime.faces(harbour)
        self.assertIs(shared, self.runtime.faces(meadow))
        running = self.runtime.begin(harbour)
        try:
            self.runtime.begin(meadow).end()        # before, refused: "face models with detect_max_side None"
        finally:
            running.end()
        self.assertEqual([None, 2048], self.handed)
        self.assertIs(shared, self.runtime.faces(harbour), "the face models were let go and built again on the switch")
        self.assertEqual(LIVE_DETECTOR, self.detector_name(harbour))
        self.assertEqual(dict(json.loads(LIVE_DETECTOR), detect_max_side=2048), json.loads(self.detector_name(meadow)))

    def test_a_run_keeps_the_cap_it_began_with_and_the_next_run_reads_the_new_one(self):
        meadow = self.library("meadow.db", cap=1600)
        running = self.runtime.begin(meadow)
        try:
            self.set_cap(meadow, 3000)
            self.runtime.begin(meadow).end()
        finally:
            running.end()
        self.assertEqual([1600, 3000], self.handed)
        self.assertEqual(1, settings.change(meadow, {"faces.detect_max_side": ""}, acknowledged=["faces"]).changed)
        self.assertEqual(LIVE_DETECTOR, self.detector_name(meadow))


@unittest.skipIf(own_home.installed_exiftool() is None, "ExifTool not installed")
class TheCommandLineIndexReadsTheCapToo(ALibrary):
    """The CLI index reads the cap by its own path (tagpup_cli.index), not through the server's Suggest: in a library
    with the cap on, each photo is detected at it and recorded under the capped name; with none, as before."""

    def index(self, library, folder):
        """`tagpup_cli.py index` of `folder` in `library`, CLIP and the face models stood in for; the `max_side` each
        detection was handed."""
        handed = []

        def detect(model, path, max_side=None):
            handed.append(max_side)
            return []
        with mock.patch.object(clip.ClipModel, "_init_model"), \
                mock.patch.object(clip.ClipModel, "embed_picture", return_value=[0.1] * 8), \
                mock.patch.object(face_models.FaceModel, "_init_models"), \
                mock.patch.object(face_models.FaceModel, "detect_and_embed_faces", detect):
            result = CliRunner().invoke(tagpup_cli.cli, ["--db", library.path, "index", folder])
        self.assertEqual(0, result.exit_code, result.output)
        return handed

    def test_the_cap_is_handed_to_each_detection_and_is_in_the_name_it_is_recorded_under(self):
        for name, cap in (("meadow.db", 800), ("harbour.db", None)):
            with self.subTest(cap=cap):
                library = self.library(name, cap=cap)
                folder = os.path.join(self.folder, name)
                os.makedirs(folder)
                picture(os.path.join(folder, "quay.jpg"), (64, 48), None)
                self.assertEqual([cap], self.index(library, folder))
                expected = json.loads(LIVE_DETECTOR)
                if cap is not None:
                    expected["detect_max_side"] = cap
                conn = db.connect(db.readonly_uri(library.path), uri=True)
                try:
                    names = [json.loads(row[0]) for row in conn.execute("SELECT detector FROM faces_detected")]
                finally:
                    conn.close()
                self.assertEqual([expected], names)


if __name__ == "__main__":
    unittest.main()
