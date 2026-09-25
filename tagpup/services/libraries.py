"""Making a library, and bringing one up to date: what the picker's Create does, and
what opening a library by its URL does first.

The old server made a library by opening it through PhotoIndex and then seeding its
tag tree, two old modules the web layer may not import (tests/test_layers.py). A new
library's tables come from tagpup.store.schema and its first nodes -- one face root,
People -- from tagpup.store.taxonomy.seed.
"""
import os

from tagpup.core import library, validation
from tagpup.core.result import Result
from tagpup.store import schema, taxonomy


def bring_up_to_date(db_path):
    """The library's tables, made or migrated (tagpup.store.schema.ensure). Returns the
    names of the migrations applied: none, usually."""
    return schema.ensure(db_path)


def create(db_path):
    """Make the library at `db_path`: its folder, its tables, and its first nodes. A
    library already there is brought up to date and its tree left alone.

    Refused, and nothing made, when its name -- the file's, without ".db" or a test
    prefix -- may not name a library (tagpup.core.validation). changed: 1 when the
    library was made, 0 when it was there already."""
    result = Result(attempted=1)
    problem = validation.problem("library name", library.picker_name(os.path.basename(db_path)))
    if problem:
        result.refuse(problem)
        return result
    existed = os.path.exists(db_path)
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    bring_up_to_date(db_path)
    taxonomy.seed(db_path)
    result.changed = 0 if existed else 1
    return result
