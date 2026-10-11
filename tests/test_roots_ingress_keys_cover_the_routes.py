"""Every parameter a route reads is either one the Roots ingress resolves (roots_ingress.KEYS) or reviewed here
as no path.

The ingress turns the values of KEYS into the first place's spelling before any route reads them; a route that
reads a path under another name (`where`, `source_folder`) is served the previous place's spelling after a move,
and writes a second cache entry, serves the old copy or opens the old file (findings #465, #480-#482). KEYS,
LET_THROUGH-style lists are written by hand, and tests/test_roots_ingress.py checks the names it was given, not the
routes that exist. So this reads the routes: the names a handler takes from `request.args` and from the request's
JSON (directly, or through a variable it was assigned to, or the `int_arg` helpers), and fails on a name that is
neither in KEYS nor in the list below, which says why each is no path. A new parameter is a decision made here.
"""
import ast
import glob
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.web import roots_ingress  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Read by a route and no path: a flag, a number, an id, a tag or a person's name, text.
NOT_A_PATH = frozenset({
    # flags, numbers, modes, text
    "acknowledged", "action", "after", "all", "apply", "base", "before", "bulk", "camera_model", "cluster",
    "date_taken", "direction", "dry_run", "force", "group", "grouping", "has_face", "hidden_from_autocomplete",
    "include_hidden", "kind", "lease", "let_go", "level", "limit", "mode", "only_folder", "op", "order", "override",
    "page", "page_writes_tags", "quiet", "reason", "recursive", "retire", "run", "runs", "seconds", "section",
    "shift_minutes", "show_matched", "size", "stamp", "text", "threshold", "title", "undo", "upright", "v", "values",
    # ids, names, tags
    "db_name", "face_id", "face_ids", "id", "ids", "job", "library", "name", "new_name", "old_name", "parent_id",
    "people", "person_id", "person_name", "records", "tag", "tag_id", "tags", "target_tag", "add_tags",
    "remove_tags", "into", "to",
})

#: Read by a route and a path, resolved somewhere else, each with where.
RESOLVED_ELSEWHERE = {
    # The library view's sources: the service canonicalises every folder of a source or a union
    # (tagpup.services.library_view._source_of; the selection's source is read by the same function).
    "value": "services/library_view._source_of canonicalises a folder source and each member of a union",
    "selection": "services/selection.read -> library_view sources",
    "params": "bulk edit's parameters: tags and people, by id; the photos are the selection's",
    # The roots routes: a place being chosen, or seen (LEFT_ALONE).
    "location": "/api/roots, left alone on purpose",
    "from": "/api/roots (a place the page saw) and tags_merge (a tag): left alone / no path",
}

#: URL variables of the routes, and no path.
URL_VARIABLES = frozenset({"name", "change_id"})

#: Helpers that read a request parameter by a name their caller gives; the callers' literal names are read below.
NAMED_BY_CALLER = {"_int_arg": 0, "int_arg": 0}


def _is_request(node, names):
    return (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "request"
            and node.attr in names)


def _source(value):
    """'args' or 'body' for the expression request.args / the request's JSON (possibly `... or {}`)."""
    if isinstance(value, ast.BoolOp):
        value = value.values[0]
    if _is_request(value, ("args",)):
        return "args"
    if isinstance(value, ast.Call) and _is_request(value.func, ("get_json",)):
        return "body"
    if _is_request(value, ("json",)):
        return "body"
    return None


def parameters_read():
    """{name: [(file, function), ...]} of the request parameters the web layer's handlers read; and the
    [(file, function)] that read one by a name they were not given as a literal."""
    found, unnamed, variables = {}, [], []
    for file in sorted(glob.glob(os.path.join(ROOT, "tagpup", "web", "*.py"))):
        with open(file, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        base = os.path.basename(file)
        if base == "roots_ingress.py":
            continue                         # the ingress reads KEYS by name; it is what is being checked
        for func in ast.walk(tree):
            if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in func.decorator_list:
                    for node in ast.walk(decorator):
                        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "<" in node.value:
                            variables.append(node.value)
        for func in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            held = {}
            for node in ast.walk(func):
                if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                        and _source(node.value)):
                    held[node.targets[0].id] = _source(node.value)

            def kind_of(expr):
                return held.get(expr.id) if isinstance(expr, ast.Name) else _source(expr)

            for node in ast.walk(func):
                key, holder = None, None
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("get", "getlist", "pop") and node.args):
                    holder, first = kind_of(node.func.value), node.args[0]
                elif isinstance(node, ast.Subscript):
                    holder, first = kind_of(node.value), node.slice
                elif (isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.In)):
                    holder, first = kind_of(node.comparators[0]), node.left
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in NAMED_BY_CALLER \
                        and node.args:
                    holder, first = "args", node.args[NAMED_BY_CALLER[node.func.id]]
                else:
                    continue
                if not holder:
                    continue
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    key = first.value
                    found.setdefault(key, []).append((base, func.name))
                else:
                    unnamed.append((base, func.name))
    return found, unnamed, variables


class EveryParameterIsClassified(unittest.TestCase):
    def test_a_parameter_read_is_resolved_by_the_ingress_or_reviewed_as_no_path(self):
        found, _unnamed, _variables = parameters_read()
        self.assertGreater(len(found), 60, "the scan found almost nothing: it no longer reads the routes")
        known = set(roots_ingress.KEYS) | NOT_A_PATH | set(RESOLVED_ELSEWHERE)
        unclassified = {key: where[:3] for key, where in found.items() if key not in known}
        self.assertEqual({}, unclassified,
                         "a route reads a parameter that is neither in roots_ingress.KEYS nor reviewed in this file as "
                         "no path: if it holds a path, add it to KEYS (the ingress resolves it through the root's first "
                         "place), else to NOT_A_PATH")

    def test_every_key_the_ingress_resolves_is_read_by_a_route(self):
        found, _unnamed, _variables = parameters_read()
        self.assertEqual([], [key for key in roots_ingress.KEYS if key not in found])

    def test_a_name_is_never_both_resolved_and_called_no_path(self):
        self.assertEqual(set(), set(roots_ingress.KEYS) & (NOT_A_PATH | set(RESOLVED_ELSEWHERE)))

    def test_a_parameter_read_by_a_name_that_is_not_a_literal_is_a_helper_whose_callers_give_literals(self):
        _found, unnamed, _variables = parameters_read()
        helpers = {"_int_arg", "int_arg"}
        self.assertEqual([], [where for where in unnamed if where[1] not in helpers],
                         "a handler reads a parameter by a name it computes: classify it by hand")

    def test_a_url_variable_is_no_path(self):
        _found, _unnamed, variables = parameters_read()
        names = set()
        for text in variables:
            for part in text.split("<")[1:]:
                names.add(part.split(">")[0].split(":")[-1])
        self.assertLessEqual(names, URL_VARIABLES)


if __name__ == "__main__":
    unittest.main()
