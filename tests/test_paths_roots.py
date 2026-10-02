"""tagpup/core/paths.py's roots, and tagpup/config.py's machine map: native in memory,
root-relative in the database (docs/ARCHITECTURE.md, "Roots and machines").

The shapes are the owner's: a UNC share root, a drive-letter root, and one root --
`pictures`, the share's own address \\\\idziserver\\Pictures\\Pictures -- kept at one place
on the desktop and another on the server. Names are fictional.
"""
import json
import os
import random
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
from tagpup import config  # noqa: E402
from tagpup.core import paths, processes  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import db  # noqa: E402

WINDOWS = os.name == "nt"
LOGICAL = "\\\\idziserver\\Pictures\\Pictures"
DESKTOP = "D:\\Training\\Pictures"
SERVER = "D:\\ServerFolders\\Pictures\\Pictures"


def desktop():
    return paths.Roots.of({"pictures": LOGICAL}, {"pictures": [DESKTOP]})


def server():
    return paths.Roots.of({"pictures": LOGICAL}, {"pictures": [SERVER]})


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class NothingChangesWithoutRoots(unittest.TestCase):
    def test_functions_are_the_old_ones_given_none(self):
        for roots in (None, paths.Roots(), paths.Roots.of({}, {"unused": ["E:\\x"]})):
            self.assertEqual(paths.to_row("D:/Pictures/a/../b.JPG", roots), "D:\\Pictures\\b.JPG")
            self.assertEqual(paths.from_row("D:\\Pictures\\b.JPG", roots), "D:\\Pictures\\b.JPG")
            self.assertEqual(paths.sql_equals("path", "D:/P/a.jpg", roots), paths.sql_equals("path", "D:/P/a.jpg"))
            self.assertEqual(paths.sql_under("path", "D:/P", roots), paths.sql_under("path", "D:/P"))
            self.assertEqual(paths.sql_in("path", "D:/P", roots), paths.sql_in("path", "D:/P"))
        self.assertEqual(paths.to_row("", None), "")
        self.assertEqual(paths.from_row("", None), "")

    def test_a_root_row_without_roots_is_refused_not_passed_on(self):
        with self.assertRaises(paths.UnknownRoot):
            paths.from_row("@pictures/a.jpg", None)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheRowForm(unittest.TestCase):
    def test_under_a_root(self):
        self.assertEqual(paths.to_row(DESKTOP + "\\2024\\Trip\\IMG_01.JPG", desktop()),
                         "@pictures/2024/Trip/IMG_01.JPG")

    def test_the_root_itself_has_no_separator_and_no_relative_part(self):
        self.assertEqual(paths.to_row(DESKTOP, desktop()), "@pictures")
        self.assertEqual(paths.to_row(DESKTOP + "\\", desktop()), "@pictures")
        self.assertEqual(paths.from_row("@pictures", desktop()), DESKTOP)

    def test_the_same_row_on_each_machine_and_a_new_native_path_from_it(self):
        row = paths.to_row(DESKTOP + "\\2024\\a.jpg", desktop())
        self.assertEqual(paths.to_row(SERVER + "\\2024\\a.jpg", server()), row)
        self.assertEqual(paths.from_row(row, server()), SERVER + "\\2024\\a.jpg")
        self.assertEqual(paths.from_row(row, desktop()), DESKTOP + "\\2024\\a.jpg")

    def test_changing_the_map_changes_native_and_touches_no_stored_value(self):
        stored_rows = [paths.to_row(DESKTOP + "\\" + name, desktop()) for name in ("a.jpg", "x\\b.jpg")]
        before = list(stored_rows)
        moved = paths.Roots.of({"pictures": LOGICAL}, {"pictures": ["E:\\Moved\\Pics"]})
        self.assertEqual([paths.from_row(row, moved) for row in stored_rows],
                         ["E:\\Moved\\Pics\\a.jpg", "E:\\Moved\\Pics\\x\\b.jpg"])
        self.assertEqual(stored_rows, before)

    def test_a_case_difference_in_the_location_is_the_same_root(self):
        self.assertEqual(paths.to_row("d:/training/PICTURES/2024/a.jpg", desktop()), "@pictures/2024/a.jpg")

    def test_the_share_address_names_the_root_too(self):
        self.assertEqual(paths.to_row(LOGICAL + "\\2024\\a.jpg", desktop()), "@pictures/2024/a.jpg")
        # And the drive-letter and UNC spellings of one location, both listed, round-trip to the first.
        both = paths.Roots.of({"pictures": ""}, {"pictures": [DESKTOP, LOGICAL]})
        self.assertEqual(paths.to_row(LOGICAL + "\\a.jpg", both), paths.to_row(DESKTOP + "\\a.jpg", both))
        self.assertEqual(paths.from_row("@pictures/a.jpg", both), DESKTOP + "\\a.jpg")

    def test_dot_dot_and_trailing_separators_collapse_as_stored_does(self):
        self.assertEqual(paths.to_row(DESKTOP + "\\2024\\..\\2025\\a.jpg\\", desktop()), "@pictures/2025/a.jpg")

    def test_non_ascii_names_round_trip(self):
        native = DESKTOP + "\\Ferien \u00e4\u00f6\u00fc\\Caf\u00e9 \u65e5\u672c\\\u00dfa.jpg"
        self.assertEqual(paths.from_row(paths.to_row(native, desktop()), desktop()), native)

    def test_a_bare_drive_is_the_drive_root(self):
        drive = paths.Roots.of({"d": ""}, {"d": ["D:"]})
        self.assertEqual(paths.to_row("D:", drive), "@d")
        self.assertEqual(paths.to_row("D:\\x\\a.jpg", drive), "@d/x/a.jpg")
        self.assertEqual(paths.from_row("@d", drive), "D:\\")
        self.assertEqual(paths.from_row("@d/x/a.jpg", drive), "D:\\x\\a.jpg")
        self.assertEqual(paths.to_row("d:", drive), "@d")

    def test_a_row_that_is_not_a_path_under_a_root_is_refused(self):
        for bad in ("@pictures/../x", "@pictures//x", "@pictures/./x", "@pictures/x/"):
            with self.assertRaises(paths.RootsError, msg=bad):
                paths.from_row(bad, desktop())

    def test_names(self):
        self.assertEqual(paths.root_name("Pictures-2_x"), "pictures-2_x")
        for bad in ("", "a" * 33, "a b", "a/b", "caf\u00e9", "@x", None, 5):
            with self.assertRaises(paths.RootsError, msg=repr(bad)):
                paths.root_name(bad)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class RoundTrip(unittest.TestCase):
    PARTS = ["a", "IMG_0001.jpg", "Caf\u00e9", "\u65e5\u672c", "x y", "100%", "a_b", "[1]", "UPPER", "q.q", "-", "@at"]

    def spellings(self, rng, native):
        """The same path as a person, a dialog and a walk each type it."""
        yield native
        yield native.replace("\\", "/")
        yield native.upper() if rng.random() < 0.5 else native.lower()
        yield native + "\\"
        head, tail = os.path.split(native)
        yield head + "\\zz\\..\\" + tail

    def test_many_generated_paths(self):
        rng = random.Random(20261002)
        maps = [
            (desktop(), DESKTOP), (server(), SERVER),
            (paths.Roots.of({"share": "\\\\nas\\photos"}, {"share": ["\\\\nas\\photos"]}), "\\\\nas\\photos"),
            (paths.Roots.of({"d": ""}, {"d": ["D:\\"]}), "D:\\"),
            (paths.Roots.of({"pictures": LOGICAL, "deep": ""}, {"pictures": [DESKTOP], "deep": [DESKTOP + "\\Deep\\er"]}),
             DESKTOP),
        ]
        checked = 0
        for roots, location in maps:
            for _ in range(300):
                native = paths.stored(location + "\\" + "\\".join(rng.choice(self.PARTS) for _ in range(rng.randint(0, 5))))
                for spelled in self.spellings(rng, native):
                    row = paths.to_row(spelled, roots)
                    self.assertTrue(row.startswith("@"), (spelled, row))
                    back = paths.from_row(row, roots)
                    # Equal to the path, apart from the root's own spelling, which the map decides.
                    # (A UNC share root has two spellings, with and without the trailing separator.)
                    self.assertEqual(paths.key(back).rstrip("\\"), paths.key(spelled).rstrip("\\"), (spelled, row, back))
                    # Idempotent: a row is the same row from what it gave back.
                    self.assertEqual(paths.to_row(back, roots), row)
                    if spelled is native and "/" in row:
                        # The case of what is under the root is the file's, kept exactly.
                        self.assertEqual(os.path.basename(back), os.path.basename(native))
                        self.assertTrue(back.endswith(row.split("/", 1)[1].replace("/", "\\")), (back, row))
                    checked += 1
        self.assertGreater(checked, 4000)

    def test_unrooted_paths_round_trip_unchanged(self):
        for native in ("E:\\Elsewhere\\a.jpg", "\\\\other\\share\\x\\a.jpg", "D:\\Training\\PicturesX\\a.jpg",
                       "D:\\Training\\a.jpg", "D:\\"):
            row = paths.to_row(native, desktop())
            self.assertEqual(row, paths.stored(native))
            self.assertFalse(row.startswith("@"))
            self.assertEqual(paths.from_row(row, desktop()), paths.stored(native))


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class WhichRootAPathIsUnder(unittest.TestCase):
    def test_a_prefix_sibling_is_not_under_the_root(self):
        roots = paths.Roots.of({"photos": "\\\\nas\\photos", "photos2": "\\\\nas\\photos2"},
                               {"photos": ["\\\\nas\\photos"], "photos2": ["\\\\nas\\photos2"]})
        self.assertEqual(paths.to_row("\\\\nas\\photos\\a.jpg", roots), "@photos/a.jpg")
        self.assertEqual(paths.to_row("\\\\nas\\photos2\\a.jpg", roots), "@photos2/a.jpg")
        self.assertEqual(paths.to_row("\\\\nas\\photos2", roots), "@photos2")
        only = paths.Roots.of({"photos": ""}, {"photos": ["\\\\nas\\photos"]})
        self.assertEqual(paths.to_row("\\\\nas\\photos2\\a.jpg", only), "\\\\nas\\photos2\\a.jpg")

    def test_nested_locations_resolve_to_the_deeper(self):
        roots = paths.Roots.of({"outer": "", "inner": ""},
                               {"outer": ["D:\\X"], "inner": ["D:\\X\\Y"]})
        self.assertEqual(paths.to_row("D:\\X\\a.jpg", roots), "@outer/a.jpg")
        self.assertEqual(paths.to_row("D:\\X\\Y\\a.jpg", roots), "@inner/a.jpg")
        self.assertEqual(paths.to_row("D:\\X\\Y", roots), "@inner")
        self.assertEqual(paths.to_row("D:\\X\\Yz\\a.jpg", roots), "@outer/Yz/a.jpg")

    def test_a_location_under_two_roots_is_refused(self):
        with self.assertRaises(paths.RootsError):
            paths.Roots({"a": "", "b": ""}, {"a": ["D:\\X"], "b": ["d:/x/"]})
        with self.assertRaises(paths.RootsError):
            paths.Roots({"a": ""}, {"a": ["D:\\X", "D:\\X\\"]})

    def test_a_root_without_a_location_here_never_makes_a_guess(self):
        # `east` is mapped; `west` is not (its drive is not mounted, its location not written down).
        roots = paths.Roots.of({"east": "", "west": ""}, {"east": ["D:\\East"]})
        self.assertEqual(paths.to_row("D:\\East\\a.jpg", roots), "@east/a.jpg")
        with self.assertRaises(paths.UnmappedRoot):
            paths.to_row("E:\\West\\a.jpg", roots)           # might be west's: not guessed unrooted
        with self.assertRaises(paths.UnmappedRoot):
            paths.from_row("@west/a.jpg", roots)
        with self.assertRaises(paths.UnknownRoot):
            paths.from_row("@north/a.jpg", roots)
        with self.assertRaises(paths.UnmappedRoot):
            paths.sql_equals("path", "E:\\West\\a.jpg", roots)

    def test_an_unmapped_root_with_an_address_still_names_paths_in_its_address(self):
        roots = paths.Roots.of({"east": "", "west": "\\\\nas\\West"}, {"east": ["D:\\East"]})
        self.assertEqual(paths.to_row("\\\\nas\\west\\a.jpg", roots), "@west/a.jpg")

    def test_a_drive_not_mounted_is_not_a_difference(self):
        # The model asks the disk nothing, so a root on an absent drive still maps its paths.
        roots = paths.Roots.of({"usb": ""}, {"usb": ["Q:\\Camera"]})
        self.assertEqual(paths.to_row("Q:\\Camera\\a.jpg", roots), "@usb/a.jpg")

    def test_a_map_naming_a_root_the_library_lacks_is_ignored_by_it(self):
        roots = paths.Roots.of({"pictures": ""}, {"pictures": [DESKTOP], "other": ["E:\\Else"]})
        self.assertEqual(paths.to_row("E:\\Else\\a.jpg", roots), "E:\\Else\\a.jpg")

    def test_describe_and_propose(self):
        roots = paths.Roots.of({"pictures": LOGICAL, "family": "\\\\idziserver\\Pictures\\Family"},
                               {"pictures": [DESKTOP]})
        self.assertEqual(roots.describe(), [
            {"name": "family", "logical": "\\\\idziserver\\Pictures\\Family", "locations": [], "mapped": False},
            {"name": "pictures", "logical": LOGICAL, "locations": [DESKTOP], "mapped": True}])
        self.assertEqual(roots.propose_row(DESKTOP + "\\2024\\a.jpg"), "@pictures/2024/a.jpg")
        self.assertIsNone(roots.propose_row("E:\\Elsewhere\\a.jpg"))
        self.assertEqual(roots.propose_locations(["F:\\Backup\\Family", "F:\\Backup\\Pictures"]),
                         {"family": ["F:\\Backup\\Family"]})


LIBRARY_ROWS = [
    "@pictures", "@pictures/a.jpg", "@pictures/IMG_01.jpg", "@pictures/IMGX01.jpg", "@pictures/2024/b.jpg",
    "@pictures/2024/Trip/c.jpg", "@pictures/100%/d.jpg", "@pictures/_x/e.jpg", "@pictures2/a.jpg",
    "@pictures-2/a.jpg", "@pictures0/a.jpg", "@inner/a.jpg", "@inner/deep/b.jpg",
    "E:\\Elsewhere\\a.jpg", "E:\\Elsewhere\\sub\\b.jpg", "D:\\Training\\a.jpg",
]


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheRealIndexAnswersTheRanges(unittest.TestCase):
    """The helpers against a real library's photos table and its real NOCASE index."""

    @classmethod
    def setUpClass(cls):
        cls.home = own_home.for_class(cls)
        cls.library = cls.home.library("roots.db")
        PhotoIndex(cls.library).load()
        conn = db.connect(cls.library)
        conn.executemany("INSERT INTO photos (path) VALUES (?)", [(row,) for row in LIBRARY_ROWS])
        conn.commit()
        conn.close()
        cls.roots = paths.Roots.of(
            {"pictures": LOGICAL, "pictures2": "", "pictures-2": "", "pictures0": "", "inner": ""},
            {"pictures": [DESKTOP], "pictures2": ["D:\\Other2"], "pictures-2": ["D:\\Other-2"],
             "pictures0": ["D:\\Other0"], "inner": [DESKTOP + "\\Deep"]})

    @classmethod
    def tearDownClass(cls):
        cls.home.close()

    def ask(self, clause_params, explain=False):
        clause, params = clause_params
        conn = db.connect(db.readonly_uri(self.library), uri=True)
        try:
            if explain:
                return " | ".join(row[3] for row in conn.execute(
                    "EXPLAIN QUERY PLAN SELECT path FROM photos WHERE " + clause, params))
            return sorted(row[0] for row in conn.execute("SELECT path FROM photos WHERE " + clause, params))
        finally:
            conn.close()

    def assert_uses_the_index(self, clause_params):
        plan = self.ask(clause_params, explain=True)
        self.assertIn("idx_photos_path_nocase", plan)
        self.assertNotIn("SCAN", plan, plan)

    def test_equals_is_case_insensitive_and_exact(self):
        found = self.ask(paths.sql_equals("path", DESKTOP.upper() + "\\img_01.JPG", self.roots))
        self.assertEqual(found, ["@pictures/IMG_01.jpg"])
        self.assertEqual(self.ask(paths.sql_equals("path", "e:/elsewhere/A.JPG", self.roots)), ["E:\\Elsewhere\\a.jpg"])
        self.assert_uses_the_index(paths.sql_equals("path", DESKTOP + "\\a.jpg", self.roots))

    def test_underscore_and_percent_are_plain_characters(self):
        self.assertEqual(self.ask(paths.sql_equals("path", DESKTOP + "\\IMGX01.jpg", self.roots)), ["@pictures/IMGX01.jpg"])
        self.assertEqual(self.ask(paths.sql_equals("path", DESKTOP + "\\IMG_01.jpg", self.roots)), ["@pictures/IMG_01.jpg"])
        self.assertEqual(self.ask(paths.sql_under("path", DESKTOP + "\\100%", self.roots)), ["@pictures/100%/d.jpg"])

    def test_under_the_root_is_its_rows_and_not_a_sibling_roots(self):
        found = self.ask(paths.sql_under("path", DESKTOP.lower(), self.roots))
        self.assertEqual(found, sorted(["@pictures/a.jpg", "@pictures/IMG_01.jpg", "@pictures/IMGX01.jpg",
                                        "@pictures/2024/b.jpg", "@pictures/2024/Trip/c.jpg",
                                        "@pictures/100%/d.jpg", "@pictures/_x/e.jpg",
                                        # the root nested under it: its rows are spelled "@inner/..."
                                        "@inner/a.jpg", "@inner/deep/b.jpg"]))
        self.assert_uses_the_index(paths.sql_under("path", DESKTOP, self.roots))

    def test_under_a_subfolder(self):
        self.assertEqual(self.ask(paths.sql_under("path", DESKTOP + "\\2024", self.roots)),
                         ["@pictures/2024/Trip/c.jpg", "@pictures/2024/b.jpg"])
        self.assert_uses_the_index(paths.sql_under("path", DESKTOP + "\\2024", self.roots))

    def test_under_a_folder_above_the_root_reaches_its_rows_and_unrooted_ones(self):
        found = self.ask(paths.sql_under("path", "D:\\Training", self.roots))
        self.assertIn("D:\\Training\\a.jpg", found)
        self.assertIn("@pictures/a.jpg", found)
        self.assertIn("@inner/deep/b.jpg", found)
        self.assertNotIn("@pictures2/a.jpg", found)
        self.assertNotIn("@pictures-2/a.jpg", found)
        self.assertNotIn("@pictures0/a.jpg", found)
        self.assertNotIn("E:\\Elsewhere\\a.jpg", found)
        self.assert_uses_the_index(paths.sql_under("path", "D:\\Training", self.roots))

    def test_under_an_unrooted_folder_is_native(self):
        self.assertEqual(self.ask(paths.sql_under("path", "e:\\ELSEWHERE", self.roots)),
                         ["E:\\Elsewhere\\a.jpg", "E:\\Elsewhere\\sub\\b.jpg"])
        self.assert_uses_the_index(paths.sql_under("path", "E:\\Elsewhere", self.roots))

    def test_the_root_is_not_in_its_own_folder_but_its_siblings_never_are(self):
        sibling = self.ask(paths.sql_under("path", "D:\\Other2", self.roots))
        self.assertEqual(sibling, ["@pictures2/a.jpg"])
        self.assertEqual(self.ask(paths.sql_under("path", "D:\\Other-2", self.roots)), ["@pictures-2/a.jpg"])

    def test_in_is_the_direct_rows_only(self):
        self.assertEqual(self.ask(paths.sql_in("path", DESKTOP + "\\2024", self.roots)), ["@pictures/2024/b.jpg"])
        self.assertEqual(self.ask(paths.sql_in("path", "E:\\Elsewhere", self.roots)), ["E:\\Elsewhere\\a.jpg"])
        self.assertEqual(self.ask(paths.sql_in("path", DESKTOP, self.roots)),
                         sorted(["@pictures/IMG_01.jpg", "@pictures/IMGX01.jpg", "@pictures/a.jpg"]))
        self.assert_uses_the_index(paths.sql_in("path", DESKTOP + "\\2024", self.roots))


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheMachineMap(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.file = config.machine_roots_path()

    def write(self, text):
        with open(self.file, "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_the_file_lives_in_the_home(self):
        self.assertEqual(self.file, os.path.join(self.home.root, "machine_roots.json"))

    def test_absent_means_no_mapping(self):
        self.assertEqual(config.machine_roots(), {})
        self.assertTrue(config.roots_of({}).identity)

    def test_loaded_and_prepared(self):
        self.write(json.dumps({"version": 1, "roots": {"Pictures": [DESKTOP], "inbox": "E:\\Inbox"}}))
        self.assertEqual(config.machine_roots(), {"pictures": (DESKTOP,), "inbox": ("E:\\Inbox",)})
        roots = config.roots_of({"pictures": LOGICAL})
        self.assertEqual(paths.to_row(DESKTOP + "\\a.jpg", roots), "@pictures/a.jpg")
        self.assertIs(config.roots_of({"pictures": LOGICAL}), roots)   # prepared once per content
        self.assertEqual(config.describe_machine({"pictures": LOGICAL}),
                         [{"name": "pictures", "logical": LOGICAL, "locations": [DESKTOP], "mapped": True}])
        self.assertEqual(config.propose_row(DESKTOP + "\\a.jpg", {"pictures": LOGICAL}), "@pictures/a.jpg")

    def test_malformed_is_an_error_naming_the_file_never_an_identity(self):
        bad = [
            "", "not json", "[]", '{"version": 1}', '{"version": 2, "roots": {}}', '{"roots": {}}',
            '{"version": 1, "roots": {}, "extra": 1}', '{"version": 1, "roots": []}',
            '{"version": 1, "roots": {"a": []}}', '{"version": 1, "roots": {"a": [5]}}',
            '{"version": 1, "roots": {"a": ["relative\\\\path"]}}',
            '{"version": 1, "roots": {"a": ["D:Training"]}}',
            '{"version": 1, "roots": {"bad name": ["D:\\\\x"]}}',
            '{"version": 1, "roots": {"a": ["D:\\\\x"], "A": ["D:\\\\y"]}}',
            '{"version": 1, "roots": {"a": ["D:\\\\x"], "a": ["D:\\\\y"]}}',
            '{"version": 1, "roots": {"a": ["D:\\\\x"], "b": ["d:/X/"]}}',
            '{"version": 1, "roots": {"a": ["D:\\\\x", "D:\\\\x"]}}',
        ]
        for text in bad:
            self.write(text)
            with self.assertRaises(config.MachineMapError, msg=text) as caught:
                config.machine_roots()
            self.assertIn("machine_roots.json", str(caught.exception))

    def test_an_unreadable_file_is_an_error_too(self):
        with open(self.file, "wb") as handle:
            handle.write(b"\xff\xfe\x00bad")
        with self.assertRaises(config.MachineMapError):
            config.machine_roots()

    def test_two_processes_loading_while_it_is_replaced_see_one_whole_map_or_the_other(self):
        first = json.dumps({"version": 1, "roots": {"pictures": [DESKTOP]}})
        second = json.dumps({"version": 1, "roots": {"pictures": [SERVER], "extra": ["E:\\Extra"]}})
        self.write(first)
        script = (
            "import json, sys\n"
            "sys.path.insert(0, %r)\n"
            "from tagpup import config\n"
            "seen = set()\n"
            "for _ in range(150):\n"
            "    seen.add(json.dumps(config.machine_roots(), sort_keys=True))\n"
            "print(json.dumps(sorted(seen)))\n"
        ) % os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        environ = dict(os.environ, TAGPUP_HOME=self.home.root)
        readers = [processes.start([sys.executable, "-c", script], stdout=-1, stderr=-1, env=environ, text=True)
                   for _ in range(2)]
        for turn in range(60):
            scratch = self.file + ".new"
            with open(scratch, "w", encoding="utf-8") as handle:
                handle.write(second if turn % 2 == 0 else first)
            try:
                os.replace(scratch, self.file)
            except PermissionError:
                pass   # a reader had it open at that instant; the next turn replaces it
            time.sleep(0.005)
        whole = {json.dumps({"pictures": [DESKTOP]}, sort_keys=True),
                 json.dumps({"extra": ["E:\\Extra"], "pictures": [SERVER]}, sort_keys=True)}
        for reader in readers:
            out, err = reader.communicate(timeout=120)
            self.assertEqual(reader.returncode, 0, err)
            self.assertTrue(set(json.loads(out)) <= whole, out)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class ItIsFastEnoughForEveryRow(unittest.TestCase):
    def test_no_compile_no_file_no_sorting_per_call(self):
        roots = desktop()
        samples = [DESKTOP + "\\2024\\Trip %d\\IMG_%04d.JPG" % (i % 50, i) for i in range(2000)]
        with mock.patch("re.compile", side_effect=AssertionError("compiled per call")), \
                mock.patch("builtins.open", side_effect=AssertionError("file per call")), \
                mock.patch("builtins.sorted", side_effect=AssertionError("sorted per call")):
            for sample in samples:
                paths.from_row(paths.to_row(sample, roots), roots)
                paths.sql_under("path", sample, roots)

    def test_to_row_costs_a_small_multiple_of_stored(self):
        roots = desktop()
        samples = [DESKTOP + "\\2024\\Trip %d\\IMG_%04d.JPG" % (i % 50, i) for i in range(20000)]
        began = time.perf_counter()
        for sample in samples:
            paths.stored(sample)
        base = time.perf_counter() - began
        began = time.perf_counter()
        for sample in samples:
            paths.to_row(sample, roots)
        took = time.perf_counter() - began
        # 68,466 rows would take about 3.4 times this; the bound is loose, to hold on a busy machine.
        self.assertLess(took, max(base * 6, 0.25), (took, base))


if __name__ == "__main__":
    unittest.main()
