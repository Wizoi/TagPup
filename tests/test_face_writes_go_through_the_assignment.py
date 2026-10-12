"""A write that names, unnames or rules out faces of many photos goes through the one path (docs/findings.md, #892, #893, #894; People
owner's charter, "A bulk path that names or unnames a face settles the photo keyword").

The bulk paths that wrote faces without touching the photo's keywords (Re-examine, Identify Faces' match-bulk and unmatch-bulk, Ignore
cluster) each re-created the drift between a face and its photo's person tag. Now they are one batch (tagpup.services.face_assignment),
whose photos' faces are written in the transaction that marks each file done. So no other module of the services or the jobs may call a
function that names, unnames, rules out or restores faces: a new one is either that path, or named here with why it is safe. The scan
reads the code: a call of a function of tagpup.store.faces or tagpup.services.faces, by the name the module imported it under.
"""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The functions of the faces store and the faces service that write who a face is.
WRITERS = {
    "tagpup.store.faces": {"name", "name_if_unnamed", "name_unnamed", "set_names", "unname", "unname_photo", "unname_person", "exclude",
                           "restore", "confirm", "revert_automatic", "clear_automatic_names", "put_decisions", "delete"},
    "tagpup.services.faces": {"name_face", "unname_face", "unname_faces", "unname_photo", "exclude", "restore", "automatch_photo",
                              "automatch_folder", "clear_automatic_names", "remove_folder"},
}

#: The modules allowed to call them, and why they are safe.
ALLOWED = {
    "tagpup/services/faces.py": "the faces service itself: the writers",
    "tagpup/services/face_people.py": "one face (or one photo's) with the photo's tag, the tag first or the face first as its docstring says",
    "tagpup/services/face_assignment.py": "the bulk door: the faces that carry no name write no photo (Unmatch, Ignore), the rest are the batch's",
    "tagpup/services/duplicate_faces.py": "removes the second row of a face detected twice, a journaled change of rows",
    "tagpup/services/identities.py": "clustering gives automatic names, which are guesses and not tags",
}


def bound(tree):
    """{local name: module} of the faces store and service as this module imported them."""
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for each in node.names:
                if node.module + "." + each.name in WRITERS:
                    found[each.asname or each.name] = node.module + "." + each.name
    return found


def callers():
    """{relative path: {(module, function)}} of every call of a WRITERS function in tagpup/services and tagpup/jobs."""
    found = {}
    for folder in ("services", "jobs"):
        for name in sorted(os.listdir(os.path.join(ROOT, "tagpup", folder))):
            if not name.endswith(".py"):
                continue
            relative = "tagpup/%s/%s" % (folder, name)
            with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            local = bound(tree)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                        and local.get(node.func.value.id) and node.func.attr in WRITERS[local[node.func.value.id]]):
                    found.setdefault(relative, set()).add((local[node.func.value.id], node.func.attr))
    return found


class OnlyTheAssignmentWritesWho(unittest.TestCase):
    def test_no_other_service_or_job_calls_a_function_that_names_unnames_or_rules_out_faces(self):
        outside = {path: sorted(calls) for path, calls in callers().items() if path not in ALLOWED}
        self.assertEqual({}, outside, "name the module in ALLOWED with why it is safe, or make the write the batch "
                                      "(tagpup.services.face_assignment): a face and its photo's tag must be written together")

    def test_the_allowed_are_real_callers(self):
        found = callers()
        self.assertEqual([], sorted(set(ALLOWED) - set(found)), "allowed, but it calls none of them")

    def test_the_scan_sees_the_bulk_path(self):
        self.assertIn(("tagpup.services.faces", "exclude"), callers()["tagpup/services/face_assignment.py"])


if __name__ == "__main__":
    unittest.main()
