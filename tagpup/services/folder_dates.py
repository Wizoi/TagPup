"""The one-off dating of the undated photos and clips in a full-date folder by the folder
(scripts/date_undated_by_folder.py; DECISIONS 2026-11-15; docs/plans/date-undated-by-folder.md): which rows the index
lists, one change writing a group of them through file_changes.write_fields, and what that change did, said as counts and
ids. Deleted with the script, dates.folder_date and photos.undated once its dry run says 0 left."""
from tagpup.core import dates, fields, paths
from tagpup.services import file_changes
from tagpup.store import db, photos

#: The change's kind, as History names it.
OPERATION = "date undated photos by their folders"
#: Why a file listed is left alone: it holds a date of its own when read (saved since the index read it).
DATED_SINCE = "it holds a date of its own when read"
#: What is read of each file (clip or not): the fields its Date Taken save writes, which the library dates it by.
READ = {False: dates.SHIFTED_FIELDS, True: dates.CLIP_XMP_DATE_FIELDS + fields.CLIP_HEADER_DATES}


def listed(library):
    """([(id, path, is a clip, date)] of the undated rows its folder dates, [id] of those whose file name holds another
    date, left for the owner (4 in photo_index, counted 2026-11-15)), by id, from the index alone."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        rows = photos.undated(conn)
    finally:
        conn.close()
    to_date, named_otherwise = [], []
    for photo_id, path, kind in rows:
        date = dates.folder_date(path)
        if date and dates.name_date(path) not in (None, date):
            named_otherwise.append(photo_id)
        elif date:
            to_date.append((photo_id, path, kind == dates.VIDEO, date))
    return to_date, named_otherwise


def write(library, exiftool, rows, clip, planned, out):
    """One change dating `rows` (all clips, or none) through the panel's save's write; its Result."""
    dated = {paths.key(path): date for _id, path, _clip, date in rows}

    def plan_one(path, held):
        if dates.date_taken(held, None, dates.VIDEO if clip else "photo"):
            return file_changes.skip(DATED_SINCE)
        return file_changes.Plan(after=fields.held_dates(fields.date_taken_fields(dated[paths.key(path)], clip=clip), held))

    return file_changes.write_fields(
        library, OPERATION, exiftool, [path for _id, path, _clip, _date in rows], list(READ[clip]), plan_one,
        summary={"photos": len(rows)}, unreadable="skip", uncapped=True, patient=True, stop_at_first_error=True,
        on_planned=planned.append, on_chunk=lambda done, of: out("%d of %d files carried out." % (done, of)))


def said(library, result, rows, noun, db_path, out):
    """What one change did, from the journal: `wrote N` its files done, never what it tried."""
    change = result.details.get("change")
    away = {paths.key(path) for path in result.details.get("not_answered", [])}
    left = {DATED_SINCE: 0, "unreadable": 0, "away": 0}
    for path, why in result.skipped:
        left["away" if paths.key(path) in away else DATED_SINCE if why == DATED_SINCE else "unreadable"] += 1
    refused = len(result.details.get(file_changes.WRITER_REFUSED, []))
    conflicts = len(result.details.get("conflicts", []))
    out("%ss: wrote %d. Left alone: %d holding a date of its own when read, %d unreadable, %d away (their network share "
        "did not answer). Refused by the writer: %d. Conflicts: %d. Failed: %d."
        % (noun, file_changes.files_done(library, change) if change is not None else 0, left[DATED_SINCE],
           left["unreadable"], left["away"], refused, conflicts, len(result.errors) - refused - conflicts))
    if result.refused:
        out("Refused: %s" % result.refused)
    id_of = {paths.key(path): photo_id for photo_id, path, _clip, _date in rows}
    for path, why in result.errors:
        out("%s %s: %s" % (noun, id_of.get(paths.key(path)), why))
    out("Recorded as change %d in History; to undo it: tagpup_cli.py --db \"%s\" undo %d --apply" % (change, db_path, change)
        if change is not None else "Nothing recorded for the %ss: no file was written." % noun)
