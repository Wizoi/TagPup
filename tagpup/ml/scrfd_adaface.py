"""Faces found by SCRFD 10g and made into vectors by AdaFace IR-101, all in torch (docs/AI_PIPELINE.md, phase 3;
DECISIONS 2026-10-18, "PHASE 3 FACE MODELS").

The detector looks at each photo twice, fitted into a square of 640 and of 1280 pixels, and the faces of the two are
merged: the small faces are found at 1280, the large ones at 640. Each face keeps its five landmarks (the eyes, the
nose, the corners of the mouth), from which it is aligned onto the 112 x 112 template the recogniser was trained on;
the recogniser runs in half precision on the card, in fixed chunks. A face's vector is given normalized, and its length
before (the norm) beside it: AdaFace's measure of how recognisable the face is.

Built like tagpup.ml.faces.FaceModel, by tagpup.runtime only; nothing builds it yet (phase 3's next merge does).

The weights are three files in torch's checkpoints folder (facenet-pytorch keeps its own there): the detector traced
once for each square (TorchScript: the converted model cannot be traced for every size at once), and AdaFace's
safetensors. Nothing downloads them: the detector is made by conversion (scripts/make_scrfd_torchscript.py), and a
missing file is said in a sentence naming each file, the folder and how to make it (missing_weights).
"""
import logging
import os
import threading

import numpy as np
import torch
from PIL import Image
from torchvision.ops import nms

from tagpup.files import images
from tagpup.ml import free_device_memory, gpu, refuse_in_tests
from tagpup.ml.faces import SMALLEST_FACE, NotDetected

logger = logging.getLogger("tagpup_cli.faces")

#: The squares the detector looks at a photo in, each side in pixels.
SCALES = (640, 1280)
#: SCRFD's three strides, an output each of score, box and landmarks, two anchors a place.
STRIDES = (8, 16, 32)
ANCHORS = 2
#: The score a face needs: insightface's own default for SCRFD.
THRESHOLD = 0.5
#: Two boxes overlapping more than this (intersection over union) are one face, the higher score kept.
OVERLAP = 0.4
#: Faces made into vectors at a time: a fixed size, so the card's memory does not follow the crowd in a photo.
CHUNK = 32
#: The five points of a face aligned for ArcFace-style recognisers, in the 112 x 112 crop.
TEMPLATE = ((38.2946, 51.6963), (73.5318, 51.5014), (56.0252, 71.7366), (41.5493, 92.3655), (70.7299, 92.2041))
ALIGNED = 112

DETECTOR_FILE = "scrfd_10g_%d.pt"
RECOGNISER_FILE = "adaface_ir101_webface12m.safetensors"
RECOGNISER_SOURCE = "https://huggingface.co/minchul/cvlface_adaface_ir101_webface12m (model.safetensors)"
CONVERTER = "scripts/make_scrfd_torchscript.py"


def weights_folder():
    """torch's checkpoints folder, where facenet-pytorch keeps its weights too."""
    return os.path.join(os.path.dirname(torch.hub.get_dir()), "checkpoints")


def missing_weights(folder):
    """A sentence naming the weight files not in `folder` and how to make each, or None when all are there."""
    detectors = [DETECTOR_FILE % side for side in SCALES if not os.path.isfile(os.path.join(folder, DETECTOR_FILE % side))]
    recogniser = not os.path.isfile(os.path.join(folder, RECOGNISER_FILE))
    if not detectors and not recogniser:
        return None
    lines = ["The face models' weights are not all in %s." % folder]
    if detectors:
        command = " ".join("%d %s" % (side, os.path.join(folder, DETECTOR_FILE % side)) for side in SCALES)
        lines.append("Missing %s: make them from insightface's buffalo_l det_10g.onnx, in a Python with onnx and "
                     "onnx2torch, with: python %s <det_10g.onnx> %s" % (", ".join(detectors), CONVERTER, command))
    if recogniser:
        lines.append("Missing %s: download it from %s and save it under that name."
                     % (RECOGNISER_FILE, RECOGNISER_SOURCE))
    return " ".join(lines)


def squares(img):
    """{side: (canvas, scale back to `img`)} for each of SCALES: `img` (RGB, as images.opened gives it) scaled so that
    its long side is the square's, at the top left of a black square canvas, as an array of RGB bytes. The largest
    square first, each smaller one scaled from the one before (on the processor, a third of the time of scaling each
    from the full picture), and Pillow's reducing gap as the detector's cap uses it (faces.detection_picture)."""
    found, source = {}, img
    width, height = img.size
    for side in sorted(SCALES, reverse=True):
        scale = side / max(width, height)
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        scaled = source if source.size == size else source.resize(size, Image.BILINEAR, reducing_gap=3.0)
        canvas = Image.new("RGB", (side, side))
        canvas.paste(scaled, (0, 0))
        found[side] = (np.array(canvas), 1.0 / scale)   # writable: torch takes it as it is
        if scale < 1:
            source = scaled
    return found


def decoded(outputs, side, back):
    """The faces of one square's nine outputs (score, box distances and landmarks, for each stride) scoring at least
    THRESHOLD: (boxes n x 4, scores n, landmarks n x 5 x 2), in the picture `back` scales the square to."""
    boxes, scores, points = [], [], []
    for level, stride in enumerate(STRIDES):
        score = np.asarray(outputs[level], dtype=np.float32).reshape(-1)
        distance = np.asarray(outputs[level + 3], dtype=np.float32).reshape(-1, 4) * stride
        marks = np.asarray(outputs[level + 6], dtype=np.float32).reshape(-1, 10) * stride
        places = side // stride
        centres = np.stack(np.mgrid[:places, :places][::-1], axis=-1).reshape(-1, 2).astype(np.float32) * stride
        centres = np.repeat(centres, ANCHORS, axis=0)
        keep = score >= THRESHOLD
        c, d = centres[keep], distance[keep]
        boxes.append(np.concatenate([c - d[:, :2], c + d[:, 2:]], axis=1))
        scores.append(score[keep])
        points.append(marks[keep].reshape(-1, 5, 2) + c[:, None, :])
    return np.concatenate(boxes) * back, np.concatenate(scores), np.concatenate(points) * back


def merged(found):
    """One list of faces from the faces of each square ((boxes, scores, landmarks) each): a face found at both is kept
    once, at its higher score. The faces in order of score, the highest first."""
    boxes = np.concatenate([f[0] for f in found])
    scores = np.concatenate([f[1] for f in found])
    points = np.concatenate([f[2] for f in found])
    if not len(scores):
        return []
    kept = nms(torch.from_numpy(boxes).float(), torch.from_numpy(scores).float(), OVERLAP).tolist()
    return [(boxes[i], float(scores[i]), points[i]) for i in kept]


def similarity(source, target):
    """The 2 x 3 similarity (turn, scale, shift) taking the points `source` nearest to `target` by least squares
    (Umeyama's)."""
    source, target = np.asarray(source, dtype=np.float64), np.asarray(target, dtype=np.float64)
    source_mean, target_mean = source.mean(axis=0), target.mean(axis=0)
    s, t = source - source_mean, target - target_mean
    u, singular, vt = np.linalg.svd(t.T @ s / len(source))
    flip = np.eye(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        flip[1, 1] = -1
    turn = u @ flip @ vt
    scale = np.trace(np.diag(singular) @ flip) / ((s ** 2).sum() / len(source))
    return np.hstack([scale * turn, (target_mean - scale * turn @ source_mean)[:, None]])


def aligned(img, landmarks):
    """The face at `landmarks` (five points in `img`) turned and scaled onto TEMPLATE: a 112 x 112 RGB array."""
    matrix = np.vstack([similarity(landmarks, TEMPLATE), [0.0, 0.0, 1.0]])
    inverse = np.linalg.inv(matrix)[:2].reshape(-1)   # Pillow's transform maps each output pixel back into `img`
    return np.asarray(img.transform((ALIGNED, ALIGNED), Image.AFFINE, tuple(inverse), Image.BILINEAR))


class ScrfdPicture:
    """A photo made ready for the detector on the processor (ScrfdAdaFace.picture): `stored`, the picture as stored --
    the coordinates face boxes are kept in, and what faces are aligned from --, `squares`, {side: (canvas, scale back
    to `stored`)}, and `path`, the photo's, for the log."""

    def __init__(self, stored, squares, path):
        self.stored, self.squares, self.path = stored, squares, path


class ScrfdAdaFace:
    """The detector at its two squares and the recogniser, loaded the first time either is used."""

    def __init__(self, device=None, folder=None):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.folder = folder or weights_folder()
        #: {side: the traced detector for that square}, and the recogniser.
        self.detectors = None
        self.recogniser = None
        self._init_lock = threading.Lock()
        #: The process's turn on the graphics card (tagpup.ml.gpu), which the runtime hands it; this process's own,
        #: card(), when none was.
        self.gpu = None

    def load(self):
        """Load the models now, if they are not loaded yet."""
        self._init_models()

    def loaded(self):
        """Are the weights in memory now?"""
        return self.detectors is not None or self.recogniser is not None

    def unload(self):
        """Let the weights go, and the card's memory they held. Used again, they load again."""
        with self._init_lock:
            if not self.loaded():
                return
            self.detectors = self.recogniser = None
        logger.info("Unloaded the face models (SCRFD, AdaFace).")
        free_device_memory()

    def _init_models(self):
        """Load the models once, under a lock (a pool shares one model); on the graphics card only with this process's
        turn on it (tagpup.ml.gpu), waited for before the lock. A weight file missing raises its sentence before
        anything is loaded."""
        if self.detectors is not None:
            return
        missing = missing_weights(self.folder)
        if missing:
            raise FileNotFoundError(missing)
        if gpu.on_the_card(self.device):
            (self.gpu or gpu.card()).ensure("loading the face models")
        with self._init_lock:
            if self.detectors is None:
                self._load_models()

    def _load_models(self):
        refuse_in_tests("The face models (SCRFD, AdaFace)")
        from safetensors.torch import load_file
        logger.info("Loading face detection (SCRFD 10g) and recognition (AdaFace IR-101) on %s...", self.device.upper())
        detectors = {side: torch.jit.load(os.path.join(self.folder, DETECTOR_FILE % side), map_location=self.device).eval()
                     for side in SCALES}
        state = load_file(os.path.join(self.folder, RECOGNISER_FILE))
        recogniser = ir101()
        recogniser.load_state_dict({key[len("model.net."):]: value for key, value in state.items()}, strict=True)
        recogniser = recogniser.eval().to(self.device)
        self.recogniser = recogniser.half() if self.device == "cuda" else recogniser
        self.detectors = detectors
        logger.info("Face models loaded.")

    def detect_and_embed_faces(self, img_path):
        """The faces of the photo at `img_path` (detect_and_embed)."""
        return self.detect_and_embed(self.picture(img_path))

    def picture(self, img_path):
        """The photo made ready for the detector, on the processor alone (ScrfdPicture): decoded as stored, a turned
        photo's boxes being kept in the picture as stored (tagpup.files.images), and fitted into each square.
        NotDetected when the file is gone or its picture does not decode."""
        if not os.path.exists(img_path):
            return NotDetected()
        try:
            img = images.opened(img_path, upright=False)
            return ScrfdPicture(img, squares(img), img_path)
        except Exception as e:
            logger.error("Error processing faces in %s: %s", img_path, e)
            return NotDetected()

    def detect_and_embed(self, picture):
        """The faces of a picture made ready (ScrfdPicture), the highest score first, each a dict of its `box` (in the
        picture as stored, clamped to it, at least SMALLEST_FACE a side), `prob` (its score), `landmarks` (five
        points), `embedding` (the vector, normalized) and `norm` (the vector's length before). NotDetected handed in is
        handed back, and given when a model fails."""
        if isinstance(picture, NotDetected):
            return picture
        self._init_models()
        # The models as they are now: an unload (the idle release) nulls the attributes, and this call finishes with
        # the ones it started with.
        detectors, recogniser = self.detectors, self.recogniser
        try:
            faces = self._detected(detectors, picture)
            if not faces:
                return []
            vectors = self.embed(recogniser, [aligned(picture.stored, face["landmarks"]) for face in faces])
            for face, (vector, norm) in zip(faces, vectors):
                face["embedding"], face["norm"] = vector, norm
            return faces
        except Exception as e:
            logger.error("Error processing faces in %s: %s", picture.path, e)
            return NotDetected()

    def _detected(self, detectors, picture):
        width, height = picture.stored.size
        found = []
        with torch.inference_mode():
            for side, (canvas, back) in picture.squares.items():
                blob = (torch.from_numpy(canvas).to(self.device).permute(2, 0, 1)[None].float() - 127.5) / 128.0
                found.append(decoded([output.float().cpu().numpy() for output in detectors[side](blob)], side, back))
        faces = []
        for box, score, points in merged(found):
            x1, y1 = max(0, int(box[0])), max(0, int(box[1]))
            x2, y2 = min(width, int(box[2])), min(height, int(box[3]))
            if (x2 - x1) < SMALLEST_FACE or (y2 - y1) < SMALLEST_FACE:
                continue
            faces.append({"box": [x1, y1, x2, y2], "prob": score, "landmarks": points.tolist()})
        return faces

    def embed(self, recogniser, crops):
        """[(vector normalized, its length before)] of each aligned face in `crops` (112 x 112 RGB arrays), CHUNK at a
        time."""
        vectors = []
        with torch.inference_mode():
            for start in range(0, len(crops), CHUNK):
                batch = torch.from_numpy(np.stack(crops[start:start + CHUNK])).to(self.device).permute(0, 3, 1, 2).float()
                batch = (batch / 255.0 - 0.5) / 0.5
                if self.device == "cuda":
                    batch = batch.half()
                found = recogniser(batch).float()
                norms = found.norm(dim=-1, keepdim=True)
                vectors += zip((found / norms).cpu().numpy().tolist(), norms[:, 0].cpu().numpy().tolist())
        return vectors


def ir101():
    """AdaFace's IR-101 (CVLFace's iresnet Backbone, mode "ir", 112 input), written out to load the weights into: its
    keys are checked one for one on loading (strict)."""
    from torch import nn

    class Block(nn.Module):
        def __init__(self, cin, depth, stride):
            super().__init__()
            self.shortcut_layer = (nn.MaxPool2d(1, stride) if cin == depth else
                                   nn.Sequential(nn.Conv2d(cin, depth, 1, stride, bias=False), nn.BatchNorm2d(depth)))
            self.res_layer = nn.Sequential(nn.BatchNorm2d(cin), nn.Conv2d(cin, depth, 3, 1, 1, bias=False),
                                           nn.BatchNorm2d(depth), nn.PReLU(depth),
                                           nn.Conv2d(depth, depth, 3, stride, 1, bias=False), nn.BatchNorm2d(depth))

        def forward(self, x):
            return self.res_layer(x) + self.shortcut_layer(x)

    class Backbone(nn.Module):
        def __init__(self):
            super().__init__()
            self.input_layer = nn.Sequential(nn.Conv2d(3, 64, 3, 1, 1, bias=False), nn.BatchNorm2d(64), nn.PReLU(64))
            body = []
            for cin, depth, units in ((64, 64, 3), (64, 128, 13), (128, 256, 30), (256, 512, 3)):
                body += [Block(cin, depth, 2)] + [Block(depth, depth, 1) for _ in range(units - 1)]
            self.body = nn.Sequential(*body)
            self.output_layer = nn.Sequential(nn.BatchNorm2d(512), nn.Dropout(0.4), nn.Flatten(),
                                              nn.Linear(512 * 7 * 7, 512), nn.BatchNorm1d(512, affine=False))

        def forward(self, x):
            return self.output_layer(self.body(self.input_layer(x)))

    return Backbone()
