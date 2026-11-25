"""Every kind of change the code makes says what its way back is (tagpup.services.journal.UNDO_CHECKS), and every undo
asks it (#1805). The rule that a way back is refused where the way forward would be had three owners, each listing the
changes it knew; a kind none listed was offered and undone unasked. Now one table names every operation, and this reads
the source to hold it there:

- every operation name passed to a journal writer in tagpup/, scripts/ and the entry points -- any function with an `operation` parameter, journal.apply,
  record, record_faces, file_changes.write_fields and rename, maintenance.run and the rest -- is a key of UNDO_CHECKS, the
  name resolved from a literal, a `format % key` (its format), either arm of an if-expression, a local, a module constant
  or one imported, or every value of a table it is looked up in; a parameter `operation` passed on is followed to every
  caller of its function instead, its default included; a function whose `operation` is not a change's name (NOT_NAMES)
  is not followed;
- every key of UNDO_CHECKS is made by the code or is in RETIRED (a kind only the live journals hold);
- every kind the code makes is a row of the undo matrix or left out of it with its reason (tests/undo_kinds.py,
  scenario D2; #2748), a reason saying the kind writes photo files held to a writer that reaches
  file_changes.write_fields or rename, and one saying it writes rows alone to a writer that reaches neither;
- nothing but tagpup/services/journal.py calls the store's undo or rehearse_undo, or file_changes' undo or
  rehearse_undo, from another module: each undo goes through why_not first (file_changes.undo rehearses by its own
  rehearse_undo with the check it was given).
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shipped_sources import ROOT  # noqa: E402

sys.path.insert(0, ROOT)

from tagpup.services import journal as journal_service  # noqa: E402
import undo_kinds  # noqa: E402

PARAMETER = "operation"
#: (module, function) whose `operation` is not the name of a change: the write itself (a callable), the reader of the
#: table, and a batch's label in the line of batches (the change it holds is named to its writer).
NOT_NAMES = {("tagpup.store.db", "write"), ("tagpup.store.db", "write_with_connection"), ("tagpup.store.db", "write_if_free"),
             ("tagpup.store.db", "read_with_connection"),
             ("tagpup.services.journal", "undo_rule"), ("tagpup.services.batches", "claim"),
             ("tagpup.services.file_changes", "batch_exclusively"),
             # A read of the changes made as `operation`: the face pass's undo asks it of a retired kind (pass_check).
             ("tagpup.store.journal", "kept_named")}
#: What is scanned besides tagpup/: the programs, the tools and the entry points (index --reset stamps its settings in
#: tagpup_cli).
PROGRAMS = ("scripts", "tools", "tagpup_cli.py", "tagpup_web.py")
#: The writers of a change of photo files: a function passing its `operation` on to one is one too (_Source.file_writers).
FILE_WRITERS = {("tagpup.services.file_changes", "write_fields"), ("tagpup.services.file_changes", "rename")}
#: The one module that may call an undo of the store or of the files: it asks why_not before each.
UNDOER = "tagpup.services.journal"
#: (module, function) of the undos that write without asking why_not.
UNDOS = {("tagpup.store.journal", "undo"), ("tagpup.store.journal", "rehearse_undo"),
         ("tagpup.services.file_changes", "undo"), ("tagpup.services.file_changes", "rehearse_undo")}


class Unresolved(Exception):
    pass


class _Module:
    def __init__(self, name, tree):
        self.name, self.tree = name, tree
        self.constants = {}   # NAME -> [expr]
        self.imports = {}     # local name -> ("module", dotted) or ("name", dotted module, name)
        self.functions = {}   # name -> FunctionDef (top level)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.constants.setdefault(target.id, []).append(node.value)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[node.name] = node
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("tagpup"):
                for alias in node.names:
                    self.imports[alias.asname or alias.name] = ("from", node.module, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("tagpup"):
                        self.imports[alias.asname or alias.name] = ("module", alias.name)


def _files():
    for top in ("tagpup",) + PROGRAMS:
        if top.endswith(".py"):
            yield os.path.join(ROOT, top)
            continue
        for folder, dirs, names in os.walk(os.path.join(ROOT, top)):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            yield from (os.path.join(folder, name) for name in sorted(names) if name.endswith(".py"))


def _modules():
    found = {}
    for path in _files():
        dotted = os.path.relpath(path, ROOT)[:-3].replace(os.sep, ".")
        if dotted.endswith(".__init__"):
            dotted = dotted[:-len(".__init__")]
        with open(path, encoding="utf-8") as source:
            found[dotted] = _Module(dotted, ast.parse(source.read(), path))
    return found


class _Source:
    """tagpup/ read once: every function with an `operation` parameter, every call of one, and the names they pass."""

    def __init__(self):
        self.modules = _modules()

    def module_of(self, module, local):
        """The dotted module `local` (a name in `module`) is, or None."""
        found = module.imports.get(local)
        if found is None:
            return None
        dotted = found[1] if found[0] == "module" else "%s.%s" % (found[1], found[2])
        return dotted if dotted in self.modules else None

    def callee(self, module, func):
        """(module, function name) a call's `func` names, a function of tagpup/; or None."""
        if isinstance(func, ast.Name):
            if func.id in module.functions:
                return module.name, func.id
            found = module.imports.get(func.id)
            if found and found[0] == "from" and found[1] in self.modules:
                return found[1], found[2]
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            dotted = self.module_of(module, func.value.id)
            if dotted is not None:
                return dotted, func.attr
        return None

    def takes_operation(self, dotted, name):
        """The position of the `operation` parameter of function `name` of `dotted`, or None."""
        function = self.modules[dotted].functions.get(name) if dotted in self.modules else None
        if function is None:
            return None
        names = [arg.arg for arg in function.args.posonlyargs + function.args.args]
        if PARAMETER in names:
            return names.index(PARAMETER)
        return -1 if PARAMETER in [arg.arg for arg in function.args.kwonlyargs] else None

    def constant(self, dotted, name, seen):
        module = self.modules.get(dotted)
        if module is None:
            raise Unresolved("%s.%s" % (dotted, name))
        if name in module.constants:
            return set().union(*(self.resolve(module, None, value, seen) for value in module.constants[name]))
        found = module.imports.get(name)
        if found and found[0] == "from":
            return self.constant(found[1], found[2], seen)
        raise Unresolved("%s.%s" % (dotted, name))

    def resolve(self, module, function, node, seen=()):
        """The operation names `node`, an expression in `function` of `module`, can be. A parameter passed on is
        followed by its callers (calls), not here: it resolves to nothing."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            return self.resolve(module, function, node.left, seen)
        if isinstance(node, ast.IfExp):
            return self.resolve(module, function, node.body, seen) | self.resolve(module, function, node.orelse, seen)
        if isinstance(node, ast.Subscript):
            return self.values_of(module, function, node.value, seen)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            dotted = self.module_of(module, node.value.id)
            if dotted is not None:
                return self.constant(dotted, node.attr, seen)
        if isinstance(node, ast.Name):
            if function is not None:
                if node.id == PARAMETER and node.id in [a.arg for a in ast.walk(function.args) if isinstance(a, ast.arg)]:
                    return set()   # passed on: its function's callers are scanned
                local = [n.value for n in ast.walk(function) if isinstance(n, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == node.id for t in n.targets)]
                if local:
                    return set().union(*(self.resolve(module, function, value, seen) for value in local))
            if (module.name, node.id) in seen:
                return set()
            return self.constant(module.name, node.id, seen + ((module.name, node.id),))
        raise Unresolved("%s: %s" % (module.name, ast.unparse(node)))

    def values_of(self, module, function, node, seen):
        """Every value of the table `node` names (a dict literal, or a constant holding one)."""
        if isinstance(node, ast.Dict):
            return set().union(*(self.resolve(module, function, value, seen) for value in node.values))
        tables = []
        if isinstance(node, ast.Name):
            tables = module.constants.get(node.id, [])
            if not tables and node.id in module.imports and module.imports[node.id][0] == "from":
                _kind, dotted, name = module.imports[node.id]
                tables = self.modules[dotted].constants.get(name, [])
                module = self.modules[dotted]
        if not tables or not all(isinstance(table, ast.Dict) for table in tables):
            raise Unresolved("%s: %s" % (module.name, ast.unparse(node)))
        return set().union(*(self.values_of(module, None, table, seen) for table in tables))

    def _functions_in(self, module):
        """(enclosing top-level or nested function or None, node) of every node of `module`."""
        def walk(node, function):
            for child in ast.iter_child_nodes(node):
                inner = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
                yield inner, child
                yield from walk(child, inner)
        yield from walk(module.tree, None)

    def made(self):
        """{operation name: [where]} of every name passed to a function with an `operation` parameter, and every
        default of one; and [where: what] could not be resolved. Kept: `callees` ({operation name: {(module, function)
        it is passed to, or the default of}}) and `passed_on` ({(module, function): {(module, function) its own
        `operation` is passed to}})."""
        names, unresolved = {}, []
        self.callees, self.passed_on = {}, {}

        def add(module, function, node, where, callee):
            if (isinstance(node, ast.Name) and node.id == PARAMETER and function is not None
                    and self.takes_operation(module.name, function.name) is not None):
                self.passed_on.setdefault((module.name, function.name), set()).add(callee)
            try:
                for name in self.resolve(module, function, node):
                    names.setdefault(name, []).append(where)
                    self.callees.setdefault(name, set()).add(callee)
            except Unresolved as problem:
                unresolved.append("%s: %s" % (where, problem))

        for module in self.modules.values():
            for function, node in self._functions_in(module):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = node.args
                    positional = args.posonlyargs + args.args
                    defaults = dict(zip([a.arg for a in positional[len(positional) - len(args.defaults):]], args.defaults))
                    defaults.update({a.arg: d for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None})
                    if PARAMETER in defaults:
                        add(module, None, defaults[PARAMETER], "%s.%s's default" % (module.name, node.name),
                            (module.name, node.name))
                    continue
                if not isinstance(node, ast.Call):
                    continue
                callee = self.callee(module, node.func)
                if callee is None or callee in NOT_NAMES:
                    continue
                position = self.takes_operation(*callee)
                if position is None:
                    continue
                where = "%s:%d %s.%s" % (module.name, node.lineno, *callee)
                passed = [kw.value for kw in node.keywords if kw.arg == PARAMETER]
                if not passed and 0 <= position < len(node.args):
                    if any(isinstance(a, ast.Starred) for a in node.args[:position + 1]):
                        unresolved.append("%s: a starred argument" % where)
                        continue
                    passed = [node.args[position]]
                for value in passed:
                    add(module, function, value, where, callee)
        return names, unresolved

    def file_writers(self):
        """{(module, function)} of FILE_WRITERS and every function passing its own `operation` on to one (made first)."""
        found = set(FILE_WRITERS)
        while True:
            more = {caller for caller, callees in self.passed_on.items() if callees & found} - found
            if not more:
                return found
            found |= more

    def undo_callers(self):
        """[where] of each call of an undo that does not ask why_not (UNDOS) outside UNDOER and the undo's own module."""
        found = []
        for module in self.modules.values():
            if module.name == UNDOER:
                continue
            for node in ast.walk(module.tree):
                callee = self.callee(module, node.func) if isinstance(node, ast.Call) else None
                if callee in UNDOS and callee[0] != module.name:
                    found.append("%s:%d %s" % (module.name, node.lineno, ast.unparse(node.func)))
        return found


class EveryChangeKindHasAnUndoCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _Source()
        cls.made, cls.unresolved = cls.source.made()

    def test_every_operation_name_the_code_passes_is_resolved(self):
        self.assertEqual([], self.unresolved, "an operation name this cannot follow: name it by a constant or a literal")

    def test_every_kind_of_change_the_code_makes_says_what_its_way_back_is(self):
        missing = {name: where for name, where in self.made.items() if name not in journal_service.UNDO_CHECKS}
        self.assertEqual({}, missing, "add each to tagpup.services.journal.UNDO_CHECKS: a sentence, a check, or None")

    def test_every_kind_the_table_names_is_made_by_the_code_or_held_by_a_live_journal(self):
        stale = sorted(set(journal_service.UNDO_CHECKS) - set(self.made) - set(journal_service.RETIRED))
        self.assertEqual([], stale, "no code makes these: delete them, or move them to RETIRED with the live counts")
        made_again = sorted(set(journal_service.RETIRED) & set(self.made))
        self.assertEqual([], made_again, "the code makes these again: they are not retired")

    def test_every_kind_the_code_makes_is_a_row_of_the_undo_matrix_or_left_with_its_reason(self):
        """A kind of change added to the journal fails here, in the fast tier, until it is a row of the undo matrix
        (undo_kinds.OPERATIONS) or left out saying why (undo_kinds.LEFT) (#2748); a reason is held to what the kind's
        writer does: a write of photo files reaches file_changes, a change of rows alone does not."""
        operations, left = undo_kinds.OPERATIONS, undo_kinds.LEFT
        made = set(journal_service.UNDO_CHECKS) - set(journal_service.RETIRED)
        self.assertEqual(set(), set(operations) & set(left), "a kind both a row of the matrix and left out")
        self.assertEqual(made, (set(operations) - set(journal_service.RETIRED)) | set(left),
                         "a kind the code makes is neither a row of the undo matrix nor left with its reason "
                         "(tests/undo_kinds.py), or a row or a reason names a kind the code does not make")
        self.assertEqual(set(), set(operations) - set(journal_service.UNDO_CHECKS), "a row of a kind UNDO_CHECKS lacks")
        rows = [row for each in operations.values() for row in each]
        self.assertEqual(sorted(set(rows)), sorted(rows), "a row of the matrix named for two kinds")
        writers = self.source.file_writers()
        said = {kind: why in undo_kinds.WRITES_FILES for kind, why in left.items()
                if why in undo_kinds.WRITES_FILES | undo_kinds.ROWS_ALONE}
        does = {kind: bool(self.source.callees.get(kind, set()) & writers) for kind in said}
        self.assertEqual(said, does, "a reason says the kind writes photo files (or not) and its writer does not")
        self.assertEqual({}, {kind: journal_service.UNDO_CHECKS[kind] for kind in said
                              if journal_service.UNDO_CHECKS[kind] is not None},
                         "a kind left as the journal's alone has a way back of its own")

    def test_the_writers_are_found(self):
        # The scan finds the writers it exists for: were it to find none, it would pass with nothing checked.
        for name in ("change settings", "rotate", "retire vectors (%s)", "migration %d: %s",
                     "stamp settings with the defaults", "stamp settings from the library it replaced", "sync"):
            self.assertIn(name, self.made)

    def test_only_the_journal_service_calls_an_undo_and_so_asks_why_not_first(self):
        self.assertEqual([], self.source.undo_callers(),
                         "undo through tagpup.services.journal (undo, rehearse, undo_rows), which asks why_not first")

    def test_a_retiring_of_the_old_detectors_faces_a_live_journal_holds_is_never_undone(self):
        # Each live library holds one (photo_index 3247, counted 2026-10-25): undone, it would put the old model's faces
        # back (#1783). Said before the journal's account, as a stamp's.
        rule = journal_service.undo_rule("retire faces (scrfd-adaface)")
        self.assertIsInstance(rule, str)
        self.assertIn("are not put back", rule)


if __name__ == "__main__":
    unittest.main()
