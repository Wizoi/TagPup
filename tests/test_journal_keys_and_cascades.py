"""Keys never reused, and cascades into journaled tables recorded or forbidden.

An undo puts a deleted row back under its old key. If SQLite could hand that key out
again, the row put back could collide with a newer one -- or, worse, the undo's check
that the row is gone could pass because a different row now has it. So every table the
journal writes (tagpup.store.journal.KEYS) is keyed by an AUTOINCREMENT id, or by the id
of a table that has one.

A delete also takes rows it does not name: a trigger deletes a face's crop and a photo's
vectors, people and suggestions on any connection, and ON DELETE CASCADE deletes a
photo's faces and a node's children on a connection with foreign keys on. Each of those,
read from a new library's schema rather than from a list, must be one the journal
records, rebuilds (derived data only) or refuses (journal.CASCADES). A new trigger or
foreign key that deletes from a journaled table fails here until the journal says what
it does about it (docs/ARCHITECTURE.md, phase 7.5).
"""
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.store import db, journal, schema  # noqa: E402

#: A trigger that deletes one table's rows when another's go.
DELETES_WITH = re.compile(r"AFTER DELETE ON (\w+)\s+BEGIN\s+DELETE FROM (\w+) WHERE (\w+) = OLD\.(\w+)", re.I)


class ANewLibrary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = tempfile.mkdtemp(prefix="journal_keys_")
        cls.addClassCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "library.db")
        schema.ensure(path)
        conn = db.connect(db.readonly_uri(path), uri=True)
        try:
            cls.sql = {name: sql for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table'")}
            cls.triggers = [sql for (sql,) in conn.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger'")]
            cls.foreign = {table: conn.execute("PRAGMA foreign_key_list(%s)" % table).fetchall()
                           for table in cls.sql if not table.startswith("sqlite_")}
            cls.primary = {table: [row[1] for row in sorted(conn.execute("PRAGMA table_info(%s)" % table),
                                                             key=lambda row: row[5]) if row[5]]
                           for table in cls.sql if not table.startswith("sqlite_")}
        finally:
            conn.close()

    def autoincrement(self, table):
        return "AUTOINCREMENT" in self.sql[table].upper()

    def test_every_journaled_table_is_keyed_by_ids_never_handed_out_again(self):
        for table, key in journal.KEYS.items():
            self.assertEqual(list(key), self.primary[table], "%s: the journal keys it otherwise" % table)
            if self.autoincrement(table):
                continue
            # Else its key starts with the id of a table that is AUTOINCREMENT.
            parents = [row[2] for row in self.foreign[table] if row[3] == key[0] and row[4] == "id"]
            self.assertTrue(parents and all(self.autoincrement(p) for p in parents),
                            "%s is keyed by %s, which SQLite may hand out again" % (table, key[0]))

    def test_the_journal_itself_never_reuses_a_change_id(self):
        self.assertTrue(self.autoincrement("changes"))

    def cascades(self):
        """{(parent, child)}: every table whose rows go when a journaled table's rows do."""
        found = set()
        for sql in self.triggers:
            match = DELETES_WITH.search(sql)
            if match:
                found.add((match.group(1), match.group(2)))
        for child, keys in self.foreign.items():
            for row in keys:
                if row[6].upper() == "CASCADE":
                    found.add((row[2], child))
        return {(parent, child) for parent, child in found if parent in journal.KEYS}

    def test_every_cascade_is_recorded_rebuilt_or_forbidden(self):
        cascades = self.cascades()
        self.assertIn(("faces", "face_crops"), cascades, "the schema was not read")
        unknown = cascades - set(journal.CASCADES)
        self.assertEqual(set(), unknown, "deletes the journal would not see: say in journal.CASCADES what it does")
        for (parent, child), (column, how) in journal.CASCADES.items():
            self.assertIn(how, (journal.RECORDED, journal.REBUILT, journal.FORBIDDEN))
            self.assertIn(column, self.primary[child] + [row[3] for row in self.foreign[child]],
                          "%s.%s names no parent" % (child, column))
            if how == journal.REBUILT:
                self.assertIn(child, journal.DERIVED, "only derived data is rebuilt: %s" % child)
            if how == journal.RECORDED:
                self.assertIn(child, journal.KEYS, "a recorded cascade is a journaled table: %s" % child)

    def test_derived_tables_are_never_journaled(self):
        self.assertEqual(set(), set(journal.DERIVED) & set(journal.KEYS))
        self.assertIn("photo_people", journal.DERIVED)


if __name__ == "__main__":
    unittest.main()
