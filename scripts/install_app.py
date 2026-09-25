"""Install the apps from this checkout, so what you run is not the code being edited.

Run from the repository, TagPup and TagTuner restart whenever a .py file is saved: the
reloader drops the folder queue and the identify cache and can orphan an indexer, and
a half-made change is what is running. This copies the code into a version folder of
its own and writes launchers that run that copy, with TAGPUP_HOME set to this checkout
so that data/ -- the libraries, which hold their own settings -- stays where it is.
Nothing is copied or expected beside it: config.ini is read only to stamp a library
that holds no settings yet (tagpup.config). Saving a file here then changes
nothing that is running. Installing again makes a new version and moves the launchers
to it; the two before it are kept, to go back to by editing current.txt.

    .venv/Scripts/python.exe scripts/install_app.py            # shows what it would do
    .venv/Scripts/python.exe scripts/install_app.py --apply

Start the apps with the launchers it writes: TagPup.cmd and TagTuner.cmd (one server
for both; the second started opens its page in the running one), TagPup Runner.cmd,
and TagPup CLI.cmd for indexing and the other CLI commands.
"""
import argparse
import datetime
import os
import shutil
import stat
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from code_snapshot import REPO_ROOT, copy_code  # noqa: E402
from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core import processes  # noqa: E402

#: Versions kept: the new one and the two before it.
KEEP = 3

#: Launcher file -> the program it starts, and its arguments.
LAUNCHERS = {
    "TagPup.cmd": ("tagpup_web.py", "--open tagpup"),
    "TagTuner.cmd": ("tagpup_web.py", "--open tuner"),
    "TagPup Runner.cmd": ("runner.py", ""),
    "TagPup CLI.cmd": ("tagpup_cli.py", ""),
}

LAUNCHER = (
    "@echo off\r\n"
    "rem Written by scripts/install_app.py. Runs the installed {script} with this home.\r\n"
    'set "TAGPUP_HOME={home}"\r\n'
    'set /p TAGPUP_VERSION=<"%~dp0current.txt"\r\n'
    'cd /d "%TAGPUP_HOME%"\r\n'
    '"{python}" "%~dp0versions\\%TAGPUP_VERSION%\\{script}" {args} %*\r\n'
)


def default_destination():
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "TagPup")


def default_python():
    """The checkout's virtualenv, which has the app's packages; else this interpreter."""
    venv = os.path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")
    return venv if os.path.exists(venv) else sys.executable


def git(*args):
    try:
        return processes.run(["git", "-C", REPO_ROOT] + list(args), capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def version_name(now=None):
    """When it was installed, from which commit, and whether uncommitted code came too."""
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d-%H%M%S")
    commit = git("rev-parse", "--short", "HEAD") or "nogit"
    dirty = "-uncommitted" if git("status", "--porcelain", "--untracked-files=no") else ""
    return "%s-%s%s" % (stamp, commit, dirty)


def is_link(path):
    """A symlink or an NTFS junction. A recursive delete must never go through either."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def read_current(destination):
    try:
        with open(os.path.join(destination, "current.txt"), encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return None


def versions(destination):
    folder = os.path.join(destination, "versions")
    if not os.path.isdir(folder):
        return []
    return sorted(name for name in os.listdir(folder)
                  if os.path.isdir(os.path.join(folder, name)))


def to_remove(existing, new, previous):
    """The versions to delete: all but the newest KEEP, never the new or the previous one."""
    ordered = sorted(set(existing) | {new})
    return [name for name in ordered[:-KEEP] if name not in (new, previous)]


def install(destination, home, python, name=None, apply=False, say=print):
    """Install a new version. Returns (the version's name, the versions removed)."""
    name = name or version_name()
    folder = os.path.join(destination, "versions", name)
    while os.path.exists(folder):   # two installs in one second
        name += "+"
        folder = os.path.join(destination, "versions", name)
    previous = read_current(destination)
    removing = to_remove(versions(destination), name, previous)

    say("install      %s" % folder)
    say("home         %s  (data/)" % home)
    say("python       %s" % python)
    say("launchers    %s" % ", ".join(os.path.join(destination, n) for n in LAUNCHERS))
    if previous:
        say("replacing    %s" % previous)
    for old in removing:
        say("removing     %s" % os.path.join(destination, "versions", old))
    if not apply:
        say("\nDry run. Nothing was changed. Re-run with --apply to install.")
        return name, []

    copy_code(folder, REPO_ROOT, launchers=True)
    with open(os.path.join(folder, "VERSION.txt"), "w", encoding="utf-8") as handle:
        handle.write("%s\nfrom %s\n" % (name, REPO_ROOT))
    for launcher, (script, args) in LAUNCHERS.items():
        # newline="" keeps the CRLFs the template already has: cmd.exe wants them.
        with open(os.path.join(destination, launcher), "w", encoding="utf-8", newline="") as handle:
            handle.write(LAUNCHER.format(home=home, python=python, script=script, args=args))
    current = os.path.join(destination, "current.txt")
    with open(current + ".writing", "w", encoding="utf-8") as handle:
        handle.write(name)
    os.replace(current + ".writing", current)

    removed = []
    for old in removing:
        path = os.path.join(destination, "versions", old)
        if is_link(path):
            say("left alone   %s (a link, not a version this made)" % path)
            continue
        shutil.rmtree(path, ignore_errors=True)
        if os.path.exists(path):
            say("could not remove %s; a program started from it may still be running" % path)
        else:
            removed.append(old)
    say("\nInstalled %s. Start the apps with the launchers above." % name)
    return name, removed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="install; the default is a dry run")
    parser.add_argument("--to", default=default_destination(),
                        help="where to install (default: %(default)s)")
    parser.add_argument("--home", default=tagpup_config.home(),
                        help="TAGPUP_HOME for the installed apps (default: %(default)s)")
    parser.add_argument("--python", default=default_python(),
                        help="the interpreter the launchers use (default: %(default)s)")
    args = parser.parse_args(argv)
    install(os.path.abspath(args.to), os.path.abspath(args.home), args.python, apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
