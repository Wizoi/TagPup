"""Writes that change photo files and nothing of the library's: Just look in TagPup.

A photo in a folder the library does not hold has no row, and a write must not make one
-- a row makes its folder the library's, sync keeps it in step, the watcher watches it
and its tags grow the library's tag tree (the 2026-09-28 bug, tagpup.store.photos.
ensure_row). So the writes that need no database -- a caption, a tag, a rename, a turn, a
date, a delete -- are made here for such a photo, and write only its file:

- no photo row is made or touched, no journal change is planned, nothing is derived
  (people, dates, embeddings, faces, thumbnails), no damaged-photo record is made, and the
  folder is not registered with sync or the watcher. The library's database is not opened
  for writing at all; tests dump every table before and after;
- a photo whose picture does not decode, or may be an incomplete copy, is still never
  written into, though nothing is recorded of it (unwritable): the library's records of
  damaged photos are for the photos it holds;
- what is reported is what was written: a file counted changed is one read back holding
  what was written, as the journaled write counts it.

What the journal gave a write and this does not: the way back (History's Undo) and the
settling of a write a crash cut short. The page's own one-step Undo writes the earlier
values again, and so works; a process killed between two files leaves those files
written and the rest not, which a second run of the same edit finishes; a Smart Rename
killed between its two passes leaves files under a `tmp_rename_` name beside the photos
(the name each held is kept in its PreservedFileName).

The caller decides which photos these are (tagpup.services.libraries.split), per photo,
at the time of the write, from the library's own answer.
"""
import logging
import os

from tagpup.core import fields, paths
from tagpup.core.result import DAMAGED_PHOTOS, Result
# Looked up at call time, as exiftool_session.ExifToolSession, so a test standing in for
# ExifTool there reaches this too.
from tagpup.files import exiftool_session, field_values, images, lock_owners, metadata, names, recycle_bin
from tagpup.services import file_changes

logger = logging.getLogger(__name__)

#: What a Result's details count of the photos a write made to files only, and of those
#: it wrote with their rows: how many files each, as written.
FILE_ONLY = "file_only"
WITH_ROWS = "with_rows"

INCOMPLETE_REASON = ("possibly an incomplete copy: the file ends in zero bytes, as an interrupted copy leaves it, "
                     "and the picture may be grey below a line")


# ---- Which photos may be written into -----------------------------------------------------

def unwritable(photo_paths, full=False):
    """[(path, what is wrong)] of the photos of `photo_paths` nothing may be written into:
    the file is cut short, empty, or ends in zeros as an interrupted copy leaves it ("was found
    damaged -- <how> --"), or cannot be read ("could not be read -- <why> --"). The end of each
    file is read (images.tail_check) and ExifTool's own failure to read it, which fails the write,
    is the other signal; with `full` -- a rotate, which rewrites picture data -- the whole picture
    is decoded as the indexer decodes it (images.zero_tail_if_whole). No record is read or made. A
    share that does not answer is a photo that could not be read, and not written."""
    found = []
    check = images.zero_tail_if_whole if full else images.tail_check
    for photo_path in photo_paths:
        try:
            zeros = check(photo_path)
        except images.Unreadable as damage:
            found.append((photo_path, "was found damaged -- %s --" % images.DAMAGE.get(damage.kind, damage.kind)))
            continue
        except OSError as e:
            found.append((photo_path, "could not be read -- %s --" % (e.strerror or type(e).__name__)))
            continue
        if zeros >= images.ZERO_TAIL:
            found.append((photo_path, "was found damaged -- %s --" % INCOMPLETE_REASON))
    return found


def refuse_unwritable(result, photo_paths, full=False):
    """Refuse `result` -- a write of files, nothing written yet -- when a photo of `photo_paths`
    is unwritable. Returns True when refused. details[DAMAGED_PHOTOS] holds them, answered 409
    as the journaled writes' refusal is."""
    found = unwritable(photo_paths, full)
    if not found:
        return False
    more = "" if len(found) == 1 else " (and %d more)" % (len(found) - 1)
    result.refuse("%s%s %s and nothing is written to it. Restore it from a backup, then try again."
                  % (os.path.basename(found[0][0]), more, found[0][1]))
    result.details[DAMAGED_PHOTOS] = [path for path, _why in found]
    return True


def leave_out_unwritable(photo_paths):
    """(`photo_paths` but the unwritable ones, [(path, why)] of those left out): a bulk write
    skips them and writes the rest (libraries.leave_out_damaged does for the photos a
    library holds)."""
    found = dict(unwritable(photo_paths))
    return ([p for p in photo_paths if p not in found],
            [(path, "damaged, nothing is written to it: %s" % why) for path, why in found.items()])


# ---- Combining a write of held photos with one of the others -----------------------------

def combined(held, loose):
    """One Result for a write made in two parts: `held`, the photos of folders the library
    holds, written as always, and `loose`, the others, written to files only. Counts add;
    details keep `held`'s with the files each wrote under them -- `written` and `read_back`
    joined -- and FILE_ONLY and WITH_ROWS say how many files each part wrote. Either may be
    None."""
    if loose is None and held is None:
        return Result(details={WITH_ROWS: 0, FILE_ONLY: 0, "written": {}})
    if loose is None:
        held.details[WITH_ROWS] = held.changed
        held.details[FILE_ONLY] = 0
        return held
    if held is None:
        loose.details[WITH_ROWS] = 0
        loose.details[FILE_ONLY] = loose.changed
        return loose
    out = Result(attempted=held.attempted + loose.attempted, changed=held.changed + loose.changed,
                 skipped=held.skipped + loose.skipped, errors=held.errors + loose.errors,
                 refused=held.refused or loose.refused)
    out.details = dict(held.details)
    for name in ("written", "read_back"):
        if name in held.details or name in loose.details:
            out.details[name] = {**held.details.get(name, {}), **loose.details.get(name, {})}
    if "conflicts" in held.details or "conflicts" in loose.details:
        out.details["conflicts"] = list(held.details.get("conflicts", [])) + list(loose.details.get("conflicts", []))
    out.details[WITH_ROWS] = held.changed
    out.details[FILE_ONLY] = loose.changed
    if "skipped_damaged" in held.details or "skipped_damaged" in loose.details:
        out.details["skipped_damaged"] = held.details.get("skipped_damaged", 0) + loose.details.get(
            "skipped_damaged", 0)
    return out


# ---- Fields ---------------------------------------------------------------------------------

@file_changes.exclusively()
def write_fields(exiftool_path, photo_paths, read, plan_one, unreadable="fail", stop_at_first_error=False,
                 et=None, held=None, read_back_also=()):
    """file_changes.write_fields for photos the library does not hold: the same planning
    (`read` the fields to read of each file, `plan_one(path, held)` a Plan from what it
    holds, `unreadable`, `stop_at_first_error`, an `et` session and what the caller `held`
    of the files in it, `read_back_also`) and the same Result -- `changed` the files
    written, errors the photos that failed, details `written` {path: detail} of each written
    or already holding it, `conflicts`, `read_back` -- but only the files are written: no
    journal change (details["change"] is None), no row, nothing after."""
    if et is None:
        with exiftool_session.ExifToolSession(executable=exiftool_path) as session:
            return _write_fields(session, photo_paths, read, plan_one, unreadable, stop_at_first_error, held,
                                 read_back_also)
    return _write_fields(et, photo_paths, read, plan_one, unreadable, stop_at_first_error, held, read_back_also)


def _write_fields(et, photo_paths, read, plan_one, unreadable, stop_at_first_error, fresh, read_back_also):
    result = Result(attempted=len(photo_paths))
    written = result.details["written"] = {}
    result.details.update(change=None, conflicts=[], read_back={})
    stored = [paths.stored(p) for p in photo_paths]
    held = fresh if fresh is not None else field_values.read(et, stored, read)
    planned = []
    for path in stored:
        now = held.get(paths.key(path))
        if now is None or isinstance(now, Exception):
            why = now or "ExifTool answered nothing for it"
            if unreadable == "skip":
                result.skip(path, why)
                continue
            result.fail(path, why)
            if stop_at_first_error:
                break
            continue
        try:
            plan = plan_one(path, now)
        except Exception as e:
            result.fail(path, e)
            if stop_at_first_error:
                break
            continue
        if plan.skip:
            result.skip(path, plan.skip)
            continue
        after = {field: fields.field_values(value) for field, value in plan.after.items()}
        before = {field: now.get(field, []) for field in after}
        if fields.reads_same(before, after):
            written[path] = plan.detail
            continue
        planned.append((path, before, after, plan.detail))
    wrote = []
    for path, before, after, detail in planned:
        failure = _write_one(et, path, before, after, fresh is not None, result)
        if failure:
            result.fail(path, failure)
            if stop_at_first_error:
                break
            continue
        result.changed += 1
        written[path] = detail
        wrote.append((path, before, after))
    _read_back(et, wrote, read_back_also, result)
    logger.info("Wrote %d file(s) only, no row of any library (%d failed)", result.changed, len(result.errors))
    return result


def _write_one(et, path, before, after, just_read, result):
    """Bring one file from `before` to `after`. None when it was, else why it was not; a file
    that changed outside since it was read is never overwritten."""
    if not just_read:
        try:
            now = field_values.read_one(et, path, list(after))
        except field_values.Unreadable as e:
            return "could not be read: %s" % e
        if not fields.reads_same(now, before):
            result.details["conflicts"].append(path)
            return ("changed since it was read: it holds neither what the edit found nor what it was to leave,"
                    " and is not overwritten")
    try:
        field_values.write(et, path, after)
    except Exception as error:
        # Settled by what the file holds after it, as the journaled write settles it.
        try:
            now = field_values.read_one(et, path, list(after))
        except field_values.Unreadable:
            return lock_owners.explain(error, path)
        if fields.reads_same(now, after):
            return None
        error = lock_owners.explain(error, path)   # after a failure only: who holds the file, if it is held
        if fields.reads_same(now, before):
            return error
        result.details["conflicts"].append(path)
        return "a write failed (%s) and left it holding neither what it held nor what it was to hold" % error
    return None


def _read_back(et, wrote, also, result):
    """Read the files written back, once. One that holds what it held before, though ExifTool
    said it wrote it, changed nothing: not counted (the journaled write's #276). A value
    ExifTool keeps differently from how it was written is the file's: nothing is recorded
    here that could disagree. Never raises: the files are written either way."""
    if not wrote:
        return
    try:
        held = field_values.read(et, [path for path, _b, _a in wrote],
                                 sorted({field for _p, _b, after in wrote for field in after}), also,
                                 result.details["read_back"])
    except Exception as e:
        logger.warning("Could not read back %d file(s) written: %s", len(wrote), e)
        return
    for path, before, after in wrote:
        now = held.get(paths.key(path))
        if now is None or isinstance(now, Exception):
            continue
        kept = {field: now.get(field, []) for field in after}
        if fields.reads_same(kept, before) and not fields.reads_same(kept, after):
            result.changed -= 1
            result.details["written"].pop(path, None)
            result.fail(path, "ExifTool reported it written, but it holds what it held before")


# ---- Names, turns and deletes -------------------------------------------------------------

@file_changes.exclusively()
def rename(renames, aside):
    """Rename photos -- `renames`, old -> new, and first the files in the way, `aside`
    (tagpup.files.names.aside_for) -- all together or not at all (tagpup.files.names.rename_all,
    which puts every one back under its old name when one fails). No journal, no row, so no way back
    from History: if the process dies between its two passes, run Smart Rename on the folder again (it
    regenerates the same names); a file left named tmp_rename_* is a photo waiting for its new name.
    Raises names.RenameFailed. Returns file_changes.Renamed: nothing moved in any index."""
    done, moved_aside = names.rename_all(renames, aside=aside)
    return file_changes.Renamed(done, moved_aside, 0, [], None)


@file_changes.exclusively()
def rotate(photo_path, direction, exiftool_path) -> Result:
    """Turn a photo a quarter (tagpup.files.metadata.rotate_image_file): its Orientation
    only. No row, no face boxes, no vectors, no thumbnail to clear. details: `orientation`,
    `mtime` and `size` of the file now, as services.photos.rotate gives them."""
    result = Result(attempted=1)
    try:
        orientation = metadata.rotate_image_file(photo_path, direction, exiftool_path)
    except Exception as e:
        result.fail(photo_path, e)
        return result
    result.changed = 1
    stat = os.stat(photo_path)
    result.details.update(orientation=orientation, faces_turned=0, mtime=stat.st_mtime, size=stat.st_size)
    return result


def delete(photo_path):
    """Send a photo to the Recycle Bin and forget nothing: it has no row. details: `removed`
    None."""
    result = Result(attempted=1)
    try:
        moved = recycle_bin.send_to_recycle_bin(photo_path)
    except Exception as e:
        result.fail(photo_path, e)
        return result
    if not moved:
        result.fail(photo_path, "Failed to move file to Recycle Bin")
        return result
    result.changed = 1
    result.details["removed"] = None
    return result
