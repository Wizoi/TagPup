"""The one-off writing each clip's Date Taken into its QuickTime headers (scripts/date_clip_headers.py; DECISIONS
2026-11-13, A CLIP'S DATE TAKEN IS WRITTEN INTO ITS QUICKTIME HEADERS; docs/plans/clip-date-in-quicktime-headers.md):
a module of its own, named by that script alone, deleted with it once its dry run says 0 left. A clip dated afterwards
gets its headers from the save that dates it (tagging.save_photo, Convert's carry)."""
import os

from tagpup.core import dates, fields, paths
from tagpup.core.result import Result
from tagpup.files import field_values
from tagpup.services import file_changes
from tagpup.store import db, photos
from tagpup.store import roots as store_roots

#: The one-off's change, as History names it (scripts/date_clip_headers.py).
DATE_HEADERS = "date clip headers"
#: Why a clip listed is left alone when its file is read: its XMP holds another Date Taken now (a save since).
REDATED_SINCE = "its Date Taken changed since it was listed"
#: What the one-off reads of each clip: the fields a clip's Date Taken save writes (fields.date_taken_fields, clip=True).
HEADER_READ = dates.CLIP_XMP_DATE_FIELDS + fields.CLIP_HEADER_DATES


def _xmp_date(meta):
    """The Date Taken a clip's XMP holds (`meta`, a row's raw_metadata or what its file holds), as the library reads it
    first (dates.date_taken), or None."""
    return dates.date_taken({field: meta.get(field) for field in dates.CLIP_XMP_DATE_FIELDS}, None, dates.VIDEO)


def clips_to_date_headers(library, named=()):
    """([(group, id, path, folder, date, problem)] by id, [the (folder, name) of `named` no clip is at]): the clips whose
    movie header does not hold the digits of the Date Taken their XMP holds (fields.date_taken_fields), read from the
    index alone. Group "A": the header zero or absent; "B": another time, and the clip named in `named`, the owner's
    reviewed list (DECISIONS 2026-11-13): (folder relative to the library's root, file name), each the path under its
    root's place on this machine, compared by paths.key. A header no list names is never listed: a camera's holds true
    UTC. `folder` as the row spells it ("@pictures/Trips/2019"); `problem` the save's sentence for a date no header holds
    (field_values.clip_date_refused), else None. A clip written is not listed again: its row follows its file."""
    listed, seen = [], set()
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        places = [found[0] for found in store_roots.roots_for(conn).locations.values()]
        wanted = {paths.key(os.path.join(place, folder, name)): (folder, name) for folder, name in named for place in places}
        for photo_id, row, path, raw in photos.clip_raw_rows(conn):
            one = wanted.get(paths.key(path))
            seen.add(one)
            date = _xmp_date(raw)
            if not date:
                continue
            digits = fields.date_taken_fields(date, clip=True)[fields.CLIP_HEADER_DATES[0]]
            header = fields.field_values(raw.get(fields.CLIP_HEADER_DATES[0]))
            zero = dates.date_taken({fields.CLIP_HEADER_DATES[0]: header}, None, dates.VIDEO) is None
            if set(header) != {digits} and (zero or one is not None):
                listed.append(("A" if zero else "B", photo_id, path, paths.row_parent(row), date,
                               field_values.clip_date_refused(digits)))
    finally:
        conn.close()
    return listed, [one for one in named if one not in seen]


def date_clip_headers(library, exiftool_path, named=(), apply=False, limit=None, on_planned=None, on_chunk=None):
    """The one-off (scripts/date_clip_headers.py): save each clip clips_to_date_headers lists with its own Date Taken
    through the clip save's write (file_changes.write_fields, fields.date_taken_fields: its XMP given the same value,
    every mvhd, tkhd and mdhd its digits), so a reader of the headers (SmugMug) shows the date the library shows
    (DECISIONS 2026-11-13; docs/plans/clip-date-in-quicktime-headers.md). A dry run (the default) reads the index alone.
    With `apply`, the first `limit` (None: all) listed and not refused, as ONE change undoable from History (its undo
    reads every clip once before it writes), uncapped and patient as the caption sweep, stopping at the first error (a
    clip its writer refuses is its own). A clip whose XMP holds another date when it is read is left alone
    (REDATED_SINCE), one changed between its read and its write is a conflict: never written over.

    details: `listed`, `unmatched` (clips_to_date_headers), `change`, `failures` [(id, sentence)], `counts`: `wrote` the
    files the journal holds done (file_changes.files_done), `held` those found holding it already, REDATED_SINCE,
    `unreadable` (a file gone too), `away`, `conflicts`, `failed`, `left` (listed again after the write)."""
    listed, unmatched = clips_to_date_headers(library, named)
    to_write = [one for one in listed if not one[5]][:limit]
    result = Result(attempted=len(to_write), details={"change": None, "conflicts": [], "written": {}})
    if apply and to_write:
        dated = {paths.key(one[2]): one[4] for one in to_write}

        def plan_one(path, held):
            date = dated[paths.key(path)]
            if _xmp_date(held) != date:
                return file_changes.skip(REDATED_SINCE)
            return file_changes.Plan(after=fields.held_dates(fields.date_taken_fields(date, clip=True), held), detail=date)

        result = file_changes.write_fields(library, DATE_HEADERS, exiftool_path, [one[2] for one in to_write],
                                           list(HEADER_READ), plan_one, summary={"photos": len(to_write)},
                                           unreadable="skip", uncapped=True, patient=True, stop_at_first_error=True,
                                           on_planned=on_planned, on_chunk=on_chunk)
    change = result.details.get("change")
    wrote = file_changes.files_done(library, change) if change is not None else 0
    away = {paths.key(path) for path in result.details.get("not_answered", [])}
    counts = {"wrote": wrote, "held": len(result.details.get("written", {})) - wrote, REDATED_SINCE: 0,
              "unreadable": 0, "away": 0, "conflicts": len(result.details.get("conflicts", []))}
    for path, why in result.skipped:
        counts["away" if paths.key(path) in away else REDATED_SINCE if why == REDATED_SINCE else "unreadable"] += 1
    id_of = {paths.key(one[2]): one[1] for one in to_write}
    result.details["failures"] = [(id_of.get(paths.key(path)), why) for path, why in result.errors]
    counts.update(failed=len(result.errors) - counts["conflicts"],
                  left=len(clips_to_date_headers(library, named)[0]) if apply else len(listed))
    result.details.update(listed=listed, unmatched=unmatched, counts=counts)
    return result
