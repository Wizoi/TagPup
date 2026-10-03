"""Everything a library's database holds, for a test that says a write changed none of it.

`dump` reads every table the database has -- the photos, faces, journal, generations,
settings, job runs, added folders, damaged files, roots, whatever the schema holds now
or later -- row by row, BLOBs included, so that two dumps are equal only when the
database holds the same thing. Read-only.
"""
from tagpup.store import db


def dump(db_path):
    """{table: [rows]} of every table of the library at `db_path`, each in rowid order
    (a WITHOUT ROWID table in key order), and the schema itself."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        found = {"schema": conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()}
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall():
            quoted = '"%s"' % name.replace('"', '""')
            try:
                found[name] = conn.execute("SELECT * FROM %s ORDER BY rowid" % quoted).fetchall()
            except Exception:
                found[name] = conn.execute("SELECT * FROM %s" % quoted).fetchall()
        return found
    finally:
        conn.close()


def table_names(db_path):
    return sorted(name for name in dump(db_path) if name != "schema")
