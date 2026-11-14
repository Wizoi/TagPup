"""Delete the failed changes of a library's journal that hold nothing (#2996): status `failed`, no file in change_files, no
row in change_rows. A one-off: a write that wrote nothing is now erased by the writer itself (file_journal.finish), so no
such change appears again; this clears the ones made before (tagpup.store.file_journal.erase_empty_failed).

Dry run by default: the count and the operations they were, nothing written. With --apply, each is deleted, checked again
inside the transaction that deletes it (a change that gained a file or a row meanwhile stays), and the count deleted is
said, then the dry-run count after. Counts only. Back the library up first.

    .venv/Scripts/python.exe scripts/delete_empty_failed_changes.py --library photo_index
    .venv/Scripts/python.exe scripts/delete_empty_failed_changes.py --library photo_index --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import file_changes  # noqa: E402


def library_at(named):
    """The library's file: a name in the data folder (photo_index), or a path to a .db."""
    if os.path.isabs(named) or os.sep in named or "/" in named:
        return named
    return tagpup_config.library_path(named if named.endswith(".db") else named + ".db")


def by_operation(counts):
    return ", ".join("%s %d" % (operation, n) for operation, n in counts.items()) or "none"


def say(line):
    print(line, flush=True)


def run(db_path, apply, out=say):
    """Say how many empty failed changes `db_path` holds, or delete them with `apply`. Returns the exit code."""
    if not os.path.isfile(db_path):
        out("There is no library at the path given.")
        return 1
    library = Library(db_path)
    found = file_changes.empty_failed_changes(library)
    out("%d empty failed change(s): %s." % (sum(found.values()), by_operation(found)))
    if not apply:
        out("A dry run: nothing was written. --apply deletes them, after a backup of the library.")
        return 0
    deleted = file_changes.empty_failed_changes(library, erase=True)
    out("wrote %d (deleted %s)." % (sum(deleted.values()), by_operation(deleted)))
    out("dry run now: %d left." % sum(file_changes.empty_failed_changes(library).values()))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", required=True, help="the library: its name in the data folder, or its .db file")
    parser.add_argument("--apply", action="store_true", help="delete them; without it, count only")
    args = parser.parse_args(argv)
    return run(library_at(args.library), args.apply)


if __name__ == "__main__":
    sys.exit(main())
