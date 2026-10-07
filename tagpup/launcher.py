"""Replacing a running server with the version being launched, without the always-on
process (docs/ARCHITECTURE.md, "The installed copy"; docs/findings.md, #726).

TagPup.cmd and TagTuner.cmd install a newer commit, then start tagpup_web.py --open from
it. A server already answering on the port used to be given the page whatever version
it was, and the owner ended old servers by hand to reach the version installed. Now:

- Every server, once its ports are bound, says where it answers in a record per port in
  the user's servers folder (`folder()`: %LOCALAPPDATA%\\TagPup\\servers, or
  TAGPUP_SERVERS), with a token of its own, the launcher's authority to drain it (HEADER).
  The folder is in the user's own profile, so another user's launcher finds no record; and
  the server takes the token only from this machine (tagpup.web.lifecycle).
- A launcher (`make_way`) asks the server answering on its port which version it is
  (GET /api/server). The version being launched: its page is opened. The always-on
  process's: left to it, which moves it at its next quiet moment. Any other -- older,
  newer, or one run from a checkout (no version) -- is asked to drain with the record's
  token: refused while a Suggest run, an index, a bulk edit or the startup migrations run,
  which the launcher waits out, saying so, with the running page opened meanwhile; once
  drained (nothing in flight, nothing running, nothing new taken), it is ended, and the
  launcher serves in its place.
- A server that does not answer /api/server HUNG_TRIES times in a row, HUNG_TIMEOUT each,
  is ended -- only when its record is alive: the same process id and start time, so this
  user's server and no process that reused its id.
- One that answers with no record -- a version from before records, another user's -- is
  not replaced: the launcher says so and opens its page, as before.

Two launches at once (TagPup.cmd and TagTuner.cmd clicked together) both make way; the
ports decide which serves (tagpup.web.app.bind refuses a port in use), and the other opens
the page of the one that won.

An install by hand (scripts/install_app.py --apply) hands the server over itself
(`hand_over`, #795): the server of an earlier version this installed app runs is made way
for as a launch would (the same make_way, opening no page), and the version installed is
started in its place, on its ports, as a process of its own with a hidden console
(`start_server`): an open page says TagPup was updated, and a reload reaches the new
version. The old server is ended before the new one starts -- they share the ports the
pages and bookmarks use, and two servers starting on one library run its migrations at
once -- so first the new version is checked to start at all (`starts`: its tagpup_web.py
--help, every module the server imports); and when it does not answer once started, the
old version is started again. A server run from a checkout, one of another installed
folder, and the always-on process's are left alone; when the owner chose the always-on
process, it is started (if it is not running) rather than a server of the install's own.
Two installs at once take turns (HANDOVER_LOCK); the second leaves the server to the first,
which starts the version current.txt names when it starts it.
"""
import http.client
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request

from tagpup import config
from tagpup import supervisor
from tagpup.core import processes

#: Where the records are, when not the user's own folder (a test's home, a sandbox).
ENV = "TAGPUP_SERVERS"

#: The header a launcher's drain carries the record's token in.
HEADER = "X-TagPup-Launcher"

#: The header every response carries: the version answering, which a page compares with
#: the one that served it (web/common/api.js). A server run from its code folder has no
#: version's name; it says FROM_A_CHECKOUT.
VERSION_HEADER = "X-TagPup-Version"
FROM_A_CHECKOUT = "checkout"

#: What make_way decides: open the page of the server that answers, or serve.
OPEN, SERVE = "open", "serve"

#: How long /api/server has to answer, and how many unanswered in a row mean it hangs
#: (the supervisor's rule, closer together: the owner is waiting at the window).
HUNG_TIMEOUT = 20
HUNG_TRIES = 3

#: How long a drain may wait for the requests in flight; how soon a busy server is asked
#: again; how often the launcher says again what it waits for.
DRAIN_SECONDS = 20
RETRY_BUSY = 10
SAY_AGAIN = 300

#: A drain waits for QUIET seconds with no request -- a page in the middle of a save is not
#: cut -- until it has waited QUIET_PATIENCE for one (a page that asks every few seconds
#: never leaves one); then it takes the next moment with nothing in flight.
QUIET = 15
QUIET_PATIENCE = 120

#: Drained, the server takes work again if it has not been ended within LEASE seconds: the
#: launcher ends it within a second, and a window closed in between leaves no server
#: refusing every request (tagpup.web.lifecycle).
LEASE = 20

#: How long an ended server has to let go of its port; how long something that answers on
#: the port without a record (a server starting, before its record) is waited for.
END_WAIT = 30
STARTING_WAIT = 60


def folder():
    """The folder the records are in: the user's own (TAGPUP_SERVERS overrides it)."""
    return os.environ.get(ENV) or os.path.join(supervisor.default_installed(), "servers")


def record_path(port):
    return os.path.join(folder(), "%d.json" % int(port))


# ---- The server's side: saying where it answers ------------------------------------------

def say_where(ports, version, token, supervised=False):
    """The server's, once its ports are bound and before it answers: a record per port.
    The records of servers since ended are cleared first: a server ended without stopping
    -- killed, or its machine turned off -- leaves its own."""
    clear_stale()
    found = {"pid": os.getpid(), "started": processes.started(os.getpid()), "ports": dict(ports),
             "version": version, "token": token, "supervised": bool(supervised)}
    for port in sorted(set(ports.values())):
        supervisor.write_json(record_path(port), found)


def forget(ports):
    """The server's, as it stops: its records, and only its own."""
    for port in sorted(set(ports.values())):
        found = supervisor.read_json(record_path(port))
        if found and found.get("pid") == os.getpid():
            supervisor.remove(record_path(port))


# ---- Reading them ---------------------------------------------------------------------------

def record(port):
    """The record of the live server that said it answers on `port`, or None: none, or its
    process has ended (a record left by a server ended without stopping)."""
    found = supervisor.read_json(record_path(port))
    if not processes.recorded_alive(found):
        return None
    if int(port) not in [value for value in (found.get("ports") or {}).values() if isinstance(value, int)]:
        return None
    return found


def clear_stale():
    """Remove each record whose process has ended. How many."""
    removed = 0
    try:
        names = os.listdir(folder())
    except OSError:
        return 0
    for name in names:
        path = os.path.join(folder(), name)
        if name.endswith(".json") and not processes.recorded_alive(supervisor.read_json(path)) and supervisor.remove(path):
            removed += 1
    return removed


def running():
    """Every live server with a record: [record], one per process."""
    seen = {}
    try:
        names = sorted(os.listdir(folder()))
    except OSError:
        return []
    for name in names:
        if name.endswith(".json"):
            found = supervisor.read_json(os.path.join(folder(), name))
            if processes.recorded_alive(found):
                seen.setdefault(found["pid"], found)
    return list(seen.values())


def versions_running():
    """The installed versions a live server runs from: what an install must not remove."""
    return {found["version"] for found in running() if found.get("version")}


# ---- Asking the server ------------------------------------------------------------------------

def ask(port, timeout=None):
    """GET /api/server on `port`: what it says, or None when nothing answers as a TagPup
    server does within `timeout` (HUNG_TIMEOUT)."""
    timeout = HUNG_TIMEOUT if timeout is None else timeout
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/server" % port, timeout=timeout) as reply:
            value = json.loads(reply.read().decode("utf-8"))
    except (OSError, ValueError, http.client.HTTPException):
        # Not HTTP is no answer too: a port reached as it is let go, or a connection to
        # itself -- the request read back as the reply (http.client.BadStatusLine).
        return None
    return value if isinstance(value, dict) and "version" in value else None


def drain(port, token, quiet=QUIET, seconds=None):
    """POST /api/server/drain with the record's token, after `quiet` seconds with no request,
    for LEASE: {"drained": bool, "waiting_for": [...]}, "refused": the status when the server
    refused it, "no_answer" when nothing answered."""
    seconds = DRAIN_SECONDS if seconds is None else seconds
    request = urllib.request.Request(
        "http://127.0.0.1:%d/api/server/drain" % port, method="POST",
        data=json.dumps({"seconds": seconds, "quiet": quiet, "lease": LEASE}).encode("utf-8"),
        headers={"Content-Type": "application/json", HEADER: token or ""})
    try:
        with urllib.request.urlopen(request, timeout=seconds + 30) as reply:
            value = json.loads(reply.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"drained": False, "refused": e.code}
    except (OSError, ValueError, http.client.HTTPException) as e:
        return {"drained": False, "no_answer": str(e) or type(e).__name__}
    return value if isinstance(value, dict) else {"drained": False, "no_answer": "not JSON"}


def end(found, port, answering, wait=END_WAIT, sleep=time.sleep):
    """End the server `found` names, and what it started, once it is drained or hangs --
    only while its record is alive, so never a process that reused its id -- and wait for
    `port` to be let go. True when it is."""
    if processes.recorded_alive(found):
        processes.kill_tree(found["pid"])
    deadline = time.monotonic() + wait
    while processes.recorded_alive(found) or answering(port):
        if time.monotonic() >= deadline:
            return False
        sleep(0.2)
    for each in sorted(set((found.get("ports") or {}).values())):
        left = supervisor.read_json(record_path(each))
        if left and left.get("pid") == found.get("pid"):
            supervisor.remove(record_path(each))
    return True


def _name(version):
    return version or "from a checkout"


def answering(port):
    """Is something already answering on `port` on this machine?"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def make_way(port, version, say, open_page, answering, sleep=time.sleep, clock=time.monotonic, page=True):
    """Before a launcher of `version` serves on `port`: what answers there, if anything,
    and whether to replace it (module doc). (OPEN or SERVE, whether `open_page()` was
    already called: the page of the server answering, opened while it finishes a long job.)
    `say(line)` tells the owner, at the launcher's window. Not `page`: an install's, which
    opens no page and says what it leaves running instead."""
    then = "opening its page" if page else "leaving it running"
    stopping = "closing this window" if page else "stopping this install (Ctrl+C)"
    opened = False
    said, said_at = None, None
    unanswered = 0
    starting_since = None
    #: Since when the drain has waited only for a moment with no request.
    quiet_since = None
    while True:
        if not answering(port):
            return SERVE, opened
        status = ask(port)
        theirs = record(port)
        if status is None:
            if theirs is None:
                # Something holds the port without a record: a server starting (its record
                # comes once it serves), or no TagPup server of this user's.
                starting_since = clock() if starting_since is None else starting_since
                if clock() - starting_since >= STARTING_WAIT:
                    say("Port %d answers, but not as a TagPup server this user started; %s." % (port, then))
                    return OPEN, opened
                sleep(1)
                continue
            if theirs.get("supervised"):
                say("TagPup %s (process %s), run by the always-on process, is not answering; the always-on "
                    "process ends a server that hangs; %s." % (_name(theirs.get("version")), theirs.get("pid"), then))
                return OPEN, opened
            unanswered += 1
            if unanswered < HUNG_TRIES:
                say("TagPup %s (process %s) is not answering; asking again (%d of %d)."
                    % (_name(theirs.get("version")), theirs.get("pid"), unanswered, HUNG_TRIES))
                continue
            say("TagPup %s (process %s) has not answered for %d tries of %d s; ending it."
                % (_name(theirs.get("version")), theirs.get("pid"), HUNG_TRIES, HUNG_TIMEOUT))
            if not end(theirs, port, answering, sleep=sleep):
                say("It did not end; %s, on port %d." % (then, port))
                return OPEN, opened
            continue
        unanswered = 0
        starting_since = None
        running_version = status.get("version")
        if running_version == version:
            return OPEN, opened
        if status.get("supervised"):
            say("TagPup %s answers, run by the always-on process, which moves it onto %s at its next quiet "
                "moment; %s." % (_name(running_version), _name(version), then))
            return OPEN, opened
        if theirs is None or theirs.get("version") != running_version:
            say("TagPup %s answers on port %d and cannot be asked to make way for %s: it was started before "
                "a launch could replace it, or by another user. Close its window (or end it in Task Manager) "
                "and launch again; %s now." % (_name(running_version), port, _name(version), then))
            return OPEN, opened
        if said is None:
            say("TagPup %s is running (process %s); handing over to %s."
                % (_name(running_version), theirs.get("pid"), _name(version)))
            said, said_at = "", clock()
        patient = quiet_since is not None and clock() - quiet_since >= QUIET_PATIENCE
        answer = drain(port, theirs.get("token"), quiet=0 if patient else QUIET)
        if answer.get("drained"):
            say("TagPup %s finished what it was doing; ending it and starting %s."
                % (_name(running_version), _name(version)))
            if not end(theirs, port, answering, sleep=sleep):
                say("It did not end; %s, on port %d." % (then, port))
                return OPEN, opened
            continue
        if answer.get("refused"):
            say("TagPup %s refused to make way (%s); %s." % (_name(running_version), answer["refused"], then))
            return OPEN, opened
        if answer.get("no_answer"):
            continue   # asked again: gone (serve), or hanging (counted above)
        reasons = answer.get("waiting_for") or ["the server"]
        if all(reason.startswith("a request ") for reason in reasons):
            # Someone is using the page: the moment after their last request, or, after
            # QUIET_PATIENCE, the next with nothing in flight.
            quiet_since = clock() if quiet_since is None else quiet_since
            reasons = ["a moment with no request: the app is in use"]
        else:
            quiet_since = None
        waiting = ", ".join(reasons)
        if waiting != said or clock() - said_at >= SAY_AGAIN:
            say("TagPup %s is busy (%s): %s takes over when that ends. It answers meanwhile; %s leaves the "
                "update to the next launch." % (_name(running_version), waiting, _name(version), stopping))
            said, said_at = waiting, clock()
        if not opened:
            open_page()
            opened = True
        sleep(RETRY_BUSY)


# ---- An install's hand-over (#795) -----------------------------------------------------------

#: Held, in the installed app's folder, while an install hands a server over: a second
#: install leaves it to the first, which starts what current.txt names when it starts it.
HANDOVER_LOCK = "handover.lock"

#: How long the check that a version starts at all may take (its tagpup_web.py --help, about
#: 2 s here); how long a server an install started has to answer as its version.
CHECK_WAIT = 120
START_WAIT = 120

#: Where a server an install started writes what it prints, in its home's data/logs: its log
#: is server.log (tagpup.logs), and this holds what comes before it -- a traceback that stops
#: it starting.
STARTED_LOG = "tagpup_web.installed.log"

#: What an install does with each server running (`kind_of`).
SAME, ALWAYS_ON, CHECKOUT, ELSEWHERE, REPLACE = "same", "always-on", "checkout", "elsewhere", "replace"


def kind_of(found, installed, current):
    """What an install of `current` into `installed` does with the server `found` names:
    SAME (it runs `current`), ALWAYS_ON (the always-on process's, which moves its own),
    CHECKOUT (run from a checkout: a developer's, left to the next launch), ELSEWHERE (a
    version this installed folder does not hold), or REPLACE."""
    version = found.get("version")
    if version == current:
        return SAME
    if found.get("supervised"):
        return ALWAYS_ON
    if not version:
        return CHECKOUT
    if not os.path.isdir(os.path.join(installed, "versions", version)):
        return ELSEWHERE
    return REPLACE


def ports_of(found):
    """The ports a record names, as the owner reads them: each once, in order, by commas."""
    return ", ".join(str(port) for port in sorted(set((found.get("ports") or {}).values())))


def _main_port(found):
    ports = found.get("ports") or {}
    return ports.get("tagpup") or sorted(ports.values())[0]


def server_command(python, code, ports):
    """The command line of the server of the code in `code` on `ports` ({"tagpup", "tuner"}),
    opening no page."""
    command = [supervisor.console_python(python), os.path.join(code, "tagpup_web.py"), "--open", "none"]
    for kind in ("tagpup", "tuner"):
        if kind in ports:
            command += ["--%s-port" % kind, str(int(ports[kind]))]
    return command


def _environment(home):
    env = dict(os.environ, TAGPUP_HOME=home)
    env.pop(supervisor.TOKEN, None)   # never the always-on process's child
    return env


def starts(python, code, home):
    """Does the server of `code` start at all -- does its tagpup_web.py import every module
    the server needs, and answer --help? None when it does, else why not."""
    try:
        done = processes.run([supervisor.console_python(python), os.path.join(code, "tagpup_web.py"), "--help"],
                             cwd=home, env=_environment(home), stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, errors="replace", timeout=CHECK_WAIT)
    except (OSError, subprocess.SubprocessError) as e:
        return str(e)
    if done.returncode == 0:
        return None
    last = [line.strip() for line in (done.stderr or "").splitlines() if line.strip()]
    return "it exited with %s%s" % (done.returncode, ": " + last[-1] if last else "")


def started_log(home):
    return os.path.join(home, config.DATA, "logs", STARTED_LOG)


def start_server(python, code, home, ports):
    """Start the server of `code` on `ports`, `home` its home: a process of its own that
    outlives the install -- a hidden console and a process group of its own
    (tagpup.core.processes), its output in STARTED_LOG -- opening no page. The Popen."""
    log_path = started_log(home)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    try:   # rotated as each starts, as the always-on process's console log is
        if os.path.getsize(log_path) > supervisor.CONSOLE_LOG_MAX:
            os.replace(log_path, log_path + ".1")
    except OSError:
        pass
    with open(log_path, "ab") as output:
        return processes.start(server_command(python, code, ports), own_group=True, cwd=home, env=_environment(home),
                               stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)


def wait_for(port, version, started, wait=START_WAIT, sleep=time.sleep, clock=time.monotonic):
    """Wait for `version` to answer on `port`: the server `started` (a Popen; None for the
    always-on process's), or one a launch started meanwhile. None once it answers, else why
    not."""
    deadline = clock() + wait
    while True:
        status = ask(port, timeout=5)
        if status is not None and status.get("version") == version:
            return None
        if started is not None and started.poll() is not None and not answering(port):
            return "it exited with %s" % started.poll()
        if clock() >= deadline:
            return "it did not answer in %d s" % wait
        sleep(0.5)


def hand_over(installed, python, home, say, sleep=time.sleep, clock=time.monotonic):
    """After an install into `installed`: hand each server of an earlier version this
    installed app runs over to the version current.txt names (module doc), `home` the new
    one's home. `say(line)` tells the owner, at the install's window. False when a server
    was not handed over: left running (busy and refusing, not this user's), or the new
    version did not start."""
    lock = supervisor.Lock(os.path.join(installed, HANDOVER_LOCK))
    if not lock.acquire(0):
        say("Another install is handing the running server over; it starts the version installed last.")
        return True
    try:
        current = supervisor.read_current(installed)
        replacing = [found for found in running() if kind_of(found, installed, current) == REPLACE]
        return all([_hand_over(found, installed, python, home, say, sleep, clock) for found in replacing])
    finally:
        lock.release()


def _hand_over(found, installed, python, home, say, sleep, clock):
    old, ports, port = found.get("version"), dict(found.get("ports") or {}), _main_port(found)
    new = supervisor.read_current(installed)
    back = os.path.join(installed, "current.txt")
    why = starts(python, os.path.join(installed, "versions", new), home)
    if why:
        say("TagPup %s does not start (%s), so TagPup %s was left running. The launchers start %s now: run "
            "TagPup.cmd to see why, or write %s in %s to go back." % (new, why, old, new, old, back))
        return False
    started = None
    try:
        way, _shown = make_way(port, new, say=say, open_page=lambda: None, answering=answering, sleep=sleep,
                               clock=clock, page=False)
        if way == OPEN:
            # Left running (make_way said why), or already the new version: a launch was quicker.
            status = ask(port, timeout=5)
            return bool(status) and status.get("version") == new
        new = supervisor.read_current(installed) or new   # another install's, since
        if supervisor.always_on(installed):
            # The owner chose the always-on process: the launchers' way, it serves, never a server of ours.
            if supervisor.running() is None:
                say("Starting the always-on process, which serves %s." % new)
                supervisor.start_in_background(installed, python)
        else:
            started = start_server(python, os.path.join(installed, "versions", new), home, ports)
        why = wait_for(port, new, started, sleep=sleep, clock=clock)
        if why is None:
            now = record(port) or {}
            say("TagPup %s answers on port %s (process %s), with no window of its own: a page left open says TagPup "
                "was updated, and a reload shows %s. The next install or launch of another version replaces it."
                % (new, ports_of(found), now.get("pid"), new))
            return True
        if started is not None and started.poll() is None:
            processes.kill_tree(started.pid)
        say("TagPup %s did not start (%s); what it said is in %s." % (new, why, started_log(home)))
        started = None
        return _start_again(found, installed, python, home, say, sleep, clock, new, back)
    except KeyboardInterrupt:
        if started is not None and started.poll() is None:
            say("Stopped: TagPup %s was started (process %s) and goes on without this window." % (new, started.pid))
        elif answering(port):
            say("Stopped: TagPup %s still answers on port %s; the next launch of TagPup or TagTuner replaces it."
                % (_name(old), ports_of(found)))
        else:
            say("Stopped: TagPup %s was ended and nothing answers on port %s; start TagPup or TagTuner."
                % (_name(old), ports_of(found)))
        raise


def _start_again(found, installed, python, home, say, sleep, clock, new, back):
    """The new version did not start, and the old was ended: start the old again, so the
    pages answer as they did. False: the hand-over failed either way."""
    old, ports, port = found.get("version"), dict(found.get("ports") or {}), _main_port(found)
    code = os.path.join(installed, "versions", old)
    if answering(port) or not os.path.isfile(os.path.join(code, "tagpup_web.py")):
        say("TagPup %s was not started again (%s). Start TagPup or TagTuner: its window says why %s does not start."
            % (old, "something answers on port %d" % port if answering(port) else "its folder is gone", new))
        return False
    again = start_server(python, code, home, ports)
    why = wait_for(port, old, again, sleep=sleep, clock=clock)
    if why is None:
        say("Started TagPup %s again (process %s): the pages answer as before. The launchers start %s now: run "
            "TagPup.cmd to see why it does not start, or write %s in %s to go back." % (old, again.pid, new, old, back))
        return False
    if again.poll() is None:
        processes.kill_tree(again.pid)
    say("TagPup %s did not start again either (%s): nothing answers on port %s. Start TagPup or TagTuner."
        % (old, why, ports_of(found)))
    return False
