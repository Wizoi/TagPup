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
and TagPup CLI.cmd for indexing and the other CLI commands. It also writes TagPup
Background.pyw, the always-on process's launcher (tagpup.supervisor), which the Startup
shortcut scripts/startup.py makes runs: it reads current.txt, so installing again moves
the always-on process too -- a running one moves onto the new version at its next
quiet moment. When the owner has chosen the always-on process, TagPup.cmd and
TagTuner.cmd start it, if it is not running, rather than a server of their own.

It also makes shortcuts to TagPup and TagTuner, with their icons, on the Desktop and
in the Start menu: a .cmd cannot be pinned to the taskbar or given an icon, and a
shortcut can.

Each launcher first runs this with --if-changed: when the checkout has moved on to a
newer commit -- the installed one among its ancestors -- and holds no uncommitted code,
that commit is installed before the app starts, so a merge reaches the apps at their
next start. An older or unrelated commit (a branch switched back) is never installed so. A checkout in the middle of
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
from tagpup import supervisor  # noqa: E402
from tagpup.core import processes  # noqa: E402

#: Versions kept: the new one and the two before it.
KEEP = 3

#: Launcher file -> the program it starts, and its arguments.
LAUNCHERS = {
    # --installed: this folder, where scripts/startup.py marks the always-on process chosen.
    "TagPup.cmd": ("tagpup_web.py", '--open tagpup --installed "%~dp0."'),
    "TagTuner.cmd": ("tagpup_web.py", '--open tuner --installed "%~dp0."'),
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


#: The always-on process's launcher (supervisor.BACKGROUND_LAUNCHER), with the home: run
#: by pythonw.exe from the Startup shortcut scripts/startup.py makes, no window. It reads
#: current.txt, so it always starts the installed version, and the shortcut never names one.
BACKGROUND = '''"""Written by scripts/install_app.py: starts the installed TagPup's always-on process
(tagpup.supervisor), hidden, with this home. The Startup shortcut runs it with pythonw.exe."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["TAGPUP_HOME"] = {home!r}
with open(os.path.join(HERE, "current.txt"), encoding="utf-8") as handle:
    CODE = os.path.join(HERE, "versions", handle.read().strip())
sys.path.insert(0, CODE)
os.chdir(os.environ["TAGPUP_HOME"])

from tagpup import supervisor  # noqa: E402

sys.exit(supervisor.main(["--installed", HERE] + sys.argv[1:]))
'''


#: Shortcut -> (the launcher it runs, its icon in web/common/icons, what it says).
SHORTCUTS = {
    "TagPup.lnk": ("TagPup.cmd", "tagpup.ico", "Tag the photos of a folder"),
    "TagTuner.lnk": ("TagTuner.cmd", "tagtuner.ico", "Tune a photo library's tags and faces"),
}

def shortcut_folders():
    """The Desktop and the Start menu's Programs folder, wherever Windows keeps them
    (a Desktop moved into OneDrive, say)."""
    return supervisor.known_folders("Desktop", "Programs")


def make_shortcuts(destination, folders, say=print):
    """Put the icons beside the launchers and a shortcut to each app in each of
    `folders`: cmd.exe running the launcher, which can be pinned to the taskbar.
    Returns the shortcuts made."""
    made = []
    for icon in {icon for _cmd, icon, _desc in SHORTCUTS.values()}:
        shutil.copyfile(os.path.join(REPO_ROOT, "web", "common", "icons", icon),
                        os.path.join(destination, icon))
    cmd = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    for folder in folders:
        for name, (launcher, icon, description) in SHORTCUTS.items():
            link = os.path.join(folder, name)
            if supervisor.make_shortcut(link, cmd, '/c "%s"' % os.path.join(destination, launcher),
                                        destination, os.path.join(destination, icon), description):
                made.append(link)
            else:
                say("could not make the shortcut %s" % link)
    return made


def default_destination():
    return supervisor.default_installed()


def default_python():
    """The checkout's virtualenv, which has the app's packages; else this interpreter."""
    return supervisor.checkout_python(REPO_ROOT)


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


def is_ancestor(commit):
    """Is `commit` HEAD or one of its ancestors -- is HEAD the same or newer? False for a
    commit git does not know."""
    try:
        return processes.run(["git", "-C", REPO_ROOT, "merge-base", "--is-ancestor", commit, "HEAD"],
                             capture_output=True, text=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def commit_of(version):
    """The commit a version's name says it came from, or None."""
    parts = (version or "").rstrip("+").split("-")
    return parts[2] if len(parts) >= 3 else None


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
    installed = commit_of(current)
    if installed and not is_ancestor(installed):
        # A checkout moved back, or onto an unrelated branch: never installed on its own
        # -- the always-on process installs unattended. By hand: install_app.py --apply.
        say("TagPup: the checkout's %s is not newer than the installed %s, so it was not installed; "
            "starting the installed version." % (commit, installed))
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


def install(destination, home, python, name=None, apply=False, say=print, shortcuts_in=()):
    """Install a new version, and make shortcuts to the apps in each folder of
    `shortcuts_in`. Returns (the version's name, the versions removed)."""
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
    say("launchers    %s" % ", ".join(os.path.join(destination, n)
                                      for n in list(LAUNCHERS) + [supervisor.BACKGROUND_LAUNCHER]))
    if previous:
        say("replacing    %s" % previous)
    for old in removing:
        say("removing     %s" % os.path.join(destination, "versions", old))
    for place in shortcuts_in:   # not `folder`: that is the version's, copied into below
        say("shortcuts    %s" % ", ".join(os.path.join(place, n) for n in SHORTCUTS))
    if not apply:
        say("\nDry run. Nothing was changed. Re-run with --apply to install.")
        return name, []

    copy_code(folder, REPO_ROOT, launchers=True)
    # Checked where current.txt will send the launchers, by the version's name, before
    # it moves: ae726d6 copied the code elsewhere under a variable that still said
    # "the version's folder", a version without its programs became current, and
    # every launcher failed.
    version = os.path.join(destination, "versions", name)
    missing = [script for script, _args in LAUNCHERS.values()
               if not os.path.isfile(os.path.join(version, script))]
    if missing:
        raise RuntimeError("the new version %s lacks %s; the launchers still start %s"
                           % (version, ", ".join(sorted(set(missing))), previous))
    with open(os.path.join(folder, "VERSION.txt"), "w", encoding="utf-8") as handle:
        handle.write("%s\nfrom %s\n" % (name, REPO_ROOT))
    for launcher, (script, args) in LAUNCHERS.items():
        # newline="" keeps the CRLFs the template already has: cmd.exe wants them.
        with open(os.path.join(destination, launcher), "w", encoding="utf-8", newline="") as handle:
            handle.write(LAUNCHER.format(home=home, python=python, script=script, args=args))
    with open(os.path.join(destination, supervisor.BACKGROUND_LAUNCHER), "w", encoding="utf-8") as handle:
        handle.write(BACKGROUND.format(home=home))
    current = os.path.join(destination, "current.txt")
    with open(current + ".writing", "w", encoding="utf-8") as handle:
        handle.write(name)
    os.replace(current + ".writing", current)
    if shortcuts_in:
        make_shortcuts(destination, shortcuts_in, say)

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
    parser.add_argument("--no-shortcuts", action="store_true",
                        help="make no shortcuts on the Desktop or in the Start menu")
    args = parser.parse_args(argv)
    if args.if_changed and args.apply:
        update(os.path.abspath(args.to), os.path.abspath(args.home), args.python)
        return 0
    install(os.path.abspath(args.to), os.path.abspath(args.home), args.python, apply=args.apply,
            shortcuts_in=() if args.no_shortcuts else shortcut_folders())
    return 0


if __name__ == "__main__":
    sys.exit(main())
