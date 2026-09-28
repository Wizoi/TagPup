"""Making a library, and bringing one up to date: what the picker's Create does, and
what opening a library by its URL does first. And whether a library holds a folder: the
one answer every entry point asks before work that would make rows for its photos.

The old server made a library by opening it through PhotoIndex and then seeding its
tag tree, two old modules the web layer may not import (tests/test_layers.py). A new
library's tables come from tagpup.store.schema, its first nodes -- one face root,
People -- from tagpup.store.taxonomy.seed, and its settings, the defaults, from
tagpup.services.settings.
"""
import os

from tagpup.core import paths, validation
from tagpup.core.library import Library, picker_name
from tagpup.core.result import Result
from tagpup.files import images
from tagpup.services import settings
from tagpup.store import db, photos, schema, taxonomy


def bring_up_to_date(db_path):
    """The library's tables, made or migrated (tagpup.store.schema.ensure). Returns the
    names of the migrations applied: none, usually."""
    return schema.ensure(db_path)


def create(db_path):
    """Make the library at `db_path`: its folder, its tables, and its first nodes. A
    library already there is brought up to date and its tree left alone.

    Refused, and nothing made, when its name -- the file's, without ".db" or a test
    prefix -- may not name a library (tagpup.core.validation). changed: 1 when the
    library was made, 0 when it was there already."""
    result = Result(attempted=1)
    problem = validation.problem("library name", picker_name(os.path.basename(db_path)))
    if problem:
        result.refuse(problem)
        return result
    existed = os.path.exists(db_path)
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    bring_up_to_date(db_path)
    taxonomy.seed(db_path)
    if not existed:
        # A new library holds the defaults from the start (tagpup.services.settings): it
        # was made with nothing else, whatever an old config.ini beside it says.
        settings.stamp(Library(db_path), None)
    result.changed = 0 if existed else 1
    return result



# ---- Does a library hold a folder -------------------------------------------------------
#
# The owner opened a folder of another library's in TagPup with kr-track selected, and
# Suggest made 25 rows for its photos in kr-track without asking; the folder watcher then
# kept the folder in step, read its tags and grew kr-track's tag tree from them
# (2026-09-28). A folder is a library's when the library holds a photo directly in it
# (tagpup.store.photos.holds_folder); these say which folders are, for a page to ask
# before anything is done in one.

def _read(db_path, read, default):
    """`read(conn)` on a read-only connection to the library at `db_path`, or `default`
    for one that is not there or cannot be read just now."""
    if not os.path.exists(db_path):
        return default
    try:
        conn = db.connect(db.readonly_uri(db_path), uri=True)
    except Exception:
        return default
    try:
        return read(conn)
    except Exception:
        return default
    finally:
        conn.close()


def not_held(library, photo_paths):
    """The folders of `photo_paths` the library holds no photo directly in, each once, as
    stored, in path order: those it must be asked to add before any row is made for their
    photos (tagpup.store.photos.ensure_row). One query a folder, not a photo."""
    folders = {}
    for photo_path in photo_paths:
        folder = os.path.dirname(paths.stored(photo_path))
        folders.setdefault(paths.key(folder), folder)
    if not folders:
        return []
    ordered = [folders[key] for key in sorted(folders)]
    return _read(library.path, lambda conn: [f for f in ordered if not photos.holds_folder(conn, f)], ordered)


def held_under(library, folder):
    """How many photos under `folder`, at any depth, the library holds; 0 for one not there."""
    return _read(library.path, lambda conn: photos.count_under(conn, folder), 0)


def _under_any(folder, folders):
    return any(paths.same(folder, other) or paths.is_under(folder, other) for other in folders)


def membership(library, folder, roots=(), ignored=(), others=()):
    """What `library` holds of `folder`, for the page to say before anything is done in it:
    {"library" (its name, as the address bar has it), "folder" (as stored), "photos" (on
    disk under it, at any depth), "photos_held" (the library's rows under it),
    "photos_not_held" (photos in folders under it the library holds none directly in),
    "folders_not_held", "first_not_held" (the first such folder, or None), "has_roots",
    "under_roots" (the folder is one of `roots` or under one), "ignored" (likewise, of
    `ignored`), "others": [{"library", "photos"}] -- each library of `others` holding
    photos under it, and how many}. One walk of the folder, no file read; the other
    libraries are only read."""
    folder = paths.stored(folder)
    on_disk = images.photos_under(folder)
    unheld = not_held(library, on_disk)
    unheld_keys = {paths.key(each) for each in unheld}
    elsewhere = []
    for other in others:
        if other == library:
            continue
        count = held_under(other, folder)
        if count:
            elsewhere.append({"library": picker_name(os.path.basename(other.path)), "photos": count})
    return {
        "library": picker_name(os.path.basename(library.path)),
        "folder": folder,
        "photos": len(on_disk),
        "photos_held": held_under(library, folder),
        "photos_not_held": sum(1 for p in on_disk if paths.key(os.path.dirname(p)) in unheld_keys),
        "folders_not_held": len(unheld),
        "first_not_held": unheld[0] if unheld else None,
        "has_roots": bool(roots),
        "under_roots": _under_any(folder, roots),
        "ignored": _under_any(folder, ignored),
        "others": elsewhere,
    }
