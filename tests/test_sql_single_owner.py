"""SQL is written only inside tagpup.store.

On 2026-09-24, 210 SQL calls sat outside it, in both servers, the CLI, the runner, a
service and a dozen scripts, and the same question was asked of the database in several
places in several ways: three copies of who is named on each photo, four of the schema
(docs/findings.md, #48). Phase 3 moved every one into the store. This keeps it there.

A statement is a string that starts with an SQL keyword in capitals, or a call to
executemany or executescript. Docstrings are prose, and are left out.
"""
import ast
import os
import re
import unittest

from tests.shipped_sources import ROOT, python_sources

#: The start of an SQL statement, as this codebase writes them: in capitals.
STATEMENT = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|PRAGMA|BEGIN|REPLACE|WITH)\b")

OWNER = os.path.join("tagpup", "store") + os.sep


def docstrings(tree):
    """The string constants that are docstrings, by id."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                found.add(id(body[0].value))
    return found


def sql_in(source):
    """(line, what) of each SQL statement in `source`."""
    tree = ast.parse(source)
    # A docstring is prose; the pieces of an f-string are judged as the whole.
    prose = docstrings(tree) | {id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)
                                for part in node.values}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in prose:
            if STATEMENT.match(node.value):
                found.append((node.lineno, node.value.strip().split("\n")[0][:60]))
        elif isinstance(node, ast.JoinedStr) and node.values:
            first = node.values[0]
            if isinstance(first, ast.Constant) and STATEMENT.match(str(first.value)):
                found.append((node.lineno, str(first.value).strip()[:60]))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("executemany", "executescript"):
            found.append((node.lineno, node.func.attr))
    return found


class SqlOnlyInTheStore(unittest.TestCase):
    def test_no_shipped_file_outside_the_store_holds_sql(self):
        problems = []
        for relative in python_sources():
            if relative.startswith(OWNER):
                continue
            with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                for line, what in sql_in(handle.read()):
                    problems.append("%s:%d  %s" % (relative, line, what))
        self.assertEqual(problems, [], "SQL outside tagpup.store:\n  " + "\n  ".join(problems))

    def test_the_guard_sees_a_statement_however_it_is_written(self):
        source = '''
def plain(conn):
    """SELECT in a docstring is prose."""
    return conn.execute("SELECT path FROM photos").fetchall()

def built(conn, where):
    return conn.execute("UPDATE faces SET name = NULL WHERE " + where)

def formatted(conn, table):
    return conn.execute(f"DELETE FROM {table}")

def many(conn, rows):
    conn.executemany("?", rows)
'''
        self.assertEqual([4, 7, 10, 13], sorted(line for line, _ in sql_in(source)))

    def test_prose_that_starts_like_sql_is_not_sql(self):
        # Lower case, and a message to a person, is not a statement.
        self.assertEqual([], sql_in('message = "select a folder first"\nhint = "Update the app"\n'))


if __name__ == "__main__":
    unittest.main()
