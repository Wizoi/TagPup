"""P13: a clip's people are the people its frames show (owner, 2026-11-13, DECISIONS "PEOPLE ON CLIPS ARE TRANSIENT").

A person adds a folder of clips and asks for suggestions. Each clip's frames (five, one ffmpeg pass a clip, whether its
vector was kept or is made now) are looked at as a photo is, and a named person any frame shows is offered once, the chip
saying in how many of the frames looked at; Apply All files them as a click on the chip does and the clip's people row
follows its keywords. Nothing of a face is kept of a clip, and the library refuses one at the source. Frames that cannot
be read -- a file ffmpeg cannot decode, or no ffmpeg -- leave the clip its tags and say why beside them; a cancel part-way
through a clip keeps nothing of it.

The clips are made here by the machine's ffmpeg (skipped without it): five one-second colour segments joined, so each of
the five frames read (at (2i+1)/10 of the clip) is one segment's colour. The stand-in face model reads the colour at the
frame's centre: red shows Rowan, green no one, blue a crowd (Rowan, Ines, Wren, Rowan again), yellow Ines, magenta Rowan
too small to be a face (30 px), each face where a detector would put it on the 64x48 frame; the faces it may keep are the
real model's (scrfd_adaface.new_faces: the 40 px floor on the frame's own pixels, each face's crop cut from the frame).
Rowan is named on a photo of the library, Ines on a photo of 1995, Wren nowhere. CLIP is V3's FramesClip. The app, the routes,
ffmpeg, ExifTool, the journal and the library are real. Names are fictional.

Every test fails on the code before people on clips (the face model was never shown a frame; nothing refused a clip's
face).
"""
import json
import os
import shutil
import sys
import unittest
from collections import Counter
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import damaged_photos  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import test_analyse_only_suggest as base  # noqa: E402
from face_rows import FACE_MODEL  # noqa: E402
from test_suggest_then_apply_all import TOOLS, FramesClip  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core import processes  # noqa: E402
from tagpup.files import images, shares, video_frames  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.ml import scrfd_adaface  # noqa: E402
from tagpup.services import faces as face_records  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.store import faces_detected  # noqa: E402
from tagpup.store import taxonomy as store_taxonomy  # noqa: E402

ROWAN, WREN = base.ROWAN, base.WREN
INES = np.array([0.8, -0.6, 0.0, 0.0], dtype=np.float32)    # at right angles to Rowan and to Wren
ROWAN_TAG, INES_TAG = "People/Rowan Thackeray", "People/Ines Halloran"

PALETTE = {"red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0),
           "magenta": (255, 0, 255)}
#: Four places on the 64x48 frame a face of 40 px -- the floor of a new face (images.is_big_enough) -- may be found at.
AT = ([0, 0, 40, 40], [24, 0, 64, 40], [0, 8, 40, 48], [24, 8, 64, 48])
#: A frame's faces, by its colour: [(box, vector)].
SHOWN = {"red": [(AT[0], ROWAN)], "green": [],
         "blue": [(AT[0], ROWAN), (AT[1], INES), (AT[2], WREN), (AT[3], ROWAN)],   # Rowan twice in one frame
         "yellow": [(AT[0], INES)],
         "magenta": [([0, 0, 30, 30], ROWAN)]}                                    # under the floor
#: Each clip, by the colours of its five segments.
CLIPS = {
    "reel_01.mp4": ("red", "green", "green", "green", "green"),     # Rowan in one frame
    "reel_02.mp4": ("red", "red", "green", "red", "red"),           # Rowan in four
    "reel_03.mp4": ("blue", "green", "green", "green", "green"),    # a crowd in one frame
    "reel_04.mp4": ("green",) * 5,                                  # no one
    "reel_05.mp4": ("yellow",) * 5,                                 # Ines, the clip undated
    "reel_07.mp4": ("magenta",) * 5,                                # Rowan, too small in every frame
}


def make_clip(path, colours):
    """A clip of one-second segments of `colours`, 64x48, joined into one stream as a camera's is."""
    args = [TOOLS.ffmpeg, "-v", "error", "-y"]
    for colour in colours:
        args += ["-f", "lavfi", "-i", "color=c=%s:s=64x48:r=10:d=1" % colour]
    joined = "".join("[%d:v]" % n for n in range(len(colours))) + "concat=n=%d:v=1:a=0" % len(colours)
    args += ["-filter_complex", joined, "-pix_fmt", "yuv420p", "-movflags", "+faststart", path]
    processes.run(args, check=True, capture_output=True, timeout=60)


class FrameFaces(base.FakeFaces):
    """The face model: a frame's faces by the colour at its centre (SHOWN); `looked`, the clips whose frames it was
    shown, a frame each; `on_frame`, called with each frame's label before it is looked at. The faces it keeps are the
    real model's (as NewFaces has them): the 40 px floor, and a crop cut from the frame."""
    new_faces = scrfd_adaface.ScrfdAdaFace.new_faces

    def __init__(self):
        super().__init__()
        self.looked = []
        self.on_frame = None

    def detect_and_embed(self, picture):
        clip = picture.path.split(",")[0]
        self.looked.append(os.path.basename(clip))
        if self.on_frame:
            self.on_frame(picture.path)
        upright = picture.upright
        centre = upright.getpixel((upright.width // 2, upright.height // 2))
        colour = min(PALETTE, key=lambda name: sum((a - b) ** 2 for a, b in zip(PALETTE[name], centre)))
        return [{"box": list(box), "embedding": vector.tolist(), "prob": 0.99} for box, vector in SHOWN[colour]]


@unittest.skipIf(TOOLS.problem, "ffmpeg is not installed on this machine")
class AClipsPeopleAreThePeopleItsFramesShow(base.Base):
    """The library holds Pictures/Regatta (Rowan named), Pictures/Archive (Ines named, 1995) and, once a test holds it,
    Pictures/Reel: the clips the test names, read as the index reads them, those it says with their vectors kept."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.made = own_home.for_class(cls, "tagpup_clip_people_").root
        for name, colours in CLIPS.items():
            make_clip(os.path.join(cls.made, name), colours)

    def app_home(self):
        super().app_home()
        self.clip = FramesClip(self.clip.settings)
        self.faces = FrameFaces()

    def seed_library(self, library):
        super().seed_library(library)
        archive = os.path.join(self.home.root, "Pictures", "Archive", "archive_01.jpg")
        damaged_photos.whole_jpeg(archive, seed=21)
        conn = db.connect(library.path)
        try:
            photo_rows.add_read(conn, archive, {"EXIF:DateTimeOriginal": "1995:06:01 10:00:00"})
            store_taxonomy.add_node(conn, INES_TAG)
            store_faces.insert(conn, archive, [0, 0, 100, 100], INES.tobytes(), name="Ines Halloran", prob=0.99,
                               model=FACE_MODEL)
            conn.commit()
        finally:
            conn.close()

    def setUp(self):
        super().setUp()
        self.reel = os.path.join(self.home.root, "Pictures", "Reel")
        self.passes = Counter()
        real = video_frames.frames

        def counted(tools, path, count, *args, **kwargs):
            self.passes[os.path.basename(path)] += 1
            return real(tools, path, count, *args, **kwargs)
        patcher = mock.patch.object(video_frames, "frames", counted)
        patcher.start()
        self.addCleanup(patcher.stop)

    def hold(self, names, kept=(), broken=()):
        """Pictures/Reel holds the clips `names` (and `broken`, files no decoder reads), each read as the index reads it;
        those of `kept` and `broken` with the vector of their frames kept. The library as it is now is `before`."""
        os.makedirs(self.reel, exist_ok=True)
        for name in names:
            shutil.copyfile(os.path.join(self.made, name), os.path.join(self.reel, name))
        for name in broken:
            with open(os.path.join(self.reel, name), "wb") as handle:
                handle.write(b"not a picture of anything " * 64)
        library_actions.record_added(self.library, [self.reel])
        vector = np.array(base.vector_of("frame_00"), dtype=np.float32).tobytes()
        conn = db.connect(self.library.path)
        try:
            self.ids = {name: photo_rows.add_read(conn, os.path.join(self.reel, name), {})
                        for name in list(names) + list(broken)}
            for name in list(kept) + list(broken):
                path = os.path.join(self.reel, name)
                stamp = shares.read_stamp(path)
                store_embeddings.put(conn, path, store_embeddings.model_key(**self.clip.settings), stamp[0], stamp[1],
                                     vector, None, frames=[vector] * 4)
            conn.commit()
        finally:
            conn.close()
        self.before = base.library_dump.dump(self.library.path)

    def people(self, entry):
        return sorted((person["name"], person.get("frames")) for person in entry["people"])

    def face_rows_of_clips(self):
        marks = ", ".join("?" * len(self.ids))
        return [self.rows("SELECT COUNT(*) FROM %s WHERE photo_id IN (%s)" % (table, marks), tuple(self.ids.values()))
                [0][0] for table in ("faces", "faces_detected")]

    def raw_of(self, name):
        row = self.rows("SELECT raw, error FROM suggestions WHERE photo_id = ?", (self.ids[name],))
        self.assertEqual(1, len(row), "the clip's suggestion is kept")
        return json.loads(row[0][0]), row[0][1]

    def test_a_person_seen_in_one_frame_or_four_is_offered_once_and_nothing_of_a_face_is_kept(self):
        self.hold(list(CLIPS), kept=["reel_02.mp4"])
        faces_before = self.rows("SELECT COUNT(*) FROM faces")[0][0]
        self.start(self.reel)
        offered = self.suggested(self.reel)
        self.assertEqual(sorted(CLIPS), sorted(offered))
        for name, entry in offered.items():
            self.assertNotIn("error", entry, entry)
            self.assertIn("Trips/Regatta", [t["tag"] for t in entry["tags"]], "its tags stay offered beside its people")
        self.assertEqual([(ROWAN_TAG, {"seen": 1, "of": 5})], self.people(offered["reel_01.mp4"]), "seen once")
        self.assertEqual([(ROWAN_TAG, {"seen": 4, "of": 5})], self.people(offered["reel_02.mp4"]), "seen in four")
        self.assertEqual([(INES_TAG, {"seen": 1, "of": 5}), (ROWAN_TAG, {"seen": 1, "of": 5})],
                         self.people(offered["reel_03.mp4"]), "a crowd: each named person once, Wren no one")
        self.assertEqual([], self.people(offered["reel_04.mp4"]), "frames with no face offer no one")
        self.assertEqual([(INES_TAG, {"seen": 5, "of": 5})], self.people(offered["reel_05.mp4"]),
                         "an undated clip is compared with every year of Ines's faces")
        self.assertEqual([], self.people(offered["reel_07.mp4"]), "a face under 40 px of the frame offers no one")
        self.assertEqual({name: 5 for name in CLIPS}, dict(Counter(self.faces.looked)), "every frame looked at")
        self.assertEqual({name: 1 for name in CLIPS}, dict(self.passes), "one ffmpeg pass a clip, kept vector or not")
        self.assertEqual([0, 0], self.face_rows_of_clips(), "no face and no detection of a clip")
        self.assertEqual(faces_before, self.rows("SELECT COUNT(*) FROM faces")[0][0])

    def test_apply_all_files_the_person_a_clip_shows_and_its_people_row_follows(self):
        self.hold(["reel_02.mp4"])
        self.start(self.reel)
        reply = self.post("/folder/auto-apply", {"folder_path": self.reel})
        self.assertEqual(200, reply.status_code, reply.data)
        clip = os.path.join(self.reel, "reel_02.mp4")
        self.assertIn(ROWAN_TAG, base.tags_in(clip), "filed by the path of their node, as a click on the chip files")
        rowan = self.rows("SELECT id FROM tag_taxonomy WHERE tag = ?", (ROWAN_TAG,))[0][0]
        self.assertEqual([("keyword", rowan)], self.rows(
            "SELECT source, tag_id FROM photo_people WHERE photo_id = ?", (self.ids["reel_02.mp4"],)),
            "the clip's people row follows its keywords")
        self.assertEqual([0, 0], self.face_rows_of_clips())

    def test_a_clip_ffmpeg_cannot_read_is_offered_its_tags_and_says_why_beside_them(self):
        self.hold([], broken=["reel_06.mp4"])
        self.start(self.reel)
        entry = self.suggested(self.reel)["reel_06.mp4"]
        self.assertNotIn("error", entry, "its kept vector still offers tags: no error the next run would retry")
        self.assertIn("Trips/Regatta", [t["tag"] for t in entry["tags"]])
        self.assertEqual([], entry["people"])
        raw, error = self.raw_of("reel_06.mp4")
        self.assertIsNone(error)
        self.assertTrue(raw.get("faces_unread"), "why its people were not read, beside its tags")
        self.assertEqual([0, 0], self.face_rows_of_clips())

    def test_with_no_ffmpeg_a_clip_whose_vector_is_kept_is_offered_its_tags_and_says_why(self):
        self.hold(["reel_02.mp4"], kept=["reel_02.mp4"])
        with mock.patch.object(config, "locate_ffmpeg", return_value=config.Ffmpeg(None, None, config.FFMPEG_MISSING)):
            self.start(self.reel)
        entry = self.suggested(self.reel)["reel_02.mp4"]
        self.assertNotIn("error", entry, entry)
        self.assertIn("Trips/Regatta", [t["tag"] for t in entry["tags"]])
        self.assertEqual([], entry["people"])
        raw, error = self.raw_of("reel_02.mp4")
        self.assertIsNone(error)
        self.assertIn("ffmpeg", raw.get("faces_unread", ""))
        self.assertEqual([], self.faces.looked)

    def test_a_run_cancelled_part_way_through_a_clips_frames_keeps_nothing_of_it(self):
        self.hold(["reel_02.mp4"], kept=["reel_02.mp4"])
        self.faces.on_frame = lambda label: suggestion_jobs.runs_for(self.library).cancel(self.reel)
        self.start(self.reel)
        self.assertEqual("cancelled", self.status(self.reel)["status"])
        self.assertTrue(self.faces.looked, "cancelled while its frames were looked at")
        after = base.library_dump.dump(self.library.path)
        # The words a run compares with are made ready, and cached, before its first photo: the run's, not the clip's.
        for table in set(self.before) - {"tag_embeddings"}:
            self.assertEqual(self.before[table], after[table], "the cancelled run kept something in %s" % table)

    def test_the_library_refuses_a_face_or_a_detection_on_a_clip_it_has_no_row_for_and_writes_nothing(self):
        # The folder is the library's and the clip not read yet: no row, so no kind of a row to ask. The refusal is by
        # what the file is (its name, images.kind_of), before a row is made -- one made first was of kind 'photo', and
        # the face went through.
        os.makedirs(self.reel)
        clip = os.path.join(self.reel, "reel_01.mp4")
        shutil.copyfile(os.path.join(self.made, "reel_01.mp4"), clip)
        library_actions.record_added(self.library, [self.reel])
        self.before = base.library_dump.dump(self.library.path)
        face = {"box": [0, 0, 40, 40], "embedding": ROWAN.tolist(), "prob": 0.99}
        self.assertEqual(0, face_records.record_detected(self.library.path, clip, [face], model=FACE_MODEL),
                         "Suggest's and the index's door refuses it")
        conn = db.connect(self.library.path)
        try:
            with self.assertRaises(ValueError):
                store_faces.insert(conn, clip, [0, 0, 40, 40], ROWAN.tobytes(), model=FACE_MODEL, prob=0.99,
                                   kind=images.kind_of(clip))
            with self.assertRaises(ValueError):
                faces_detected.record(conn, clip, "scrfd", shares.read_stamp(clip), 1, kind=images.kind_of(clip))
            conn.commit()
        finally:
            conn.close()
        self.assert_library_unchanged()


if __name__ == "__main__":
    unittest.main()
