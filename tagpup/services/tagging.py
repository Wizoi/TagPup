"""Actions on photos' tags and captions."""
import logging
import os

from tagpup.core import fields, paths, suggesting, validation, vocabulary
from tagpup.core.result import Result
# Looked up at call time, as exiftool_session.ExifToolSession, so a test standing in for
# ExifTool there reaches this too.
from tagpup.files import exiftool_session, keywords, metadata
from tagpup.store import db, embeddings, photos, taxonomy

logger = logging.getLogger(__name__)


def save_photo(library, photo_path, title, tags, date_taken, exiftool_path, rename_format):
    """Save one photo's caption, tags and Date Taken -- the photo panel -- and rename it
    after its new caption if Smart Rename named it.

    `tags` is the photo's whole tag list. Only the ones the file does not hold yet are
    checked (tagpup.core.validation): one written by another program must not stop the
    photo being saved, least of all a save that removes it. A new one that may not be
    set refuses the save and nothing is written; the others go in in their one spelling,
    as the tag tree holds them. The caption is held to its rules the same way. A rename moves the photo's index row -- embedding, faces and all -- rather
    than leaving them behind; then the row gets what the file holds now. A failure
    recording it is logged, not raised: the file is written either way.

    details: `new_path`, `renamed`, `tags` as written, `flat` and `hierarchical` as
    written, and `index_warning` when the renamed photo's new name already had rows.
    """
    result = Result(attempted=1)
    params = fields.caption_fields(title or "")
    if date_taken:
        params.update(fields.date_taken_fields(date_taken))
    people = taxonomy.people_paths(library.path)
    with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
        held = set(keywords.tags_in_file(et, photo_path))
        problem = (validation.first_problem("tag", (t for t in tags if t not in held))
                   or _caption_problem(et, photo_path, title))
        if problem:
            result.refuse(problem)
            return result
        tags = [t if t in held else vocabulary.normalize(t) for t in tags]
        before = embeddings.stamp_of(photo_path)
        flat, hierarchical = keywords.write_keywords(
            et, photo_path, vocabulary.resolve_people(tags, people), extra_params=params)
    result.changed = 1

    new_path = paths.stored(metadata.sync_title_to_filename(photo_path, title, exiftool_path, rename_format))
    renamed = not paths.same(new_path, photo_path)
    result.details.update(new_path=new_path, renamed=renamed, tags=tags, flat=flat,
                          hierarchical=hierarchical, index_warning=None)
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
            photos.record_saved(library.path, new_path, recorded_tags, [title] if title else [], raw_meta,
                                before=before)
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
    return _change_each(library, [(path, add, remove) for path in photo_paths], exiftool_path)


def add_tags(library, additions, exiftool_path):
    """Add each photo in `additions` (path -> tags) its own tags. Apply All on a folder's
    suggestions: suggestions deal in people's bare names, which are written as the tags
    they are filed under. A photo with nothing to add is left alone. See _change_each.
    A tag that may not be set refuses the whole of it, and nothing is written."""
    problem = validation.first_problem("tag", dict.fromkeys(t for tags in additions.values() for t in tags))
    if problem:
        return _refused(len(additions), problem)
    return _change_each(library, [(path, tags, ()) for path, tags in additions.items() if tags],
                        exiftool_path)


def _refused(attempted, problem):
    """A bulk change refused before anything was written; `written` is empty."""
    result = Result(attempted=attempted)
    result.details["written"] = {}
    result.refuse(problem)
    return result


def _change_each(library, plan, exiftool_path):
    """Write each photo in `plan` -- (path, tags to add, tags to take off) -- in one
    ExifTool session.

    Each write replaces the photo's whole keyword set, so it starts from what the file
    holds now -- never from a cache that may be cold or an index that may never have
    seen the photo. People are written as the tags they are filed under, and the index
    is told what was written.

    Stops at the first photo that cannot be read or written, which is the error; the
    photos before it keep their changes. details: `written`, path -> (tags, flat,
    hierarchical) for each photo written.
    """
    result = Result(attempted=len(plan))
    written = result.details["written"] = {}
    people = taxonomy.people_paths(library.path)
    with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
        for path, add, remove in plan:
            path = paths.stored(path)
            try:
                tags = set(keywords.tags_in_file(et, path))
                tags.update(add)
                tags.difference_update(remove)
                new_tags = vocabulary.resolve_people(list(tags), people)
                before = embeddings.stamp_of(path)
                flat, hierarchical = keywords.write_keywords(et, path, new_tags)
                photos.record_tags(library.path, path, new_tags, flat, hierarchical, before=before)
            except Exception as err:
                result.fail(path, err)
                break
            written[path] = (new_tags, flat, hierarchical)
            result.changed += 1
    return result


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

    attempted: the photos the index has a row for. changed: the rows rewritten. A photo
    not carrying the tag is skipped; one that could not be read or written is an error.
    """
    rows = photos.read_tags(library.path, photo_paths)
    result = Result(attempted=len(rows))
    people = taxonomy.people_paths(library.path)
    with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
        for path, indexed_tags, raw_meta in rows:
            try:
                current_tags = keywords.tags_in_file(et, path)
            except Exception as err:
                logger.error("Could not read the keywords of %s: %s", path, err)
                result.fail(path, err)
                continue
            new_tags, changed = vocabulary.retag(current_tags, old, new)
            if not changed:
                result.skip(path, "does not carry the tag")
                if set(current_tags) != set(indexed_tags):
                    photos.record_tags(library.path, path, current_tags)
                continue
            try:
                before = embeddings.stamp_of(path)
                flat, hierarchical = keywords.write_keywords(
                    et, path, vocabulary.resolve_people(new_tags, people))
                # The tags column holds the view extract_tags derives from the file's
                # fields, as it did before; derived here from exactly the fields just
                # written.
                updated = vocabulary.extract_tags(fields.record_keyword_fields(raw_meta, flat, hierarchical))
                if photos.record_tags(library.path, path, updated, flat, hierarchical, before=before):
                    result.changed += 1
            except Exception as err:
                logger.error("Failed to update metadata on disk/db for %s: %s", path, err)
                result.fail(path, err)
    return result


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


def write_suggestions(library, writes, exiftool_path, nobackup=False):
    """Write each (path, tags, caption) of `writes` (suggestion_writes') in one ExifTool
    session, and tell the index what was written.

    The caption first, so the stat recorded with the keywords is the file's final one.
    The tags are added to what the file holds, through the one keyword writer: whole
    paths only, people filed where the library's tree files them. The writer had its
    own ExifTool code that also wrote every path's parts as loose keywords, wrote people
    bare, and never told the index.

    A photo that cannot be written is an error, and the next is tried; ExifTool that
    cannot be started raises. changed: the photos written. `nobackup` has ExifTool
    overwrite each file rather than keep an _original beside it.

    A tag or a caption that may not be set refuses the whole run before anything is
    written (tagpup.core.validation).
    """
    result = Result(attempted=len(writes))
    problem = (validation.first_problem("tag", dict.fromkeys(t for _, tags, _ in writes for t in tags))
               or validation.first_problem("caption", (caption for _, _, caption in writes if caption)))
    if problem:
        result.refuse(problem)
        return result
    params = ["-overwrite_original"] if nobackup else None
    # Who a bare name means, read once for the run, not once per photo.
    people = taxonomy.people_paths(library.path)
    with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
        for path, tags, caption in writes:
            try:
                # The file's stamp before this write, over which its CLIP vectors are
                # carried (tagpup.store.embeddings).
                before = embeddings.stamp_of(path)
                if caption:
                    et.set_tags([path], tags=fields.caption_fields(caption), params=params)
                    db.write_with_connection(
                        library.path, lambda conn: photos.set_captions(conn, path, [caption]),
                        label="caption for %s" % path)
                if tags:
                    current = keywords.tags_in_file(et, path)
                    merged = current + [t for t in tags if t not in current]
                    flat, hierarchical = keywords.write_keywords(
                        et, path, vocabulary.resolve_people(merged, people))
                    photos.record_tags(library.path, path, flat, flat, hierarchical, before=before)
                elif caption:
                    photos.record_file_stat(library.path, path, before=before)
            except Exception as err:
                result.fail(path, err)
                continue
            result.changed += 1
    return result
