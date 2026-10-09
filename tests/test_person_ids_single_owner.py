"""A person's id beside their name has one owner: tagpup.store.person_ids (docs/ARCHITECTURE.md,
"Identity by id").

`faces.tag_id` and `photo_people.tag_id` are the PERSON (the node of the tag tree; stage 2: docs/ARCHITECTURE.md,
"People by id, stage 2"), and `name` beside them the cache of the node's leaf. A writer that set a name and not the id -- or
worked the id out by a rule of its own -- would leave rows whose id names someone else, and every reader reads the id. So this
fails the build on a function in tagpup/ that

* writes `tag_id` of faces or photo_people anywhere but person_ids and is not one of the named few that take the pair from it
  (schema.py adds the column, and calls person_ids to fill it); or
* writes a face's name (UPDATE faces SET ... name, INSERT INTO faces) or a photo's people (INSERT INTO
  or UPDATE photo_people) and neither calls person_ids nor ends in faces._rebuilt, which does.

A function that does neither itself says why, here. A new writer is added to nothing: it calls
person_ids or fails this.
"""
import ast
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shipped_sources import ROOT, python_sources  # noqa: E402

OWNER = "tagpup/store/person_ids.py"

#: SQL that writes the id of a face or of a listed person.
ID_WRITE = re.compile(r"\b(?:UPDATE\s+(?:faces|photo_people)\s+SET\b(?:(?!\bWHERE\b).)*\btag_id\b"
                      r"|INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(?:faces|photo_people)\s*\([^)]*\btag_id\b)", re.I | re.S)
#: SQL that writes a face's name or a photo's people.
NAME_WRITE = re.compile(r"\b(?:UPDATE\s+faces\s+SET\b(?:(?!\bWHERE\b).)*\bname\b|UPDATE\s+faces\s+SET\s*$"
                        r"|INSERT\s+(?:OR\s+\w+\s+)?INTO\s+faces\b"
                        r"|INSERT\s+(?:OR\s+\w+\s+)?INTO\s+photo_people\b|UPDATE\s+photo_people\s+SET\b)", re.I | re.S)

#: (module, function) that write a person's id outside person_ids, and why that is the pair's one owner all the same. Each must
#: mention person_ids: the pair comes from it.
IDS_WRITTEN_WITH_THE_PAIR = {
    # target() gives the pair (the node's id and its leaf) and the INSERT writes both.
    ("tagpup/store/faces.py", "insert"): "person_ids.target",
    # Renames faces with no person to the name of one: target() gives the pair, and the UPDATE writes both.
    ("tagpup/store/faces.py", "rename_unresolved"): "person_ids.target",
    # The ids vocabulary.people_rows gave from the tree it was read from; settled by person_ids.follow_listed right after.
    ("tagpup/store/people.py", "rebuild"): "person_ids.follow_listed",
}

#: (module, function) that write names and say so, with the function that gives the ids.
COVERED_ELSEWHERE = {
    # Renames faces whose name no person is filed under; the photos' lists are rebuilt by the rule (the id comes from target).
    ("tagpup/store/faces.py", "rename_unresolved"): ("tagpup/store/people.py", "rebuild"),
    # Migration 4 rebuilt faces before the column existed; migration 21 fills it (_person_ids).
    ("tagpup/store/schema.py", "_photo_ids"): ("tagpup/store/schema.py", "_person_ids"),
    # The journal writes any row of a journaled table; _derive follows what it wrote.
    ("tagpup/store/journal.py", "_write"): ("tagpup/store/journal.py", "_derive"),
}

#: Where the follow is delegated: a function ending in `_rebuilt` is covered because `_rebuilt` calls
#: person_ids, which this holds too.
DELEGATES = {"_rebuilt": "tagpup/store/faces.py"}


def functions(tree):
    return [(node.name, node) for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def strings(node):
    doc = ast.get_docstring(node, clean=False) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
    return [child.value for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str) and child.value != doc]


def mentions(node, name):
    return any((isinstance(child, ast.Name) and child.id == name)
               or (isinstance(child, ast.Attribute) and child.attr == name) for child in ast.walk(node))


def scan():
    """([(module, function)] writing an id, [(module, function, node)] writing a name, {(module,
    function): node})."""
    ids, names, nodes = [], [], {}
    for relative in python_sources():
        module = relative.replace(os.sep, "/")
        if not module.startswith("tagpup/"):
            continue
        with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
            parsed = ast.parse(handle.read())
        for name, node in functions(parsed):
            nodes.setdefault((module, name), node)
            texts = strings(node)
            if any(ID_WRITE.search(text) for text in texts):
                ids.append((module, name))
            if any(NAME_WRITE.search(text) for text in texts):
                names.append((module, name, node))
    return ids, names, nodes


class PersonIdsHaveOneOwner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ids, cls.names, cls.nodes = scan()

    def test_the_scan_finds_what_it_is_meant_to(self):
        """A guard that matches nothing passes forever."""
        found = {(module, name) for module, name, _node in self.names}
        self.assertLessEqual({("tagpup/store/faces.py", "name"), ("tagpup/store/faces.py", "insert"),
                              ("tagpup/store/faces.py", "set_names"), ("tagpup/store/faces.py", "rename_unresolved"),
                              ("tagpup/store/people.py", "rebuild")}, found)
        self.assertIn((OWNER, "follow_faces"), self.ids)

    def test_only_person_ids_writes_an_id_but_the_few_that_take_the_pair_from_it(self):
        self.assertEqual([], [found for found in self.ids if found[0] != OWNER and found not in IDS_WRITTEN_WITH_THE_PAIR])
        for found, via in IDS_WRITTEN_WITH_THE_PAIR.items():
            with self.subTest(found):
                self.assertIn(found, self.ids, "the exception is for a function that does write an id")
                self.assertTrue(mentions(self.nodes[found], "person_ids"), "%s: the pair comes from %s" % (found, via))

    def test_every_writer_of_a_name_follows_it_with_the_id(self):
        unfollowed = []
        for module, name, node in self.names:
            if module == OWNER:
                continue
            covered = COVERED_ELSEWHERE.get((module, name))
            if covered is not None:
                self.assertIn(covered, self.nodes, "the function said to cover %s.%s is gone" % (module, name))
                if not mentions(self.nodes[covered], "person_ids") and covered[1] != "_person_ids":
                    unfollowed.append("%s.%s (by %s)" % (module, name, covered[1]))
                continue
            if mentions(node, "person_ids") or any(mentions(node, d) for d in DELEGATES):
                continue
            unfollowed.append("%s.%s" % (module, name))
        self.assertEqual([], unfollowed)

    def test_the_delegates_and_the_tree_edit_do_call_it(self):
        for delegate, module in DELEGATES.items():
            self.assertTrue(mentions(self.nodes[(module, delegate)], "person_ids"), delegate)
        self.assertTrue(mentions(self.nodes[("tagpup/store/people.py", "tree_edit")], "follow_tree"))
        self.assertTrue(mentions(self.nodes[("tagpup/store/journal.py", "_derive")], "person_ids"))
        self.assertTrue(mentions(self.nodes[("tagpup/store/schema.py", "_person_ids")], "person_ids"))


if __name__ == "__main__":
    unittest.main()
