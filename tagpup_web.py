"""Start TagPup and TagTuner: one process, TagPup on 8090 and TagTuner on 8080.

    .venv/Scripts/python.exe tagpup_web.py --open tagpup
    .venv/Scripts/python.exe tagpup_web.py --open tuner --reload

Each app had a launcher of its own (tagpup_gui.py, tagtuner.py), each starting a server
of its own (docs/ARCHITECTURE.md, phase 5). Started while the server is already
running, this opens the page in the browser and stops: the installer's TagPup.cmd and
TagTuner.cmd both run it, so double-clicking either twice reaches the running server
rather than a second one. A server of another version -- older, newer, or run from a
checkout -- is asked to finish what it is doing and is replaced (tagpup.launcher): a
long job is waited out, its page opened meanwhile. When the owner has chosen the always-on process
(scripts/startup.py) and it is not running, they start it rather than a server of their
own, and open the page once it answers. `--reload` restarts the process when a .py file
is saved, for development only (scripts/reloader.py); the installed copy never changes
under its server.

Run by the always-on process (tagpup.supervisor), which hands it a token in the
environment, it says where it answers in data/server.json, lets the supervisor drain it
before an update (tagpup.web.lifecycle), and exits PORTS_TAKEN when another server
already answers on its ports. Beside its requests it runs the background tasks
(tagpup.runtime.BACKGROUND): the recurring jobs, letting idle models go, and watching
each library's folders (tagpup.jobs.watching).
"""
import argparse
import logging
import os
import secrets
import socket
import sys
import time
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from tagpup import config as tagpup_config  # noqa: E402
from tagpup import launcher  # noqa: E402
from tagpup import logs  # noqa: E402
from tagpup import supervisor  # noqa: E402
from tagpup.core import library as libraries  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web.lifecycle import Lifecycle  # noqa: E402

logger = logging.getLogger("tagpup_web")

#: The port each app answers on, as they always have. The apps are told them, so a page
#: can link to the other (/api/apps).
PORTS = {"tagpup": 8090, "tuner": 8080}

#: The reloader's environment variables: `_CHILD` marks the process holding the
#: server, `_RELOADED` a restart.
RELOADER = "TAGPUP_WEB_RELOADED"

#: How long a launcher waits for the always-on process it started to answer.
BACKGROUND_START_SECONDS = 120

#: How many times a launcher makes way and tries to bind, and how long between: another
#: launch may take the ports first, or an ended server still be letting them go.
BIND_TRIES = 20
BIND_RETRY_SECONDS = 1.0


def page_url(port):
    return "http://localhost:%d/" % port


def answering(port):
    """Is something already answering on `port` on this machine?"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def open_page(url):
    """Open `url` in the browser once, from the process that serves.

    The reloader runs a parent supervisor and a child; the child is the one with the
    server. A guard on the restart flag alone opened a tab from each, since neither
    has it on a first start (tests/test_browser_opens_once.py). `serving` means "I am
    the server" -- with the reloader, its child; without it, this process, which
    main() marks as the child for that reason. `restarting` means "this is a restart,
    they already have a tab open".
    """
    serving = bool(os.environ.get("TAGPUP_WEB_RELOADED_CHILD"))
    restarting = bool(os.environ.get("TAGPUP_WEB_RELOADED"))
    if serving and not restarting:
        webbrowser.open(url)


def tell(line):
    """Say `line` at the launcher's window -- the log goes only to its file -- and log it."""
    logger.info(line)
    try:
        print("TagPup: " + line, flush=True)
    except (OSError, ValueError, AttributeError):
        pass   # no console (pythonw.exe)


def open_on_the_background_server(installed, port):
    """The launcher's way when the owner chose the always-on process: start it unless it
    runs, wait for its server to answer on `port`, and open the page. The exit code."""
    if supervisor.running() is None:
        logger.info("Starting the always-on process (%s).", supervisor.BACKGROUND_LAUNCHER)
        supervisor.start_in_background(installed)
    deadline = time.monotonic() + BACKGROUND_START_SECONDS
    while time.monotonic() < deadline:
        if answering(port):
            open_page(page_url(port))
            return 0
        time.sleep(0.5)
    logger.error("The always-on process did not answer on port %d in %ds; see data/logs/supervisor.log.",
                 port, BACKGROUND_START_SECONDS)
    return 1


def served_libraries(startup=None):
    """The libraries whose models are warmed at start: the startup library, or else each
    one the picker offers in the data folder, of which the warm-up loads the one set of
    models most of them share (tagpup.runtime.Runtime.warm_up)."""
    if startup is not None:
        return [startup]
    folder = tagpup_config.data_dir()
    files = os.listdir(folder) if os.path.isdir(folder) else []
    return [Library(tagpup_config.library_path(libraries.for_mode(name + ".db", False)))
            for name in libraries.picker_names(files, False)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tagpup-port", type=int, default=PORTS["tagpup"])
    parser.add_argument("--tuner-port", type=int, default=PORTS["tuner"])
    parser.add_argument("--db", default=None,
                        help="serve this library (a name in the data folder, or a path) to a URL naming "
                             "none, making it if it is missing: for a sandbox or a test. Normally none: a "
                             "page is reached by its library's URL, and the browser remembers the last one "
                             "(docs/findings.md, #100).")
    parser.add_argument("--open", choices=("tagpup", "tuner", "none"), default="none",
                        help="open this app's page in the browser once the server answers")
    parser.add_argument("--reload", action="store_true", help="restart when a .py file is saved (development)")
    parser.add_argument("--release-models-after", type=float, metavar="MINUTES",
                        default=runtimes.RELEASE_MODELS_AFTER_MINUTES,
                        help="let the models go when none has been used for this long; the next Suggest loads "
                             "them again; 0 keeps them (default: %(default)s)")
    parser.add_argument("--listen", choices=web.LISTEN, default=web.LOCAL,
                        help="local (the default): answer this PC only. lan: every interface, for phase 10, "
                             "when the apps have logins; until then the apps answer anyone who can reach them")
    parser.add_argument("--installed", default=None,
                        help="the installed app's folder, which the launchers name: with --open, when the "
                             "owner chose the always-on process there (scripts/startup.py), start it rather "
                             "than a server of this process's own")
    args = parser.parse_args(argv)

    # Without the reloader this process is the server, which is what the child flag
    # means; setting it here lets the log and the browser ask one question either way.
    if not args.reload:
        os.environ[RELOADER + "_CHILD"] = "1"

    # The serving process writes the log; the reloader's supervisor only restarts it,
    # and two processes rotating one file fail on Windows.
    if os.environ.get(RELOADER + "_CHILD"):
        logger.info("Logging to %s", logs.to_file(logs.SERVER_PROGRAM))

    ports = {"tagpup": args.tagpup_port, "tuner": args.tuner_port}
    version = tagpup_config.code_version()
    sockets = None
    opened = False
    if args.open != "none" and supervisor.always_on(args.installed):
        if answering(ports[args.open]):
            logger.info("A server is already answering on port %d; opening its page.", ports[args.open])
            open_page(page_url(ports[args.open]))
            return 0
        return open_on_the_background_server(args.installed, ports[args.open])
    if args.open != "none":
        # What answers on the port: this version's server (its page is opened), or another
        # version's, which makes way (tagpup.launcher). Then the ports, bound before anything
        # else is started: a launch that loses them to another opens the winner's page.
        url = page_url(ports[args.open])
        for _attempt in range(BIND_TRIES):
            way, shown = launcher.make_way(ports[args.open], version, say=tell,
                                           open_page=lambda: open_page(url), answering=answering)
            opened = opened or shown
            if way == launcher.OPEN:
                if not opened:
                    open_page(url)
                return 0
            if args.reload:
                break   # the reloader's child binds, in serve()
            try:
                sockets = web.bind_all(list(ports.values()), args.listen)
                break
            except OSError as e:
                tell("The ports are not free yet (%s); trying again." % e)
                time.sleep(BIND_RETRY_SECONDS)
        else:
            logger.error("Could not bind ports %s after %d tries; see the lines above.",
                         ", ".join(str(port) for port in ports.values()), BIND_TRIES)
            return 1

    # The always-on process's child: it hands this server a token -- taken out of the
    # environment, so no program the server starts inherits it -- and waits rather than
    # counting a crash while another server holds the ports.
    token = os.environ.pop(supervisor.TOKEN, None)
    if token:
        taken = [port for port in ports.values() if answering(port)]
        if taken:
            logger.warning("A server is already answering on port %d; this one is not started.", taken[0])
            return supervisor.PORTS_TAKEN

    if args.reload:
        from reloader import start_reloader_thread
        start_reloader_thread(RELOADER)

    startup = None
    if args.db:
        db_path = args.db if os.path.isabs(args.db) else tagpup_config.library_path(libraries.file_name_for(args.db))
        if not os.path.exists(db_path):
            logger.info("No library at %s; making one.", db_path)
            made = library_actions.create(db_path)
            if made.refused:
                # A name the library-name rule refuses makes nothing; serving it would
                # answer every request against a library that is not there.
                logger.error("Could not make a library at %s: %s", db_path, made.refused)
                return 2
        startup = Library(db_path)
    # The process's models, one per set of settings a library names, given to both apps;
    # one set warmed on a thread of their own -- the startup library's, or the one most
    # libraries in the data folder share -- never a library held open (#99) -- and let go
    # after they have gone unused for a while.
    idle = args.release_models_after * 60 if args.release_models_after and args.release_models_after > 0 else None
    # It keeps its models between runs -- with 0, for good -- and gives them up, with its
    # turn on the graphics card, when another process waits for it (tagpup.ml.gpu, #774).
    runtime = Runtime(idle_after=idle, keep_models=True)
    # What runs beside the requests (tagpup.runtime.BACKGROUND): the recurring jobs --
    # snapshots, pruning the journal -- of every library in the data folder, in the
    # process that runs them (none a test started), and letting idle models go
    # (docs/ARCHITECTURE.md, phase 8).
    background = runtimes.background(runtime)
    # A launcher of another version drains this server with the token in its record
    # (tagpup.launcher), written once it serves.
    launch_token = secrets.token_hex(16)
    lifecycle = Lifecycle(version=version, token=token, background=background, launcher_token=launch_token)
    apps = {ports[kind]: web.create_app(kind, startup=startup, runtime=runtime, ports=ports, lifecycle=lifecycle)
            for kind in ("tagpup", "tuner")}
    # Each library's migrations now, beside the serving, not in the first request (#661); a drain
    # waits for them (Lifecycle.long_work, #664).
    library_actions.bring_up_to_date_in_background(served_libraries(startup))
    if not os.environ.get("TAGPUP_WEB_NO_WARMUP"):
        runtime.warm_up_in_background(served_libraries(startup))
    background.start()

    def ready():
        if token:
            supervisor.write_server(ports, version)
        launcher.say_where(ports, version, launch_token, supervised=bool(token))
        # Not again when the page of the server it replaced was opened while it waited:
        # that page says it was updated (web/common/api.js).
        if args.open != "none" and not opened:
            open_page(page_url(ports[args.open]))
    try:
        web.serve(apps, ready=ready, listen=args.listen, sockets=sockets)
    finally:
        background.stop()
        launcher.forget(ports)
        if token:
            supervisor.forget_server()
    return 0


if __name__ == "__main__":
    sys.exit(main())
