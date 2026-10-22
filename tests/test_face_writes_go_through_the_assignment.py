"""A write that names, unnames or rules out faces of many photos goes through the one path (docs/findings.md, #892, #893, #894; People
owner's charter, "A bulk path that names or unnames a face settles the photo keyword").

The bulk paths that wrote faces without touching the photo's keywords (Re-examine, Identify Faces' match-bulk and unmatch-bulk, Ignore
cluster) each re-created the drift between a face and its photo's person tag. Now they are one batch (tagpup.services.face_assignment),
whose photos' faces are written in the transaction that marks each file done. So no other module of the services, the jobs or the web
may call a function that names, unnames, rules out or restores faces: a new one is either that path, or named here with why it is safe.

The scan reads the code, in every way a module can reach a function of tagpup.store.faces or tagpup.services.faces: the module
imported under a name (`from tagpup.store import faces`, `import tagpup.store.faces as f`), the dotted path called whole
(`tagpup.store.faces.unname(...)`) and the function imported by name (`from tagpup.store.faces import unname`).
"""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The functions of the faces store and the faces service that write who a face is.
WRITERS = {
    "tagpup.store.faces": {"name", "name_if_unnamed", "name_unnamed", "set_names", "unname", "unname_photo", "unname_person", "exclude",
                           "restore", "confirm", "revert_automatic", "put_decisions", "delete"},
    "tagpup.services.faces": {"name_face", "unname_face", "unname_faces", "unname_photo", "exclude", "restore", "automatch_photo",
                              "automatch_folder", "remove_folder"},
}

#: The modules allowed to call them, and why they are safe. Only the first three are the path; the rest are named exceptions that
#: write faces for a reason that is not "the owner chose a person for these faces".
ALLOWED = {
    "tagpup/services/faces.py": "the faces service itself: the writers",
    "tagpup/services/face_people.py": "one face (or one photo's) with the photo's tag, the tag first or the face first as its docstring says",
    "tagpup/services/face_assignment.py": "the bulk door: the faces that carry no name write no photo (Unmatch, Ignore), the rest are the batch's",
    "tagpup/services/duplicate_faces.py": "removes the second row of a face detected twice: a journaled change of rows",
    "tagpup/services/identities.py": "clustering gives automatic names, which are guesses with no decider and no tag",
    "tagpup/web/tuner_routes.py": "Restore: a ruled-out face comes back unnamed and unclaimed, which writes no tag",
    "tagpup/store/face_batches.py": "the batch's own follower: faces written in the transaction that marks the photo's file done",
    "tagpup/store/face_tags.py": "the one-face rule: a photo's keyword just recorded names its only unnamed face as a GUESS (no decider)",
    "tagpup/store/taxonomy.py": "deleting a person with force unnames their faces, journaled (PERSON_DELETED); their tag goes with the node",
    "tagpup/services/retire_faces.py": ("the old detector's faces deleted and every kept face's decision cleared, journaled; no photo's "
                                        "keyword moves, by the owner's decision (2026-10-22): the keywords are the record kept"),
}

#: Writers that reach faces another way and are not scanned: the naming job (Name faces from tags), the indexer, and the MCP server's
#: merge_duplicate_person_tags / dedupe_faces write through the journal's rows (tagpup.store.journal) with the tags they were read from.

#: What is read for each module of the scan: the dotted names it binds to the modules of WRITERS.
FOLDERS = ("services", "jobs", "web", "store", "mcp", "")


def bindings(tree):
    """({local name: module of WRITERS}, {local name: (module, function)}) a module imports."""
    modules, functions = {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for each in node.names:
                if each.name in WRITERS:
                    modules[each.asname or each.name] = each.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for each in node.names:
                if node.module + "." + each.name in WRITERS:
                    modules[each.asname or each.name] = node.module + "." + each.name
                elif node.module in WRITERS and each.name in WRITERS[node.module]:
                    functions[each.asname or each.name] = (node.module, each.name)
    return modules, functions


def dotted(node):
    """'a.b.c' for a chain of attributes ending in a name, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return ".".join([node.id] + parts[::-1])
    return None


def writes_in(source):
    """{(module, function)} of the calls in `source` that write who a face is."""
    tree = ast.parse(source)
    modules, functions = bindings(tree)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in functions:
            found.add(functions[node.func.id])
        elif isinstance(node.func, ast.Attribute):
            owner = dotted(node.func.value)
            if owner is None:
                continue
            module = modules.get(owner) or (owner if owner in WRITERS else None)
            if module and node.func.attr in WRITERS[module]:
                found.add((module, node.func.attr))
    return found


def callers():
    """{relative path: {(module, function)}} of every module of the scan that calls a writer."""
    found = {}
    for folder in FOLDERS:
        for name in sorted(os.listdir(os.path.join(ROOT, "tagpup", folder))):
            if name.endswith(".py"):
                relative = "tagpup/%s%s" % (folder + "/" if folder else "", name)
                with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                    calls = writes_in(handle.read())
                if calls:
                    found[relative] = calls
    return found


class OnlyTheAssignmentWritesWho(unittest.TestCase):
    def test_no_other_service_job_or_route_calls_a_function_that_names_unnames_or_rules_out_faces(self):
        outside = {path: sorted(calls) for path, calls in callers().items() if path not in ALLOWED}
        self.assertEqual({}, outside, "name the module in ALLOWED with why it is safe, or make the write the batch "
                                      "(tagpup.services.face_assignment): a face and its photo's tag must be written together")

    def test_the_allowed_are_real_callers(self):
        self.assertEqual([], sorted(set(ALLOWED) - set(callers())), "allowed, but it calls none of them")

    def test_the_scan_sees_the_bulk_path(self):
        self.assertIn(("tagpup.services.faces", "exclude"), callers()["tagpup/services/face_assignment.py"])

    def test_the_scan_sees_every_way_of_reaching_a_writer(self):
        ways = {
            "from tagpup.store import faces\nfaces.unname(conn, [1])": ("tagpup.store.faces", "unname"),
            "from tagpup.store import faces as f\nf.exclude(conn, [1], 'x')": ("tagpup.store.faces", "exclude"),
            "import tagpup.store.faces\ntagpup.store.faces.name(conn, [1], 2)": ("tagpup.store.faces", "name"),
            "import tagpup.store.faces as f\nf.restore(conn, [1])": ("tagpup.store.faces", "restore"),
            "from tagpup.store.faces import unname\nunname(conn, [1])": ("tagpup.store.faces", "unname"),
            "from tagpup.store.faces import unname as nobody\nnobody(conn, [1])": ("tagpup.store.faces", "unname"),
            "from tagpup.services import faces as faces_service\nfaces_service.name_face(lib, 1, 'x')":
                ("tagpup.services.faces", "name_face"),
            "import tagpup.services.faces\ntagpup.services.faces.unname_faces(lib, [1])": ("tagpup.services.faces", "unname_faces"),
        }
        for source, expected in ways.items():
            self.assertEqual({expected}, writes_in(source), source)
        self.assertEqual(set(), writes_in("from tagpup.store import faces\nfaces.rows(conn, [1])"), "a read is no write")


if __name__ == "__main__":
    unittest.main()
