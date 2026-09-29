"""The Activity page and what it asks: the background work of every library, in one place
(docs/ARCHITECTURE.md, phase 8.5). Both apps serve it alike, at /activity/ -- not under a
library's address: it covers every library in the home's data folder.

Modelled on how Immich (Jobs), Jellyfin (Scheduled Tasks, Logs), Hangfire and Sidekiq
(history, failures kept in view, run now) and Home Assistant (logs by source, filtered,
raw, downloaded) show background work. What it asks:

- GET /api/activity/now, which the page asks every few seconds while it is in view: what
  runs now -- each library's index queue and Suggest runs, the recurring jobs running in
  any process (their `running` rows), the folder watcher's sync, and whether the server
  takes work or waits to move onto a new version. Cheap: memory, and one small read of
  each library's `job_runs`.
- GET /api/activity/jobs: each recurring job, why it is scheduled, and for each library
  its last runs, what each changed as counts and why a failed one failed, and when it is
  due; POST /api/activity/jobs/run runs one now, on the recurring jobs' runner.
- GET /api/activity/sync, /snapshots, /server, /timeline: each library's syncs and
  watched folders, its snapshots, the always-on process, and one timeline of what was done.
- GET /api/activity/attention: what needs the owner -- each library's photos found damaged
  (tagpup.services.damaged_photos), with their paths and TagPup's page on each folder;
  POST /api/activity/attention/check reads them again now (Check again).
- GET /api/activity/logs and /api/activity/logs/<name>[/raw|/download]: the logs in
  data/logs, read from the end and never whole (tagpup.logs.read).

It answers this PC alone: a request from any other address is refused 403 whatever
address the server listens on (it listens on loopback: tagpup.web.app.bind). What it
shows names folders -- the watched roots, the log lines, a failed run's error -- as
TagTuner's own dialogs do.
"""
import logging
import os
import time
import urllib.parse

from flask import Blueprint, Response, abort, current_app, jsonify, request, send_file

from tagpup import logs
from tagpup import runtime as runtimes
from tagpup import supervisor
from tagpup.core import library as libraries
from tagpup.core import paths, runs
from tagpup.jobs import indexing as indexing_jobs
from tagpup.jobs import recurring
from tagpup.jobs import suggestions as suggestion_jobs
from tagpup.services import activity
from tagpup.services import damaged_photos
from tagpup.services import indexing
from tagpup.services import job_runs
from tagpup.web import responses

logger = logging.getLogger(__name__)

#: The page's folder, beside the other pages, and its route (tagpup.core.library.ROUTES).
PAGE = libraries.ACTIVITY_PAGE
routes = Blueprint(PAGE, __name__)
PAGE_FILES = {"index.html": "text/html; charset=utf-8", "style.css": "text/css; charset=utf-8"}
SCRIPT_TYPE = "application/javascript; charset=utf-8"
NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"}

#: The addresses of this PC: a request from any other is refused.
LOOPBACK = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})

#: The background tasks the page asks (tagpup.runtime.BACKGROUND).
WATCHER, JOBS = "folder watcher", "recurring jobs"

#: A job's runs listed when the page asks for no number, and the most; the same for the timeline.
RUNS, MOST_RUNS = 10, 50
TIMELINE, MOST_TIMELINE = 50, 500
#: Log records when the page asks for no number, and the most.
LINES, MOST_LINES = 200, 1000


@routes.before_request
def only_this_pc():
    """Refuse, 403, a request from anywhere but this PC."""
    if request.remote_addr not in LOOPBACK:
        abort(403, description="The Activity page answers this PC only")


# ---- The page ------------------------------------------------------------------------------

def _folder():
    return os.path.join(os.path.dirname(current_app.config["PAGES"]), PAGE)


def _send(folder, name, content_type):
    path = os.path.join(folder, name)
    if not os.path.isfile(path):
        abort(404, description="File %s not found" % name)
    with open(path, "rb") as handle:
        content = handle.read()
    return Response(content, content_type=content_type, headers=NO_CACHE)


def _module_name(name):
    from tagpup.web.libraries import MODULE_NAME
    if not MODULE_NAME.match(name):
        abort(404, description="File %s not found" % name)


@routes.get("/activity/")
def page():
    return _send(_folder(), "index.html", PAGE_FILES["index.html"])


@routes.get("/activity/style.css")
def page_style():
    return _send(_folder(), "style.css", PAGE_FILES["style.css"])


@routes.get("/activity/<name>.js")
def page_module(name):
    _module_name(name)
    return _send(_folder(), name + ".js", SCRIPT_TYPE)


@routes.get("/activity/common/<name>.js")
def common_module(name):
    _module_name(name)
    return _send(current_app.config["COMMON"], name + ".js", SCRIPT_TYPE)


@routes.get("/activity/common/<name>.css")
def common_style(name):
    _module_name(name)
    return _send(current_app.config["COMMON"], name + ".css", PAGE_FILES["style.css"])


# ---- What it reads from -------------------------------------------------------------------

def _libraries():
    """Every library in the home's data folder: the test libraries for a server started on
    one, as the picker offers them."""
    startup = current_app.config.get("STARTUP_LIBRARY")
    test_mode = startup is not None and libraries.is_test_library(os.path.basename(startup.path))
    return runtimes.home_libraries(test_mode)


def _library_named(name):
    for library in _libraries():
        if library.name.lower() == str(name or "").lower():
            return library
    return None


def _lifecycle():
    return current_app.config["LIFECYCLE"]


def _task(name):
    background = _lifecycle().background
    return background.task(name) if background is not None else None


def _stamp(seconds):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(seconds)) if seconds else None


def _supervisor_state():
    """What the always-on process says in data/supervisor.json, less its server's token;
    `alive` whether it runs now. None when none has run in this home."""
    try:
        state = supervisor.last_state()
    except Exception:
        state = None
    if not state:
        return None
    shown = {key: value for key, value in state.items() if key not in ("server_token", "started")}
    try:
        shown["alive"] = supervisor.running() is not None
    except Exception:
        shown["alive"] = False
    return shown


def _int_arg(name, default, most):
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        abort(400, description="%s is a number" % name)
    return max(1, min(value, most))


def _app_url(kind, library_name, query=""):
    """The page of app `kind` for a library on this machine, or None for an app this process
    does not serve (the ports it was told: create_app's `ports`)."""
    port = current_app.config["PORTS"].get(kind)
    if not port:
        return None
    host = urllib.parse.urlsplit(request.host_url).hostname or "localhost"
    if ":" in host:
        host = "[%s]" % host
    return "%s://%s:%d/%s/%s" % (request.scheme, host, port, urllib.parse.quote(library_name), query)


def _index_runs_of():
    """{a run's tag: [the runs of the indexer it queued]}, from this process's index queues:
    a sync's new folders, indexed as part of it (their `parents`)."""
    found = {}
    for _name, queue in indexing_jobs.every_queue():
        running = queue.now()["running"]
        for run in queue.history() + ([running] if running else []):
            for parent in run.get("parents") or ():
                found.setdefault(parent, []).append(run["run"])
    return found


def _logs_for(run, children=None):
    """The logs a run's lines are in, as the page is to read them, the first its own: a run
    of the indexer's own file, then this server's; any other run, this server's, then the
    own files of the runs of the indexer it queued (`children`, _index_runs_of). [] for
    none. Named by tagpup.logs alone: the page never spells a log's name."""
    if not run:
        return []
    if run.startswith("index:"):
        return [logs.run_log(indexing.INDEXER_LOG, run), logs.SERVER_LOG]
    return [logs.SERVER_LOG] + [logs.run_log(indexing.INDEXER_LOG, child) for child in (children or {}).get(run, [])]


# ---- Now -----------------------------------------------------------------------------------

@routes.get("/api/activity/now")
def now():
    """What runs now, in this process and -- for the recurring jobs -- in any."""
    every = _libraries()
    queues = {name.lower(): queue for name, queue in indexing_jobs.every_queue() if name}
    suggesting = suggestion_jobs.under_way()
    children = _index_runs_of()
    found = []
    for library in every:
        queue = queues.get(library.name.lower())
        indexed = queue.now() if queue is not None else {"running": None, "queued": []}
        if indexed["running"]:
            indexed["running"]["logs"] = _logs_for(indexed["running"]["run"])
        running = []
        try:
            for (job, _for), (last, _ended) in job_runs.latest(library).items():
                if last is not None and last.outcome == job_runs.RUNNING:
                    tag = runs.job_tag(last.library, last.id)
                    running.append({"job": job, "run_id": last.id, "started": last.started, "run": tag,
                                    "logs": _logs_for(tag, children)})
        except Exception as e:
            logger.warning("Could not read the running jobs of %s: %s", library.name, e)
        found.append({"name": library.name, "indexing": indexed,
                      "suggesting": [run for run in suggesting if run["library"].lower() == library.name.lower()],
                      "jobs": running})
    watcher = _task(WATCHER)
    watching = watcher.status() if watcher is not None and hasattr(watcher, "status") else None
    status = _lifecycle().status()
    return jsonify({"at": _stamp(time.time()), "libraries": found,
                    "syncing": watching["syncing"] if watching else None,
                    "watching": bool(watching and watching["running"]),
                    "server": {"version": status["version"], "taking_work": status["taking_work"],
                               "requests": status["requests"], "busy": status["busy"]},
                    "supervisor": _supervisor_state()})


# ---- The recurring jobs ------------------------------------------------------------------

@routes.get("/api/activity/jobs")
def jobs():
    limit = _int_arg("runs", RUNS, MOST_RUNS)
    runner = _task(JOBS)
    children = _index_runs_of()
    listed = []
    for library in _libraries():
        try:
            listed.append({"name": library.name, "jobs": recurring.overview(library, limit=limit)})
            for job in listed[-1]["jobs"]:
                for run in job["runs"]:
                    run["logs"] = _logs_for(run["run"], children)
        except Exception as e:
            logger.warning("Could not read the jobs of %s: %s", library.name, e)
            listed.append({"name": library.name, "jobs": [], "error": str(e)})
    return jsonify({"runs_jobs": runner is not None, "check_every": recurring.CHECK_EVERY,
                    "running_here": runner.status() if runner is not None else [], "libraries": listed})


@routes.post("/api/activity/jobs/run")
def run_job():
    """Run a recurring job now for a library, on the runner of this process's recurring
    jobs: answered once the run has started or been refused (a run holds it already)."""
    body = request.get_json(silent=True) or {}
    runner = _task(JOBS)
    if runner is None:
        return responses.error(409, "This server runs no recurring jobs (one a test started, or TAGPUP_NO_JOBS "
                                    "is set); run it with the CLI: tagpup_cli.py jobs run NAME --db LIBRARY")
    job = runner.registry.get(body.get("job") or "")
    if job is None:
        return responses.error(404, "There is no recurring job called %s" % (body.get("job"),))
    library = _library_named(body.get("library"))
    if job.per_library and library is None:
        return responses.error(404, "There is no library called %s" % (body.get("library"),))
    outcome = runner.start_job(job.name, library)
    if outcome is None:
        return jsonify({"success": True, "started": None, "run_id": None,
                        "why": "it has not started yet; its run shows here once it does"})
    if outcome.ran:
        return jsonify({"success": True, "started": True, "run_id": outcome.run_id,
                        "run": runs.job_tag(outcome.library, outcome.run_id)})
    why = {"running": "a run of it is under way already", "behind": "the library has not had this version's "
           "migrations; open it in an app first", "no library": "there is no library"}.get(outcome.why, outcome.why)
    return jsonify({"success": False, "started": False, "run_id": None, "why": why,
                    "error": "Not started: %s." % why})


# ---- Sync and the watcher -----------------------------------------------------------------

@routes.get("/api/activity/sync")
def sync_state():
    watcher = _task(WATCHER)
    watching = watcher.status() if watcher is not None and hasattr(watcher, "status") else None
    children = _index_runs_of()
    listed = []
    for library in _libraries():
        entry = {"name": library.name, "review_url": _app_url("tuner", library.name, "?review=1")}
        try:
            entry.update(activity.sync_state(library))
            for which in ("last_whole", "last_folder"):
                if entry.get(which):
                    entry[which]["logs"] = _logs_for(entry[which]["run"], children)
        except Exception as e:
            logger.warning("Could not read the syncs of %s: %s", library.name, e)
            entry["error"] = str(e)
        try:
            roots = runtimes.peek_settings(library).roots
        except Exception:
            roots = []
        watched = {}
        for root in (watching or {}).get("roots", []):
            if library.name in root["libraries"]:
                watched[paths.key(root["path"])] = root
        entry["roots"] = [{"path": root, "there": os.path.isdir(root),
                           "watched": bool(watched.get(paths.key(root), {}).get("watched"))} for root in roots]
        entry["watches"] = sorted({root["path"] for root in watched.values() if root["watched"]}, key=paths.key)
        entry["watcher"] = (watching or {}).get("libraries", {}).get(library.name)
        listed.append(entry)
    return jsonify({"watching": bool(watching and watching["running"]), "libraries": listed})


# ---- Needs attention ---------------------------------------------------------------------

@routes.get("/api/activity/attention")
def attention():
    """Every library's photos found damaged and not replaced since, with their paths: the
    files the owner is to restore, and TagPup's page on the folder of each."""
    listed, totals = [], {"unreadable": 0, "incomplete": 0}
    for library in _libraries():
        try:
            photos = damaged_photos.listed(library)
        except Exception as e:
            logger.warning("Could not read the damaged photos of %s: %s", library.name, e)
            listed.append({"name": library.name, "photos": [], "error": str(e)})
            continue
        for photo in photos:
            photo["folder_url"] = _app_url("tagpup", library.name, "?path=" + urllib.parse.quote(photo["folder"]))
            totals["incomplete" if photo["indexed"] else "unreadable"] += 1
        try:
            to_detect = damaged_photos.faces_to_detect_count(library)
        except Exception as e:
            logger.warning("Could not count the photos of %s whose faces are to be detected: %s", library.name, e)
            to_detect = 0
        listed.append({"name": library.name, "photos": photos, "faces_to_detect": to_detect})
    return jsonify({"libraries": listed, **totals})


@routes.post("/api/activity/attention/check")
def attention_check():
    """Check again: read the photos recorded damaged again now, whatever their stamp -- of
    `library`, or of every library; only `path`, when given (tagpup.runtime.check_damaged).
    One that reads whole is forgotten and indexed again for real."""
    body = request.get_json(silent=True) or {}
    name, path = body.get("library"), body.get("path")
    if path is not None and not isinstance(path, str):
        return responses.error(400, "path is a photo's path")
    if name is not None:
        library = _library_named(name)
        if library is None:
            return responses.error(404, "There is no library called %s" % (name,))
        every = [library]
    else:
        every = _libraries()
    listed = []
    for library in every:
        try:
            done = runtimes.check_damaged(library, [path] if path else None)
            listed.append({"name": library.name, **damaged_photos.checked_counts(done)})
        except Exception as e:
            logger.warning("Could not check the damaged photos of %s again: %s", library.name, e)
            listed.append({"name": library.name, "error": str(e)})
    return jsonify({"success": True, "libraries": listed})


# ---- Snapshots ----------------------------------------------------------------------------

@routes.get("/api/activity/snapshots")
def snapshot_list():
    listed = []
    for library in _libraries():
        try:
            listed.append(dict(activity.snapshot_list(library), name=library.name))
        except Exception as e:
            listed.append({"name": library.name, "snapshots": [], "bytes": 0, "error": str(e)})
    return jsonify({"libraries": listed, "bytes": sum(each["bytes"] for each in listed)})


# ---- The always-on process ------------------------------------------------------------------

@routes.get("/api/activity/server")
def server():
    lifecycle = _lifecycle()
    status = lifecycle.status()
    background = lifecycle.background
    return jsonify({"version": status["version"], "supervised": status["supervised"],
                    "taking_work": status["taking_work"], "busy": status["busy"],
                    "running_since": _stamp(lifecycle.started), "pid": os.getpid(),
                    "background": background.names() if background is not None else [],
                    "supervisor": _supervisor_state()})


# ---- What was done ---------------------------------------------------------------------------

def _updates(limit):
    """The always-on process's moves onto a new version, from the end of its log."""
    found = logs.read("supervisor.log", limit=limit, text="moving it from")
    if not found:
        return []
    entries = []
    for record in found["records"]:
        entries.append({"kind": "update", "library": None, "time": record["time"], "finished": None,
                        "seconds": None, "what": record["message"].split("\n")[0], "outcome": "moved",
                        "counts": {}, "id": None, "run": None, "error": None})
    return entries


@routes.get("/api/activity/timeline")
def timeline():
    limit = _int_arg("limit", TIMELINE, MOST_TIMELINE)
    entries = []
    for library in _libraries():
        try:
            entries += activity.timeline(library, limit)
        except Exception as e:
            logger.warning("Could not read what was done in %s: %s", library.name, e)
    for name, queue in indexing_jobs.every_queue():
        for run in queue.history()[:limit]:
            entries.append({"kind": "index", "library": name, "time": run["started"], "finished": run["finished"],
                            "seconds": activity.seconds_between(run["started"], run["finished"]),
                            "what": "index of %s%s" % (run["name"], " and %d more folder(s)" % (run["folders"] - 1)
                                                       if run["folders"] > 1 else ""),
                            "outcome": run["outcome"], "counts": {"folders": run["folders"]}, "id": None,
                            "run": run["run"], "error": run["message"] if run["outcome"] == "failed" else None})
    try:
        entries += _updates(limit)
    except Exception as e:
        logger.warning("Could not read the supervisor's log: %s", e)
    entries.sort(key=lambda entry: entry["time"] or "", reverse=True)
    children = _index_runs_of()
    for entry in entries[:limit]:
        entry["logs"] = _logs_for(entry["run"], children)
    return jsonify({"limit": limit, "entries": entries[:limit], "more": len(entries) > limit})


# ---- The logs --------------------------------------------------------------------------------

@routes.get("/api/activity/logs")
def log_list():
    return jsonify({"folder": logs.log_dir(), "server": logs.SERVER_LOG, "logs": logs.log_files(),
                    "levels": list(logs.LEVELS)})


def _offset(name, value):
    if value in (None, ""):
        return None
    try:
        return max(0, int(value))
    except ValueError:
        abort(400, description="%s is an offset in the file, a number" % name)


@routes.get("/api/activity/logs/<name>")
def log_lines(name):
    level = request.args.get("level") or None
    if level is not None and level.upper() not in logs.LEVELS:
        return responses.error(400, "level is one of %s" % ", ".join(logs.LEVELS))
    run = request.args.get("run") or None
    if run is not None and not runs.is_tag(run):
        return responses.error(400, "run is a run's tag, as job:<library>:<id>")
    found = logs.read(name, limit=_int_arg("limit", LINES, MOST_LINES), level=level,
                      text=request.args.get("text") or None, run=run,
                      before=_offset("before", request.args.get("before")),
                      after=_offset("after", request.args.get("after")))
    if found is None:
        return responses.error(404, "There is no log called %s in data/logs" % name)
    for record in found["records"]:
        record.pop("text", None)
    return jsonify(found)


@routes.get("/api/activity/logs/<name>/raw")
def log_raw(name):
    data = logs.tail(name)
    if data is None:
        abort(404, description="There is no log called %s in data/logs" % name)
    return Response(data, content_type="text/plain; charset=utf-8", headers=NO_CACHE)


@routes.get("/api/activity/logs/<name>/download")
def log_download(name):
    path = logs.log_path(name)
    if path is None:
        abort(404, description="There is no log called %s in data/logs" % name)
    return send_file(path, mimetype="text/plain", as_attachment=True, download_name=name, max_age=0)
