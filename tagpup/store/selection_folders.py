"""The folders a selection of photos is in (docs/ARCHITECTURE.md, "The owner's first review of the library views"): what the
selection panel of a library view offers to open in Organize (#675), and where a Delete of the selection would send its files
(#674). A selection is the library views' own (tagpup.store.library_view: a list of ids, or a source less the excluded ids).

One grouped read of `photo_folder` over the selection -- each photo is in exactly one folder -- and the folders' rows by key
for the few that are wanted. Never a list of paths of the photos: a Select all of 68,000 photos is 2,672 folder ids on
photo_index (counted 2026-10-04), not 68,000 paths. Reads only.
"""
from tagpup.store import library_view
from tagpup.store import roots as store_roots

#: Folder ids read in one statement.
CHUNK = 500


def counts(conn, ids=None, source=None, excluded=()):
    """{folder id: how many photos of the selection it holds} -- `ids`, or every photo of `source` but `excluded`. The
    selection is made as the tally makes it (library_view's temporary `sel` table), so it may be called inside the tally's
    read transaction and counts the same photos."""
    # The tally's own selection (library_view.selected_sql), not a copy of it: the panel's counts and its folders are of one set
    # of photos. Private there; that module is another branch's this week (#671-#673), so it is used and not renamed here.
    selected, params = library_view.selected_sql(conn, ids, source, excluded)
    if selected is None:
        return {}
    return dict(conn.execute(
        "SELECT folder_id, COUNT(*) FROM photo_folder WHERE photo_id IN (%s) GROUP BY folder_id" % selected, params))


def described(conn, folder_ids):
    """{folder id: (path -- native --, name)} of the folders `folder_ids`: their rows by primary key, in batches. Raises
    paths.RootsError for a root this machine does not place."""
    found = {}
    wanted = list(folder_ids)
    for start in range(0, len(wanted), CHUNK):
        chunk = wanted[start:start + CHUNK]
        rows = conn.execute("SELECT id, path, name FROM folders WHERE id IN (%s)" % ",".join("?" * len(chunk)),
                            chunk).fetchall()
        for folder_id, path, name in store_roots.natives(conn, rows, 1):
            found[folder_id] = (path, name)
    return found


def bytes_by_folder(conn, ids=None, source=None, excluded=()):
    """{folder id: (the bytes -- photos.size, as the index last read them -- of the photos of the selection it holds, the largest
    of them)}: what copies of them would take on this PC (a delete from a place with no Recycle Bin goes through this PC's, #694,
    #703). One grouped read."""
    selected, params = library_view.selected_sql(conn, ids, source, excluded)
    if selected is None:
        return {}
    return {folder_id: (total, largest) for folder_id, total, largest in conn.execute(
        "SELECT pf.folder_id, COALESCE(SUM(p.size), 0), COALESCE(MAX(p.size), 0) FROM photo_folder pf JOIN photos p"
        " ON p.id = pf.photo_id WHERE pf.photo_id IN (%s) GROUP BY pf.folder_id" % selected, params)}
