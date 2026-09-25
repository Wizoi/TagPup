"""Start TagPup and TagTuner: one process, TagPup on 8090 and TagTuner on 8080.

    .venv/Scripts/python.exe tagpup_web.py --open tagpup
    .venv/Scripts/python.exe tagpup_web.py --open tuner --reload

Each app had a launcher of its own (tagpup_gui.py, tagtuner.py), each starting a server
of its own (docs/ARCHITECTURE.md, phase 5). Started while the server is already
running, this opens the page in the browser and stops: the installer's TagPup.cmd and
TagTuner.cmd both run it, so double-clicking either twice reaches the running server
rather than a second one. `--reload` restarts the process when a .py file is saved,
for development only (scripts/reloader.py); the installed copy never changes under
its server.
"""
import argparse
import logging
import os
import socket
import sys
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from tagpup import config as tagpup_config  # noqa: E402
from tagpup import logs  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.web import app as web  # noqa: E402

logger = logging.getLogger("tagpup_web")

#: The port each app answers on, as they always have.
PORTS = {"tagpup": 8090, "tuner": 8080}

#: The reloader's environment variables: `_CHILD` marks the process holding the
#: server, `_RELOADED` a restart.
RELOADER = "TAGPUP_WEB_RELOADED"


def page_url(port):
    return "http://localhost:%d/" % port


def answering(port):
    """Is something already answering on `port` on this machine?"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def open_page(url, reloading):
    """Open `url` in the browser once, from the process that serves.

    The reloader runs a parent supervisor and a child; the child is the one with the
    server. A guard on the restart flag alone opened a tab from each, since neither
    has it on a first start (tests/test_browser_opens_once.py). `serving` means "I am
    the server": without the reloader, this process; with it, its child. `restarting`
    means "this is a restart, they already have a tab open".
    """
    serving = not reloading or bool(os.environ.get(RELOADER + "_CHILD"))
    restarting = bool(os.environ.get(RELOADER))
    if serving and not restarting:
        webbrowser.open(url)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tagpup-port", type=int, default=PORTS["tagpup"])
    parser.add_argument("--tuner-port", type=int, default=PORTS["tuner"])
    parser.add_argument("--db", default=None,
                        help="the library to start on, a file in the data folder (default: the configured one)")
    parser.add_argument("--open", choices=("tagpup", "tuner", "none"), default="none",
                        help="open this app's page in the browser once the server answers")
    parser.add_argument("--reload", action="store_true", help="restart when a .py file is saved (development)")
    args = parser.parse_args(argv)

    # The serving process writes the log; the reloader's supervisor only restarts it,
    # and two processes rotating one file fail on Windows.
    serving = not args.reload or bool(os.environ.get(RELOADER + "_CHILD"))
    if serving:
        logger.info("Logging to %s", logs.to_file("tagpup_web"))

    ports = {"tagpup": args.tagpup_port, "tuner": args.tuner_port}
    if args.open != "none" and answering(ports[args.open]):
        logger.info("A server is already answering on port %d; opening its page.", ports[args.open])
        open_page(page_url(ports[args.open]), args.reload)
        return 0

    if args.reload:
        from reloader import start_reloader_thread
        start_reloader_thread(RELOADER)

    db_path = tagpup_config.library_path(args.db) if args.db else tagpup_config.default_library()
    if not os.path.exists(db_path):
        logger.info("No library at %s; making one.", db_path)
        library_actions.create(db_path)
    startup = Library(db_path)
    apps = {ports[kind]: web.create_app(kind, startup=startup) for kind in ("tagpup", "tuner")}
    ready = None
    if args.open != "none":
        def ready():
            open_page(page_url(ports[args.open]), args.reload)
    web.serve(apps, ready=ready)
    return 0


if __name__ == "__main__":
    sys.exit(main())
