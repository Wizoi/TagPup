"""Moved: PhotoIndex to tagpup/services/search.py, PathLocker to tagpup/store/locks.py,
and the face reads and writes PhotoIndex wrapped to tagpup/services/faces.py and
tagpup/store/faces.py.

`from index import PhotoIndex` keeps working for the tests that still use it: an index
with the config's model when none is named, and the face wrappers it had.
"""
from typing import Any, Dict, List, Optional, Set, Tuple

import _root  # noqa: F401
import db as tagpup_db
from tagpup import config as tagpup_config
from tagpup.services import faces as face_records
from tagpup.services import search
from tagpup.services.identities import _all_faces, _excluded_face_ids, _manual_face_names, _save_face_names
from tagpup.store import embeddings as store_embeddings
from tagpup.store import faces as store_faces
from tagpup.store.locks import PathLocker  # noqa: F401

# Connection settings, the write lock and the busy retry live in db.py. These names are
# kept so existing callers and tests keep working.
DB_BUSY_TIMEOUT_MS = tagpup_db.BUSY_TIMEOUT_MS
configure_connection = tagpup_db.configure
retry_when_busy = tagpup_db.retry_when_busy


class PhotoIndex(search.PhotoIndex):
    def __init__(self, db_path: str, model: Optional[str] = None):
        super().__init__(db_path, model or store_embeddings.model_key(**tagpup_config.embedder_settings()))

    def save(self):
        """No-op: changes are committed as they are made."""

    def remove_faces_for_path(self, photo_path: str):
        if self.conn is None:
            return
        try:
            store_faces.remove_for_photo(self.conn, photo_path)
            self.conn.commit()
        except Exception:
            self.conn.rollback()

    def save_faces_for_path(self, photo_path: str, faces: List[Dict[str, Any]]):
        face_records.replace_detected(self.conn, photo_path, faces)

    def save_faces_if_absent(self, photo_path: str, faces: List[Dict[str, Any]]) -> int:
        return face_records.record_detected(self.db_path, photo_path, faces)

    def save_faces_batch(self, batch_faces: Dict[str, List[Dict[str, Any]]], overwrite: bool = False):
        face_records.record_batch(self.conn, batch_faces, overwrite=overwrite)

    def get_manual_face_names(self) -> Dict[int, Optional[str]]:
        return _manual_face_names(self)

    def get_excluded_face_ids(self) -> Set[int]:
        return _excluded_face_ids(self)

    def get_all_faces(self) -> List[Dict[str, Any]]:
        return _all_faces(self)

    def known_faces(self):
        return face_records.known_faces(self.db_path)

    def save_face_names(self, face_updates: List[Tuple[Optional[str], int]]):
        _save_face_names(self, face_updates)

    def reset_face_assignments(self):
        return face_records.clear_automatic_names(self.conn)
