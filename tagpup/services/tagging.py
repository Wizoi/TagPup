"""Actions on photos' tags and titles."""
import logging
import os

from tagpup.core import fields, paths, suggesting, validation, vocabulary
from tagpup.core.result import CHANGED_ON_DISK, CHANGED_ON_DISK_SENTENCE, UNREADABLE_BASE_SENTENCE, Result
# Looked up at call time, as exiftool_session.ExifToolSession, so a test standing in for
# ExifTool there reaches this too.
from tagpup.files import exiftool_session, field_values, images, metadata, names
from tagpup.services import batches, file_changes, file_only, libraries
from tagpup.services import photos as photo_actions
from tagpup.services import roots as roots_service
from tagpup.store import db, journal, person_ids, photos, taxonomy

logger = logging.getLogger(__name__)


#: `base` not given: no check of the file's tags and title (the CLI, the MCP). None is a page's record of a photo
#: ExifTool could not read; a dict is what the page read.
NO_BASE = object()


def refuse_under_people(library, tags):
    """Rule (a), the owner's own writes *(owner, 2026-10-09)*: the sentence that refuses `tags` when one of them would be a new
    tag under a person that faces or photos carry ("Sam is a person (12 faces, 40 photos); a person cannot have tags under
    them. Choose another group."), else None. A tag the tree has already is not new, and a keyword read from a file is never
    refused (the indexer does not come here). The one keyword writer asks it for every write the owner makes: a photo's save,
    Add to all selected, Apply All, the bulk Tags edit."""
    tags = [vocabulary.normalize(tag) for tag in dict.fromkeys(tags) if vocabulary.SEPARATOR in vocabulary.normalize(tag)]
    if not tags or not os.path.exists(library.path):
        return None
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        for tag in tags:
            taxonomy.refuse_child_of_person(conn, tag)
    except person_ids.PersonHasNoChildren as why:
        return str(why)
    finally:
        conn.close()
    return None


def _held_title(now):
    """The title the file holds, as the page shows it (fields.title_of: its Title, else its Description)."""
    return fields.title_of({field: now.get(field) for field in fields.CAPTION_FIELDS})


#: Every field _differs_from_base reads of the file: the fields its tags come from and every field its title is
#: shown from -- XMP:Title, else a clip's QuickTime Title, else IPTC:ObjectName, else the Description a photo with no
#: title shows as its title (fields.CAPTION_FIELDS). The save once read the description fields alone, found no title on a photo titled only,
#: and refused every save from the page of it as changed on disk (6,123 photos of one library, 2026-11-06). The
#: prefixed names alone, as _caption_problem asks: a read answers a field under its group's name (field_values), so a
#: bare name holds nothing, and a bare key in what the file holds broke the recording of a file that held the save
#: already (store.photos.follow_fields reads each key as group:name).
BASE_READ = tuple(dict.fromkeys(fields.TAG_SOURCE_FIELDS + tuple(name for name in fields.CAPTION_FIELDS
                                                                  if ":" in name)))


def _differs_from_base(now, base):
    """Does the file hold other tags or another title than the page read (`base`, {"tags", "title"})? The tags
    as a SET of keyword paths, the title trimmed: the two things a save writes over. `now` holds BASE_READ."""
    return (set(_tags_held(now)) != set(base.get("tags") or [])
            or _held_title(now) != fields.trimmed(base.get("title") or ""))


def _title_written(path, now, wanted):
    """The title fields a save of `path` writes, of `wanted` (fields.title_fields of the title saved): none when the file
    holds its title only in a clip's QuickTime Title (fields.CLIP_TITLE; 2,017 of the 2,018 photo_index clips holding
    one, counted 2026-11-08) and the save keeps that title, so a tag save of one makes no XMP copy of it; a title changed is written to XMP:Title
    (the writer patches a clip's XMP alone), the QuickTime Title left as it was; a title cleared clears XMP:Title and a
    clip's QuickTime Title too, so no title comes back (DECISIONS 2026-11-08, #2652, #2657). `now` holds BASE_READ."""
    title = fields.trimmed(wanted.get("XMP:Title") or "")
    in_xmp = any(fields.trimmed(held) for held in fields.field_values(now.get("XMP:Title")))
    if not in_xmp and _holds_text(now, fields.CLIP_TITLE, title):
        return {name: value for name, value in wanted.items() if name not in fields.title_fields("")}
    if not title and images.is_clip(path) and fields.field_values(now.get(fields.CLIP_TITLE)):
        return {**wanted, fields.CLIP_TITLE: ""}
    return wanted


def _caption_moved(path, now):
    """The caption fields a save clears, {field: ""}: on a photo holding no title of its own, whose page shows its
    Description as the title, every caption field holding that very text, whatever title is saved -- the same text
    (the promotion), another, or none. The text shown as the title moves out of the caption in one rule: a save of the
    same moves it into the title and leaves no copy behind, a changed title replaces it, a cleared one clears it, and
    no save leaves it to come back as the title (DECISIONS 2026-11-06, THE TITLE AND THE CAPTION ARE TWO VALUES;
    #2583). A photo with a title of its own keeps its Description, and a caption field holding other text is never
    written. A clip's caption is not written (fields.CLIP_WRITES): it answers none, so a clip's save reads what it was
    admitted with (file_changes.leave_out_clips) and is never refused at its write for a field it did not ask (#2663).
    `now` holds BASE_READ and the caption fields."""
    shown = _held_title(now)
    if not shown or fields.holds_title(now) or images.is_clip(path):
        return {}
    return {field: "" for field in fields.caption_fields("") if _holds_text(now, field, shown)}


def _holds_text(now, field, text):
    """Does `field` of `now` (what a file holds, or a row's raw_metadata) hold `text`, trimmed as the reader trims it?
    The one test of a caption field holding the title: a save's (_caption_moved) and the sweep's (duplicate_captions)."""
    return text in (fields.trimmed(held) for held in fields.field_values(now.get(field)))


# ---- The one-off sweep of captions that repeat the title -------------------------------------------------------------

#: The caption fields the sweep clears where they hold the photo's own title (duplicate_captions): every field a caption
#: is held in but Windows' Comment (fields.SWEPT_CAPTION_FIELDS).
DUPLICATE_FIELDS = fields.SWEPT_CAPTION_FIELDS
#: What the sweep reads of each file: the title fields and DUPLICATE_FIELDS, by their prefixed names (BASE_READ's reason).
DUPLICATE_READ = tuple(dict.fromkeys(tuple(fields.title_fields("")) + DUPLICATE_FIELDS))
#: The sweep's change, as History names it.
CLEAR_DUPLICATES = "clear duplicate captions"
#: Why a photo the index named is left alone when its file is read: no caption field holds its title (any more).
NO_DUPLICATE = "no caption field holds its title"


def duplicate_captions(meta):
    """The DUPLICATE_FIELDS of `meta` (what a file holds, or a row's raw_metadata) holding the photo's own title exactly,
    trimmed (fields.own_title: XMP:Title, else a clip's QuickTime Title, else IPTC:ObjectName); [] for a photo with no title of its own. A caption
    holding the title and more ("Relay at the lake" beside the title "Relay") is not one."""
    title = fields.own_title(meta)
    return [field for field in DUPLICATE_FIELDS if _holds_text(meta, field, title)] if title else []


def duplicate_candidates(library):
    """([path] of the photos whose index row holds a caption repeating its title, {what: how many}), read from the
    index alone, no file: the photos the sweep reads. The row holds XMP's description and IPTC's caption, not EXIF's
    ImageDescription (fields.as_read), which the file's read finds. A clip is left out: the sweep writes photos only.
    Counts: `photos` read, `to read` (the paths), each of DUPLICATE_FIELDS the rows hold so, `captions kept` (a photo with
    a title of its own and a caption holding other text), `clips left alone` (a clip whose row holds one),
    `newly shown IPTC captions` (a photo whose XMP Description is cleared while a different IPTC caption remains)."""
    counts = {"photos": 0, "to read": 0}
    counts.update({field: 0 for field in DUPLICATE_FIELDS if fields.scan_reads(field)})
    counts.update({"captions kept": 0, "clips left alone": 0, "newly shown IPTC captions": 0})
    found = []
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        for path, raw in photos.raw_rows(conn):
            repeated = duplicate_captions(raw)
            if images.is_clip(path):
                counts["clips left alone"] += bool(repeated)
                continue
            counts["photos"] += 1
            title = fields.own_title(raw)
            if title and any(text != title for field in DUPLICATE_FIELDS
                             for text in (fields.trimmed(held) for held in fields.field_values(raw.get(field)))):
                counts["captions kept"] += 1
            if repeated:
                found.append(path)
                # XMP's Description (the caption shown first) cleared while a different IPTC caption stays: the photo
                # shows that one from now on (104 in photo_index, 2026-11-06).
                if "XMP:Description" in repeated and any(
                        text and text != title for text in (fields.trimmed(held) for held in
                                                            fields.field_values(raw.get("IPTC:Caption-Abstract")))):
                    counts["newly shown IPTC captions"] += 1
                for field in repeated:
                    if field in counts:
                        counts[field] += 1
    finally:
        conn.close()
    counts["to read"] = len(found)
    return found, counts


def clear_duplicate_captions(library, exiftool_path, apply=False, on_planned=None, on_chunk=None):
    """The one-off sweep (scripts/clear_duplicate_captions.py): clear each of DUPLICATE_FIELDS that holds the photo's
    own title exactly (duplicate_captions), in every photo the index names (duplicate_candidates). A caption that differs
    is kept, Windows' Comment (XPComment) is never written, a clip never read. The title and the caption are two values (DECISIONS
    2026-11-06), and a save stops making new copies; this clears the copies made before.

    A dry run (the default) reads the index alone and writes nothing: the Result's details["counts"]. With `apply`, one
    change of photo files (file_changes.write_fields, uncapped: a batch, its chunks of batches.CHUNK, the lock let go
    between them, Cancel or Ctrl+C honoured, each file read again before its write and a file changed since a conflict,
    each row following its file in the transaction that marks it done), undoable as one. A photo whose file no longer
    holds a repeated caption, or cannot be read, is skipped, counted by why in details["left alone"] ({"nothing to
    clear", "unreadable", "away": on a share that did not answer, given one more look by write_fields}); re-run, the
    photos done are no longer named by the index,
    so a run stopped part-way is continued by running it again. `on_planned(change_id)` and `on_chunk(done, of)` are
    file_changes.write_fields's: the script says how far it has got."""
    found, counts = duplicate_candidates(library)
    if not apply or not found:
        result = Result(attempted=len(found), details={"change": None, "conflicts": [], "written": {}})
    else:
        def plan_one(_path, held):
            repeated = duplicate_captions(held)
            if not repeated:
                return file_changes.skip(NO_DUPLICATE)
            return file_changes.Plan(after={field: "" for field in repeated}, detail=repeated)

        result = file_changes.write_fields(library, CLEAR_DUPLICATES, exiftool_path, found, list(DUPLICATE_READ), plan_one,
                                           summary={"photos": len(found)}, unreadable="skip", uncapped=True, patient=True,
                                           on_planned=on_planned, on_chunk=on_chunk)
    # Why each photo was left alone: a share that did not answer is not "nothing to clear" (#2622).
    away = {paths.key(path) for path in result.details.get("not_answered", [])}
    left = {"nothing to clear": 0, "unreadable": 0, "away": 0}
    for path, why in result.skipped:
        left["away" if paths.key(path) in away else "nothing to clear" if why == NO_DUPLICATE else "unreadable"] += 1
    result.details.update(counts=counts, dry_run=not apply)
    result.details["left alone"] = left
    return result


@file_changes.exclusively()
@roots_service.canonical_args("photo_path")
def save_photo(library, photo_path, title, tags, date_taken, exiftool_path, rename_format, stamp=None, base=NO_BASE):
    """Save one photo's title, tags and Date Taken -- the photo panel -- and rename it
    after its new title if Smart Rename named it.

    The title is written to the title fields alone (fields.title_fields); no caption field is written but those that
    hold the Description a photo with no title showed as its title, which the save clears (_caption_moved; DECISIONS
    2026-11-06).

    `tags` is the photo's whole tag list. Only the ones the file does not hold yet are
    checked (tagpup.core.validation): one written by another program must not stop the
    photo being saved, least of all a save that removes it. A new one that may not be
    set refuses the save and nothing is written; the others go in in their one spelling,
    as the tag tree holds them. The caption is held to its rules the same way.

    The write is a change of photo files (tagpup.services.file_changes), which can be
    undone: the photo's keyword, caption and date fields, before and after, committed
    first, and the row told what the file holds as it is marked done. It was written
    with ExifTool of its own and recorded nowhere (docs/findings.md, #266). A file
    holding it all already is not written: `changed` is 0. A write that fails is in the Result's
    errors (the route answers 500 from them); nothing is renamed.

    A rename is a change of its own in the library's journal (file_changes.rename: in History, undoable), which
    moves the photo's index row -- embedding, faces and all -- rather than leaving them behind; then the row gets
    what the file holds now. A rename that fails leaves the file as it was: `renamed` False, `rename_failed` says why. A failure recording
    it is logged, not raised: the file is written either way. The read, the write and the
    rename hold the lock of changes of photo files (file_changes.exclusively): a write
    between the read and the write was overwritten.

    A photo in a folder the library does not hold (Just look) is written to its file only
    (tagpup.services.file_only): no row, no journal change (`change` is None), nothing
    derived, nothing recorded of it afterwards; the caption still renames it after itself.
    The library's own answer decides, here, now. A photo that does not decode is refused.

    `stamp`, (mtime, size), is the file's stamp as the record the page built the save from had it: a file whose stamp
    is not that has changed since the page read it, and the save is refused (as CHANGED_ON_DISK, the web route's
    409) and writes nothing -- the whole tag list it carries may be missing what the file now holds. None (the CLI,
    the MCP) is no check, as it was.

    `base`, {"tags": [...], "title": "..."}, is the tags and caption the page read of the photo: the stamp can be
    kept (a copy keeps its times; a same-length rename of a tag then the time put back), so under the same lock,
    after the read of the file's current state, the file's tags (as a set) and caption are compared with `base` and
    any difference refuses the save the same way, writing nothing. `base` None -- a record of a photo ExifTool
    could not read when it was opened -- is refused (UNREADABLE_BASE_SENTENCE): the whole tag list it carries is
    not made from what the file holds. Not given at all is no check.

    details: `new_path`, `renamed`, `tags` as written, `flat` and `hierarchical` as
    written, `change`, `stamp`, the file's (mtime, size) as the write recorded it, after the write, when it wrote the
    file and did not rename it (None: the caller looks), `dates`, the date fields the write set ({field: value}; a clip's XMP:CreateDate only where it
    held one, fields.held_dates), `taken`, the Date Taken saved as the library spells it (None when none was sent),
    `base`, the tags and title the file holds after the write (the page's next `base`), and
    `index_warning` when the renamed photo's new name already had rows; `file_only` and `with_rows`, how many files (0 or 1) were written each way; `pictures_refused` when the write
    changed the picture's bytes and was made again (file_changes); `named_faces` ([{"id", "name"}]) when the tags written named
    a face (the one-face rule, as the row was recorded), and what follow_people says.
    """
    result = Result(attempted=1)
    _held, loose = libraries.split(library, [photo_path])
    files_only = bool(loose)
    wanted = fields.title_fields(title or "")
    if date_taken:
        wanted.update(fields.date_taken_fields(date_taken, clip=images.is_clip(photo_path)))
    photo_path = paths.stored(photo_path)
    # A clip this save may not write is refused with its own sentence before anything is compared (#2584); one it may
    # write is compared by its title as the page shows it, its QuickTime Title among the fields read (BASE_READ).
    # The read is the write's (write_fields asks again with all of it, _caption_moved clearing no caption of a clip): the
    # fields it writes and the ones its tags come from. A file of a folder the library does not hold names none, as
    # file_only asks.
    clip_read = None if files_only else _save_read(fields.keyword_fields([], []), wanted)
    if not file_changes.leave_out_clips(Result(), [photo_path], read=clip_read):
        result.refuse(images.CLIP_NOT_EDITED)
        return result
    # A clip's Date Taken goes into its QuickTime headers too, which hold no date before 1904 or after early 2040: the
    # digits asked are the ones the write sets (date_taken_fields), a zone left out, as Convert's carry asks.
    problem = date_taken and images.is_clip(photo_path) and field_values.clip_date_refused(
        wanted[fields.CLIP_HEADER_DATES[0]])
    if problem:
        result.refuse(problem)
        return result
    if stamp is not None and photo_actions.changed_on_disk(photo_path, stamp):
        result.refuse(CHANGED_ON_DISK_SENTENCE, CHANGED_ON_DISK)
        return result
    # One ExifTool session and one read before the write: the tags the file holds, the
    # plan's before, and the name Smart Rename kept.
    read = list(dict.fromkeys(SAVE_READ + BASE_READ + tuple(wanted) + tuple(fields.caption_fields(""))))
    with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
        now = field_values.read_one(et, photo_path, read)
        if base is None:
            result.refuse(UNREADABLE_BASE_SENTENCE, CHANGED_ON_DISK)
            return result
        if base is not NO_BASE and _differs_from_base(now, base):
            result.refuse(CHANGED_ON_DISK_SENTENCE, CHANGED_ON_DISK)
            return result
        held = set(_tags_held(now))
        problem = (validation.first_problem("tag", (t for t in tags if t not in held))
                   or (None if files_only else refuse_under_people(library, [t for t in tags if t not in held]))
                   or _caption_problem(et, photo_path, title))
        if problem:
            result.refuse(problem)
            return result
        tags = [t if t in held else vocabulary.normalize(t) for t in tags]
        flat, hierarchical = fields.expand_tag_fields(tags)
        after = dict(fields.keyword_fields(flat, hierarchical))
        after.update(fields.held_dates(_title_written(photo_path, now, wanted), now))
        asked = fields.date_taken_fields(date_taken, clip=images.is_clip(photo_path)) if date_taken else ()
        dates_written = {field: after[field] for field in asked if field in after}
        after.update(_caption_moved(photo_path, now))
        plan = lambda _path, _held: file_changes.Plan(after=after)   # noqa: E731
        if files_only:
            written = file_only.write_fields(exiftool_path, [photo_path], list(after), plan, et=et,
                                             held={paths.key(photo_path): now})
        else:
            # Read back whole and recorded so, stamped, as the file is marked done: the save's row, once.
            written = file_changes.write_fields(library, "save photo", exiftool_path, [photo_path],
                                                _save_read(after), plan, summary={"photos": 1}, et=et,
                                                held={paths.key(photo_path): now}, whole=True)
        if not written.ok:
            result.errors.extend(written.errors)
            result.changed = written.changed
            return result
        result.changed = written.changed
        kept = now.get(fields.PRESERVED_NAME) or [""]
        new_path, skipped, rename_failed = _rename_after_caption(
            library, photo_path, paths.stored(metadata.title_filename(photo_path, title, rename_format, kept[0])),
            files_only)
        renamed = not paths.same(new_path, photo_path)
        if rename_failed:
            result.details["rename_failed"] = rename_failed
        if written.details.get("pictures_refused"):
            result.details["pictures_refused"] = written.details["pictures_refused"]
        if written.details.get("named_faces"):
            result.details["named_faces"] = written.details["named_faces"]
        if not renamed:
            result.details["stamp"] = (written.details.get("stamps") or {}).get(paths.key(photo_path))
        result.details.update(new_path=new_path, renamed=renamed, tags=tags, flat=flat,
                              hierarchical=hierarchical, index_warning=None, change=written.details["change"],
                              dates=dates_written, taken=fields.date_taken_value(date_taken) if date_taken else None,
                              base={"tags": _tags_held(after), "title": _held_title({**now, **after})})
        touched = 1 if (written.changed or renamed) else 0
        result.details.update({file_only.FILE_ONLY: touched if files_only else 0,
                               file_only.WITH_ROWS: 0 if files_only else touched})
    if files_only:
        return result   # no row to tell: the index reads the file when the folder is added
    if skipped:
        result.details["index_warning"] = (
            "Renamed, but the index already has a photo at %s; its rows were left as they were." % new_path)
    elif written.changed:
        # A person really taken off the photo is off its faces, one really put back is on its face again (#908), now that
        # the index says what the file holds (the write recorded its row, the rename moved it).
        follow_people(library, result, moved={photo_path: new_path})
    return result


def follow_people(library, result, moved=None):
    """What the change of photo files `result` made (details["change"]) did to the people on its photos is done to their faces
    (tagpup.services.face_people.follow_change: the one place that decides it, from the change's own files, so a photo that
    never held the tag, or whose file was not written, is not touched). Said in `result`'s details: `unnamed_faces` and
    `renamed_faces` ([{"id", "name"}], for a page that reads its faces again), `faces_changes` (the journal's ids), and
    `faces_problem` when the faces could not be followed -- the files are written all the same. Never raises."""
    change = result.details.get("change")
    if change is None:
        return
    from tagpup.services import face_people   # face_people writes through this module
    try:
        done = face_people.follow_change(library, change, moved=moved)
    except Exception as e:
        logger.warning("Could not follow the people of change %s with their faces: %s", change, e)
        result.details["faces_problem"] = "The faces of the people taken off or put back could not be followed: %s" % type(e).__name__
        return
    if done.errors:
        logger.warning("Could not follow the people of change %s with their faces: %s", change, done.message())
        result.details["faces_problem"] = "The faces of the people taken off or put back could not be followed."
    if done.details.get("unnamed"):
        result.details["unnamed_faces"] = list(done.details["unnamed"])
    if done.details.get("renamed"):
        result.details["renamed_faces"] = list(done.details["renamed"])
    if done.details.get("faces_changes"):
        result.details["faces_changes"] = list(done.details["faces_changes"])


def _rename_after_caption(library, photo_path, wanted, files_only):
    """(the photo's path now, the (old, new) pairs whose new name the index already had, why the rename failed
    or None). The rename of a photo of the library is a change in its journal (file_changes.rename: in
    History, undoable, its row moving with it); a photo of a folder it does not hold is renamed alone, as Smart
    Rename does it. A rename that fails leaves the file as it was and is reported, not raised: the fields were
    written."""
    if wanted == photo_path:
        return photo_path, [], None
    try:
        if files_only:
            done = file_only.rename({photo_path: wanted}, {})
        else:
            done = file_changes.rename(library, "rename after caption", {photo_path: wanted}, {}, summary={"photos": 1})
    except Exception as e:
        logger.warning("Could not rename %s after its caption: %s", photo_path, e)
        return photo_path, [], e.message() if isinstance(e, names.RenameFailed) else str(e)
    return paths.stored(done.done.get(photo_path, wanted)), done.skipped or [], None


def _caption_problem(et, photo_path, caption):
    """Why `caption` cannot be set on the photo, or None. A caption the file holds
    already is not being set: one another program wrote is kept, as a tag is. The file
    is read for it only when the caption breaks a rule -- every field a caption is held
    in, EXIF's too -- and the caption is compared as the reader trims the file's."""
    problem = validation.problem("caption", caption or "")
    if not problem:
        return None
    found = et.get_tags([photo_path], tags=[field for field in fields.HELD_CAPTION_FIELDS if ":" in field])
    held = fields.captions_held(found[0] if found else {})
    return None if fields.trimmed(caption) in held else problem


#: What a bulk write says of a photo whose file is gone (the page starts the reason with "missing, ").
MISSING_WHY = "missing, nothing is written to it: its file is not on disk"

#: details key: how many photos of a bulk write were left out because their files are gone.
SKIPPED_MISSING = "skipped_missing"


def leave_out_missing(photo_paths):
    """(`photo_paths` but those whose file is gone, [(path, why)] of those left out) (file_changes.file_states). A bulk write
    skips them and writes the rest: the library still holds the row of a missing photo and a view shows it
    (phase 9c), so a selection can name one. A file that cannot be read, or on a share that does not answer, is NOT
    missing: the write meets it (file_changes.write_fields makes a photo on a share asleep its error: at once in a capped
    write, after one more look once the share's window is over in a patient one, the caption sweep's), and stops at it as for any read or
    write error of a present file."""
    states = file_changes.file_states(photo_paths)
    return ([path for path, state in states if state != file_changes.GONE],
            [(path, MISSING_WHY) for path, state in states if state == file_changes.GONE])


#: What the journal calls a change that adds and removes tags on a selection (History's `operation`).
ADD_TO_ALL = "add to all selected"


def change_tags(library, photo_paths, add, remove, exiftool_path, operation=ADD_TO_ALL, stop_at_first_error=True,
                persons=None, et=None, group=None):
    """Add the same tags to many photos and take the same tags off them: change_tags_of, each photo given `add` and
    `remove`. A bulk edit of a selection (tagpup.services.bulk_edit) passes its own `operation` and
    `stop_at_first_error=False`."""
    return change_tags_of(library, {path: (add, remove) for path in photo_paths}, exiftool_path, operation=operation,
                          stop_at_first_error=stop_at_first_error, persons=persons, et=et, group=group)


@roots_service.canonical_args("changes")
def change_tags_of(library, changes, exiftool_path, operation=ADD_TO_ALL, stop_at_first_error=True, persons=None,
                   et=None, group=None):
    """Give each photo in `changes` (path -> (tags to add, tags to take off)) its own tags added and taken off, as ONE
    change of photo files (change_each). The page's bulk tag write (POST /api/photos/bulk-tags) sends each file's own.

    `operation` names the change in the journal. `stop_at_first_error=False` (a bulk edit's): a photo that cannot be
    read or written is an error and the others are written all the same. `persons` ({paths.key(path): the tags added
    that are people}): a person the file already names is not added again (_change_each). `et`: an ExifTool session
    the caller opened (a bulk edit gets its own, with a deadline of its own), used instead of one per write. `group`:
    as change_each's.

    A photo whose file is gone is left out and listed in the result's skips (details[SKIPPED_MISSING] says how many);
    the photos after it are written. Only a read or write ERROR of a present file stops the run
    (_change_each).

    What is added is checked (tagpup.core.validation) and written in its one spelling;
    what is taken off is not: taking a bad tag off must stay possible. One that may not
    be set refuses the whole change, and nothing is written."""
    adds = list(dict.fromkeys(tag for add, _remove in changes.values() for tag in add))
    problem = validation.first_problem("tag", adds) or refuse_under_people(library, adds)
    if problem:
        return _refused(len(changes), problem)
    present, gone = leave_out_missing(list(changes))
    result = (change_each(library, {path: ([vocabulary.normalize(tag) for tag in changes[path][0]], changes[path][1])
                                    for path in present},
                          exiftool_path, operation, persons=persons, et=et, stop_at_first_error=stop_at_first_error,
                          group=group)
              if present else _refused(0, None))
    if not result.refused:
        result.attempted += len(gone)
        for path, why in gone:
            result.skip(path, why)
    result.details[SKIPPED_MISSING] = len(gone)
    if not result.refused:
        follow_people(library, result)
    return result


@roots_service.canonical_args("additions")
def add_tags(library, additions, exiftool_path, persons=None):
    """Add each photo in `additions` (path -> tags) its own tags. Apply All on a folder's
    suggestions: suggestions deal in people's bare names, which are written as the tags
    they are filed under. A photo with nothing to add is left alone. See change_each.
    A tag that may not be set refuses the whole of it, and nothing is written.

    `persons` ({paths.key(path): the tags of `additions` that are people}): a person the file already
    names is not added again (_change_each)."""
    return change_each(library, {path: (tags, ()) for path, tags in additions.items()}, exiftool_path,
                       "apply all suggestions", persons=persons)


@roots_service.canonical_args("changes")
def change_each(library, changes, exiftool_path, operation, persons=None, et=None, stop_at_first_error=True,
                unpooled=None, on_planned=None, group=None):
    """Give each photo in `changes` (path -> (tags to add, tags to take off)) its own, as ONE change of
    photo files named `operation` in the journal. A photo with nothing to add or take off is left alone.
    Held photos are written with their rows and journaled; a photo in a folder
    the library does not hold (Just look: what Suggest offered it was analysed in memory) is written to
    its file only (tagpup.services.file_only): no row, no journal change, and the tag tree is only read,
    so a person or tag the tree does not hold is written as it is offered. The library's own answer
    decides, per photo, now. What is added is checked (tagpup.core.validation); what is taken off is not.

    `persons`: as add_tags'. `et`: an ExifTool session the caller opened (with a deadline of its own), as
    change_tags'. `stop_at_first_error` False: a photo that cannot be read or written is an error and the others are
    written, as a bulk edit does. Apply All's own is add_tags; the faces' (tagpup.services.face_people) adds and takes
    off a person per photo. `unpooled`: the caller asks which faces the held photos' records take out of identity work;
    `on_planned` is told the id of the held photos' change (file_changes.write_fields).

    `group`: the token of the one action of the owner's this is a chunk of (journal.GROUP, written in the change's
    summary): History lists the chunks as one line and undoes them together. A group it cannot carry (batches.group_problem)
    refuses the whole of it, nothing written."""
    adds = dict.fromkeys(t for add, _remove in changes.values() for t in add)
    problem = (validation.first_problem("tag", adds) or refuse_under_people(library, adds)
               or batches.group_problem(group, len(changes)))
    if problem:
        return _refused(len(changes), problem)
    refused = _refused(len(changes), None)
    held, loose = libraries.split(library, [path for path, (add, remove) in changes.items() if add or remove])

    def plan_of(chosen):
        return [(path, list(changes[path][0]), changes[path][1]) for path in chosen]
    done = None
    if held or not loose:
        done = _change_each(library, plan_of(held), exiftool_path, operation, persons=persons, et=et,
                            stop_at_first_error=stop_at_first_error, unpooled=unpooled, on_planned=on_planned,
                            group=group)
    if not loose:
        return file_only.combined(done, None)
    if stop_at_first_error and done is not None and not done.ok:
        done.attempted += len(loose)
        for path in loose:
            done.skip(path, "not written: an earlier photo failed")
        return file_only.combined(done, None)
    files = _change_each(library, plan_of(loose), exiftool_path, operation, files_only=True, persons=persons,
                         et=et, stop_at_first_error=stop_at_first_error)
    return file_only.combined(done, files)


class PersonFiler:
    """name -> the tag a suggested person is written as, the rule a click on the suggestion's chip
    follows (the page's resolveTagOrPerson): a name the tree files once is that path; a name it does
    not hold is filed under the library's one people root (People/<name>). A name it files twice, or
    one it files nowhere in a tree with several people roots, is None: the page asks, and Apply All,
    with nobody to ask, refuses (`why_not`). It wrote the bare name, a keyword that names nobody. The
    tree is read once, here, and only read: no node is made."""

    def __init__(self, library):
        self.filed, self.roots = taxonomy.people_filing(library.path)

    def __call__(self, name):
        name = (name or "").strip()
        return vocabulary.person_tag(name, self.filed.get(vocabulary.key(name), []), self.roots)

    def names_already(self, tags, name, known):
        """Do the keywords `tags` name one of the people `name` is filed as (by the id of their node)? Such a photo has
        them, whichever: nothing is asked of it."""
        return any(vocabulary.names_same_person(known, tag, path)
                   for path in self.filed.get(vocabulary.key(name), []) for tag in tags)

    def why_not(self, names):
        """The sentence refusing a run that names `names`, people this cannot file."""
        said = []
        for name in names:
            paths_ = self.filed.get(vocabulary.key(name), [])
            if len(paths_) > 1:
                said.append("%s is filed under %s" % (name, " and ".join(paths_)))
            else:
                said.append("%s is not filed, and the library has several people roots (%s)" % (name, ", ".join(self.roots)))
        return ("Nothing was written: %s. Add them on a photo, choosing which, then Apply All again."
                % "; ".join(said))


def person_filer(library):
    """The PersonFiler of `library`'s tree as it stands."""
    return PersonFiler(library)


def apply_suggestions(library, suggestions, exiftool_path):
    """Apply All: write each photo of `suggestions` ({path: its entry}, what Suggest offered) the tags and
    people the panel showed (tagpup.core.suggesting.offered_tags), and nothing else. A person is filed as a click on
    their chip files them (person_filer) and is left out when the file already names them by the id of their node
    (vocabulary.names_same_person; a keyword spelled like their leaf is nobody, #1394), read from the file as it is under
    the write's lock. A person it cannot file (a name filed twice, or nowhere among several people roots) refuses the
    run before anything is written, unless the photo's file already names one of the people filed as that name (read
    here, of those photos alone): that photo has them. A file that cannot be read names nobody, and the run is refused.
    Held or not held (add_tags)."""
    file_person = person_filer(library)
    asked = {path: [name for name in ((person.get("name") or "").strip() for person in entry["people"])
                    if name and file_person(name) is None]
             for path, entry in suggestions.items()}
    unfiled = set()
    if any(asked.values()):
        known = taxonomy.people_vocabulary(library.path)
        with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
            read = field_values.read(et, [path for path, names in asked.items() if names], KEYWORD_READ)
        held = {key: ([] if isinstance(fields_, Exception) else list(_tags_held(fields_))) for key, fields_ in read.items()}
        unfiled = {name for path, names in asked.items() for name in names
                   if not file_person.names_already(held.get(paths.key(path), []), name, known)}
    if unfiled:
        return _refused(len(suggestions), file_person.why_not(sorted(unfiled, key=vocabulary.tag_sort_key)))
    additions, persons = {}, {}
    for path, entry in suggestions.items():
        # A person the photo already has, whom the tree files twice, is no tag to add (None).
        additions[path] = [tag for tag in suggesting.offered_tags(entry, file_person) if tag is not None]
        offered = {file_person(person["name"]) for person in entry["people"]
                   if (person.get("name") or "").strip()} - {None}
        persons[paths.key(path)] = offered
    return add_tags(library, additions, exiftool_path, persons=persons)


def _refused(attempted, problem):
    """A bulk change refused before anything was written; `written` is empty."""
    result = Result(attempted=attempted)
    result.details["written"] = {}
    if problem:
        result.refuse(problem)
    return result


#: What a keyword write reads of each file: the fields its tags come from, and every
#: field it writes (tagpup.core.fields.keyword_fields), which the journal records before
#: and after.
KEYWORD_READ = tuple(dict.fromkeys(fields.TAG_SOURCE_FIELDS + tuple(fields.keyword_fields([], []))))

#: What a save reads of the file before it writes, beside the caption and date fields
#: it writes: the fields its tags come from and every keyword field (KEYWORD_READ), and
#: the name Smart Rename kept, by which a new caption renames it.
SAVE_READ = KEYWORD_READ + (fields.PRESERVED_NAME,)


def _save_read(*written):
    """What a save of a held photo says it reads (file_changes.write_fields' `read`): the fields of each of `written` and
    every field its tags come from (fields.TAG_SOURCE_FIELDS)."""
    return list(dict.fromkeys([field for each in written for field in each] + list(fields.TAG_SOURCE_FIELDS)))


def _keywords_plan(tags):
    """The Plan of a file whose tags are to be `tags`: every keyword field, and the tags
    and their two forms as written (details["written"])."""
    flat, hierarchical = fields.expand_tag_fields(tags)
    return file_changes.Plan(after=fields.keyword_fields(flat, hierarchical), detail=(tags, flat, hierarchical))


def _tags_held(held):
    return vocabulary.extract_tags({field: held.get(field) for field in fields.TAG_SOURCE_FIELDS})


def keywords_planner(library, edit):
    """The `plan_one` of a write of keywords (file_changes.write_fields) whose photos each get their own change: `edit(path, held)`
    gives the (tags to add, tags to take off) for a photo that holds the tags `held`. Each write replaces the photo's whole keyword
    set, in the file's order with what is added after (a set's order changed from run to run, and a file holding every tag already
    was written again for its order)."""
    def plan_one(path, held):
        held_tags = list(_tags_held(held))
        add, remove = edit(path, held_tags)
        tags = [tag for tag in dict.fromkeys(held_tags + list(add)) if tag not in set(remove)]
        return _keywords_plan(tags)
    return plan_one


def _change_each(library, plan, exiftool_path, operation, files_only=False, persons=None, stop_at_first_error=True,
                 et=None, unpooled=None, on_planned=None, group=None):
    """Write each photo in `plan` -- (path, tags to add, tags to take off) -- as one change
    of photo files (tagpup.services.file_changes): planned from what every file holds,
    committed, then written a file at a time, each recorded in its row as it is marked
    done. The change can be undone.

    Each write replaces the photo's whole keyword set, so it starts from what the file
    holds now -- never from a cache that may be cold or an index that may never have
    seen the photo. People are written as the tags they are filed under, and the index
    is told what was written. A file changed outside between the plan and its write is
    a conflict, reported and not overwritten.

    Stops at the first photo that cannot be read or written, which is the error; the
    photos before it keep their changes (unless not `stop_at_first_error`: each such photo is an error and the others
    are written). details: `written`, path -> (tags, flat,
    hierarchical) for each photo written, and `change`. With `files_only` (photos of folders
    the library does not hold: tagpup.services.file_only) the files are written and nothing
    else is: no journal change (`change` is None), no row.

    `persons` ({paths.key(path): tags that are people}): of what is added, a person the file already names
    is left out -- by the id of their node (two people called alike are two: vocabulary.names_same_person) --
    read from the file here, under the lock, as the page leaves out one the photo already has. `group`: the token
    the change's summary carries (change_each).
    """
    known = taxonomy.people_vocabulary(library.path) if persons else None
    wanted = {paths.key(path): (add, remove) for path, add, remove in plan}

    def edit(path, held_tags):
        add, remove = wanted[paths.key(path)]
        mine = (persons or {}).get(paths.key(path), ())
        return [tag for tag in add
                if not (tag in mine and any(vocabulary.names_same_person(known, tag, there) for there in held_tags))], remove

    plan_one = keywords_planner(library, edit)

    if files_only:
        return file_only.write_fields(exiftool_path, [path for path, _a, _r in plan], KEYWORD_READ, plan_one,
                                      stop_at_first_error=stop_at_first_error, et=et)
    return file_changes.write_fields(library, operation, exiftool_path, [path for path, _a, _r in plan],
                                     KEYWORD_READ, plan_one,
                                     summary={"photos": len(plan), **({journal.GROUP: group} if group else {})},
                                     stop_at_first_error=stop_at_first_error, et=et, unpooled=unpooled,
                                     on_planned=on_planned)


@roots_service.canonical_args("photo_paths")
def replace_tag(library, photo_paths, old, new, exiftool_path):
    """Rename the tag `old` to `new` -- and every tag under it -- on each photo in
    `photo_paths`, or take it off without `new`: in the file, then in the index.

    Renaming or deleting a node of the tag tree, merging one tag into another and
    renaming a person all come here. Each photo is written through the one keyword
    writer and recorded through the one recorder,
    as a bulk edit is: this used to write its own rows, recording two of the keyword
    fields and not the file's new mtime, so a renamed tag's photos were re-read on every
    scan and a stale IPTC:Keywords was re-derived straight back into the tags.

    Each photo's keywords are read from its file first. The write replaces the whole
    keyword set, and the index can lag the file: starting from the index's copy dropped
    a keyword written elsewhere since, and wrote back one taken off elsewhere (finding
    #30). A photo whose file does not carry the tag is left alone, and its row, which
    said it did, is made to say what the file holds.

    attempted: the photos the index has a row for. changed: the files rewritten, as one
    change of photo files (tagpup.services.file_changes), which can be undone. A photo not
    carrying the tag is skipped; one that could not be read or written, or that changed
    outside between the plan and its write, is an error.
    """
    rows = photos.read_tags(library.path, photo_paths)
    def plan_one(path, held):
        new_tags, changed = vocabulary.retag(_tags_held(held), old, new)
        if not changed:
            # Its row is made to say what the file holds (file_changes.write_fields).
            return file_changes.skip("does not carry the tag")
        return _keywords_plan(new_tags)

    operation = "rename tag" if new else "remove tag"
    # A photo whose file is gone (or whose root is not there) carries nothing to rewrite: skipped, so it does not hold the tree
    # back. One that is there and cannot be written (a share that does not answer, a locked file) is an error that does.
    present, gone = leave_out_missing([path for path, _t, _r in rows])
    # The tag tree's rewrite has no cap (owner, 2026-10-11): it has progress, and Cancel, as every batch.
    result = file_changes.write_fields(library, operation, exiftool_path, present, KEYWORD_READ, plan_one,
                                       summary={"photos": len(present)}, uncapped=True)
    result.skipped.extend(gone)
    return result
