"""The three block editors are called from one place (a guard).

`tagpup.files.segment_edits` decides which fields go to which segment of a JPEG and in what spelling (the XMP-only rule,
the dates' spelling, Orientation following into XMP); a second caller of xmp_packet, exif_block or iptc_block would be a
second copy of those rules. One exception, named: `in_place` lays the patch's XMP padding with `xmp_packet.padding`,
until the own writer replaces ExifTool's header copy (docs/PLAN_OWN_METADATA.md, M4). Each editor's own test imports
it; the tests are not looked at.
"""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITORS = {"xmp_packet", "exif_block", "iptc_block"}
CALLER = os.path.join("tagpup", "files", "segment_edits.py")
#: file: (editor, the one name of it the file may use).
EXCEPTIONS = {os.path.join("tagpup", "files", "in_place.py"): ("xmp_packet", "padding")}


def _sources():
    for top in ("tagpup", "scripts", "tools"):
        for folder, _dirs, files in os.walk(os.path.join(ROOT, top)):
            for name in files:
                if name.endswith(".py"):
                    path = os.path.join(folder, name)
                    yield os.path.relpath(path, ROOT), path


def _editors_imported(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.rsplit(".", 1)[-1] for alias in node.names} & EDITORS
        elif isinstance(node, ast.ImportFrom):
            found |= {(node.module or "").rsplit(".", 1)[-1]} & EDITORS
            found |= {alias.name for alias in node.names} & EDITORS
    return found


def _names_used(tree, editor):
    """The names of `editor` a file uses: each attribute of a name bound to the module, and each name imported from
    it (`from tagpup.files.xmp_packet import edit` is `edit`)."""
    bound, names = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bound |= {alias.asname or alias.name for alias in node.names if alias.name.rsplit(".", 1)[-1] == editor}
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").rsplit(".", 1)[-1] == editor:
                names |= {alias.name for alias in node.names}
            bound |= {alias.asname or alias.name for alias in node.names if alias.name == editor}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in bound:
            names.add(node.attr)
    return names


class TheBlockEditorsHaveOneCaller(unittest.TestCase):

    def test_only_segment_edits_imports_an_editor_but_the_named_exception(self):
        wrong = []
        for relative, path in _sources():
            own = os.path.splitext(os.path.basename(relative))[0]
            if relative == CALLER:
                continue
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), path)
            used = _editors_imported(tree) - {own}
            allowed = EXCEPTIONS.get(relative)
            if allowed and used == {allowed[0]}:
                names = _names_used(tree, allowed[0])
                if names <= {allowed[1]}:
                    continue
                wrong.append("%s uses %s.%s" % (relative, allowed[0], sorted(names - {allowed[1]})))
            elif used:
                wrong.append("%s imports %s" % (relative, ", ".join(sorted(used))))
        self.assertEqual([], wrong, "only tagpup/files/segment_edits.py calls the block editors")

    def test_the_guard_sees_an_import(self):
        self.assertEqual({"exif_block"}, _editors_imported(ast.parse("from tagpup.files import containers, exif_block")))
        self.assertEqual({"iptc_block"}, _editors_imported(ast.parse("import tagpup.files.iptc_block as ib")))
        self.assertEqual({"xmp_packet"}, _editors_imported(ast.parse("from tagpup.files.xmp_packet import edit")))

    def test_the_exception_is_held_to_its_one_name_however_it_is_imported(self):
        for source in ("from tagpup.files import xmp_packet\nxmp_packet.padding(1)",
                       "import tagpup.files.xmp_packet as xp\nxp.padding(1)",
                       "from tagpup.files.xmp_packet import padding"):
            self.assertEqual({"padding"}, _names_used(ast.parse(source), "xmp_packet"), source)
        for source in ("from tagpup.files import xmp_packet as xp\nxp.edit(b'', {})",
                       "from tagpup.files.xmp_packet import padding, edit"):
            self.assertIn("edit", _names_used(ast.parse(source), "xmp_packet"), source)


if __name__ == "__main__":
    unittest.main()
