"""Actions on photos' tags and captions."""
import logging
import os

from tagpup.core import fields, paths, suggesting, validation, vocabulary
from tagpup.core.result import Result
# Looked up at call time, as exiftool_session.ExifToolSession, so a test standing in for
# ExifTool there reaches this too.
from tagpup.files import exiftool_session, keywords, metadata
from tagpup.services import file_changes
from tagpup.store import photos, taxonomy

logger = logging.getLogger(__name__)


def save_photo(library, photo_path, title, tags, date_taken, exiftool_path, rename_format):
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
    it is logged, not raised: the file is written either way.

    details: `new_path`, `renamed`, `tags` as written, `flat` and `hierarchical` as
    written, `change`, and `index_warning` when the renamed photo's new name already had
    rows.
    """
    result = Result(attempted=1)
    wanted = fields.caption_fields(title or "")
    if date_taken:
        wanted.update(fields.date_taken_fields(date_taken))
    people = taxonomy.people_paths(library.path)
    with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
        held = set(keywords.tags_in_file(et, photo_path))
        problem = (validation.first_problem("tag", (t for t in tags if t not in held))
                   or _caption_problem(et, photo_path, title))
    if problem:
        result.refuse(problem)
        return result
    tags = [t if t in held else vocabulary.normalize(t) for t in tags]
    flat, hierarchical = fields.expand_tag_fields(vocabulary.resolve_people(tags, people))
    after = dict(fields.keyword_fields(flat, hierarchical))
    after.update(wanted)
    written = file_changes.write_fields(library, "save photo", exiftool_path, [photo_path], list(after),
                                        lambda _path, _held: file_changes.Plan(after=after), summary={"photos": 1})
    if not written.ok:
        raise RuntimeError(written.message())
    result.changed = written.changed

    new_path = paths.stored(metadata.sync_title_to_filename(photo_path, title, exiftool_path, rename_format))
    renamed = not paths.same(new_path, photo_path)
    result.details.update(new_path=new_path, renamed=renamed, tags=tags, flat=flat,
                          hierarchical=hierarchical, index_warning=None, change=written.details["change"])
    try:
        with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
            raw_meta = metadata.raw_metadata(et, new_path)
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


def change_tags(library, photo_paths, add, remove, exiftool_path):
    """Add the same tags to many photos and take the same tags off them. Adding or
    removing tags on a selection of photos. See _change_each.

    What is added is checked (tagpup.core.validation) and written in its one spelling;
    what is taken off is not: taking a bad tag off must stay possible. One that may not
    be set refuses the whole change, and nothing is written."""
    problem = validation.first_problem("tag", add)
    if problem:
        return _refused(len(photo_paths), problem)
    add = [vocabulary.normalize(tag) for tag in add]
    return _change_each(library, [(path, add, remove) for path in photo_paths], exiftool_path,
                        "add to all selected")


def add_tags(library, additions, exiftool_path):
    """Add each photo in `additions` (path -> tags) its own tags. Apply All on a folder's
    suggestions: suggestions deal in people's bare names, which are written as the tags
    they are filed under. A photo with nothing to add is left alone. See _change_each.
    A tag that may not be set refuses the whole of it, and nothing is written."""
    problem = validation.first_problem("tag", dict.fromkeys(t for tags in additions.values() for t in tags))
    if problem:
        return _refused(len(additions), problem)
    return _change_each(library, [(path, tags, ()) for path, tags in additions.items() if tags],
                        exiftool_path, "apply all suggestions")


def _refused(attempted, problem):
    """A bulk change refused before anything was written; `written` is empty."""
    result = Result(attempted=attempted)
    result.details["written"] = {}
    result.refuse(problem)
    return result


#: What a keyword write reads of each file: the fields its tags come from, and every
#: field it writes (tagpup.core.fields.keyword_fields), which the journal records before
#: and after.
KEYWORD_READ = tuple(dict.fromkeys(fields.TAG_SOURCE_FIELDS + tuple(fields.keyword_fields([], []))))


def _keywords_plan(tags):
    """The Plan of a file whose tags are to be `tags`: every keyword field, and the tags
    and their two forms as written (details["written"])."""
    flat, hierarchical = fields.expand_tag_fields(tags)
    return file_changes.Plan(after=fields.keyword_fields(flat, hierarchical), detail=(tags, flat, hierarchical))


def _tags_held(held):
    return vocabulary.extract_tags({field: held.get(field) for field in fields.TAG_SOURCE_FIELDS})


def _change_each(library, plan, exiftool_path, operation):
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
    hierarchical) for each photo written, and `change`.
    """
    people = taxonomy.people_paths(library.path)
    wanted = {paths.key(path): (add, remove) for path, add, remove in plan}

    def plan_one(path, held):
        add, remove = wanted[paths.key(path)]
        # In the file's order, what is added after: a set's order changed from run to
        # run, and a file holding every tag already was written again for its order.
        tags = [tag for tag in dict.fromkeys(list(_tags_held(held)) + list(add)) if tag not in set(remove)]
        return _keywords_plan(vocabulary.resolve_people(tags, people))

    return file_changes.write_fields(library, operation, exiftool_path, [path for path, _a, _r in plan],
                                     KEYWORD_READ, plan_one, summary={"photos": len(plan)},
                                     stop_at_first_error=True)


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

    return file_changes.write_fields(library, "write suggestions", exiftool_path,
                                     [path for path, _tags, _caption in wanted.values()], SUGGESTION_READ,
                                     plan_one, summary={"photos": len(wanted)})
