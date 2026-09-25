"""The MCP server (tagpup.mcp): every tool, through a client session, over a library in a
home of its own.

Settling #42 in docs/findings.md took four throwaway scripts; phase 7 makes each such
question one tool call (docs/ARCHITECTURE.md). Each test here calls its tool the way
Claude does -- an MCP client session over the SDK's in-memory transport -- and names the
finding its tool answers where there is one. One test starts the server as a process,
over stdio, as Claude Code's .mcp.json starts it.

The library is seeded in plain SQL, as the indexer stores rows, never through the reads
under test. Its names and folders are fictional; the privacy test holds every tool's
answer to carrying none of them unless asked with reveal.
"""
import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from face_rows import add_face, add_people, photo_id  # noqa: E402
from shipped_sources import ROOT  # noqa: E402

from mcp import types  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from tagpup.core import processes  # noqa: E402
from tagpup.mcp import server  # noqa: E402
from tagpup.store import checks, db, schema  # noqa: E402

LIBRARY = "harbour"

#: Everything in the library that names a person, a place or a file.
SECRETS = ("Rowan Thackeray", "Maren Oakhollow", "Pell Quarrington", "Tamsin Ferrow", "Quayside Rowers",
           "Harbourview", "Lindqvist", "regatta_", "camp_", "Sailing", "Dinghy")

READ_TOOLS = {"libraries", "summary", "folders", "photos", "photo_against_file", "faces_in_photo",
              "check", "checks", "missing_files", "query_plan"}

#: The maintenance operations (tests/test_mcp_write_tools.py applies them).
WRITE_TOOLS = {"refresh_rows", "merge_duplicate_person_tags", "dedupe_faces"}

TOOLS = READ_TOOLS | WRITE_TOOLS


def _tree(conn):
    """The tag tree: People holds faces, one person filed under it, an activity branch,
    and a node whose parent is gone (an orphan, for the checks)."""
    rows = [(1, "People", None, "People", 1), (2, "People/Rowan Thackeray", 1, "Rowan Thackeray", 1),
            (3, "Activity", None, "Activity", 0), (4, "Activity/Sailing", 3, "Sailing", 0),
            (5, "Activity/Sailing/Dinghy", 4, "Dinghy", 0), (6, "Clubs/Quayside Rowers", 99, "Quayside Rowers", 0)]
    for row in rows:
        conn.execute("INSERT INTO tag_taxonomy (id, tag, parent_id, name, has_face) VALUES (?, ?, ?, ?, ?)", row)


def _photo(conn, path, tags, mtime=None, size=None):
    conn.execute("INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, ?, ?, ?, '[]', '{}')",
                 (path, mtime, size, json.dumps(tags)))
    return photo_id(conn, path)


class McpServer(unittest.TestCase):
    """One library for every test: nothing a tool does writes to it."""

    @classmethod
    def setUpClass(cls):
        cls.home = own_home.for_class(cls)
        cls.db_path = cls.home.library(LIBRARY + ".db")
        schema.ensure(cls.db_path)
        cls.here = os.path.join(cls.home.root, "Harbourview Regatta")
        cls.gone = os.path.join(cls.home.root, "Lindqvist Summer Camp")
        os.makedirs(cls.here)
        cls.sailing = os.path.join(cls.here, "regatta_001.jpg")
        cls.dinghy = os.path.join(cls.here, "regatta_002.jpg")
        cls.unlisted = os.path.join(cls.here, "regatta_003.jpg")
        cls.exiftool = own_home.installed_exiftool()
        cls._make_photos()
        conn = db.connect(cls.db_path)
        try:
            _tree(conn)
            stat = os.stat(cls.sailing)
            cls.ids = {
                "sailing": _photo(conn, cls.sailing, ["People/Rowan Thackeray", "Activity/Sailing"],
                                  stat.st_mtime, stat.st_size),
                "dinghy": _photo(conn, cls.dinghy, ["Activity/Sailing/Dinghy"]),
                # Tagged with a person, and nobody listed: a writer that did not rebuild.
                "unlisted": _photo(conn, cls.unlisted, ["People/Tamsin Ferrow"]),
                "camp1": _photo(conn, os.path.join(cls.gone, "camp_001.jpg"), []),
                "camp2": _photo(conn, os.path.join(cls.gone, "camp_002.jpg"), []),
            }
            add_people(conn, cls.sailing, ["Rowan Thackeray"])
            cls.faces = {
                "rowan": add_face(conn, cls.sailing, (10, 20, 110, 140), name="Rowan Thackeray",
                                  name_source="manual", prob=0.99, embedding=b"\x00" * 16),
                "stranger": add_face(conn, cls.sailing, (200, 20, 260, 90), excluded=1,
                                     excluded_reason="background", prob=0.91, embedding=b"\x00" * 16),
                # Named and excluded: what the doctor's named_and_excluded finds (#3).
                "both": add_face(conn, cls.dinghy, name="Pell Quarrington", excluded=1, excluded_reason="background"),
                # #42: names on the rows of a folder whose files are gone.
                "camp1": add_face(conn, os.path.join(cls.gone, "camp_001.jpg"), name="Maren Oakhollow",
                                  name_source="manual"),
                "camp2": add_face(conn, os.path.join(cls.gone, "camp_002.jpg"), name="Maren Oakhollow",
                                  name_source="manual"),
                "camp2b": add_face(conn, os.path.join(cls.gone, "camp_002.jpg")),
            }
            add_people(conn, os.path.join(cls.gone, "camp_001.jpg"), ["Maren Oakhollow"], source="face")
            add_people(conn, os.path.join(cls.gone, "camp_002.jpg"), ["Maren Oakhollow"], source="face")
            conn.commit()
        finally:
            conn.close()
        with open(cls.db_path, "rb") as handle:
            cls.before = handle.read()

    @classmethod
    def _make_photos(cls):
        from PIL import Image
        for path in (cls.sailing, cls.dinghy, cls.unlisted):
            Image.new("RGB", (64, 48), (90, 120, 200)).save(path, "JPEG")
        if cls.exiftool:
            from tagpup.files.exiftool_session import ExifToolSession
            from tagpup.files.keywords import write_keywords
            with ExifToolSession(executable=cls.exiftool) as et:
                # The file carries one tag its row does not.
                write_keywords(et, cls.sailing, ["People/Rowan Thackeray", "Activity/Sailing", "Activity/Racing"])

    def tearDown(self):
        with open(self.db_path, "rb") as handle:
            self.assertEqual(handle.read(), self.before, "a read-only tool changed the library")

    # ---- Calling the tools -------------------------------------------------------------

    def session(self, work):
        """Run `work(client)` in a client session connected to the server in process."""
        async def run():
            async with create_connected_server_and_client_session(server.build()) as client:
                return await work(client)
        return asyncio.run(run())

    def result(self, tool, **arguments):
        return self.session(lambda client: client.call_tool(tool, arguments))

    def call(self, tool, **arguments):
        """The tool's answer, parsed from the JSON it returned; fails on an error."""
        result = self.result(tool, **arguments)
        self.assertFalse(result.isError, result.content[0].text if result.content else result)
        answer = json.loads(result.content[0].text)
        self.assertEqual(answer, result.structuredContent)
        return answer

    def refused(self, tool, **arguments):
        """The error a tool answered with."""
        result = self.result(tool, **arguments)
        self.assertTrue(result.isError, "expected a refusal from %s" % tool)
        return result.content[0].text

    # ---- The tools -----------------------------------------------------------------------

    def test_every_tool_is_listed_and_says_whether_it_writes(self):
        listed = self.session(lambda client: client.list_tools()).tools
        self.assertEqual({t.name for t in listed}, TOOLS)
        for t in listed:
            self.assertEqual(t.annotations.readOnlyHint, t.name in READ_TOOLS, t.name)
            if t.name in WRITE_TOOLS:
                self.assertIn("dry run", t.description, t.name)
                self.assertIn("backs the library up", t.description, t.name)
                self.assertIs(t.inputSchema["properties"]["apply"]["default"], False, t.name)
            if "reveal" in t.inputSchema["properties"]:
                self.assertIn("reveal", t.description, t.name)
            if t.name != "libraries":
                self.assertIn("library", t.inputSchema["required"], t.name)

    def test_libraries_are_the_data_folder_s(self):
        """No default library (#100): the tools name one, and this lists them."""
        self.assertEqual(self.call("libraries"), {"libraries": [LIBRARY]})

    def test_a_library_that_is_not_there_is_refused_and_not_made(self):
        """Selecting a library that does not exist created it (#16); a tool never does."""
        for tool in TOOLS - {"libraries"}:
            arguments = {"library": "harbor", "photo_id": 1, "name": "orphan_nodes", "sql": "SELECT 1",
                         "folder": self.here}
            schema_of = {t.name: t for t in self.session(lambda client: client.list_tools()).tools}[tool]
            arguments = {k: v for k, v in arguments.items() if k in schema_of.inputSchema["properties"]}
            self.assertIn("no library called", self.refused(tool, **arguments))
        self.assertEqual(sorted(os.listdir(self.home.data)), [LIBRARY + ".db"])

    def test_summary_is_what_the_doctor_prints(self):
        answer = self.call("summary", library=LIBRARY)
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            printed = checks.summary(conn)
        finally:
            conn.close()
        self.assertEqual({k: answer[k] for k in printed}, printed)
        self.assertEqual((answer["photos"], answer["faces"], answer["named"], answer["manual"], answer["excluded"]),
                         (5, 6, 4, 3, 2))
        self.assertEqual(answer["schema_version"], schema.MIGRATIONS[-1].version)
        self.assertEqual(answer["tree_nodes"], 6)
        self.assertEqual(answer["without_a_vector"], 5)

    def test_folders_count_their_photos_and_say_which_are_gone(self):
        answer = self.call("folders", library=LIBRARY)
        self.assertEqual((answer["count"], answer["photos"]), (2, 5))
        self.assertEqual([(f["photos"], f["on_disk"]) for f in answer["folders"]], [(3, True), (2, False)])

    def test_photos_by_folder_tag_or_person(self):
        ids = self.ids
        by = lambda **criteria: self.call("photos", library=LIBRARY, **criteria)["ids"]  # noqa: E731
        self.assertEqual(by(folder=self.here), sorted([ids["sailing"], ids["dinghy"], ids["unlisted"]]))
        self.assertEqual(by(folder=self.here.upper()), by(folder=self.here), "a folder is found however spelled")
        self.assertEqual(by(tag="Activity/Sailing"), sorted([ids["sailing"], ids["dinghy"]]))
        self.assertEqual(by(tag="Activity/Sail"), [], "a tag is not a prefix of another")
        self.assertEqual(by(person="Rowan Thackeray"), [ids["sailing"]])
        self.assertEqual(by(person="People/Rowan Thackeray"), [ids["sailing"]])
        self.assertEqual(by(person="Maren Oakhollow", folder=self.here), [])
        self.assertEqual(by(tag="Activity", folder=self.gone), [])
        limited = self.call("photos", library=LIBRARY, folder=self.here, limit=1)
        self.assertEqual((limited["count"], len(limited["ids"]), limited["more"]), (3, 1, 2))
        self.assertIn("Name a folder", self.refused("photos", library=LIBRARY))

    def test_a_row_whose_file_is_gone_is_stale(self):
        answer = self.call("photo_against_file", library=LIBRARY, photo_id=self.ids["camp1"])
        self.assertEqual((answer["file_exists"], answer["stale"]), (False, True))
        self.assertIn("no photo", self.refused("photo_against_file", library=LIBRARY, photo_id=999))

    def test_a_row_against_what_its_file_holds_now(self):
        """#30, #44 and #84 each turned on a row that no longer described its file, and each
        took a script to see it."""
        if not self.exiftool:
            self.skipTest("ExifTool is not installed")
        answer = self.call("photo_against_file", library=LIBRARY, photo_id=self.ids["sailing"])
        self.assertTrue(answer["file_exists"])
        self.assertTrue(answer["stale"])
        self.assertEqual({k: answer["tags"][k] for k in ("same", "in_row", "in_file", "only_in_row", "only_in_file")},
                         {"same": False, "in_row": 2, "in_file": 3, "only_in_row": 0, "only_in_file": 1})
        self.assertTrue(answer["keyword_people"]["same"])
        self.assertEqual((answer["mtime"]["same"], answer["size"]["same"]), (True, True),
                         "the row was stamped after the write")
        revealed = self.call("photo_against_file", library=LIBRARY, photo_id=self.ids["sailing"], reveal=True)
        self.assertEqual(revealed["tags"]["only_in_file_values"], ["Activity/Racing"])

    def test_the_faces_in_a_photo(self):
        answer = self.call("faces_in_photo", library=LIBRARY, photo_id=self.ids["sailing"])
        self.assertEqual((answer["count"], answer["named"], answer["excluded"], answer["people_listed"]), (2, 1, 1, 1))
        rowan, stranger = answer["faces"]
        self.assertEqual((rowan["id"], rowan["box"], rowan["named"], rowan["name_source"], rowan["excluded"]),
                         (self.faces["rowan"], [10, 20, 110, 140], True, "manual", False))
        self.assertEqual((stranger["excluded"], stranger["excluded_reason"]), (True, "background"))
        self.assertNotIn("embedding", rowan)
        revealed = self.call("faces_in_photo", library=LIBRARY, photo_id=self.ids["sailing"], reveal=True)
        self.assertEqual(revealed["faces"][0]["name"], "Rowan Thackeray")
        self.assertIn("no photo", self.refused("faces_in_photo", library=LIBRARY, photo_id=999))

    def test_each_check_by_name(self):
        """Each rule of tools/doctor.py is one call: named and excluded (#3), people out of
        date (#42, #63), and the tree's orphans (#37)."""
        both = self.call("check", library=LIBRARY, name="named_and_excluded")
        self.assertEqual((both["count"], both["examples"]), (1, [self.faces["both"]]))
        stale = self.call("check", library=LIBRARY, name="people_out_of_date")
        self.assertEqual((stale["count"], stale["example_ids"]), (1, [self.ids["unlisted"]]))
        self.assertNotIn("examples", stale, "its examples are paths")
        orphans = self.call("check", library=LIBRARY, name="orphan_nodes")
        self.assertEqual(orphans["count"], 1)
        self.assertNotIn("examples", orphans, "its examples are tags")
        self.assertEqual(self.call("check", library=LIBRARY, name="orphan_nodes", reveal=True)["examples"],
                         ["Clubs/Quayside Rowers"])
        for rule in checks.RULES:
            self.assertEqual(self.call("check", library=LIBRARY, name=rule.__name__)["check"], rule.__name__)
        self.assertIn("no check called", self.refused("check", library=LIBRARY, name="everything"))

    def test_every_check_together(self):
        answer = self.call("checks", library=LIBRARY)
        self.assertEqual([c["check"] for c in answer["checks"]], [rule.__name__ for rule in checks.RULES])
        self.assertEqual(answer["broken"], 3)
        self.assertEqual(answer["without_a_vector"], 5)

    def test_which_names_sit_on_rows_of_a_folder_whose_files_are_gone(self):
        """#42: 35 names on 28 rows of a deleted folder took four throwaway scripts to find."""
        answer = self.call("missing_files", library=LIBRARY)
        self.assertEqual((answer["rows"], answer["folders"], answer["folders_gone"], answer["named"], answer["names"]),
                         (2, 1, 1, 2, 1))
        (folder,) = answer["by_folder"]
        self.assertEqual(folder["ids"], sorted([self.ids["camp1"], self.ids["camp2"]]))
        self.assertEqual((folder["faces"], folder["named"], folder["named_by_hand"], folder["photos_with_a_name"]),
                         (3, 2, 2, 2))
        (revealed,) = self.call("missing_files", library=LIBRARY, reveal=True)["by_folder"]
        self.assertEqual((revealed["path"], revealed["name_list"]), (self.gone, ["Maren Oakhollow"]))

    def test_the_plan_of_a_query(self):
        answer = self.call("query_plan", library=LIBRARY, sql="SELECT path FROM photos WHERE id = ?")
        self.assertEqual(answer["scans"], [])
        self.assertTrue(any("photos" in step["detail"] for step in answer["plan"]))
        scan = self.call("query_plan", library=LIBRARY, sql="SELECT COUNT(*) FROM photos WHERE LOWER(path) = ?")
        self.assertTrue(scan["scans"], "a function on the column cannot use its index")
        self.assertEqual(self.call("query_plan", library=LIBRARY,
                                   sql="EXPLAIN QUERY PLAN SELECT id FROM faces WHERE name = ?")["scans"], [])

    def test_the_plan_of_anything_but_a_read_is_refused(self):
        for sql in ("UPDATE photos SET tags = '[]'", "DELETE FROM faces", "INSERT INTO photos (path) VALUES ('x')",
                    "DROP TABLE faces", "PRAGMA journal_mode = DELETE",
                    "WITH doomed AS (SELECT id FROM faces) DELETE FROM faces WHERE id IN doomed",
                    "SELECT 1; DELETE FROM faces", "SELECT 1; SELECT 2",
                    "-- a comment\nDELETE FROM faces", "/* SELECT */ DELETE FROM faces", "",
                    # A read, but not a SELECT: only the first-word check refuses it.
                    "VALUES (1)"):
            self.assertTrue(self.refused("query_plan", library=LIBRARY, sql=sql), sql)

    # ---- Privacy -------------------------------------------------------------------------

    def every_answer(self, reveal):
        """The text of every tool's answer, each asked what the tests above ask it."""
        ids, texts = self.ids, []
        calls = [("libraries", {}), ("summary", {}), ("folders", {}),
                 ("photos", {"folder": self.here}), ("photos", {"tag": "Activity/Sailing"}),
                 ("photos", {"person": "Rowan Thackeray"}), ("photos", {"person": "Maren Oakhollow"}),
                 ("photo_against_file", {"photo_id": ids["sailing"]}),
                 ("photo_against_file", {"photo_id": ids["camp1"]}),
                 ("faces_in_photo", {"photo_id": ids["sailing"]}), ("faces_in_photo", {"photo_id": ids["camp2"]}),
                 ("checks", {}), ("missing_files", {}),
                 ("query_plan", {"sql": "SELECT path FROM photos WHERE path = 'Harbourview'"})]
        # The write tools as dry runs: tearDown holds the library to being unchanged.
        calls += [(tool, {}) for tool in sorted(WRITE_TOOLS)]
        calls += [("check", {"name": rule.__name__}) for rule in checks.RULES]
        self.assertEqual({tool for tool, _ in calls}, TOOLS)
        for tool, arguments in calls:
            if tool != "libraries":
                arguments = dict(arguments, library=LIBRARY)
                if tool not in ("summary", "query_plan"):
                    arguments["reveal"] = reveal
            result = self.result(tool, **arguments)
            texts.append((tool, " ".join(c.text for c in result.content) + json.dumps(result.structuredContent)))
        return texts

    def test_no_answer_names_anyone_or_any_file_without_reveal(self):
        """The library is photographs of real people, many of them minors."""
        for tool, text in self.every_answer(reveal=False):
            for secret in SECRETS + (self.home.root, self.home.root.replace("\\", "\\\\")):
                self.assertNotIn(secret, text, "%s answered with %r" % (tool, secret))

    def test_reveal_names_them(self):
        """The test above could pass by never seeing a name; with reveal, it would see them."""
        seen = " ".join(text for _tool, text in self.every_answer(reveal=True))
        for secret in ("Rowan Thackeray", "Maren Oakhollow", "Quayside Rowers", "Lindqvist", "regatta_"):
            self.assertIn(secret, seen)


class OverStdio(unittest.TestCase):
    def test_the_server_starts_as_a_process_and_lists_its_tools(self):
        """As .mcp.json starts it: python -m tagpup.mcp, the protocol on stdin and stdout
        and nothing else there, its log in the home's data/logs."""
        home = own_home.for_test(self)
        schema.ensure(home.library(LIBRARY + ".db"))
        child = processes.start([sys.executable, "-m", "tagpup.mcp"], cwd=ROOT,
                                env=dict(os.environ, TAGPUP_HOME=home.root),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: child.poll() is None and processes.kill_tree(child.pid))
        lines = queue.Queue()
        threading.Thread(target=lambda: [lines.put(line) for line in child.stdout], daemon=True).start()

        def send(message):
            child.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            child.stdin.flush()

        def answer():
            line = lines.get(timeout=60)
            return json.loads(line)   # anything but the protocol on stdout fails here

        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": types.LATEST_PROTOCOL_VERSION, "capabilities": {},
                         "clientInfo": {"name": "test", "version": "0"}}})
        self.assertEqual(answer()["result"]["serverInfo"]["name"], "tagpup")
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual({t["name"] for t in answer()["result"]["tools"]}, TOOLS)
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "libraries", "arguments": {}}})
        self.assertEqual(answer()["result"]["structuredContent"], {"libraries": [LIBRARY]})
        child.stdin.close()
        self.assertEqual(child.wait(timeout=60), 0)
        child.stdout.close()
        self.assertTrue(os.path.exists(os.path.join(home.data, "logs", "tagpup_mcp.log")))


if __name__ == "__main__":
    unittest.main()
