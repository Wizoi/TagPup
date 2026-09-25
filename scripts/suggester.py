"""Moved to tagpup/services/suggester.py. `from suggester import TagSuggester` keeps
working for the tests that still use it.

The suggester made its own face model and kept it here, in `_global_face_processor`;
it is given one now (tagpup.runtime). A test that sets the slot is still heard: a
suggester given no face model uses the one in the slot, if any.
"""
import threading

import _root  # noqa: F401
from tagpup.services import suggester as _moved
from tagpup.services.suggester import extract_path_hints  # noqa: F401

_face_processor_lock = threading.Lock()
_global_face_processor = None


class TagSuggester(_moved.TagSuggester):
    @property
    def faces(self):
        given = self.__dict__.get("_faces")
        return given if given is not None else _global_face_processor

    @faces.setter
    def faces(self, value):
        self.__dict__["_faces"] = value
