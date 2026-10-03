"""Actions on photos' tags and captions."""
import logging
import os

from tagpup.core import fields, paths, suggesting, validation, vocabulary
from tagpup.core.result import CHANGED_ON_DISK, CHANGED_ON_DISK_SENTENCE, UNREADABLE_BASE_SENTENCE, Result
# Looked up at call time, as exiftool_session.ExifToolSession, so a test standing in for
# ExifTool there reaches this too.
from tagpup.files import exiftool_session, field_values, metadata, names
from tagpup.services import damaged_photos, file_changes, file_only, libraries
from tagpup.services import photos as photo_actions
from tagpup.services import roots as roots_service
from tagpup.store import photos, taxonomy

logger = logging.getLogger(__name__)


#: `base` not given: no check of the file's tags and caption (the CLI, the MCP). None is a page's record of a photo
#: ExifTool could not read; a dict is what the page read.
NO_BASE = object()


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
    holding it all already is not written: `changed` is 0. A write that fails raises,
    as it did, for the page to say so.

    A rename moves the photo's index row -- embedding, faces and all -- rather than
    leaving them behind; then the row gets what the file holds now. A failure recording
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
    people = taxonomy.people_paths(library.path)
    photo_path = paths.stored(photo_path)
    if stamp is not None and photo_actions.changed_on_disk(photo_path, stamp):
        result.refuse(CHANGED_ON_DISK_SENTENCE)
        result.details[CHANGED_ON_DISK] = True
        return result
    # One ExifTool session and one read before the write: the tags the file holds, the
    # plan's before, and the name Smart Rename kept.
    read = list(dict.fromkeys(SAVE_READ + tuple(wanted)))
    with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
        now = field_values.read_one(et, photo_path, read)
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
                   or _caption_problem(et, photo_path, title))
        if problem:
            result.refuse(problem)
            return result
        tags = [t if t in held else vocabulary.normalize(t) for t in tags]
        flat, hierarchical = fields.expand_tag_fields(vocabulary.resolve_people(tags, people))
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
            raise RuntimeError(written.message())
        result.changed = written.changed
        kept = now.get(names.PRESERVED_NAME) or [""]
        new_path = paths.stored(metadata.sync_title_to_filename(photo_path, title, exiftool_path, rename_format,
                                                                kept[0]))
        renamed = not paths.same(new_path, photo_path)
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
        skipped = photos.move_rows(library.path, {photo_path: new_path})[1] if renamed else []
        if skipped:
            result.details["index_warning"] = (
                "Renamed, but the index already has a photo at %s; its rows were left as they were."
                % new_path)
        else:
            # The journaled write carried its vectors over the write already.
            photos.record_saved(library.path, new_path, recorded_tags, [title] if title else [], raw_meta)
    except Exception as e:
        logger.warning("Failed to update SQLite database metadata for %s: %s", new_path, e)
    return result


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
        (gone if damaged_photos.stamp_of(path) is None else present).append(path)
    return present, [(path, MISSING_WHY) for path in gone]


@roots_service.canonical_args("photo_paths")
def change_tags(library, photo_paths, add, remove, exiftool_path):
    """Add the same tags to many photos and take the same tags off them. Adding or
    removing tags on a selection of photos. See _change_each.

    A photo whose file is gone is left out and listed in the result's skips (details[SKIPPED_MISSING] says how many);
    the photos after it are written. Only a read or write ERROR of a present file stops the run
    (_change_each).

    What is added is checked (tagpup.core.validation) and written in its one spelling;
    what is taken off is not: taking a bad tag off must stay possible. One that may not
    be set refuses the whole change, and nothing is written."""
    problem = validation.first_problem("tag", add)
    if problem:
        return _refused(len(photo_paths), problem)
    present, gone = leave_out_missing(photo_paths)
    result = _change_present(library, present, add, remove, exiftool_path) if present else _refused(0, None)
    if not result.refused:
        result.attempted += len(gone)
        for path, why in gone:
            result.skip(path, why)
    result.details[SKIPPED_MISSING] = len(gone)
    return result


def _change_present(library, photo_paths, add, remove, exiftool_path):
    """change_tags for photos whose files are there."""
    refused = _refused(len(photo_paths), None)
    held, loose = libraries.split(library, photo_paths)
    if held and libraries.refuse_writes(refused, library, held, damaged_ok=True):
        return refused
    # A damaged photo is skipped, the rest written (libraries.leave_out_damaged).
    kept, left = libraries.leave_out_damaged(refused, library, held) if held else ([], [])
    if kept is None:
        return refused
    add = [vocabulary.normalize(tag) for tag in add]
    done = None
    if held or not loose:
        done = libraries.with_skipped(_change_each(library, [(path, add, remove) for path in kept],
                                                   exiftool_path, "add to all selected"), left)
    if not loose:
        return file_only.combined(done, None)
    # The photos of folders the library does not hold: their files only, in the same request
    # (tagpup.services.file_only). After the others: a failure stops the run, as always.
    if done is not None and not done.ok:
        done.attempted += len(loose)
        for path in loose:
            done.skip(path, "not written: an earlier photo failed")
        return file_only.combined(done, None)
    writable, skipped = file_only.leave_out_unwritable(loose)
    files = libraries.with_skipped(
        _change_each(library, [(path, add, remove) for path in writable], exiftool_path, "add to all selected",
                     files_only=True) if writable else _refused(0, None), skipped)
    return file_only.combined(done, files)


@roots_service.canonical_args("additions")
def add_tags(library, additions, exiftool_path, persons=None):
    """Add each photo in `additions` (path -> tags) its own tags. Apply All on a folder's
    suggestions: suggestions deal in people's bare names, which are written as the tags
    they are filed under. A photo with nothing to add is left alone. See _change_each.
    A tag that may not be set refuses the whole of it, and nothing is written.

    A photo in a folder the library does not hold (Just look: what Suggest offered it was
    analysed in memory) is written to its file only, as change_tags does
    (tagpup.services.file_only): no row, no journal change, and the tag tree is only read, so a
    person or tag the tree does not hold is written as it is offered. The library's own answer
    decides, per photo, now.

    `persons` ({paths.key(path): the tags of `additions` that are people}): a person the file already
    names by their leaf is not added again (_change_each)."""
    problem = validation.first_problem("tag", dict.fromkeys(t for tags in additions.values() for t in tags))
    if problem:
        return _refused(len(additions), problem)
    refused = _refused(len(additions), None)
    held, loose = libraries.split(library, [path for path, tags in additions.items() if tags])
    if held and libraries.refuse_writes(refused, library, held, damaged_ok=True):
        return refused
    # A damaged photo is skipped, the rest written (libraries.leave_out_damaged).
    kept, left = libraries.leave_out_damaged(refused, library, held) if held else ([], [])
    if kept is None:
        return refused
    done = None
    if held or not loose:
        done = libraries.with_skipped(_change_each(library, [(path, additions[path], ()) for path in kept],
                                                   exiftool_path, "apply all suggestions", persons=persons), left)
    if not loose:
        return file_only.combined(done, None)
    if done is not None and not done.ok:
        done.attempted += len(loose)
        for path in loose:
            done.skip(path, "not written: an earlier photo failed")
        return file_only.combined(done, None)
    writable, skipped = file_only.leave_out_unwritable(loose)
    files = libraries.with_skipped(
        _change_each(library, [(path, additions[path], ()) for path in writable], exiftool_path,
                     "apply all suggestions", files_only=True, persons=persons) if writable
        else _refused(0, None), skipped)
    return file_only.combined(done, files)


def person_filer(library):
    """name -> the tag a suggested person is written as, the rule a click on the suggestion's chip
    follows (the page's resolveTagOrPerson): a name the tree files once is that path; a name it does
    not hold is filed under the library's one people root (People/<name>); a name it files twice,
    or a tree with several people roots, is left as it is -- the page asks, and a bare name is the
    form resolve_people still files when the person is added. The tree is read once, here, and only
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


def _change_each(library, plan, exiftool_path, operation, files_only=False, persons=None):
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
    photos before it keep their changes. details: `written`, path -> (tags, flat,
    hierarchical) for each photo written, and `change`. With `files_only` (photos of folders
    the library does not hold: tagpup.services.file_only) the files are written and nothing
    else is: no journal change (`change` is None), no row.

    `persons` ({paths.key(path): tags that are people}): of what is added, a person the file already names
    by their leaf is left out -- read from the file here, under the lock, as the page leaves out one the
    photo already has.
    """
    people = taxonomy.people_paths(library.path)
    wanted = {paths.key(path): (add, remove) for path, add, remove in plan}

    def plan_one(path, held):
        add, remove = wanted[paths.key(path)]
        held_tags = list(_tags_held(held))
        mine = (persons or {}).get(paths.key(path), ())
        add = [tag for tag in add
               if not (tag in mine and any(vocabulary.same_person(tag, there) for there in held_tags))]
        # In the file's order, what is added after: a set's order changed from run to
        # run, and a file holding every tag already was written again for its order.
        tags = [tag for tag in dict.fromkeys(held_tags + list(add)) if tag not in set(remove)]
        return _keywords_plan(vocabulary.resolve_people(tags, people))

    if files_only:
        return file_only.write_fields(exiftool_path, [path for path, _a, _r in plan], KEYWORD_READ, plan_one,
                                      stop_at_first_error=True)
    return file_changes.write_fields(library, operation, exiftool_path, [path for path, _a, _r in plan],
                                     KEYWORD_READ, plan_one, summary={"photos": len(plan)},
                                     stop_at_first_error=True)


@roots_service.canonical_args("photo_paths")
def replace_tag(library, photo_paths, old, new, exiftool_path):
    """Rename the tag `old` to `new` -- and every tag under it -- on each photo in
    `photo_paths`, or take it off without `new`: in the file, then in the index.

    Renaming or deleting a node of the tag tree, merging one tag into another and
    renaming a person all come here. Each photo is written through the one keyword
    writer, people resolved to their tags first, and recorded through the one recorder,
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
    people = taxonomy.people_paths(library.path)

    def plan_one(path, held):
        new_tags, changed = vocabulary.retag(_tags_held(held), old, new)
        if not changed:
            # Its row is made to say what the file holds (file_changes.write_fields).
            return file_changes.skip("does not carry the tag")
        return _keywords_plan(vocabulary.resolve_people(new_tags, people))

    operation = "rename tag" if new else "remove tag"
    return file_changes.write_fields(library, operation, exiftool_path, [path for path, _t, _r in rows],
                                     KEYWORD_READ, plan_one, summary={"photos": len(rows)})


def suggestion_writes(library, suggestions, min_score=suggesting.OFFER_A_TAG):
    """What the CLI's `write` writes from the entries of a suggestions file: (path, tags,
    caption) for each photo with a tag scoring at least `min_score`
    (tagpup.core.suggesting.written_tags), or a caption made from them; and the paths
    left out because there is no file there.

    The library's face roots, which the caption files people under, are read once for
    the run; scripts/writer.py read them again for every photo.
    """
    face_roots = taxonomy.people_vocabulary(library.path).roots
    writes, missing = [], []
    for entry in suggestions:
        path = entry.get("path")
        if not path or not os.path.exists(path):
            missing.append(path)
            continue
        tags = suggesting.written_tags(entry.get("suggested_tags", []), min_score)
        caption = suggesting.caption_from_tags(tags, face_roots)
        if tags or caption:
            writes.append((path, tags, caption))
    return writes, missing


#: What the CLI's `write` reads of each file: the keyword fields, and every field a
#: caption is written to (tagpup.core.fields.caption_fields).
SUGGESTION_READ = tuple(dict.fromkeys(KEYWORD_READ + tuple(fields.caption_fields(""))))


@roots_service.canonical_args("writes")
def write_suggestions(library, writes, exiftool_path, nobackup=False):
    """Write each (path, tags, caption) of `writes` (suggestion_writes') as one change of
    photo files (tagpup.services.file_changes), which can be undone, each file's row told
    what it holds as it is marked done.

    The tags are added to what the file holds, in the file's order, as Add to all
    selected adds them: whole paths only, people filed where the library's tree files
    them. The caption, when there is one, is written to every caption field. A file
    already holding both is left alone. The writer had its own ExifTool code that also
    wrote every path's parts as loose keywords, wrote people bare, and never told the
    index; then its writes were recorded nowhere, and undone only from the _original
    copies ExifTool left beside each file (docs/findings.md, #266). Those are not made
    any more: the journal is the way back, and `nobackup` is kept for callers that pass
    it.

    A photo that cannot be read or written is an error, and the next is tried; ExifTool
    that cannot be started raises. changed: the files written. details: `change`.

    A tag or a caption that may not be set refuses the whole run before anything is
    written (tagpup.core.validation).
    """
    result = Result(attempted=len(writes))
    problem = (validation.first_problem("tag", dict.fromkeys(t for _, tags, _ in writes for t in tags))
               or validation.first_problem("caption", (caption for _, _, caption in writes if caption)))
    if problem:
        result.refuse(problem)
        return result
    if libraries.refuse_writes(result, library, [path for path, _tags, _caption in writes], damaged_ok=True):
        return result
    # A damaged photo is skipped, the rest written (libraries.leave_out_damaged).
    kept, left = libraries.leave_out_damaged(result, library, [path for path, _tags, _caption in writes])
    if kept is None:
        return result
    if left:
        kept = {paths.key(path) for path in kept}
        writes = [write for write in writes if paths.key(write[0]) in kept]
    # Who a bare name means, read once for the run, not once per photo.
    people = taxonomy.people_paths(library.path)
    # A photo named twice in the file is written once, with the tags of both.
    wanted = {}
    for path, tags, caption in writes:
        _path, held_tags, held_caption = wanted.get(paths.key(path), (path, [], ""))
        wanted[paths.key(path)] = (_path, list(dict.fromkeys(held_tags + list(tags))), caption or held_caption)

    def plan_one(path, held):
        _path, tags, caption = wanted[paths.key(path)]
        after = {}
        if tags:
            merged = list(dict.fromkeys(list(_tags_held(held)) + tags))
            after.update(_keywords_plan(vocabulary.resolve_people(merged, people)).after)
        if caption:
            after.update(fields.caption_fields(caption))
        return file_changes.Plan(after=after, detail=(tags, caption))

    # What was skipped as damaged is in the Result returned: its skips, and skipped_damaged.
    return libraries.with_skipped(file_changes.write_fields(
        library, "write suggestions", exiftool_path, [path for path, _tags, _caption in wanted.values()],
        SUGGESTION_READ, plan_one, summary={"photos": len(wanted)}), left)
