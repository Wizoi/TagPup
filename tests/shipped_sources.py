"""The Python files TagPup ships, for the tests that hold a rule across all of them.

Each single-owner guard used to list the files it checked for itself, and the lists
differed: the database guard skipped runner.py, which opened its own connection
unseen. One list means a rule covers every file, including each module as it moves
into tagpup/.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(ROOT, "scripts"))
from code_snapshot import LAUNCHERS  # noqa: E402  -- the list the installer copies


def python_sources():
    """Repo-relative paths: the launchers, scripts/*.py, tools/*.py, and every module in
    tagpup/. tools/ is not installed, but it opens the owner's libraries all the same."""
    found = [name for name in LAUNCHERS if os.path.exists(os.path.join(ROOT, name))]
    for folder in ("scripts", "tools"):
        if os.path.isdir(os.path.join(ROOT, folder)):
            found += [os.path.join(folder, name) for name in sorted(os.listdir(os.path.join(ROOT, folder)))
                      if name.endswith(".py")]
    for folder, dirs, names in os.walk(os.path.join(ROOT, "tagpup")):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        found += [os.path.relpath(os.path.join(folder, name), ROOT)
                  for name in sorted(names) if name.endswith(".py")]
    return found


def page_source(page):
    """A page's modules (web/<page>/*.js), one after another: where a guard looks for
    the page's copy of a rule, whichever module holds it."""
    folder = os.path.join(ROOT, "web", page)
    texts = []
    for name in sorted(os.listdir(folder)):
        if name.endswith(".js"):
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                texts.append(f.read())
    return "\n".join(texts)
