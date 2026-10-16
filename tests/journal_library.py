"""A library for the journal's tests, with rows shaped like the owner's: float32 face and
photo vectors, 6 KB JPEG crops, JSON tags and metadata, a tree with a people root.

Seeded in plain SQL, as the indexer stores rows, never through the journal under test;
the faces by the store's writers (tagpup.store.faces: insert, name, exclude, name_if_unnamed),
so each holds what a detection and a decision leave; each photo's dates and people are then
made by the functions the indexer calls. In a TagPup home of the test's own (tests/own_home.py).
"""
import json
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.store import db, faces, people, person_ids, schema  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402

#: The tables a change can touch, derived ones included, with the order to list them in.
TABLES = {
    "photos": "id", "faces": "id", "face_crops": "face_id", "embeddings": "photo_id, model",
    "suggestions": "photo_id", "tag_taxonomy": "id", "photo_people": "photo_id, position",
}

MODEL = "ViT-B-32|laion2b_s34b_b79k|0|2.0|224"


def vector(seed, size=512):
    """float32 bytes, as the models' vectors are stored."""
    return struct.pack("<%df" % size, *[((seed * 31 + i) % 97) / 97.0 - 0.5 for i in range(size)])


def jpeg(seed):
    """About 6 KB that starts and ends like a JPEG, as a face's crop does."""
    return b"\xff\xd8\xff\xe0" + bytes((seed + i) % 256 for i in range(6000)) + b"\xff\xd9"


class JournalLibrary(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.db_path = self.home.library("harbour.db")
        schema.ensure(self.db_path)
        self.library = Library(self.db_path)
        self.ids = self.seed()

    # ---- Seeding -----------------------------------------------------------------------

    def seed(self):
        conn = db.connect(self.db_path)
        try:
            ids = {}

            def node(tag, parent, has_face):
                ids[tag] = conn.execute("INSERT INTO tag_taxonomy (tag, parent_id, name, has_face) VALUES (?, ?, ?, ?)",
                                        (tag, ids.get(parent), tag.split("/")[-1], has_face)).lastrowid

            node("People", None, 1)
            node("People/Rowan Thackeray", "People", 1)
            node("People/Maren Oakhollow", "People", 1)
            node("Friends", None, 0)
            node("Friends/Imogen Vale", "Friends", 1)
            node("Rowan Thackeray", None, 0)
            node("Activity", None, 0)
            node("Activity/Sailing", "Activity", 0)

            def photo(name, tags, taken, stamp):
                raw = {"XMP:Subject": tags, "IPTC:Keywords": tags, "EXIF:DateTimeOriginal": taken,
                       "XMP:Title": "Start line", "File:FileSize": "4.6 MB"}
                return conn.execute(
                    "INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata, document_id)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (os.path.join(self.home.root, "Harbour Regatta", name), 1727000000.123456 + stamp,
                     4812345 + stamp, json.dumps(tags), json.dumps(["Start line"]), json.dumps(raw),
                     "xmp.did:%08d-3f2a-4c1b-9d7e-5a6b7c8d9e0f" % stamp)).lastrowid

            ids["start"] = photo("regatta_001.jpg", ["People/Rowan Thackeray", "Activity/Sailing"],
                                 "2024:06:01 10:00:00", 1)
            ids["finish"] = photo("regatta_002.jpg", ["Friends/Imogen Vale", "People/Rowan Thackeray"], "2024:06:01 11:30:00", 2)
            ids["prize"] = photo("regatta_003.jpg", ["Activity/Sailing"], "2024:06:02 09:15:00", 3)

            def face(photo_id, n, crop=True):
                """A detected face, as the indexer records one (its crop cut, unless it is never shown)."""
                path = conn.execute("SELECT path FROM photos WHERE id = ?", (photo_id,)).fetchone()[0]
                return faces.insert(conn, path, [120 + n, 80, 260 + n, 240], vector(n), crop=jpeg(n) if crop else None,
                                    prob=0.99873 - n / 1000.0)

            ids["kept"] = face(ids["start"], 1)
            faces.name(conn, [ids["kept"]], ids["People/Rowan Thackeray"])   # a person's decision
            ids["copy"] = face(ids["start"], 2)
            ids["excluded"] = face(ids["finish"], 3, crop=False)
            faces.exclude(conn, [ids["excluded"]], "not a face")
            # Named on a face only, by a guess: the photo's keywords do not name her.
            ids["named"] = face(ids["finish"], 4)
            faces.name_if_unnamed(conn, ids["named"], ids["People/Maren Oakhollow"])
            for photo_id in (ids["start"], ids["finish"], ids["prize"]):
                conn.execute("INSERT INTO embeddings (photo_id, model, mtime, size, vector) VALUES (?, ?, ?, ?, ?)",
                             (photo_id, MODEL, 1727000000.5, 4812345, vector(100 + photo_id)))
            conn.execute("INSERT INTO suggestions (photo_id, tags, people, title, raw, before_consensus, model, created)"
                         " VALUES (?, ?, '[]', 'Sailing at the start', '{}', 0, ?, '2026-09-24 10:00:00')",
                         (ids["prize"], json.dumps([{"tag": "Activity/Sailing", "score": 0.61}]), MODEL))
            # As the indexer leaves a photo: dated, and its people by the rule; and the faces as
            # naming them left them, holding their person's id (tagpup.store.person_ids).
            store_photos.date_photos(conn)
            people.rebuild(conn)
            person_ids.sync(conn)
            conn.commit()
            return ids
        finally:
            conn.close()

    # ---- Reading -----------------------------------------------------------------------

    def query(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def execute(self, sql, params=()):
        conn = db.connect(self.db_path)
        try:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def dump(self):
        """Every row of every table a change can touch, each value with the type SQLite
        stored it as: two dumps are equal only if the tables are, byte for byte."""
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            found = {}
            for table, order in TABLES.items():
                columns = [row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)]
                typed = ", ".join("%s, typeof(%s)" % (c, c) for c in columns)
                found[table] = conn.execute("SELECT %s FROM %s ORDER BY %s" % (typed, table, order)).fetchall()
            return found
        finally:
            conn.close()

    def changes(self):
        return self.query("SELECT id, operation, status FROM changes ORDER BY id")

    def people_of(self, photo_id):
        return [name for (name,) in self.query(
            "SELECT name FROM photo_people WHERE photo_id = ? ORDER BY position", (photo_id,))]
