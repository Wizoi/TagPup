"""The indexer's progress as typed facts, one line each on its stdout, which the server reads into its live line and the
run's row (tagpup.services.indexing): what a phase has done, and what the run did, as values, never scraped from what a
person reads.

The child is started from the running server's own code (tagpup.runtime), so the writer and the reader of one pipe are
always the same version: a line that is not a fact is a person's (log text, a traceback), shown as it is. The facts are
said only when the parent asks for them (ASKED, in the child's environment): the owner running the CLI by hand reads
the person's words, never JSON.

A fact is a dict with `kind` and the fields KINDS names for it, `phase` and `sentence` text, every other field a whole
number (a count, a percent; never a bool). A line is PREFIX and the fact as JSON in ASCII (#2150: the pipe's code page
cannot be trusted with a path's letters).
"""
import json
import os

PREFIX = "@tagpup "

#: The environment variable whose "1" asks the child for facts rather than a person's words (ask, asked).
ASKED = "TAGPUP_PROGRESS_LINES"

#: Each kind of fact, and the fields it must have: a phase's progress (`phase`, one of job_runs.PHASES, and its counts);
#: the line of a wait for the graphics card; the bake's line of what it wrote (`baked`, with its counts when it ran);
#: what the index read from the photos' bakes (`bake_read`); what it says of the faces it made (`new_faces`, with how
#: many of them have a likely name, `likely`, or `matching_failed` 1 when the matching failed:
#: tagpup.services.indexing.new_faces_fact); how many photos failed (`failed`).
KINDS = {
    "phase": ("phase", "sentence"),
    "waiting": ("sentence",),
    "baked": ("sentence",),
    "bake_read": ("sentence",),
    "new_faces": ("sentence",),
    "failed": ("photos",),
}

#: The fields that are text; every other is a whole number.
_TEXT = ("phase", "sentence")


def ask(env):
    """Ask the child started with `env` for facts."""
    env[ASKED] = "1"


def asked(environ=None):
    """Was this process asked for facts?"""
    return (os.environ if environ is None else environ).get(ASKED) == "1"


def _problem(fact):
    """What is wrong with `fact`, or None."""
    if not isinstance(fact, dict):
        return "not a dict"
    kind = fact.get("kind")
    if kind not in KINDS:
        return "an unknown kind %r" % (kind,)
    missing = [name for name in KINDS[kind] if name not in fact]
    if missing:
        return "%s without %s" % (kind, ", ".join(missing))
    for name, value in fact.items():
        if name == "kind":
            continue
        if not isinstance(name, str):
            return "a field that is not named"
        if name in _TEXT:
            if not isinstance(value, str):
                return "%s is not text" % name
        elif not isinstance(value, int) or isinstance(value, bool):
            return "%s is not a whole number" % name
    return None


def fact(kind, **fields):
    """The fact of `kind` with `fields`, those given as None left out; a fact KINDS does not allow is a mistake in the
    code (ValueError)."""
    made = dict(kind=kind, **{name: value for name, value in fields.items() if value is not None})
    problem = _problem(made)
    if problem:
        raise ValueError("Not a progress fact: %s" % problem)
    return made


def say(made):
    """The line that carries `made` (fact's), with no newline."""
    return PREFIX + json.dumps(made, ensure_ascii=True, separators=(",", ":"))


def _frame(line):
    """The line as it stands last: a carriage return redraws it."""
    return (line or "").split("\r")[-1].strip()


def heard(line):
    """The fact a line say made carries, or None for any other line and for one that is not whole (cut by a child
    killed mid-line, garbled). Never raises."""
    try:
        whole = _frame(line)
        if not whole.startswith(PREFIX):
            return None
        found = json.loads(whole[len(PREFIX):])
        return found if _problem(found) is None else None
    except Exception:
        return None


def plain(line):
    """A person's line as it stands, stripped; None for an empty one and for any line carrying a fact, read or not (a
    fact cut short is not shown to anyone)."""
    whole = _frame(line)
    if not whole or PREFIX in whole:
        return None
    return whole


def counts(made):
    """The fact's whole numbers, by name."""
    return {name: value for name, value in made.items()
            if name not in _TEXT and name != "kind" and isinstance(value, int) and not isinstance(value, bool)}
