"""Actions on photo files, and reading a folder of them for the page."""
import json
import logging
import os

from tagpup.core import dates, fields, paths, renaming, validation, vocabulary
from tagpup.core.result import NotFound, Refused, Result
from tagpup.files import images, metadata, names, recycle_bin, shares
from tagpup.services import file_changes, file_only, libraries, thumbnails
from tagpup.services import roots as roots_service
from tagpup.store import db, embeddings, faces, photos, taxonomy
from tagpup.store import folders as store_folders

logger = logging.getLogger(__name__)

#: Photos read from disk in one ExifTool call. A folder of thousands is read in these.
READ_BATCH = 500


# ---- A folder, as the page sees it --------------------------------------------------------

def page_record(path, meta, mtime=0.0, size=0):
    """One photo as the TagPup page reads it, from what was read of it: `meta` is a
    record from the file reader or an index row (tags, people, captions, raw_metadata).

    "path" is the stored spelling whatever the caller had, so the browser only ever
    sees one spelling of a photo and hands back the one the index uses. `taken` is when
    it was taken as the library records it, which the page reads rather than fields of
    its own (docs/findings.md, #67). This was scripts/metadata.build_photo_ui_record.
    """
    path = paths.stored(path)
    raw_meta = meta.get("raw_metadata", {})
    captions = meta.get("captions", [])
    year = meta["year"] if "year" in meta else dates.photo_year(raw_meta, path)
    record = {
        "path": path,
        "filename": os.path.basename(path),
        "tags": meta.get("tags", []),
        "people": meta.get("people", []),
        "title": captions[0] if captions else "",
        "mtime": mtime,
        "size": size,
        "year": dates.shown_year(None if year is None else str(year)),
        "taken": dates.date_taken(raw_meta),
        "raw_metadata": raw_meta,
    }
    if meta.get("read_error"):
        # ExifTool could not read it: it shows as holding nothing, which a save must not take for what the file holds.
        record["unreadable"] = True
        record["read_error"] = meta["read_error"]
    return record


def file_stamp(path):
    """(mtime, size) of the file at `path` as it is now, or None when there is none or it cannot be read."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime, stat.st_size)


def changed_on_disk(path, stamp):
    """Is the file at `path` other than the one a page's record was built from, `stamp` = (mtime, size)? The row
    comparison the folder scan trusts a row by (store.photos.describes); a file that is gone is changed."""
    return not photos.describes(stamp[0], stamp[1], file_stamp(path))


def read_file(library, path, exiftool_path, stamp=None):
    """The page record of one photo read from its FILE with ExifTool, as the folder scan reads a photo whose row
    does not describe it (the library's people vocabulary told to the reader). mtime and size are the file's:
    `stamp`, (mtime, size), when the caller has just taken it."""
    stamp = stamp or file_stamp(path) or (None, None)
    return _read(library, [(path, stamp[0], stamp[1])], exiftool_path)[paths.key(path)]


def taken_order(record):
    """The folder view's order for a page record: when it was taken, then file time."""
    return dates.date_taken_sort_key(record.get("raw_metadata", {}), record.get("mtime", 0.0))


@roots_service.canonical_args("folder")
def scan_folder(library, folder, exiftool_path):
    """The photos under `folder`, at any depth, as page records keyed by paths.key:
    opening a folder in TagPup.

    A photo whose index row matches the file's mtime and size is taken from the row;
    the rest are read with ExifTool, in batches. A row with no stamp -- made for a
    photo Suggest saw but the index never read -- is read now (docs/findings.md, #94).
    The reader is told what the library says about people, as the indexer is; the
    server's scan used the default face roots alone.
    """
    folder = paths.stored(folder)
    image_files = images.photos_under(folder)
    if not image_files:
        return {}

    known = {}
    try:
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            for path, mtime, size, tags, people, captions, raw_meta in photos.rows_under(conn, folder):
                known[paths.key(path)] = {
                    "path": paths.stored(path), "mtime": mtime, "size": size,
                    "tags": json.loads(tags) if tags else [],
                    "people": json.loads(people) if people else [],
                    "captions": json.loads(captions) if captions else [],
                    "raw_metadata": json.loads(raw_meta) if raw_meta else {},
                }
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Failed to query index DB for folder scan cache: %s", e)

    found, to_read = {}, []
    for file in image_files:
        try:
            stat = os.stat(file)
        except OSError:
            continue
        row = known.get(paths.key(file))
        if row and photos.describes(row["mtime"], row["size"], (stat.st_mtime, stat.st_size)):
            found[paths.key(file)] = page_record(row["path"], row, stat.st_mtime, stat.st_size)
        else:
            to_read.append((file, stat.st_mtime, stat.st_size))

    if to_read:
        logger.info("Scan found %d new/modified files in %s. Running ExifTool...", len(to_read), folder)
        try:
            found.update(_read(library, [(f, mt, sz) for f, mt, sz in to_read], exiftool_path))
        except Exception as e:
            logger.error("Error running ExifTool during scan: %s", e)
    return found


@roots_service.canonical_args("folder")
def read_folder(library, folder, exiftool_path):
    """Every photo under `folder` read from its file, whatever the index holds, as page
    records keyed by paths.key: the folder after Smart Rename, and a time shift on a
    folder never opened. `mtime` and `size` come from the reader."""
    folder = paths.stored(folder)
    image_files = images.photos_under(folder)
    if not image_files:
        return {}
    return _read(library, [(f, None, None) for f in image_files], exiftool_path)


def _read(library, files, exiftool_path):
    """{paths.key: page record} of `files`, each (path, mtime, size), mtime and size the
    reader's when None, read with ExifTool in batches of READ_BATCH."""
    people = taxonomy.people_vocabulary(library.path)
    reader = metadata.MetadataExtractor(exiftool_path=exiftool_path)
    found = {}
    for start in range(0, len(files), READ_BATCH):
        batch = files[start:start + READ_BATCH]
        for (file, mtime, size), meta in zip(batch, reader.batch_read([f for f, _, _ in batch], people=people)):
            found[paths.key(file)] = page_record(
                file, meta, meta.get("mtime", 0.0) if mtime is None else mtime,
                meta.get("size", 0) if size is None else size)
    return found


def people_of(library, raw_meta, tags, photo_path):
    """Everyone in a photo, by its metadata and its named faces
    (tagpup.core.vocabulary.people_in_photo), for a page record just written."""
    return vocabulary.people_in_photo(raw_meta, tags, faces.face_names(photo_path, db_path=library.path),
                                      taxonomy.people_vocabulary(library.path))


def record_written(library, record, path, tags, flat, hierarchical):
    """Bring a page record up to date with the keywords just written to its photo: its
    tags, every keyword field of its raw metadata, and its people.

    Every keyword field, not just the XMP pair: the page re-derives tags from the raw
    metadata, and a stale IPTC:Keywords brought a removed tag straight back.
    """
    raw_meta = fields.record_keyword_fields(record.setdefault("raw_metadata", {}), flat, hierarchical)
    record["tags"] = list(tags)
    record["people"] = people_of(library, raw_meta, tags, path)
    return raw_meta


def page_copy(photo_path, max_size=None, upright=True):
    """(bytes, content type) of a photo for a page: the file itself, or with `max_size`
    a JPEG no larger than that on a side -- turned upright by its Orientation when
    `upright`. A copy that cannot be made falls back to the file.

    Refused for a file the servers do not send: the path is the page's to name.
    """
    if not images.is_servable(photo_path):
        raise Refused("Forbidden: Invalid file type requested")
    if not os.path.exists(photo_path):
        raise NotFound("Photo file not found: %s" % photo_path)
    if max_size:
        try:
            return images.smaller_copy(photo_path, max_size, upright), "image/jpeg"
        except Exception as e:
            logger.warning("Could not make a smaller copy of %s: %s", photo_path, e)
    with open(photo_path, "rb") as f:
        return f.read(), images.content_type(photo_path)


#: How long a look at a photo's header waits on a network share, and how long a share that did not
#: answer is taken as away (tagpup.files.shares.bounded).
SHAPE_WAIT = 1.0
SHAPE_AWAY = 30.0


def box_shape(photo_path):
    """What a page needs to draw a face's box over the photo: {"size": [width, height] of the
    pixels the boxes are in (the file as stored; images.shown_size), or None when the file
    cannot be read -- no size, no box, rather than a box on the wrong face (#787) --, "turned":
    does the file declare an Orientation (2 to 8) that the picture is shown by, so that a box
    drawn over it in the stored pixels is not where the face is. Only the header is read, once (a
    TIFF is decoded, and its boxes turn with it: never "turned"). On a network share the look is
    bounded (tagpup.files.shares.bounded): a share that does not answer in a second gives no size
    and no box, and is not asked again for a while, never a request stalled for as long as Windows
    waits (#836)."""
    none = {"size": None, "turned": False}
    state, shape = shares.bounded(photo_path, lambda: images.shown_shape(photo_path), SHAPE_WAIT, SHAPE_AWAY)
    if state != "ok":
        if state == "error":
            logger.info("No size for %s: %s", photo_path, shape)
        return none
    width, height, oriented, orientation = shape
    if width <= 0 or height <= 0:
        return none
    return {"size": [width, height], "turned": orientation != 1 and not oriented}


def face_crop(library, face_id):
    """A face's crop, as JPEG bytes: kept in its row, or cut from its photo and kept
    there the first time it is asked for. Both servers had a copy of this, and each
    wrote the crop back on a connection of its own, without the write lock.

    Raises ValueError for a box that cannot be read.
    """
    found = faces.crop_of(library.path, face_id)
    if not found:
        raise NotFound("Face not found")
    photo_path, box, crop = found
    if crop:
        return crop
    if not photo_path or not os.path.exists(photo_path):
        raise NotFound("Original photo not found")
    box = images.parse_box(box)
    if len(box) < 4:
        raise ValueError("Invalid bounding box")
    crop = images.face_crop(photo_path, box)
    try:
        faces.cache_crop(library.path, face_id, crop)
    except Exception as e:
        logger.warning("Could not cache face crop %s: %s", face_id, e)
    return crop


@roots_service.canonical_args("photo_paths")
def preserve_names(library, photo_paths, exiftool_path, split=None):
    """Write each photo's current name into its XMP-xmpMM:PreservedFileName where it holds
    none -- the name it had before Smart Rename first renamed it, which later renames
    leave alone, and which relink_photos finds a renamed photo by -- as one change of
    photo files, `smart rename: original names` (tagpup.services.file_changes), which
    can be undone. A photo holding one already is skipped; one that cannot be read is
    skipped too, as the rename leaves it be. A Result, as write_fields'. A photo of a folder
    the library does not hold has the name written to its file only (tagpup.services.
    file_only): no journal change, no row. `split` is (held, loose) as the caller decided it
    (libraries.split): Smart Rename decides once and hands it on, so the folder added in between
    cannot send the names one way and the renames the other (#524).

    A file-only rename has no journal: if it stops part-way, run Smart Rename on the folder again
    -- it regenerates the same names -- and a file left named tmp_rename_* is a photo waiting for
    its new name.
    """
    def plan_one(path, held):
        if held.get(names.PRESERVED_NAME):
            return file_changes.skip("keeps the name it had before it was first renamed")
        return file_changes.Plan(after={names.PRESERVED_NAME: os.path.basename(path)})

    if split is None:
        held, loose = libraries.split(library, photo_paths)
    else:
        wanted = {paths.key(path) for path in photo_paths}
        held, loose = [[path for path in side if paths.key(path) in wanted] for side in split]
    journaled = file_changes.write_fields(
        library, "smart rename: original names", exiftool_path, held, [names.PRESERVED_NAME], plan_one,
        summary={"photos": len(held)}, unreadable="skip") if held else None
    files = file_only.write_fields(exiftool_path, loose, [names.PRESERVED_NAME], plan_one,
                                   unreadable="skip") if loose else None
    return file_only.combined(journaled, files)


@roots_service.canonical_args("photo_paths")
@file_changes.exclusively()
def smart_rename(library, photo_paths, grouping, rename_format, exiftool_path):
    """Number photos in the order given and name each for it: "<grouping> - <index> -
    <caption>" in `rename_format`, the caption being the one on the photo. Smart Rename.

    Each photo stays in its own folder. A file already holding one of the new names is
    moved aside to "<name>_conflict_<n>". The files are renamed all together or not at
    all (tagpup.files.names.rename_all), as one change of photo files (tagpup.services.
    file_changes): the renames and moves aside planned and committed first, and marked
    done in the transaction that tells the index where they went -- their rows carry
    their embeddings and faces, names included, and one rename that did not tell it
    stranded 78 rows holding 234 faces. The change can be undone. A photo no longer on
    disk keeps its number, unused. First each photo keeps the name it had before its
    first rename (preserve_names), a change of its own; one whose name could not be
    kept is an error, and nothing is renamed.

    details: `updated_paths`, old -> new for every photo, those already so named
    included; `renamed`, those whose name changed; `moved_aside`, the files moved out
    of the way; `index_rows_moved`; `index_skipped`, the (old, new) pairs whose new name
    already had rows in the index, left as they were; `change`.

    The photos of a folder the library does not hold (Just look) are renamed, all together
    or not at all among themselves, with no journal change and no row to move
    (tagpup.services.file_only); the others as above. A failure of the second part leaves
    the first part renamed, and says so. `file_only` and `with_rows` count the photos
    renamed each way.

    Refused, and nothing renamed, for a grouping that may not be used
    (tagpup.core.validation). The grouping is used trimmed as the rules trim it
    (validation.trim): the rules allow blanks at its ends, and a trailing space -- from
    a caller that does not strip, or before a U+FEFF, which strip() leaves -- put a
    double space before every photo's number.
    """
    result = Result(attempted=len(photo_paths))
    problem = validation.problem("grouping", grouping)
    if problem:
        result.refuse(problem)
        return result
    # A photo found damaged refuses the whole rename, nothing moved: skipped, it kept a name
    # the numbering gave another photo, which moved it aside and lost its record.
    held, loose = libraries.split(library, photo_paths)
    if held and libraries.refuse_writes(result, library, held):
        return result
    if loose and file_only.refuse_unwritable(result, loose):
        return result
    held_keys = {paths.key(p) for p in held}
    grouping = validation.trim(grouping)
    width = len(str(len(photo_paths)))
    present = [p for p in photo_paths if os.path.exists(p)]
    captions = names.read_for_renaming(exiftool_path, present)
    kept = preserve_names(library, [p for p in present if p in captions], exiftool_path, split=(held, loose))
    if kept.errors:
        # Nothing is renamed, as when this write raised: a photo renamed without the
        # name it had is found again only by its identity.
        for what, why in kept.errors:
            result.fail(what, "the name it had before its first rename could not be kept: %s" % why)
        return result
    renames = {}
    for index, old_path in enumerate(photo_paths, start=1):
        if old_path not in captions:
            continue
        base = renaming.file_base(rename_format, grouping, str(index).zfill(width), captions[old_path])
        renames[old_path] = os.path.join(os.path.dirname(old_path), base + os.path.splitext(old_path)[1])

    # Two parts: the photos of held folders, then the others. Photos stay in their own folder
    # and a folder is held or not, so the names of the parts never meet.
    held_renames = {old: new for old, new in renames.items() if paths.key(old) in held_keys}
    loose_renames = {old: new for old, new in renames.items() if paths.key(old) not in held_keys}
    done, moved_aside, skipped, moved, change, with_rows, files_only = {}, {}, [], 0, None, 0, 0

    def report():
        renamed = {old: new for old, new in done.items() if old != new}
        result.changed = len(renamed)
        result.details.update(updated_paths=done, renamed=renamed, moved_aside=moved_aside,
                              index_rows_moved=moved, index_skipped=skipped, change=change,
                              **{file_only.WITH_ROWS: with_rows, file_only.FILE_ONLY: files_only})

    stage = "held"
    try:
        if held_renames:
            # The rows move in the transaction that marks the files done, the files moved
            # aside and renamed into their names in one call, so each frees its name for the
            # next within it.
            outcome = file_changes.rename(library, "smart rename", held_renames, names.aside_for(held_renames),
                                          exiftool_path, summary={"photos": len(held_renames)})
            done.update(outcome.done)
            moved_aside.update(outcome.moved_aside)
            skipped, moved, change = list(outcome.skipped or []), outcome.moved, outcome.change_id
            with_rows = sum(1 for old, new in outcome.done.items() if old != new)
        if loose_renames:
            stage = "files only"
            outcome = file_only.rename(loose_renames, names.aside_for(loose_renames))
            done.update(outcome.done)
            moved_aside.update(outcome.moved_aside)
            files_only = sum(1 for old, new in outcome.done.items() if old != new)
    except Exception as failure:
        if not done and not isinstance(failure, names.RenameFailed):
            raise   # nothing was renamed: an error, as it always was
        # Any failure after the held part committed (#531): what did change is reported, not lost.
        report()
        said = failure.message() if isinstance(failure, names.RenameFailed) else (
            "Could not rename: %s." % failure)
        if done:
            said += " The photos of %s were renamed, and stay so." % (
                "the folders the library holds" if held_renames else "the other folders")
        if stage == "files only":
            said += " If it stops part-way, run Smart Rename on the folder again: it regenerates the same names. A file left named tmp_rename_* is a photo waiting for its new name; the name it had is in its PreservedFileName."
        result.fail("smart rename", said)
        return result

    report()
    for old_path, new_path in skipped:
        logger.warning("Renamed %s to %s, but the index already has rows for the new "
                       "name; left both as they were.", old_path, new_path)
    logger.info("Renamed %d photo(s) (%d to files only); moved %d index row(s).", result.changed, files_only, moved)
    return result


def date_shift_plan(minutes, strict=False):
    """The `plan_one` of a Date Taken shift by `minutes` (tagpup.services.file_changes.write_fields): each date the photo
    holds is moved by the same minutes; one it does not hold is not made, and a photo holding none is skipped. The one plan
    of Camera Time Shift (shift_date_taken) and of the bulk job (tagpup.services.bulk_edit). `strict` (the job): a date the
    shift would move out of range (dates.ShiftOutOfRange) is raised, and so the photo's error with a sentence; Camera Time
    Shift leaves such a photo alone, as it always did."""
    shift = dates.shifted_strictly if strict else dates.shifted

    def plan_one(_path, held):
        after = {}
        for field in dates.SHIFTED_FIELDS:
            values = held.get(field) or []
            moved = shift(values[0], minutes) if len(values) == 1 else None
            if moved is not None:
                after[field] = [moved]
        return file_changes.Plan(after=after) if after else file_changes.skip("holds no Date Taken to move")
    return plan_one


@roots_service.canonical_args("photo_paths")
def shift_date_taken(library, photo_paths, minutes, exiftool_path):
    """Move Date Taken in each photo by `minutes`, and tell the index. Time Shift.

    One change of photo files (tagpup.services.file_changes): every photo's dates read,
    each moved by the minutes (tagpup.core.dates.shifted), the plan committed, then each
    file written and its row recorded as it is marked done -- its new Date Taken, which
    orders photos and picks the era a face is compared against, and its new mtime and
    size, without which the next scan distrusts it. The change can be undone.

    `changed` is the files written: a photo that could not be read, or holds no date to
    move, is skipped; one that could not be written, or changed outside between the plan
    and its write, is an error. details: `records`, each photo as read back afterwards;
    `change`. Refused, and nothing written, for a shift that is not a whole number of
    minutes (tagpup.core.validation).
    """
    problem = validation.problem("time shift", minutes)
    if problem:
        result = Result(attempted=len(photo_paths))
        result.refuse(problem)
        return result
    refused = Result(attempted=len(photo_paths))
    # A photo found damaged refuses the whole shift, nothing written, as it refuses a rename.
    # Photos of folders the library does not hold are shifted in their files only
    # (tagpup.services.file_only), and are asked for what they decode as, not for records.
    held, loose = libraries.split(library, photo_paths)
    if held and libraries.refuse_writes(refused, library, held):
        return refused
    if loose and file_only.refuse_unwritable(refused, loose):
        return refused

    plan_one = date_shift_plan(minutes)

    try:
        journaled = file_changes.write_fields(
            library, "time shift", exiftool_path, held, dates.SHIFTED_FIELDS, plan_one,
            summary={"photos": len(held), "minutes": minutes}, unreadable="skip") if held else None
        files = file_only.write_fields(exiftool_path, loose, dates.SHIFTED_FIELDS, plan_one,
                                       unreadable="skip") if loose else None
        result = file_only.combined(journaled, files)
    except Exception as e:
        result = Result(attempted=len(photo_paths))
        result.fail("time shift", e)
        return result
    result.details.pop("written", None)
    # Only reading, for the page: minting a DocumentID here would write the files again.
    result.details["records"] = metadata.MetadataExtractor(exiftool_path=exiftool_path
                                                           ).batch_read(photo_paths,
                                                                        people=taxonomy.people_vocabulary(library.path))
    return result


@roots_service.canonical_args("photo_path")
def delete(library, photo_path):
    """Send a photo to the Recycle Bin, and forget it: its row, faces and cached
    embedding. Clicking Delete (Organize's, and each photo of a library view's bulk Delete).

    The file goes first. A photo that could not be moved stays in the library, and its
    rows with it.

    A photo of a folder the library does not hold (Just look) is sent to the Recycle Bin
    and nothing else happens: it has no row to forget (tagpup.services.file_only).

    Only an existing photo FILE is deleted, in a held folder or not: a folder, a .txt, a path
    that is not there or ends in a separator is refused, and nothing is moved (#520). A file on
    a network share, or a drive with no Recycle Bin, is never deleted for good (#694): it goes
    THROUGH THIS PC (tagpup.files.recycle_bin.delete_file) -- copied under the Downloads folder's
    "TagPup deleted from shares", the copy checked, the copy to this PC's Recycle Bin, then the
    original -- and any step that fails leaves it, and its rows, where they are.

    details: `removed`, the rows removed from each table (None for such a photo);
    `file_only` and `with_rows`, 1 for the way it was done; `through_this_pc`, `no_bin_reason`
    (why its place has no Bin, as a phrase) and `copy` (where its copy was put, and so where the
    Bin restores it).
    """
    result = Result(attempted=1)
    why = recycle_bin.problem(photo_path)
    if why:
        result.refuse(why)
        return result
    _held, loose = libraries.split(library, [photo_path])
    if loose:
        result = file_only.delete(photo_path)
        result.details.update({file_only.FILE_ONLY: result.changed, file_only.WITH_ROWS: 0})
        return result
    # A damaged photo may be deleted: nothing is written into it.
    if libraries.refuse_writes(result, library, [photo_path], damaged_ok=True):
        return result
    try:
        went = recycle_bin.delete_file(photo_path)
    except Exception as e:
        result.fail(photo_path, e)
        return result
    result.changed = 1
    # The thumbnail goes with the photo: its id is read first, while the row is there to name it.
    ids = thumbnails.ids_of(library, [photo_path])
    result.details["removed"] = photos.forget_photo(library.path, photo_path)
    thumbnails.forget(library, ids)
    result.details.update({file_only.FILE_ONLY: 0, file_only.WITH_ROWS: 1, "through_this_pc": went["through_this_pc"],
                           "no_bin_reason": went["reason"], "copy": went["copy"]})
    return result


@file_changes.exclusively()
@roots_service.canonical_args("photo_path")
def rotate(library, photo_path, direction, exiftool_path):
    """Turn a photo a quarter left or right. Clicking Rotate Left or Rotate Right.

    Only the file's Orientation changes; its pixels do not (tagpup.files.metadata.
    rotate_image_file). Face boxes are in the coordinates Pillow shows the photo in:
    for most formats the stored pixels, which do not move, so the boxes stay where they
    are. Pillow applies a TIFF's Orientation as it loads it, so a TIFF's boxes turn with
    the photo. The index row gets the file's new mtime and size, or the folder scan
    would distrust it and read the photo again.

    details: `mtime` and `size` of the file now (the pages version image URLs by mtime,
    because thumbnails are cached for a day), its `orientation`, and `faces_turned`.
    Refused, touching nothing, for a direction that is not a "rotate direction".
    """
    result = Result(attempted=1)
    refused = validation.problem("rotate direction", direction)
    if refused:
        result.refuse(refused)
        return result
    _held, loose = libraries.split(library, [photo_path])
    if loose:
        # A photo of a folder the library does not hold: its file only, but only if it decodes.
        if file_only.refuse_unwritable(result, [photo_path], full=True):
            return result
        turned = file_only.rotate(photo_path, direction, exiftool_path)
        turned.details.update({file_only.FILE_ONLY: turned.changed, file_only.WITH_ROWS: 0})
        return turned
    if libraries.refuse_writes(result, library, [photo_path]):
        return result
    # The row is stamped only if it described the file just before the turn (#249).
    before = embeddings.stamp_of(photo_path)
    try:
        width, height, oriented = images.shown_size(photo_path)
        orientation = metadata.rotate_image_file(photo_path, direction, exiftool_path)
    except Exception as e:
        result.fail(photo_path, e)
        return result
    result.changed = 1
    result.details["orientation"] = orientation
    result.details["faces_turned"] = (
        faces.turn_boxes(library.path, photo_path, direction, width, height) if oriented else 0)
    # The embedder applies the Orientation, so the photo's vectors describe the turn
    # before; they go, and are computed again.
    photos.record_file_stat(library.path, photo_path, looks_different=True, before=before)
    stat = os.stat(photo_path)
    result.details.update(mtime=stat.st_mtime, size=stat.st_size)
    result.details.update({file_only.FILE_ONLY: 0, file_only.WITH_ROWS: 1})
    return result


def count_photos(folder, recursive=True):
    """(how many photos are under `folder`, has it subfolders): what the folder picker
    shows beside each folder. `recursive` counts every depth; else the folder's own
    files only. What counts as a photo is tagpup.files.images' (docs/findings.md, #72)."""
    count, has_subdirs = 0, False
    try:
        if recursive:
            # The one walk (tagpup.files.images.photo_entries), which goes into no junction.
            count = sum(1 for _entry in images.photo_entries(folder))
            has_subdirs = images.has_subfolders(folder)
        else:
            for entry in os.listdir(folder):
                if os.path.isdir(os.path.join(folder, entry)):
                    has_subdirs = True
                elif images.is_photo(entry):
                    count += 1
    except OSError:
        pass
    return count, has_subdirs


def indexed_by_folder(library):
    """{paths.key of a folder: photos the library holds directly in it}, each folder of
    the library (tagpup.store.folders): one added and not read yet holds 0. Lets the
    folder picker show what is already in rather than offering it as if new; a library
    that cannot be read just now counts as holding nothing."""
    try:
        conn = db.connect(db.readonly_uri(library.path), uri=True)
    except Exception:
        return {}
    try:
        return {paths.key(folder): count for folder, count in store_folders.of(conn).listed()}
    except Exception:
        return {}
    finally:
        conn.close()


def indexed_folders(library):
    """The folders Remove Folder offers, by path: each the library holds photos directly
    in, and each folder above those up to (not including) its drive's root, which holds
    none of its own -- removing a folder takes every folder under it
    (store.photos.remove_under), so a parent removes a season in one step. Each is
    {"path" (as stored), "photos" (every photo under it: what removing it takes),
    "own_photos" (those directly in it; 0 for a folder above), "on_disk"}. TagTuner
    offers these rather than the disk's folders, so a folder deleted from disk can still
    be taken out (#47). The library's folders are tagpup.store.folders': a folder added
    and not read yet is listed, holding none, and can be removed."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        held = store_folders.of(conn).listed()
    finally:
        conn.close()
    # {key: [spelling, photos under it, photos directly in it]}
    listed = {paths.key(folder): [folder, 0, count] for folder, count in held}
    for folder, count in held:
        # Up through its ancestors, adding its photos to each; one not yet listed is
        # listed under the spelling of the first folder found below it.
        current = folder
        while True:
            parent = os.path.dirname(current)
            if parent == current:
                break   # a drive's root: never offered
            entry = listed.setdefault(paths.key(current), [current, 0, 0])
            entry[1] += count
            current = parent
    return [{"path": folder, "photos": under, "own_photos": own, "on_disk": os.path.isdir(folder)}
            for _key, (folder, under, own) in sorted(listed.items())]


def is_photo(path):
    """Does `path` name a photo, by its extension (tagpup.files.images)? The web layer
    asks before opening one on the desktop."""
    return images.is_photo(path)
