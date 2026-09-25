"""A bulk operation records a change; a full copy of the library is taken only where the
plan allows one.

The rule in CLAUDE.md: bulk operations dry-run by default, --apply to write, and back
up first. Every script here dry-runs; six of them wrote without a backup, among them
dedupe_faces and relink_renamed_photos, which delete and re-point face rows. Three had
their own copy of a backup function.

Phase 7.5 (docs/ARCHITECTURE.md) replaced the copy -- 1.4 GB of photo_index for each
apply, five kept -- with the library's journal: a service on the maintenance scaffold
(tagpup.services.maintenance) records each apply as one change, rehearsed first and
undoable after, and copies nothing. A full copy stays only where the plan keeps one
(FULL_COPIES), and this holds every --apply script to one or the other.
"""
import os
import re
import shutil
import sys
import tempfile
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import shipped_sources  # noqa: E402
from tagpup.store import db as tagpup_db  # noqa: E402


#: A script that opens a database, in any of the ways the codebase imports db.
OPENS_A_DATABASE = re.compile(r"\bimport db\b|from tagpup\.store import db\b|\btagpup_db\b")

#: The services a script imports: `from tagpup.services import refresh_rows`.
IMPORTS_SERVICES = re.compile(r"^from tagpup\.services import ([\w, ]+)", re.M)

#: A full copy of the library, taken by the one function that makes them.
TAKES_A_COPY = re.compile(r"\b(?:db|tagpup_db)\.backup\(")

#: The only shipped files that copy the whole library, and why each may.
FULL_COPIES = {
    # Until phase 7.5's migrations stage: then only a migration that destroys
    # information copies the library, and one that changes data records its rows.
    os.path.join("tagpup", "store", "schema.py"): "a migration that rewrites data",
    # `index --reset` deletes the library; `compact` rewrites the whole file.
    "tagpup_cli.py": "a library deleted or rewritten whole",
    # backfill_document_ids and relink_renamed_photos copied it too, until phase 7.5's
    # photo-file stage (docs/findings.md, #193): each records changes of the journal now,
    # the photo files it writes among them (tagpup.services.file_changes).
}


def read(*parts):
    with open(os.path.join(WORKSPACE_DIR, *parts), encoding="utf-8") as handle:
        return handle.read()


def maintenance_services(source):
    """The services a script imports that run on the maintenance scaffold."""
    names = [name.strip() for found in IMPORTS_SERVICES.findall(source) for name in found.split(",")]
    return [name for name in names
            if os.path.exists(os.path.join(WORKSPACE_DIR, "tagpup", "services", name + ".py"))
            and re.search(r"\bmaintenance\.run\(", read("tagpup", "services", name + ".py"))]


def bulk_scripts():
    """Scripts that write with --apply and open a database. (install_app.py writes with
    --apply too, but files, not a database.)"""
    folder = os.path.join(WORKSPACE_DIR, "scripts")
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as handle:
            source = handle.read()
        if '"--apply"' in source and (OPENS_A_DATABASE.search(source) or maintenance_services(source)):
            yield name, source


class EveryBulkScriptRecordsAChange(unittest.TestCase):
    def test_there_are_bulk_scripts_to_check(self):
        self.assertGreaterEqual(len(list(bulk_scripts())), 5)

    def test_each_one_records_a_change_or_may_copy_the_library(self):
        missing = [name for name, source in bulk_scripts()
                   if not maintenance_services(source)
                   and not (os.path.join("scripts", name) in FULL_COPIES and TAKES_A_COPY.search(source))]
        self.assertEqual([], missing, "these write with --apply, record no change and take no copy")

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

    def test_none_keeps_its_own_copy(self):
        own = [name for name, source in bulk_scripts() if re.search(r"^def backup\(", source, re.M)]
        self.assertEqual([], own)

    def test_index_reset_backs_up_before_it_deletes(self):
        # `index --reset` deletes the database, every hand-named face with it, one
        # flag away from an ordinary run. (`cluster-faces --reset` clears only the
        # names clustering gave, which clustering gives again; it keeps hand-given
        # ones, and needs no copy of the library each time.)
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
