"""A person Suggest names is filed, or left out, as a click on their chip does: one table for both.

The page's rule is resolveTagOrPerson and photoAlreadyHas (web/tagpup/tags.js, web/common/vocabulary.js); the
server's is tagpup.core.vocabulary.person_tag and same_person, expressed once and used by Apply All (held and
not held). tests/fixtures/person_filing.json is the table both are fed -- here Apply All of a real photo with real
ExifTool, in tests/frontend/person-filing-table.test.mjs a click on the chip -- so they cannot drift (#555).
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import damaged_photos  # noqa: E402
import library_dump  # noqa: E402
import own_home  # noqa: E402

from tagpup.core import fields  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import tagging  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import taxonomy as store_taxonomy  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
TABLE = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "person_filing.json"),
                       encoding="utf-8"))


def tags_in(path):
    with ExifToolSession(executable=EXIFTOOL) as et:
        return fields.field_values(et.get_tags([path], tags=["XMP:Subject"])[0].get("XMP:Subject"))


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class ApplyAllFilesAPersonAsTheChipDoes(unittest.TestCase):
    def library_with(self, number, case):
        path = self.home.library("case%d.db" % number)
        library_actions.create(path)
        conn = db.connect(path)
        try:
            conn.execute("DELETE FROM tag_taxonomy")
            for name, has_face in case["roots"]:
                store_taxonomy.add_node(conn, name, root_has_face=has_face)
            for node in case["paths"]:
                store_taxonomy.add_node(conn, node)
            conn.commit()
        finally:
            conn.close()
        return Library(path)

    @staticmethod
    def tree(library):
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            return sorted(store_taxonomy.tags(conn))
        finally:
            conn.close()

    def test_the_table_for_a_folder_the_library_does_not_hold(self):
        self.run_table(held=False)

    def test_the_table_for_a_folder_it_holds(self):
        self.run_table(held=True)

    def run_table(self, held):
        self.home = own_home.for_test(self)
        for number, case in enumerate(TABLE["cases"]):
            with self.subTest(case["why"]):
                library = self.library_with(number, case)
                photo = os.path.join(self.home.root, "Share", "Case %d" % number, "wren.jpg")
                damaged_photos.whole_jpeg(photo, seed=number + 1)
                if held:
                    library_actions.record_added(library, [os.path.dirname(photo)])
                if case["file"]:
                    with ExifToolSession(executable=EXIFTOOL) as et:
                        et.set_tags([photo], tags={"XMP:Subject": case["file"]}, params=["-overwrite_original"])
                before = library_dump.dump(library.path)
                self.tree_before = self.tree(library)
                entry = {"tags": [], "people": [{"name": TABLE["name"], "score": 0.9}]}
                result = tagging.apply_suggestions(library, {photo: entry}, EXIFTOOL, 0.0)
                self.assertFalse(result.refused, result.refused)
                wanted = {"already": case["file"], "ask": case["file"] + [TABLE["name"]]}.get(
                    case["expect"], case["file"] + [case["expect"]])
                self.assertEqual(wanted, tags_in(photo))
                if not held:   # a held write has its journal and rows; the tree is the same either way
                    self.assertEqual(before, library_dump.dump(library.path), "the library was changed")
                self.assertEqual(self.tree(library), self.tree_before)


if __name__ == "__main__":
    unittest.main()
