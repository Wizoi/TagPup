"""The domain owners' charters (.claude/agents/tagpup-owner-*.md) name real paths.

A charter lists its domain's files and the tests that guard it. A module that is renamed or
deleted leaves the charter pointing at nothing, and the owner agent then reviews against a map
that is wrong. This fails the build on any backticked repository path in a charter that does
not exist (a `*` is a glob), so the commit that moves a module changes its charter too.
Dotted module names and prose are not paths and are not checked.
"""
import glob
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shipped_sources import ROOT  # noqa: E402

AGENTS = os.path.join(ROOT, ".claude", "agents")
OWNERS = ("data", "people", "files", "views-ai", "integration")

#: A backticked token is a repository path when it starts with one of these.
PATH = re.compile(r"^(?:(?:tagpup|tests|web|scripts|tools|docs|\.claude)/\S+|tagpup_cli\.py|tagpup_web\.py)$")
TICKED = re.compile(r"`([^`\n]+)`")
SCOPE_HEAD = "## Scope"


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


class OwnerCharters(unittest.TestCase):
    def test_every_domain_has_a_charter_with_a_scope(self):
        for owner in OWNERS:
            with self.subTest(owner=owner):
                text = read(charter_path(owner))
                self.assertIn(SCOPE_HEAD, text)
                self.assertGreaterEqual(len(paths_in(scope_of(text))), 5, "a Scope that names almost nothing")

    def test_every_path_a_charter_names_exists(self):
        for owner in OWNERS:
            text = read(charter_path(owner))
            for token in paths_in(text):
                with self.subTest(owner=owner, path=token):
                    self.assertTrue(exists(token), "%s names %s, which is not there: change the charter in the commit that moves it" % (os.path.basename(charter_path(owner)), token))

    def test_a_charter_reviews_and_edits_nothing(self):
        for owner in OWNERS:
            with self.subTest(owner=owner):
                text = read(charter_path(owner))
                self.assertIn("name: tagpup-owner-%s\n" % owner, text)
                tools = re.search(r"^tools: (.*)$", text, re.M).group(1)
                self.assertEqual(tools, "Read, Grep, Glob, Bash")

    def test_the_owners_page_names_every_charter(self):
        page = read(os.path.join(ROOT, "docs", "OWNERS.md"))
        for owner in OWNERS:
            with self.subTest(owner=owner):
                self.assertIn("tagpup-owner-%s" % owner, page)

    def test_the_check_would_notice_a_missing_path(self):
        self.assertEqual(paths_in("`tagpup/store/db.py` and `tagpup.store.db` and `no/such.py`"), ["tagpup/store/db.py"])
        self.assertFalse(exists("tagpup/store/not_a_module.py"))
        self.assertTrue(exists("tagpup/store/db*.py"))


if __name__ == "__main__":
    unittest.main()
