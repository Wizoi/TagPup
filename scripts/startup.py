"""Start TagPup's always-on process at login, or stop doing so (docs/ARCHITECTURE.md,
phase 8, "Always on, from login").

    .venv/Scripts/python.exe scripts/startup.py status
    .venv/Scripts/python.exe scripts/startup.py install            # says what it would do
    .venv/Scripts/python.exe scripts/startup.py install --apply
    .venv/Scripts/python.exe scripts/startup.py uninstall --apply  # --force: end it at once

install makes a shortcut in the owner's Startup folder to the installed app's stable
launcher, TagPup Background.pyw, run by pythonw.exe so no window opens. It starts the
always-on process (tagpup.supervisor): the web server -- both apps -- and the recurring
jobs, hidden, restarted after a crash, and moved onto each newer version as it is
installed. The launcher reads current.txt, so installing the app again moves it with the
rest; the shortcut never names a version. It marks the installed app always-on
(always-on.txt), so TagPup.cmd and TagTuner.cmd start the process, if it is not
running, rather than a server of their own; and it starts it now.

uninstall removes the shortcut and the mark and stops the process. The process drains
its server first -- a write under way finishes, and so do a Suggest run, an index and a
recurring job -- which can take a while; it waits --wait seconds and says so if it is
still stopping. --force ends it at once.

The app must be installed first: scripts/install_app.py --apply.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from code_snapshot import REPO_ROOT  # noqa: E402
from tagpup import logs, supervisor  # noqa: E402
from tagpup.core import processes  # noqa: E402

DESCRIPTION = "TagPup, always on: both apps and the recurring jobs, from login"
ICON = "tagpup.ico"


def startup_folder():
    """The owner's Startup folder, wherever Windows keeps it; None when it cannot say."""
    found = supervisor.known_folders("Startup")
    return found[0] if found else None


def version_of(record):
    return record.get("version") or "from its code folder"


def wait_until(check, seconds):
    deadline = time.monotonic() + seconds
    while True:
        found = check()
        if found or time.monotonic() >= deadline:
            return found
        time.sleep(0.25)


def install(installed, folder, python, apply=False, wait=60, say=print):
    """Make the Startup shortcut, mark the app always-on, and start the process. The exit code."""
    launcher = os.path.join(installed, supervisor.BACKGROUND_LAUNCHER)
    link = os.path.join(folder, supervisor.STARTUP_SHORTCUT)
    marker = os.path.join(installed, supervisor.ALWAYS_ON)
    target = supervisor.windowless_python(python)
    if not os.path.exists(launcher):
        say("The installed app at %s has no %s: install it again first (scripts/install_app.py --apply)."
            % (installed, supervisor.BACKGROUND_LAUNCHER))
        return 1
    running = supervisor.running()
    if not apply:
        say("shortcut     %s -> %s \"%s\"" % (link, target, launcher))
        say("mark         %s" % marker)
        say("start        %s" % ("already running (pid %s)" % running["pid"] if running else launcher))
        say("\nDry run. Nothing was changed. Re-run with --apply to make it so.")
        return 0

    if supervisor.make_shortcut(link, target, '"%s"' % launcher, installed, os.path.join(installed, ICON),
                                DESCRIPTION):
        say("made         %s" % link)
    else:
        say("could not make the shortcut %s" % link)
        return 1
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write(link + "\n")
    say("marked       %s" % marker)
    if running:
        say("running      already (pid %s, version %s)" % (running["pid"], version_of(running)))
        return 0
    supervisor.start_in_background(installed, python)
    started = wait_until(supervisor.running, wait)
    if not started:
        say("did not start in %ds; see %s" % (wait, os.path.join(logs.log_dir(), "supervisor.log")))
        return 1
    say("started      pid %s, version %s" % (started["pid"], version_of(started)))
    return 0


def uninstall(installed, folder, apply=False, wait=120, force=False, say=print):
    """Remove the shortcut and the mark, and stop the process. The exit code."""
    link = os.path.join(folder, supervisor.STARTUP_SHORTCUT) if folder else None
    marker = os.path.join(installed, supervisor.ALWAYS_ON)
    running = supervisor.running()
    if not apply:
        say("remove       %s" % (link if link and os.path.exists(link) else "(no shortcut)"))
        say("remove       %s" % (marker if os.path.exists(marker) else "(no mark)"))
        say("stop         %s" % ("pid %s%s" % (running["pid"], ", at once" if force else ", once its server has drained")
                                 if running else "(not running)"))
        say("\nDry run. Nothing was changed. Re-run with --apply to make it so.")
        return 0

    if link and supervisor.remove(link):
        say("removed      %s" % link)
    else:
        say("no shortcut  %s" % link)
    if supervisor.remove(marker):
        say("removed      %s" % marker)
    if not running:
        say("not running")
        return 0
    if force:
        processes.kill_tree(running["pid"])
        stopped = wait_until(lambda: supervisor.running() is None, 30)
        say("ended        pid %s, at once" % running["pid"] if stopped else "could not end pid %s" % running["pid"])
        return 0 if stopped else 1
    supervisor.request_stop()
    say("asked        pid %s to stop once its server has drained" % running["pid"])
    if wait_until(lambda: supervisor.running() is None, wait):
        say("stopped      pid %s" % running["pid"])
        return 0
    state = supervisor.last_state() or {}
    say("still stopping after %ds (%s): it lets the work under way finish first. It stops by itself; "
        "run again with --force to end it now." % (wait, state.get("state", "unknown")))
    return 0


def status(installed, folder, say=print):
    link = os.path.join(folder, supervisor.STARTUP_SHORTCUT) if folder else None
    say("shortcut     %s" % (link if link and os.path.exists(link) else "none"))
    say("always on    %s" % ("yes" if supervisor.always_on(installed) else "no"))
    say("installed    %s" % (supervisor.read_current(installed) or "nothing in %s" % installed))
    running = supervisor.running()
    if running:
        say("running      pid %s, version %s, %s since %s%s" % (
            running["pid"], version_of(running), running.get("state"), running.get("since"),
            " (%s)" % running["why"] if running.get("why") else ""))
    else:
        last = supervisor.last_state()
        say("running      no%s" % (" (last: %s, %s%s)" % (last.get("state"), last.get("since"),
                                                            ", %s" % last["why"] if last.get("why") else "")
                                   if last else ""))
    refused = (running or supervisor.last_state() or {}).get("update_refused")
    if refused:
        say("updates      refused since %s: %s" % (refused.get("since"), refused.get("said")))
    where = supervisor.server()
    if where:
        port = sorted(where.get("ports", {}).values())[0]
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/server" % port, timeout=5) as reply:
                answer = json.loads(reply.read().decode("utf-8"))
            say("answering    version %s on %s" % (answer.get("version"), ", ".join(
                "%s %d" % (kind, number) for kind, number in sorted(where["ports"].items()))))
        except (OSError, ValueError) as e:
            say("answering    no answer on port %d (%s)" % (port, e))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("install", "uninstall", "status"))
    parser.add_argument("--apply", action="store_true", help="make the change; the default is a dry run")
    parser.add_argument("--to", default=supervisor.default_installed(),
                        help="the installed app's folder (default: %(default)s)")
    parser.add_argument("--startup-folder", default=None,
                        help="the Startup folder to put the shortcut in (default: the owner's, as Windows says)")
    parser.add_argument("--python", default=supervisor.checkout_python(REPO_ROOT),
                        help="the interpreter; its pythonw.exe runs the launcher (default: %(default)s)")
    parser.add_argument("--wait", type=float, default=None,
                        help="seconds to wait for the process to start (60) or stop (120)")
    parser.add_argument("--force", action="store_true", help="uninstall: end the process at once, not drained")
    args = parser.parse_args(argv)
    installed = os.path.abspath(args.to)
    folder = args.startup_folder or startup_folder()
    if args.action == "status":
        return status(installed, folder)
    if not folder:
        print("Could not find the Startup folder; name it with --startup-folder.")
        return 1
    if args.action == "install":
        return install(installed, folder, args.python, apply=args.apply,
                       wait=60 if args.wait is None else args.wait)
    return uninstall(installed, folder, apply=args.apply, wait=120 if args.wait is None else args.wait,
                     force=args.force)


if __name__ == "__main__":
    sys.exit(main())
