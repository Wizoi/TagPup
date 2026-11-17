"""The three block editors are called from one place (a guard).

`tagpup.files.segment_edits` decides which fields go to which segment of a JPEG and in what spelling (the XMP-only rule,
the dates' spelling, Orientation following into XMP); a second caller of xmp_packet, exif_block or iptc_block would be a
second copy of those rules. `in_place` was the one exception, laying the XMP padding of ExifTool's header copy with
`xmp_packet.padding`, until the own writer replaced that copy (docs/PLAN_OWN_METADATA.md, 4d). Each editor's own test
imports it; the tests are not looked at.
"""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITORS = {"xmp_packet", "exif_block", "iptc_block"}
CALLER = os.path.join("tagpup", "files", "segment_edits.py")


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


class TheBlockEditorsHaveOneCaller(unittest.TestCase):

    def test_only_segment_edits_imports_an_editor(self):
        wrong = []
        for relative, path in _sources():
            own = os.path.splitext(os.path.basename(relative))[0]
            if relative == CALLER:
                continue
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), path)
            used = _editors_imported(tree) - {own}
            if used:
                wrong.append("%s imports %s" % (relative, ", ".join(sorted(used))))
        self.assertEqual([], wrong, "only tagpup/files/segment_edits.py calls the block editors")

    def test_the_guard_sees_an_import(self):
        self.assertEqual({"exif_block"}, _editors_imported(ast.parse("from tagpup.files import containers, exif_block")))
        self.assertEqual({"iptc_block"}, _editors_imported(ast.parse("import tagpup.files.iptc_block as ib")))
        self.assertEqual({"xmp_packet"}, _editors_imported(ast.parse("from tagpup.files.xmp_packet import edit")))


if __name__ == "__main__":
    unittest.main()
