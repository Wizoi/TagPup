"""Moved: the face models to tagpup/ml/faces.py, resolving who is who to
tagpup/services/identities.py.

`from faces import FaceProcessor` keeps working for the tests that still use it: a
processor is the face models with the config's thresholds, and resolves identities as
it did. Code that ships gets its face model from tagpup.runtime.
"""
import _root  # noqa: F401
from tagpup import config as tagpup_config
from tagpup.ml import faces as face_models
from tagpup.services import identities
from tagpup.services.identities import resolution_trace_path  # noqa: F401


class FaceProcessor(face_models.FaceModel):
    def __init__(self, device=None):
        super().__init__(device=device, **tagpup_config.face_settings())

    def cluster_and_resolve_identities(self, photo_index, taxonomy, max_iterations=5):
        """tagpup.services.identities.resolve; `taxonomy` was never used."""
        return identities.resolve(photo_index, max_iterations=max_iterations)
