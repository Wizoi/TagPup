"""A page's write of photo files, by operation: the service of the one entry point (POST /api/photos/write; plan
one-write-path, cut 2a). A dispatcher only: each operation is written by the service that owns it, as it always was.

    tags   each photo's own tags to add and take off, as ONE change of photo files (tagging.change_tags_of)
    set    ONE photo's title, tags and Date Taken from the record the page read, its stamp and base checked against the
           file under the lock (tagging.save_photo); no page sets several photos at once

`changes` is {path: (id, the operation's own fields)}: a photo is named by its path, and by its row's id when it has one
(Just look's photos have none: answer 5 of the plan). An id whose row is not at that path now -- the photo renamed,
moved or deleted since the page read it -- refuses the whole write, as a stale record's save is refused (CHANGED_ON_DISK,
409), nothing written: the page's record is not the file's. A photo named by its path alone is written as that path's
file, whatever row it has.
"""
import os

from tagpup.core import paths
from tagpup.core.result import CHANGED_ON_DISK, Result
from tagpup.services import file_changes, selection, tagging

TAGS, SET = "tags", "set"
#: The operations, each with the name a batch of it is listed under (Activity) while it waits for its turn.
OPERATIONS = {TAGS: "bulk tags", SET: "save photo"}

#: The refusal of a photo whose id no longer names the file at the path the page sent.
MOVED_SENTENCE = "%s is not the photo this page read (renamed, moved or deleted since): reload it first."


def write(library, op, changes, exiftool_path, rename_format, group=None, stop_at_first_error=False):
    """Write `changes` ({path: (id or None, fields)}) by `op`, in `library`, under the lock of changes of photo files: a
    Result as the operation's service gives it. `tags`' fields are {"add": [tag], "remove": [tag]}, `group` the token
    of the page's action this is a chunk of (journal.GROUP), `stop_at_first_error` False: a photo that cannot be read or
    written (a share that does not answer) is its own error and the others are written. `set`'s are {"title", "tags",
    "date_taken", "stamp", "base"} of the one photo, as tagging.save_photo takes them. The ids are checked under the same
    lock as the write, so a rename cannot come between (_moved)."""
    with file_changes.exclusively():
        moved = _moved(library, changes)
        if moved is not None:
            return moved
        if op == TAGS:
            return tagging.change_tags_of(
                library, {path: (own.get("add", []), own.get("remove", [])) for path, (_id, own) in changes.items()},
                exiftool_path, stop_at_first_error=stop_at_first_error, group=group)
        ((path, (_id, own)),) = changes.items()
        return tagging.save_photo(library, path, own["title"], own["tags"], own["date_taken"], exiftool_path,
                                  rename_format, own["stamp"], own["base"])


def _moved(library, changes):
    """A Result refused as CHANGED_ON_DISK, nothing written, when a photo named by id is not at the path it is named by:
    its row is at another path now, or gone. None when every id is where the page says (or none is given)."""
    asked = {photo_id: path for path, (photo_id, _own) in changes.items() if photo_id is not None}
    if not asked:
        return None
    now = selection.paths_of(library, asked)
    for photo_id, path in asked.items():
        if photo_id not in now or not paths.same(now[photo_id], path):
            result = Result(attempted=len(changes))
            result.refuse(MOVED_SENTENCE % os.path.basename(path), CHANGED_ON_DISK)
            return result
    return None
