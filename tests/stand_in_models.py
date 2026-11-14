"""The models a Suggest run is handed, stood in for: no download, no GPU (tests/test_no_test_loads_a_model.py).

A run is given both, always (tagpup.runtime.Runtime.begin -> tagpup.services.suggester.model_for_run), and a test reaches
the suggester the way a run does:

    suggester = stand_in_models.run_for(photo_index, faces, dim=3).suggester

`faces` is the test's own face model (what it detects is the test's point); CLIP is `Clip`, which asks nothing of a photo
(the test gives its vector) and embeds every word to nothing, so no word is offered.
"""
import os
from unittest import mock

from tagpup.core import validation
from tagpup.ml import photo_models, scrfd_adaface
from tagpup.services import search
from tagpup.services import settings as library_settings
from tagpup.services import suggester


#: A photo model other than a new library's (SigLIP 2): a second pair the table of photo models holds for a test that
#: declares it (`declare_other_photo_model`), so a library can be put on it, or a model made, retired or switched to.
OTHER_PHOTO_MODEL = ("ViT-Other-14", "other-weights")
#: The settings that put a library on it.
ON_THE_OTHER_PHOTO_MODEL = {"model.name": OTHER_PHOTO_MODEL[0], "model.pretrained": OTHER_PHOTO_MODEL[1]}


def declare_other_photo_model(test):
    """The table of photo models (tagpup.ml.photo_models.MODELS) holds OTHER_PHOTO_MODEL for `test`, one that may be
    shown photos at a size of the library's choosing; the table is as it was after the test."""
    patched = mock.patch.dict(photo_models.MODELS, {OTHER_PHOTO_MODEL: {"word_cut": 0.2, "sizes": True}})
    patched.start()
    test.addCleanup(patched.stop)


class Faces:
    """The face model a new library is on (the setting's default, tagpup.ml.scrfd_adaface.ScrfdAdaFace) as an index or a
    Suggest run is handed it: its `key`, which its vectors are filed under, and its `detector`, the record a detection is
    kept under (tagpup.services.faces.detector_of), the model's own. A test's face model is one of these and says what it
    detects (`detect_and_embed_faces`); every face it finds is made a face (`new_faces`), as found."""
    key = validation.SETTINGS["faces.model"]["default"]
    detector = scrfd_adaface.ScrfdAdaFace.detector

    @staticmethod
    def new_faces(detected, picture):
        """Every face the test's detection found, as found: what it detects is the test's point."""
        return detected

    def missing(self):
        """Every weight file is there."""
        return None

    def picture(self, path):
        """The processor's half, as the CLI's index asks it (the model's `picture`): what the test's
        detect_and_embed_faces is handed then."""
        return path

    def picture_bytes(self, width, height):
        """What the processor's half holds, as the read-ahead asks it: nothing decoded here."""
        return 0

    @staticmethod
    def picture_of_frame(frame, label):
        """A clip's frame as the model makes it ready (ScrfdAdaFace.picture_of_frame), no squares: what the test's
        detect_and_embed is handed for a frame."""
        return scrfd_adaface.ScrfdPicture(frame, {}, label)

    def detect_and_embed(self, picture):
        return self.detect_and_embed_faces(picture)


class NewFaces:
    """The new face model (tagpup.ml.scrfd_adaface.ScrfdAdaFace) as the face pass is handed it: its `key`, the model's own
    `picture` of the photo upright (scrfd_adaface.ScrfdPicture of images.opened upright, no squares; NotDetected when it
    does not decode, handed back by `detect_and_embed`), the model's own `new_faces` and `detector`, and
    `detect_and_embed`, which answers what the test says each photo holds -- `found`, {file name: [(box, prob, seed)]},
    highest score first, each box where the detector finds the face, on the picture upright, kept as the model keeps it
    (scrfd_adaface.kept: clamped, and in the picture as stored), with a vector made from `seed` (`vector`); a file name
    given None is a model that fails (NotDetected); one not given has no face. `kill_at`, a file name, raises Killed
    there: the process stopped."""
    key = validation.SCRFD_ADAFACE
    detector = scrfd_adaface.ScrfdAdaFace.detector
    new_faces = scrfd_adaface.ScrfdAdaFace.new_faces

    class Killed(BaseException):
        """The run stopped part-way: a crash, the window closed."""

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
        from tagpup.files import images
        from tagpup.ml.scrfd_adaface import NotDetected
        try:
            return scrfd_adaface.ScrfdPicture(images.opened(path, upright=True), {}, path)
        except Exception:
            return NotDetected()

    def detect_and_embed(self, picture):
        from tagpup.ml.scrfd_adaface import NotDetected
        if isinstance(picture, NotDetected):
            return picture   # handed back, as the model's own
        name = os.path.basename(picture.path)
        self.asked.append(name)
        if name == self.kill_at:
            raise self.Killed(name)
        if name in self.found and self.found[name] is None:
            return NotDetected()
        return [dict(face, embedding=self.vector(seed), norm=20.0)
                for box, prob, seed in self.found.get(name, [])
                for face in scrfd_adaface.kept([(box, prob, [])], picture)]


class RefusableFaces(NewFaces):
    """The new face model (NewFaces), which raises when the test refuses it (`refused`, noting the photo either way), and
    whose `picture` of a file named in `undecodable` does not decode (NotDetected). `vector`, given, makes each face's
    vector from its seed instead (a bake's test: one as long as the face model's, baked_files.face_vector)."""
    refused = False
    undecodable = ()

    def __init__(self, found=None, kill_at=None, *, vector=None):
        super().__init__(found, kill_at)
        if vector is not None:
            self.vector = vector

    def picture(self, path):
        from tagpup.ml.scrfd_adaface import NotDetected
        if os.path.basename(path) in self.undecodable:
            return NotDetected()
        return super().picture(path)

    def detect_and_embed(self, picture):
        if self.refused:
            self.asked.append(os.path.basename(picture.path))
            raise AssertionError("the face model was asked of %s" % os.path.basename(picture.path))
        return super().detect_and_embed(picture)


class Hold:
    """The CLI's turn on the graphics card, as the runtime hands it (tagpup.ml.gpu.Hold): nothing to give back."""

    def release(self):
        pass


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
    clip = Clip(dim)
    return suggester.model_for_run(photo_index, clip, faces, list(words),
                                   embeddings=search.PhotoEmbeddings(clip, photo_index))
