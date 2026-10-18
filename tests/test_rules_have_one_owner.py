"""Rules that were written more than once, each now said in one place (docs/findings.md, #74). The copies
agreed when they were found; nothing kept them agreeing. Each rule is held to its owner: a copy that reappears
fails (the table below), and where a page must keep a copy of its own, the page is pinned to the owner.

Folded in from the guards for paths, ExifTool and subprocess (a line rule each), and from the bucket-names and
face-values guards: a person called "Excluded" opened the excluded faces
instead of their own (#68); a face was compared with a mean in clustering, a geometric median in the grid and a
mean with no years in the suggester, each against a value of its own (#71, #75).
"""
import inspect
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shipped_sources import ROOT, page_source, python_sources  # noqa: E402

CLUSTERING = os.path.join("tagpup", "core", "clustering.py")
IMAGES = os.path.join("tagpup", "files", "images.py")
SUGGESTING = os.path.join("tagpup", "core", "suggesting.py")
LIBRARY = os.path.join("tagpup", "core", "library.py")
#: (what, the one file that may say it, what only it may spell, the files besides it that may). owner None: anywhere
#: in tagpup is a copy. `allowed` is a path prefix or a path.
RULES = (
    ("a background face is decided in clustering", CLUSTERING, r"0\.10?\s*\*\s*max_area|area\s*<\s*2000", ()),
    ("a face crop is encoded by images", IMAGES, r"format\s*=\s*['\"]JPEG['\"]", ()),
    # The tutorial's seeding copies a PNG without its metadata: a file, not a photo.
    ("a photo is opened by tagpup.files", None, r"Image\.open\(|exif_transpose\(|MAX_IMAGE_PIXELS",
     (os.path.join("tagpup", "files"), os.path.join("scripts", "prepare_test_environment.py"))),
    ("CLIP's prompts are suggesting's", SUGGESTING, r"[\"']a photo of", ()),
    ("a test library's prefix is library's", LIBRARY, r"[\"']test_[\"']", ("tools",)),   # tools/ names test modules
    ("a library is named in the data folder", None, r"[\"']data/[^\"']*\.db[\"']|os\.path\.join\(\s*[\"']data[\"']", ()),
    ("a new library's roots are vocabulary's", os.path.join("tagpup", "core", "vocabulary.py"),
     # "Location" alone is an HTTP header; "activity" is also the Activity page's route, named once (ACTIVITY_PAGE).
     r"[\"'](Activity|School|Trips|Scenic|Albums)/?[\"']|[\"']Location/[\"']|(?<!ACTIVITY_PAGE = )[\"'](activity|school|trips)[\"']", ()),
    ("an unknown year is dates'", os.path.join("tagpup", "core", "dates.py"), r"[\"']Unknown[\"']", ()),
    ("the apps' ports are tagpup_web's", "tagpup_web.py", r"def find_available_port|\b80[89]0\b", ()),
    ("a face decision's numbers are clustering's", CLUSTERING, r"\b(OFFER_A_NAME|NAME_WITHOUT_ASKING|NAME_MARGIN|OFFER_MARGIN)\b", ()),
    ("the grouping radius is clustering's", CLUSTERING, r"eps\s*=\s*0\.\d|CLUSTER_EPS\s*=\s*0\.\d", ()),
    # A photo file's fields are written by the journaled path (file_changes.write_fields), and by the just-look path
    # (file_only, unjournaled by the owner's decision, #527) alone: a bulk write of its own once left rows describing
    # what the photos used to hold (#1443).
    ("a photo's fields are written by file_changes", os.path.join("tagpup", "services", "file_changes.py"),
     r"field_values\.write\(|from\s+tagpup\.files\.field_values\s+import\s[^\n]*\bwrite\b",
     (os.path.join("tagpup", "services", "file_only.py"),)),
)


def read(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as f:
        return f.read()


def sources_matching(pattern, owner=None):
    """Shipped Python files other than `owner` whose text matches `pattern`."""
    owner = os.path.normcase(owner) if owner else None
    return [path for path in python_sources() if os.path.normcase(path) != owner and re.search(pattern, read(path))]


def lines_spelling(page, pattern, *exempt):
    """Line numbers of a page that spell `pattern`, outside a declaration or a comment."""
    return [n for n, line in enumerate(page.splitlines(), 1) if re.search(pattern, line)
            and not any(e in line for e in exempt) and not line.strip().startswith(("//", "*", "/*"))]


PATHS = os.path.join("tagpup", "core", "paths.py")
PROCESSES = os.path.join("tagpup", "core", "processes.py")
EXIFTOOL = os.path.join("tagpup", "files", "exiftool_session.py")
FIELD_VALUES = os.path.join("tagpup", "files", "field_values.py")
#: A line that is genuinely not about a photo path -- a tag hierarchy, a database file's URI -- says so, which keeps
#: each exception visible where it is made.
NOT_A_PATH = "# not a path:"
#: The test of what the processes owner does: it spells the flags it checks.
BEHAVES = os.path.join("tests", "test_processes_single_owner.py")
#: (what only the owner may say, the one file that may, a pattern for a line, a line that must match it, what the
#: owner must still contain, so the check is not worthless, the checked files besides the shipped ones).
#: Comment lines are not read. A rule whose last column is "all" also reads the tests and the root's modules.
LINE_RULES = (
    # Nothing but paths.py decides how a photo path is spelled or compared: every component converted paths by hand,
    # each its own way, and the database was looked up in a spelling it never stored; tag writes, renames and deletes
    # reported success and changed nothing (CLAUDE.md). LIKE reads "_" as any character and neither it nor LOWER can
    # use the path index: paths.sql_equals / paths.sql_under.
    ("a separator converted by hand", PATHS, r"""\.replace\(\s*(['"])\\\\\1\s*,\s*(['"])/\2""", 'x = p.replace("\\\\", "/")', "", ""),
    ("a separator converted by hand", PATHS, r"""\.replace\(\s*(['"])/\1\s*,\s*(['"])\\\\\2""", "x = p.replace('/', '\\\\')", "", ""),
    ("os.path.normcase", PATHS, r"\bnormcase\(", "k = os.path.normcase(p)", "", ""),
    ("a local path-normalising helper", PATHS, r"^\s*def\s+(normalize_path|to_db_path|normalise_path|norm_path)\b",
     "def to_db_path(path):", "", ""),
    ("LOWER(path) in SQL", PATHS, r"LOWER\(\s*(\w+\.)?(photo_)?path\s*\)", 'execute("SELECT 1 FROM photos WHERE LOWER(path) = ?")', "", ""),
    ("LIKE on a path column", PATHS, r"\b(photo_)?path\s+(NOT\s+)?LIKE\b", 'execute("SELECT 1 FROM faces WHERE photo_path LIKE ?")', "", ""),
    # Every ExifTool process comes from ExifToolSession: pyexiftool reads a command's stdout to the end before any
    # stderr, a batch drawing a warning per file fills the 4 KB stderr pipe, and two scripts sat at 0% CPU for two days.
    ("pyexiftool constructed directly", EXIFTOOL, r"\b(?:exiftool\.)?(ExifToolHelper|ExifToolAlpha|ExifTool)\s*\(",
     "with exiftool.ExifTool(executable=path) as et:", "class ExifToolSession(exiftool.ExifToolHelper", ""),
    # A photo's fields are written by one writer, field_values.write: the indexer's identity mint built its own set_tags,
    # outside the writer's clears, its minor-error rule and its guard (#1473, #1418). An execute of a "-TAG=value" is a
    # write too, and one without -overwrite_original leaves an _original copy beside the photo (#1496).
    ("an ExifTool write outside field_values", FIELD_VALUES, r"\.set_tags\(|-overwrite_original|\.execute\([^)]*[\"']-[\w:-]+<?=",
     'et.execute("-XMP:Subject=Quay", path)', "-overwrite_original", ""),
    # Other processes are started, run and stopped through tagpup.core.processes: every spawn decided for itself
    # whether the child got a console window, and a test run put a terminal window on the desktop for every server it
    # started and every reaper (#124).
    ("subprocess called directly", PROCESSES, r"subprocess\.(Popen|run|call|check_output|check_call)\(", "subprocess.run(c)",
     "Popen(", "all"),
    ("a console flag or taskkill spelled", PROCESSES, r"creationflags|CREATE_NO_WINDOW|DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP|\btaskkill\b|\btasklist\b",
     "flags = CREATE_NO_WINDOW", "CREATE_NO_WINDOW", "all"),
)
#: Lines no line rule may match.
NOT_FORBIDDEN = ('execute("SELECT 1 FROM photos WHERE people LIKE ?")', 'x = tag.split("/")',
                 "with ExifToolSession(executable=executable) as et:", "from exiftool_session import ExifToolSession")


def lines_of(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        return list(enumerate(handle, 1))


class OneOwnerPerLine(unittest.TestCase):
    def test_each_rule_is_said_once(self):
        here = os.path.relpath(__file__, ROOT)
        everywhere = [os.path.join("tests", n) for n in sorted(os.listdir(os.path.join(ROOT, "tests"))) if n.endswith(".py")] + \
                     [n for n in sorted(os.listdir(ROOT)) if n.endswith(".py")]
        problems = []
        for what, owner, pattern, _hit, _proof, scope in LINE_RULES:
            pattern = re.compile(pattern, re.I if "LOWER" in pattern or "LIKE" in pattern else 0)
            for relative in sorted(set(python_sources()) | (set(everywhere) if scope == "all" else set())):
                if os.path.normcase(relative) in map(os.path.normcase, (owner, here, BEHAVES)):
                    continue
                problems += ["%s:%d  %s\n    %s" % (relative, number, what, line.strip()) for number, line in lines_of(relative)
                             if pattern.search(line) and NOT_A_PATH not in line and not line.lstrip().startswith("#")]
        self.assertEqual([], problems, "\n\nSay it through its owner (paths.stored / key / sql_equals, ExifToolSession, "
                         "tagpup.core.processes):\n\n" + "\n".join(problems))

    def test_the_rules_see_what_they_forbid_and_their_owners_still_say_it(self):
        # A guard whose patterns quietly match nothing passes forever.
        for what, owner, pattern, hit, proof, _scope in LINE_RULES:
            with self.subTest(what, hit=hit):
                flags = re.I if "LOWER" in pattern or "LIKE" in pattern else 0
                self.assertTrue(re.search(pattern, hit, flags))
                self.assertIn(proof, read(owner))
        for line in NOT_FORBIDDEN:
            self.assertFalse(any(re.search(rule[2], line, re.I) for rule in LINE_RULES), line)


class NobodyElseSpellsIt(unittest.TestCase):
    def test_each_rule_is_said_once(self):
        for what, owner, pattern, allowed in RULES:
            with self.subTest(what):
                found = [p for p in sources_matching(pattern, owner) if not p.startswith(allowed)]
                self.assertEqual([], found, what)

    def test_nobody_guesses_a_vector_length_from_the_models_name(self):
        # The CLI did, three times: 512 for anything not ViT-L or ViT-H, and so cleared every vector on each index
        # with ViT-bigG-14, whose are 1280. The length is asked of open_clip, in tagpup.ml.clip.
        guess = r"[\"']ViT-[LH][\"']\s+in\b"
        tests = [name for name in os.listdir(os.path.join(ROOT, "tests")) if name.endswith(".py")
                 and name != os.path.basename(__file__) and re.search(guess, read(os.path.join("tests", name)))]
        self.assertEqual([], sources_matching(guess) + tests)

    def test_the_clip_words_are_merged_in_one_place(self):
        # The runtime reads config.ini's words once, for the suggester to merge with the tree's at the start of each
        # run (tagpup.services.suggester.model_for_run); every other reader merges through zero_shot_words.
        for path in sources_matching(r"config\.candidate_tags\("):
            if path != os.path.join("tagpup", "runtime.py"):
                for line in read(path).splitlines():
                    if "config.candidate_tags(" in line:
                        self.assertIn("zero_shot_words(", line, path)
        from tagpup.services import suggester
        self.assertIn("zero_shot_words(configured_words,", inspect.getsource(suggester.model_for_run))

    def test_a_model_has_no_setting_of_its_own(self):
        self.assertNotRegex(read(os.path.join("tagpup", "ml", "clip.py")), r"[\"'](ViT-|laion)|max_aspect_ratio:\s*float\s*=")
        from tagpup.ml.clip import ClipModel
        from tagpup.services import settings as library_settings
        with self.assertRaises(TypeError):
            ClipModel(model="ViT-T", **library_settings.LibrarySettings(dict(library_settings.DEFAULTS)).embedder)

    def test_an_index_names_its_library(self):
        from tagpup.services.search import PhotoIndex
        self.assertIs(inspect.Parameter.empty, inspect.signature(PhotoIndex).parameters["db_path"].default)


class TheOwnersValues(unittest.TestCase):
    """Each rule's owner says it as the rule is."""

    def test_the_background_face(self):
        from tagpup.core import clustering
        big, small, middling = [0, 0, 100, 100], [0, 0, 20, 20], [0, 0, 40, 60]
        self.assertEqual({1}, clustering.background_faces([big, small, middling]))
        self.assertEqual(set(), clustering.background_faces([[0, 0, 400, 400], [0, 0, 50, 50]]))   # not small outright
        self.assertEqual(set(), clustering.background_faces([small]))   # alone in its photo
        self.assertEqual(set(), clustering.background_faces([]))
        self.assertEqual({1}, clustering.background_faces([big, None]))

    def test_the_face_crop_and_the_opened_photo(self):
        from PIL import Image

        from tagpup.files import images
        crop = images.crop_jpeg(Image.new("RGB", (600, 300), (120, 80, 40)))
        with Image.open(io.BytesIO(crop)) as back:
            self.assertEqual(("JPEG", (images.CROP_SIZE, images.CROP_SIZE // 2)), (back.format, back.size))
        with Image.open(io.BytesIO(images.crop_jpeg(Image.new("RGB", (40, 60))))) as back:
            self.assertEqual((40, 60), back.size)
        folder = tempfile.mkdtemp(prefix="open_photo_")
        self.addCleanup(shutil.rmtree, folder, True)
        photo = os.path.join(folder, "sideways.png")
        img = Image.new("L", (40, 20))
        exif = img.getexif()
        exif[0x0112] = 6
        img.save(photo, exif=exif.tobytes())
        upright = images.opened(photo, upright=True)
        self.assertEqual(((20, 40), "RGB"), (upright.size, upright.mode))
        self.assertEqual((40, 20), images.opened(photo, upright=False).size)
        self.assertGreaterEqual(Image.MAX_IMAGE_PIXELS, 500_000_000)

    def test_what_clip_is_asked(self):
        from tagpup.core import suggesting
        words = suggesting.zero_shot_words(
            ["Beach", "sunset "], ["Trips/Coast", "People/Oda Castellane", "Activity/Beach", "Activity/ Rowing"], {"people"})
        self.assertEqual(["Beach", "sunset", "Rowing", "Coast"], words)
        self.assertEqual("a photo of a sunset", suggesting.clip_prompt("Sunset"))
        self.assertEqual("a photo of a sunset in 1998", suggesting.clip_prompt("Sunset", 1998))
        self.assertEqual("a photo of Oda Castellane in 1998", suggesting.clip_prompt("Oda Castellane", 1998, person=True))

    def test_the_runtimes_model_is_the_librarys(self):
        import own_home
        from tagpup.core.library import Library
        from tagpup.ml import clip
        from tagpup.runtime import Runtime
        from tagpup.services import libraries as library_actions
        from tagpup.services import search
        from tagpup.services import settings as library_settings
        from tagpup.store import embeddings
        home = own_home.for_test(self)
        library_actions.create(home.library("harbour.db"))
        library = Library(home.library("harbour.db"))
        found = library_settings.of(library).embedder
        with mock.patch.object(clip, "ClipModel") as model:
            model.return_value.settings = found
            runtime = Runtime()
            self.assertEqual(embeddings.model_key(**found), runtime.model_key(library))
            self.assertEqual(runtime.model_key(library), search.PhotoEmbeddings(runtime.clip(library)).model_key)
        model.assert_called_once_with(**found)

    def test_a_test_librarys_name_and_where_a_library_is(self):
        from tagpup import config
        from tagpup.core import library
        self.assertEqual("test_harbour.db", library.for_mode("harbour.db", True))
        self.assertEqual("test_harbour.db", library.for_mode("test_harbour.db", True))
        self.assertEqual("harbour.db", library.for_mode("test_harbour.db", False))
        self.assertEqual("harbour.db", library.for_mode("harbour.db", False))
        self.assertTrue(library.is_test_library("D:/data/test_harbour.db"))
        self.assertFalse(library.is_test_library("D:/test_data/harbour.db"))
        self.assertEqual(os.path.join(config.data_dir(), "family.db"), config.library_path("family.db"))

    def test_the_roots_a_new_library_is_given(self):
        from tagpup.core import vocabulary
        from tagpup.core.suggesting import caption_from_tags
        self.assertEqual(("Activity", "Pets", "School", "Trips"), vocabulary.NEW_LIBRARY_ROOTS)
        self.assertIn(vocabulary.ACTIVITY_ROOT, vocabulary.NEW_LIBRARY_ROOTS)
        self.assertTrue(set(vocabulary.PLACE_ROOTS) <= set(vocabulary.NEW_LIBRARY_ROOTS))
        self.assertTrue({vocabulary.ACTIVITY_ROOT, *vocabulary.PLACE_ROOTS} <= set(vocabulary.CONTEXT_ROOTS))
        self.assertEqual("Oda Castellane - Rowing, Harbour School and Lakes",
                         caption_from_tags(["People/Oda Castellane", "Activity/Rowing", "Trips/Lakes",
                                            "School/Harbour School"], {"people"}))

    def test_the_year_of_a_photo_with_none_and_the_ports(self):
        import tagpup_web
        from tagpup.core import dates
        self.assertEqual((2019, "2019", dates.UNKNOWN_YEAR, dates.UNKNOWN_YEAR),
                         (dates.shown_year(2019), dates.shown_year("2019"), dates.shown_year(None), dates.shown_year(0)))
        self.assertEqual({"tagpup": 8090, "tuner": 8080}, tagpup_web.PORTS)

    def test_a_bucket_name_is_refused_whatever_the_case(self):
        from tagpup.core import validation
        for name in ("Excluded", "ungrouped", "Unknown Faces", " UNMATCHED "):
            self.assertIsNotNone(validation.problem("name", name), name)
        self.assertIsNone(validation.problem("name", "Excluded Ferris"))

    def test_the_camera_of_a_photo(self):
        """The shapes photo_index holds: Shift Date Taken chooses the photos of "Google Pixel 8 Pro" and not those of a
        model that is only "Pixel 8 Pro"; the server, the panel and the search name a camera by one rule."""
        from tagpup.core import fields, photo_meta
        from tagpup.services.photos import page_record
        self.assertEqual("Fujifilm X100V", fields.camera_of({"Model": "X100V", "EXIF:Make": "Fujifilm"}))
        self.assertEqual("Fujifilm", fields.camera_of({"EXIF:Make": "Fujifilm", "Model": ""}))
        self.assertEqual(fields.UNKNOWN_CAMERA, fields.camera_of(None))
        self.assertTrue(fields.on_camera({}, fields.ALL_CAMERAS))
        self.assertTrue(fields.on_camera({"EXIF:Model": "X100V"}, "X100V"))
        self.assertFalse(fields.on_camera({"EXIF:Model": "X100V"}, "Pixel 8"))
        shapes = {
            "Google Pixel 8 Pro": {"EXIF:Make": "Google", "EXIF:Model": "Pixel 8 Pro"},
            "Canon EOS R6m2": {"EXIF:Make": "Canon", "EXIF:Model": "Canon EOS R6m2"},
            "KODAK CX4310 DIGITAL CAMERA": {"EXIF:Make": "EASTMAN KODAK COMPANY", "EXIF:Model": "KODAK CX4310 DIGITAL CAMERA"},
            "SONY DSC-X8": {"Make": "SONY", "Model": "DSC-X8"},
            "Lanternfly": {"EXIF:Make": "Lanternfly"},
            "Pixel 8 Pro": {"EXIF:Model": "Pixel 8 Pro"},
            fields.UNKNOWN_CAMERA: {"XMP:Subject": ["a"]},
        }
        for name, raw in shapes.items():
            with self.subTest(name=name):
                self.assertEqual(name, fields.camera_of(raw))
                self.assertEqual(name, page_record("D:/a.jpg", {"raw_metadata": raw})["camera"] or fields.UNKNOWN_CAMERA)
                self.assertEqual(photo_meta.gear(raw).camera or fields.UNKNOWN_CAMERA, name)
                self.assertEqual([name], [other for other, theirs in shapes.items() if fields.on_camera(theirs, name)])
        spelled = r"[\"'](Unknown Camera|All Cameras|EXIF:Model)[\"']"
        self.assertEqual([], [p for p in sources_matching(spelled, os.path.join("tagpup", "core", "fields.py"))
                              if re.search(r"[\"'](Unknown Camera|All Cameras)[\"']|get\([\"']EXIF:Model", read(p))])

    def test_the_decisions_about_a_face(self):
        """The likeness and the margin over the next person each a gate (owner, 2026-09-24 and 2026-10-17): 0.70 and 0.02
        to offer, 0.80 and 0.04 to name unasked, 0.70 and 0.04 when the photo's keywords name the person."""
        import math
        from tagpup.core import clustering
        self.assertEqual((True, False, False, True, False, False, True, False),
                         (clustering.is_offered(0.70, 0.02), clustering.is_offered(0.69, 1), clustering.is_offered(0.9, 0.019),
                          clustering.names_unasked(0.80, 0.04), clustering.names_unasked(0.79, 1),
                          clustering.names_unasked(0.9, 0.039), clustering.looks_wrong(0.69), clustering.looks_wrong(0.70)))
        self.assertEqual((True, False, False), (clustering.names_from_the_tag(0.70, 0.04), clustering.names_from_the_tag(0.69, 1),
                                                clustering.names_from_the_tag(0.9, 0.03)))
        self.assertEqual(("likely", "possible", "possible", "possible", None, None, None),
                         tuple(clustering.band(v, m) for v, m in ((0.80, 0.04), (0.79, 1), (0.70, 0.02), (0.9, 0.03),
                                                                   (0.69, 1), (0.9, 0.01), (None, None))))
        self.assertTrue(clustering.names_unasked(0.80, math.inf))   # a name carried is asked no margin
        self.assertEqual(0.885, clustering.GROUPING)
        self.assertAlmostEqual(0.48, clustering.distance(clustering.GROUPING), places=2)
        self.assertEqual((True, False, False), (clustering.gathers_for_a_new_person(0.80, 0.02),
                                                clustering.gathers_for_a_new_person(0.79, 1),
                                                clustering.gathers_for_a_new_person(0.9, 0.019)))
        for decide in (clustering.is_offered, clustering.names_unasked, clustering.names_from_the_tag,
                       clustering.gathers_for_a_new_person):
            self.assertFalse(decide(None, None))   # nothing is decided about nothing
        self.assertFalse(clustering.looks_wrong(None))


class ThePagesKeepNoCopyOfTheirOwn(unittest.TestCase):
    """Where a page must keep a copy, it is pinned to the owner's."""

    def test_tagtuners_buckets_reasons_year_and_bands(self):
        from tagpup.core import dates, validation, vocabulary
        from tagpup.services import faces
        page = page_source("tuner")
        found = re.search(r"const BUCKET = Object\.freeze\(\{(.*?)\}\)", page)
        self.assertIsNotNone(found, "the page's copy of the bucket names moved")
        self.assertEqual({key.upper(): name for key, name in vocabulary.BUCKETS.items()},
                         dict(re.findall(r"(\w+):\s*'([^']*)'", found.group(1))))
        self.assertEqual([], lines_spelling(page, r"'(Unknown Faces|Ungrouped|Excluded)'", "const BUCKET"))
        # The page keeps no list of reasons: it offers the published choices, less the one an ignored cluster is given.
        self.assertIsNone(re.search(r"const EXCLUDE_REASONS\b", page), "the page keeps its own copy of the reasons again")
        self.assertIn("choicesOf('exclusion reason')", page)
        ignored = re.search(r"const EXCLUDE_IGNORED_CLUSTER = '([^']*)';", page)
        self.assertIsNotNone(ignored, "the page's reason for an ignored cluster moved")
        self.assertEqual(validation.IGNORED_CLUSTER, ignored.group(1))
        self.assertEqual(validation.EXCLUSION_REASONS[-1], ignored.group(1), "the ignored cluster is no longer the last choice")
        self.assertNotIn(validation.IGNORED_CLUSTER, validation.EXCLUSION_REASONS[:-1])
        # The server's own choices: a caller may still send "stranger" as its reason.
        chosen = r"[\"'](%s|%s)[\"']" % (re.escape(faces.DEFAULT_REASON), re.escape(faces.IGNORED_CLUSTER))
        self.assertEqual([], sources_matching(chosen, os.path.join("tagpup", "core", "validation.py")))
        spelled = r"[\"'](%s)[\"']" % "|".join(re.escape(r) for r in faces.EXCLUSION_REASONS)
        self.assertEqual([], lines_spelling(page, spelled, "const EXCLUDE_"))
        self.assertIn("const UNKNOWN_YEAR = '%s';" % dates.UNKNOWN_YEAR, page)
        self.assertEqual([], lines_spelling(page, r"'%s'" % dates.UNKNOWN_YEAR, "const UNKNOWN_YEAR"))
        # The page asks the server which names look wrong and which band a face is in; it keeps no numbers.
        self.assertNotRegex(page, r"similarity\s*<\s*0\.\d")
        self.assertIn("possibly_wrong", page)
        self.assertEqual([], re.findall(r"(?:similarity|sim|ranked)\s*>=\s*0\.\d+", page))

    def test_the_tagpup_page_names_a_camera_as_the_server_does(self):
        from tagpup.core import fields
        page = read(os.path.join("web", "tagpup", "rename.js"))
        self.assertNotIn("CAMERA_FIELDS", page)
        self.assertNotIn("raw_metadata", page, "the page does not read the metadata to name a camera")
        self.assertIn("photo.camera", page)
        self.assertIn("const UNKNOWN_CAMERA = '%s';" % fields.UNKNOWN_CAMERA, page)
        self.assertIn("const ALL_CAMERAS = '%s';" % fields.ALL_CAMERAS, page)
        self.assertEqual([], lines_spelling(page, r"[\"'](Unknown Camera|All Cameras|EXIF:Model)[\"']",
                                            "const CAMERA_FIELDS =", "const UNKNOWN_CAMERA =", "const ALL_CAMERAS ="))


if __name__ == "__main__":
    unittest.main()
