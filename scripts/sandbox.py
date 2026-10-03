"""What the measurement scripts share: a private copy of a library, a free port, a clean-up that tells.

scripts/measure_identify_faces.py, measure_suggest_folder.py and measure_grid.py each run the app in a
sandbox: the library copied through SQLite's backup API under a temporary TAGPUP_HOME, the library's roots
placed at empty sandbox folders by the sandbox's own machine map, a server on a free port, all deleted
afterwards. A script may not import another script, so what they share is here, a helper of scripts/
(tests/test_scripts_are_entry_points.py, HELPERS) beside code_snapshot, which copies the code.
"""
import os
import shutil
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup.store import db as tagpup_db  # noqa: E402
from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402


def free_port():
    """A port nothing is on, so a run can never collide with a server you are using."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def copy_library(source_db, target):
    """Copy a library to `target` through SQLite's backup API, the original opened read-only.

    A live library has a write-ahead log beside it, and copying the .db on its own captures a file
    that is missing whatever is still in the WAL. Returns the size of the copy in GB."""
    started = time.time()
    source = tagpup_db.connect(tagpup_db.readonly_uri(source_db), uri=True)
    destination = tagpup_db.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    size = os.path.getsize(target) / 1e9
    print("  copied %.1f GB in %.1fs" % (size, time.time() - started))
    return size


def place_roots(db_path, sandbox):
    """Make the sandbox's copy of the library safe if it holds roots: write the sandbox's OWN
    machine map (machine_roots.json in its home) placing each root at an empty folder of the sandbox,
    never at the real photos -- a converted library copy would otherwise point at them through the
    machine's map, and a sandbox server would read them, or write a tag into them. Fails loudly
    (roots_service.SandboxError) when the copy has a root the sandbox did not place. {name: place}."""
    map_path = os.path.join(sandbox, tagpup_config.MACHINE_ROOTS_FILE)
    machine = roots_service.Machine(
        lambda: tagpup_config.machine_roots(map_path),
        lambda name, place: tagpup_config.add_machine_root(name, place, path=map_path),
        lambda: map_path)
    placed = roots_service.place_in_sandbox(Library(db_path), sandbox, machine)
    for name, place in placed.items():
        print("  root %s placed at %s, by the sandbox's own map" % (name, place))
    return placed


def remove_sandbox(sandbox):
    """Delete the sandbox, and say so if it cannot be.

    Windows releases a dead process's file handles a moment after it exits, so the
    first attempt can fail on a database the server still had open. Retried rather
    than ignored: this directory holds a copy of the whole library, and the first
    version of this quietly left 2.7 GB in the temp directory every run because the
    failure was swallowed.
    """
    for _attempt in range(10):
        shutil.rmtree(sandbox, ignore_errors=True)
        if not os.path.exists(sandbox):
            return True
        time.sleep(0.5)

    size = 0
    for root, _dirs, files in os.walk(sandbox):
        size += sum(os.path.getsize(os.path.join(root, f)) for f in files)
    print("\nWARNING: could not delete the sandbox. %.1f GB left at:\n  %s"
          % (size / 1e9, sandbox), file=sys.stderr)
    return False
