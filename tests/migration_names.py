"""The names of the migrations, for the tests that say which ran (docs/findings.md, #689).

Every new migration used to be added by hand to a list of names in six test files, because each said "opening a library
at schema N runs these". The migrations are one list (tagpup.store.schema.MIGRATIONS); what a test says is *which of
them*, and these name them by version: `after(21)` is what a library at schema 21 runs on opening, in order.
tests/test_migrations.py (KINDS) is where a new migration is declared, once.
"""
from tagpup.store import schema


def after(version):
    """The names of the migrations after `version`, oldest first: what ensure() returns for a library at `version`."""
    return [m.name for m in schema.MIGRATIONS if m.version > version]


def named(*versions):
    """The names of those migrations, in the order given."""
    by_version = {m.version: m.name for m in schema.MIGRATIONS}
    return [by_version[version] for version in versions]


def operations_after(version):
    """What the journal calls those migrations (`changes.operation`): ["migration 22: photos by file name", ...]."""
    return ["migration %d: %s" % (m.version, m.name) for m in schema.MIGRATIONS if m.version > version]
