"""Libraries that hold roots, for the roots tests (docs/ARCHITECTURE.md, "Roots and machines").

A `Side` is a library and the folder of photos it describes, in a TagPup home of the test's
own: a few real JPEGs (the flows that write files run real ExifTool on them) and rows for
many more that are not on disk, as photo_index holds rows for photos indexed earlier; faces,
suggestions, added folders, damaged files, the folder settings and a journal of changes made
before anything was adopted -- all made by the code that makes them for real
(tests/photo_rows.py, tagpup.services). Two sides built alike hold the same photos under
different folders, one of them adopted by a root and the other not: the same flow on both
must give the same answer once each side's own folder is taken out of it (`norm`).

Fictional names only: the libraries are photographs of real people, many of them minors.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import add_face  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.services import tagging  # noqa: E402
from tagpup.store import added_folders, damaged_files, db, suggestions  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()

#: The share's own address, as the owner's roots have it, and the root's name.
ADDRESS = "\\\\idziserver\\Pictures\\Pictures"
NAME = "pictures"

#: Folders of photos under a side's Pictures folder.
FOLDERS = ("2024 Regatta", "2024 Harbour", os.path.join("Trips", "2025 Coast"))

#: What the indexer read of each photo: keywords and a Date Taken.
READ = {"XMP:Subject": ["Activity/Sailing"], "IPTC:Keywords": ["Activity/Sailing"],
        "EXIF:DateTimeOriginal": "2024:06:01 10:00:00"}


def read(path):
    """What ExifTool answers of the photo at `path`: READ, and the SourceFile it gives, with
    forward slashes whatever the machine's separator."""
    return dict(READ, SourceFile=path.replace(os.sep, "/"))


def doctor():
    """tools/doctor.py, loaded by its path: a program to run, not a module of the package."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("doctor", os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "doctor.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def machine():
    """This machine's map, as the CLI hands it to the service (tagpup.config), in the
    test's own home."""
    return roots_service.Machine(config.machine_roots, config.add_machine_root, config.machine_roots_path)


def make_jpeg(path, shade=(90, 110, 130)):
    from PIL import Image
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (16, 12), shade).save(path, "JPEG")


class Side:
    """One library and its photos. `real` JPEGs on disk in each folder, `bulk` more rows for
    photos not on disk; `outside` photos in a folder beside Pictures, under no root."""

    def __init__(self, home, label, real=2, bulk=30, outside=2, share=None):
        """`share`: another Side whose folders this library holds too -- a second library over
        the same photos, as two libraries on one machine can hold one folder."""
        self.home, self.label = home, label
        self.base = share.base if share else os.path.join(home.root, label)
        self.pictures = os.path.join(self.base, "Pictures")
        self.outside_folder = os.path.join(self.base, "Elsewhere")
        self.db_path = home.library(label + ".db")
        self.library = Library(self.db_path)
        library_actions.create(self.db_path)
        self.real, self.rows_only, self.outside = [], [], []
        conn = db.connect(self.db_path)
        try:
            for number, folder in enumerate(FOLDERS):
                for n in range(real):
                    path = os.path.join(self.pictures, folder, "IMG_%d%03d.jpg" % (number + 1, n + 1))
                    if not share:
                        make_jpeg(path, (80 + 20 * number, 100 + n, 120))
                    self.real.append(path)
                    photo_rows.add_read(conn, path, read(path))
                for n in range(bulk):
                    path = os.path.join(self.pictures, folder, "Earlier_%d%03d.jpg" % (number + 1, n + 1))
                    self.rows_only.append(path)
                    photo_rows.add_read(conn, path, read(path))
            for n in range(outside):
                path = os.path.join(self.outside_folder, "Loose_%03d.jpg" % (n + 1))
                if not share:
                    make_jpeg(path, (10, 20 + n, 30))
                self.outside.append(path)
                photo_rows.add_read(conn, path, read(path))
            # Faces on some of them, decided by hand and not.
            for number, path in enumerate(self.real + self.rows_only[:5]):
                add_face(conn, path, [1, 2, 11, 12], name="Rowan Thackeray" if number % 2 == 0 else None,
                         name_source="manual" if number % 2 == 0 else None, embedding=b"\x00" * 16)
            conn.commit()
            # What Suggest offered a photo, with the paths its output carries.
            neighbour = (self.rows_only or self.real)[0]
            suggestions.put(conn, self.real[0], {
                "tags": ["Activity/Sailing"], "people": [], "title": "Start",
                "raw_suggestions": {"path": self.real[0], "suggested_tags": [],
                                    "nearest_neighbors": [{"path": neighbour, "similarity": 0.8}]},
                "raw_before_consensus": True})
            added_folders.record(conn, os.path.join(self.pictures, "Added later"), subfolders=True)
            damaged_files.record(conn, os.path.join(self.pictures, FOLDERS[0], "Cut short.jpg"), (1.0, 100),
                                 "truncated", "the picture ends early")
            damaged_files.record(conn, os.path.join(self.pictures, FOLDERS[1], "Cut short.jpg"), (1.0, 100),
                                 "truncated", "the picture ends early")
            conn.commit()
        finally:
            conn.close()
        library_settings.change(self.library, {
            library_settings.ROOTS: self.pictures,
            library_settings.IGNORED: os.path.join(self.pictures, "Not these")}, apply=True)

    # ---- What was done before anything was adopted -----------------------------------------

    def tagged_before(self):
        """A journaled change of photo files made before any adoption: its change_files rows
        hold native paths, as every library's did. Needs ExifTool."""
        return tagging.change_tags(self.library, self.real[:2], ["Harbour"], [], EXIFTOOL)

    # ---- Adopting ----------------------------------------------------------------------------

    def adopt(self, apply=True, location=None):
        return roots_service.adopt(self.library, NAME, ADDRESS, location or self.pictures, machine(), apply=apply)

    # ---- Reading ------------------------------------------------------------------------------

    def rows(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def dump(self, leave_out=()):
        """Every row of every table of the library, as the file holds it: {table: [rows]}. What
        a test compares to know the library is exactly as it was (the tables in `leave_out`
        aside)."""
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            tables = {name: sql for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")}
            # A table WITHOUT ROWID (photo_tags) has no rowid to order by: its rows are in key order.
            return {table: [tuple(row) for row in conn.execute("SELECT * FROM %s ORDER BY %s" % (
                table, "1, 2" if "WITHOUT ROWID" in sql.upper() else "rowid"))]
                    for table, sql in tables.items() if table not in leave_out}
        finally:
            conn.close()

    def raw_paths(self, table="photos", column="path"):
        return [value for (value,) in self.rows("SELECT %s FROM %s ORDER BY 1" % (column, table))]

    def norm(self, value, base=None):
        """`value` with this side's folder taken out of every path in it, so what two sides
        say can be compared: strings, and the lists, tuples, dicts and sets of them. A time is
        masked: the two sides' files were written at different moments."""
        base = base or self.base
        if isinstance(value, float):
            return "<time>"
        if isinstance(value, str):
            for each in (base, base.replace(os.sep, "/")):
                value = re.sub(re.escape(each), "<BASE>", value, flags=re.IGNORECASE)
            return value
        if isinstance(value, dict):
            return {self.norm(k, base): self.norm(v, base) for k, v in value.items()}
        if hasattr(value, "_fields"):   # a namedtuple, such as a Ref
            return type(value)(*(self.norm(each, base) for each in value))
        if isinstance(value, (list, tuple)):
            return type(value)(self.norm(each, base) for each in value)
        if isinstance(value, (set, frozenset)):
            return {self.norm(each, base) for each in value}
        return value

    def library_roots(self):
        from tagpup.store import roots as store_roots
        return store_roots.listing(self.db_path)
