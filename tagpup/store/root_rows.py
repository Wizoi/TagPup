"""The photo rows as the library holds them -- their path in row form, and the stamp of the file
they were read from -- for Verify (tagpup.services.roots_verify; docs/ARCHITECTURE.md, "Roots and
machines").

Verify asks where a root WOULD live, a place the machine's map does not say yet, so it cannot
read the rows through the connection's Roots, which converts by the map as it is: it reads what
the library holds and converts by the hypothetical map it was given. Read-only; the path, mtime
and size columns only (never the BLOBs).
"""
from tagpup.store import db
from tagpup.store import roots as store_roots


def count(db_path, name):
    """How many photos the library holds under root `name`, by where this machine keeps it now
    (one range of the path index): None when this machine does not place the root. A map that
    cannot be read is the caller's (paths.RootsError)."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        places = store_roots.roots_for(conn).locations.get(name)
        if not places:
            return None
        where, params = store_roots.sql_under(conn, "path", places[0])
        return conn.execute("SELECT COUNT(*) FROM photos WHERE " + where, params).fetchone()[0]
    finally:
        conn.close()


def stamps(db_path):
    """[(path as the library holds it, mtime, size)] of every photo: `@name/under/it` for a
    photo under a root, the native path for one under none. mtime and size are None for a
    row the index never read."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return [tuple(row) for row in conn.execute("SELECT path, mtime, size FROM photos")]
    finally:
        conn.close()
