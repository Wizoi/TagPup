"""A bulk operation records a change; a full copy of the library is taken only where the
plan allows one.

The rule in CLAUDE.md: bulk operations dry-run by default, --apply to write, and back
up first. Phase 7.5 (docs/ARCHITECTURE.md) replaced the copy -- 1.4 GB of photo_index for
each apply, five kept -- with the library's journal: a service on the maintenance scaffold
(tagpup.services.maintenance) records each apply as one change, rehearsed first and
undoable after, and copies nothing. A full copy stays only where the plan keeps one
(FULL_COPIES). No script in scripts/ writes a database with --apply now; the operations
are services run by the CLI and the MCP server.
"""
import os
import re
import shutil
import sys
import tempfile
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import shipped_sources  # noqa: E402
from tagpup.store import db as tagpup_db  # noqa: E402


#: A full copy of the library, taken by the one function that makes them.
TAKES_A_COPY = re.compile(r"\b(?:db|tagpup_db)\.backup\(")

#: The only shipped files that copy the whole library, and why each may.
FULL_COPIES = {
    # Until phase 7.5's migrations stage: then only a migration that destroys
    # information copies the library, and one that changes data records its rows.
    os.path.join("tagpup", "store", "schema.py"): "a migration that rewrites data",
    # `index --reset` deletes the library; `compact` rewrites the whole file.
    "tagpup_cli.py": "a library deleted or rewritten whole",
    # The bulk scripts copied it too, until phase 7.5's
    # photo-file stage (docs/findings.md, #193): each records changes of the journal now,
    # the photo files it writes among them (tagpup.services.file_changes).
}


def read(*parts):
    with open(os.path.join(WORKSPACE_DIR, *parts), encoding="utf-8") as handle:
        return handle.read()


class BulkOperationsRecordAChange(unittest.TestCase):
    def test_the_scaffold_records_a_change_and_copies_nothing(self):
        source = read("tagpup", "services", "maintenance.py")
        self.assertRegex(source, r"\bjournal\.apply\(")
        self.assertRegex(source, r"\bjournal\.rehearse\(")
        self.assertNotRegex(source, r"\.backup\(")

    def test_only_the_files_the_plan_allows_copy_the_library(self):
        copying = {path for path in shipped_sources.python_sources()
                   if TAKES_A_COPY.search(read(path))}
        self.assertEqual(set(FULL_COPIES), copying,
                         "a full copy of the library outside FULL_COPIES, or one listed there that takes none")

    def test_index_reset_backs_up_before_it_deletes(self):
        # `index --reset` deletes the database, every hand-named face with it, one
        # flag away from an ordinary run.
        import ast
        with open(os.path.join(WORKSPACE_DIR, "tagpup_cli.py"), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        index = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "index")
        reset = next(n for n in ast.walk(index) if isinstance(n, ast.If)
                     and getattr(n.test, "id", None) == "reset")
        calls = [name for _, name in sorted(
            (n.lineno, ast.unparse(n.func)) for n in ast.walk(reset) if isinstance(n, ast.Call))]
        self.assertIn("tagpup_db.backup", calls)
        self.assertLess(calls.index("tagpup_db.backup"), calls.index("os.remove"),
                        "the backup comes after the delete")


class TheBackupIsACopy(unittest.TestCase):
    def test_it_holds_what_the_database_held(self):
        tmp = tempfile.mkdtemp(prefix="db_backup_")
        self.addCleanup(shutil.rmtree, tmp, True)
        source = os.path.join(tmp, "kr-track.db")
        conn = tagpup_db.connect(source)
        conn.execute("CREATE TABLE faces (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO faces (name) VALUES ('Rowan Thackeray')")
        conn.commit()
        conn.close()

        copy = tagpup_db.backup(source, "dedupe-faces", into=os.path.join(tmp, "backups"))

        self.assertRegex(os.path.basename(copy), r"^kr-track\.before-dedupe-faces-\d{8}_\d{6}\.db$")
        conn = tagpup_db.connect(tagpup_db.readonly_uri(copy), uri=True)
        try:
            self.assertEqual([("Rowan Thackeray",)], conn.execute("SELECT name FROM faces").fetchall())
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
