"""A write of many photo files goes through the one batch model (tagpup.services.batches; DECISIONS 2026-10-11, "One batch model").

`file_changes.write_fields` is where a batch is claimed, capped, chunked and stopped, so a route that writes photo files is safe
when it reaches it. A new route that does must be named here, with what bounds it, so that whoever adds it asks whether it can write
more than one chunk (batches.CHUNK) and what the person sees while it does. The reach is read from the code: each function's calls
are resolved by the module that defines them (a module imported by name, or a function imported from one; a method called on
`self`), from each POST route's view to `file_changes.write_fields`.
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SINK = ("tagpup.services.file_changes", "write_fields")
WRITES = {"POST", "PUT", "PATCH", "DELETE"}

#: Routes that reach write_fields with as many photos as the person chose: a batch, claimed (one at a time, the rest queued),
#: capped at batches.MAX_BATCH, stopped by Cancel or a hand-over at its chunk, listed on the Activity page.
BATCHES = {
    "/api/library/bulk-edit": "bulk_edit.run takes the claim first (a delete is a batch too, with the same chunks)",
    "/api/photos/bulk-tags": "file_changes.batch_exclusively takes the claim before the lock, and refuses over the cap before it",
    "/api/folder/auto-apply": "file_changes.batch_exclusively takes the claim before the lock",
    "/api/folder/time-shift": "write_fields takes the claim; over the cap it is refused",
    "/api/folder/rename-photos": "the original names are written by write_fields under Smart Rename's lock (a batch waiting ahead "
                                 "refuses it rather than wait); Smart Rename refuses over the cap before it reads a caption",
    "/api/taxonomy/rename": "the tag tree's rewrite: no cap (owner, 2026-10-11), a batch with progress and Cancel",
    "/api/taxonomy/delete-confirm": "the tag tree's rewrite: no cap, a batch",
    "/api/tags/merge": "the tag tree's rewrite: no cap, a batch",
    "/api/person/rename": "the tag tree's rewrite: no cap, a batch",
    "/api/faces/match-bulk": "face_assignment.run: one batch, the photos' faces written in the transaction that marks each file done; "
                             "refused over the cap saying so",
    "/api/faces/unmatch-bulk": "face_assignment.run: one batch (faces with no name to take off touch no photo)",
    "/api/folder/automatch": "face_assignment.run: Re-examine's guesses are one batch",
    "/api/faces/exclude": "bulk (Exclude selected, Ignore cluster): face_assignment.run, one batch; one face's photo otherwise "
                          "(face_people.write_tags)",
}

#: Routes that reach write_fields for one photo, or a few at a time (tagpup.services.face_people.write_tags: one change, a batch
#: over batches.CHUNK).
SMALL = {
    "/api/photo/save-metadata": "one photo",
    "/api/face/match": "one face's photo",
    "/api/face/unmatch": "one face's photo",
    "/api/photo/automatch": "one photo",
    "/api/photo/unmatch-all": "one photo",
    "/api/photo/rotate": "one photo; photos.rotate_photos takes any number through the same claim and chunks (no route sends more)",
}


def functions():
    """{(module, name): {(module, name) it calls}} of every function and method in tagpup/."""
    trees = {}
    for folder, _dirs, files in os.walk(os.path.join(ROOT, "tagpup")):
        for name in files:
            if name.endswith(".py"):
                dotted = os.path.relpath(os.path.join(folder, name), ROOT)[:-3].replace(os.sep, ".")
                with open(os.path.join(folder, name), encoding="utf-8") as handle:
                    trees[dotted[:-9] if dotted.endswith(".__init__") else dotted] = ast.parse(handle.read())
    graph = {}
    for module, tree in trees.items():
        known = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                known.update({(a.asname or a.name.split(".")[-1]): (a.name, None) for a in node.names if a.name in trees})
            elif isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    if node.module + "." + a.name in trees:
                        known[a.asname or a.name] = (node.module + "." + a.name, None)
                    elif node.module in trees:
                        known[a.asname or a.name] = (node.module, a.name)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = graph.setdefault((module, node.name), set())
            for call in (each for each in ast.walk(node) if isinstance(each, ast.Call)):
                function = call.func
                if isinstance(function, ast.Name):
                    calls.add(known[function.id] if function.id in known and known[function.id][1] else (module, function.id))
                elif isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
                    if function.value.id in known and known[function.value.id][1] is None:
                        calls.add((known[function.value.id][0], function.attr))
                    elif function.value.id == "self":
                        calls.add((module, function.attr))
    return graph


def reaches(graph, start, wanted):
    seen, todo = set(), [start]
    while todo:
        for found in graph.get(todo.pop(), ()):
            if found == wanted:
                return True
            if found not in seen:
                seen.add(found)
                todo.append(found)
    return False


class EveryRouteThatWritesFilesIsKnown(unittest.TestCase):
    def test_the_routes_that_reach_write_fields_are_exactly_the_ones_named_here(self):
        graph, reached = functions(), set()
        for kind in ("tagpup", "tuner"):
            app, _home = web_client.app_for(self, kind)
            for rule in app.url_map.iter_rules():
                if rule.methods & WRITES:
                    view = app.view_functions[rule.endpoint]
                    if reaches(graph, (view.__module__, view.__name__), SINK):
                        reached.add(rule.rule[rule.rule.find("/api/"):])
        named = set(BATCHES) | set(SMALL)
        self.assertEqual([], sorted(reached - named), "a route reaches file_changes.write_fields: say here what bounds it")
        self.assertEqual([], sorted(named - reached), "named here, but no route reaches write_fields any more")
        self.assertEqual([], sorted(set(BATCHES) & set(SMALL)))


if __name__ == "__main__":
    unittest.main()
