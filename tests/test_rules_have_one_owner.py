"""Rules that were written more than once, each now said in one place (docs/findings.md, #74). The copies
agreed when they were found; nothing kept them agreeing. Each rule is held to its owner: a copy that reappears
fails (the table below), and where a page must keep a copy of its own, the page is pinned to the owner.

Folded in from the guards for paths, ExifTool and subprocess (a line rule each), and from the bucket-names and
face-values guards: a person called "Excluded" opened the excluded faces
instead of their own (#68); a face was compared with a mean in clustering, a geometric median in the grid and a
mean with no years in the suggester, each against a value of its own (#71, #75).
"""
import ast
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
RUNTIME = os.path.join("tagpup", "runtime.py")
#: (what, the one file that may say it, what only it may spell, the files besides it that may). owner None: anywhere
#: in tagpup is a copy. `allowed` is a path prefix or a path.
RULES = (
    # The photos' vectors are made by the runtime's PhotoEmbeddings, which hands it ffmpeg: Suggest built its own
    # without, and refused every clip (#2603). The CLI's index reads what is kept with no model (PhotoEmbeddings(None).
    ("a PhotoEmbeddings that makes vectors is the runtime's", RUNTIME, r"PhotoEmbeddings\((?!None\b)", ()),
    # The libraries a home holds were listed twice, runtime and the picker (#2758).
    ("the home's libraries are listed by the runtime", RUNTIME, r"def home_libraries\(", ()),
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
    # A run's lifecycle (claim, progress, failure mapping, end, give-back) is job_runs.run's: each job kept its own claim and
    # end, and an end that failed left a run `running` for good (#2189, #2199).
    ("a run is claimed, ended or discarded by job_runs.run", os.path.join("tagpup", "services", "job_runs.py"),
     r"\b(job_runs|runs_service|stored_runs)\.(claim|end|discard|finish)\(", ()),
    # A recorded time was spelled by hand in fourteen places (the journal's, the runs', the Activity page's), one
    # format each could have drifted from: dates.recorded, or job_runs.stamp for a run's.
    ("a recorded time is spelled by dates", os.path.join("tagpup", "core", "dates.py"), r"%Y-%m-%d %H:%M:%S", ()),
    ("the apps' ports are tagpup_web's", "tagpup_web.py", r"def find_available_port|\b80[89]0\b", ()),
    ("a face decision's numbers are clustering's", CLUSTERING, r"\bCuts\(|\b(offer_a_name|name_without_asking|name_margin|offer_margin|group_at)\s*=", ()),
    # The radius is a face model's (Cuts.group_at): a module constant of one value for every model is a copy, even in
    # clustering (it was GROUPING, and grouping's CLUSTER_EPS).
    ("the grouping radius is a face model's cuts", None, r"eps\s*=\s*0\.\d|\b(CLUSTER_EPS|GROUPING)\s*=", ()),
    # A photo file's fields are written by the journaled path (file_changes.write_fields), and by the just-look path
    # (file_only, unjournaled by the owner's decision, #527) alone: a bulk write of its own once left rows describing
    # what the photos used to hold (#1443). metadata_check writes copies in its sandbox alone, never a library's photo, once
    # as ExifTool's rewrite (no `saving`) and once as a journaled write is (a `saving` that saves nothing: the own
    # writer's patch when it fits; tests/test_the_two_writers_compared_on_copies.py).
    ("a photo's fields are written by file_changes", os.path.join("tagpup", "services", "file_changes.py"),
     r"field_values\.write\(|from\s+tagpup\.files\.field_values\s+import\s[^\n]*\bwrite\b",
     (os.path.join("tagpup", "services", "file_only.py"), os.path.join("tagpup", "services", "metadata_check.py"))),
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
LOCK_OWNERS = os.path.join("tagpup", "files", "lock_owners.py")
#: A line that is genuinely not about a photo path -- a tag hierarchy, a database file's URI -- says so, which keeps
#: each exception visible where it is made.
NOT_A_PATH = "# not a path:"
#: The test of what the processes owner does: it spells the flags it checks.
BEHAVES = os.path.join("tests", "test_processes_single_owner.py")
PYEXIFTOOL = "pyexiftool constructed directly"
SESSION_OUTSIDE = "ExifToolSession constructed outside field_values"
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
    (PYEXIFTOOL, EXIFTOOL, r"\b(?:exiftool\.)?(ExifToolHelper|ExifToolAlpha|ExifTool)\s*\(",
     "with exiftool.ExifTool(executable=path) as et:", "class ExifToolSession(exiftool.ExifToolHelper", ""),
    # And every ExifToolSession is field_values' (opened, oracle), which starts it knowing TagPup's namespace: four read
    # sites and three write sites each opened their own, read a bake's fields under no configuration, and a test standing
    # in for ExifTool had five doors to stand at (docs/plans/own-metadata-library.md, L0a and L0b).
    (SESSION_OUTSIDE, FIELD_VALUES, r"(?<!class )\bExifToolSession\(",
     "with exiftool_session.ExifToolSession(executable=exiftool_path) as et:", "exiftool_session.ExifToolSession(", ""),
    # A session that stays dead after a timeout is Fields.together()'s, one block an operation: three writers wrapped
    # their own by hand, each its own span (docs/plans/own-metadata-library.md, L0b).
    ("StaysDead outside field_values", FIELD_VALUES, r"\bStaysDead\(", "et = exiftool_session.StaysDead(session)",
     "exiftool_session.StaysDead(", ""),
    # A photo's fields are written by one writer, field_values.write: the indexer's identity mint built its own set_tags,
    # outside the writer's clears, its minor-error rule and its guard (#1473, #1418). An execute of a "-TAG=value" is a
    # write too. Not even field_values overwrites a photo in place (OVERWRITES).
    ("an ExifTool write outside field_values", FIELD_VALUES, r"\.set_tags\(|-overwrite_original|\.execute\([^)]*[\"']-[\w:-]+<?=",
     'et.execute("-XMP:Subject=Quay", path)', ".set_tags(", ""),
    # A photo's fields are read by one reader, field_values.read: Smart Rename read its own, a command a photo, beside it
    # (#3377). It batches, reads a refused batch a photo at a time and gives up at a share that does not answer.
    ("an ExifTool read outside field_values", FIELD_VALUES, r"\.get_tags\(", 'meta = et.get_tags([path], tags=FIELDS)',
     "et.get_tags(", ""),
    # A JPEG is patched in place by field_values.write alone, which gives it the journal's `saving` and its picture
    # rules. A file opened saying what others may do while it is open (the patch's, sharing nothing; a video's piece read,
    # sharing a rename and a delete too) is lock_owners.opened, the one CreateFileW of the code.
    ("a JPEG patched outside field_values.write", FIELD_VALUES, r"(?<!def )\bpatch_jpeg\(",
     "done = in_place.patch_jpeg(path, serialise, saving)", "in_place.patch_jpeg(", ""),
    ("a clip patched outside field_values.write", FIELD_VALUES, r"(?<!def )\bpatch_clip\(",
     "stale = in_place.patch_clip(path, packet, saving)", "in_place.patch_clip(", ""),
    ("a file opened by CreateFileW outside lock_owners.opened", LOCK_OWNERS, r"\bCreateFileW\b",
     'create = kernel.CreateFileW', ".CreateFileW", ""),
    # Other processes are started, run and stopped through tagpup.core.processes: every spawn decided for itself
    # whether the child got a console window, and a test run put a terminal window on the desktop for every server it
    # started and every reaper (#124).
    ("subprocess called directly", PROCESSES, r"subprocess\.(Popen|run|call|check_output|check_call)\(", "subprocess.run(c)",
     "Popen(", "all"),
    ("a console flag or taskkill spelled", PROCESSES, r"creationflags|CREATE_NO_WINDOW|DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP|\btaskkill\b|\btasklist\b",
     "flags = CREATE_NO_WINDOW", "CREATE_NO_WINDOW", "all"),
)
#: ExifTool told to write over a photo, which no shipped file says, field_values included: a write is made beside the photo
#: (`-o`) and moved over it only once its picture is the photo's (field_values._landed; #2377). Without the option ExifTool
#: leaves an _original copy beside the photo (#1496).
OVERWRITES = "-overwrite_original"
#: Lines no line rule may match.
NOT_FORBIDDEN = ('execute("SELECT 1 FROM photos WHERE people LIKE ?")', 'x = tag.split("/")',
                 "from exiftool_session import ExifToolSession")
#: Lines the pyexiftool rule may not match: an ExifToolSession is no pyexiftool class constructed (field_values' own is
#: SESSION_OUTSIDE's to judge).
NOT_PYEXIFTOOL = ("with ExifToolSession(executable=executable) as et:",)


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
                for number, line in lines_of(relative):
                    if not pattern.search(line) or NOT_A_PATH in line or line.lstrip().startswith("#"):
                        continue
                    problems.append("%s:%d  %s\n    %s" % (relative, number, what, line.strip()))
        self.assertEqual([], problems, "\n\nSay it through its owner (paths.stored / key / sql_equals, ExifToolSession, "
                         "field_values.opened, tagpup.core.processes):\n\n" + "\n".join(problems))

    def test_no_photo_is_overwritten_in_place(self):
        found = ["%s:%d" % (relative, number) for relative in python_sources() for number, line in lines_of(relative)
                 if OVERWRITES in line and not line.lstrip().startswith("#")]
        self.assertEqual([], found, "a photo is written beside itself and moved over it (field_values.write)")

    def test_the_rules_see_what_they_forbid_and_their_owners_still_say_it(self):
        # A guard whose patterns quietly match nothing passes forever.
        for what, owner, pattern, hit, proof, _scope in LINE_RULES:
            with self.subTest(what, hit=hit):
                flags = re.I if "LOWER" in pattern or "LIKE" in pattern else 0
                self.assertTrue(re.search(pattern, hit, flags))
                self.assertIn(proof, read(owner))
        for line in NOT_FORBIDDEN:
            self.assertFalse(any(re.search(rule[2], line, re.I) for rule in LINE_RULES), line)
        pyexiftool = next(rule[2] for rule in LINE_RULES if rule[0] == PYEXIFTOOL)
        for line in NOT_PYEXIFTOOL:
            self.assertFalse(re.search(pyexiftool, line, re.I), line)


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

    def test_a_recorded_time_reads_back_as_it_was_written(self):
        from tagpup.core import dates
        from tagpup.services import job_runs
        at = 1790000000.0
        self.assertEqual(dates.recorded(at), job_runs.stamp(at))
        self.assertEqual(at, job_runs.seconds(dates.recorded(at)))
        self.assertRegex(dates.recorded(at), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

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
                self.assertEqual([name], [other for other, theirs in shapes.items() if fields.camera_of(theirs) == name])
        spelled = r"[\"'](Unknown Camera|All Cameras|EXIF:Model)[\"']"
        self.assertEqual([], [p for p in sources_matching(spelled, os.path.join("tagpup", "core", "fields.py"))
                              if re.search(r"[\"'](Unknown Camera|All Cameras)[\"']|get\([\"']EXIF:Model", read(p))])

    def test_the_decisions_about_a_face(self):
        """The likeness and the margin over the next person each a gate (owner, 2026-09-24 and 2026-10-17), at AdaFace's
        values read off the phase 3 gate, naming at 0.99 and offering at 0.98 (owner, 2026-10-20): 0.310 and 0.090 to
        offer, 0.520 and 0.205 to name unasked, 0.310 and 0.205 when the photo's keywords name the person."""
        import math
        from tagpup.core import clustering, validation
        adaface = clustering.cuts_of(validation.SCRFD_ADAFACE)
        self.assertEqual((0.310, 0.520, 0.205, 0.090, 0.80), (adaface.offer_a_name, adaface.name_without_asking,
                                                                adaface.name_margin, adaface.offer_margin, adaface.group_at))
        self.assertEqual((True, False, False, True, False, False, True, False),
                         (adaface.is_offered(0.310, 0.090), adaface.is_offered(0.309, 1), adaface.is_offered(0.9, 0.089),
                          adaface.names_unasked(0.520, 0.205), adaface.names_unasked(0.519, 1),
                          adaface.names_unasked(0.9, 0.204), adaface.looks_wrong(0.309), adaface.looks_wrong(0.310)))
        self.assertEqual((True, False, False), (adaface.names_from_the_tag(0.310, 0.205),
                                                adaface.names_from_the_tag(0.309, 1), adaface.names_from_the_tag(0.9, 0.204)))
        self.assertEqual(("likely", "possible", None), tuple(adaface.band(v, m) for v, m in ((0.52, 0.205), (0.4, 0.09), (0.4, 0.089))))
        self.assertTrue(adaface.names_unasked(0.520, math.inf))   # a name carried is asked no margin
        self.assertEqual((True, False, False), (adaface.gathers_for_a_new_person(0.520, 0.090),
                                                adaface.gathers_for_a_new_person(0.519, 1),
                                                adaface.gathers_for_a_new_person(0.9, 0.089)))
        # every model a library can be put on has its own cuts
        self.assertEqual(set(validation.FACE_MODELS), set(clustering.CUTS))
        for cuts in clustering.CUTS.values():
            for decide in (cuts.is_offered, cuts.names_unasked, cuts.names_from_the_tag, cuts.gathers_for_a_new_person):
                self.assertFalse(decide(None, None))   # nothing is decided about nothing
            self.assertFalse(cuts.looks_wrong(None))


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
        self.assertIn("const ALL_CAMERAS = 'All Cameras';", page)   # the page's own choice: no server reads it
        self.assertEqual([], lines_spelling(page, r"[\"'](Unknown Camera|All Cameras|EXIF:Model)[\"']",
                                            "const CAMERA_FIELDS =", "const UNKNOWN_CAMERA =", "const ALL_CAMERAS ="))


#: The photo vectors' key (tagpup.store.embeddings.model_key) is a name made from settings given, not a read of the
#: library: a function naming two (a switch's before and after, settings.photo_switch_on) reads nothing twice.
PHOTO_KEY_MODULES = ("embeddings", "store_embeddings")


def functions_reading_the_key_twice(source):
    """[(function, lines)] of each function in `source` that calls model_key more than once (the photo vectors' key,
    PHOTO_KEY_MODULES, not counted)."""
    found = []
    for fn in ast.walk(ast.parse(source)):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
                     and getattr(n.func, "attr", getattr(n.func, "id", "")) == "model_key"
                     and getattr(getattr(n.func, "value", None), "id", None) not in PHOTO_KEY_MODULES]
            if len(lines) > 1:
                found.append((fn.name, lines))
    return found


class AFaceModelKeyIsReadOnceAndTravelsWithItsVectors(unittest.TestCase):
    """Vectors and the cuts that judge them were read under two reads of the library's face model, and a switch between
    the reads gave a result under neither (#1709, #1722-#1726; retro 2026-10-20, action 1). One read per function, and
    the key then travels with the vectors (clustering.FaceRows)."""

    def test_a_function_that_reads_the_key_twice_is_found(self):
        source = "def f(conn):\n    a = faces.model_key(conn)\n    return faces.model_key(conn), a\n"
        self.assertEqual([("f", [2, 3])], functions_reading_the_key_twice(source))
        self.assertEqual([], functions_reading_the_key_twice("def f(conn):\n    return faces.model_key(conn)\n"))
        photo = "def f(a, b):\n    return store_embeddings.model_key(**a), store_embeddings.model_key(**b)\n"
        self.assertEqual([], functions_reading_the_key_twice(photo))

    def test_no_function_in_the_package_reads_it_twice(self):
        problems = ["%s: %s reads the face model key at lines %s" % (relative, name, lines)
                    for relative in python_sources() if relative.startswith("tagpup")
                    for name, lines in functions_reading_the_key_twice(read(relative))]
        self.assertEqual([], problems)


#: The exceptions a service raises that a route answers (tagpup.web.responses.ANSWERS), by name.
ANSWERED = ("NotFound", "Conflict", "Refused", "Unavailable", "RootsError")


def answered_names():
    """ANSWERED and every class of the package that derives from one of them (NotHeld, AlreadyWorking, UnmappedRoot...),
    by the last part of each base's name."""
    classes = [(node.name, {getattr(base, "attr", getattr(base, "id", None)) for base in node.bases})
               for relative in python_sources() if relative.startswith("tagpup")
               for node in ast.walk(ast.parse(read(relative))) if isinstance(node, ast.ClassDef)]
    names, grown = set(ANSWERED), True
    while grown:
        more = {name for name, bases in classes if bases & names} - names
        names |= more
        grown = bool(more)
    return names


def own_statuses(source, answered):
    """(line, what) for each place in `source` that answers a service's exception with a status of its own: an `except`
    naming one of `answered` whose arm spells a status number, or an isinstance test against one."""
    def named(node):
        return {getattr(each, "attr", getattr(each, "id", None))
                for each in (node.elts if isinstance(node, ast.Tuple) else [node])}
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ExceptHandler) and node.type is not None and named(node.type) & (answered | {"ANSWERED"}):
            if any(isinstance(each, ast.Constant) and type(each.value) is int and 400 <= each.value < 600
                   for line in node.body for each in ast.walk(line)):
                found.append((node.lineno, "except %s" % ", ".join(sorted(named(node.type) & answered))))
        elif (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "isinstance" and len(node.args) == 2
              and named(node.args[1]) & answered):
            found.append((node.lineno, "isinstance %s" % ", ".join(sorted(named(node.args[1]) & answered))))
    return found


class ARouteAnswersWhatAServiceRaisedThroughTheOneMapper(unittest.TestCase):
    """Conflict was answered 409 by hand at nine sites, NotFound and Refused at a dozen more, a NotFound as Flask's HTML
    page at some: a route catching fewer than its service raised answered 500, and the pages read two shapes (#2743).
    tagpup.web.responses owns the mapping (ANSWERS, of, abort_with); a route catches responses.ANSWERED."""

    def test_a_route_that_spells_its_own_status_is_found(self):
        answered = answered_names()
        self.assertTrue({"NotHeld", "AlreadyWorking", "UnmappedRoot", "SandboxError"} <= answered, answered)
        planted = ("def a():\n    try:\n        f()\n    except Conflict as why:\n        return responses.error(409, str(why))\n"
                   "def b():\n    try:\n        f()\n    except (Refused, paths.RootsError):\n        abort(400)\n"
                   "def c(why):\n    if isinstance(why, NotFound):\n        return 404\n"
                   "def d():\n    try:\n        f()\n    except naming_faces.AlreadyWorking as why:\n"
                   "        return responses.error(409, str(why), job=why.job)\n"
                   "def e():\n    try:\n        f()\n    except responses.ANSWERED as why:\n        return responses.error(409, str(why))\n")
        self.assertEqual([4, 9, 12, 17, 22],[line for line, _what in own_statuses(planted, answered)])
        mapped = ("def a():\n    try:\n        f()\n    except ValueError as why:\n        return responses.error(400, str(why))\n"
                  "    except responses.ANSWERED as why:\n        return responses.of(why)\n")
        self.assertEqual([], own_statuses(mapped, answered))

    def test_no_route_module_maps_one_itself(self):
        answered = answered_names()
        web = os.path.join("tagpup", "web")
        problems = ["%s:%d: %s" % (relative, line, what) for relative in python_sources()
                    if relative.startswith(web) and relative != os.path.join(web, "responses.py")
                    for line, what in own_statuses(read(relative), answered)]
        self.assertEqual([], problems)


#: The functions of tagpup/web that may tell what the server keeps of a photo a write renamed, and what each may tell:
#: `_followed_rename`, the one follow-up of every rename (Suggest's looks, the folder maps' records moved, their stamps),
#: a folder's Smart Rename's included (#2852, #2892).
RENAME_FOLLOWERS = {"_followed_rename": {"looks", "records"}}


def rename_followups(source):
    """[(function, line, what)] of each place in `source` that tells what the server keeps of a renamed photo: a call
    of `.renamed(` (Suggest's looks, "looks") or a record's "path" or "filename" set (a folder map's record moved,
    "records"), by the innermost function holding it."""
    found = []

    class Visit(ast.NodeVisitor):
        def __init__(self):
            self.within = ["<module>"]

        def visit_FunctionDef(self, node):
            self.within.append(node.name)
            self.generic_visit(node)
            self.within.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            if getattr(node.func, "attr", None) == "renamed":
                found.append((self.within[-1], node.lineno, "looks"))
            self.generic_visit(node)

        def visit_Assign(self, node):
            for target in node.targets:
                for each in (target.elts if isinstance(target, ast.Tuple) else [target]):
                    if (isinstance(each, ast.Subscript) and isinstance(each.slice, ast.Constant)
                            and each.slice.value in ("path", "filename")):
                        found.append((self.within[-1], node.lineno, "records"))
            self.generic_visit(node)

    Visit().visit(ast.parse(source))
    return found


class ARenamedPhotoIsFollowedInOnePlace(unittest.TestCase):
    """The follow-up of a photo's rename -- Suggest's looks kept under the new name, the folder maps' records moved to it,
    their stamps taken -- was written three times in tagpup_routes (a Smart Rename of one photo, a save renaming it after
    its title, a clip's conversion), and a fourth write renaming a photo would have written a fourth that could skip a
    step (#2852); the folder's Smart Rename, which told the looks and read its own folder again, skipped an ancestor's
    scan (#2892). `_followed_rename` is the one; a write that renames a photo calls it
    (tests/test_what_the_server_keeps_follows_a_rename.py holds each caller to what it does)."""

    def test_a_follow_up_of_its_own_is_found(self):
        planted = ("def own(library, old, new):\n    runs.renamed({old: new})\n"
                   "    for held, record in cache.entries_for(old):\n        record['path'] = new\n"
                   "        record['filename'], x = name, 1\n"
                   "def _followed_rename(library, old, new, renamed):\n    record['mtime'] = 1\n")
        self.assertEqual([("own", 2, "looks"), ("own", 4, "records"), ("own", 5, "records")], rename_followups(planted))

    def test_no_route_module_follows_a_rename_itself(self):
        web = os.path.join("tagpup", "web")
        found = [(relative, name, line, what) for relative in python_sources() if relative.startswith(web)
                 for name, line, what in rename_followups(read(relative))]
        problems = ["%s:%d: %s tells the %s of a renamed photo itself" % (relative, line, name, what)
                    for relative, name, line, what in found if what not in RENAME_FOLLOWERS.get(name, ())]
        self.assertEqual([], problems, "call tagpup_routes._followed_rename")
        self.assertEqual({"looks", "records"}, {what for _r, name, _l, what in found if name == "_followed_rename"},
                         "the follow-up still tells both: the guard sees what it forbids")


if __name__ == "__main__":
    unittest.main()
