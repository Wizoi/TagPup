"""The one scaffold every maintenance operation runs on: plan, back up, apply, report.

The maintenance scripts in scripts/ each carried their own copy of the same steps --
work out what would change, print it, and only with --apply back the library up, write
and say what was written -- and they got them wrong one at a time: one printed what it
had planned as what it had removed, six wrote without a backup. The MCP server's write
tools make the same operations (docs/ARCHITECTURE.md, phase 7), so the steps live here,
once, and a script and a tool call the same service.

An operation hands `run` two functions:

- `plan(library)` returns a Plan: how many writes it would make, the counts and ids a
  report gives, what only a person may be shown (paths, names), and whatever the write
  needs. It reads; it never writes.
- `write(library, plan, result)` writes under the library's write lock and counts into
  `result.changed` what the writes changed -- rowcounts, never the plan's sizes -- with
  anything it left alone in `result.skipped`.

and, optionally, `remaining(library)`: counts re-read once the write is done, where the
operation can re-read cheaply.

A dry run returns the plan as a Result and changes nothing: `attempted` is what it would
write, `changed` is 0, and no backup is taken. Applying backs the library up once
(db.backup; CLAUDE.md: one backup per apply), unless the plan holds nothing to write.

The Result's details, in every operation:

    dry_run    True unless applied
    backup     where the copy went, or None
    counts     {what: how many}: the plan, safe to show anyone
    ids        {what: [id]}: photo, face or tag-node ids, safe to show anyone
    reveal     paths, names, captions: the library is photographs of real people, many
               of them minors, so a tool shows these only when asked (reveal=True)
    remaining  {what: how many} re-read after the write, where the operation offers it
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from tagpup.core.result import Result
from tagpup.store import db


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
    #: Whatever `write` needs; never reported.
    work: Any = None


def run(library, reason, plan, write, apply=False, remaining=None):
    """Plan an operation on `library` and, with `apply`, back up and write it. Returns a
    Result (see the module's docstring). `reason` names the backup:
    <library>.before-<reason>-<time>.db."""
    planned = plan(library)
    result = Result(attempted=planned.size, details={
        "dry_run": not apply,
        "backup": None,
        "counts": dict(planned.counts),
        "ids": dict(planned.ids),
        "reveal": dict(planned.reveal),
    })
    if planned.refused:
        result.refuse(planned.refused)
        return result
    if not apply or not planned.size:
        return result
    result.details["backup"] = db.backup(library.path, reason)
    write(library, planned, result)
    if remaining is not None:
        result.details["remaining"] = remaining(library)
    return result
