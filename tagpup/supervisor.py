"""The always-on process: one hidden background process that keeps the web server -- both
apps and the recurring jobs -- running while the owner is logged in (docs/ARCHITECTURE.md,
phase 8, "Always on, from login" and "Updating itself").

    pythonw.exe "%LOCALAPPDATA%\\TagPup\\TagPup Background.pyw"   # the Startup shortcut
    .venv/Scripts/python.exe -m tagpup.supervisor                 # from a checkout

`scripts/startup.py install --apply` makes the shortcut in the owner's Startup folder and
starts it; the stable launcher it runs (BACKGROUND_LAUNCHER, which scripts/install_app.py
writes beside the other launchers from its BACKGROUND) reads current.txt, so installing
again moves it too.
It runs the web server (tagpup_web.py) as its child, and:

- refuses a second copy of itself: one per home, held by a lock on data/supervisor.lock;
- restarts the server after a crash, waiting longer after each (BACKOFF), and gives up
  after MAX_CRASHES in CRASH_WINDOW, saying why in its log; a server alive but not
  answering GET /api/server MAX_UNANSWERED times in a row is ended, and counted a crash;
- waits, rather than counting crashes, while another server holds the ports: its child
  exits PORTS_TAKEN (a TagPup started by hand, say);
- every UPDATE_EVERY, installs the checkout's commit when it is newer and holds no
  uncommitted code (scripts/install_app.py --apply --if-changed: the launchers' rule),
  and moves the server onto the version current.txt names at the next quiet moment -- no
  request for QUIET seconds, or, once it has waited PATIENCE, nothing in flight. It
  asks the server to drain (tagpup.web.lifecycle): nothing new is taken, and it is
  stopped only once nothing is in flight and no Suggest run, index or recurring job is
  under way. A drain refused is asked again every RETRY_MOVE, the old version answering
  meanwhile. It then starts a supervisor from the new version and waits for it to say it
  is up (HANDOVER_FILE) before it stops the server and lets go of the lock; the new one
  takes the lock and starts the server, which settles anything left unfinished, as every
  start does. The old one exits only once that server answers naming the new version and
  goes on answering for SETTLE. A new supervisor that does not come up, does not take the
  lock, or whose server does not answer, is ended, current.txt pointed back, and this one
  runs the version it ran, trying again after PATIENCE. A supervisor taken over to that
  gives up on its server points current.txt back to the version before it;
- stops, draining the server first -- for at most STOP_DRAIN_LIMIT, then it ends it --
  when data/supervisor.stop appears (scripts/startup.py uninstall);
- logs to data/logs/supervisor.log, the server's own output to
  data/logs/tagpup_web.console.log, and says what it is doing in data/supervisor.json,
  which scripts/startup.py status and the launchers read.

The server tells it where it answers in data/server.json (write_server), and a drain
carries the token it was given (TOKEN), so no other program can drain it.

Nothing here imports the web server: it is a program of its own, which the server reads
for the few names they share.
"""
import argparse
import datetime
import json
import logging
import os
import secrets
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from tagpup import config as tagpup_config
from tagpup import logs
from tagpup.core import processes

logger = logging.getLogger(__name__)

#: The environment variable the supervisor hands its child a token in, and the header a
#: drain carries it back in (tagpup.web.lifecycle).
TOKEN = "TAGPUP_SUPERVISOR_TOKEN"
TOKEN_HEADER = "X-TagPup-Supervisor"

#: How the server exits when another already answers on its ports: not a crash.
PORTS_TAKEN = 3

#: How the supervisor exits: another holds the lock; it gave up on a crashing server.
ALREADY_RUNNING = 4
GAVE_UP = 5

#: The stable launcher the Startup shortcut runs, beside the other launchers, and the file
#: that says the owner chose the always-on process (scripts/startup.py install).
BACKGROUND_LAUNCHER = "TagPup Background.pyw"
ALWAYS_ON = "always-on.txt"
STARTUP_SHORTCUT = "TagPup (always on).lnk"

#: Its files, in the home's data folder; HANDOVER_FILE is where a supervisor started to
#: take over says it is up, before it waits for the lock.
LOCK_FILE, STATE_FILE, STOP_FILE, SERVER_FILE = "supervisor.lock", "supervisor.json", "supervisor.stop", "server.json"
HANDOVER_FILE = "supervisor.handover.json"

#: What run() returns when a supervisor of the new version is to take over: main() lets
#: go of the lock, and makes sure it did.
HANDED_OVER = "handed over"
CONSOLE_LOG = "tagpup_web.console.log"
CONSOLE_LOG_MAX = 5 * 1024 * 1024

#: Seconds to wait before each restart after a crash (the last repeats); crashes counted
#: over CRASH_WINDOW, and how many there it gives up at.
BACKOFF = (2, 5, 15, 30, 60)
MAX_CRASHES = 5
CRASH_WINDOW = 10 * 60

#: How often to look for a newer version; how soon to ask a busy server again; how long
#: a drain may wait for the requests in flight; how soon to try ports another holds.
UPDATE_EVERY = 10 * 60
RETRY_MOVE = 60
DRAIN_SECONDS = 120
PORTS_WAIT = 30

#: How often the server is asked how it is (GET /api/server), how long it has to answer,
#: and how many unanswered in a row mean it hangs: it is ended and counted as a crash. The
#: status route passes no library and waits on nothing, so only a server whose every
#: thread is stuck misses three minutes of them.
HEALTH_EVERY = 60
HEALTH_TIMEOUT = 20
MAX_UNANSWERED = 3

#: How long a stop waits for the server to drain before it ends it anyway: the owner
#: asked, and a Suggest run or an index may run for hours.
STOP_DRAIN_LIMIT = 30 * 60

#: A move waits for a quiet moment: no request for QUIET seconds -- someone at the app
#: is not moved under -- until it has waited PATIENCE; then it takes the next moment with
#: nothing in flight.
QUIET = 120
PATIENCE = 60 * 60

#: How long the new version's server must go on answering, once it has answered, before
#: the old supervisor lets it be: a release whose server crashes at its first request, or
#: soon after it starts, is caught while the old one can still take back.
SETTLE = 30

#: How long a supervisor started by another, moving onto a new version, waits for the
#: other to let go of the lock.
HAND_OVER_WAIT = 60


# ---- Its files --------------------------------------------------------------------------

def data_file(name):
    return os.path.join(tagpup_config.data_dir(), name)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


#: How long a replace or a delete waits for a reader of the file to let go: on Windows a
#: file another process has open cannot be replaced or deleted, and these are read by
#: startup.py status, the launchers and the installer.
READER_WAIT = 5.0


def _while_read(action, path):
    """`action()`, tried again while a reader holds `path` (PermissionError), for up to
    READER_WAIT."""
    deadline = time.monotonic() + READER_WAIT
    while True:
        try:
            return action()
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def write_json(path, value):
    """Write `value` whole or not at all: a reader never sees half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".writing", "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=1)
    _while_read(lambda: os.replace(path + ".writing", path), path)


def remove(path):
    """Remove `path`: True if it went. One a reader holds past READER_WAIT is left, and
    logged -- never raised: the supervisor goes on, and its next loop tries again."""
    try:
        _while_read(lambda: os.remove(path), path)
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.error("Could not remove %s (%s); trying again later.", os.path.basename(path), e)
        return False


def _alive(record):
    """Is the process a record names -- by its id and when it started -- still running?"""
    if not record or not record.get("pid"):
        return False
    started = processes.started(record["pid"])
    return started is not None and started == record.get("started")


def running():
    """The running supervisor's record (data/supervisor.json), or None when none runs."""
    record = read_json(data_file(STATE_FILE))
    return record if _alive(record) and record.get("state") not in ("stopped", "gave up") else None


def last_state():
    """What the last supervisor wrote, running or not."""
    return read_json(data_file(STATE_FILE))


def server():
    """Where the supervised server answers: {"pid", "started", "ports", "version"}, or None."""
    record = read_json(data_file(SERVER_FILE))
    return record if _alive(record) else None


def versions_in_use(home):
    """The installed versions a live supervisor or server of `home` runs from: what an
    install must not remove (scripts/install_app.py)."""
    found = set()
    data = os.path.join(home, tagpup_config.DATA)
    for name, keys in ((STATE_FILE, ("version", "server_version")), (SERVER_FILE, ("version",))):
        record = read_json(os.path.join(data, name))
        if _alive(record):
            found.update(record[key] for key in keys if record.get(key))
    return found


def write_server(ports, version):
    """The server's, once its ports are bound: where it answers, for its supervisor."""
    write_json(data_file(SERVER_FILE), {"pid": os.getpid(), "started": processes.started(os.getpid()),
                                        "ports": dict(ports), "version": version})


def forget_server():
    record = read_json(data_file(SERVER_FILE))
    if record and record.get("pid") == os.getpid():
        remove(data_file(SERVER_FILE))


def request_stop():
    """Ask the running supervisor to drain the server and stop (scripts/startup.py uninstall)."""
    os.makedirs(tagpup_config.data_dir(), exist_ok=True)
    with open(data_file(STOP_FILE), "w", encoding="utf-8") as handle:
        handle.write(_now())


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---- One per home ----------------------------------------------------------------------

class Lock:
    """An exclusive lock on a file, held while the process lives: the operating system
    lets go of it when the process ends, however it ends."""

    def __init__(self, path):
        self.path = path
        self._fd = None

    def acquire(self, wait=0.0):
        """Take it, waiting up to `wait` seconds. False when another holds it."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        deadline = time.monotonic() + wait
        while True:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT)
            try:
                _lock(fd)
                self._fd = fd
                return True
            except OSError:
                os.close(fd)
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

    def release(self):
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _unlock(fd)
        except OSError:
            pass
        os.close(fd)


def _lock(fd):
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd):
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


# ---- The installed app -------------------------------------------------------------------

def read_current(installed):
    """The version current.txt names in the installed app's folder, or None."""
    try:
        with open(os.path.join(installed, "current.txt"), encoding="utf-8") as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def default_installed():
    """Where scripts/install_app.py installs the app: %LOCALAPPDATA%\\TagPup."""
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "TagPup")


def checkout_python(root):
    """The checkout's virtualenv interpreter, which has the app's packages; else this one."""
    venv = os.path.join(root, ".venv", "Scripts", "python.exe")
    return venv if os.path.exists(venv) else sys.executable


def always_on(installed):
    """Has the owner chosen the always-on process for this installed app (scripts/startup.py)?"""
    return bool(installed) and os.path.exists(os.path.join(installed, ALWAYS_ON))


def console_python(python=None):
    """python.exe beside `python` (or this interpreter): the server's, whose output goes
    to a file. The supervisor itself runs under pythonw.exe."""
    python = python or sys.executable
    folder, name = os.path.split(python)
    if name.lower() == "pythonw.exe" and os.path.exists(os.path.join(folder, "python.exe")):
        return os.path.join(folder, "python.exe")
    return python


def windowless_python(python=None):
    """pythonw.exe beside `python`, where there is one: a program run with it has no window."""
    python = python or sys.executable
    folder, name = os.path.split(python)
    if name.lower() == "python.exe" and os.path.exists(os.path.join(folder, "pythonw.exe")):
        return os.path.join(folder, "pythonw.exe")
    return python


def start_in_background(installed, python=None, handed_over=False, more=()):
    """Start the installed app's always-on process through its stable launcher, detached
    and without a window, with the arguments `more` (a supervisor's own, passed on). The
    Popen."""
    args = [windowless_python(python), os.path.join(installed, BACKGROUND_LAUNCHER)] + list(more)
    if handed_over:
        args.append("--handed-over")
    return processes.start(args, own_group=True, cwd=installed, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def install_newer(installed, python=None, home=None):
    """Install the checkout's commit into `installed` if it is newer and clean: what each
    launcher runs before its app (scripts/install_app.py --apply --if-changed), with the
    home's copy of the installer. Logs what it said, and returns its lines."""
    home = home or tagpup_config.home()
    script = os.path.join(home, "scripts", "install_app.py")
    if not os.path.exists(script):
        return []
    try:
        done = processes.run([console_python(python), script, "--apply", "--if-changed", "--to", installed],
                             cwd=home, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as e:
        logger.error("Could not look for a newer version: %s", e)
        return []
    said = [line.strip() for line in (done.stdout or "").splitlines() + (done.stderr or "").splitlines()
            if line.strip()]
    for line in said:
        logger.info("install_app: %s", line)
    return said


#: What install_app says when the checkout is not newer than what is installed.
NOT_NEWER = "not newer"


# ---- Windows: the Startup folder and its shortcut ----------------------------------------------

#: Makes one shortcut; its values come in the environment, so no path is quoted here.
SHORTCUT_SCRIPT = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:TAGPUP_LNK); "
    "$s.TargetPath = $env:TAGPUP_TARGET; $s.Arguments = $env:TAGPUP_ARGS; "
    "$s.WorkingDirectory = $env:TAGPUP_WORKDIR; $s.IconLocation = $env:TAGPUP_ICON; "
    "$s.Description = $env:TAGPUP_DESC; $s.Save()"
)


def known_folders(*names):
    """Where Windows keeps each of `names` ('Desktop', 'Programs', 'Startup'), wherever
    that is (a Desktop moved into OneDrive, say); [] when it cannot say."""
    script = "; ".join("[Environment]::GetFolderPath('%s')" % name for name in names)
    try:
        out = processes.run(["powershell", "-NoProfile", "-Command", script],
                            capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def make_shortcut(link, target, arguments, workdir, icon, description):
    """Make the shortcut `link`. Whether it is there after."""
    env = dict(os.environ, TAGPUP_LNK=link, TAGPUP_TARGET=target, TAGPUP_ARGS=arguments,
               TAGPUP_WORKDIR=workdir, TAGPUP_ICON=icon, TAGPUP_DESC=description)
    try:
        processes.run(["powershell", "-NoProfile", "-Command", SHORTCUT_SCRIPT],
                      env=env, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.exists(link)


# ---- The supervisor ----------------------------------------------------------------------

def default_command(code_root, python=None):
    """The server's command line, from the code in `code_root`."""
    return [console_python(python), os.path.join(code_root, "tagpup_web.py")]


class Supervisor:
    """Runs the server as its child until asked to stop.

    `installed` is the installed app's folder: the server is started from the version its
    current.txt names, and newer versions are installed and moved onto. Without it (a
    checkout, a test) the server runs from `code_root` and is never moved. `command(code)`
    is the server's command line for the code in that folder (default_command, or a test's
    stand-in); `env` its environment, to which the token is added. `install()` installs a
    newer version if there is one (install_newer). `hand_over`: an installed supervisor
    moving the server onto a version other than its own starts a supervisor from it
    instead (a test's restarts the server itself). The rest are the timings above, a
    test's shorter.
    """

    def __init__(self, installed=None, code_root=None, command=None, env=None, install=None, python=None,
                 hand_over=True, backoff=BACKOFF, max_crashes=MAX_CRASHES, crash_window=CRASH_WINDOW, update_every=UPDATE_EVERY,
                 retry_move=RETRY_MOVE, drain_seconds=DRAIN_SECONDS, ports_wait=PORTS_WAIT, tick=1.0,
                 clock=time.monotonic, health_every=HEALTH_EVERY, health_timeout=HEALTH_TIMEOUT,
                 max_unanswered=MAX_UNANSWERED, stop_drain_limit=STOP_DRAIN_LIMIT, patience=PATIENCE,
                 server_args=(), hand_over_wait=HAND_OVER_WAIT, passed_on=(), settle=SETTLE):
        self.installed = installed
        self.code_root = code_root or tagpup_config.CODE_ROOT
        self.own_version = tagpup_config.code_version(self.code_root) if code_root is None else None
        self._command = command or (lambda code: default_command(code, python))
        self._env = env
        self._install = install or (lambda: install_newer(installed, python))
        self._python = python
        self.hand_over = hand_over
        self.backoff, self.max_crashes, self.crash_window = tuple(backoff), max_crashes, crash_window
        self.update_every, self.retry_move, self.drain_seconds = update_every, retry_move, drain_seconds
        self.ports_wait, self.tick, self._clock = ports_wait, tick, clock
        self.health_every, self.health_timeout, self.max_unanswered = health_every, health_timeout, max_unanswered
        self.stop_drain_limit = stop_drain_limit
        self.patience = patience
        self._pending_since = None
        #: More arguments for the server's command line (ports for a sandbox or a test),
        #: and the supervisor's own to pass on to the one that takes over.
        self.server_args = list(server_args)
        self._passed_on = list(passed_on)
        self.hand_over_wait = hand_over_wait
        self.settle = settle
        #: The version the supervisor this one took over from ran: what current.txt is
        #: pointed back to if this one gives up.
        self._predecessor_version = None
        #: The version to keep running when one to hand over to did not start.
        self._pinned = None
        self._pinned_until = None
        self._successor = None
        #: The token of the server a supervisor before this one ran, kept until any server
        #: it left running has been seen to (end_orphan): this one's own writes of
        #: supervisor.json must not lose it.
        self._inherited_token = None
        #: {"said", "since"} while the installer refuses the checkout as not newer (a
        #: history rewritten, a branch switched back): in supervisor.json, for status.
        self._update_refused = None
        self._next_health = 0
        self._unanswered = 0
        self._stop = threading.Event()
        self._child = None
        self._child_version = None
        self._child_started = None
        self._token = None
        self._crashes = []
        self._restart_at = None
        self._pending = None
        self._next_update = None
        self._next_move = 0
        self._last_wait = None
        self._ports_said = False
        self.starts = 0
        self._state = {}
        #: When this supervisor started, as supervisor.json says it: the Activity page's
        #: "running since", and, for one that took over, when it moved onto its version.
        self._began = _now()

    # ---- What it says ---------------------------------------------------------------

    def _say(self, state, why=None):
        self._state = {"pid": os.getpid(), "started": processes.started(os.getpid()), "home": tagpup_config.home(),
                       "installed": self.installed, "version": self.own_version, "state": state, "why": why,
                       "since": _now(), "server_version": self._child_version,
                       "server_pid": self._child.pid if self._child is not None else None,
                       # So the next supervisor can drain a server this one left running (the
                       # file is the user's, as the libraries are).
                       "server_token": self._token or self._inherited_token,
                       "previous_version": self._predecessor_version,
                       "update_refused": self._update_refused,
                       "running_since": self._began, "server_starts": self.starts,
                       "crashes": len(self._crashes), "crash_window_minutes": round(self.crash_window / 60)}
        try:
            write_json(data_file(STATE_FILE), self._state)
        except OSError as e:
            logger.error("Could not write %s: %s", STATE_FILE, e)

    # ---- Which code the server runs ---------------------------------------------------

    def current(self):
        """(the version to run, its code folder): current.txt's for an installed app, or
        the one kept when a hand-over to current.txt's failed."""
        if self.installed and self._pinned and self._clock() < (self._pinned_until or 0):
            return self._pinned, os.path.join(self.installed, "versions", self._pinned)
        if self.installed:
            version = read_current(self.installed)
            if version:
                return version, os.path.join(self.installed, "versions", version)
        return self.own_version, self.code_root

    # ---- The child ------------------------------------------------------------------

    def child_alive(self):
        return self._child is not None and self._child.poll() is None

    def start_child(self):
        version, code = self.current()
        remove(data_file(SERVER_FILE))
        self._token = secrets.token_hex(16)
        env = dict(self._env if self._env is not None else os.environ)
        env[TOKEN] = self._token
        log_path = os.path.join(logs.log_dir(), CONSOLE_LOG)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        try:
            if os.path.getsize(log_path) > CONSOLE_LOG_MAX:
                os.replace(log_path, log_path + ".1")
        except OSError:
            pass
        with open(log_path, "ab") as output:
            self._child = processes.start(self._command(code) + self.server_args, env=env, cwd=tagpup_config.home(),
                                          stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
        self._child_version = version
        self._child_started = self._clock()
        self._next_health = self._child_started + self.health_every
        self._unanswered = 0
        self._restart_at = None
        self.starts += 1
        logger.info("Started the server (pid %d) from %s.", self._child.pid, version or code)
        self._say("running")

    def stop_child(self):
        """End the server and what it started. Only once it is drained, or not serving.
        True when it has ended; one that did not is kept, still watched."""
        child = self._child
        if child is None:
            return True
        if child.poll() is None:
            processes.kill_tree(child.pid)
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                logger.error("The server (pid %d) did not end.", child.pid)
                return False
        self._child = None
        remove(data_file(SERVER_FILE))
        logger.info("Stopped the server (pid %d).", child.pid)
        return True

    def _child_ended(self, code):
        """The server ended without being asked. False when it has crashed too often."""
        child, self._child = self._child, None
        now = self._clock()
        ran = now - (self._child_started or now)
        if code == PORTS_TAKEN:
            if not self._ports_said:
                logger.warning("Another server answers on the ports (one started by hand?); starting when "
                               "they are free, every %ds.", self.ports_wait)
                self._ports_said = True
            self._restart_at = now + self.ports_wait
            self._say("waiting for the ports", "another server answers on them")
            return True
        self._ports_said = False
        self._crashes = [at for at in self._crashes if now - at < self.crash_window] + [now]
        if len(self._crashes) >= self.max_crashes:
            why = ("the server ended %d times in %d minutes (the last with exit code %s after %ds); giving up. "
                   "Its output is in %s" % (len(self._crashes), round(self.crash_window / 60), code, ran,
                                            os.path.join(logs.log_dir(), CONSOLE_LOG)))
            logger.error("Gave up: %s", why)
            self._say("gave up", why)
            return False
        delay = self.backoff[min(len(self._crashes), len(self.backoff)) - 1]
        logger.warning("The server (pid %d) ended with exit code %s after %ds; starting it again in %ss "
                       "(%d of %d in %d minutes).", child.pid, code, ran, delay, len(self._crashes),
                       self.max_crashes, round(self.crash_window / 60))
        self._restart_at = now + delay
        self._say("restarting", "exit code %s" % code)
        return True

    # ---- Is it answering? --------------------------------------------------------------

    def answers(self):
        """Does the server answer GET /api/server within health_timeout? True for one not
        serving yet (no server.json): it is starting."""
        where = server()
        if where is None:
            return True
        port = sorted(where.get("ports", {}).values())[0]
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/server" % port, timeout=self.health_timeout) as reply:
                reply.read()
            return True
        except urllib.error.HTTPError:
            return True   # it answered
        except (OSError, ValueError):
            return False

    def check_health(self):
        """Ask the server how it is; after max_unanswered unanswered in a row, end it and
        count it as a crash. False when that crash was one too many."""
        self._next_health = self._clock() + self.health_every
        if not self.child_alive() or self.answers():
            self._unanswered = 0
            return True
        self._unanswered += 1
        logger.warning("The server did not answer in %ss (%d of %d).", self.health_timeout, self._unanswered,
                       self.max_unanswered)
        if self._unanswered < self.max_unanswered:
            return True
        self._unanswered = 0
        logger.error("The server (pid %d) stopped answering; ending it.", self._child.pid)
        child = self._child
        processes.kill_tree(child.pid)
        try:
            child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            logger.error("The server (pid %d) did not end.", child.pid)
        remove(data_file(SERVER_FILE))
        return self._child_ended("no answer")

    # ---- Draining and moving ----------------------------------------------------------

    def drain(self, quiet=0):
        """Ask the server to drain: {"drained": bool, "waiting_for": [...]}, after `quiet`
        seconds without a request. A server that is not up yet, or has ended, has
        nothing in flight."""
        if not self.child_alive():
            return {"drained": True}
        where = server()
        if where is None:
            return {"drained": True, "note": "not serving yet"}
        return self._drain_at(where, self._token, quiet)

    def _drain_at(self, where, token, quiet=0):
        port = sorted(where.get("ports", {}).values())[0]
        request = urllib.request.Request(
            "http://127.0.0.1:%d/api/server/drain" % port, method="POST",
            data=json.dumps({"seconds": self.drain_seconds, "quiet": quiet}).encode("utf-8"),
            headers={"Content-Type": "application/json", TOKEN_HEADER: token or ""})
        try:
            with urllib.request.urlopen(request, timeout=self.drain_seconds + 30) as reply:
                return json.loads(reply.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return {"drained": False, "waiting_for": ["the server refused the drain (%d)" % e.code]}
        except (OSError, ValueError) as e:
            return {"drained": False, "waiting_for": ["no answer from the server (%s)" % e]}

    def _drained(self, purpose, quiet=0):
        """Drain for `purpose`; True once drained. Logs what it waits for when that changes."""
        answer = self.drain(quiet)
        if answer.get("drained"):
            self._last_wait = None
            return True
        waiting = ", ".join(answer.get("waiting_for") or ["the server"])
        if waiting != self._last_wait:
            logger.info("%s waits for %s; asking again every %ds.", purpose, waiting, self.retry_move)
            self._last_wait = waiting
        return False

    def install(self):
        """Run the installer (install_newer), and keep whether it refused the checkout as
        not newer, for status to show."""
        said = self._install() or []
        refused = next((line for line in said if NOT_NEWER in line), None)
        before = self._update_refused
        if refused is None:
            self._update_refused = None
        elif before is None or before.get("said") != refused:
            self._update_refused = {"said": refused, "since": _now()}
        if self._update_refused != before:
            self._say(self._state.get("state") or "running", self._state.get("why"))

    def look_for_update(self):
        """Install a newer version if there is one; note it to move onto."""
        self._next_update = self._clock() + self.update_every
        if not self.installed:
            return
        self.install()
        # What is installed: current.txt, never the version kept after a failed hand-over.
        version = read_current(self.installed)
        if version and version != self._child_version:
            logger.info("Version %s is installed; the server moves onto it at the next quiet moment.", version)
            self._pending = version
            self._pending_since = self._clock()
            self._next_move = 0
            self._say("moving", "to %s" % version)

    def move(self):
        """Move the server onto the pending version once it drains. "moved", "handed over",
        or None while it waits."""
        self._next_move = self._clock() + self.retry_move
        # Someone using the app is not moved under, until the update has waited an hour.
        waited = self._clock() - (self._pending_since if self._pending_since is not None else self._clock())
        quiet = QUIET if waited < self.patience else 0
        if not self._drained("The move to %s" % self._pending, quiet):
            return None
        logger.info("The server is drained; moving it from %s to %s.", self._child_version, self._pending)
        version, self._pending = self._pending, None
        if self.installed and self.hand_over and version != self.own_version:
            if self.hand_over_to(version):
                return HANDED_OVER
            # The new version's supervisor did not start: this one goes on with the
            # version it runs, and tries again after its patience.
            self._keep(self._child_version)
            self._resume_server()
            return None
        self._pinned = None
        self.stop_child()
        self.start_child()
        return "moved"

    def hand_over_to(self, version):
        """Start a supervisor from `version` and, once it says it is up, stop the server
        for it. False, the server left running, when it does not come up."""
        remove(data_file(HANDOVER_FILE))
        logger.info("Starting a supervisor from %s to take over.", version)
        started = start_in_background(self.installed, self._python, handed_over=True, more=self._passed_on)
        deadline = time.monotonic() + self.hand_over_wait
        ready = None
        while time.monotonic() < deadline and started.poll() is None:
            record = read_json(data_file(HANDOVER_FILE))
            if record and record.get("version") == version and _alive(record):
                ready = record
                break
            time.sleep(0.1)
        if ready is None:
            why = ("it exited with %s" % started.poll() if started.poll() is not None
                   else "it said nothing in %ds" % self.hand_over_wait)
            logger.error("The supervisor of %s did not start (%s); staying on %s.", version, why,
                         self._child_version or self.own_version)
            if started.poll() is None:
                processes.kill_tree(started.pid)
            return False
        if not self.stop_child():
            # Never a hand-over with the old server still running and nothing watching it.
            logger.error("The server would not end; the supervisor of %s is stopped, and this one stays.", version)
            processes.kill_tree(started.pid)
            self._resume_server()
            return False
        self._successor = ready
        self._say("handed over", "to %s (pid %s)" % (version, ready.get("pid")))
        return True

    def _keep(self, version):
        """Stay on `version` -- the server started from it, whatever current.txt says --
        for the patience, and look for a newer one again after it."""
        self._pinned = version
        self._pinned_until = self._clock() + self.patience
        self._next_update = self._clock() + self.patience

    def _resume_server(self):
        """Tell a drained server to take work again (a move that did not happen)."""
        where = server()
        if where is None or not self.child_alive():
            return
        port = sorted(where.get("ports", {}).values())[0]
        request = urllib.request.Request("http://127.0.0.1:%d/api/server/resume" % port, method="POST", data=b"",
                                         headers={TOKEN_HEADER: self._token or ""})
        try:
            with urllib.request.urlopen(request, timeout=30) as reply:
                reply.read()
        except (OSError, ValueError) as e:
            logger.error("Could not tell the server to take work again: %s", e)

    def _taken_over(self, version):
        """Did the supervisor handed over to take over -- take the lock, and its server,
        of `version`, answer and go on answering for `settle` seconds? (True, None) or
        (False, why)."""
        successor = self._successor or {}
        deadline = time.monotonic() + self.hand_over_wait
        while True:
            now = running()
            if now and now.get("pid") == successor.get("pid"):
                break
            if time.monotonic() >= deadline:
                return False, "it did not take the lock"
            time.sleep(0.1)
        deadline = time.monotonic() + self.hand_over_wait
        while True:
            where = server()
            if where and where.get("version") == version and self._answers_as(where, version):
                break
            if time.monotonic() >= deadline:
                return False, "its server did not answer in %ds" % self.hand_over_wait
            time.sleep(0.2)
        time.sleep(self.settle)
        if not (_alive(where) and self._answers_as(where, version)):
            return False, "its server stopped answering within %ds" % self.settle
        return True, None

    def _answers_as(self, where, version):
        """Does the server at `where` answer GET /api/server naming `version`?"""
        port = sorted(where.get("ports", {}).values())[0]
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/server" % port, timeout=self.health_timeout) as reply:
                return json.loads(reply.read().decode("utf-8")).get("version") == version
        except (OSError, ValueError):
            return False

    def point_back(self, failed, previous):
        """current.txt names `failed`, a release that did not run: point it back to
        `previous`, so the next start -- the next login -- is not the same failure."""
        if not (self.installed and failed and previous and previous != failed):
            return
        if read_current(self.installed) != failed or not os.path.isdir(os.path.join(self.installed, "versions", previous)):
            return
        path = os.path.join(self.installed, "current.txt")
        try:
            with open(path + ".writing", "w", encoding="utf-8") as handle:
                handle.write(previous)
            _while_read(lambda: os.replace(path + ".writing", path), path)
        except OSError as e:
            logger.error("Could not point current.txt back to %s: %s", previous, e)
            return
        logger.error("current.txt points back to %s: %s did not run.", previous, failed)

    # ---- Running ----------------------------------------------------------------------

    def stop(self):
        """Ask run() to drain the server and return (a test's; the owner's is STOP_FILE)."""
        self._stop.set()

    def _stop_asked(self):
        return self._stop.is_set() or os.path.exists(data_file(STOP_FILE))

    def _stop_now(self):
        self._say("stopping", "asked to")
        logger.info("Asked to stop: draining the server first.")
        give_up = self._clock() + self.stop_drain_limit
        while self.child_alive() and not self._drained("Stopping"):
            if self._clock() >= give_up:
                logger.warning("The server did not drain in %d minutes (%s); ending it anyway, as asked.",
                               round(self.stop_drain_limit / 60), self._last_wait or "no answer")
                break
            time.sleep(min(self.retry_move, max(0.0, give_up - self._clock())))
        self.stop_child()
        remove(data_file(STOP_FILE))
        self._say("stopped", "asked to")
        logger.info("Stopped.")
        return 0

    def end_orphan(self):
        """A server a supervisor before this one left running -- it died, was ended, or
        crashed -- holds the ports, and nothing watches it. Drained first when its token
        was kept (data/supervisor.json), for at most STOP_DRAIN_LIMIT; then ended."""
        where = server()
        if where is None:
            self._inherited_token = None
            return
        token = self._inherited_token or (last_state() or {}).get("server_token")
        logger.warning("A server (pid %s) a supervisor before this one left running holds the ports; ending it%s.",
                       where.get("pid"), " once it drains" if token else "")
        give_up = self._clock() + self.stop_drain_limit
        while token and self._clock() < give_up and _alive(where):
            answer = self._drain_at(where, token)
            if answer.get("drained"):
                break
            time.sleep(min(self.retry_move, max(0.0, give_up - self._clock())))
        # Only the process it was: a pid is soon another's once its process has ended.
        if _alive(where):
            processes.kill_tree(where["pid"])
        deadline = time.monotonic() + 30
        while _alive(where) and time.monotonic() < deadline:
            time.sleep(0.1)
        remove(data_file(SERVER_FILE))
        self._inherited_token = None

    def run(self):
        """Keep the server running until asked to stop (0), it crashes too often (GAVE_UP),
        or a new version's supervisor takes over (0)."""
        remove(data_file(STOP_FILE))
        self.end_orphan()
        self._say("starting")
        self._next_update = self._clock() + self.update_every
        self.start_child()
        while True:
            if self._stop_asked():
                return self._stop_now()
            if self._child is not None:
                code = self._child.poll()
                if code is not None and not self._child_ended(code):
                    return GAVE_UP
            if self._child is None and self._restart_at is not None and self._clock() >= self._restart_at:
                self.start_child()
            if self._child is not None and self._clock() >= self._next_health and not self.check_health():
                return GAVE_UP
            if self._pending is None and self._clock() >= self._next_update:
                self.look_for_update()
            if self._pending is not None and self._clock() >= self._next_move:
                if self.move() == HANDED_OVER:
                    return HANDED_OVER
            self._stop.wait(self.tick)

    def main(self, wait_for_lock=0.0, update_first=True, handed_over=False):
        """Take the home's lock, then run(). ALREADY_RUNNING when another holds it. Started
        to take over (`handed_over`), it says it is up before it waits for the lock."""
        if handed_over:
            write_json(data_file(HANDOVER_FILE), {"pid": os.getpid(), "started": processes.started(os.getpid()),
                                                  "version": self.own_version})
        # Before this one writes supervisor.json: the token of a server left running, and,
        # taking over, the version the one before ran.
        before = last_state() or {}
        self._inherited_token = before.get("server_token")
        if handed_over:
            self._predecessor_version = before.get("server_version") or before.get("version")
        lock = Lock(data_file(LOCK_FILE))
        if not lock.acquire(wait_for_lock):
            other = running() or {}
            logger.warning("Another TagPup supervisor is running in this home (pid %s); this one stops.",
                           other.get("pid", "unknown"))
            return ALREADY_RUNNING
        try:
            logger.info("The supervisor (pid %d) starts, %s.", os.getpid(),
                        "version %s" % self.own_version if self.own_version else "from a checkout")
            if self.installed and update_first:
                # A server left running is seen to first, with the token kept for it.
                self.end_orphan()
                # What a launcher does before it starts its app; and a supervisor started
                # from an older version hands over to one started from the newer.
                self.install()
                version, _code = self.current()
                if version and self.own_version and version != self.own_version:
                    logger.info("Version %s is current; handing over to a supervisor started from it.", version)
                    if self.hand_over_to(version):
                        result = HANDED_OVER
                    else:
                        self._keep(self.own_version)
                        result = self.run()
                else:
                    result = self.run()
            else:
                result = self.run()
            while result == HANDED_OVER:
                ours = self._child_version or self.own_version
                target = (self._successor or {}).get("version")
                lock.release()
                taken, why = self._taken_over(target)
                if taken:
                    remove(data_file(HANDOVER_FILE))
                    return 0
                logger.error("The supervisor of %s did not take over (%s); ending it, and starting the server "
                             "again on %s.", target, why, ours)
                if self._successor and self._successor.get("pid"):
                    processes.kill_tree(self._successor["pid"])
                if not lock.acquire(self.hand_over_wait):
                    return ALREADY_RUNNING
                self.point_back(target, ours)
                self._keep(ours)
                # Its server, if it left one, is seen to with the token it kept.
                self._inherited_token = (last_state() or {}).get("server_token")
                result = self.run()
            if result == GAVE_UP and handed_over:
                self.point_back(self.own_version, self._predecessor_version)
            return result
        finally:
            # Leaving any other way than by its own stop -- an error -- the server goes
            # with it: nothing would watch it, and the next supervisor would find it
            # holding the ports.
            if self.child_alive():
                logger.error("The supervisor is ending unexpectedly; ending its server (pid %d) too.",
                             self._child.pid)
                self.stop_child()
            lock.release()


def main(argv=None):
    parser = argparse.ArgumentParser(description="TagPup's always-on process: runs the web server, restarts it "
                                                 "after a crash, and moves it onto a newer installed version.")
    parser.add_argument("--installed", default=None,
                        help="the installed app's folder, whose current.txt names the version to run; without "
                             "it the server runs from this code, and is never moved")
    parser.add_argument("--handed-over", action="store_true",
                        help="started by a supervisor moving onto this version: wait for it to let go")
    parser.add_argument("--server-args", default="",
                        help="more arguments for the server (ports and a library for a sandbox or a test)")
    parser.add_argument("--update-every", type=float, default=UPDATE_EVERY, help=argparse.SUPPRESS)
    parser.add_argument("--hand-over-wait", type=float, default=HAND_OVER_WAIT, help=argparse.SUPPRESS)
    parser.add_argument("--settle", type=float, default=SETTLE, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    logs.to_file("supervisor")
    passed_on = []
    if args.server_args:
        passed_on += ["--server-args", args.server_args]
    if args.update_every != UPDATE_EVERY:
        passed_on += ["--update-every", str(args.update_every)]
    if args.hand_over_wait != HAND_OVER_WAIT:
        passed_on += ["--hand-over-wait", str(args.hand_over_wait)]
    if args.settle != SETTLE:
        passed_on += ["--settle", str(args.settle)]
    try:
        supervisor = Supervisor(installed=os.path.abspath(args.installed) if args.installed else None,
                                server_args=shlex.split(args.server_args), update_every=args.update_every,
                                hand_over_wait=args.hand_over_wait, passed_on=passed_on, settle=args.settle)
        return supervisor.main(wait_for_lock=args.hand_over_wait if args.handed_over else 0.0,
                               update_first=not args.handed_over, handed_over=args.handed_over)
    except Exception:
        # Under pythonw.exe nothing shows an uncaught error; the log is all there is.
        logger.exception("The supervisor failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
