"""When each recurring job last ran for the library the URL names, and when it is due
next, which both apps serve alike (tagpup.jobs.recurring; docs/ARCHITECTURE.md, phase 8).

Counts only: a run's outcome and what it changed as numbers, never the note a failure
left, which can name a path.
"""
from flask import Blueprint, jsonify

from tagpup.jobs import recurring
from tagpup.web import responses, state

routes = Blueprint("jobs", __name__)


@routes.get("/api/jobs")
def recurring_jobs():
    library = state.require()
    try:
        listed = recurring.status(library)
    except Exception as e:
        return responses.error(500, str(e))
    return jsonify({"library": library.name, "jobs": listed})
