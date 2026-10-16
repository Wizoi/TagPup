"""Synthetic migrations for the tests that need a library BEHIND the code, or the runner doing a step (docs/DATABASE.md,
"The baseline").

The real migrations 1 to 31 are gone (the baseline, schema 32, makes a new library whole, and 33 follows it), so a library is
made at the latest real schema. A test that needs one behind the code makes it: the library is made the real way
(`schema.ensure`) and the code is given a step it has not yet run, for as long as the test says:

    with migration_names.ahead():                        # one additive step, the latest real + 1
        schema.ensure(path)                              # -> ["a step"]
    self.enterContext(migration_names.ahead(step(...)))  # for the whole test

`step` is the migration a test declares (version = the latest + 1 unless given). The steps are the test's; nothing here is a
real schema change, so a test that holds a library at a version holds it with these. `up_to(32)` is the app before the real
migration 33: a library it makes is at 32, as the owner's were, and it refuses one at 33.
"""
import contextlib
from unittest import mock

from tagpup.store import schema

#: The migrations the code really has: `ahead` adds to these, however many times it is nested.
REAL = schema.MIGRATIONS

#: The latest real schema: what `schema.LATEST` is outside `ahead`, which changes it for as long as it holds.
BASE = REAL[-1].version

#: The table the default step makes (and touches): one of its own, no table the journal keys.
STEP_TABLE = "tagpup_test_step"


def _add_the_step_table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS %s (id INTEGER PRIMARY KEY)" % STEP_TABLE)


def _drop_tag_embeddings(conn):
    conn.execute("DROP TABLE tag_embeddings")


def step(kind=schema.ADDITIVE, apply=None, touches=None, checks=None, version=None, name="a step"):
    """A migration after the last, for a test: by default an additive one that adds a table of its own."""
    if apply is None:
        apply = _add_the_step_table
        touches = (STEP_TABLE,) if touches is None else touches
    touches = tuple(touches or ())
    return schema.Migration(version or schema.LATEST + 1, name, apply, kind, "a test's", touches,
                            (schema.RowsKept(but=touches),) + schema.STANDARD if checks is None else checks)


def two_steps():
    """The two steps after the latest real one, as a test declares them: an additive one that adds a table, then a destructive one that drops
    `tag_embeddings` (a table the journal does not key, empty or not)."""
    first = step()
    return first, step(schema.DESTRUCTIVE, _drop_tag_embeddings, (), version=first.version + 1, name="a step that destroys")


@contextlib.contextmanager
def ahead(*steps):
    """Within it the code knows `steps` (one default additive step when none is given) after the latest real migration, so a
    library at the baseline is behind them. The libraries this process found current are forgotten on the way in and out: the
    next open reads the file."""
    steps = steps or (step(),)
    with mock.patch.object(schema, "MIGRATIONS", REAL + tuple(steps)), \
            mock.patch.object(schema, "LATEST", steps[-1].version):
        schema.forget()
        try:
            yield steps
        finally:
            schema.forget()
    schema.forget()


@contextlib.contextmanager
def up_to(version):
    """Within it the code knows the real migrations up to `version` and none after: the app as it was before the next one was
    written. The libraries this process found current are forgotten on the way in and out."""
    kept = tuple(m for m in REAL if m.version <= version)
    with mock.patch.object(schema, "MIGRATIONS", kept), mock.patch.object(schema, "LATEST", kept[-1].version):
        schema.forget()
        try:
            yield kept
        finally:
            schema.forget()


def operations(*steps):
    """What the journal calls those migrations (`changes.operation`): ["migration 33: a step", ...]."""
    return ["migration %d: %s" % (m.version, m.name) for m in steps]
