"""metadata.py: the old calls, until their callers use tagpup directly.

This module mixed three layers, and has been split by them:

  reading photo files     tagpup.files.metadata   (MetadataExtractor, rotate, rename)
  what the fields mean    tagpup.core.vocabulary  (tags, people, captions)
  what a library says     tagpup.store            (its people, its face names)

What is left joins them back into the calls that took a `db_path` or `conn`, and holds
the two helpers that format a photo for the pages, which go with the web layer.
"""
from typing import Any, Dict, List, Optional, Set

import _root  # noqa: F401
from tagpup import config as tagpup_config
from tagpup.core import dates
from tagpup.core import vocabulary as tag_vocabulary  # extract_people has a `vocabulary` argument
from tagpup.core.vocabulary import extract_captions, extract_tags  # noqa: F401
from tagpup.files import metadata as files_metadata
from tagpup.files.metadata import (  # noqa: F401
    METADATA_FIELDS, ROTATED_ORIENTATION, clean_metadata_value, rotate_image_file,
    sanitize_filename)
from tagpup.store import faces as store_faces
from tagpup.store import taxonomy as store_taxonomy


class PeopleVocabulary(tag_vocabulary.PeopleVocabulary):
    """tagpup.core.vocabulary.PeopleVocabulary, loadable from a library as before."""

    @classmethod
    def load(cls, db_path: Optional[str] = None, conn: Any = None) -> tag_vocabulary.PeopleVocabulary:
        """From `conn`, else from `db_path`, else the defaults alone
        (tagpup.store.taxonomy.people_vocabulary)."""
        return store_taxonomy.people_vocabulary(db_path, conn=conn)


def get_people_roots(db_path: Optional[str] = None, conn: Any = None) -> Set[str]:
    """Retrieve lowercase names of all root categories marked as People from database."""
    return store_taxonomy.people_vocabulary(db_path, conn=conn).roots


def extract_people(meta: Dict[str, Any], tags: List[str], db_path: Optional[str] = None,
                   conn: Any = None, vocabulary: Optional[tag_vocabulary.PeopleVocabulary] = None) -> List[str]:
    """Whom a photo's metadata names (tagpup.core.vocabulary.extract_people).

    Pass `vocabulary` when resolving many photos; otherwise the tag tree is read from
    `conn` or `db_path` for this one call.
    """
    if vocabulary is None:
        vocabulary = store_taxonomy.people_vocabulary(db_path, conn=conn)
    return tag_vocabulary.extract_people(meta, tags, vocabulary)


def face_names(photo_path: str, db_path: Optional[str] = None, conn: Any = None) -> List[str]:
    """The names given to a photo's faces (tagpup.store.faces.face_names)."""
    return store_faces.face_names(photo_path, db_path=db_path, conn=conn)


def photo_people(meta: Dict[str, Any], tags: List[str], photo_path: str,
                 db_path: Optional[str] = None, conn: Any = None) -> List[str]:
    """Everyone in a photo, by its metadata and its named faces
    (tagpup.core.vocabulary.people_in_photo)."""
    known = store_taxonomy.people_vocabulary(db_path, conn=conn)
    return tag_vocabulary.people_in_photo(
        meta, tags, face_names(photo_path, db_path=db_path, conn=conn), known)


class MetadataExtractor(files_metadata.MetadataExtractor):
    """The file reader, told what the library at `db_path` says about people."""

    def batch_read(self, file_paths: List[str], db_path: Optional[str] = None) -> List[Dict[str, Any]]:
        """Read a batch of photos. The library's people are read once for the batch;
        each photo used to read them again."""
        if not file_paths:
            return []
        return super().batch_read(file_paths, people=store_taxonomy.people_vocabulary(db_path))


def sync_title_to_filename(photo_path: str, new_title: str, exiftool_path: str) -> str:
    """Rename a photo after its new caption, in the format config.ini names
    (tagpup.files.metadata.sync_title_to_filename)."""
    return files_metadata.sync_title_to_filename(
        photo_path, new_title, exiftool_path, tagpup_config.rename_format())


def parse_year_from_metadata(meta: Dict[str, Any]) -> Optional[int]:
    """The year a photo was taken: the record's "year", as the library records it
    (photos.year) and the reader gives it; else, for a record without one or a
    raw_metadata dict itself, tagpup.core.dates.photo_year."""
    if "year" in meta:
        return meta["year"]
    return dates.photo_year(meta.get("raw_metadata", meta), meta.get("path"))


def build_photo_ui_record(path: str, meta: Dict[str, Any], mtime: float = 0.0, size: int = 0) -> Dict[str, Any]:
    """A photo as the TagPup page reads it (tagpup.services.photos.page_record), under
    the name the old callers use."""
    from tagpup.services import photos as photo_actions

    return photo_actions.page_record(path, meta, mtime, size)
