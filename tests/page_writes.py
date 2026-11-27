"""What a page sends to the one entry point of a write of photo files, POST /api/photos/write (plan one-write-path;
tagpup.web.tagpup_routes.photos_write), built as the pages build it, for a test that drives a write through the route.

    tags(changes, group)      the tag queue's chunk (web/tagpup/pending.js): {path: {"add": [...], "remove": [...]}},
                              each photo named by its path (and its row's "id" when the entry gives one).
    record(library, path)     the page's record of a photo, as opening its folder gives it (photos.scan_folder).
    set_from(record, ...)     one photo's save from that record (web/common/photo-write.js postPhotoMetadata): its title,
                              tags and Date Taken, the record's id and stamp, and the base it read.
    save(library, path, ...)  the two together: the record read now, the save made from it.
    base_of(record)           the base a page keeps of its record (photo-write.js baseOf): the tags and title it read.
    base(library, path)       the base of the photo at `path` as the page has it now: what a save names (a test that
                              calls tagging.save_photo itself names it as the entry point does).
    shift(photos, minutes)    a Time Shift of the photos the page chose (web/tagpup/rename.js applyTimeShift).
    shift_all(library, folder, minutes)
                              a folder's Time Shift for every camera: each photo of the folder, never a clip, as the page
                              counts them (rename.js onCamera with "All Cameras").
    why(reply)                the sentence a page shows of a write that failed or was refused.
    saved(reply)              a save's reply as the page reads it, its one photo's part of `written` at the top.

A test's record is made by the code that makes the page's (CLAUDE.md, "A test's data is made by the code that makes the
real thing"); nothing here writes, and the reply is the route's envelope, read as it is.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import photos  # noqa: E402

ROUTE = "/api/photos/write"
EXIFTOOL = own_home.installed_exiftool()


def tags(changes, group=None):
    """The body of a chunk of the page's tag queue: `changes` {path: {"add": [tags], "remove": [tags]}, an "id" too
    when the test names the row}."""
    body = {"op": "tags", "changes": changes}
    if group is not None:
        body["group"] = group
    return body


def record(library, path, exiftool=EXIFTOOL):
    """The page's record of the photo at `path`, as opening its folder gives it (photos.scan_folder: from its row when
    the row describes the file, else read with ExifTool). `library`, a Library or its database's path."""
    library = library if isinstance(library, Library) else Library(library)
    return photos.scan_folder(library, os.path.dirname(path), exiftool)[paths.key(path)]


def set_from(found, title=None, tags=None, date_taken=None, path=None, **named):
    """The body of one photo's save from its page record `found`: `title` and `tags` as typed (the record's when not
    given), `date_taken` when one is asked, the record's id and stamp, and the base it read. `path`, the one the page
    names (the record's); `named`, a "stamp", "base" or "id" the test names instead of the record's (a stale record)."""
    own = {"id": found.get("id"), "title": (found.get("title") or "") if title is None else title,
           "tags": list(found.get("tags") or []) if tags is None else list(tags),
           "stamp": {"mtime": found["mtime"], "size": found["size"]},
           "base": base_of(found), **named}
    if date_taken is not None:
        own["date_taken"] = date_taken
        if "base" not in named:    # a save of a date names the date the page read (photo-write.js baseSent)
            own["base"] = {**own["base"], "date_taken": found.get("taken") or ""}
    return {"op": "set", "changes": {path or found["path"]: own}}


def save(library, path, title=None, tags=None, date_taken=None, exiftool=EXIFTOOL, **named):
    """The body of a save of the photo at `path` from the record the page has of it now, named by `path` as asked."""
    return set_from(record(library, path, exiftool), title, tags, date_taken, path=path, **named)


def base_of(found):
    """The base a page keeps of its record `found` (web/common/photo-write.js baseOf): the tags and title it read of the
    photo, which its save names and the server compares with the file under its lock (tagging.save_photo `base`)."""
    return {"tags": list(found.get("tags") or []), "title": found.get("title") or ""}


def base(library, path, exiftool=EXIFTOOL):
    """The base of the photo at `path` as the page has it now, on opening its folder (record)."""
    return base_of(record(library, path, exiftool))


def why(reply):
    """The sentence a page shows of a write that did not succeed (photo-write.js postPhotoMetadata): the refusal's
    `error`, else the first photo's error."""
    return reply.get("error") or (reply.get("errors") or [{}])[0].get("why")


def saved(reply):
    """A save's reply as the page reads it (photo-write.js postPhotoMetadata: `{...data, ...data.written[path]}`): the
    one photo's part of `written` -- its new_path, taken, base, stamp, rename_failed -- beside what the reply says of
    the whole write. A reply that wrote nothing is itself."""
    written = list((reply.get("written") or {}).values())
    return {**reply, **written[0]} if len(written) == 1 else reply


def shift(photos_named, minutes, group=None):
    """The body of a Time Shift of `photos_named`, {path: its row's id or None} or a list of paths, by `minutes`."""
    named = photos_named if isinstance(photos_named, dict) else dict.fromkeys(photos_named)
    body = {"op": "shift", "changes": {path: {"id": photo_id} for path, photo_id in named.items()},
            "params": {"minutes": minutes}}
    if group is not None:
        body["group"] = group
    return body


def shift_all(library, folder, minutes, exiftool=EXIFTOOL):
    """The body of a folder's Time Shift for every camera: each photo of its scan, never a clip (rename.js onCamera), by
    its path and no id, as the page sends it (a folder's records carry none). `library`, a Library or its database's
    path."""
    library = library if isinstance(library, Library) else Library(library)
    found = photos.scan_folder(library, folder, exiftool).values()
    return shift([each["path"] for each in found if each["media"] == "photo"], minutes)
