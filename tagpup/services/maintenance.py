"""The one scaffold every maintenance operation runs on: plan, rehearse, apply, report.

The maintenance scripts in scripts/ each carried their own copy of the same steps --
work out what would change, print it, and only with --apply back the library up, write
and say what was written -- and they got them wrong one at a time: one printed what it
had planned as what it had removed, six wrote without a backup. The MCP server's write
tools make the same operations (docs/ARCHITECTURE.md, phase 7), so the steps live here,
once, and a script and a tool call the same service.

An operation hands `run` two functions:

- `plan(library)` returns a Plan: how many writes it would make, the counts and ids a
  report gives, what only a person may be shown (paths, names), and whatever the edits
  need. It reads; it never writes.
- `edits(planned)` turns the plan into the rows it writes (tagpup.store.journal.Edit),
  each carrying what the plan read of it.

and, optionally, `remaining(library)`: counts re-read once the write is done, where the
operation can re-read cheaply.

Applying records the edits as one change of the library's journal (phase 7.5) instead of
copying the library first: applied only where every row is still what the plan read --
otherwise the whole change is refused, naming the rows -- and undoable afterwards on the
same terms (tagpup.services.journal). A dry run is the rehearsal: the change applied and
undone inside a transaction that is rolled back, which says whether the undo restored
every row exactly. Nothing is written by it.

The Result's details, in every operation:

    dry_run    True unless applied
    change     the id of the change applied, or None
    rehearsal  the dry run's: rows, exact, differences, derived_exact, refused
    counts     {what: how many}: the plan, safe to show anyone
    ids        {what: [id]}: photo, face or tag-node ids, safe to show anyone
    reveal     paths, names, captions: the library is photographs of real people, many
               of them minors, so a tool shows these only when asked (reveal=True)
    remaining  {what: how many} re-read after the write, where the operation offers it
    pruned     changes whose values the journal's retention took away after the apply
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from tagpup.core.result import Result
from tagpup.store import journal


@dataclass
class Plan:
    #: How many writes applying would make: the Result's `attempted`.
    size: int = 0
    counts: Dict[str, Any] = field(default_factory=dict)
    ids: Dict[str, Any] = field(default_factory=dict)
    #: What only a person asking for it is shown: paths, names, captions.
    reveal: Dict[str, Any] = field(default_factory=dict)
    #: Why nothing can be planned at all -- a library with no tag tree, say.
    refused: Optional[str] = None
    #: Whatever `edits` needs; never reported.
    work: Any = None


def run(library, operation, plan, edits, apply=False, remaining=None, kinds=()):
    """Plan an operation on `library`, and rehearse it, or with `apply` record and write
    it as one change named `operation`. Returns a Result (see the module's docstring):
    `changed` is the rows the change changed, read from the writes, not the plan's size.
    With `kinds`, details["changed"] counts the edits of each kind (Edit.kind)."""
    planned = plan(library)
    result = Result(attempted=planned.size, details={
        "dry_run": not apply,
        "change": None,
        "counts": dict(planned.counts),
        "ids": dict(planned.ids),
        "reveal": dict(planned.reveal),
    })
    if planned.refused:
        result.refuse(planned.refused)
        return result
    if not planned.size:
        return result
    wanted = edits(planned)
    summary = {"counts": dict(planned.counts)}
    if not apply:
        try:
            rehearsal = journal.rehearse(library.path, operation, wanted, summary)
        except Exception as e:
            result.fail("the rehearsal", "%s: %s" % (type(e).__name__, e))
            return result
        result.details["rehearsal"] = rehearsal.as_dict()
        if rehearsal.refused:
            result.refuse(rehearsal.refused)
        return result
    try:
        applied = journal.apply(library.path, operation, wanted, summary)
    except journal.Refusal as e:
        result.refuse("Nothing was written: %s" % e)
        return result
    except Exception as e:
        result.fail("the write", "%s: %s" % (type(e).__name__, e))
        return result
    result.changed = applied.changed
    result.details["change"] = applied.change_id
    if kinds:
        result.details["changed"] = {kind: applied.by_kind.get(kind, 0) for kind in kinds}
    if not applied.settled:
        result.fail("the people and dates of the photos it touched",
                    "not rebuilt yet; they are, the next time the library is opened")
    result.details["pruned"] = journal.prune(library.path)[0]
    if remaining is not None:
        result.details["remaining"] = remaining(library)
    return result


# ---- What the scripts print ------------------------------------------------------------

def rehearsed(result):
    """What a dry run's rehearsal found, in a line for a person."""
    rehearsal = result.details.get("rehearsal")
    if not rehearsal:
        return "Nothing to rehearse."
    if rehearsal["refused"]:
        return "Applying now would be refused: %s" % rehearsal["refused"]
    if rehearsal["exact"] and rehearsal["derived_exact"]:
        return "Rehearsed: %d row(s) written and undone, and the undo restored every one exactly." % rehearsal["rows"]
    return "Rehearsed: the undo did NOT restore every row exactly: %s" % "; ".join(
        rehearsal["differences"] or ["the photos' people differ"])


def recorded(result, db_path):
    """Which change an apply was recorded as, and how to take it back, in a line."""
    return ('Recorded as change %d; to undo it: tagpup_cli.py --db "%s" undo %d --apply'
            % (result.details["change"], db_path, result.details["change"]))
