"""The MCP server's write tools: the maintenance scripts' operations, through a client
session, each over a library in a home of its own.

Phase 7 (docs/ARCHITECTURE.md) gives Claude the maintenance scripts' operations as tools
that call the same services the scripts call (tagpup.services.maintenance's scaffold): a
dry run unless told to apply, one backup before it applies, and the Result back. The
scripts' own history is why each test checks what it does: one printed what it planned
as what it had removed, six wrote without a backup (docs/findings.md, #54), and a
backfill reported 60 done and wrote nothing.

Rows are seeded in plain SQL, as the indexer stores them. ExifTool is never run: the
refresh test's rows are either fixable from the row alone or read with an ExifTool that
is not there.
"""
import asyncio
import json
import logging
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from face_rows import add_face, photo_id  # noqa: E402

from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from tagpup.mcp import server  # noqa: E402
from tagpup.store import db, schema  # noqa: E402

LIBRARY = "harbour"

#: What the seeded libraries hold that names a person, a place or a file.
SECRETS = ("Rowan Thackeray", "Maren Oakhollow", "Pell Quarrington", "Harbourview", "regatta_")


class WriteTools(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.db_path = self.home.library(LIBRARY + ".db")
        schema.ensure(self.db_path)
        self.photos = os.path.join(self.home.root, "Harbourview Regatta")
        os.makedirs(self.photos)
        # Never the machine's ExifTool: one that is not there.
        patcher = mock.patch.object(server.config, "exiftool_path",
                                    return_value=os.path.join(self.home.root, "no-exiftool.exe"))
        patcher.start()
        self.addCleanup(patcher.stop)
        # That ExifTool is not there is logged at every read; the test expects it.
        quiet = mock.patch.object(logging.getLogger("tagpup_cli.metadata"), "disabled", True)
        quiet.start()
        self.addCleanup(quiet.stop)

    # ---- Seeding, in plain SQL ---------------------------------------------------------

    def seed(self, work):
        conn = db.connect(self.db_path)
        try:
            found = work(conn)
            conn.commit()
            return found
        finally:
            conn.close()

    def read(self, query, params=()):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute(query, params).fetchall()
        finally:
            conn.close()

    def file(self, name):
        path = os.path.join(self.photos, name)
        with open(path, "wb") as handle:
            handle.write(b"not really a photo")
        return path

    def backups(self):
        """Every backup in the home: copies named <library>.before-<reason>-<time>.db."""
        return [name for _folder, _dirs, names in os.walk(self.home.root) for name in names
                if ".before-" in name]

    # ---- Calling the tools -------------------------------------------------------------

    def call(self, tool, **arguments):
        async def run():
            async with create_connected_server_and_client_session(server.build()) as client:
                return await client.call_tool(tool, dict(arguments, library=arguments.get("library", LIBRARY)))
        result = asyncio.run(run())
        self.assertFalse(result.isError, result.content[0].text if result.content else result)
        self.answers.append(result.content[0].text)
        return json.loads(result.content[0].text)

    @property
    def answers(self):
        if not hasattr(self, "_answers"):
            self._answers = []
        return self._answers

    def dry_run_then_apply(self, tool, count, **arguments):
        """The tool's dry run, which must change nothing and back nothing up, then its
        apply, which must back up exactly once: (dry run, applied, rows before, rows after)
        where `count` reads what the operation changes."""
        before = count()
        with open(self.db_path, "rb") as handle:
            file_before = handle.read()
        planned = self.call(tool, **arguments)
        with open(self.db_path, "rb") as handle:
            self.assertEqual(handle.read(), file_before, "a dry run changed the library")
        self.assertEqual((planned["dry_run"], planned["changed"], planned["backup"]), (True, 0, None))
        self.assertEqual(self.backups(), [], "a dry run took a backup")
        self.assertEqual(count(), before)

        applied = self.call(tool, apply=True, **arguments)
        after = count()
        self.assertFalse(applied["dry_run"])
        self.assertEqual(len(self.backups()), 1, "applying backs up once, and the tool takes none of its own")
        self.assertEqual(applied["backup"], self.backups()[0], "the backup is named by its file alone")
        return planned, applied, before, after

    # ---- The tools -----------------------------------------------------------------------

    def seed_faces(self):
        def faces(conn):
            photo = os.path.join(self.photos, "regatta_001.jpg")
            return {
                "kept": add_face(conn, photo, (1, 2, 30, 40), name="Rowan Thackeray", name_source="manual"),
                "copy": add_face(conn, photo, (1, 2, 30, 40)),
                "alone": add_face(conn, photo, (50, 2, 80, 40)),
                "one": add_face(conn, photo, (90, 2, 120, 40), name="Maren Oakhollow"),
                "other": add_face(conn, photo, (90, 2, 120, 40), name="Pell Quarrington"),
            }
        return self.seed(faces)

    def seed_tree(self):
        def tree(conn):
            for row in [(1, "People", None, "People", 1), (2, "People/Rowan Thackeray", 1, "Rowan Thackeray", 1),
                        (3, "Rowan Thackeray", None, "Rowan Thackeray", 0), (4, "Activity", None, "Activity", 0)]:
                conn.execute("INSERT INTO tag_taxonomy (id, tag, parent_id, name, has_face) VALUES (?, ?, ?, ?, ?)",
                             row)
            conn.execute("INSERT INTO photos (path, tags, captions, raw_metadata) VALUES (?, ?, '[]', '{}')",
                         (os.path.join(self.photos, "regatta_004.jpg"), json.dumps(["Rowan Thackeray"])))
        self.seed(tree)

    def seed_captions(self, caption):
        """A row listing its caption twice, its file's stamp its own; and a row whose file
        has moved on. Returns their ids."""
        stamped = self.file("regatta_002.jpg")
        moved_on = self.file("regatta_003.jpg")
        stat = os.stat(stamped)

        def rows(conn):
            conn.execute("INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)"
                         " VALUES (?, ?, ?, '[]', ?, '{}')",
                         (stamped, stat.st_mtime, stat.st_size, json.dumps([caption, caption])))
            conn.execute("INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)"
                         " VALUES (?, 0, 0, '[]', '[]', '{}')", (moved_on,))
            return photo_id(conn, stamped), photo_id(conn, moved_on)
        return self.seed(rows)

    def test_dedupe_faces(self):
        ids = self.seed_faces()
        count = lambda: {i for (i,) in self.read("SELECT id FROM faces")}  # noqa: E731
        planned, applied, before, after = self.dry_run_then_apply("dedupe_faces", count)
        self.assertEqual((planned["attempted"], planned["counts"]["redundant"], planned["counts"]["disputed"]),
                         (1, 1, 1))
        self.assertEqual(planned["ids"]["redundant"], [ids["copy"]])
        self.assertEqual(applied["changed"], len(before - after), "changed is the rows deleted, re-read")
        self.assertEqual(before - after, {ids["copy"]})
        self.assertEqual(applied["remaining"], {"redundant": 0, "disputed": 1})

    def test_merge_duplicate_person_tags(self):
        self.seed_tree()
        count = lambda: {tag for (tag,) in self.read("SELECT tag FROM tag_taxonomy")}  # noqa: E731
        tags = lambda: self.read("SELECT tags FROM photos")  # noqa: E731
        photo_tags = tags()
        planned, applied, before, after = self.dry_run_then_apply("merge_duplicate_person_tags", count)
        self.assertEqual((planned["attempted"], planned["counts"]), (1, {"duplicates": 1, "affected_photos": 1}))
        self.assertEqual(planned["ids"]["duplicates"], [3])
        self.assertEqual(applied["changed"], len(before - after))
        self.assertEqual(before - after, {"Rowan Thackeray"})
        self.assertEqual(tags(), photo_tags, "photos are counted, not rewritten")
        self.assertEqual(applied["remaining"], {"duplicates": 0})

    def test_a_refused_merge_is_an_answer(self):
        """No tag tree: refused, nothing written, and the refusal names the library, not
        where it is."""
        bare = self.home.library("bare.db")
        conn = db.connect(bare)
        conn.execute("CREATE TABLE photos (id INTEGER PRIMARY KEY, path TEXT, tags TEXT)")
        conn.commit()
        conn.close()
        for apply in (False, True):
            answer = self.call("merge_duplicate_person_tags", library="bare", apply=apply)
            self.assertIn("no tag_taxonomy", answer["refused"])
            self.assertIn("bare", answer["refused"])
            self.assertNotIn(self.home.root, answer["refused"])
            self.assertEqual((answer["ok"], answer["changed"], answer["backup"]), (False, 0, None))
        self.assertEqual(self.backups(), [])

    def test_refresh_rows(self):
        """A row that lists its caption twice is fixed from the row; one whose file has
        moved on is read with ExifTool, which is not there, and is left as it was."""
        caption = "Sunset over Harbourview"
        twice, unread = self.seed_captions(caption)
        count = lambda: self.read("SELECT id, captions, mtime FROM photos ORDER BY id")  # noqa: E731
        planned, applied, before, after = self.dry_run_then_apply("refresh_rows", count)
        self.assertEqual((planned["attempted"], planned["counts"]["captions_only"], planned["counts"]["stale"],
                          planned["counts"]["unreadable"]), (1, 1, 1, 1))
        self.assertEqual((planned["ids"]["captions_only"], planned["ids"]["unreadable"]), ([twice], [unread]))
        changed_rows = [a for a, b in zip(before, after) if a != b]
        self.assertEqual(applied["changed"], len(changed_rows))
        self.assertEqual(applied["changed_by_kind"], {"from_files": 0, "captions": 1})
        self.assertEqual(dict((i, json.loads(c)) for i, c, _m in after)[twice], [caption])
        self.assertEqual(after[1], before[1], "a file ExifTool could not read leaves its row alone")

    def test_refresh_is_limited_to_a_folder(self):
        elsewhere = os.path.join(self.home.root, "Elsewhere")
        os.makedirs(elsewhere)
        path = os.path.join(elsewhere, "x.jpg")
        with open(path, "wb") as handle:
            handle.write(b"x")
        stat = os.stat(path)
        self.seed(lambda conn: conn.execute(
            "INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, ?, ?, '[]', ?, '{}')",
            (path, stat.st_mtime, stat.st_size, json.dumps(["a", "a"]))))
        self.assertEqual(self.call("refresh_rows", folder=self.photos)["attempted"], 0)
        self.assertEqual(self.call("refresh_rows", folder=elsewhere)["attempted"], 1)

    # ---- Privacy -------------------------------------------------------------------------

    def test_no_write_answer_names_anyone_or_any_file_without_reveal(self):
        """Every write tool's dry run and apply, over rows naming people and files."""
        self.seed_faces()
        self.seed_tree()
        self.seed_captions("Sunset over Harbourview")
        tools = ("dedupe_faces", "merge_duplicate_person_tags", "refresh_rows")
        for tool in tools:
            self.call(tool)
        revealed = " ".join(json.dumps(self.call(tool, reveal=True)) for tool in tools)
        for tool in tools:
            self.call(tool, apply=True)
        hidden = self.answers[:len(tools)] + self.answers[2 * len(tools):]
        for text in hidden:
            for secret in SECRETS + (self.home.root, self.home.root.replace("\\", "\\\\")):
                self.assertNotIn(secret, text)
        # The test could pass by never seeing a name; with reveal, it would see them.
        self.assertIn("Maren Oakhollow", revealed)
        self.assertIn("Rowan Thackeray", revealed)
        self.assertIn("regatta_", revealed)


if __name__ == "__main__":
    unittest.main()
