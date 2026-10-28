"""Every look at a photo or a photo's folder -- a stat, an exists, an isdir, a listing -- goes through tagpup.files.shares
(owner, 2026-10-28: one wrapper, one subsystem for a share that is away). A look of its own on a network share holds its
thread for as long as Windows waits: the watcher's walk listed a folder with a bare os.scandir and a share that dropped
mid-walk held it (#2104).

A call to one of LOOKS in tagpup/ fails unless it is
- in tagpup/files/shares.py;
- in a module of OWNERS, each a module whose every look is at the app's own files, or the documented owner of a file
  operation whose look is part of it (a write, a delete, a read that follows), each with its reason;
- at a library's database by name (`library.path`, `db_path`, `self.db_path`): never a photo;
- on a line that says why it is not bounded, `# unbounded: <reason>`.
"""
import ast
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shipped_sources import ROOT  # noqa: E402

SHARES = os.path.join("tagpup", "files", "shares.py")
LOOKS = {"os.stat", "os.lstat", "os.listdir", "os.scandir", "os.walk", "os.path.exists", "os.path.lexists",
         "os.path.isdir", "os.path.isfile", "os.path.getsize", "os.path.getmtime", "os.path.getctime"}
MARKER = "# unbounded:"
#: A library's database, by the names it goes by: a look at it is never at a photo.
DATABASE = re.compile(r"^(self\.)?(\w+_)?db_path$|^(\w+\.)?library\.path$")

#: module (a path, or a folder ending in os.sep) -> why its looks are not shares'.
OWNERS = {
    os.path.join("tagpup", "store") + os.sep: "the database layer: libraries, their snapshots and locks; it may not "
                                              "import tagpup.files",
    os.path.join("tagpup", "ml") + os.sep: "model weights and the graphics card's tickets, on this PC",
    os.path.join("tagpup", "config.py"): "the home, its settings file and the data folder",
    os.path.join("tagpup", "launcher.py"): "the installed copy, its versions and its log",
    os.path.join("tagpup", "logs.py"): "the log folder",
    os.path.join("tagpup", "runtime.py"): "the data folder's libraries",
    os.path.join("tagpup", "core", "processes.py"): "/proc",
    os.path.join("tagpup", "mcp", "server.py"): "the data folder's libraries",
    os.path.join("tagpup", "files", "thumbs.py"): "the thumbnail cache, a folder of this PC's",
    os.path.join("tagpup", "web", "app.py"): "the pages' own files",
    os.path.join("tagpup", "web", "activity_routes.py"): "the log files",
    os.path.join("tagpup", "web", "libraries.py"): "the data folder's libraries",
    # The owners of a file operation: the look is part of the operation, which itself holds the thread on a share that
    # drops; the batch's first look is bounded (file_changes.states, shares.stamp_of).
    os.path.join("tagpup", "services", "file_changes.py"): "the journaled write: the file's fingerprint around its write",
    os.path.join("tagpup", "files", "metadata.py"): "the reader's stamp before ExifTool reads, and Smart Rename's free name",
    os.path.join("tagpup", "files", "names.py"): "a rename's free name, before the journaled rename",
    os.path.join("tagpup", "files", "recycle_bin.py"): "a delete: the copy to the bin and its check",
}

def _name(node):
    if isinstance(node, ast.Attribute):
        inner = _name(node.value)
        return inner + "." + node.attr if inner else ""
    return node.id if isinstance(node, ast.Name) else ""


def _owned(relative, table):
    return next((module for module in table if relative == module or (module.endswith(os.sep)
                                                                       and relative.startswith(module))), None)


def looks(relative):
    """[(line, call)] of the looks in a module of tagpup/ none of the exemptions but OWNERS' covers."""
    with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        return looks_in(handle.read())


def looks_in(source):
    """looks, of a module's text."""
    lines, found = source.splitlines(), []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or _name(node.func) not in LOOKS:
            continue
        if node.args and DATABASE.match(ast.unparse(node.args[0])):
            continue
        if MARKER in lines[node.lineno - 1]:
            continue
        found.append((node.lineno, _name(node.func)))
    return found


def modules():
    for folder, _dirs, files in os.walk(os.path.join(ROOT, "tagpup")):
        for name in files:
            if name.endswith(".py"):
                yield os.path.relpath(os.path.join(folder, name), ROOT)


class PhotoLooksHaveOneOwner(unittest.TestCase):

    def setUp(self):
        self.found = {relative: looks(relative) for relative in modules() if relative != SHARES}

    def test_no_look_at_a_photo_path_outside_shares(self):
        stray = ["%s:%d %s" % (relative, line, call) for relative, calls in sorted(self.found.items())
                 if not _owned(relative, OWNERS) for line, call in calls]
        self.assertEqual([], stray, "a look at a photo path goes through tagpup.files.shares (stamp_of, there, "
                                    "known_stamp, folder_state, listing); one that is not at a photo says why on its "
                                    "line, '%s <reason>'" % MARKER)

    def test_every_owner_still_looks(self):
        """No stale entry: each module (or folder) of OWNERS still makes a look the list excuses."""
        stale = [module for module in OWNERS
                 if not any(calls and _owned(relative, {module: None})
                            for relative, calls in self.found.items())]
        self.assertEqual([], stale)

    def test_a_look_of_its_own_is_found(self):
        """The scan sees what it is for, and lets by what it should."""
        source = ("import os\n"
                  "listing = os.scandir(current)\n"
                  "there = os.path.exists(library.path)\n"
                  "held = os.path.isfile(self.db_path)\n"
                  "stamp = os.stat(photo_path)  # unbounded: the read that follows\n"
                  "gone = not os.path.isdir(dest)\n")
        self.assertEqual([(2, "os.scandir"), (6, "os.path.isdir")], sorted(looks_in(source)))


if __name__ == "__main__":
    unittest.main()
