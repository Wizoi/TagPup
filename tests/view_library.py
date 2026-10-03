"""A library for the library views' tests: made by the code that makes the real thing, in a home of its own.

A photo is what the indexer records of a read (tests/photo_rows.py: add_read, then store.photos.record_indexed,
which keeps photo_people and the derived tables as it does for real), or what Suggest makes of a photo it never
read (add_unread); a tag is a node made by store.taxonomy.add_path. A photo may be a real JPEG on disk (the
thumbnail cache decodes it) or only a row, as photo_index holds rows for photos indexed earlier.

Fictional names only: the libraries are photographs of real people, many of them minors.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import photo_rows  # noqa: E402
from PIL import Image  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db, taxonomy  # noqa: E402


def make_jpeg(path, shade=(90, 110, 130), size=(800, 600)):
    """A real JPEG at `path`, `size` pixels, of one colour."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, shade).save(path, "JPEG")
    return path


class ViewLibrary:
    """A library `name` in a home of its own (or `home`), with its connection held open for the test's writes."""

    def __init__(self, testcase, name="harbour", home=None):
        self.home = home or own_home.for_test(testcase)
        self.path = self.home.library(name + ".db")
        library_actions.create(self.path)
        self.library = Library(self.path)
        self.pictures = os.path.join(self.home.root, name, "Pictures")
        self.conn = db.connect(self.path)
        testcase.addCleanup(self.conn.close)

    def tree(self, *tags, face_root=None):
        """Nodes of the tag tree, each with the levels above it. `face_root` is a root that files people."""
        for tag in tags:
            taxonomy.add_path(self.conn, tag, root_has_face=1 if tag.split("/")[0] == face_root else 0)
        self.conn.commit()

    def photo(self, folder, name, taken=None, tags=(), real=False, shade=(90, 110, 130), size=(800, 600), at=None):
        """The id of a photo recorded as the indexer records a read of `folder`/`name` under Pictures (`at`: the whole
        path instead), with Date Taken `taken` (None: no date) and keywords `tags`; a real JPEG on disk when `real`."""
        path = at or os.path.join(self.pictures, folder, name)
        if real:
            make_jpeg(path, shade, size)
        fields = {"XMP:Subject": list(tags)}
        if taken is not None:
            fields["EXIF:DateTimeOriginal"] = taken
        photo_id = photo_rows.add_read(self.conn, path, fields)
        self.conn.commit()
        return photo_id

    def unread(self, folder, name):
        """The id of the row Suggest makes for a photo the index never read."""
        photo_id = photo_rows.add_unread(self.conn, os.path.join(self.pictures, folder, name))
        self.conn.commit()
        return photo_id

    def path_of(self, photo_id):
        return self.conn.execute("SELECT path FROM photos WHERE id = ?", (photo_id,)).fetchone()[0]

    def rows(self, sql, *params):
        return self.conn.execute(sql, params).fetchall()
