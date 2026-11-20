"""A write of photo files, by operation: the service of the one entry point (POST /api/photos/write; plan one-write-path,
cuts 2a and 2b), which a library view's bulk edit of tags or people calls too (tagpup.services.bulk_edit). A dispatcher
only: each operation is written by the service that owns it, as it always was.

    tags   each photo's own tags to add and take off, as ONE change of photo files (tagging.change_tags_of)
    set    ONE photo's title, tags and Date Taken from the record the page read, its stamp and base checked against the
           file under the lock (tagging.save_photo); no page sets several photos at once
    shift  each photo's Date Taken moved by `params["minutes"]`, as ONE change of photo files (photos.shift_date_taken);
           which photos a folder's Time Shift moves (its camera's) is its caller's to choose

`changes` is {path: (id, the operation's own fields)}: a photo is named by its path, and by its row's id when it has one
(Just look's photos have none: answer 5 of the plan). An id whose row is not at that path now -- the photo renamed,
moved or deleted since the page read it -- refuses the whole write, as a stale record's save is refused (CHANGED_ON_DISK,
409), nothing written: the page's record is not the file's. A photo named by its path alone is written as that path's
file, whatever row it has.

Which hold of the lock of changes of photo files a write runs under is its caller's to decide, never this module's: it
takes no lock. The route (tagpup.web.tagpup_routes._write: a page's write, and a folder's Time Shift) holds it through
the whole write (file_changes.batch_exclusively), so a named id is checked under the same hold as the write and no
rename comes between (_moved), and no writer comes in between its files. A library view's bulk edit
(tagpup.services.bulk_edit._write) names no id and holds no lock: each chunk takes it as its service does, which lets a
writer that waits in between the chunks (file_changes.let_others_in); from its plan on, its files are refused to another
writer until written (file_changes.unsettled).
"""
import os

from tagpup.core import paths
from tagpup.core.result import CHANGED_ON_DISK, Result
from tagpup.services import selection, tagging
from tagpup.services import photos as photo_actions

TAGS, SET, SHIFT = "tags", "set", "shift"
#: The operations, each with the name a batch of it is listed under (Activity) while it waits for its turn.
OPERATIONS = {TAGS: "bulk tags", SET: "save photo", SHIFT: "time shift"}

#: The refusal of a photo whose id no longer names the file at the path the page sent.
MOVED_SENTENCE = "%s is not the photo this page read (renamed, moved or deleted since): reload it first."


def write(library, op, changes, exiftool_path, rename_format, group=None, stop_at_first_error=False, params=None,
          operation=tagging.ADD_TO_ALL, persons=None):
    """Write `changes` ({path: (id or None, fields)}) by `op`, in `library`, under the caller's hold of the lock of
    changes of photo files when it names an id (the module's docstring): a Result as the operation's service gives it. `tags`' fields are {"add": [tag], "remove": [tag]}, `group` the token
    of the page's action this is a chunk of (journal.GROUP), `stop_at_first_error` False: a photo that cannot be read or
    written (a share that does not answer) is its own error and the others are written; `operation` the journal's name
    of the change (tagging.ADD_TO_ALL unless named) and `persons` {paths.key(path): the tags added that are people}
    (tagging.change_tags_of): a bulk edit's (bulk_edit.OPERATIONS). `set`'s are {"title", "tags", "date_taken", "stamp",
    "base"} of the one photo, as tagging.save_photo takes them. `shift`'s are none, its `params` {"minutes": whole
    minutes}. The ids are checked under the caller's hold of the lock, the write's own, so a rename cannot come between
    (_moved)."""
    moved = _moved(library, changes)
    if moved is not None:
        return moved
    if op == TAGS:
        return tagging.change_tags_of(
            library, {path: (own.get("add", []), own.get("remove", [])) for path, (_id, own) in changes.items()},
            exiftool_path, operation=operation, stop_at_first_error=stop_at_first_error,
            persons=persons, group=group)
    if op == SHIFT:
        return photo_actions.shift_date_taken(library, list(changes), params["minutes"], exiftool_path)
    ((path, (_id, own)),) = changes.items()
    return tagging.save_photo(library, path, own["title"], own["tags"], own["date_taken"], exiftool_path,
                              rename_format, own["stamp"], own["base"])


def _moved(library, changes):
    """A Result refused as CHANGED_ON_DISK, nothing written, when a photo named by id is not at the path it is named by:
    its row is at another path now, or gone. None when every id is where the page says (or none is given). The caller
    holds the lock of changes of photo files when it names an id, and through the write after, so no rename comes
    between this look and the write."""
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
