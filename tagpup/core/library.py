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
    def taxonomy_file(self):
        """The tag tree's JSON copy, as the servers name it: photo_index_taxonomy.json.

        taxonomy.TagTaxonomy and the CLI call the main library's photo_taxonomy.json
        instead, and the owner's data folder holds both (docs/findings.md, #13). Both
        go when the tag tree lives only in the database.
        """
        return self._beside("_taxonomy.json")

    @property
    def face_trace_file(self):
        """Where clustering records how it named each face, for this library alone."""
        return self._beside("_face_resolution_trace.json")

    @property
    def backups(self):
        """Where copies go before a bulk write: beside the library, never in the code."""
        return os.path.join(self.folder, "backups")

    def _beside(self, suffix):
        return os.path.splitext(self.path)[0] + suffix
