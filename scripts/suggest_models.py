"""The models Suggest runs on, for the web server: each library's suggester and CLIP
embedder, and warming the models up.

The suggester (scripts/suggester.py), the CLIP embedder (scripts/embedder.py), the face
models (scripts/faces.py) and PhotoIndex (scripts/index.py) have not moved into the
package, and nothing in the package may import them (tests/test_layers.py). So the
launcher installs this module as the suggestion runs' model provider
(tagpup.jobs.suggestions.models) and starts the warm-up on a thread:

    import suggest_models
    suggest_models.install()
    threading.Thread(target=suggest_models.warm_up, daemon=True).start()

When those modules move into tagpup.ml, this becomes a service and the launcher's two
lines go with it. Until then it is the one place the web server reaches them.
"""
import logging
import threading

import _root  # noqa: F401
from tagpup import config as tagpup_config
from tagpup.core import suggesting
from tagpup.jobs import suggestions as suggestion_jobs
from tagpup.core.per_library import PerLibrary

logger = logging.getLogger(__name__)


def _new_embedder(library):
    """A library's embedder over its loaded index. Called once per library (embedders)."""
    from embedder import ClipEmbedder
    from index import PhotoIndex

    photo_index = PhotoIndex(db_path=library.path)
    photo_index.load()
    return ClipEmbedder(photo_index=photo_index, **tagpup_config.embedder_settings())


#: Each library's embedder and index, made once and kept for the process.
#:
#: The startup library's was made at startup and never reloaded, so photos indexed and
#: tags saved since never reached Suggest. Every other library had none, and loaded its
#: whole index again on every run. Each library keeps one, and a run reloads its index
#: only when the photos table has changed. The CLIP model itself is shared by every
#: embedder (ClipEmbedder._shared_model).
embedders = PerLibrary(_new_embedder)


def library_embedder(library):
    """This library's embedder, its index brought up to date."""
    embedder = embedders.of(library)
    embedder.photo_index.reload_if_changed()
    return embedder


class Model:
    """What a suggestion run calls (tagpup.jobs.suggestions.SuggestionRuns.start): one
    library's suggester and embedder, readied for one run."""

    def __init__(self, suggester, embedder, face_roots):
        self.suggester, self.embedder = suggester, embedder
        # Read once for the run: the caption files people under them (#66).
        self.face_roots = face_roots

    @property
    def model_key(self):
        """The model its suggestions are made with (tagpup.store.embeddings)."""
        return self.embedder.model_key

    def suggest(self, photo, meta):
        return self.suggester.suggest_for_photo(
            photo, self.embedder.embed_image(photo), k=15, min_sim=0.35, target_metadata=meta)

    def offered(self, suggestion):
        """What the panel shows for one suggestion: tags, people, title."""
        from writer import derive_caption_from_tags

        tags, people = [], []
        for item in suggestion.get("suggested_tags", []):
            score = item.get("score", 0.0)
            if score >= suggesting.OFFER_A_TAG:
                if item.get("has_face_match"):
                    people.append({"name": item["tag"], "score": score})
                else:
                    tags.append({"tag": item["tag"], "score": score})
        return tags, people, derive_caption_from_tags(
            [t["tag"] for t in tags] + [p["name"] for p in people], self.face_roots)

    def consensus(self, suggestions):
        return self.suggester.apply_folder_consensus(suggestions)


class Models:
    """The model provider the runs are given (tagpup.jobs.suggestions.models)."""

    def begin(self, library):
        """Ready the suggester and the CLIP model for `library`, on the run's thread."""
        from suggester import TagSuggester
        from taxonomy import TagTaxonomy

        settings = tagpup_config.load()
        taxonomy = TagTaxonomy(library.path)
        taxonomy.load()
        # config.ini's words and the tree's, but no one's name (tagpup.core.suggesting).
        candidates = suggesting.zero_shot_words(tagpup_config.candidate_tags(settings), taxonomy.paths, taxonomy.people_roots())
        embedder = library_embedder(library)
        suggester = TagSuggester(embedder.photo_index, taxonomy, embedder=embedder, candidate_tags=candidates)
        # Candidate text embeddings, before the parallel photos need them.
        suggester._precompute_candidates()
        return Model(suggester, embedder, taxonomy.people_roots())


def install():
    """Give the suggestion runs their models. The launcher calls this once."""
    suggestion_jobs.models = Models()


def warm_up():
    """Load the CLIP model and the face models, so the first Suggest does not pay for
    them. On a thread of its own; it opens no library.

    The old server's warm-up made a PhotoIndex on the startup library and loaded it in
    the background, keeping it open until the process ended -- and a thread that
    started after the file had gone made an empty library in its place
    (docs/findings.md, #99). The models are the process's; a library is a request's.
    """
    from embedder import ClipEmbedder

    logger.info("Background thread starting CLIP model warmup...")
    try:
        embedder = ClipEmbedder()
        embedder._init_model()
        embedder.embed_text("warmup")
        logger.info("Background CLIP model warmup completed successfully.")
    except Exception as e:
        logger.error("Error warming up CLIP model: %s", e)

    try:
        logger.info("Background thread starting Face model warmup...")
        import suggester
        from faces import FaceProcessor
        with suggester._face_processor_lock:
            if suggester._global_face_processor is None:
                suggester._global_face_processor = FaceProcessor()
        # Constructing the processor loads nothing; the models load on first use. So
        # this reported the face models warm while the first Suggest still paid for
        # loading them.
        suggester._global_face_processor._init_models()
        logger.info("Background Face model warmup completed successfully.")
    except Exception as e:
        logger.error("Error warming up Face models: %s", e)


def warm_up_in_background():
    """warm_up on a daemon thread; returns the thread."""
    thread = threading.Thread(target=warm_up, name="WarmupModelsThread", daemon=True)
    thread.start()
    return thread
