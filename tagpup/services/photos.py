"""Actions on photo files, and reading a folder of them for the page."""
import collections
import json
import logging
import os

from tagpup.core import dates, fields, paths, photo_meta, renaming, validation, vocabulary
from tagpup.core.result import Conflict, NotFound, Refused, Result, Unavailable
from tagpup.files import convertible, field_values, images, metadata, names, recycle_bin, shares, video_frames
from tagpup.services import batches, file_changes, file_only, libraries, rotation, thumbnails
from tagpup.services import roots as roots_service
from tagpup.store import db, faces, file_journal, generations, photos, taxonomy
from tagpup.store import journal as store_journal
from tagpup.store import roots as store_roots
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
    # 'photo' or 'video', as the index gives a row its kind (images.kind_of, photos.kind): what it is dated by as well.
    media = images.kind_of(path)
    year = meta["year"] if "year" in meta else dates.photo_year(raw_meta, path, media)
    gear = photo_meta.gear(raw_meta)
    record = {
        "path": path,
        "filename": os.path.basename(path),
        "tags": meta.get("tags", []),
        "people": meta.get("people", []),
        # The title by the one rule the save compares by (fields.title_of over what the file holds), never the
        # stored `captions`, whose order is the indexer's of the day: a row stored before the title came first showed
        # its Description while the save read its Title, and every save of it was refused as changed on disk (#2582).
        "title": fields.title_of(raw_meta),
        "mtime": mtime,
        "size": size,
        "year": dates.shown_year(None if year is None else str(year)),
        "taken": dates.date_taken(raw_meta, path, media),
        "raw_metadata": raw_meta,
        # The camera and lens it was taken with, named as a search finds them (photo_meta.gear): "" for what the
        # metadata does not say, which the panel shows as nothing.
        "camera": gear.camera or "",
        "lens": gear.lens or "",
        # A library card carries the kind as well: the page plays a video and draws it by its poster, never asks
        # /api/photo-file for it (media.js, grid.js).
        "media": media,
        # What a save may change of it, by name (field_values.writes): the page offers no other edit of it.
        "writes": list(field_values.writes(path)),
    }
    if meta.get("read_error"):
        # ExifTool could not read it: it shows as holding nothing, which a save must not take for what the file holds.
        record["unreadable"] = True
        record["read_error"] = meta["read_error"]
    return record


def file_stamp(path):
    """(mtime, size) of the file at `path` as it is now, or None when there is none, it cannot be read, or its share does
    not answer (tagpup.files.shares.known_stamp): the look at a file for the pages and the jobs, which may not import
    tagpup.files. With folder_state and subfolders, their one door to tagpup.files.shares."""
    return shares.known_stamp(path)


def folder_state(folder):
    """"there", "gone" or "unanswered" (a network share's that did not answer in time) of `folder` now
    (tagpup.files.shares.folder_state): the look at a folder for the pages and the jobs."""
    return shares.folder_state(folder)


def folder_refusal(folder, state=None):
    """None when `folder` is a folder now; else a Result refused with the one sentence of why, 503's for a share that did
    not answer (tagpup.files.shares.folder_refusal): the refusal of a folder for the pages, the jobs and the CLI."""
    return shares.folder_refusal(folder, state)


def file_refusal(path):
    """None when there is a file at `path` now; else a Result refused with the one sentence of why, 503's for a share
    that did not answer, never "not there" (tagpup.files.shares.file_refusal): the refusal of one photo for the routes."""
    return shares.file_refusal(path)


def subfolders(folder):
    """The names of the folders `folder` holds, in its listing's order (tagpup.files.shares.listing, waited for on a
    share); None when its share did not answer. Raises the listing's OSError. What the folder pickers list."""
    listed = shares.listing(folder)
    if listed is shares.UNANSWERED:
        return None
    return [entry.name for entry in listed if entry.is_dir()]


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
    return dates.date_taken_sort_key(record.get("raw_metadata", {}), record.get("path"), record["media"],
                                     record.get("mtime", 0.0))


@roots_service.canonical_args("folder")
def scan_stamp(library):
    """What the library's photos and tag tree were at this moment, to be taken BEFORE a scan and kept with it
    (tagpup.web.tagpup_routes.FolderCache): the photos and taxonomy counters that the triggers move for a write from any
    process -- this server, an index, a CLI `sync --apply` or undo, a snapshot restore, a tree edit (a file read
    from disk is told the tree's people) -- and that a move of a root moves (generations' salt), so a scan kept past one
    is read again. None when the library cannot be read just now: a scan is then kept as it was, and a scan of an
    unreadable library fails by itself."""
    try:
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            return generations.values(conn, ("photos", "taxonomy"))
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Could not read the library's generations for a folder scan: %s", e)
        return None


def scan_folder(library, folder, exiftool_path):
    """The photos under `folder`, at any depth, as page records keyed by paths.key:
    opening a folder in TagPup.

    A photo whose index row matches the file's mtime and size is taken from the row;
    the rest are read with ExifTool, in batches. A row with no stamp -- made for a
    photo Suggest saw but the index never read -- is read now (docs/findings.md, #94).
    The reader is told what the library says about people, as the indexer is; the
    server's scan used the default face roots alone.

    A clip is listed beside the photos (`media` 'video'). One the index has recorded carries its row's `id` and `thumb`,
    its poster by id (thumbnails.url, as a library card's): the only way a clip's picture is served, made by B1's bounded
    ffmpeg runs, so a folder of fifty clips is not fifty at once. One the index has not recorded has neither, and the
    page shows it a placeholder until the next open after the index records it (the photos counter moved: scan_stamp).
    """
    folder = paths.stored(folder)
    listed = images.stamps_under(folder)
    if not listed:
        return {}

    known = _rows_under(library, folder)
    found, to_read = {}, []
    for key, (file, mtime, size) in listed.items():   # the stamps the listing gave: no look at each file (#2104)
        row = known.get(key)
        if row and photos.describes(row["mtime"], row["size"], (mtime, size)):
            found[key] = page_record(row["path"], row, mtime, size)
        else:
            to_read.append((file, mtime, size))

    if to_read:
        logger.info("Scan found %d new/modified files in %s. Running ExifTool...", len(to_read), folder)
        try:
            found.update(_read(library, [(f, mt, sz) for f, mt, sz in to_read], exiftool_path))
        except Exception as e:
            logger.error("Error running ExifTool during scan: %s", e)
    return _with_posters(found, known)


def _rows_under(library, folder):
    """{paths.key: what the index holds of it} of each photo and clip under `folder` (photos.rows_under): what
    scan_folder trusts a row by, and a recorded clip's id. Empty, logged, when the library cannot be read: the folder
    is then read from its files, and a clip shows its placeholder."""
    known = {}
    try:
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            for path, mtime, size, tags, people, captions, raw_meta, photo_id, kind in photos.rows_under(conn, folder):
                known[paths.key(path)] = {
                    "path": paths.stored(path), "mtime": mtime, "size": size, "id": photo_id, "kind": kind,
                    "tags": json.loads(tags) if tags else [],
                    "people": json.loads(people) if people else [],
                    "captions": json.loads(captions) if captions else [],
                    "raw_metadata": json.loads(raw_meta) if raw_meta else {},
                }
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Failed to query index DB for folder scan cache: %s", e)
    return known


def _with_posters(found, known):
    """`found`, each clip the index has recorded (`known`, _rows_under) given its row's `id` and `thumb`, its poster by
    id: the one place a folder's clip gets the picture the page draws it by (scan_folder)."""
    for key, record in found.items():
        row = known.get(key)
        if row and row["kind"] == "video":
            # Its poster is stamped by the file as listed: the cache keys a poster by the file's stamp, not the row's.
            record["id"] = row["id"]
            record["thumb"] = thumbnails.url(row["id"], record["mtime"])
    return found


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
                file, meta, meta["mtime"] if mtime is None else mtime,
                meta["size"] if size is None else size)
    return found


def people_of(library, tags, photo_path):
    """Everyone in a photo, by its keywords and its named faces
    (tagpup.core.vocabulary.people_in_photo), for a page record just written."""
    return vocabulary.people_in_photo(tags, faces.face_refs(photo_path, db_path=library.path),
                                      taxonomy.people_vocabulary(library.path))


def record_written(library, record, path, tags, flat, hierarchical, dates_written=None):
    """Bring a page record up to date with the keywords just written to its photo: its
    tags, every keyword field of its raw metadata, and its people; and with `dates_written` ({field: value}, the save's
    `details["dates"]`), the date fields it wrote (fields.record_dates).

    Every keyword field, not just the XMP pair: the page re-derives tags from the raw
    metadata, and a stale IPTC:Keywords brought a removed tag straight back.
    """
    raw_meta = fields.record_keyword_fields(record.setdefault("raw_metadata", {}), flat, hierarchical)
    record["tags"] = list(tags)
    record["people"] = people_of(library, tags, path)
    if dates_written:
        fields.record_dates(raw_meta, dates_written)
    return raw_meta


def page_copy(photo_path, max_size=None, upright=True):
    """(bytes, content type) of a photo for a page: the file itself, or with `max_size`
    a JPEG no larger than that on a side -- turned upright by its Orientation when
    `upright`. A copy that cannot be made falls back to the file.

    Refused for a file the servers do not send: the path is the page's to name.
    """
    if not images.is_servable(photo_path):
        raise Refused("Forbidden: Invalid file type requested")
    if not shares.there(photo_path):
        raise NotFound("Photo file not found: %s" % photo_path)
    if max_size:
        try:
            return images.smaller_copy(photo_path, max_size, upright), "image/jpeg"
        except Exception as e:
            logger.warning("Could not make a smaller copy of %s: %s", photo_path, e)
    with open(photo_path, "rb") as f:
        return f.read(), images.content_type(photo_path)


#: The most bytes one answer to a range of a video sends (tagpup.web.responses.ranged): a browser playing a video asks
#: `bytes=N-` and asks again from where an answer ended, so a cap keeps one request from holding one of the server's
#: threads (waitress has 16) and its buffer for the length of a whole film.
VIDEO_SPAN = 4 * 1024 * 1024
#: The bytes read from a video's file at a time (shares.read_span), each read opening and closing the file.
VIDEO_READ = 1024 * 1024

#: A video as /api/video-file answers it: its native path, and the stamp (size, mtime) its bytes are read against.
Video = collections.namedtuple("Video", "path size mtime content_type")

#: Why a video's bytes are not read while a delete of its file is under way (recycle_bin.being_deleted): a 409.
BEING_DELETED = "This video is being deleted."


def video(library, photo_id):
    """The Video of photo `photo_id` of `library`. Raises NotFound -- with its sentence -- for an id the library has no
    photo of, one that is a photo and not a video (photos.kind), and one whose file is not there; Unavailable when the
    file is on a share that is away or refuses to be read (shares.reachable_stamp: a 503, try again);
    paths.RootsError for a video under a root this machine does not place; Conflict (BEING_DELETED) while a delete of its
    file is under way (recycle_bin.being_deleted), from the panel's Delete or a bulk Delete, in any library holding it."""
    row = None
    if 0 < photo_id < 2 ** 63:   # an id SQLite cannot hold names no photo: a sentence, not an overflow
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            row = photos.path_and_kind(conn, photo_id)
        finally:
            conn.close()
    if row is None or row[1] != "video":
        raise NotFound("There is no video %d in this library." % photo_id)
    path = row[0]
    if recycle_bin.being_deleted(path):
        raise Conflict(BEING_DELETED)
    stamp = shares.reachable_stamp(path, "video")
    if stamp is None:
        raise NotFound("The file of video %d is not there." % photo_id)
    return Video(path, stamp[1], stamp[0], images.content_type(path))


#: Seconds ffprobe may take to say what a clip holds (clip_plan): its header, read once; past it, killed with its tree.
PLAN_SECONDS = 20


def clip_plan(library, photo_id, find_ffmpeg):
    """What the clip `photo_id` of `library` needs before a browser plays it, as /api/clip/convert-plan answers it:
    {"action", "sentence"}, tagpup.files.convertible's plan of what ffprobe finds in it (video_frames.streams, one run
    within PLAN_SECONDS; `find_ffmpeg` is tagpup.config.locate_ffmpeg). Raises as `video` does (NotFound, Conflict,
    Unavailable, paths.RootsError); Unavailable with ffprobe's sentence when it could not be read just now (its share
    away, the deadline passed, ffmpeg gone); Refused with it when ffprobe cannot read the file as a video. Reads the
    file's header; writes nothing."""
    found = video(library, photo_id)
    tools = find_ffmpeg()
    if tools.problem:
        raise Unavailable(tools.problem)
    held = video_frames.streams(tools, found.path, PLAN_SECONDS)
    if held.problem:
        raise (Refused if held.whose == video_frames.THE_FILE else Unavailable)(held.problem)
    plan = convertible.plan_for(os.path.splitext(found.path)[1], held.video, held.audio)
    return {"action": plan.action, "sentence": plan.reason, "replaces": convertible.REPLACES[plan.action]}


def video_bytes(video, start, length):
    """The bytes of `video` from `start`, `length` of them, a VIDEO_READ at a time (shares.read_span, the file opened and
    closed for each): what an answer to a range streams. Ends early -- a short answer, which the browser asks again
    from where it stopped -- when the file changed since `video` was looked at, cannot be read, its share stopped
    answering, or a delete of it began (recycle_bin.being_deleted: the browser's next ask is refused), with one line in the
    log; never raises once the answer has begun."""
    end = start + length
    while start < end:
        if recycle_bin.being_deleted(video.path):
            logger.info("A video's answer ended at byte %d of %d: its file is being deleted", start, video.size)
            return
        try:
            piece = shares.read_span(video.path, start, min(VIDEO_READ, end - start), (video.mtime, video.size))
        except (OSError, Unavailable) as why:
            logger.info("A video's answer ended at byte %d of %d: %s", start, video.size, why)
            return
        if not piece:
            return
        yield piece
        start += len(piece)


#: How long a look at a photo's header waits on a network share, and how long a share that did not
#: answer is taken as away (tagpup.files.shares.bounded).
SHAPE_WAIT = 1.0
SHAPE_AWAY = 30.0


def box_shape(photo_path):
    """What a page needs to draw a face's box over the photo: {"size": [width, height] of the
    pixels the boxes are in (the file as stored; images.shown_shape), or None when the file
    cannot be read -- no size, no box, rather than a box on the wrong face (#787) --, "turn": the
    Orientation (orientation.boxes_turn) the boxes are turned by onto the picture as shown. Only the
    header is read, once (a TIFF is decoded, and its boxes turn with
    it: its turn is always 1). On a network share or drive the look is
    bounded (tagpup.files.shares.bounded): a share that does not answer in a second gives no size
    and no box, and is not asked again for a while, never a request stalled for as long as Windows
    waits (#836)."""
    none = {"size": None, "turn": 1}
    if shares.on_a_network_drive(photo_path):
        state, shape = shares.bounded(photo_path, lambda: images.shown_shape(photo_path), SHAPE_WAIT, SHAPE_AWAY)
    else:
        # A local disk is not a share that can stop answering: a slow look (a decoded TIFF, a disk
        # spinning up) must not mark the drive away for every photo on it (#842).
        try:
            state, shape = "ok", images.shown_shape(photo_path)
        except Exception as error:
            state, shape = "error", error
    if state != "ok":
        if state == "error":
            logger.info("No size for %s: %s", photo_path, shape)
        return none
    width, height, _oriented, turn = shape
    if width <= 0 or height <= 0:
        return none
    return {"size": [width, height], "turn": turn}


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
    if not photo_path or not shares.there(photo_path):
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
    can be undone. A photo holding one already is skipped; a held one that cannot be read is
    skipped too, as its row keeps its record. A Result, as write_fields'. A photo of a folder
    the library does not hold has the name written to its file only (tagpup.services.
    file_only): no journal change, no row; one ExifTool cannot read or write is an error, so
    Smart Rename renames none: its PreservedFileName is the only record of the name it had,
    and a photo left out keeps a name the numbering may give another (#523). `split` is (held, loose) as the caller decided it
    (libraries.split): Smart Rename decides once and hands it on, so the folder added in between
    cannot send the names one way and the renames the other (#524).

    A file-only rename has no journal: if it stops part-way, run Smart Rename on the folder again
    -- it regenerates the same names -- and a file left named tmp_rename_* is a photo waiting for
    its new name.
    """
    def plan_one(path, held):
        # A clip's is patched into its XMP, as its tags are (fields.CLIP_WRITES, "name"; field_values.write).
        if held.get(fields.PRESERVED_NAME):
            return file_changes.skip("keeps the name it had before it was first renamed")
        return file_changes.Plan(after={fields.PRESERVED_NAME: os.path.basename(path)})

    if split is None:
        held, loose = libraries.split(library, photo_paths)
    else:
        wanted = {paths.key(path) for path in photo_paths}
        held, loose = [[path for path in side if paths.key(path) in wanted] for side in split]
    journaled = file_changes.write_fields(
        library, "smart rename: original names", exiftool_path, held, list(_PRESERVE_READ), plan_one,
        summary={"photos": len(held)}, unreadable="skip") if held else None
    files = file_only.write_fields(exiftool_path, loose, list(_PRESERVE_READ), plan_one) if loose else None
    return file_only.combined(journaled, files)


#: What preserve_names reads and writes of each file: the name it had before its first rename.
_PRESERVE_READ = (fields.PRESERVED_NAME,)


def _renamable(result, photo_paths, held, loose):
    """The paths of `photo_paths` a Smart Rename may rename, in order: every photo, and a clip of a folder the library
    holds (`held`), whose name preserve_names keeps by the journaled patch of its XMP (file_changes.leave_out_clips
    admits a write reading _PRESERVE_READ). A clip of a folder it does not hold (`loose`) is left out, its error in
    `result` (images.CLIP_NOT_EDITED: file_only writes no clip, with no journal to settle a patch a crash cut short), and
    keeps its name, as a photo whose name could not be kept is not renamed."""
    kept = {paths.key(p) for p in file_changes.leave_out_clips(result, held, read=_PRESERVE_READ)}
    kept |= {paths.key(p) for p in file_changes.leave_out_clips(result, loose)}
    return [p for p in photo_paths if paths.key(p) in kept]


#: The details key of the keys of the clips a rename left out because their writer refused them (_rename_numbered).
LEFT_OUT = "left_out"


def _refused_clips(kept):
    """The keys of the clips preserve_names' Result `kept` failed because their writer refused them before a byte was
    written (field_values._clip_written raises Refused; file_changes.WRITER_REFUSED): left out of a Smart Rename, not
    the error that stops it. Only a clip's writer raises Refused per file (field_values._clip_written, and in_place when
    another program holds the clip open), so no filter on the path is needed."""
    return {paths.key(path) for path in kept.details.get(file_changes.WRITER_REFUSED, ())}


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
    kept is an error, and nothing is renamed -- but a clip its writer refused (not patchable, open in another program)
    is left out, its error, its number unused, and the others are renamed (_refused_clips).

    details: `updated_paths`, old -> new for every photo, those already so named
    included; `renamed`, those whose name changed; `moved_aside`, the files moved out
    of the way; `index_rows_moved`; `index_skipped`, the (old, new) pairs whose new name
    already had rows in the index, left as they were; `change`.

    The photos of a folder the library does not hold (Just look) are renamed, all together
    or not at all among themselves, with no journal change and no row to move
    (tagpup.services.file_only); the others as above. A failure of the second part leaves
    the first part renamed, and says so. `file_only` and `with_rows` count the photos
    renamed each way.

    Refused, and nothing read or renamed, for more photos than a write may name (file_changes.too_many) and
    for a grouping that may not be used (tagpup.core.validation). The grouping is used trimmed as the rules trim it
    (validation.trim): the rules allow blanks at its ends, and a trailing space -- from
    a caller that does not strip, or before a U+FEFF, which strip() leaves -- put a
    double space before every photo's number.
    """
    result = Result(attempted=len(photo_paths))
    # The cap of a write of photo files, asked before a caption is read (file_changes.too_many).
    problem = validation.problem("grouping", grouping) or file_changes.too_many(len(photo_paths))
    if problem:
        result.refuse(problem)
        return result
    width = len(str(len(photo_paths)))
    numbers = {path: str(index).zfill(width) for index, path in enumerate(photo_paths, start=1)}
    return _rename_numbered(library, result, photo_paths, validation.trim(grouping), rename_format, exiftool_path,
                            numbers, move_aside=True)


@roots_service.canonical_args("photo_path")
@file_changes.exclusively()
def smart_rename_one(library, photo_path, grouping, rename_format, exiftool_path):
    """Smart Rename of the one photo `photo_path` (the photo screen's button beside its file name): smart_rename's
    renaming, journal, index rows and undo, but the number is the one its name holds (renaming.parts_of: kept when the
    grouping changes, DECISIONS 2026-11-05), and a photo whose name holds none takes the next after the highest its
    folder's names give `grouping` ("1" when none does; as wide as that highest, "08" after "07"). Numbered in the
    lock of changes of photo files, so two at once never take one number.

    A file that already holds the new name is not moved aside, as a folder's rename moves it (the photos of that
    rename are renumbered together, this one alone): refused, and nothing written. Refused too for a grouping that may
    not be used, a folder not there or on a share that does not answer (shares.folder_refusal, SHARE_AWAY), and a photo
    no longer there, a clip of a folder the library does not hold (_renamable, its sentence) and a clip its writer
    refuses (images.CLIP_NOT_PATCHED, in_place.OPEN_ELSEWHERE: the clip keeps its name). The name already right
    is no change (`renamed` empty). details: smart_rename's, and `new_path`."""
    result = Result(attempted=1)
    problem = validation.problem("grouping", grouping)
    if problem:
        result.refuse(problem)
        return result
    if not _renamable(result, [photo_path], *libraries.split(library, [photo_path])):
        result.refuse(images.CLIP_NOT_EDITED)   # the one photo asked for: the request is refused, nothing is done
        return result
    folder = os.path.dirname(photo_path)
    refusal = shares.folder_refusal(folder)
    if refusal is None and not shares.there(photo_path):
        refusal = Result(attempted=1)
        refusal.refuse("%s is not in its folder any more: it was moved, renamed or deleted. Refresh the folder."
                       % os.path.basename(photo_path))
    grouping = validation.trim(grouping)
    number, refusal = (None, refusal) if refusal else _number_of(photo_path, grouping, rename_format)
    if refusal:
        result.refuse(refusal.refused, refusal.refused_kind)
        return result
    _rename_numbered(library, result, [photo_path], grouping, rename_format, exiftool_path, {photo_path: number},
                     move_aside=False)
    if result.details.pop(LEFT_OUT, None):
        # The one clip asked for is left out because its writer refused it (CLIP_NOT_PATCHED, in_place.OPEN_ELSEWHERE):
        # the request is refused, as a clip of a folder not held is, not an error of the server.
        why = result.errors[0][1]
        result.errors.clear()
        result.refuse(why)
        return result
    result.details["new_path"] = result.details.get("updated_paths", {}).get(photo_path, photo_path)
    return result


def _number_of(photo_path, grouping, rename_format):
    """(the number smart_rename_one gives the photo, None) or (None, a Result refused): the number its name holds, or
    the next after the highest its folder's names give `grouping` (compared as Windows compares names, case aside)."""
    own = renaming.parts_of(rename_format, os.path.splitext(os.path.basename(photo_path))[0])
    if own:
        return own[1], None
    folder = os.path.dirname(photo_path)
    entries = shares.listing(folder)
    if entries is shares.UNANSWERED:
        return None, shares.folder_refusal(folder, "unanswered")
    wanted = grouping.casefold()
    taken = [parts[1] for parts in (renaming.parts_of(rename_format, os.path.splitext(entry.name)[0])
                                    for entry in entries) if parts and parts[0].casefold() == wanted]
    if not taken:
        return "1", None
    highest = max(taken, key=int)
    return str(int(highest) + 1).zfill(len(highest)), None


def _rename_numbered(library, result, photo_paths, grouping, rename_format, exiftool_path, numbers, move_aside):
    """smart_rename's renaming of `photo_paths`, each to the number `numbers` gives it, into `result`. `move_aside`: a
    file outside them holding a new name is moved aside (names.aside_for); without it, the rename is refused."""
    held, loose = libraries.split(library, photo_paths)
    held_keys = {paths.key(p) for p in held}
    # A clip of a folder the library does not hold is not renamed (_renamable): its error, its number left unused as a
    # photo's no longer on disk is. Asked here, not left to the names kept: their failure would stop every rename.
    present = [p for p in _renamable(result, photo_paths, held, loose) if shares.there(p)]
    captions = names.read_for_renaming(exiftool_path, present)
    renames = {}
    for old_path in photo_paths:
        if old_path not in captions:
            continue
        base = renaming.file_base(rename_format, grouping, numbers[old_path], captions[old_path])
        renames[old_path] = os.path.join(os.path.dirname(old_path), base + os.path.splitext(old_path)[1])
    if not move_aside:
        refusal = _name_taken(renames)
        if refusal:
            result.refuse(refusal.refused, refusal.refused_kind)
            return result
    kept = preserve_names(library, present, exiftool_path, split=(held, loose))
    if kept.refused:
        result.refuse(kept.refused)
        return result
    # A clip whose writer refused it (its boxes not patchable, open in another program) keeps its name, its number left
    # unused, as a clip of a folder not held does: its error, and the others are renamed (no bytes of it written).
    left_out = _refused_clips(kept)
    for what, why in kept.errors:
        if paths.key(what) in left_out:
            result.fail(what, why)
    if left_out:
        result.details[LEFT_OUT] = sorted(left_out)
    errors = [(what, why) for what, why in kept.errors if paths.key(what) not in left_out]
    if errors:
        # Nothing is renamed, as when this write raised: a photo renamed without the
        # name it had is found again only by its identity.
        for what, why in errors:
            result.fail(what, "the name it had before its first rename could not be kept: %s" % why)
        return result
    renames = {old: new for old, new in renames.items() if paths.key(old) not in left_out}

    # Two parts: the photos of held folders, then the others. Photos stay in their own folder
    # and a folder is held or not, so the names of the parts never meet.
    held_renames = {old: new for old, new in renames.items() if paths.key(old) in held_keys}
    loose_renames = {old: new for old, new in renames.items() if paths.key(old) not in held_keys}
    done, moved_aside, skipped, moved, change, with_rows, files_only = {}, {}, [], 0, None, 0, 0
    aside_for = names.aside_for if move_aside else (lambda _renames: {})

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
            outcome = file_changes.rename(library, "smart rename", held_renames, aside_for(held_renames),
                                          exiftool_path, summary={"photos": len(held_renames)})
            done.update(outcome.done)
            moved_aside.update(outcome.moved_aside)
            skipped, moved, change = list(outcome.skipped or []), outcome.moved, outcome.change_id
            with_rows = sum(1 for old, new in outcome.done.items() if old != new)
        if loose_renames:
            stage = "files only"
            outcome = file_only.rename(loose_renames, aside_for(loose_renames))
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


def _name_taken(renames):
    """A Result refused when a file outside `renames` already holds one of their new names (or its share does not say),
    else None: smart_rename_one's check, made before anything is written. A rename onto the photo's own name in
    another case is no other file."""
    own = {paths.key(old) for old in renames}
    for new in renames.values():
        if paths.key(new) in own:
            continue
        stamp = shares.stamp_of(new)
        if stamp is None:
            continue
        if stamp is shares.UNANSWERED:
            return shares.folder_refusal(os.path.dirname(new), "unanswered")
        refusal = Result(attempted=1)
        refusal.refuse("Another file in the folder is already named %s: type another grouping, or rename that file "
                       "first. Nothing was renamed." % os.path.basename(new))
        return refusal
    return None


def date_shift_plan(minutes):
    """The `plan_one` of a Date Taken shift by `minutes` (tagpup.services.file_changes.write_fields): each date the photo
    holds is moved by the same minutes; one it does not hold is not made, and a photo holding none is skipped. The plan
    of Camera Time Shift (shift_date_taken); a date the shift would move out of range is left alone."""

    def plan_one(_path, held):
        after = {}
        for field in dates.SHIFTED_FIELDS:
            values = held.get(field) or []
            moved = dates.shifted(values[0], minutes) if len(values) == 1 else None
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
    `written`, the photos written (file_changes.write_fields'; a stopped shift's, those
    before the stop); `change`. Refused, and nothing written, for a shift that is not a
    whole number of minutes (tagpup.core.validation).
    """
    problem = validation.problem("time shift", minutes)
    if problem:
        result = Result(attempted=len(photo_paths))
        result.refuse(problem)
        return result
    refused = Result(attempted=len(photo_paths))
    # Photos of folders the library does not hold are shifted in their files only (tagpup.services.file_only).
    held, loose = libraries.split(library, photo_paths)

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
    # Only reading, for the page: minting a DocumentID here would write the files again.
    result.details["records"] = metadata.MetadataExtractor(exiftool_path=exiftool_path
                                                           ).batch_read(photo_paths,
                                                                        people=taxonomy.people_vocabulary(library.path))
    return result


@roots_service.canonical_args("photo_path")
def delete(library, photo_path, for_good=False, fresh=True):
    """Send a photo or a clip to the Recycle Bin, and forget it: its row, faces, cached
    embedding (a clip's frames with it) and thumbnail or poster. Clicking Delete in Organize's
    panel (a library view's bulk Delete is delete_in_chunks).

    The file goes first. A photo that could not be moved stays in the library, and its
    rows with it.

    A photo of a folder the library does not hold (Just look) is sent to the Recycle Bin
    and nothing else happens: it has no row to forget (tagpup.services.file_only).

    Only an existing photo or clip FILE is deleted, in a held folder or not: a folder, a .txt, a
    path that is not there or ends in a separator is refused, and nothing is moved (#520). A clip over
    recycle_bin.MOST_CLIP_BYTES is deleted for good, never copied, only with `for_good` (DECISIONS 2026-11-08, A5). Any other
    file on a network share, or a drive with no Recycle Bin, is never deleted for good (#694): it goes
    THROUGH THIS PC (tagpup.files.recycle_bin.delete_file) -- copied under the Downloads folder's
    "TagPup deleted from shares", the copy checked, the copy to this PC's Recycle Bin, then the
    original -- and any step that fails leaves it, and its rows, where they are. A file its own drive's Bin would not
    keep is deleted for good only with `for_good`, else refused untouched (file_only.send: details `not_kept`). The rows go
    only once the file is. `fresh` (recycle_bin.delete_file's): a Bin that looks full is asked again first.

    Under the lock of changes of photo files, the file judged in it: a Delete waits for a write under way, a write for
    it, and of two Deletes of one photo the second finds it gone and is refused. A held photo's Delete is one change of
    the journal (plan_deletes, file_journal.DELETE: internal, never listed nor undone; delete_planned): a stop between
    its file and its rows is finished by the next settle (settle_delete). A photo of a folder the library does not hold
    has no record: it has no row to forget.

    details: `removed`, the rows removed from each table (None for such a photo);
    `file_only` and `with_rows`, 1 for the way it was done; `through_this_pc`, `no_bin_reason`
    (why its place has no Bin, as a phrase), `copy` (where its copy was put, and so where the
    Bin restores it) and `for_good` (it was deleted for good, confirmed).
    """
    result = Result(attempted=1)
    with file_changes.exclusively():
        why = recycle_bin.problem(photo_path)
        if why:
            result.refuse(why)
            return result
        _held, loose = libraries.split(library, [photo_path])
        if loose:
            result = file_only.delete(photo_path, for_good, fresh)
            result.details.update({file_only.FILE_ONLY: result.changed, file_only.WITH_ROWS: 0})
            return result
        change_id, rows = plan_deletes(library, [(_photo_id_at(library, photo_path), photo_path, for_good, None)])
        try:
            delete_planned(library, rows, result, fresh)
        except Exception:
            end_deletes(library, change_id)
            raise
        end_deletes(library, change_id)
    if result.changed:
        result.details.update({file_only.FILE_ONLY: 0, file_only.WITH_ROWS: 1})
    return result


# ---- A Delete's record -----------------------------------------------------------------------

#: The steps of a Delete of a held photo, in order; `_reached` is told of each.
DELETE_STEPS = ("delete planned", "delete writing", "delete sent")

#: What a Delete whose share stopped answering at its Bin step is told (the route's error, the page's alert): its file is
#: left `writing`, and the next settle sends it once the share answers, if it is still the file the Delete stamped.
DELETED_WHEN_THE_SHARE_ANSWERS = ("The share did not answer: the photo is deleted when the share answers, unless another "
                                  "file has taken its place.")

#: What a Delete is told when the look at its file before the Bin step gave no answer (a share away, a file held open).
NOTHING_DELETED_NO_LOOK = "The share did not answer: nothing was deleted. Try again when it does."

#: What each photo a bulk Delete had not reached is told when the owner stopped it (Cancel): taken out, nothing deleted.
STOPPED_NOT_DELETED = "stopped as asked: not deleted"

#: What each is told when an update stopped it (Batch.for_an_update): left for the next start, which deletes it
#: (settle_delete).
DELETED_WHEN_TAGPUP_STARTS = "stopped for an update: it is deleted when TagPup starts again"

#: The key of a Delete change's summary that says how many of its photos a bulk Delete stopped by an update left `planned`
#: for the next start (end_deletes), which settle_delete deletes instead of withdrawing them. Never the change's owner: a
#: settle put aside releases a change a dead process planned as a clean stop does (#3256). Taken off before the first of them
#: is begun, so that a stop that is not clean from then on leaves the rest to be withdrawn (the owner's answer 4).
LEFT_FOR_THE_NEXT_START = "left for the next start"


def _reached(step):
    """A step of a Delete is done (DELETE_STEPS). Nothing happens here; the tests stop a Delete at each by patching it."""


def _photo_id_at(library, photo_path):
    """The id of the row of `photo_path` in `library`, or None."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        found = store_roots.photo_ids_at(conn, [photo_path])
    finally:
        conn.close()
    return found[0] if found else None


def plan_deletes(library, deletes):
    """Commit ONE change of file_journal.DELETE, owned by this process: a row `planned` for each of `deletes`, (photo id or
    None, path, for_good, look) -- a photo of a folder the library holds, named by the id of its row (the id its thumbnail
    goes by); `for_good` in its fields_after, for a settle that sends it again; and `look`, the stamp the caller's own look
    at the file found (shares.stamp_of, None for none), in its fields_before: the file the Delete is of, which delete_planned
    marks `writing` with instead of looking again, and which a resume after a clean stop deletes only while it is there
    (settle_delete). Returns (change id, [FileRow]). The caller holds the lock of changes of photo files, and ends the change
    (end_deletes)."""
    files = [{"photo_id": photo_id, "path": path, "after": {"for_good": bool(for_good)},
              "before": {"stamp": list(look)} if look else {}} for photo_id, path, for_good, look in deletes]
    planned = file_journal.plan(library.path, file_journal.DELETE, files, summary={"photos": len(files)})
    _reached("delete planned")
    return planned


def _look_of(row):
    """The stamp of the file the Delete `row` is of, as its plan recorded it (plan_deletes), or None."""
    return tuple(row.before["stamp"]) if row.before.get("stamp") else None


def delete_planned(library, rows, result, fresh=True):
    """Carry out the Deletes `rows` (plan_deletes), one at a time: each marked `writing` with the stamp of the file at its
    path (its plan's look, else shares.stamp_of now; with no stamp it is taken out, nothing sent and nothing promised, as a
    settle could not tell it from another file put at its path), sent to the Recycle Bin (file_only.send, `fresh` its),
    then forgotten (_forget), its rows removed in the transaction that marks it done. One the Bin step did not send is
    settled by what is at its path (_by_look): gone, forgotten; there, taken out of the change, nothing deleted; on a share
    that does not answer, left `writing` for a settle. `result` is told of each. The caller holds the lock of changes of
    photo files, and ends the change (end_deletes)."""
    for row in rows:
        stamp = _look_of(row) or shares.stamp_of(row.path)
        if stamp is shares.UNANSWERED or stamp is shares.CANNOT_READ:
            # No stamp, so a settle could not tell this file from another put at its path: nothing is sent, nothing is promised.
            file_journal.withdraw(library.path, [row.id])
            result.fail(row.path, NOTHING_DELETED_NO_LOOK)
            continue
        file_journal.writing(library.path, row.id, stamp if isinstance(stamp, tuple) else None)
        _reached("delete writing")
        if file_only.send(row.path, row.after.get("for_good", False), result, fresh) is None:
            _by_look(library, row, result, redo=False)
            continue
        _reached("delete sent")
        _forget(library, row, result)


def delete_in_chunks(library, deletes, told):
    """Delete `deletes` (plan_deletes's: a library view's bulk Delete, tagpup.services.bulk_edit) as ONE change, a
    batches.CHUNK of photos at a time -- a clip a chunk of its own, its copy through this PC can take minutes --, the lock
    of changes of photo files let go between chunks and a stop asked taken there (file_changes.between_chunks, of the batch
    this thread carries out). `told(row, result)` after each photo, with its Result.

    Asked to stop by the owner (Cancel): the photos not reached are taken out of the change, nothing deleted, each told
    STOPPED_NOT_DELETED. By an update (Batch.for_an_update; a Cancel in the same chunk is the owner's): they are left `planned`, the change released with how many in its
    summary (LEFT_FOR_THE_NEXT_START), each told DELETED_WHEN_TAGPUP_STARTS, and the next start deletes them (settle_delete).
    The caller holds the lock."""
    change_id, rows = plan_deletes(library, deletes)
    batch, left = batches.current(), 0
    try:
        for n, row in enumerate(rows):
            turn = n and (n % batches.CHUNK == 0 or images.is_clip(rows[n - 1].path) or images.is_clip(row.path))
            if turn and not file_changes.between_chunks(batch):
                left = len(rows) - n if batch.for_an_update else 0
                for rest in rows[n:]:
                    stopped = Result(attempted=1)
                    stopped.refuse(DELETED_WHEN_TAGPUP_STARTS if left else STOPPED_NOT_DELETED)
                    told(rest, stopped)
                break
            result = Result(attempted=1)
            delete_planned(library, [row], result, fresh=False)
            told(row, result)
    finally:
        end_deletes(library, change_id, left)


def _forget(library, row, result):
    """The photo of the Delete `row` has left its folder: its rows go (store.photos.forget_photo) in the transaction
    that marks `row` done, then its thumbnail (by the id the plan read while the row was there). `result` counts it."""
    result.details["removed"] = photos.forget_photo(
        library.path, row.path, also=lambda conn: file_journal.set_state(conn, row.id, "done"))
    thumbnails.forget(library, [row.photo_id] if row.photo_id is not None else [])
    result.changed += 1


def _by_look(library, row, result, redo):
    """Settle the Delete `row`, `writing`, by what is at its path now (shares.stamp_of). Gone: its rows go (_forget).
    On a share that does not answer: left `writing`, never decided on, `result` failed with DELETED_WHEN_THE_SHARE_ANSWERS,
    and False. There: with `redo` (a settle: a stop
    came before the Bin step or between the Bin's copy and the original's removal) it is sent to the Bin again and
    forgotten -- only while it is the file the Delete stamped (`row.stamp`): another file put at its path meanwhile is
    taken out of the change, nothing deleted, its row left as it is, and skipped in `result`, as _settle_files holds out
    a file holding another's write (#1937) --, else -- the Bin step failed and said why -- it is taken out of the change,
    nothing deleted. True when the row is settled."""
    stamp = shares.stamp_of(row.path)
    if stamp is shares.UNANSWERED:
        result.fail(row.path, DELETED_WHEN_THE_SHARE_ANSWERS)
        return False
    if stamp is None:
        _forget(library, row, result)
        return True
    if redo and stamp != row.stamp:
        file_journal.withdraw(library.path, [row.id])
        result.skip(row.path, "another file is at its path")
        return True
    if redo:
        if file_only.send(row.path, row.after.get("for_good", False), result) is not None:
            _forget(library, row, result)
            return True
        return _by_look(library, row, result, redo=False)
    file_journal.withdraw(library.path, [row.id])
    return True


def end_deletes(library, change_id, left=0):
    """The Deletes of change `change_id` end, done or stopped: each file still `planned` is taken out of it, nothing
    deleted, and the change finishes, erased (file_journal.INTERNAL), or, a file left `writing`, is released for the
    next settle. `left`: so many files still `planned` were left by an update's stop (delete_in_chunks): they stay, the
    change says how many (LEFT_FOR_THE_NEXT_START), and it is released for the next start to delete them."""
    if left:
        _left_for_the_next_start(library, change_id, left)
        logger.info("%s: change %d (%s): %d photo(s) of a Delete stopped for an update are deleted when TagPup starts "
                    "again", library.path, change_id, file_journal.DELETE, left)
        file_journal.release(library.path, change_id)
        return
    file_journal.withdraw(library.path, [row.id for row in file_journal.files_of(library.path, change_id)
                                         if row.state == "planned"])
    if file_journal.finish(library.path, change_id) is None:
        file_journal.release(library.path, change_id)


def _left_for_the_next_start(library, change_id, count):
    """Add `count` (taken off when negative) to how many photos change `change_id` leaves for the next start."""
    db.write_with_connection(
        library.path, lambda conn: store_journal.add_counts(conn, change_id, {LEFT_FOR_THE_NEXT_START: count}),
        label="change %d: photos left for the next start" % change_id)


def settle_delete(library, change):
    """Settle a Delete `change` a stop left (file_changes._finish routes it here). A file still `planned` was never
    begun. Left by an update's stop (the change's LEFT_FOR_THE_NEXT_START, never its `owner`: a settle put aside releases
    one a dead process planned, as a clean stop would, #3256), it is deleted now, a line saying how many -- the count taken
    off first, so a stop that is not clean from then on leaves the rest to be withdrawn; while this process is itself
    handing over (batches.closing) the change is left as it is, and one an update stops part-way is left again with the
    rest. Else it is never begun now (the owner's answer 4): it is taken out, nothing deleted, and a warning names the
    change and how many, no path. Each file `writing` is settled by a look (_by_look, redo): gone, its rows go; there and
    the file it stamped, it is sent to the Bin; another file at its path, taken out, nothing deleted, with a warning (the
    change, a count, no path); on a share that does not answer, left for the next settle. Each resumed is looked at once
    (_resume): deleted (delete_planned) only while it is the file its plan looked at; anything else is taken out, nothing
    deleted, its rows and faces kept -- it was never begun, so a file gone (removed, its folder renamed, its drive not
    connected) is not the Delete's doing --, with a warning. True when the change is finished."""
    left = change.summary.get(LEFT_FOR_THE_NEXT_START, 0)
    if left and batches.closing():
        return False
    rows = file_journal.files_of(library.path, change.id)
    planned = [row for row in rows if row.state == "planned"]
    if left:
        _left_for_the_next_start(library, change.id, -left)
        logger.info("%s: change %d (%s): deleting the %d photo(s) a Delete stopped for an update left for this start",
                    library.path, change.id, change.operation, len(planned))
    elif planned:
        logger.warning("%s: change %d (%s): %d photo(s) were still to be deleted when TagPup stopped; they were not "
                       "deleted", library.path, change.id, change.operation, len(planned))
        file_journal.withdraw(library.path, [row.id for row in planned])
        planned = []
    result, finished, kept = Result(attempted=len(rows)), True, {NOT_FOUND: 0, NO_ANSWER: 0}
    with file_changes.exclusively():
        for row in rows:
            if row.state == "writing":
                _by_look(library, row, result, redo=True)
        for n, row in enumerate(planned):
            if batches.closing():
                end_deletes(library, change.id, len(planned) - n)
                finished = False
                break
            _resume(library, row, result, kept)
    for why, count in kept.items():
        if count:
            logger.warning("%s: change %d (%s): %d photo(s) it was to delete %s; they were not deleted and keep their rows",
                           library.path, change.id, change.operation, count, why)
    if result.skipped:
        logger.warning("%s: change %d (%s): %d photo(s) it was deleting had another file put at their path; that file "
                       "was not deleted", library.path, change.id, change.operation, len(result.skipped))
    if not result.ok:
        logger.warning("%s: change %d (%s): %s", library.path, change.id, change.operation, result.message())
    return finished and file_journal.finish(library.path, change.id) is not None


#: Why a photo an update's stop left for this start was taken out of its Delete (settle_delete's warning).
NOT_FOUND = "were not found or could not be read"
NO_ANSWER = "were on a share that did not answer"


def _resume(library, row, result, kept):
    """Delete the photo of `row`, left `planned` by an update's stop, by one look at its file now: the file its plan looked
    at, deleted (delete_planned, with that look); another file at its path, taken out and skipped in `result`; none there,
    one that cannot be read, or one planned with no look, taken out and counted in kept[NOT_FOUND]; on a share that does not
    answer, taken out too (the change no longer counts it for another start) and counted in kept[NO_ANSWER]. A row taken
    out keeps the photo's row and faces."""
    look, now = _look_of(row), shares.stamp_of(row.path)
    if look is not None and now == look:
        delete_planned(library, [row], result)
        return
    file_journal.withdraw(library.path, [row.id])
    if now is shares.UNANSWERED:
        kept[NO_ANSWER] += 1
    elif look is None or not isinstance(now, tuple):
        kept[NOT_FOUND] += 1
    else:
        result.skip(row.path, "another file is at its path")


def rotate_plan(direction):
    """The `plan_one` of a quarter turn or a mirror (tagpup.services.file_changes.write_fields): the Orientation the photo
    holds, turned, composed with the one already there (a phone photo stored sideways, 6, turned left is 1, not 8;
    mirrored, 5); one it holds none of is taken for 1. `detail` is what a turn reports of the photo."""

    def plan_one(_path, held):
        new = rotation.next_orientation(direction, held.get(rotation.FIELD))
        return file_changes.Plan(after={rotation.FIELD: [str(new)]}, detail={"orientation": new, "faces_turned": 0})
    return plan_one


@roots_service.canonical_args("photo_paths")
def rotate_photos(library, photo_paths, direction, exiftool_path):
    """Turn photos a quarter left or right, or mirror them left to right ("mirror"), as ONE journaled change "rotate" (tagpup.services.file_changes.write_fields):
    each photo's Orientation planned from what the file holds, written, and its row recorded as it is marked done -- with
    the face boxes the turn moves and the vectors it makes stale (tagpup.services.rotation), in the same transaction. A crash
    between the file and its rows is settled forward at the next start; History's Undo turns the files and the boxes back.
    More than batches.CHUNK photos are a batch like any other (claim, chunks, Cancel, progress), over batches.MAX_BATCH refused.

    Only the file's Orientation changes; its pixels do not (tagpup.services.rotation.FIELD). Face boxes are in
    the coordinates Pillow shows the photo in: for most formats the stored pixels, which do not move, so the boxes stay where
    they are. Pillow applies a TIFF's Orientation as it loads it, so a TIFF's boxes turn with the photo. The index row gets
    the file's new mtime and size (only when it described the file just before, #249), or the folder scan would distrust it.

    A photo of a folder the library does not hold (Just look) is turned by the same plan in its file only, unjournaled
    (file_only.write_fields). A file
    ExifTool cannot write is that photo's error (DECISIONS 2026-10-15). details: `written`
    {path: {"orientation", "faces_turned"}} and `change`, as write_fields gives them, `file_only` and `with_rows`. Refused,
    touching nothing, for a direction that is not a "rotate direction".
    """
    result = Result(attempted=len(photo_paths))
    refused = validation.problem("rotate direction", direction)
    if refused:
        result.refuse(refused)
        return result
    held, loose = libraries.split(library, photo_paths)
    try:
        journaled = file_changes.write_fields(
            library, "rotate", exiftool_path, held, [rotation.FIELD], rotate_plan(direction),
            summary={"photos": len(held), "direction": direction}) if held else None
        files = file_only.write_fields(exiftool_path, loose, [rotation.FIELD], rotate_plan(direction)) if loose else None
        return file_only.combined(journaled, files)
    except Exception as e:
        result = Result(attempted=len(photo_paths))
        result.fail("rotate", e)
        return result


def rotate(library, photo_path, direction, exiftool_path):
    """Turn a photo a quarter left or right, or mirror it. Clicking Rotate Left, Rotate Right or Mirror: rotate_photos of one
    photo.

    details: `mtime` and `size` of the file now (the pages version image URLs by mtime, because thumbnails are cached for a
    day), its `orientation`, `faces_turned`, and `change` (History's id of the turn; None for a photo of a folder the library
    does not hold). A photo left alone is an error here, not a quiet nothing.
    """
    result = rotate_photos(library, [photo_path], direction, exiftool_path)
    if result.refused or result.errors:
        return result
    turned = next((detail for key, detail in result.details.get("written", {}).items()
                   if paths.same(key, photo_path)), None) if result.changed else None
    if turned is None:
        result.fail(photo_path, "; ".join(why for _what, why in result.skipped) or "it was not turned")
        return result
    mtime, size = shares.known_stamp(photo_path) or (None, None)
    result.details.update(turned)
    result.details.update(mtime=mtime, size=size)
    return result


def count_photos(folder, recursive=True):
    """(how many photos and clips are under `folder`, has it subfolders): what the folder picker
    shows beside each folder, what indexing it would add. `recursive` counts every depth; else the folder's own
    files only. What counts as a photo is tagpup.files.images' (docs/findings.md, #72)."""
    count, has_subdirs = 0, False
    try:
        if recursive:
            # The one walk (tagpup.files.images.photo_entries), which goes into no junction.
            count = sum(1 for _entry in images.photo_entries(folder))
            has_subdirs = images.has_subfolders(folder)
        else:
            listed = shares.listing(folder)
            for entry in () if listed is shares.UNANSWERED else listed:
                if entry.is_dir():
                    has_subdirs = True
                elif images.is_media(entry.name):
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
    "own_photos" (those directly in it; 0 for a folder above)}. Manage Folders offers
    these rather than the disk's folders, so a folder deleted from disk can still be taken
    out (#47). The library's folders are tagpup.store.folders': a folder added and not read
    yet is listed, holding none, and can be removed. Whether each is on disk is asked
    apart, of the folders a page shows (folder_states): looking at all of them took 22 s
    for photo_index's 2,660, nearly all on a share (2026-11-19)."""
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
    return [{"path": folder, "photos": under, "own_photos": own}
            for _key, (folder, under, own) in sorted(listed.items())]


#: The most folders one folder_states asks about: a page asks of the rows it shows.
FOLDER_STATES_MOST = 100


def folder_states(folders):
    """[{"path", "state"}] of each of `folders`, in order: "there", "gone" or "unanswered" (a share that did not
    answer), tagpup.files.shares.folder_state's words. Each look is bounded on a share."""
    return [{"path": folder, "state": shares.folder_state(folder)} for folder in folders]


def is_photo(path):
    """Does `path` name a photo, by its extension (tagpup.files.images)? The web layer
    asks before opening one on the desktop."""
    return images.is_photo(path)
