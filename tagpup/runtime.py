"""The composition root: the settings an entry point read, made into the process's
long-lived objects.

    runtime = Runtime(tagpup.config.load)      # read again when a setting is needed
    app = tagpup.web.app.create_app("tagpup", runtime=runtime)
    runtime.warm_up_in_background()

A Runtime builds the CLIP model and the face models once each, the first time either is
asked for, and hands them to whatever needs them; it keeps each library's photo index
for Suggest, brought up to date when a run begins. Nothing below it builds a model or
reads a setting: a service that uses a model is given it (docs/ARCHITECTURE.md, "The
layers, revisited").

It replaces scripts/suggest_models.py, which the web launcher used to fill a module-level
slot in tagpup.jobs.suggestions with (docs/findings.md, #112), and the class attribute
every ClipEmbedder shared its model through. `tests/test_models_single_owner.py` fails a
module that builds a model anywhere else.
"""
import logging
import threading

from tagpup import config as tagpup_config
from tagpup.core.per_library import PerLibrary
from tagpup.services import search
from tagpup.services import suggester as suggestions
from tagpup.store import embeddings as store_embeddings

logger = logging.getLogger(__name__)


class Runtime:
    """What lives as long as the process: the models, built from the settings, and each
    library's photo index for Suggest.

    `settings` is the settings (a tagpup.config.load()) or what reads them
    (tagpup.config.load itself): a server passes the reader, so a setting read per run --
    the candidate words -- follows an edit to config.ini as it always did. The CLIP
    model's settings are read once, here: its vectors are named by them. The face
    models' are read when those models are first built, so a command that never uses
    them never parses them.

    `clip` and `faces` stand in for the models the settings name: a test's fakes, which
    are then never built.
    """

    def __init__(self, settings, clip=None, faces=None):
        self._read = settings if callable(settings) else (lambda: settings)
        #: The CLIP model's settings (tagpup.config.embedder_settings).
        self.embedder_settings = tagpup_config.embedder_settings(self._read())
        #: The name the CLIP model's vectors are kept under (tagpup.store.embeddings).
        self.model_key = store_embeddings.model_key(**self.embedder_settings)
        self._clip = clip
        self._faces = faces
        self._models_lock = threading.Lock()
        self._indexes = PerLibrary(self._open_index)

    @property
    def face_settings(self):
        """The face models' thresholds (tagpup.config.face_settings), read now."""
        return tagpup_config.face_settings(self._read())

    @property
    def candidate_words(self):
        """config.ini's words CLIP is asked about a photo, before the tree's
        (tagpup.core.suggesting.zero_shot_words), read now: each run reads them, as
        each run always did."""
        return tagpup_config.candidate_tags(self._read())

    # ---- The models ------------------------------------------------------------------

    @property
    def clip(self):
        """The CLIP model (tagpup.ml.clip), built the first time it is asked for. Its
        weights load on first use, or in warm_up."""
        with self._models_lock:
            if self._clip is None:
                # Here, not at the top: open_clip and torch take seconds to import.
                from tagpup.ml.clip import ClipModel
                self._clip = ClipModel(**self.embedder_settings)
            return self._clip

    @property
    def faces(self):
        """The face models (tagpup.ml.faces), built the first time they are asked for.
        Their weights load on first use, or in warm_up."""
        with self._models_lock:
            if self._faces is None:
                from tagpup.ml.faces import FaceModel
                self._faces = FaceModel(**self.face_settings)
            return self._faces

    def warm_up(self):
        """Load the CLIP model and the face models, so the first Suggest does not pay for
        them. It opens no library.

        The old server's warm-up made a PhotoIndex on the startup library and loaded it in
        the background, keeping it open until the process ended -- and a thread that
        started after the file had gone made an empty library in its place
        (docs/findings.md, #99). The models are the process's; a library is a request's.
        """
        logger.info("Background thread starting CLIP model warmup...")
        try:
            clip = self.clip
            clip.load()
            clip.embed_text("warmup")
            logger.info("Background CLIP model warmup completed successfully.")
        except Exception as e:
            logger.error("Error warming up CLIP model: %s", e)

        try:
            logger.info("Background thread starting Face model warmup...")
            # Building the model loads nothing; its weights load on first use. So a
            # warm-up that only built it reported the face models warm while the first
            # Suggest still paid for loading them.
            self.faces.load()
            logger.info("Background Face model warmup completed successfully.")
        except Exception as e:
            logger.error("Error warming up Face models: %s", e)

    def warm_up_in_background(self):
        """warm_up on a daemon thread; returns the thread."""
        thread = threading.Thread(target=self.warm_up, name="WarmupModelsThread", daemon=True)
        thread.start()
        return thread

    # ---- Each library's photo index ---------------------------------------------------

    def _open_index(self, library):
        photo_index = search.PhotoIndex(library.path, self.model_key)
        photo_index.load()
        return photo_index

    def photo_index(self, library):
        """This library's photo index for Suggest, made once and brought up to date.

        The startup library's was made at startup and never reloaded, so photos indexed
        and tags saved since never reached Suggest. Every other library had none, and
        loaded its whole index again on every run. Each library keeps one, and it is
        loaded again only when the photos table has changed.
        """
        photo_index = self._indexes.of(library)
        photo_index.reload_if_changed()
        return photo_index

    def forget(self, library):
        """Drop a library's photo index: a library removed."""
        self._indexes.forget(library)

    # ---- What a suggestion run is given ----------------------------------------------

    def begin(self, library):
        """Ready the suggester for a run over `library`, on the run's thread: what
        tagpup.jobs.suggestions calls (tagpup.services.suggester.SuggestionModel)."""
        return suggestions.model_for_run(self.photo_index(library), self.clip, self.faces,
                                         self.candidate_words)

    def embeddings(self, photo_index):
        """The photos' vectors under the CLIP model, kept in `photo_index`'s library."""
        return search.PhotoEmbeddings(self.clip, photo_index)
