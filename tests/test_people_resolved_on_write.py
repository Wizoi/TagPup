"""No server write path can put a bare person name into a photo's keywords.

A person's name reaches this program as a leaf from half a dozen directions: the faces
table, CLIP suggestions, neighbour propagation, a name typed into a box. Each of those
paths resolving it for itself is exactly how "Hazel Brookmire" kept being written
beside "People/Hazel Brookmire" -- one of them always got missed. Folder auto-apply
outlived three separate fixes, because it writes on the server, from a suggestion list
computed before any of the UI-side resolution ran.

So resolution moved to the write boundary, which is the one place every keyword write
funnels through, and this holds it there. The last test is the guard: a new call to
write_keyword_fields that forgets db_path fails here rather than in someone's library.
"""
import ast
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import db as tagpup_db
import tagpup_server


def make_db(path):
    conn = tagpup_db.connect(path)
    conn.execute("""CREATE TABLE tag_taxonomy (
        id INTEGER PRIMARY KEY, tag TEXT, name TEXT, parent_id INTEGER, has_face INTEGER
    )""")
    conn.executemany(
        "INSERT INTO tag_taxonomy (tag, name, parent_id, has_face) VALUES (?, ?, ?, ?)",
        [
            ("People", "People", None, 1),
            ("People/Hazel Brookmire", "Hazel Brookmire", 1, 1),
            ("Family", "Family", None, 1),
            ("Family/Immediate", "Immediate", 3, 1),
            ("Family/Immediate/Linnea Ingersoll", "Linnea Ingersoll", 4, 1),
            ("Activity", "Activity", None, 0),
            ("Activity/Cross Country", "Cross Country", 6, 0),
        ],
    )
    conn.commit()
    conn.close()


class PeopleResolutionCase(unittest.TestCase):
    def setUp(self):
        import tempfile
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.db_path)
        make_db(self.db_path)
        tagpup_server.invalidate_people_cache()

        def cleanup():
            tagpup_server.invalidate_people_cache()
            for suffix in ("", "-wal", "-shm"):
                target = self.db_path + suffix
                if os.path.exists(target):
                    try:
                        os.remove(target)
                    except OSError:
                        pass
        self.addCleanup(cleanup)

    def resolve(self, tags):
        return tagpup_server.resolve_people_tags(tags, self.db_path)


class TestAPersonIsResolvedOnTheWayOut(PeopleResolutionCase):
    def test_a_bare_name_becomes_the_path_they_are_filed_under(self):
        self.assertEqual(
            self.resolve(["Hazel Brookmire"]), ["People/Hazel Brookmire"]
        )

    def test_someone_filed_deeper_keeps_every_level(self):
        self.assertEqual(
            self.resolve(["Linnea Ingersoll"]), ["Family/Immediate/Linnea Ingersoll"]
        )

    def test_a_name_already_pathed_is_untouched(self):
        self.assertEqual(
            self.resolve(["People/Hazel Brookmire"]), ["People/Hazel Brookmire"]
        )

    def test_a_bare_name_beside_its_own_path_is_dropped(self):
        self.assertEqual(
            self.resolve(["People/Hazel Brookmire", "Hazel Brookmire"]),
            ["People/Hazel Brookmire"],
        )

    def test_the_apply_all_case(self):
        # What folder auto-apply actually hands the writer: the photo's existing
        # pathed tags plus the suggester's leaf-named proposals.
        self.assertEqual(
            self.resolve(["Cross Country", "Kentridge", "Hazel Brookmire"]),
            ["Cross Country", "Kentridge", "People/Hazel Brookmire"],
        )

    def test_case_does_not_decide_whether_someone_is_recognised(self):
        self.assertEqual(
            self.resolve(["hazel brookmire"]), ["People/Hazel Brookmire"]
        )


class TestEverythingElseIsLeftAlone(PeopleResolutionCase):
    def test_a_flat_keyword_that_is_not_a_person_survives(self):
        # The line that must not be crossed: promoting "Cross Country" to
        # "Activity/Cross Country" is a different change, and not one to make quietly.
        tags = ["Cross Country", "Kentridge", "Graduation"]
        self.assertEqual(self.resolve(tags), tags)

    def test_a_name_nobody_is_filed_under_is_not_given_a_path(self):
        self.assertEqual(self.resolve(["Someone Unknown"]), ["Someone Unknown"])

    def test_order_is_preserved(self):
        self.assertEqual(
            self.resolve(["Linnea Ingersoll", "Kentridge", "Graduation"]),
            ["Family/Immediate/Linnea Ingersoll", "Kentridge", "Graduation"],
        )

    def test_an_empty_set_is_not_a_crash(self):
        self.assertEqual(self.resolve([]), [])

    def test_no_database_means_no_change_rather_than_a_failure(self):
        self.assertEqual(
            tagpup_server.resolve_people_tags(["Hazel Brookmire"], None),
            ["Hazel Brookmire"],
        )

    def test_a_missing_database_does_not_stop_the_write(self):
        self.assertEqual(
            tagpup_server.resolve_people_tags(["Hazel Brookmire"], "no/such.db"),
            ["Hazel Brookmire"],
        )


class TestTheCacheDoesNotGoStale(PeopleResolutionCase):
    def test_a_person_added_after_the_first_write_still_resolves(self):
        self.assertEqual(self.resolve(["Bethan Tamsin"]), ["Bethan Tamsin"])

        conn = tagpup_db.connect(self.db_path)
        conn.execute(
            "INSERT INTO tag_taxonomy (tag, name, parent_id, has_face) VALUES (?,?,?,?)",
            ("People/Bethan Tamsin", "Bethan Tamsin", 1, 1),
        )
        conn.commit()
        conn.close()

        # Without invalidation this still returns the bare name, which is the whole
        # reason the taxonomy handlers call invalidate_people_cache after saving.
        tagpup_server.invalidate_people_cache(self.db_path)
        self.assertEqual(self.resolve(["Bethan Tamsin"]), ["People/Bethan Tamsin"])

    def test_the_second_call_does_not_reread_the_database(self):
        self.resolve(["Hazel Brookmire"])
        os.rename(self.db_path, self.db_path + ".moved")
        self.addCleanup(lambda: os.path.exists(self.db_path + ".moved")
                        and os.rename(self.db_path + ".moved", self.db_path))
        self.assertEqual(
            self.resolve(["Hazel Brookmire"]), ["People/Hazel Brookmire"],
            "the mapping was not cached; every write would re-read the taxonomy"
        )


class TestEveryWriteSitePassesTheDatabase(unittest.TestCase):
    """The guard. Resolution at the boundary only works if the boundary is told where
    the taxonomy lives, and a new call site that forgets is invisible until a library
    has bare names in it again."""

    def test_no_call_to_write_keyword_fields_omits_db_path(self):
        source_path = os.path.join(WORKSPACE_DIR, "scripts", "tagpup_server.py")
        with open(source_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=source_path)

        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "write_keyword_fields":
                continue
            if not any(kw.arg == "db_path" for kw in node.keywords):
                offenders.append("tagpup_server.py:%d" % node.lineno)

        self.assertEqual(
            offenders, [],
            "these write keywords without telling the writer where the taxonomy is, "
            "so a person named by a bare leaf is written as a bare leaf: "
            + ", ".join(offenders)
        )

    def test_the_writer_still_takes_a_db_path(self):
        # The check above is worthless if the parameter has been renamed away.
        import inspect
        params = inspect.signature(tagpup_server.write_keyword_fields).parameters
        self.assertIn("db_path", params)


if __name__ == "__main__":
    unittest.main()
