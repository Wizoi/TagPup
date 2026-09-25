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

Each launcher first runs this with --if-changed: when the checkout has moved to
another commit and holds no uncommitted code, that commit is installed before the app
starts, so a merge reaches the apps at their next start. A checkout in the middle of
an edit is never installed; the version already installed starts instead.
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
    'cd /d "%TAGPUP_HOME%"\r\n'
    'if exist "%TAGPUP_HOME%\\scripts\\install_app.py" '
    '"{python}" "%TAGPUP_HOME%\\scripts\\install_app.py" --apply --if-changed --to "%~dp0."\r\n'
    'set /p TAGPUP_VERSION=<"%~dp0current.txt"\r\n'
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


def stale(current, commit, dirty):
    """Should the installed version `current` be replaced by the checkout at `commit`?
    Only when the checkout's code is all committed (not `dirty`) and the version came
    from another commit, or from this one with uncommitted code."""
    if not commit or dirty:
        return False
    if not current:
        return True
    parts = current.rstrip("+").split("-")
    return len(parts) < 3 or parts[2] != commit or "uncommitted" in parts[3:]


def update(destination, home, python, say=print):
    """Install the checkout's commit if the installed version is `stale`; what a
    launcher runs before it starts its app. Never stops the app from starting: a
    failed install leaves the version there was. Returns the version installed, or None."""
    commit = git("rev-parse", "--short", "HEAD")
    dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    current = read_current(destination)
    if not stale(current, commit, dirty):
        if dirty and stale(current, commit, False):
            say("TagPup: the checkout has uncommitted changes, so %s was not installed; "
                "starting the installed version." % commit)
        return None
    say("TagPup: installing %s before starting..." % commit)
    try:
        name, _removed = install(destination, home, python, apply=True, say=lambda line: None)
    except Exception as error:   # the app still starts, from the version there was
        say("TagPup: could not install (%s); starting the installed version." % error)
        return None
    say("TagPup: installed %s. An app already running keeps the old code until it is "
        "closed and started again." % name)
    return name


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
    parser.add_argument("--if-changed", action="store_true",
                        help="with --apply: install only if the checkout's commit is not the one "
                             "installed and it holds no uncommitted code (what the launchers run)")
    parser.add_argument("--to", default=default_destination(),
                        help="where to install (default: %(default)s)")
    parser.add_argument("--home", default=tagpup_config.home(),
                        help="TAGPUP_HOME for the installed apps (default: %(default)s)")
    parser.add_argument("--python", default=default_python(),
                        help="the interpreter the launchers use (default: %(default)s)")
    args = parser.parse_args(argv)
    if args.if_changed and args.apply:
        update(os.path.abspath(args.to), os.path.abspath(args.home), args.python)
        return 0
    install(os.path.abspath(args.to), os.path.abspath(args.home), args.python, apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
