"""The composition root: each library's settings, made into the process's long-lived
objects.

    runtime = Runtime()
    app = tagpup.web.app.create_app("tagpup", runtime=runtime)
    runtime.warm_up_in_background(libraries)

A library's settings are its own (tagpup.services.settings; docs/ARCHITECTURE.md, phase
7.6), and `library_settings` is where an entry point reads them: the first time a
library holding none is opened, it is stamped from what the home's config.ini says, if
it has one, else with the defaults. That is the one read of config.ini left
(tests/test_config_single_owner.py).

A Runtime builds each CLIP model and each set of face models once per set of settings,
the first time a library with those settings asks: two libraries on one model share it,
and a library on another gets its own. It keeps each library's photo index for Suggest,
brought up to date when a run begins. Nothing below it builds a model or reads a
setting: a service that uses a model is given it (docs/ARCHITECTURE.md, "The layers,
revisited").

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
from tagpup.services import settings as library_settings_service
from tagpup.services import suggester as suggestions
from tagpup.store import embeddings as store_embeddings

logger = logging.getLogger(__name__)


def library_settings(library):
    """The library's settings (tagpup.services.settings.LibrarySettings), a library
    holding none stamped first: from the home's config.ini if it has one, else with the
    defaults."""
    return library_settings_service.of(library, tagpup_config.config_ini)


def peek_settings(library):
    """The library's settings, writing nothing: for a read-only look (the MCP server's
    inspections, tools/doctor.py). A library never stamped reads as stamping would make it."""
    return library_settings_service.read(library, tagpup_config.config_ini)


def exiftool(library, settings=None):
    """The ExifTool program to run for `library`: the one it names, else the machine's
    (tagpup.config.exiftool_path)."""
    return tagpup_config.exiftool_path((settings or library_settings(library)).exiftool)


def _frozen(settings):
    """A settings dict as a key: its items, a list as a tuple."""
    return tuple(sorted((key, tuple(value) if isinstance(value, list) else value)
                        for key, value in settings.items()))


def _build_clip(settings):
    # Here, not at the top: open_clip and torch take seconds to import.
    from tagpup.ml.clip import ClipModel
    return ClipModel(**settings)


def _build_faces(settings):
    from tagpup.ml.faces import FaceModel
    return FaceModel(**settings)


class Runtime:
    """What lives as long as the process: the models, one per set of settings a library
    names, and each library's photo index for Suggest.

    `clip` and `faces` stand in for every library's models: a test's fakes, which are
    then never built. `build_clip` and `build_faces` make a model from its settings
    (tagpup.ml.clip.ClipModel's and tagpup.ml.faces.FaceModel's keyword arguments): a
    test's, to see which settings each library's model was made from.
    """

    def __init__(self, clip=None, faces=None, build_clip=None, build_faces=None):
        self._clip = clip
        self._faces = faces
        self._build_clip = build_clip or _build_clip
        self._build_faces = build_faces or _build_faces
        self._clips = {}
        self._face_models = {}
        self._models_lock = threading.Lock()
        self._indexes = PerLibrary(self._open_index)

    # ---- A library's settings --------------------------------------------------------

    def settings(self, library):
        """The library's settings, read now (library_settings): a change made in the
        dialog, or by another process, is seen by the next run."""
        return library_settings(library)

    def model_key(self, library, settings=None):
        """The name the library's CLIP model's vectors are kept under (tagpup.store.embeddings)."""
        return store_embeddings.model_key(**(settings or self.settings(library)).embedder)

    def candidate_words(self, library):
        """The library's words CLIP is asked about a photo, before the tree's."""
        return self.settings(library).candidate_words

    def exiftool(self, library):
        """The ExifTool program to run for `library`."""
        return exiftool(library)

    # ---- The models ------------------------------------------------------------------

    def clip(self, library, settings=None):
        """The CLIP model the library's settings name, built the first time any library
        on those settings asks for it. Its weights load on first use, or in warm_up."""
        if self._clip is not None:
            return self._clip
        return self._model(self._clips, self._build_clip, (settings or self.settings(library)).embedder)

    def faces(self, library, settings=None):
        """The face models the library's settings name, built the first time any library
        on those settings asks for them. Their weights load on first use, or in warm_up."""
        if self._faces is not None:
            return self._faces
        return self._model(self._face_models, self._build_faces, (settings or self.settings(library)).faces)

    def _model(self, built, build, settings):
        key = _frozen(settings)
        with self._models_lock:
            if key not in built:
                built[key] = build(settings)
            return built[key]

    def warm_up(self, libraries=()):
        """Load the CLIP model and the face models each of `libraries` names -- each set
        of settings once -- so the first Suggest does not pay for them. It stamps no
        library: their settings are read as they are (peek_settings).

        The old server's warm-up made a PhotoIndex on the startup library and loaded it in
        the background, keeping it open until the process ended -- and a thread that
        started after the file had gone made an empty library in its place
        (docs/findings.md, #99). The models are the process's; a library is a request's.
        """
        wanted = []
        if self._clip is not None or self._faces is not None:
            wanted.append((None, None))
        seen = set()
        for library in libraries:
            try:
                settings = peek_settings(library)
            except Exception as e:
                logger.error("Could not read the settings of %s to warm its models: %s", library.name, e)
                continue
            key = (_frozen(settings.embedder), _frozen(settings.faces))
            if key not in seen:
                seen.add(key)
                wanted.append((library, settings))
        for library, settings in wanted:
            logger.info("Background thread starting CLIP model warmup...")
            try:
                clip = self.clip(library, settings)
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
                self.faces(library, settings).load()
                logger.info("Background Face model warmup completed successfully.")
            except Exception as e:
                logger.error("Error warming up Face models: %s", e)

    def warm_up_in_background(self, libraries=()):
        """warm_up on a daemon thread; returns the thread."""
        thread = threading.Thread(target=self.warm_up, args=(list(libraries),), name="WarmupModelsThread",
                                  daemon=True)
        thread.start()
        return thread

    # ---- Each library's photo index ---------------------------------------------------

    def _open_index(self, library):
        photo_index = search.PhotoIndex(library.path, self.model_key(library))
        photo_index.load()
        return photo_index

    def photo_index(self, library):
        """This library's photo index for Suggest, made once and brought up to date.

        The startup library's was made at startup and never reloaded, so photos indexed
        and tags saved since never reached Suggest. Every other library had none, and
        loaded its whole index again on every run. Each library keeps one, and it is
        loaded again only when the photos table has changed -- or made again when the
        library's CLIP model has.
        """
        photo_index = self._indexes.of(library)
        if photo_index.model != self.model_key(library):
            photo_index.close()
            self._indexes.forget(library)
            photo_index = self._indexes.of(library)
        photo_index.reload_if_changed()
        return photo_index

    def forget(self, library):
        """Drop a library's photo index: a library removed."""
        self._indexes.forget(library)

    # ---- What a suggestion run is given ----------------------------------------------

    def begin(self, library):
        """Ready the suggester for a run over `library`, on the run's thread: what
        tagpup.jobs.suggestions calls (tagpup.services.suggester.SuggestionModel). The
        library's settings are read once for the run."""
        settings = self.settings(library)
        return suggestions.model_for_run(self.photo_index(library), self.clip(library, settings),
                                         self.faces(library, settings), settings.candidate_words)

    def embeddings(self, library, photo_index):
        """The photos' vectors under the library's CLIP model, kept in `photo_index`."""
        return search.PhotoEmbeddings(self.clip(library), photo_index)
