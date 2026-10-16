"""The map of the tests is true: every test file has one domain owner, the tiers name real files, and the owners'
charters name real paths.

Owners (tests/owners.py): a test file added without an owner is named here; a scenario file names scenarios that exist;
a file named for an owner (data_, people_, files_, views_, apps_) is that owner's; the tier file (tests/tiers.py) and the
map agree about which files run a flow; the files nobody could place (kind unmapped) only go down in number; and a
finding a test cites (#NNNN) is not dropped from the tests unless its number is in owners.RETIRED -- a finding is a bug
that cost time, and the test that cites it is its memory (the freeze is tests/findings_cited.txt).

Tiers: tests/tiers.py names test files that exist, each in one tier; a rename of a scenario file would otherwise leave
its old name behind and move it to the fast tier without a word.

Charters (.claude/agents/tagpup-owner-*.md): a charter lists its domain's files and the tests that guard it. A module
that is renamed or deleted leaves the charter pointing at nothing, and the owner agent then reviews against a map that is
wrong. This fails the build on any backticked repository path in a charter that does not exist (a `*` is a glob), so the
commit that moves a module changes its charter too. Dotted module names and prose are not paths and are not checked.

Every module of the package (tagpup/, but its __init__.py files) and every module of the pages (web/**/*.js) is in one
charter's Scope: a module in none was reviewed by nobody (services/tagging.py, store/inspection.py and mcp/__main__.py
were, #1368; 58 of the 99 page modules were, #1449). A module two Scopes each claim a part of is listed in SHARED, which
only goes down.

Lifted: test_guard_tests_have_one_owner, test_tiers_name_real_files and test_owner_charters_name_real_paths, every
assertion kept. Mutation: a test file with no entry in owners.py, a tier naming a missing file, and a charter naming a
missing path each fail the matching test below.
"""
import fnmatch
import glob
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import owners  # noqa: E402
import tiers  # noqa: E402
from shipped_sources import ROOT  # noqa: E402

TESTS = os.path.join(ROOT, "tests")
FRONTEND = os.path.join(TESTS, "frontend")
#: Files nobody could place by what they import or how they are named (owners.py, kind unmapped). Lower it when one is placed.
UNMAPPED_TODAY = 0
CITES = re.compile(r"#(\d{2,4})\b")

AGENTS = os.path.join(ROOT, ".claude", "agents")
CHARTERS = ("data", "people", "files", "views-ai", "integration")
#: A charter's name for the owner tests/owners.py calls it.
IN_THE_MAP = dict(zip(CHARTERS, owners.OWNERS))

#: A backticked token is a repository path when it starts with one of these.
PATH = re.compile(r"^(?:(?:tagpup|tests|web|scripts|tools|docs|\.claude)/\S+|tagpup_cli\.py|tagpup_web\.py)$")
TICKED = re.compile(r"`([^`\n]+)`")
SCOPE_HEAD = "## Scope"
#: A Scope line that names what is not the domain's, or is gone.
NOT_CLAIMED = ("Not yours", "Gone")

#: Modules two charters' Scopes each own a part of, as the Scopes say: the composition root, the word index's query
#: side and its writers, a server's routes split by domain. It only goes down: a module newly in two Scopes fails.
SHARED = {
    "tagpup/runtime.py": ("integration", "views-ai"),
    "tagpup/store/search_index.py": ("data", "views-ai"),
    "tagpup/web/tagpup_routes.py": ("integration", "views-ai"),
    "tagpup/web/tuner_routes.py": ("integration", "people"),
}


def test_files():
    """tests/test_*.py by module name, tests/frontend/*.test.mjs by file name."""
    return ({n[:-3] for n in os.listdir(TESTS) if n.startswith("test_") and n.endswith(".py")}
            | {n for n in os.listdir(FRONTEND) if n.endswith(".test.mjs")})


def cited(names):
    found = set()
    for name in names:
        path = os.path.join(TESTS, name + ".py") if name.startswith("test_") else os.path.join(FRONTEND, name)
        with open(path, encoding="utf-8") as handle:
            found |= set(CITES.findall(handle.read()))
    return found


def charter_path(owner):
    return os.path.join(AGENTS, "tagpup-owner-%s.md" % owner)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def paths_in(text):
    return [token for token in TICKED.findall(text) if PATH.match(token) and not set(token) & set("<>{} ")]


def exists(token):
    target = os.path.join(ROOT, *token.split("/"))
    return bool(glob.glob(target)) if "*" in token else os.path.exists(target)


def scope_of(text):
    """The text of the Scope section: from its heading to the next heading."""
    start = text.index(SCOPE_HEAD) + len(SCOPE_HEAD)
    end = text.find("\n## ", start)
    return text[start:end if end >= 0 else len(text)]


def scope_claims(owner):
    """The repository paths (and globs) a charter's Scope claims: a line naming what is not the domain's left out."""
    lines = scope_of(read(charter_path(owner))).splitlines()
    return paths_in("\n".join(line for line in lines if not line.lstrip("- ").startswith(NOT_CLAIMED)))


def package_modules():
    """tagpup/**/*.py, but the __init__.py files, as charters spell them."""
    found = []
    for here, folders, names in os.walk(os.path.join(ROOT, "tagpup")):
        folders[:] = [f for f in folders if f != "__pycache__"]
        found += [os.path.relpath(os.path.join(here, n), ROOT).replace(os.sep, "/") for n in names
                  if n.endswith(".py") and n != "__init__.py"]
    return sorted(found)


def page_modules():
    """web/**/*.js, the pages' ES modules, as charters spell them."""
    found = []
    for here, folders, names in os.walk(os.path.join(ROOT, "web")):
        folders[:] = [f for f in folders if f != "node_modules"]
        found += [os.path.relpath(os.path.join(here, n), ROOT).replace(os.sep, "/") for n in names if n.endswith(".js")]
    return sorted(found)


def claimed_by(module, token):
    return fnmatch.fnmatch(module, token) or module.startswith(token.rstrip("/") + "/")


class EveryTestFileHasOneOwner(unittest.TestCase):
    def test_every_test_file_is_in_the_map_and_the_map_names_only_test_files(self):
        here = test_files()
        self.assertEqual([], sorted(here - set(owners.FILES)), "add the file to _FILES in tests/owners.py, under its owner")
        self.assertEqual([], sorted(set(owners.FILES) - here), "owners.py names a test file that is not there")
        for name, (owner, kind, ids) in owners.FILES.items():
            with self.subTest(name):
                self.assertIn(owner, owners.OWNERS)
                self.assertIn(kind, owners.KINDS)
                prefix = re.match(r"(data|people|files|views|apps)_", name)
                self.assertTrue(not prefix or prefix.group(1) == owner, "a file named for an owner is that owner's")

    def test_a_scenario_file_names_scenarios_that_exist_and_are_its_owners(self):
        for name, (owner, kind, ids) in owners.FILES.items():
            with self.subTest(name):
                self.assertEqual(kind == "scenario", bool(ids), "a scenario file names its ids, and only it does")
                for sid in ids:
                    self.assertIn(sid, owners.SCENARIOS)
                if ids:
                    self.assertEqual(owner, owners.SCENARIOS[ids[0]][0], "a file's owner is its first scenario's")
        self.assertEqual({o for o, _s in owners.SCENARIOS.values()}, set(owners.OWNERS))

    def test_the_tier_file_and_the_map_agree_about_the_files_that_run_a_flow(self):
        long = tiers.SCENARIO | tiers.SLOW
        self.assertEqual([], sorted(n for n in long if owners.FILES[n][1] != "scenario"),
                         "a file the tiers call a scenario or slow is kind scenario in owners.py (name its scenario)")

    def test_the_files_nobody_placed_only_go_down(self):
        unmapped = sorted(n for n, (_o, kind, _i) in owners.FILES.items() if kind == "unmapped")
        self.assertEqual(UNMAPPED_TODAY, len(unmapped), "unmapped files now %s: place one and lower UNMAPPED_TODAY; "
                                                         "a new file is placed by its owner, not left here" % unmapped)

    def test_a_finding_a_test_cites_is_not_dropped_unless_retired(self):
        frozen = set(read(os.path.join(TESTS, "findings_cited.txt")).split())
        lost = frozen - cited(test_files()) - {str(n) for n in owners.RETIRED}
        self.assertEqual([], sorted(lost, key=int), "tests no longer cite these findings; put the number in owners.RETIRED "
                                                    "if its test is truly redundant (and say why in the commit)")


class TiersNameRealFiles(unittest.TestCase):
    def test_every_name_is_a_test_file_in_one_tier_and_tier_of_says_which(self):
        for name in sorted(tiers.SLOW | tiers.SCENARIO):
            self.assertTrue(os.path.exists(os.path.join(TESTS, name + ".py")), name + " is not a test file")
        self.assertEqual(set(), set(tiers.SLOW) & set(tiers.SCENARIO))
        self.assertEqual("slow", tiers.tier_of(sorted(tiers.SLOW)[0]))
        self.assertEqual("scenario", tiers.tier_of(sorted(tiers.SCENARIO)[0]))
        self.assertEqual("fast", tiers.tier_of("test_fictional_anything"))


class OwnerCharters(unittest.TestCase):
    def test_every_domain_has_a_charter_with_a_scope(self):
        for owner in CHARTERS:
            with self.subTest(owner=owner):
                text = read(charter_path(owner))
                self.assertIn(SCOPE_HEAD, text)
                self.assertGreaterEqual(len(paths_in(scope_of(text))), 5, "a Scope that names almost nothing")

    def test_every_path_a_charter_names_exists(self):
        for owner in CHARTERS:
            text = read(charter_path(owner))
            for token in paths_in(text):
                with self.subTest(owner=owner, path=token):
                    self.assertTrue(exists(token), "%s names %s, which is not there: change the charter in the commit that moves it" % (os.path.basename(charter_path(owner)), token))

    def test_a_charter_reviews_and_edits_nothing(self):
        for owner in CHARTERS:
            with self.subTest(owner=owner):
                text = read(charter_path(owner))
                self.assertIn("name: tagpup-owner-%s\n" % owner, text)
                tools = re.search(r"^tools: (.*)$", text, re.M).group(1)
                self.assertEqual(tools, "Read, Grep, Glob, Bash")

    def test_a_charter_lists_the_scenarios_the_map_gives_its_owner(self):
        for owner in CHARTERS:
            with self.subTest(owner=owner):
                line = re.search(r"^Scenario ids: (.*)$", read(charter_path(owner)), re.M)
                self.assertIsNotNone(line, "a charter ends with its scenario ids (tests/owners.py)")
                mine = {i for i, (o, _sentence) in owners.SCENARIOS.items() if o == IN_THE_MAP[owner]}
                self.assertEqual(mine, set(re.findall(r"\b[A-Z]\d+\b", line.group(1))))

    def test_every_module_of_the_package_and_the_pages_is_in_one_charters_scope(self):
        claims = {owner: scope_claims(owner) for owner in CHARTERS}
        modules = package_modules() + page_modules()
        self.assertGreater(len(page_modules()), 90, "the walk of web/ found almost no page module")
        owners_of = {module: tuple(sorted(owner for owner in CHARTERS if any(claimed_by(module, t) for t in claims[owner])))
                     for module in modules}
        self.assertEqual([], [m for m, found in owners_of.items() if not found],
                         "a module in no charter's Scope: add it to its owner's, in the commit that makes it")
        self.assertEqual(SHARED, {m: found for m, found in owners_of.items() if len(found) > 1},
                         "a module in two charters' Scopes: one owns it, or both say which part each has (SHARED)")

    def test_the_owners_page_names_every_charter(self):
        page = read(os.path.join(ROOT, "docs", "OWNERS.md"))
        for owner in CHARTERS:
            with self.subTest(owner=owner):
                self.assertIn("tagpup-owner-%s" % owner, page)

    def test_every_charter_ends_with_the_owners_checklist_in_the_words_of_owners_md(self):
        # An agent reads its own definition and opens OWNERS.md only if it chooses to, so the seven conditions and the
        # order of a review stand in each charter; this holds the six copies to one text (retro 2026-10-16).
        def checklist(text):
            found = re.findall(r"^\*\*Standing goal: the domain is healthy\*\*.*?Say so in the review\.$", text, re.M | re.S)
            self.assertEqual(1, len(found), "one checklist, from **Standing goal** to 'Say so in the review.'")
            return found[0]

        model = checklist(read(os.path.join(ROOT, "docs", "OWNERS.md")))
        self.assertEqual(7, len(re.findall(r"^\d\. ", model, re.M)))
        for owner in CHARTERS:
            with self.subTest(owner=owner):
                self.assertEqual(model, checklist(read(charter_path(owner))),
                                 "the charter's checklist differs from docs/OWNERS.md: change all six together")

    def test_the_check_would_notice_a_missing_path(self):
        self.assertEqual(paths_in("`tagpup/store/db.py` and `tagpup.store.db` and `no/such.py`"), ["tagpup/store/db.py"])
        self.assertFalse(exists("tagpup/store/not_a_module.py"))
        self.assertTrue(exists("tagpup/store/db*.py"))


if __name__ == "__main__":
    unittest.main()
