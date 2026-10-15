"""Actions on photos' tags and captions."""
import logging
import os

from tagpup.core import fields, paths, suggesting, validation, vocabulary
from tagpup.core.result import CHANGED_ON_DISK, CHANGED_ON_DISK_SENTENCE, DAMAGED_PHOTOS, UNREADABLE_BASE_SENTENCE, Result
# Looked up at call time, as exiftool_session.ExifToolSession, so a test standing in for
# ExifTool there reaches this too.
from tagpup.files import exiftool_session, field_values, metadata, names, shares
from tagpup.services import file_changes, file_only, libraries
from tagpup.services import photos as photo_actions
from tagpup.services import roots as roots_service
from tagpup.store import db, person_ids, photos, taxonomy

logger = logging.getLogger(__name__)


#: `base` not given: no check of the file's tags and caption (the CLI, the MCP). None is a page's record of a photo
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
    """The caption the file holds, as the page reads it (the first of its captions, trimmed)."""
    found = vocabulary.extract_captions({field: now.get(field) for field in vocabulary.CAPTION_FIELDS})
    return vocabulary.trimmed(found[0]) if found else ""


def _differs_from_base(now, base):
    """Does the file hold other tags or another caption than the page read (`base`, {"tags", "title"})? The tags
    as a SET of keyword paths, the caption trimmed: the two things a save writes over."""
    return (set(_tags_held(now)) != set(base.get("tags") or [])
            or _held_title(now) != vocabulary.trimmed(base.get("title") or ""))


@file_changes.exclusively()
@roots_service.canonical_args("photo_path")
def save_photo(library, photo_path, title, tags, date_taken, exiftool_path, rename_format, stamp=None, base=NO_BASE):
    """Save one photo's caption, tags and Date Taken -- the photo panel -- and rename it
    after its new caption if Smart Rename named it.

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
    is not that has changed since the page read it, and the save is refused (details `changed_on_disk`, the web route's
    409) and writes nothing -- the whole tag list it carries may be missing what the file now holds. None (the CLI,
    the MCP) is no check, as it was.

    `base`, {"tags": [...], "title": "..."}, is the tags and caption the page read of the photo: the stamp can be
    kept (a copy keeps its times; a same-length rename of a tag then the time put back), so under the same lock,
    after the read of the file's current state, the file's tags (as a set) and caption are compared with `base` and
    any difference refuses the save the same way, writing nothing. `base` None -- a record of a photo ExifTool
    could not read when it was opened -- is refused (UNREADABLE_BASE_SENTENCE): the whole tag list it carries is
    not made from what the file holds. Not given at all is no check.

    details: `new_path`, `renamed`, `tags` as written, `flat` and `hierarchical` as
    written, `change`, and `index_warning` when the renamed photo's new name already had
    rows; `file_only` and `with_rows`, how many files (0 or 1) were written each way.
    """
    result = Result(attempted=1)
    _held, loose = libraries.split(library, [photo_path])
    files_only = bool(loose)
    if files_only:
        if file_only.refuse_unwritable(result, [photo_path]):
            return result
    elif libraries.refuse_writes(result, library, [photo_path]):
        return result
    wanted = fields.caption_fields(title or "")
    if date_taken:
        wanted.update(fields.date_taken_fields(date_taken))
    photo_path = paths.stored(photo_path)
    if stamp is not None and photo_actions.changed_on_disk(photo_path, stamp):
        result.refuse(CHANGED_ON_DISK_SENTENCE)
        result.details[CHANGED_ON_DISK] = True
        return result
    # One ExifTool session and one read before the write: the tags the file holds, the
    # plan's before, and the name Smart Rename kept.
    read = list(dict.fromkeys(SAVE_READ + tuple(wanted)))
    with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
        try:
            now = field_values.read_one(et, photo_path, read, validate=True)
        except field_values.CutShort as cut:
            result.refuse("%s: damaged, nothing is written to it: %s. Restore it from a backup, then try again." % (
                os.path.basename(photo_path), cut))
            result.details[DAMAGED_PHOTOS] = [photo_path]
            return result
        if base is None:
            result.refuse(UNREADABLE_BASE_SENTENCE)
            result.details[CHANGED_ON_DISK] = True
            return result
        if base is not NO_BASE and _differs_from_base(now, base):
            result.refuse(CHANGED_ON_DISK_SENTENCE)
            result.details[CHANGED_ON_DISK] = True
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
        after.update(wanted)
        plan = lambda _path, _held: file_changes.Plan(after=after)   # noqa: E731
        if files_only:
            written = file_only.write_fields(exiftool_path, [photo_path], list(after), plan, et=et,
                                             held={paths.key(photo_path): now}, read_back_also=fields.METADATA_FIELDS)
        else:
            written = file_changes.write_fields(library, "save photo", exiftool_path, [photo_path], list(after),
                                                plan, summary={"photos": 1}, et=et,
                                                held={paths.key(photo_path): now},
                                                read_back_also=fields.METADATA_FIELDS)
        if not written.ok:
            result.errors.extend(written.errors)
            result.changed = written.changed
            return result
        result.changed = written.changed
        kept = now.get(names.PRESERVED_NAME) or [""]
        new_path, skipped, rename_failed = _rename_after_caption(
            library, photo_path, paths.stored(metadata.title_filename(photo_path, title, rename_format, kept[0])),
            files_only)
        renamed = not paths.same(new_path, photo_path)
        if rename_failed:
            result.details["rename_failed"] = rename_failed
        result.details.update(new_path=new_path, renamed=renamed, tags=tags, flat=flat,
                              hierarchical=hierarchical, index_warning=None, change=written.details["change"],
                              base={"tags": _tags_held(after), "title": vocabulary.trimmed(title or "")})
        touched = 1 if (written.changed or renamed) else 0
        result.details.update({file_only.FILE_ONLY: touched if files_only else 0,
                               file_only.WITH_ROWS: 0 if files_only else touched})
        try:
            # What the write's read back found; where nothing was written, or the file
            # was renamed since (its SourceFile is its old name), a read now.
            record = None if renamed else written.details["read_back"].get(paths.key(photo_path))
            raw_meta = metadata.raw_metadata(et, new_path, record)
        except Exception as e:
            logger.warning("Could not read back %s: %s", new_path, e)
            return result
    if files_only:
        return result   # no row to tell: the index reads the file when the folder is added
    try:
        recorded_tags = vocabulary.extract_tags(raw_meta)
        if skipped:
            result.details["index_warning"] = (
                "Renamed, but the index already has a photo at %s; its rows were left as they were."
                % new_path)
        else:
            # The journaled write carried its vectors over the write already.
            photos.record_saved(library.path, new_path, recorded_tags, [title] if title else [], raw_meta)
            # A person really taken off the photo is off its faces, one really put back is on its face again (#908), once the
            # index says what the file holds.
            if written.changed:
                follow_people(library, result, moved={photo_path: new_path})
    except Exception as e:
        logger.warning("Failed to update SQLite database metadata for %s: %s", new_path, e)
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
    found = et.get_tags([photo_path], tags=[field for field in vocabulary.HELD_CAPTION_FIELDS if ":" in field])
    held = vocabulary.captions_held(found[0] if found else {})
    return None if vocabulary.trimmed(caption) in held else problem


#: What a bulk write says of a photo whose file is gone (the page starts the reason with "missing, ").
MISSING_WHY = "missing, nothing is written to it: its file is not on disk"

#: details key: how many photos of a bulk write were left out because their files are gone.
SKIPPED_MISSING = "skipped_missing"


def leave_out_missing(photo_paths):
    """(`photo_paths` but those whose file is gone, [(path, why)] of those left out). A bulk write skips them and writes
    the rest, as it does a damaged photo: the library still holds the row of a missing photo and a view shows it
    (phase 9c), so a selection can name one. A file that cannot be read, or on a share that does not answer, is NOT
    missing: the write meets it itself, and stops at it as for any read or write error of a present file."""
    present, gone = [], []
    for path in photo_paths:
        (gone if shares.stamp_of(path) is None else present).append(path)
    return present, [(path, MISSING_WHY) for path in gone]


#: What the journal calls a change that adds and removes tags on a selection (History's `operation`).
ADD_TO_ALL = "add to all selected"


@roots_service.canonical_args("photo_paths")
def change_tags(library, photo_paths, add, remove, exiftool_path, operation=ADD_TO_ALL, stop_at_first_error=True,
                persons=None, et=None):
    """Add the same tags to many photos and take the same tags off them. Adding or
    removing tags on a selection of photos. See _change_each.

    `operation` names the change in the journal. A bulk edit of a selection (tagpup.services.bulk_edit) passes its own and
    `stop_at_first_error=False`: a photo that cannot be read or written is an error and the others are written all the
    same. `persons` ({paths.key(path): the tags of `add` that are people}): a person the file already names is not
    added again (_change_each). `et`: an ExifTool session the caller opened (a bulk edit gets its own,
    with a deadline of its own), used instead of one per write.

    A photo whose file is gone is left out and listed in the result's skips (details[SKIPPED_MISSING] says how many);
    the photos after it are written. Only a read or write ERROR of a present file stops the run
    (_change_each).

    What is added is checked (tagpup.core.validation) and written in its one spelling;
    what is taken off is not: taking a bad tag off must stay possible. One that may not
    be set refuses the whole change, and nothing is written."""
    problem = validation.first_problem("tag", add) or refuse_under_people(library, add)
    if problem:
        return _refused(len(photo_paths), problem)
    present, gone = leave_out_missing(photo_paths)
    add = [vocabulary.normalize(tag) for tag in add]
    result = (change_each(library, {path: (add, remove) for path in present}, exiftool_path, operation, persons=persons,
                          et=et, stop_at_first_error=stop_at_first_error)
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
def change_each(library, changes, exiftool_path, operation, persons=None, et=None, stop_at_first_error=True):
    """Give each photo in `changes` (path -> (tags to add, tags to take off)) its own, as ONE change of
    photo files named `operation` in the journal. A photo with nothing to add or take off is left alone.
    Held photos are written with their rows and journaled, and the damaged skipped; a photo in a folder
    the library does not hold (Just look: what Suggest offered it was analysed in memory) is written to
    its file only (tagpup.services.file_only): no row, no journal change, and the tag tree is only read,
    so a person or tag the tree does not hold is written as it is offered. The library's own answer
    decides, per photo, now. What is added is checked (tagpup.core.validation); what is taken off is not.

    `persons`: as add_tags'. `et`: an ExifTool session the caller opened (with a deadline of its own), as
    change_tags'. `stop_at_first_error` False: a photo that cannot be read or written is an error and the others are
    written, as a bulk edit does. Apply All's own is add_tags; the faces' (tagpup.services.face_people) adds and takes
    off a person per photo."""
    adds = dict.fromkeys(t for add, _remove in changes.values() for t in add)
    problem = validation.first_problem("tag", adds) or refuse_under_people(library, adds)
    if problem:
        return _refused(len(changes), problem)
    refused = _refused(len(changes), None)
    held, loose = libraries.split(library, [path for path, (add, remove) in changes.items() if add or remove])
    if held and libraries.refuse_writes(refused, library, held):
        return refused
    kept, left = held, []

    def plan_of(chosen):
        return [(path, list(changes[path][0]), changes[path][1]) for path in chosen]
    done = None
    if held or not loose:
        done = libraries.with_skipped(_change_each(library, plan_of(kept), exiftool_path, operation,
                                                   persons=persons, et=et,
                                                   stop_at_first_error=stop_at_first_error), left)
    if not loose:
        return file_only.combined(done, None)
    if stop_at_first_error and done is not None and not done.ok:
        done.attempted += len(loose)
        for path in loose:
            done.skip(path, "not written: an earlier photo failed")
        return file_only.combined(done, None)
    writable, skipped = file_only.leave_out_unwritable(loose)
    files = libraries.with_skipped(
        _change_each(library, plan_of(writable), exiftool_path, operation, files_only=True, persons=persons,
                     et=et, stop_at_first_error=stop_at_first_error)
        if writable else _refused(0, None), skipped)
    return file_only.combined(done, files)


def person_filer(library):
    """name -> the tag a suggested person is written as, the rule a click on the suggestion's chip
    follows (the page's resolveTagOrPerson): a name the tree files once is that path; a name it does
    not hold is filed under the library's one people root (People/<name>); a name it files twice,
    or a tree with several people roots, is left as it is -- the page asks, and a bare name is the
    form that a bare keyword is, a tag of its own. The tree is read once, here, and only
    read: no node is made. The people the suggester names bare were written bare by Apply All."""
    filed, roots = taxonomy.people_filing(library.path)

    def file_person(name):
        name = (name or "").strip()
        return vocabulary.person_tag(name, filed.get(vocabulary.key(name), []), roots) or name
    return file_person


def apply_suggestions(library, suggestions, exiftool_path, threshold=0.0):
    """Apply All: write each photo of `suggestions` ({path: its entry}, what Suggest offered) the tags and
    people the panel showed, scoring at least `threshold` (tagpup.core.suggesting.offered_tags), and nothing
    else. A person is filed as a click on their chip files them (person_filer) and is left out when the file
    already names them by their leaf, whatever the spelling or the path -- the page's photoAlreadyHas -- read
    from the file as it is under the write's lock. Held or not held (add_tags)."""
    file_person = person_filer(library)
    additions, persons = {}, {}
    for path, entry in suggestions.items():
        additions[path] = suggesting.offered_tags(entry, threshold, file_person)
        offered = {file_person(person["name"]) for person in entry.get("people") or []
                   if person.get("score", 0.0) >= threshold and (person.get("name") or "").strip()}
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
SAVE_READ = KEYWORD_READ + (names.PRESERVED_NAME,)


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
                 et=None):
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
    read from the file here, under the lock, as the page leaves out one the photo already has.
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
                                     KEYWORD_READ, plan_one, summary={"photos": len(plan)},
                                     stop_at_first_error=stop_at_first_error, et=et)


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
