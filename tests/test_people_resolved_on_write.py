"""No write path can put a bare person name into a photo's keywords.

A person's name reaches this program as a leaf from half a dozen directions: the faces
table, CLIP suggestions, neighbour propagation, a name typed into a box. Each of those
paths resolving it for itself is exactly how "Hazel Brookmire" kept being written
beside "People/Hazel Brookmire" -- one of them always got missed. Folder auto-apply
outlived three separate fixes, because it writes on the server, from a suggestion list
computed before any of the UI-side resolution ran.

So resolution moved to the write boundary: every keyword write resolves people through
tagpup.core.vocabulary.resolve_people, over what the library's tree says
(tagpup.store.taxonomy.people_paths). The last class is the guard: a new keyword write
that forgets fails here rather than in someone's library.
"""
import ast
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

from tagpup.core import vocabulary  # noqa: E402
from tagpup.store import db as tagpup_db  # noqa: E402
from tagpup.store import taxonomy as store_taxonomy  # noqa: E402


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


def resolve_people_tags(tags, db_path):
    """What every writer does: the people of `tags` filed where the library at
    `db_path` files them."""
    return vocabulary.resolve_people(tags, store_taxonomy.people_paths(db_path))


class PeopleResolutionCase(unittest.TestCase):
    def setUp(self):
        import tempfile
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.db_path)
        make_db(self.db_path)
        store_taxonomy.forget_people_paths()

        def cleanup():
            store_taxonomy.forget_people_paths()
            for suffix in ("", "-wal", "-shm"):
                target = self.db_path + suffix
                if os.path.exists(target):
                    try:
                        os.remove(target)
                    except OSError:
                        pass
        self.addCleanup(cleanup)

    def resolve(self, tags):
        return resolve_people_tags(tags, self.db_path)


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
            resolve_people_tags(["Hazel Brookmire"], None),
            ["Hazel Brookmire"],
        )

    def test_a_missing_database_does_not_stop_the_write(self):
        self.assertEqual(
            resolve_people_tags(["Hazel Brookmire"], "no/such.db"),
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
        # reason the tree's edits forget the cache after saving; a table without the
        # generation triggers, as this hand-made one is, has nothing else to say so.
        store_taxonomy.forget_people_paths(self.db_path)
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


class TestEveryWriteSiteResolvesPeople(unittest.TestCase):
    """The guard. Resolution at the boundary only works if every keyword write goes
    through it, and a new call site that forgets is invisible until a library has bare
    names in it again."""

    #: Where keywords are written: the services, the CLI's `write` among them.
    SOURCES = [
        os.path.join("tagpup", "services", name)
        for name in sorted(os.listdir(os.path.join(WORKSPACE_DIR, "tagpup", "services")))
        if name.endswith(".py")]

    def writes(self):
        """(where, the call, the function it is in) of every write_keywords call in
        SOURCES."""
        for relative in self.SOURCES:
            source_path = os.path.join(WORKSPACE_DIR, relative)
            with open(source_path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=source_path)
            for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
                for node in ast.walk(function):
                    if isinstance(node, ast.Call) and (
                            getattr(node.func, "id", None) or getattr(node.func, "attr", None)) == "write_keywords":
                        yield "%s:%d" % (relative, node.lineno), node, function

    @staticmethod
    def _resolves(expression, function):
        """Is `expression`, the tags handed to a write, a resolve_people call -- or a
        name the function assigned one to?"""
        def is_resolution(node):
            return isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "resolve_people"

        if is_resolution(expression):
            return True
        if isinstance(expression, ast.Name):
            return any(isinstance(node, ast.Assign) and is_resolution(node.value)
                       and any(isinstance(t, ast.Name) and t.id == expression.id for t in node.targets)
                       for node in ast.walk(function))
        return False

    def test_no_call_to_write_keywords_skips_resolution(self):
        offenders = []
        for where, call, function in self.writes():
            tags = call.args[2] if len(call.args) > 2 else None
            if not self._resolves(tags, function):
                offenders.append(where)
        self.assertEqual(
            offenders, [],
            "these write keywords without resolving people to the tags they are filed "
            "under, so a person named by a bare leaf is written as a bare leaf: "
            + ", ".join(offenders)
        )

    def test_the_guard_sees_the_writers(self):
        # The check above is worthless if the writes moved away from what it reads.
        found = [where for where, _call, _function in self.writes()]
        self.assertTrue(any("tagging.py" in where for where in found), found)
        self.assertIn("write_suggestions", [function.name for _where, _call, function in self.writes()])


if __name__ == "__main__":
    unittest.main()
