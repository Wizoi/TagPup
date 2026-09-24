"""A log file for each program, so what happened is still there after the window closes.

The apps logged to the console and nowhere else. It scrolled away, it went when the
window closed, and a question like "was that click slow on the server or in the
browser" had nothing to answer it afterwards. Each program now also writes
<data_dir>/logs/<program>.log, rotating at 5 MB and keeping five old files, and the
servers log every request slower than a second and every request that failed
(scripts/localserver.py).
"""
import logging
import logging.handlers
import os

from tagpup import config

FORMAT = "%(asctime)s [%(levelname)s] %(threadName)s %(name)s - %(message)s"
MAX_BYTES = 5 * 1024 * 1024
KEEP = 5

#: Where the servers log slow and failed requests.
REQUESTS = "tagpup.requests"


def log_dir():
    return os.path.join(config.data_dir(), "logs")


def to_file(program, level=logging.INFO):
    """Also log to <data_dir>/logs/<program>.log, from this process. Returns the path.

    Call it from the process that does the work. Under the reloader that is the child,
    not the supervisor; two processes rotating one file fail on Windows. A second call
    for the same file adds nothing.
    """
    folder = log_dir()
    os.makedirs(folder, exist_ok=True)
    path = os.path.abspath(os.path.join(folder, program + ".log"))
    root = logging.getLogger()
    for handler in root.handlers:
        # A FileHandler keeps os.path.abspath of the name it was given, which is `path`.
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == path:
            return path
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=KEEP, encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.setLevel(level)
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    return path
