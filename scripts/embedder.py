"""Moved: CLIP to tagpup/ml/clip.py, the library's vectors to tagpup/services/search.py.

`from embedder import ClipEmbedder` keeps working for the tests that still use it: an
embedder is the model and its library's cache in one, with the config's settings for
any not given, as it was. Code that ships gets its model from tagpup.runtime.
"""
import functools

import _root  # noqa: F401
from tagpup import config as tagpup_config
from tagpup.files.images import pad_to_square  # noqa: F401
from tagpup.ml import clip
from tagpup.ml.clip import output_dim  # noqa: F401
from tagpup.services import search
from tagpup.services.search import stored_mismatch  # noqa: F401


class ClipEmbedder(clip.ClipModel):
    def __init__(self, photo_index=None, **settings):
        """`settings` are tagpup.config.embedder_settings()'s: the config's for any not
        given (docs/findings.md, #74)."""
        chosen = tagpup_config.embedder_settings()
        unknown = set(settings) - set(chosen)
        if unknown:
            raise TypeError("ClipEmbedder has no setting %s" % ", ".join(sorted(unknown)))
        chosen.update(settings)
        super().__init__(**chosen)
        self.photo_index = photo_index
        self.model_key = self._embeddings().model_key

    def _embeddings(self):
        return search.PhotoEmbeddings(self, self.photo_index,
                                      embed=functools.partial(clip.ClipModel.embed_image, self))

    def get_cached_embedding(self, file_path):
        return self._embeddings().cached(file_path)

    def save_to_cache(self, file_path, embedding, stamp=None):
        self._embeddings().keep(file_path, embedding, stamp)

    def embed_image(self, file_path, force_recompute=False):
        return self._embeddings().of(file_path, force_recompute=force_recompute)
