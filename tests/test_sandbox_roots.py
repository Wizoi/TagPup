"""A measurement sandbox runs a COPY of a library, and a copy that holds roots would reach the real photos
through the machine's map (tagpup.services.roots.place_in_sandbox; scripts/measure_identify_faces.py,
scripts/measure_suggest_folder.py; docs/ARCHITECTURE.md, "Roots and machines").

The sandbox writes its own machine_roots.json in its own TAGPUP_HOME, placing each root of the copy in an
empty folder of the sandbox -- never at the real photos, never in the real map -- and fails loudly for a
root it did not place, a place outside it, or a map that is not its own.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import own_home  # noqa: E402
import roots_library as rl  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402

WINDOWS = os.name == "nt"


def machine_at(map_path):
    return roots_service.Machine(lambda: config.machine_roots(map_path),
                                 lambda name, place: config.add_machine_root(name, place, path=map_path),
                                 lambda: map_path)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class ASandboxOfARootedLibrary(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_sandbox_")
        self.side = rl.Side(self.home, "photo_index", real=2, bulk=0, outside=0)
        self.assertIsNone(self.side.adopt().refused)
        self.real_map = config.machine_roots_path()
        with open(self.real_map, "rb") as handle:
            self.real_map_bytes = handle.read()
        self.sandbox = tempfile.mkdtemp(prefix="tagpup_sandbox_roots_")
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        # The copy, made as the scripts make it: SQLite's backup, into the sandbox's own data/.
        os.makedirs(os.path.join(self.sandbox, "data"))
        self.copy = os.path.join(self.sandbox, "data", "measured.db")
        source = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        destination = db.connect(self.copy)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        self.map_path = os.path.join(self.sandbox, config.MACHINE_ROOTS_FILE)

    def place(self, **kwargs):
        return roots_service.place_in_sandbox(Library(self.copy), self.sandbox, machine_at(self.map_path), **kwargs)

    def test_each_root_is_placed_in_an_empty_folder_of_the_sandbox(self):
        placed = self.place()
        self.assertEqual({"pictures"}, set(placed))
        place = placed["pictures"]
        self.assertTrue(paths.is_under(place, self.sandbox))
        self.assertTrue(os.path.isdir(place))
        self.assertEqual([], os.listdir(place))
        self.assertEqual({"pictures": (place,)}, config.machine_roots(self.map_path))

    def test_the_real_map_and_the_real_photos_are_not_touched_or_reached(self):
        placed = self.place()
        with open(self.real_map, "rb") as handle:
            self.assertEqual(self.real_map_bytes, handle.read(), "the real machine map was written")
        self.assertFalse(paths.is_under(placed["pictures"], self.side.pictures))
        self.assertNotEqual(paths.key(placed["pictures"]), paths.key(self.side.pictures))

    def test_the_sandbox_resolves_the_copys_rows_inside_itself_and_nowhere_else(self):
        """What the sandbox server does, with its home the sandbox: every row of the copy is a path in
        the sandbox, and the library opens (nothing to tell it a root is unplaced)."""
        self.place()
        with mock.patch.dict(os.environ, {"TAGPUP_HOME": self.sandbox}):
            self.assertIsNone(roots_service.problem(Library(self.copy)))
            conn = db.connect(db.readonly_uri(self.copy), uri=True)
            try:
                roots = store_roots.roots_for(conn)
                natives = [store_roots.from_row(conn, path) for (path,) in conn.execute("SELECT path FROM photos")]
            finally:
                conn.close()
        self.assertTrue(natives)
        self.assertEqual(self.sandbox.lower(), os.path.commonpath([self.sandbox, *natives]).lower())
        self.assertTrue(all(not paths.is_under(path, self.side.base) for path in natives))
        self.assertTrue(all(paths.is_under(path, roots.locations["pictures"][0]) for path in natives))

    def test_without_the_sandbox_map_the_copy_is_refused_in_words(self):
        """What a sandbox that forgot this would be told, not a traceback on the first photo."""
        with mock.patch.dict(os.environ, {"TAGPUP_HOME": self.sandbox}):
            self.assertIn("machine_roots.json", roots_service.problem(Library(self.copy)))

    def test_a_root_the_sandbox_did_not_place_fails_loudly(self):
        machine = machine_at(self.map_path)
        self.assertEqual(["pictures"], roots_service.unplaced(Library(self.copy), machine))
        broken = roots_service.Machine(lambda: {}, lambda name, place: False, lambda: self.map_path)
        with self.assertRaises(roots_service.SandboxError) as why:
            roots_service.place_in_sandbox(Library(self.copy), self.sandbox, broken)
        self.assertIn("pictures", str(why.exception))
        self.assertIn("does not place", str(why.exception))

    def test_a_place_outside_the_sandbox_is_refused_and_nothing_is_written(self):
        with self.assertRaises(roots_service.SandboxError) as why:
            self.place(folder_for=lambda name: self.side.pictures)
        self.assertIn("outside the sandbox", str(why.exception))
        self.assertFalse(os.path.exists(self.map_path))

    def test_a_map_that_is_not_the_sandboxs_own_is_refused(self):
        with self.assertRaises(roots_service.SandboxError) as why:
            roots_service.place_in_sandbox(Library(self.copy), self.sandbox, machine_at(self.real_map))
        self.assertIn("not inside the sandbox", str(why.exception))
        with open(self.real_map, "rb") as handle:
            self.assertEqual(self.real_map_bytes, handle.read())

    def test_it_can_be_run_again(self):
        first = self.place()
        self.assertEqual(first, self.place())

    def test_the_measurement_scripts_place_the_roots_after_they_copy(self):
        import measure_identify_faces
        import measure_suggest_folder
        placed = measure_identify_faces.place_roots(self.copy, self.sandbox)
        self.assertEqual({"pictures"}, set(placed))
        self.assertIs(measure_suggest_folder.place_roots, measure_identify_faces.place_roots)
        for script in ("measure_identify_faces.py", "measure_suggest_folder.py"):
            with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", script),
                      encoding="utf-8") as handle:
                self.assertIn("place_roots(target", handle.read(), script + " does not place the copy's roots")


class ALibraryWithNoRoots(unittest.TestCase):
    def test_it_writes_no_map_and_places_nothing(self):
        home = own_home.for_test(self, prefix="roots_sandbox_none_")
        side = rl.Side(home, "plain", real=1, bulk=0, outside=0)
        sandbox = tempfile.mkdtemp(prefix="tagpup_sandbox_roots_")
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        map_path = os.path.join(sandbox, config.MACHINE_ROOTS_FILE)
        self.assertEqual({}, roots_service.place_in_sandbox(side.library, sandbox, machine_at(map_path)))
        self.assertFalse(os.path.exists(map_path))


class TheScreenshotsFindTheirPhotosInThisCheckout(unittest.TestCase):
    def test_no_path_of_anybody_elses_machine_is_typed_into_the_script(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts",
                               "generate_screenshots.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("c:/src/", source.lower())
        self.assertIn("NEW_PHOTOS = os.path.join(PROJECT_ROOT", source)


if __name__ == "__main__":
    unittest.main()
