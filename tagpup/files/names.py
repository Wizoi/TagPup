"""Renaming photo files: what Smart Rename reads from each photo, and renaming a set of
them all together or not at all."""
import logging
import os
import time

from tagpup.core import paths
# Looked up at call time, as exiftool_session.ExifToolSession, so a test standing in for
# ExifTool there reaches this too.
from tagpup.files import exiftool_session

logger = logging.getLogger(__name__)

#: What Smart Rename reads from each photo: the name it had before it was first renamed,
#: and its caption.
RENAME_FIELDS = ["XMP-xmpMM:PreservedFileName", "XMP:PreservedFileName",
                 "XMP:Title", "Title", "XMP:Description", "Description",
                 "IPTC:Caption-Abstract", "Caption-Abstract"]


def _field(meta, *names):
    """(field name without its group, value) of each field in `meta` that is one of `names`."""
    for key, value in meta.items():
        base = key.split(":")[-1] if ":" in key else key
        if base in names:
            yield base, value


def preserved_name(meta):
    """The name a photo had before Smart Rename first renamed it, or None."""
    for _base, value in _field(meta, "PreservedFileName"):
        return str(value).strip()
    return None


def caption_for_name(meta):
    """The caption a photo's new name carries: the first of its description, caption
    and title fields to hold one, in the order ExifTool gave them."""
    title = ""
    for _base, value in _field(meta, "Description", "Caption-Abstract", "Title"):
        if value:
            if isinstance(value, list) and value:
                title = str(value[0]).strip()
            else:
                title = str(value).strip()
            if title:
                break
    return title.strip()


def read_for_renaming(exiftool_path, photo_paths):
    """{path: caption} for each photo, in one ExifTool session.

    A photo with no PreservedFileName has its current name written there first: the
    name it had before Smart Rename first renamed it, which later renames leave alone.
    """
    captions = {}
    with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
        for path in photo_paths:
            meta = et.get_tags([path], tags=RENAME_FIELDS)
            meta = meta[0] if meta else {}
            if not preserved_name(meta):
                et.set_tags([path], tags={"XMP-xmpMM:PreservedFileName": os.path.basename(path)},
                            params=["-overwrite_original"])
            captions[path] = caption_for_name(meta)
    return captions


class RenameFailed(Exception):
    """A set of renames that failed part way. Every photo that could be was put back under
    its old name; `stranded` are the paths that could not be."""

    def __init__(self, cause, stranded):
        super().__init__(str(cause))
        self.cause = cause
        self.stranded = stranded

    def message(self):
        if self.stranded:
            return ("Could not rename: %s. These could not be put back: %s"
                    % (self.cause, ", ".join(self.stranded)))
        return "Could not rename: %s. Every photo was put back under its old name." % self.cause


def aside_for(renames):
    """{a file outside `renames` that holds one of the new names: where it is to be
    moved aside}, "<name>_conflict_<n>" beside it. Worked out before anything is renamed,
    so the file journal can record the moves aside with the renames
    (tagpup.services.file_changes)."""
    selected_keys = {paths.key(p) for p in renames}
    target_keys = {paths.key(p) for p in renames.values()}
    aside, taken = {}, set()
    for target_path in renames.values():
        if os.path.exists(target_path) and paths.key(target_path) not in selected_keys:
            dir_name = os.path.dirname(target_path)
            base, ext = os.path.splitext(os.path.basename(target_path))
            counter = 1
            safe_path = os.path.join(dir_name, f"{base}_conflict_{counter}{ext}")
            while (os.path.exists(safe_path) or paths.key(safe_path) in target_keys
                   or paths.key(safe_path) in taken):
                counter += 1
                safe_path = os.path.join(dir_name, f"{base}_conflict_{counter}{ext}")
            aside[target_path] = safe_path
            taken.add(paths.key(safe_path))
    return aside


def rename_all(renames, aside=None):
    """Rename every photo in `renames` (old path -> new path), or none of them.

    Returns (done, moved_aside): old path -> new path for every photo, those whose name
    did not change included; and the files outside `renames` that already held one of
    the new names, each moved aside to "<name>_conflict_<n>" first -- its index row has
    to follow it, or the photo renamed into its place finds the name taken in the index.
    `aside` is those moves when the caller worked them out already (aside_for); {} moves
    nothing aside.

    The renames go in two passes through temporary names, so a run shuffling numbered
    names among themselves never lands on a name not yet vacated. If one fails, every
    photo is put back, through temporary names again (a photo already renamed may hold
    the old name of one still waiting), and RenameFailed says whether any could not be.
    """
    moved_aside = {}
    for target_path, safe_path in (aside_for(renames) if aside is None else aside).items():
        os.rename(target_path, safe_path)
        moved_aside[target_path] = safe_path

    def temporary_name(path):
        return os.path.join(os.path.dirname(path), "tmp_rename_%s_%s%s" % (
            hash(path), time.time(), os.path.splitext(path)[1]))

    temp_of = {}   # old path -> where it waits
    done = {}      # old path -> new path, once it is there
    try:
        for old_path, target_path in renames.items():
            if old_path != target_path:
                temp_path = temporary_name(old_path)
                os.rename(old_path, temp_path)
                temp_of[old_path] = temp_path
            else:
                done[old_path] = target_path
        for old_path, temp_path in list(temp_of.items()):
            os.rename(temp_path, renames[old_path])
            del temp_of[old_path]
            done[old_path] = renames[old_path]
    except OSError as rename_err:
        stranded = []
        for old_path, new_path in list(done.items()):
            if old_path == new_path:
                continue
            try:
                temp_path = temporary_name(old_path)
                os.rename(new_path, temp_path)
                temp_of[old_path] = temp_path
            except OSError as back_err:
                stranded.append(new_path)
                logger.error("Smart Rename could not undo %s: %s", new_path, back_err)
        for old_path, temp_path in temp_of.items():
            try:
                os.rename(temp_path, old_path)
            except OSError as back_err:
                stranded.append(temp_path)
                logger.error("Smart Rename could not put %s back as %s: %s",
                             temp_path, old_path, back_err)
        for original, aside in moved_aside.items():
            try:
                os.rename(aside, original)
            except OSError as back_err:
                stranded.append(aside)
                logger.error("Smart Rename could not put %s back as %s: %s",
                             aside, original, back_err)
        logger.error("Smart Rename failed and was undone: %s", rename_err)
        raise RenameFailed(rename_err, stranded) from rename_err
    return done, moved_aside
