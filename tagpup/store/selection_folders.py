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


#: A clip over the limit given (tagpup.files.recycle_bin.too_large_to_copy's rule, in SQL): deleted for good once confirmed,
#: never copied nor sent to a Bin.
_CLIP_OVER = "(p.kind = ? AND p.size > ?)"


def larger_than(conn, folder_ids, size, most_clip_bytes, most_named, ids=None, source=None, excluded=()):
    """([the ids of the photos and clips of the selection in `folder_ids` larger than `size` bytes -- photos.size, as the
    index last read them --], the stored paths of the first `most_named` of them by path): the files a Bin that keeps files
    of up to `size` would not keep (tagpup.services.selection.deleted_for_good; a `size` of 0, a Bin set to delete at once,
    keeps none, an empty file not either: recycle_bin.would_keep's rule). A clip over `most_clip_bytes` is clips_over's,
    not among them. Two reads over the folders given, which are the few whose largest file is over `size`."""
    return _files(conn, folder_ids, "(? <= 0 OR p.size > ?) AND NOT " + _CLIP_OVER,
                  (size, size, library_view.VIDEO, most_clip_bytes), most_named, ids, source, excluded)


def clips_over(conn, folder_ids, most_clip_bytes, most_named, ids=None, source=None, excluded=()):
    """([the ids of the clips of the selection in `folder_ids` over `most_clip_bytes` -- photos.size, as the index last read
    them --], the stored paths of the first `most_named` of them by path): the clips a Delete deletes for good once confirmed,
    on any drive (tagpup.files.recycle_bin.too_large_to_copy). Two reads over the folders given, which are the few holding one
    (bytes_by_folder)."""
    return _files(conn, folder_ids, _CLIP_OVER, (library_view.VIDEO, most_clip_bytes), most_named, ids, source, excluded)


def sizes_in(conn, folder_ids, most_clip_bytes, ids):
    """{photo id: (photos.size, as the index last read it, its stored path, its folder id)} of the photos and clips of the
    selection `ids` in `folder_ids` -- a clip over `most_clip_bytes` left out, clips_over's --: the files of one Recycle Bin
    that a Delete's question weighs one after another when the Bin would not take them all at once
    (tagpup.services.selection._not_binned). One read over the folders given; ids, sizes and paths."""
    selected, params = library_view.selected_sql(conn, ids, None, ())
    if selected is None or not folder_ids:
        return {}
    marks = ",".join("?" * len(folder_ids))
    return {photo_id: (size, path, folder_id) for photo_id, size, path, folder_id in conn.execute(
        "SELECT p.id, p.size, p.path, pf.folder_id FROM photo_folder pf JOIN photos p ON p.id = pf.photo_id"
        " WHERE pf.folder_id IN (%s) AND pf.photo_id IN (%s) AND NOT %s" % (marks, selected, _CLIP_OVER),
        tuple(folder_ids) + tuple(params) + (library_view.VIDEO, most_clip_bytes))}


def _files(conn, folder_ids, condition, condition_args, most_named, ids, source, excluded):
    selected, params = library_view.selected_sql(conn, ids, source, excluded)
    if selected is None or not folder_ids:
        return [], []
    marks = ",".join("?" * len(folder_ids))
    where = (" FROM photo_folder pf JOIN photos p ON p.id = pf.photo_id WHERE pf.folder_id IN (%s) AND pf.photo_id IN (%s)"
             " AND %s" % (marks, selected, condition))
    args = tuple(folder_ids) + tuple(params) + tuple(condition_args)
    found = [row[0] for row in conn.execute("SELECT DISTINCT p.id" + where, args)]
    named = [row[0] for row in conn.execute("SELECT DISTINCT p.path" + where + " ORDER BY p.path LIMIT ?", args + (most_named,))]
    return found, named


def bytes_by_folder(conn, most_clip_bytes, ids=None, source=None, excluded=()):
    """{folder id: (the bytes -- photos.size, as the index last read them -- of the photos and clips of the selection it holds,
    the largest of them, how many clips over `most_clip_bytes` it holds)}: what copies of them would take on this PC (a delete
    from a place with no Recycle Bin goes through this PC's, #694, #703). One grouped read. A clip over `most_clip_bytes`
    (tagpup.files.recycle_bin.too_large_to_copy) is deleted for good, not copied: counted apart, its size in neither of the
    first two."""
    selected, params = library_view.selected_sql(conn, ids, source, excluded)
    if selected is None:
        return {}
    over = (library_view.VIDEO, most_clip_bytes)
    return {folder_id: (total, largest, clips) for folder_id, total, largest, clips in conn.execute(
        "SELECT pf.folder_id, COALESCE(SUM(CASE WHEN {0} THEN 0 ELSE p.size END), 0),"
        " COALESCE(MAX(CASE WHEN {0} THEN 0 ELSE p.size END), 0), COALESCE(SUM({0}), 0) FROM photo_folder pf JOIN photos p"
        " ON p.id = pf.photo_id WHERE pf.photo_id IN ({1}) GROUP BY pf.folder_id".format(_CLIP_OVER, selected),
        over * 3 + tuple(params))}
