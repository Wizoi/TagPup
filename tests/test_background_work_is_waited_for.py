"""Every module that starts background work is waited for by the update's drain, or says why it need not be
(docs/INVARIANTS.md; Integration-and-apps review 2026-10-09, C14).

`tagpup.web.lifecycle.long_work` is the list a drain refuses to end the server beside: a Suggest run, an index, a bulk edit, the
naming of faces, an assignment of faces, the startup migrations. It was a hand list, and a new job joined it only if somebody
remembered. A module under tagpup/jobs or tagpup/services that starts a thread, or has a `running()` of its own, fails here until
`long_work` asks it, or it is listed below with the reason a drain need not wait for it.
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.web import lifecycle  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Modules with a thread or a `running()` that long_work does not ask, each with why.
NOT_WAITED_FOR = {
    "tagpup.jobs.recurring": "the recurring jobs and the idle release run on tagpup.runtime.Background, which a drain stops and waits for (Lifecycle.busy)",
    "tagpup.jobs.watching": "the folder watcher is one of Background's tasks, stopped by the drain the same way",
    "tagpup.jobs.verifying": "a Verify only reads the files and the library; ending the server loses nothing",
    "tagpup.services.file_access": "short probe threads of a read (is a share there), each with a deadline; they write nothing",
}


def modules():
    for folder in ("jobs", "services"):
        for name in sorted(os.listdir(os.path.join(ROOT, "tagpup", folder))):
            if name.endswith(".py") and name != "__init__.py":
                yield "tagpup.%s.%s" % (folder, name[:-3]), os.path.join(ROOT, "tagpup", folder, name)


def starts_background_work(path):
    """Does the module start a thread, or define a module-level `running`?"""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "Thread":
            return True
    return any(isinstance(node, ast.FunctionDef) and node.name == "running" for node in tree.body)


def asked_by_long_work():
    """The modules `lifecycle.long_work` calls something of (`running`, `bringing_up_to_date`), by their full names."""
    with open(lifecycle.__file__, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for each in node.names:
                aliases[each.asname or each.name] = "%s.%s" % (node.module, each.name)
    work = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "long_work")
    return {aliases[call.func.value.id] for call in ast.walk(work)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name)
            and call.func.value.id in aliases}


class EveryJobIsWaitedFor(unittest.TestCase):
    def test_each_module_that_starts_work_is_asked_by_long_work_or_listed_with_a_reason(self):
        asked = asked_by_long_work()
        found = {name for name, path in modules() if starts_background_work(path)}
        self.assertEqual([], sorted(found - asked - set(NOT_WAITED_FOR)),
                         "add the module's running() to lifecycle.long_work, or to NOT_WAITED_FOR with the reason")

    def test_the_list_holds_no_module_that_is_gone_or_asked_already(self):
        found = {name for name, path in modules() if starts_background_work(path)}
        self.assertEqual([], sorted(set(NOT_WAITED_FOR) - found), "listed, but it starts nothing")
        self.assertEqual([], sorted(set(NOT_WAITED_FOR) & asked_by_long_work()), "listed, but long_work asks it")
        self.assertTrue(all(NOT_WAITED_FOR.values()))

    def test_the_scan_sees_the_jobs_long_work_names_today(self):
        asked = asked_by_long_work()
        for name in ("tagpup.jobs.suggestions", "tagpup.jobs.indexing", "tagpup.jobs.bulk_edits", "tagpup.jobs.naming_faces",
                     "tagpup.jobs.face_assignments", "tagpup.services.libraries"):
            self.assertIn(name, asked)


if __name__ == "__main__":
    unittest.main()
