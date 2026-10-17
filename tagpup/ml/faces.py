"""Faces: finding them in a photo (MTCNN) and making each one's vector (FaceNet), from
the settings it is given.

Which thresholds it runs with is the library's (tagpup.services.settings,
LibrarySettings.faces), made into a model by tagpup.runtime. It was scripts/faces.py's
FaceProcessor, which read config.ini itself and also resolved who is who across a
library -- which is a service's, tagpup.services.identities (docs/ARCHITECTURE.md, "The
layers, revisited").
"""
from tagpup.ml import free_device_memory, gpu, refuse_in_tests
import logging
import os
import threading
import warnings

import numpy as np
import torch
from PIL import Image

warnings.filterwarnings("ignore", category=FutureWarning, message=".*weights_only.*")
from facenet_pytorch import MTCNN, InceptionResnetV1  # noqa: E402

from tagpup.files import images  # noqa: E402

logger = logging.getLogger("tagpup_cli.faces")

def detection_picture(img, max_side):
    """(the picture the detector is handed, (x, y) scale from it back to `img`): `img` itself and (1, 1) when there is
    no cap or its long side is within it; else `img` scaled so that its long side is `max_side` (docs/AI_PIPELINE.md,
    phase 1a). Only the detector sees it: crops and vectors come from `img`."""
    width, height = img.size
    longest = max(width, height)
    if not max_side or longest <= max_side:
        return img, (1.0, 1.0)
    size = (round(width * max_side / longest), round(height * max_side / longest))
    return img.resize(size, Image.BILINEAR, reducing_gap=3.0), (width / size[0], height / size[1])


class NotDetected(list):
    """What detect_and_embed_faces returns when detection did not run to the end -- the file
    is gone, the picture did not decode, the model failed: no faces, as before, but not
    "none found", which is recorded (tagpup.store.faces_detected) and never detected again."""
    failed = True


class FaceModel:
    """The detector and the face embedder, loaded the first time either is used."""

    def __init__(self, min_face_size, confidence_threshold, mtcnn_thresholds, device=None):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.mtcnn = None
        self.resnet = None
        self._init_lock = threading.Lock()
        #: The process's turn on the graphics card (tagpup.ml.gpu), which the runtime hands
        #: it; this process's own, card(), when none was.
        self.gpu = None

        self.min_face_size = min_face_size
        self.confidence_threshold = confidence_threshold
        self.mtcnn_thresholds = mtcnn_thresholds

    def load(self):
        """Load the models now, if they are not loaded yet."""
        self._init_models()

    def loaded(self):
        """Are the weights in memory now?"""
        return self.mtcnn is not None or self.resnet is not None

    def unload(self):
        """Let the weights go, and the GPU memory they held (tagpup.runtime drops a model
        no library it serves uses any more). Used again, they load again."""
        with self._init_lock:
            if self.mtcnn is None and self.resnet is None:
                return
            self.mtcnn = self.resnet = None
        logger.info("Unloaded the face models.")
        free_device_memory()

    def _init_models(self):
        """Lazily initialize MTCNN detector and InceptionResnetV1 face embedder.

        Locked: the suggester shares one model across a pool, and unlocked every
        worker saw no model yet and loaded its own copy onto the GPU. On the graphics card
        only with this process's turn on it (tagpup.ml.gpu), waited for before the lock.
        """
        if self.mtcnn is not None:
            return
        if gpu.on_the_card(self.device):
            (self.gpu or gpu.card()).ensure("loading the face models")
        with self._init_lock:
            if self.mtcnn is None:
                self._load_models()

    def _load_models(self):
        refuse_in_tests("The face models")
        logger.info(f"Initializing face detection (MTCNN) and embedding models (InceptionResnetV1) on {self.device.upper()}...")
        # MTCNN options: keep_all=True detects multiple faces, post_process=False keeps crops raw
        self.mtcnn = MTCNN(
            keep_all=True,
            device=self.device,
            min_face_size=self.min_face_size,
            thresholds=self.mtcnn_thresholds
        )
        # Resnet trained on VGGFace2 to extract 512-dimensional face features
        self.resnet = InceptionResnetV1(pretrained='vggface2', device=self.device).eval()
        if self.device == "cuda":
            self.resnet = self.resnet.half()

        logger.info("Face models loaded successfully.")

    def detect_and_embed_faces(self, img_path, *, max_side):
        """Detect all faces in an image and generate 512-dimensional embeddings for each. `max_side` is the longest
        side of the picture the detector looks at, or None for the picture as it is: the library's
        (LibrarySettings.detect_max_side), given with each call, so one model serves libraries of any cap."""
        self._init_models()
        # The models as they are now: an unload (the idle release) nulls the attributes, and this call finishes
        # with the ones it started with.
        mtcnn, resnet = self.mtcnn, self.resnet

        if not os.path.exists(img_path):
            return NotDetected()

        try:
            # As stored: the coordinates face boxes are kept in (tagpup.files.images).
            img = images.opened(img_path, upright=False)
            width, height = img.size

            # Detect bounding boxes and probability scores, on a smaller picture when the library caps it, the boxes
            # then scaled back to the picture as stored.
            picture, (scale_x, scale_y) = detection_picture(img, max_side)
            boxes, probs = mtcnn.detect(picture)

            if boxes is None or len(boxes) == 0:
                return []
            if picture is not img:
                boxes = boxes * np.array([scale_x, scale_y, scale_x, scale_y])

            detected_faces = []
            face_crops_info = []
            for box, prob in zip(boxes, probs):
                if prob < self.confidence_threshold:  # Configurable confidence threshold to filter out false face detections
                    continue

                x1, y1, x2, y2 = box
                # Clamp coordinates to image boundaries
                x1, y1 = max(0, int(x1)), max(0, int(y1))
                x2, y2 = min(width, int(x2)), min(height, int(y2))

                if (x2 - x1) < 15 or (y2 - y1) < 15:
                    continue # Skip tiny/noise crops

                # Crop face from PIL image
                face_crop = img.crop((x1, y1, x2, y2))
                # Preprocess crop to match InceptionResnetV1 inputs (160x160 RGB normalized)
                face_crop_resized = face_crop.resize((160, 160), Image.BILINEAR)
                face_tensor = torch.tensor(np.array(face_crop_resized), dtype=torch.float32).permute(2, 0, 1)
                # Normalize tensor elements from [0, 255] to [-1, 1] range as expected by facenet
                face_tensor = (face_tensor - 127.5) / 128.0

                # The crop kept with the face, at the size and quality every crop
                # is kept at (tagpup.files.images).
                crop_bytes = images.crop_jpeg(face_crop)

                face_crops_info.append({
                    "box": [x1, y1, x2, y2],
                    "tensor": face_tensor,
                    "crop_image": crop_bytes,
                    "prob": float(prob)
                })

            if face_crops_info:
                # Stack all face tensors into a single batch and move to device
                batch_tensors = torch.stack([x["tensor"] for x in face_crops_info]).to(self.device)
                if self.device == "cuda":
                    batch_tensors = batch_tensors.half()

                # Generate 512-dimensional embeddings in a single forward pass
                with torch.inference_mode():
                    emb_tensors = resnet(batch_tensors)
                    emb_tensors /= emb_tensors.norm(dim=-1, keepdim=True)
                    embeddings = emb_tensors.cpu().numpy().tolist()

                for info, emb in zip(face_crops_info, embeddings):
                    detected_faces.append({
                        "box": info["box"],
                        "embedding": emb,
                        "name": None,
                        "crop_image": info["crop_image"],
                        "prob": info["prob"]
                    })

            return detected_faces
        except Exception as e:
            logger.error(f"Error processing faces in {img_path}: {e}")
            return NotDetected()
