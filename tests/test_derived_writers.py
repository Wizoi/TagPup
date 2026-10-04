"""A writer of a photo's keywords, path or metadata, of a photo's row or of the tag tree, goes
through tagpup.store.derived (docs/ARCHITECTURE.md, phase 9a).

photo_tags, folders, photo_folder and photo_meta are derived from `photos.tags`, `photos.path`,
`photos.raw_metadata` and `tag_taxonomy`, and kept by the writes that change one of them, in the
same transaction. A writer that forgot -- the bulk tag writes once did not tell the index what they
wrote (CLAUDE.md) -- leaves the views describing what a photo used to hold. So this fails the build
on a function in tagpup/ that

* updates `photos` SET `tags`, `path` or `raw_metadata` (or a column list built at run time), inserts
  into `photos` or deletes from `photos`, and does not call something of `derived`; or
* inserts into, updates or deletes from `tag_taxonomy` and is not inside `people.tree_edit`
  (which follows the tree's edit for the photos whose keywords it names).

A function that does neither itself says why, here, and the function that does it for it is held to
the same rule. A new writer is added to nothing here: it calls `derived` or fails this.
"""
import ast
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shipped_sources import ROOT, python_sources  # noqa: E402

#: SQL that changes what the derived tables are derived from, in a string of the source.
PHOTO_WRITE = re.compile(r"\b(?:UPDATE\s+photos\s+SET\s+(?P<set>(?:(?!\bWHERE\b)[^'\"])*)|INSERT\s+(?:OR\s+\w+\s+)?INTO\s+photos\b"
                         r"|DELETE\s+FROM\s+photos\b|REPLACE\s+INTO\s+photos\b)", re.I)
TREE_WRITE = re.compile(r"\b(?:UPDATE\s+tag_taxonomy\s+SET|INSERT\s+(?:OR\s+\w+\s+)?INTO\s+tag_taxonomy\b"
                        r"|DELETE\s+FROM\s+tag_taxonomy\b)", re.I)
#: The columns of `photos` the tables are derived from (captions: the word index, tagpup.store.search_index, kept from
#: tagpup.store.derived).
DERIVED_FROM = ("tags", "path", "raw_metadata", "captions")

#: (module, function) that write the rows and say so, with the function that makes up for it.
PHOTO_WRITERS_COVERED_ELSEWHERE = {
    # Migration 4 ran before the tables existed; migration 19 makes them by rebuild_all.
    ("tagpup/store/schema.py", "_photo_ids"): ("tagpup/store/schema.py", "_derived_tables"),
    # A photo made for a path nothing was read from (Suggest's, a face's): ensure_row refreshes it.
    ("tagpup/store/photos.py", "_unread_row"): ("tagpup/store/photos.py", "ensure_row"),
    # The adoption converts every path in one pass; the folders are rebuilt after it, in the same
    # transaction, by adopt and by undo_in (the keywords and the metadata are not changed by it).
    ("tagpup/store/adoption.py", "_photos_pass"): ("tagpup/store/adoption.py", "adopt"),
}

#: The adoption's undo rebuilds the folders too.
ALSO_CALLS = {("tagpup/store/adoption.py", "_photos_pass"): [("tagpup/store/adoption.py", "undo_in")]}

#: Functions that write the tree and are only ever called inside people.tree_edit.
TREE_WRITERS_COVERED_ELSEWHERE = {
    ("tagpup/store/taxonomy.py", "add_path"): "add_node, repair, move_branch, seed and TagTaxonomy.save_to_db",
}


#: Writers the scan cannot see, since the table is a variable in their SQL (the journal writes any row of
#: a journaled table, photos among them): the function that follows what they wrote is held to calling derived.
GENERIC_WRITERS = {("tagpup/store/journal.py", "_write"): ("tagpup/store/journal.py", "_derive")}


def functions(tree):
    """[(name, node)] of each function of a module, inner ones and methods too: two may share a name."""
    return [(node.name, node) for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def strings(node, skip_docstring=True):
    """The text of each string constant in `node`, a docstring left out."""
    found = []
    doc = ast.get_docstring(node, clean=False) if skip_docstring and isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str) and child.value != doc:
            found.append(child.value)
    return found


def calls_derived(node):
    """Does `node` mention `derived` -- a call of the module, or its Batch?"""
    return any(isinstance(child, ast.Name) and child.id in ("derived", "store_derived") for child in ast.walk(node))


def inside_tree_edit(node):
    return any(isinstance(child, ast.Attribute) and child.attr == "tree_edit" for child in ast.walk(node))


def writes_photos(text):
    """Does this SQL change what the tables are derived from?"""
    for found in PHOTO_WRITE.finditer(text):
        columns = found.group("set")
        if columns is None or "%s" in columns or any(re.search(r"\b%s\b" % c, columns) for c in DERIVED_FROM):
            return True
    return False


def scan():
    """([(module, function, node)] writing photos, the same writing the tree, {(module, function): node} of
    every function, the first of a name)."""
    photos, tree, nodes = [], [], {}
    for relative in python_sources():
        if not relative.startswith("tagpup" + os.sep) and not relative.startswith("tagpup/"):
            continue
        module = relative.replace(os.sep, "/")
        with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
            parsed = ast.parse(handle.read())
        for name, node in functions(parsed):
            nodes.setdefault((module, name), node)
            texts = strings(node)
            if any(writes_photos(text) for text in texts):
                photos.append((module, name, node))
            if any(TREE_WRITE.search(text) for text in texts):
                tree.append((module, name, node))
    return photos, tree, nodes


class DerivedTablesAreKeptByTheirWriters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.photos, cls.tree, cls.nodes = scan()

    def test_the_scan_finds_the_writers_it_is_meant_to(self):
        """A guard that matches nothing passes forever."""
        found = {name for _module, name, _node in self.photos}
        for known in ("record_indexed", "record_tags", "move_rows_in", "follow_fields", "record_saved",
                      "forget_photo", "remove", "remove_under", "_photos_pass"):
            self.assertIn(known, found)
        self.assertIn("add_path", {name for _module, name, _node in self.tree})
        self.assertTrue(writes_photos("UPDATE photos SET tags = ?, raw_metadata = ? WHERE id = ?"))
        self.assertTrue(writes_photos("UPDATE photos SET captions = ? WHERE id = ?"))
        self.assertTrue(writes_photos("UPDATE photos SET %s WHERE id = ?"))
        self.assertTrue(writes_photos("INSERT INTO photos (path) VALUES (?)"))
        self.assertTrue(writes_photos("DELETE FROM photos WHERE path = ?"))
        self.assertFalse(writes_photos("UPDATE photos SET mtime = ?, size = ? WHERE id = ?"))
        self.assertFalse(writes_photos("UPDATE photos SET taken = ?, year = ? WHERE id = ?"))
        self.assertFalse(writes_photos("UPDATE photos SET document_id = ? WHERE path = ?"))
        self.assertFalse(writes_photos("SELECT tags FROM photos WHERE id = ?"))

    def test_every_writer_of_a_photos_keywords_path_or_metadata_calls_derived(self):
        missing = []
        for module, name, node in self.photos:
            if calls_derived(node) or (module, name) in PHOTO_WRITERS_COVERED_ELSEWHERE:
                continue
            missing.append("%s: %s" % (module, name))
        self.assertEqual([], missing, "\n\nthese write a photo's tags, path or raw_metadata, or insert or delete a "
                         "photo, and do not tell tagpup.store.derived (refresh_photos / record / prune), so photo_tags, "
                         "folders, photo_folder and photo_meta keep describing what the photo used to hold:\n"
                         + "\n".join(missing))

    def test_what_makes_up_for_a_writer_does_so(self):
        writers = {(module, name) for module, name, _node in self.photos}
        for key, covering in PHOTO_WRITERS_COVERED_ELSEWHERE.items():
            self.assertIn(key, writers, "%s:%s is no longer a writer: take it out of the list" % key)
            for each in [covering] + ALSO_CALLS.get(key, []):
                self.assertTrue(calls_derived(self.nodes[each]), "%s:%s does not call derived" % each)
        for key, covering in GENERIC_WRITERS.items():
            self.assertIn(key, self.nodes, "%s:%s is gone" % key)
            self.assertTrue(calls_derived(self.nodes[covering]), "%s:%s does not call derived" % covering)

    def test_every_writer_of_the_tree_is_inside_tree_edit(self):
        missing = []
        for module, name, node in self.tree:
            if inside_tree_edit(node) or (module, name) in TREE_WRITERS_COVERED_ELSEWHERE:
                continue
            missing.append("%s: %s" % (module, name))
        self.assertEqual([], missing, "\n\nthese write the tag tree outside people.tree_edit, which makes the keyword "
                         "rows of the photos whose keywords a node names follow it:\n" + "\n".join(missing))

    def test_the_tree_writer_that_leaves_it_to_its_callers_has_only_callers_that_do_not(self):
        """add_path is called by the functions that wrap it in tree_edit and by nothing else."""
        callers = []
        for (module, name), node in self.nodes.items():
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, (ast.Name, ast.Attribute)):
                    called = child.func.id if isinstance(child.func, ast.Name) else child.func.attr
                    if called == "add_path" and name != "add_path":
                        callers.append((module, name, inside_tree_edit(node)))
        self.assertTrue(callers)
        self.assertEqual([], [(module, name) for module, name, wrapped in callers if not wrapped],
                         "add_path called outside tree_edit")


if __name__ == "__main__":
    unittest.main()
