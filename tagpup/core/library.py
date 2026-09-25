"""A photo library: its database file, and the names of the things that belong to it.

A library was a bare path string, and every part of the program spelled what goes with
it for itself. Four copies of the key the servers file per-library state under; eight
of "<db>_taxonomy.json"; and the backup folder, found two folders above whichever
module was asking. When the database module moved into tagpup/store/, that folder
moved with it, into the code. Library names all of these once.

Names only: no connection is opened here. It takes the path as given, so every
file name it derives is spelled exactly as before.
"""
import os
import re


class Library:
    def __init__(self, db_path):
        if not db_path:
            raise ValueError("a library needs its database file")
        self.path = db_path

    def __repr__(self):
        return "Library(%r)" % self.path

    def __eq__(self, other):
        return isinstance(other, Library) and self.key == other.key

    def __hash__(self):
        return hash(self.key)

    @property
    def name(self):
        """What the address bar and the library picker call it: photo_index."""
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def folder(self):
        return os.path.dirname(os.path.abspath(self.path))

    @property
    def key(self):
        """One spelling per library, for filing in-memory state under.

        The same file reached as `data/photo_index.db` and as `C:\\...\\DATA\\photo_index.db`
        has one key.
        """
        return os.path.abspath(self.path).replace("\\", "/").lower()  # not a path: the library, as a registry key

    @property
    def face_trace_file(self):
        """Where clustering records how it named each face, for this library alone."""
        return self._beside("_face_resolution_trace.json")

    @property
    def backups(self):
        """Where copies go before a bulk write: beside the library, never in the code."""
        return os.path.join(self.folder, "backups")

    @property
    def locks(self):
        """Where indexers of the libraries in this folder take their per-photo write locks.

        It was data/locks under the working directory. The indexer a server starts runs
        in the code's folder, so one started by hand from anywhere else locked in another
        folder, and the two could write the same photo at once.
        """
        return os.path.join(self.folder, "locks")

    def _beside(self, suffix):
        return os.path.splitext(self.path)[0] + suffix


# The library picker. Both servers and the runner each listed the libraries in the data
# folder, and chose and created them, with their own copy of these rules -- five copies
# among them of a list of names to hide, which went with the tests' files (#14).

#: What a test library's file name starts with. A server started on one shows and
#: creates only test libraries, under their names without it.
TEST_PREFIX = "test_"

def for_mode(file_name, test_mode):
    """`file_name`, a library's file name, as test mode names it or not: with
    TEST_PREFIX, or without. The runner, the TagPup server and the CLI each added and
    took off the prefix by hand (docs/findings.md, #74)."""
    plain = file_name[len(TEST_PREFIX):] if file_name.startswith(TEST_PREFIX) else file_name
    return TEST_PREFIX + plain if test_mode else plain


def is_test_library(db_path):
    """Is the library at `db_path` (or of that file name) a test library?"""
    return os.path.basename(db_path).startswith(TEST_PREFIX)


#: The one library a picker offers when the data folder holds none.
FIRST_LIBRARY = "photo_index"


def picker_names(file_names, test_mode):
    """The library names a picker offers, from the file names in a data folder, sorted.

    A server started on a test library offers only the test libraries, by their names
    without the prefix; any other offers only the rest.

    Every .db file is offered. A list of names was hidden (NOT_LIBRARIES): files the
    test suite left in the checkout's data folder, and a tag-embedding cache nothing
    has made since the cache moved into the library. The tests keep their libraries in
    homes of their own now (docs/findings.md, #14).
    """
    names = []
    for file_name in file_names:
        if not file_name.endswith(".db"):
            continue
        if test_mode:
            if not file_name.startswith(TEST_PREFIX):
                continue
            name = os.path.splitext(file_name[len(TEST_PREFIX):])[0]
        else:
            if file_name.startswith(TEST_PREFIX):
                continue
            name = os.path.splitext(file_name)[0]
        if name not in names:
            names.append(name)
    return sorted(names) or [FIRST_LIBRARY]


def picker_name(file_name):
    """The name a picker shows for a library file: photo_index.db -> photo_index."""
    name = os.path.splitext(file_name)[0]
    return name[len(TEST_PREFIX):] if name.startswith(TEST_PREFIX) else name


def file_name_for(name):
    """The file a library name from a page names: "Harbour" -> "Harbour.db". A test
    prefix is the server's to add, never the page's, and is taken off."""
    if not name.endswith(".db"):
        name = name + ".db"
    if name.startswith(TEST_PREFIX):
        name = name[len(TEST_PREFIX):]
    return name


#: The first part of a URL the servers route themselves. A library is reached at
#: /<its name>/, so one called any of these could be created and never opened: its URL
#: reaches the route (docs/findings.md, #73). Whatever the case, as file names are.
ROUTES = frozenset({"api", "gui", "gui_tagpup"})


def problem_with_new_name(file_name):
    """Why a library cannot be created under this file name, or None if it can."""
    if not re.match(r"^[a-zA-Z0-9_\-]+\.db$", file_name):
        return "Invalid characters in database name"
    if picker_name(file_name).lower() in ROUTES:
        return "'%s' is the name of one of the app's own pages; choose another" % picker_name(file_name)
    return None
