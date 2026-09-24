"""The Python files TagPup ships, for the tests that hold a rule across all of them.

Each single-owner guard used to list the files it checked for itself, and the lists
differed: the database guard skipped runner.py, which opened its own connection
unseen. One list means a rule covers every file, including each module as it moves
into tagpup/.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LAUNCHERS = ["tagpup_cli.py", "runner.py", "tagtuner.py", "tagpup_gui.py"]


def python_sources():
    """Repo-relative paths: the launchers, scripts/*.py, and every module in tagpup/."""
    found = [name for name in LAUNCHERS if os.path.exists(os.path.join(ROOT, name))]
    scripts = os.path.join(ROOT, "scripts")
    found += [os.path.join("scripts", name) for name in sorted(os.listdir(scripts))
              if name.endswith(".py")]
    for folder, dirs, names in os.walk(os.path.join(ROOT, "tagpup")):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        found += [os.path.relpath(os.path.join(folder, name), ROOT)
                  for name in sorted(names) if name.endswith(".py")]
    return found
