"""The phase 3 speed check (docs/AI_PIPELINE.md, phase 3, "First, a speed check"): candidate face detectors and
recognisers timed photo by photo on the phase 0 sample, with their card memory and the faces each finds beside MTCNN.
Numbers only: nothing it prints or writes names a photo or a person. A spike's instrument, not the app's code: the
models here are loaded by this script alone, never by tagpup.

    set TAGPUP_HOME=<the owner's home>        (the census reads the library through the home's machine map)
    .venv/Scripts/python.exe scripts/measure_face_models.py sample --source data/photo_index.db --work <folder>
    <sandbox venv>/python.exe scripts/measure_face_models.py run --work <folder> --models <folder>
    .venv/Scripts/python.exe scripts/measure_face_models.py report --work <folder>

* `sample` (the main venv): the census of the library, read-only (sandbox.census), and phase 0's sample drawn again
  (sandbox.choose_sample, the same size, worst and seed), its photos copied into `<work>/photos` by number, with each
  one's stratum, megapixels and stored faces (box, named, excluded: services.inspect.faces_in_photo) and the library's
  face settings (services.settings.read, which writes nothing) in `<work>/plan.json`. The library's strata go with it,
  to weigh the pass estimate.
* `run` (a sandbox venv with onnxruntime-gpu and OpenCV beside the main venv's torch; owner, 2026-10-18: that runtime
  in a sandbox only): each candidate in a process of its own (`one`), in order, one at a time, holding the owner's turn
  on the card (tagpup.ml.gpu) while it has a model there; nvidia-smi read every 250 ms meanwhile, so each candidate's
  card memory is its own peak over what the card held before it started. Before each, the card is looked at: another
  process holding the turn, or the card busy, stops the run. A candidate that fails (its traceback printed) does not
  stop the ones after it; the failures are named at the end.
  - A detector: each photo decoded as the indexer decodes it for faces (tagpup.files.images.opened, as stored), then
    detected, landmarks included where the model gives them; the time of each, the card synchronised. A warm-up photo
    first, untimed.
  - A recogniser: on the faces one detector found (`--faces-from`), each face aligned (a similarity transform of its
    five landmarks onto ArcFace's 112 template; FaceNet: the crop resized as the indexer does it), then the photo's
    faces in one batch; the time of each. A check that the model is read right: a face and its mirror image should be
    alike, two faces of different photos not.
* `report`: per candidate and stratum, the mean seconds, the faces found, the MTCNN faces it finds too (IoU 0.5 and
  0.3, sandbox.pairs), the named stored faces it finds, and the pass estimate: the library's photos in each stratum
  times decode + detector + alignment + recogniser there.
"""
import argparse
import json
import os
import random
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sandbox  # noqa: E402

import _root  # noqa: E402,F401
from tagpup.core import processes  # noqa: E402

#: The detectors: name -> (kind, model file under --models, the long side the picture is fitted into, or None for the
#: picture as it is, the score a face needs). The scores are each model's project's own default.
DETECTORS = {
    "mtcnn": ("mtcnn", None, None, None),
    "scrfd_10g_640": ("scrfd", "buffalo_l/det_10g.onnx", 640, 0.5),
    "scrfd_10g_1280": ("scrfd", "buffalo_l/det_10g.onnx", 1280, 0.5),
    "scrfd_10g_1920": ("scrfd", "buffalo_l/det_10g.onnx", 1920, 0.5),
    "scrfd_10g_1280_torch": ("scrfd_torch", "buffalo_l/det_10g.onnx", 1280, 0.5),
    "scrfd_2.5g_640": ("scrfd", "buffalo_m/buffalo_m/det_2.5g.onnx", 640, 0.5),
    "scrfd_2.5g_1280": ("scrfd", "buffalo_m/buffalo_m/det_2.5g.onnx", 1280, 0.5),
    "yunet_640": ("yunet", "yunet.onnx", 640, 0.9),
    "yunet_1280": ("yunet", "yunet.onnx", 1280, 0.9),
    "yolov8n_640": ("yolo", "yolov8n-face-lindevs.onnx", 640, 0.5),
}

#: The recognisers: name -> (kind, model file under --models).
RECOGNISERS = {
    "facenet": ("facenet", None),
    "arcface_r50": ("arcface", "buffalo_l/w600k_r50.onnx"),
    "adaface_ir101": ("adaface", "adaface_ir101_wf12m.safetensors"),
}

#: The five points of a face aligned for ArcFace-style recognisers, in the 112 x 112 crop: the eyes (as seen), the
#: nose, the corners of the mouth.
TEMPLATE = ((38.2946, 51.6963), (73.5318, 51.5014), (56.0252, 71.7366), (41.5493, 92.3655), (70.7299, 92.2041))

#: The indexer's smallest face, either side in pixels (tagpup.ml.faces.FaceModel.detect_and_embed).
SMALLEST = 15

#: The overlaps a detector's box is counted as MTCNN's face at: the carrying rule's, and a looser one, since detectors
#: draw a face's box to different edges.
OVERLAPS = (0.5, 0.3)


# ---- What the numbers mean: pure, tested ---------------------------------------------------------------------

def nms(boxes, scores, threshold):
    """The indices kept by greedy non-maximum suppression of `boxes` (n x 4, x1 y1 x2 y2), the highest score first."""
    import numpy as np
    order = np.argsort(-np.asarray(scores))
    boxes = np.asarray(boxes, dtype=float)
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    kept = []
    while order.size:
        first = order[0]
        kept.append(int(first))
        rest = order[1:]
        x1 = np.maximum(boxes[first, 0], boxes[rest, 0])
        y1 = np.maximum(boxes[first, 1], boxes[rest, 1])
        x2 = np.minimum(boxes[first, 2], boxes[rest, 2])
        y2 = np.minimum(boxes[first, 3], boxes[rest, 3])
        shared = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
        overlap = shared / np.maximum(areas[first] + areas[rest] - shared, 1e-9)
        order = rest[overlap <= threshold]
    return kept


def kept_faces(faces, width, height):
    """`faces` ([x1, y1, x2, y2, score, landmarks]) clamped to the picture, without those under SMALLEST either side:
    the indexer's rule, so every detector is counted alike."""
    kept = []
    for x1, y1, x2, y2, score, landmarks in faces:
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
        if x2 - x1 >= SMALLEST and y2 - y1 >= SMALLEST:
            kept.append([float(x1), float(y1), float(x2), float(y2), float(score), landmarks])
    return kept


def weights(strata, worst):
    """{stratum: the library's photos it stands for}: the census's strata, the worst photos of the sample taken out of
    10 or more (they are its photos), so the pass is the library's mix and not the sample's."""
    found = {name: strata[name]["photos"] for name in ("none", "1-3", "4-9", "10+")}
    found["worst"] = min(worst, found["10+"])
    found["10+"] -= found["worst"]
    return found


def pass_estimate(seconds_by_stratum, weighed):
    """Seconds for the library: each stratum's photos times its mean seconds a photo; None when a stratum the library
    has holds no measured photo."""
    total = 0.0
    for name, photos in weighed.items():
        if not photos:
            continue
        seconds = seconds_by_stratum.get(name)
        if seconds is None:
            return None
        total += photos * seconds
    return total


def agreement(photos, baseline, named_only=False):
    """{stratum and "all": counts} of a detector's `photos` against `baseline` (MTCNN's run, or the stored faces):
    photos, faces found, baseline faces, baseline faces found again at each of OVERLAPS, and the longest side of
    those missed at the first. `named_only`: the baseline is the photo's stored faces, named and not excluded."""
    by_number = {photo["number"]: photo for photo in baseline}
    table = {}
    for photo in photos:
        other = by_number.get(photo["number"])
        if other is None or photo.get("faces") is None:
            continue
        if named_only:
            wanted = [face[0] for face in other.get("stored") or () if face[0] and face[1] and not face[2]]
        else:
            wanted = [face[:4] for face in other.get("faces") or ()]
        found = [face[:4] for face in photo["faces"]]
        matched = {overlap: len(sandbox.pairs(wanted, found, overlap)) for overlap in OVERLAPS}
        missed = set(range(len(wanted))) - set(sandbox.pairs(wanted, found, OVERLAPS[0]))
        for name in (photo["stratum"], "all"):
            row = table.setdefault(name, {"photos": 0, "found": 0, "baseline": 0, "missed_sides": [],
                                          **{"matched_%s" % o: 0 for o in OVERLAPS}})
            row["photos"] += 1
            row["found"] += len(found)
            row["baseline"] += len(wanted)
            for overlap in OVERLAPS:
                row["matched_%s" % overlap] += matched[overlap]
            row["missed_sides"] += [max(wanted[i][2] - wanted[i][0], wanted[i][3] - wanted[i][1]) for i in missed]
    for row in table.values():
        row["missed_sides"] = sandbox.summary(row["missed_sides"])
    return table


def means(photos, fields):
    """{stratum and "all": {field: mean seconds a photo}} of `photos`, each photo's record holding `fields`."""
    table = {}
    for photo in photos:
        for name in (photo["stratum"], "all"):
            row = table.setdefault(name, {field: [] for field in fields})
            for field in fields:
                row[field].append(photo.get(field))
    return {name: {field: sandbox.summary(values).get("mean") for field, values in row.items()}
            for name, row in table.items()}


# ---- sample: the census and phase 0's sample, read-only --------------------------------------------------------

def sample(args):
    from tagpup.core.library import Library
    from tagpup.services import inspect as inspect_service
    from tagpup.services import settings as library_settings

    work = sandbox.outside_the_repository(args.work)
    library = Library(os.path.abspath(args.source))
    print("census (read-only)")
    rows, counts = sandbox.census(library)
    chosen = sandbox.choose_sample(rows, args.sample, args.worst, random.Random(args.seed))
    random.Random(args.seed).shuffle(chosen)
    folder = os.path.join(work, "photos")
    os.makedirs(folder, exist_ok=True)
    photos = []
    for number, row in enumerate(chosen):
        stored = [[face["box"], face["named"], face["excluded"]]
                  for face in inspect_service.faces_in_photo(library, row["id"])["faces"]]
        name = "%04d%s" % (number, os.path.splitext(row["path"])[1].lower())
        shutil.copy2(row["path"], os.path.join(folder, name))
        photos.append({"number": number, "file": name, "stratum": row["stratum"], "faces": row["faces"],
                       "megapixels": row["megapixels"], "stored": stored})
    plan = {"census": sandbox.census_counts(rows, counts), "worst": args.worst, "photos": photos,
            "settings": library_settings.read(library).faces}
    with open(os.path.join(work, "plan.json"), "w", encoding="utf-8") as handle:
        json.dump(plan, handle, indent=1)
    print("%d photos copied, by stratum %s; %d readable in the library" % (
        len(photos), {s: sum(1 for p in photos if p["stratum"] == s) for s in sandbox.STRATA}, counts["readable"]))


# ---- one: a candidate in this process ----------------------------------------------------------------------------

def _ort(path, fixed_shape):
    """An onnxruntime session on the card, failing loudly when the card is not what it runs on. torch is imported
    first: its CUDA and cuDNN libraries are the ones onnxruntime finds."""
    import torch  # noqa: F401
    import onnxruntime as ort
    options = {"cudnn_conv_algo_search": "EXHAUSTIVE" if fixed_shape else "HEURISTIC"}
    session = ort.InferenceSession(path, providers=[("CUDAExecutionProvider", options), "CPUExecutionProvider"])
    if session.get_providers()[0] != "CUDAExecutionProvider":
        raise SystemExit("onnxruntime did not take the card for %s" % path)
    return session


def _fitted(pixels, side, square):
    """`pixels` scaled so that its long side is `side`, on a canvas of `side` x `side` (`square`) or of the next
    multiples of 32: (canvas, scale from the canvas back to the picture)."""
    import cv2
    import numpy as np
    height, width = pixels.shape[:2]
    scale = side / max(width, height)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    resized = cv2.resize(pixels, size, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    if square:
        canvas_h = canvas_w = side
    else:
        canvas_h, canvas_w = -(-size[1] // 32) * 32, -(-size[0] // 32) * 32
    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    canvas[:size[1], :size[0]] = resized
    return canvas, 1.0 / scale


class Scrfd:
    """SCRFD with its five landmarks (insightface's det_*.onnx): three strides, two anchors a place. `in_torch`: the
    same file turned into a torch module (onnx2torch) and run by torch alone, the question being whether the detector
    needs a second runtime."""

    def __init__(self, path, side, threshold, in_torch=False):
        self.side, self.threshold, self.in_torch = side, threshold, in_torch
        if in_torch:
            import onnx
            import torch
            from onnx2torch import convert
            self.torch = torch
            # The model loaded, not its path: given a path, onnx2torch reopens a temporary file by name, which
            # Windows refuses while it is open.
            self.model = convert(onnx.load(path)).eval().cuda()
        else:
            self.session = _ort(path, True)
            self.input = self.session.get_inputs()[0].name

    def detect(self, pixels):
        import numpy as np
        canvas, back = _fitted(pixels, self.side, True)
        if self.in_torch:
            torch = self.torch
            with torch.no_grad():
                blob = (torch.from_numpy(canvas).cuda().permute(2, 0, 1)[None].float() - 127.5) / 128.0
                outputs = [output.float().cpu().numpy() for output in self.model(blob)]
        else:
            blob = ((canvas.astype(np.float32) - 127.5) / 128.0).transpose(2, 0, 1)[None]
            outputs = self.session.run(None, {self.input: blob})
        boxes, scores, points = [], [], []
        for level, stride in enumerate((8, 16, 32)):
            score = outputs[level].reshape(-1)
            distance = outputs[level + 3].reshape(-1, 4) * stride
            marks = outputs[level + 6].reshape(-1, 10) * stride
            rows, cols = canvas.shape[0] // stride, canvas.shape[1] // stride
            centres = np.stack(np.mgrid[:rows, :cols][::-1], axis=-1).reshape(-1, 2).astype(np.float32) * stride
            centres = np.repeat(centres, 2, axis=0)
            keep = score >= self.threshold
            c, d = centres[keep], distance[keep]
            boxes.append(np.concatenate([c - d[:, :2], c + d[:, 2:]], axis=1))
            scores.append(score[keep])
            points.append(marks[keep].reshape(-1, 5, 2) + c[:, None, :])
        boxes, scores, points = np.concatenate(boxes) * back, np.concatenate(scores), np.concatenate(points) * back
        return [[*boxes[i], scores[i], points[i].tolist()] for i in nms(boxes, scores, 0.4)] if len(scores) else []


class YuNet:
    """OpenCV's YuNet (cv2.FaceDetectorYN, on the processor: OpenCV's wheel has no CUDA), five landmarks."""

    def __init__(self, path, side, threshold):
        import cv2
        self.cv2 = cv2
        self.side = side
        self.model = cv2.FaceDetectorYN.create(path, "", (side, side), threshold, 0.3, 5000)

    def detect(self, pixels):
        import numpy as np
        canvas, back = _fitted(pixels, self.side, False)
        canvas = np.ascontiguousarray(canvas[:, :, ::-1])   # OpenCV's order
        self.model.setInputSize((canvas.shape[1], canvas.shape[0]))
        _, found = self.model.detect(canvas)
        faces = []
        for row in (found if found is not None else ()):
            x, y, w, h = row[:4] * back
            faces.append([x, y, x + w, y + h, float(row[14]), (row[4:14].reshape(5, 2) * back).tolist()])
        return faces


class Yolo:
    """YOLOv8n-face (lindevs' export: boxes and a score, no landmarks), 640 square."""

    def __init__(self, path, side, threshold):
        self.session = _ort(path, True)
        self.input = self.session.get_inputs()[0].name
        fixed = self.session.get_inputs()[0].shape[2]
        self.side = fixed if isinstance(fixed, int) else side
        self.threshold = threshold

    def detect(self, pixels):
        import numpy as np
        canvas, back = _fitted(pixels, self.side, True)
        blob = (canvas.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        out = self.session.run(None, {self.input: blob})[0][0].T
        out = out[out[:, 4] >= self.threshold]
        boxes = np.concatenate([out[:, :2] - out[:, 2:4] / 2, out[:, :2] + out[:, 2:4] / 2], axis=1) * back
        return [[*boxes[i], out[i, 4], None] for i in nms(boxes, out[:, 4], 0.45)] if len(out) else []


class Mtcnn:
    """MTCNN as the indexer runs it (tagpup.ml.faces): the library's settings, the picture at full size, its score."""

    def __init__(self, settings):
        from facenet_pytorch import MTCNN
        self.model = MTCNN(keep_all=True, device="cuda", min_face_size=settings["min_face_size"],
                           thresholds=settings["mtcnn_thresholds"])
        self.confidence = settings["confidence_threshold"]

    def detect(self, pixels):
        boxes, probs = self.model.detect(pixels)
        if boxes is None:
            return []
        return [[*box, prob, None] for box, prob in zip(boxes, probs) if prob >= self.confidence]


def _ir101():
    """AdaFace's IR-101 (CVLFace's iresnet Backbone, mode "ir", 112 input), written out here to load the weights into:
    its keys are checked one for one on loading (strict)."""
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


def aligned(pixels, landmarks):
    """The face at `landmarks` (five points) turned and scaled onto TEMPLATE: a 112 x 112 RGB array."""
    import cv2
    import numpy as np
    matrix = cv2.estimateAffinePartial2D(np.asarray(landmarks, dtype=np.float32), np.asarray(TEMPLATE, np.float32),
                                         method=cv2.LMEDS)[0]
    return cv2.warpAffine(pixels, matrix, (112, 112), borderValue=0.0)


class Recogniser:
    """A recogniser's two steps for one photo's faces: `prepare` (alignment, or FaceNet's crop) and `embed` (one
    batch, on the card)."""

    def __init__(self, kind, path):
        import torch
        self.kind, self.torch = kind, torch
        if kind == "facenet":
            from facenet_pytorch import InceptionResnetV1
            self.model = InceptionResnetV1(pretrained="vggface2", device="cuda").eval().half()
        elif kind == "adaface":
            from safetensors.torch import load_file
            state = {key[len("model.net."):]: value for key, value in load_file(path).items()}
            self.model = _ir101()
            self.model.load_state_dict(state, strict=True)
            self.model = self.model.eval().cuda().half()
        else:
            self.session = _ort(path, False)
            self.input = self.session.get_inputs()[0].name

    def prepare(self, pixels, faces):
        import numpy as np
        from PIL import Image
        if self.kind == "facenet":
            picture = Image.fromarray(pixels)
            return np.stack([np.asarray(picture.crop(tuple(int(v) for v in face[:4])).resize((160, 160), Image.BILINEAR))
                             for face in faces])
        return np.stack([aligned(pixels, face[5]) for face in faces])

    def embed(self, crops):
        import numpy as np
        torch = self.torch
        if self.kind == "arcface":
            blob = ((crops.astype(np.float32) - 127.5) / 127.5).transpose(0, 3, 1, 2)
            return self.session.run(None, {self.input: np.ascontiguousarray(blob)})[0]
        with torch.no_grad():
            batch = torch.from_numpy(crops).cuda().permute(0, 3, 1, 2).float()
            batch = (batch - 127.5) / 128.0 if self.kind == "facenet" else (batch / 255.0 - 0.5) / 0.5
            found = self.model(batch.half()).float()
            torch.cuda.synchronize()
            return found.cpu().numpy()


def detector_of(name, models, settings):
    kind, file, side, threshold = DETECTORS[name]
    if kind == "mtcnn":
        return Mtcnn(settings)
    path = os.path.join(models, file)
    if kind == "scrfd_torch":
        return Scrfd(path, side, threshold, in_torch=True)
    return {"scrfd": Scrfd, "yunet": YuNet, "yolo": Yolo}[kind](path, side, threshold)


def one(args):
    """One candidate over every photo of the plan, in this process, holding the card's turn."""
    import numpy as np
    import torch
    from tagpup.files import images
    from tagpup.ml import gpu

    with open(os.path.join(args.work, "plan.json"), encoding="utf-8") as handle:
        plan = json.load(handle)
    folder = os.path.join(args.work, "photos")
    hold = gpu.card().hold("measuring face models (phase 3 speed check, %s)" % args.candidate, report=print)
    try:
        started = time.perf_counter()
        is_detector = args.candidate in DETECTORS
        if is_detector:
            model = detector_of(args.candidate, args.models, plan["settings"])
            faces_of = None
        else:
            kind, file = RECOGNISERS[args.candidate]
            model = Recogniser(kind, os.path.join(args.models, file) if file else None)
            source = "mtcnn" if kind == "facenet" else args.faces_from
            with open(os.path.join(args.work, "%s.json" % source), encoding="utf-8") as handle:
                faces_of = {p["number"]: p["faces"] for p in json.load(handle)["photos"]}
        loaded = time.perf_counter() - started

        def measure(photo):
            began = time.perf_counter()
            pixels = np.asarray(images.opened(os.path.join(folder, photo["file"]), upright=False))
            decoded = time.perf_counter()
            record = {"number": photo["number"], "stratum": photo["stratum"], "decode": decoded - began}
            height, width = pixels.shape[:2]
            if is_detector:
                faces = model.detect(pixels)
                if torch.cuda.is_initialized():   # YuNet runs on the processor and never starts the card
                    torch.cuda.synchronize()
                record["detect"] = time.perf_counter() - decoded
                record["faces"] = kept_faces(faces, width, height)
                return record, None
            faces = [face for face in faces_of[photo["number"]] if kind == "facenet" or face[5] is not None]
            record["faces"] = len(faces)
            if not faces:
                record["align"] = record["recognise"] = 0.0
                return record, None
            crops = model.prepare(pixels, faces)
            prepared = time.perf_counter()
            vectors = model.embed(crops)
            record["align"], record["recognise"] = prepared - decoded, time.perf_counter() - prepared
            return record, (vectors, model.embed(np.ascontiguousarray(crops[:, :, ::-1])))

        measure(plan["photos"][0])   # the warm-up: the card's first kernels, untimed
        if torch.cuda.is_initialized():
            torch.cuda.reset_peak_memory_stats()
        photos, alike, mirrored = [], [], []
        for photo in plan["photos"]:
            record, vectors = measure(photo)
            photos.append(record)
            if vectors is not None:
                a, b = (v / np.linalg.norm(v, axis=1, keepdims=True) for v in vectors)
                mirrored.append(float(np.mean(np.sum(a * b, axis=1))))
                alike.append(a[0])
        others = None
        if len(alike) > 1:
            stacked = np.stack(alike)
            cosines = stacked @ stacked.T
            others = float(cosines[np.triu_indices(len(alike), 1)].mean())
        result = {"candidate": args.candidate, "load_seconds": loaded, "photos": photos,
                  "torch_peak_mib": torch.cuda.max_memory_allocated() / 2 ** 20 if torch.cuda.is_initialized() else 0,
                  "mirror_cosine": sandbox.summary(mirrored).get("mean"), "different_photos_cosine": others,
                  "faces_from": None if is_detector else source}
    finally:
        hold.release()
    with open(os.path.join(args.work, "%s.json" % args.candidate), "w", encoding="utf-8") as handle:
        json.dump(result, handle)
    print("%s: %d photos, loaded in %.1f s" % (args.candidate, len(photos), loaded))


# ---- run: each candidate in turn, its card memory read --------------------------------------------------------

def run(args):
    from tagpup.ml import gpu
    work = sandbox.outside_the_repository(args.work)
    names = args.only.split(",") if args.only else list(DETECTORS) + list(RECOGNISERS)
    memory, failed = {}, []
    path = os.path.join(work, "memory.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            memory = json.load(handle)
    for name in names:
        holder = gpu.read_holder()
        if holder and not processes.is_alive(holder.get("pid")):
            holder = None   # what a process that has ended said of itself (its turn ended with it)
        sampler = sandbox.Sampler(interval=250).start()
        time.sleep(3)
        now = sandbox.window(sampler.gpu, 0, time.time())
        before = now.get("peak_memory_mib")
        if holder or not now.get("samples") or now["mean_utilisation"] > 20:
            sampler.stop()
            raise SystemExit("The card is not free (%s; utilisation %s): stopped before %s." % (
                "held: " + holder.get("what", "?") if holder else "no holder", now.get("mean_utilisation"), name))
        began = time.time()
        sys.stdout.flush()
        # Its streams handed over: a child in a hidden console of its own would write its traceback there, unseen.
        done = processes.run([sys.executable, os.path.abspath(__file__), "one", "--work", work, "--models",
                              args.models, "--candidate", name, "--faces-from", args.faces_from], timeout=args.timeout,
                             stdout=sys.stdout, stderr=sys.stderr)
        ended = time.time()
        sampler.stop()
        card = sandbox.window(sampler.gpu, began, ended)
        memory[name] = {"before_mib": before, "peak_mib": card.get("peak_memory_mib"), "exit": done.returncode,
                        "mean_utilisation": card.get("mean_utilisation"), "seconds": ended - began}
        print("%s: exit %d, card %s -> %s MiB" % (name, done.returncode, before, card.get("peak_memory_mib")))
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(memory, handle, indent=1)
        if done.returncode:
            failed.append(name)   # its traceback is above; the candidates after it do not depend on it
    if failed:
        raise SystemExit("failed: %s" % ", ".join(failed))


# ---- report --------------------------------------------------------------------------------------------------

def report(args):
    with open(os.path.join(args.work, "plan.json"), encoding="utf-8") as handle:
        plan = json.load(handle)
    with open(os.path.join(args.work, "memory.json"), encoding="utf-8") as handle:
        memory = json.load(handle)
    runs = {}
    for name in list(DETECTORS) + list(RECOGNISERS):
        path = os.path.join(args.work, "%s.json" % name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                runs[name] = json.load(handle)
    weighed = weights(plan["census"]["strata"], plan["worst"])
    out = {"weights": weighed, "readable": plan["census"]["readable"], "detectors": {}, "recognisers": {}}
    strata = list(sandbox.STRATA) + ["all"]
    print("library photos by stratum: %s" % weighed)
    print("\n%-16s %6s %6s %7s %6s | %s" % ("detector", "decode", "detect", "peakMiB", "load", "found/MTCNN found@0.5"
                                            " (named found@0.5 of named) by stratum"))
    for name in DETECTORS:
        if name not in runs:
            continue
        photos = runs[name]["photos"]
        timed = means(photos, ("decode", "detect"))
        versus = agreement(photos, runs["mtcnn"]["photos"]) if "mtcnn" in runs else {}
        named = agreement(photos, plan["photos"], named_only=True)
        found = {s: "%d/%d@%d (%d of %d)" % (versus.get(s, {}).get("found", 0), versus.get(s, {}).get("baseline", 0),
                                              versus.get(s, {}).get("matched_0.5", 0), named.get(s, {}).get("matched_0.5", 0),
                                              named.get(s, {}).get("baseline", 0)) for s in strata if s in named}
        seconds = {s: (timed[s]["decode"] or 0) + (timed[s]["detect"] or 0) for s in timed}
        out["detectors"][name] = {"means": timed, "versus_mtcnn": versus, "named": named, "memory": memory.get(name),
                                  "load_seconds": runs[name]["load_seconds"], "torch_peak_mib": runs[name]["torch_peak_mib"],
                                  "decode_and_detect_pass_hours": (pass_estimate(seconds, weighed) or 0) / 3600}
        mem = memory.get(name) or {}
        print("%-16s %6.3f %6.3f %7s %6.1f | %s" % (name, timed["all"]["decode"], timed["all"]["detect"],
                                                     (mem.get("peak_mib") or 0) - (mem.get("before_mib") or 0),
                                                     runs[name]["load_seconds"], found))
        print("%-16s by stratum detect %s; pass decode+detect %.2f h" % (
            "", {s: round(v["detect"], 3) for s, v in timed.items()}, out["detectors"][name]["decode_and_detect_pass_hours"]))
    print("\n%-14s %-16s %6s %8s %7s %8s %8s" % ("recogniser", "faces from", "align", "recog", "peakMiB", "mirror",
                                                 "others"))
    for name in RECOGNISERS:
        if name not in runs:
            continue
        r = runs[name]
        timed = means(r["photos"], ("align", "recognise"))
        mem = memory.get(name) or {}
        detector = out["detectors"].get(r["faces_from"], {}).get("means", {})
        seconds = {s: sum((detector.get(s, {}).get(f) or 0) for f in ("decode", "detect")) +
                   (timed[s]["align"] or 0) + (timed[s]["recognise"] or 0) for s in timed}
        out["recognisers"][name] = {"means": timed, "memory": mem, "faces_from": r["faces_from"],
                                    "mirror_cosine": r["mirror_cosine"], "different_photos_cosine": r["different_photos_cosine"],
                                    "torch_peak_mib": r["torch_peak_mib"], "load_seconds": r["load_seconds"],
                                    "pass_hours": (pass_estimate(seconds, weighed) or 0) / 3600}
        print("%-14s %-16s %6.3f %8.3f %7s %8.3f %8.3f  pass with its detector %.2f h; by stratum recog %s" % (
            name, r["faces_from"], timed["all"]["align"], timed["all"]["recognise"],
            (mem.get("peak_mib") or 0) - (mem.get("before_mib") or 0), r["mirror_cosine"] or 0,
            r["different_photos_cosine"] or 0, out["recognisers"][name]["pass_hours"],
            {s: round(v["recognise"], 3) for s, v in timed.items()}))
    path = os.path.join(args.work, "report.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1)
    print("written %s" % path)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("sample", "run", "one", "report"))
    parser.add_argument("--source", help="sample: the library, opened read-only and never written")
    parser.add_argument("--work", required=True, help="the folder for the copies and the JSON, outside the repository")
    parser.add_argument("--models", help="run: the folder holding the model files")
    parser.add_argument("--only", help="run: these candidates, comma-separated, in this order")
    parser.add_argument("--candidate", help=argparse.SUPPRESS)
    parser.add_argument("--faces-from", default="scrfd_10g_1280", help="run: the detector whose faces the "
                                                                       "recognisers align")
    parser.add_argument("--sample", type=int, default=300)
    parser.add_argument("--worst", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20261016)
    parser.add_argument("--timeout", type=int, default=7200, help="run: seconds a candidate may take")
    args = parser.parse_args()
    {"sample": sample, "run": run, "one": one, "report": report}[args.mode](args)


if __name__ == "__main__":
    main()
