"""The models a Suggest run is handed, stood in for: no download, no GPU (tests/test_no_test_loads_a_model.py).

A run is given both, always (tagpup.runtime.Runtime.begin -> tagpup.services.suggester.model_for_run), and a test reaches
the suggester the way a run does:

    suggester = stand_in_models.run_for(photo_index, faces, dim=3).suggester

`faces` is the test's own face model (what it detects is the test's point); CLIP is `Clip`, which asks nothing of a photo
(the test gives its vector) and embeds every word to nothing, so no word is offered.
"""
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
