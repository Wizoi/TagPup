"""The models a Suggest run is handed, stood in for: no download, no GPU (tests/test_no_test_loads_a_model.py).

A run is given both, always (tagpup.runtime.Runtime.begin -> tagpup.services.suggester.model_for_run), and a test reaches
the suggester the way a run does:

    suggester = stand_in_models.run_for(photo_index, faces, dim=3).suggester

`faces` is the test's own face model (what it detects is the test's point); CLIP is `Clip`, which asks nothing of a photo
(the test gives its vector) and embeds every word to nothing, so no word is offered.
"""
import os

from tagpup.core import validation
from tagpup.services import settings as library_settings
from tagpup.services import suggester


class Faces:
    """A face model's settings, which tagpup.ml.faces.FaceModel always holds and a detection is recorded under
    (tagpup.services.faces.detector_of): a test's face model is one of these, and says what it detects."""
    min_face_size, confidence_threshold, mtcnn_thresholds = 40, 0.9, [0.6, 0.7, 0.7]
    key = validation.MTCNN_FACENET   # the key its vectors are filed under, as tagpup.ml.faces.FaceModel's

    def picture(self, path, *, max_side):
        """The processor's half, as the CLI's index asks it (tagpup.ml.faces.FaceModel.picture): what the test's
        detect_and_embed_faces is handed then."""
        return path, max_side

    def picture_bytes(self, width, height, *, max_side):
        """What the processor's half holds, as the read-ahead asks it: nothing decoded here."""
        return 0

    def detect_and_embed(self, picture):
        path, max_side = picture
        return self.detect_and_embed_faces(path, max_side=max_side)


class NewFaces:
    """The new face model (tagpup.ml.scrfd_adaface.ScrfdAdaFace) as the face pass is handed it: its `key`, a `picture` of
    the photo as stored (decoded here by Pillow, NotDetected when it does not decode, as the model's own), and
    `detect_and_embed`, which answers what the test says each photo holds -- `found`, {file name: [(box, prob, seed)]},
    highest score first, each a detection with a vector made from `seed` (`vector`); a file name given None is a model
    that fails (NotDetected); one not given has no face. `kill_at`, a file name, raises Killed there: the process stopped."""
    key = validation.SCRFD_ADAFACE

    class Killed(BaseException):
        """The run stopped part-way: a crash, the window closed."""

    class Picture:
        def __init__(self, stored, path):
            self.stored, self.path = stored, path

    def __init__(self, found=None, kill_at=None):
        self.found = dict(found or {})
        self.kill_at = kill_at
        self.asked = []

    def missing(self):
        """Every weight file is there."""
        return None

    @staticmethod
    def vector(seed, size=8):
        """A normalized vector, as the recogniser's, made from `seed`."""
        values = [((seed * 31 + i) % 17) / 17.0 + 0.1 for i in range(size)]
        length = sum(v * v for v in values) ** 0.5
        return [v / length for v in values]

    @staticmethod
    def picture_bytes(width, height):
        """What the read-ahead reserves for the picture: the decoded RGB."""
        return 3 * width * height

    def picture(self, path):
        from PIL import Image
        from tagpup.ml.faces import NotDetected
        try:
            with Image.open(path) as img:
                return self.Picture(img.convert("RGB"), path)
        except Exception:
            return NotDetected()

    def detect_and_embed(self, picture):
        from tagpup.ml.faces import NotDetected
        name = os.path.basename(picture.path)
        self.asked.append(name)
        if name == self.kill_at:
            raise self.Killed(name)
        if name in self.found and self.found[name] is None:
            return NotDetected()
        return [{"box": list(box), "prob": prob, "landmarks": [], "embedding": self.vector(seed), "norm": 20.0}
                for box, prob, seed in self.found.get(name, [])]


class Clip:
    """CLIP as the runtime hands it to a run: the library's default settings, a word embedded to a zero vector `dim` long
    (alike nothing), and `ready` -- the run's turn on the graphics card -- with nothing to wait for."""

    def __init__(self, dim):
        self.settings = library_settings.LibrarySettings(dict(library_settings.DEFAULTS)).embedder
        self.model_name, self.pretrained = self.settings["model_name"], self.settings["pretrained"]
        self.dim = dim
        self.prompts = []

    def ready(self):
        """Nothing to wait for here."""

    def embed_text(self, prompt):
        self.prompts.append(prompt)
        return [0.0] * self.dim

    def input_of(self, path, seen=None, load=True):
        raise AssertionError("a photo was embedded: the test gives its vector")


def run_for(photo_index, faces, dim, words=()):
    """What a Suggest run over `photo_index` is handed (model_for_run), CLIP stood in for and `faces` the face model."""
    return suggester.model_for_run(photo_index, Clip(dim), faces, list(words), detect_max_side=None)
