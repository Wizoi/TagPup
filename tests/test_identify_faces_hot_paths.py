"""The data paths behind Identify Faces, and what they are allowed to touch.

This screen is the one that meets a real library head on: 225,000 face rows, 189,000 of
them still nameless, and every button on it ran work proportional to all of them. The
tests here pin the three things that made it slow, each of which is also wrong for a
reason that has nothing to do with speed:

* the faces table had no index the identify queries could use, so counting the
  excluded bucket read every row;
* the queue read all 189,000 embeddings -- 380 MB of BLOB -- to produce a list of
  counts that never looks at a vector;
* matching fell back to `photo_path LIKE ?`, which is not a full-table scan by
  accident but by definition, and which silently treats `_` in a filename as a
  wildcard. Every camera on earth writes `IMG_1234.jpg`;
* clicking a card re-read all 35,758 named embeddings and rebuilt a 73 MB matrix to
  ask who the face resembles, on every click.

The third is a correctness test wearing a performance test's clothes: a LIKE lookup on
a path does not mean "this photo". The fourth brought a shared matrix with it, and the
tests below pin what sharing it must not change: a face is not a suggestion for
itself, and naming somebody has to be visible in the next answer.
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import tuner_server
from index import PhotoIndex
from tuner_server import start_server as start_tuner_server, set_active_db_path


class TestFacesTableIsIndexedForIdentifying(unittest.TestCase):
    """The identify screen filters on `excluded` and `name`. An index has to cover it.

    Without one, `SELECT COUNT(*) FROM faces WHERE excluded = 1` -- which the sidebar
    asks for on every load -- scans 225,000 rows carrying a 2 KB embedding and a 6 KB
    JPEG crop apiece. Measured on the real library: 0.37s for a number that is almost
    always zero, and the queue asks four such questions before it draws anything.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="tagpup_idx_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db = os.path.join(self.tmpdir, "photo_index.db")

    def indexes_on_faces(self, db_path):
        conn = sqlite3.connect(db_path)
        try:
            names = [r[1] for r in conn.execute("PRAGMA index_list(faces)")]
            return {
                n: [c[2] for c in conn.execute("PRAGMA index_info(%s)" % n)]
                for n in names
            }
        finally:
            conn.close()

    def test_a_new_database_is_created_with_the_identify_index(self):
        pi = PhotoIndex(db_path=self.db)
        pi.load()
        pi.close()

        indexes = self.indexes_on_faces(self.db)
        self.assertIn(
            "idx_faces_identify", indexes,
            "faces has no index covering the identify filter; every count scans the table",
        )
        self.assertEqual(
            ["excluded", "name"], indexes["idx_faces_identify"],
            "the index has to lead with `excluded` so a covering count is possible",
        )

    def test_an_existing_database_gains_the_index_on_open(self):
        """The libraries that need this most are the ones that already exist."""
        pi = PhotoIndex(db_path=self.db)
        pi.load()
        pi.close()

        conn = sqlite3.connect(self.db)
        conn.execute("DROP INDEX IF EXISTS idx_faces_identify")
        conn.commit()
        conn.close()
        self.assertNotIn("idx_faces_identify", self.indexes_on_faces(self.db))

        pi = PhotoIndex(db_path=self.db)
        pi.load()
        pi.close()
        self.assertIn(
            "idx_faces_identify", self.indexes_on_faces(self.db),
            "opening an older database did not add the index it is missing",
        )

    def test_the_excluded_count_is_answered_from_the_index_alone(self):
        """A covering index means the count never reaches the row, crop and all."""
        pi = PhotoIndex(db_path=self.db)
        pi.load()
        pi.close()

        conn = sqlite3.connect(self.db)
        try:
            plan = " ".join(
                r[3] for r in conn.execute(
                    "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM faces WHERE excluded = 1")
            )
        finally:
            conn.close()
        self.assertIn("idx_faces_identify", plan, "plan was: %s" % plan)
        self.assertIn(
            "COVERING", plan.upper(),
            "the count still reaches the table rows; plan was: %s" % plan,
        )


def unit_vector(seed):
    """A normalised 512-dim embedding, the shape the face model produces."""
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


class MatchingTestBase(unittest.TestCase):
    """A running tuner server over a throwaway database."""

    TEST_PORT = 9094
    TEST_DB = os.path.join(WORKSPACE_DIR, "data", "test_identify_hot_paths.db")

    @classmethod
    def setUpClass(cls):
        pi = PhotoIndex(db_path=cls.TEST_DB)
        pi.load()
        pi.close()
        cls.server_thread = threading.Thread(
            target=start_tuner_server,
            kwargs={
                "port": cls.TEST_PORT,
                "db_path": cls.TEST_DB,
                "gui_dir": os.path.join(WORKSPACE_DIR, "gui"),
            },
            daemon=True,
        )
        cls.server_thread.start()
        time.sleep(1.0)

    @classmethod
    def tearDownClass(cls):
        set_active_db_path(None)
        for p in (cls.TEST_DB, cls.TEST_DB.replace(".db", "_taxonomy.json")):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="tagpup_hot_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.addCleanup(set_active_db_path, None)
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute("DELETE FROM faces")
        conn.execute("DELETE FROM photos")
        conn.commit()
        conn.close()
        # The identify cache is process-wide and outlives the rows it describes, so a
        # payload left by the last test can be served to this one.
        for key in list(tuner_server.TunerHTTPRequestHandler.identify_cache.keys()):
            tuner_server.TunerHTTPRequestHandler.identify_cache.pop(key, None)

    def post(self, path, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.TEST_PORT, path),
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")

    def add_photo(self, name, people=()):
        path = os.path.join(self.tmpdir, name).replace("\\", "/")
        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions,"
            " raw_metadata) VALUES (?, 1.0, 1, '[]', ?, '[]', '{}')",
            (path, json.dumps(list(people))),
        )
        conn.commit()
        conn.close()
        return path

    def add_face(self, photo, seed, name=None):
        conn = sqlite3.connect(self.TEST_DB)
        cur = conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name, prob)"
            " VALUES (?, '[0,0,10,10]', ?, ?, 0.99)",
            (photo, unit_vector(seed).tobytes(), name),
        )
        fid = cur.lastrowid
        conn.commit()
        conn.close()
        return fid

    def name_of(self, face_id):
        conn = sqlite3.connect(self.TEST_DB)
        try:
            row = conn.execute(
                "SELECT name FROM faces WHERE id = ?", (face_id,)).fetchone()
        finally:
            conn.close()
        return row[0] if row else None


class TestTheQueueDoesNotReadEmbeddings(MatchingTestBase):
    """Counting who is waiting does not require anybody's face vector.

    The queue endpoint selected `f.embedding` for all 189,000 nameless faces and
    normalised every one of them -- 380 MB of BLOB read and 189,000 numpy allocations
    -- then used the result for nothing but a non-empty test. It groups faces by the
    tags their photo carries and counts them; no vector is ever compared to anything.

    Tested by giving a face an embedding that is real bytes but not a vector. Code
    that only asks whether an embedding is there is unbothered; code that parses every
    one of them cannot get past it. That is the distinction worth pinning, and it is
    also a robustness property in its own right -- one damaged row should not take the
    whole queue down.
    """

    def unreadable_embedding_face(self, photo):
        conn = sqlite3.connect(self.TEST_DB)
        cur = conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name, prob)"
            " VALUES (?, '[0,0,10,10]', ?, NULL, 0.99)",
            (photo, b"\x01\x02\x03"),   # not a whole number of float32s
        )
        fid = cur.lastrowid
        conn.commit()
        conn.close()
        return fid

    def queue(self):
        with urllib.request.urlopen(
            "http://127.0.0.1:%d/api/unmatched-faces/people" % self.TEST_PORT,
            timeout=30,
        ) as r:
            return json.loads(r.read().decode("utf-8"))

    def test_the_queue_opens_when_a_face_has_an_unreadable_embedding(self):
        photo = self.add_photo("IMG_4242.jpg")
        self.add_face(photo, seed=11)
        self.unreadable_embedding_face(photo)

        buckets = self.queue()
        self.assertTrue(
            any(b["name"] == "Unknown Faces" for b in buckets),
            "the queue did not come back with its Unknown Faces bucket: %s" % (buckets,),
        )

    def test_a_face_with_no_embedding_is_not_counted_as_waiting(self):
        """Not a performance point: a face with no vector cannot be identified."""
        photo = self.add_photo("IMG_4343.jpg")
        self.add_face(photo, seed=12)

        conn = sqlite3.connect(self.TEST_DB)
        conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name, prob)"
            " VALUES (?, '[0,0,10,10]', NULL, NULL, 0.99)", (photo,))
        conn.commit()
        conn.close()

        unknown = [b for b in self.queue() if b["name"] == "Unknown Faces"]
        self.assertEqual(1, len(unknown), "no Unknown Faces bucket came back")
        self.assertEqual(
            1, unknown[0]["count"],
            "a face with no embedding was counted as waiting to be identified",
        )


class TestAnUnderscoreInAFilenameIsNotAWildcard(MatchingTestBase):
    """`photo_path LIKE ?` does not mean "this photo", and the difference bites.

    Matching looked a photo up with `=` and then, whenever that found nothing -- which
    is the ordinary case, because finding nothing is what "no conflict here" looks like
    -- asked the same question again with LIKE. Two consequences, and the second is the
    one that matters:

    1. LIKE cannot use the index on photo_path, so every assignment scanned all 225,000
       face rows. Measured at 0.38s per face: assigning a selection of fifty spent
       nineteen seconds deciding there was no conflict.
    2. In LIKE, `_` matches any single character. `IMG_1234.jpg` matches `IMG-1234.jpg`
       and `IMGX1234.jpg`. So assigning a name in one photo could be refused because a
       *different* photo, whose name differs only where the underscore sits, already had
       that person on it.

    Every camera names files with an underscore, so this is not a corner case.
    """

    def test_assigning_a_name_is_not_refused_because_of_a_lookalike_filename(self):
        mine = self.add_photo("IMG_1234.jpg")
        other = self.add_photo("IMG-1234.jpg")

        # Somebody is already named in the *other* photo, the one whose filename
        # differs from ours only where the underscore is.
        self.add_face(other, seed=1, name="Rowan Thackeray")
        candidate = self.add_face(mine, seed=2)

        status, body = self.post(
            "/api/face/match",
            {"face_id": candidate, "person_name": "Rowan Thackeray"},
        )
        self.assertEqual(
            200, status,
            "assignment was refused over a different photo's filename: %s" % (body,),
        )
        self.assertEqual("Rowan Thackeray", self.name_of(candidate))

    def test_bulk_assigning_is_not_refused_because_of_a_lookalike_filename(self):
        mine = self.add_photo("DSC_0007.jpg")
        other = self.add_photo("DSCX0007.jpg")

        self.add_face(other, seed=3, name="Marisol Vane")
        candidate = self.add_face(mine, seed=4)

        status, body = self.post(
            "/api/faces/match-bulk",
            {"face_ids": [candidate], "person_name": "Marisol Vane"},
        )
        self.assertEqual(
            200, status,
            "bulk assignment was refused over a different photo's filename: %s" % (body,),
        )
        self.assertEqual("Marisol Vane", self.name_of(candidate))

    def test_a_real_conflict_in_the_same_photo_is_still_refused(self):
        """Removing the wildcard must not remove the check it was doing badly."""
        photo = self.add_photo("IMG_9000.jpg")
        self.add_face(photo, seed=5, name="Teodor Blaskovic")
        candidate = self.add_face(photo, seed=6)

        status, _body = self.post(
            "/api/face/match",
            {"face_id": candidate, "person_name": "Teodor Blaskovic"},
        )
        self.assertEqual(
            400, status,
            "two faces in one photo were both assigned to one person",
        )
        self.assertIsNone(self.name_of(candidate))


if __name__ == "__main__":
    unittest.main()


class TestTheSharedNamedFaceMatrix(MatchingTestBase):
    """Every named face, built once per state of the faces table instead of per click.

    Selecting a card asks `/api/face-matches` who it resembles. That read all 35,758
    named embeddings back out of SQLite and built a 73 MB matrix to answer it -- half
    a second, every click, for a matrix identical to the one the last click built.

    Sharing it costs two properties that the per-request version got for free, because
    it excluded the face being asked about from its own query. Both are pinned here.
    """

    def matches_for(self, face_id):
        with urllib.request.urlopen(
            "http://127.0.0.1:%d/api/face-matches?id=%d" % (self.TEST_PORT, face_id),
            timeout=30,
        ) as r:
            return json.loads(r.read().decode("utf-8"))

    def test_a_face_is_not_a_suggestion_for_itself(self):
        """Otherwise every named face resembles itself at 100%, which says nothing."""
        photo = self.add_photo("IMG_5150.jpg")
        named = self.add_face(photo, seed=21, name="Ines Okonkwo")

        self.assertEqual(
            [], self.matches_for(named),
            "the only named face in the library was offered as its own match",
        )

    def test_another_face_of_the_same_person_is_still_offered(self):
        """Skipping the face itself must not skip the person."""
        first = self.add_photo("IMG_5151.jpg")
        second = self.add_photo("IMG_5152.jpg")
        vector = unit_vector(22)

        conn = sqlite3.connect(self.TEST_DB)
        for photo in (first, second):
            conn.execute(
                "INSERT INTO faces (photo_path, box, embedding, name, prob)"
                " VALUES (?, '[0,0,10,10]', ?, 'Ines Okonkwo', 0.99)",
                (photo, vector.tobytes()),
            )
        conn.commit()
        face_id = conn.execute(
            "SELECT id FROM faces WHERE photo_path = ?", (first,)).fetchone()[0]
        conn.close()

        names = [m["name"] for m in self.matches_for(face_id)]
        self.assertEqual(
            ["Ines Okonkwo"], names,
            "the person's other face was lost along with the face itself",
        )

    def test_naming_somebody_shows_up_in_the_next_answer(self):
        """A shared matrix is only safe if naming a face rebuilds it."""
        photo = self.add_photo("IMG_5153.jpg")
        asking = self.add_face(photo, seed=23)

        self.assertEqual(
            [], self.matches_for(asking), "nobody is named yet, so nothing can match")

        elsewhere = self.add_photo("IMG_5154.jpg")
        newly_named = self.add_face(elsewhere, seed=24)
        status, _body = self.post(
            "/api/face/match",
            {"face_id": newly_named, "person_name": "Halvard Nilsen"},
        )
        self.assertEqual(200, status)

        names = [m["name"] for m in self.matches_for(asking)]
        self.assertIn(
            "Halvard Nilsen", names,
            "the shared matrix was served stale after a face was named",
        )


class TestRemovingFacesKeepsTheGridWarm(MatchingTestBase):
    """Naming ten faces removes ten cards; it does not change the other hundred thousand.

    The per-person grid is cached against a fingerprint of the faces table, and that
    fingerprint moves the moment anything is named or excluded. So every assignment and
    every ignored cluster threw away a payload that costs the better part of a minute
    to rebuild on a real library, and the next click paid for it again -- which is why
    ignoring clusters felt like the slowest thing on the screen.

    A removal is now applied to the cached payload instead. These tests pin that the
    result is the same one a rebuild would have produced.
    """

    def matches(self, name):
        url = "http://127.0.0.1:%d/api/unmatched-faces/person-matches?name=%s" % (
            self.TEST_PORT, urllib.parse.quote(name))
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))

    def a_grid_of(self, count, tag=None):
        """`count` nameless faces that all resemble each other, so they cluster."""
        people = [tag] if tag else []
        base = unit_vector(31)
        conn = sqlite3.connect(self.TEST_DB)
        ids = []
        for i in range(count):
            photo = os.path.join(self.tmpdir, "IMG_%04d.jpg" % i).replace("\\", "/")
            conn.execute(
                "INSERT OR REPLACE INTO photos (path, mtime, size, tags, people,"
                " captions, raw_metadata) VALUES (?, 1.0, 1, '[]', ?, '[]', '{}')",
                (photo, json.dumps(people)),
            )
            jitter = unit_vector(900 + i) * 0.02
            vec = base + jitter
            vec = (vec / np.linalg.norm(vec)).astype(np.float32)
            cur = conn.execute(
                "INSERT INTO faces (photo_path, box, embedding, name, prob)"
                " VALUES (?, '[0,0,10,10]', ?, NULL, 0.99)",
                (photo, vec.tobytes()),
            )
            ids.append(cur.lastrowid)
        conn.commit()
        conn.close()
        return ids

    def test_excluding_leaves_the_rest_of_the_grid_in_place(self):
        ids = self.a_grid_of(6)
        before = self.matches("Unknown Faces")
        self.assertEqual(6, len(before["faces"]))

        status, _body = self.post(
            "/api/faces/exclude", {"face_ids": ids[:2], "reason": "not a person"})
        self.assertEqual(200, status)

        after = self.matches("Unknown Faces")
        self.assertEqual(
            4, len(after["faces"]), "the grid did not lose exactly the excluded faces")
        self.assertEqual(4, after["total_count"])
        self.assertEqual(
            set(ids[2:]), {f["id"] for f in after["faces"]},
            "the wrong faces were left behind",
        )

    def test_the_cached_grid_is_kept_rather_than_thrown_away(self):
        """The point of the exercise: the payload survives the write."""
        import tuner_server

        ids = self.a_grid_of(6)
        self.matches("Unknown Faces")

        cache = tuner_server.TunerHTTPRequestHandler.identify_cache
        key = "matches:Unknown Faces"
        self.assertIn(key, cache.keys(), "the grid was never cached to begin with")
        stamp_before = cache.get(key)["fingerprint"]

        status, _body = self.post(
            "/api/faces/exclude", {"face_ids": ids[:1], "reason": "not a person"})
        self.assertEqual(200, status)

        entry = cache.get(key)
        self.assertIsNotNone(
            entry, "excluding a face threw the whole cached grid away")
        self.assertNotEqual(
            stamp_before, entry["fingerprint"],
            "the entry was left stamped with the old table state, so it would be"
            " treated as stale and rebuilt anyway",
        )
        self.assertEqual(
            5, len(entry["value"]["faces"]),
            "the excluded face is still sitting in the cached payload",
        )

    def test_assigning_a_name_also_leaves_the_grid_in_place(self):
        ids = self.a_grid_of(6)
        self.assertEqual(6, len(self.matches("Unknown Faces")["faces"]))

        status, _body = self.post(
            "/api/faces/match-bulk",
            {"face_ids": [ids[0]], "person_name": "Bryn Aldersgate"},
        )
        self.assertEqual(200, status)

        after = self.matches("Unknown Faces")
        self.assertEqual(5, len(after["faces"]))
        self.assertNotIn(ids[0], {f["id"] for f in after["faces"]})

    def test_restoring_a_face_rebuilds_rather_than_guessing_where_it_goes(self):
        """A face coming back has no cluster to belong to. Only removals are applied."""
        ids = self.a_grid_of(6)
        self.post("/api/faces/exclude", {"face_ids": ids[:2], "reason": "not a person"})
        self.assertEqual(4, len(self.matches("Unknown Faces")["faces"]))

        status, _body = self.post("/api/faces/restore", {"face_ids": ids[:2]})
        self.assertEqual(200, status)

        after = self.matches("Unknown Faces")
        self.assertEqual(
            6, len(after["faces"]),
            "restored faces never came back to the grid; the cache was reused when it"
            " should have been rebuilt",
        )


class TestTheCappedTailRule(unittest.TestCase):
    """When a removal is worth a rebuild, and when it is not.

    Exercised directly on the cache, because the interesting cases need a grid with
    tens of thousands of unclustered faces behind the cap and that is a fixture worth
    not building 15 times.

    The first version of this rule asked only whether more faces were waiting behind
    the cap. On Unknown Faces that is true from the moment the grid is built -- 78,411
    behind a cap of 500 on the real library -- so every removal rebuilt, and the cache
    it was guarding never got used once. Measured: ignoring a cluster of 6,660 faces
    still cost 43s afterwards, against 0.2s when the cached grid is kept.
    """

    def setUp(self):
        self.handler = object.__new__(tuner_server.TunerHTTPRequestHandler)
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE faces (id INTEGER PRIMARY KEY, name TEXT,"
            " excluded INTEGER DEFAULT 0)")
        self.conn.executemany(
            "INSERT INTO faces (id, name, excluded) VALUES (?, NULL, 0)",
            [(i,) for i in range(1, 60)])
        self.conn.commit()
        self.addCleanup(self.conn.close)

        cache = tuner_server.TunerHTTPRequestHandler.identify_cache
        for key in list(cache.keys()):
            cache.pop(key, None)
        self.cache = cache

    def cache_a_grid(self, clustered_ids, shown_unclustered_ids, unclustered_total):
        faces = [{"id": i, "cluster_id": 0} for i in clustered_ids]
        faces += [{"id": i, "cluster_id": -1} for i in shown_unclustered_ids]
        self.cache["matches:Unknown Faces"] = {
            "fingerprint": ("stale",),
            "value": {
                "faces": faces,
                "total_count": len(faces),
                "unclustered_total": unclustered_total,
                "unclustered_shown": len(shown_unclustered_ids),
                "has_more": unclustered_total > len(shown_unclustered_ids),
            },
        }

    def entry(self):
        return self.cache.get("matches:Unknown Faces")

    def test_ignoring_a_cluster_keeps_a_capped_grid(self):
        """The headline case. A cluster removal never touches the unclustered tail."""
        clustered = list(range(1, 11))
        shown = list(range(11, 11 + tuner_server.UNCLUSTERED_LIMIT))
        self.cache_a_grid(clustered, shown, unclustered_total=78411)

        self.handler.identify_cache_forget_faces(self.conn, clustered)

        entry = self.entry()
        self.assertIsNotNone(
            entry, "ignoring a cluster threw away a grid whose tail it never touched")
        self.assertEqual(len(shown), entry["value"]["total_count"])
        self.assertEqual(78411, entry["value"]["unclustered_total"])
        self.assertNotEqual(
            ("stale",), entry["fingerprint"],
            "the entry kept its old stamp, so it would be rebuilt anyway")

    def test_thinning_the_tail_a_little_keeps_the_grid(self):
        shown = list(range(11, 11 + tuner_server.UNCLUSTERED_LIMIT))
        self.cache_a_grid([1, 2], shown, unclustered_total=78411)

        self.handler.identify_cache_forget_faces(self.conn, shown[:5])

        entry = self.entry()
        self.assertIsNotNone(entry, "removing five faces rebuilt the whole grid")
        self.assertEqual(
            tuner_server.UNCLUSTERED_LIMIT - 5, entry["value"]["unclustered_shown"])
        self.assertEqual(78410 - 4, entry["value"]["unclustered_total"])
        self.assertTrue(
            entry["value"]["has_more"], "the tail behind the cap was forgotten")

    def test_wearing_the_tail_down_rebuilds_so_the_next_faces_come_forward(self):
        """Otherwise the grid shrinks towards empty while faces still need a name."""
        shown = list(range(11, 11 + tuner_server.UNCLUSTERED_LIMIT))
        self.cache_a_grid([1, 2], shown, unclustered_total=78411)

        # Down past half the cap.
        self.handler.identify_cache_forget_faces(
            self.conn, shown[:tuner_server.UNCLUSTERED_LIMIT // 2 + 1])

        self.assertIsNone(
            self.entry(),
            "the visible tail wore down past half the cap and the grid was not rebuilt,"
            " so the faces waiting behind it stay unreachable")

    def test_a_grid_with_nothing_behind_the_cap_is_never_rebuilt(self):
        """No tail to bring forward means no reason to pay for a rebuild."""
        shown = [11, 12, 13]
        self.cache_a_grid([1, 2], shown, unclustered_total=3)

        self.handler.identify_cache_forget_faces(self.conn, shown)

        entry = self.entry()
        self.assertIsNotNone(entry, "a grid showing every face it had was rebuilt")
        self.assertEqual(0, entry["value"]["unclustered_total"])
        self.assertFalse(entry["value"]["has_more"])

    def test_another_persons_untouched_grid_is_re_stamped_not_discarded(self):
        """One person's edit should not cost everybody else their grid."""
        self.cache["matches:Rhiannon Vail"] = {
            "fingerprint": ("stale",),
            "value": {"faces": [{"id": 900, "cluster_id": 0}], "total_count": 1,
                      "unclustered_total": 0, "unclustered_shown": 0, "has_more": False},
        }
        self.cache_a_grid([1, 2], [], unclustered_total=0)

        self.handler.identify_cache_forget_faces(self.conn, [1, 2])

        other = self.cache.get("matches:Rhiannon Vail")
        self.assertIsNotNone(other, "an unrelated person's grid was discarded")
        self.assertEqual(1, other["value"]["total_count"])
        self.assertNotEqual(
            ("stale",), other["fingerprint"],
            "it was kept but left stale-stamped, which rebuilds it on the next click")
