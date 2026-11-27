"""Writes that change photo files and nothing of the library's: Just look in TagPup.

A photo in a folder the library does not hold has no row, and a write must not make one
-- a row makes its folder the library's, sync keeps it in step, the watcher watches it
and its tags grow the library's tag tree (the 2026-09-28 bug, tagpup.store.photos.
ensure_row). So the writes that need no database -- a caption, a tag, a rename, a turn, a
date, a delete -- are made here for such a photo, and write only its file:

- no photo row is made or touched, no journal change is planned, nothing is derived
  (people, dates, embeddings, faces, thumbnails), and the folder is not registered with
  sync or the watcher. The library's database is not opened for writing at all; tests
  dump every table before and after;
- the file is not looked at before the write, as a held photo's is not (DECISIONS
  2026-10-15): a file ExifTool cannot read or write fails that photo's write;
- what is reported is what was written: a file counted changed is one read back holding
  what was written, as the journaled write counts it;
- a write whose new file's picture changed is made once more, as a held photo's is
  (Fields.write's `again`), and counted in the Result's details, `pictures_refused`.

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

from tagpup.core import fields as core_fields, paths
from tagpup.core.result import Result
from tagpup.files import field_values, lock_owners, names, recycle_bin
from tagpup.services import file_changes

logger = logging.getLogger(__name__)

#: What a Result's details count of the photos a write made to files only, and of those
#: it wrote with their rows: how many files each, as written.
FILE_ONLY = "file_only"
WITH_ROWS = "with_rows"

# ---- Combining a write of held photos with one of the others -----------------------------

def combined(held, loose):
    """One Result for a write made in two parts: `held`, the photos of folders the library
    holds, written as always, and `loose`, the others, written to files only. Counts add;
    details keep `held`'s with the files each wrote under them -- `written` and `stamps` joined -- and FILE_ONLY and
    WITH_ROWS say how many files each part wrote. Either may be None."""
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
    for name in ("written", "stamps"):
        if name in held.details or name in loose.details:
            out.details[name] = {**held.details.get(name, {}), **loose.details.get(name, {})}
    if "conflicts" in held.details or "conflicts" in loose.details:
        out.details["conflicts"] = list(held.details.get("conflicts", [])) + list(loose.details.get("conflicts", []))
    out.details[WITH_ROWS] = held.changed
    out.details[FILE_ONLY] = loose.changed
    return out


# ---- Fields ---------------------------------------------------------------------------------

@file_changes.exclusively()
def write_fields(exiftool_path, photo_paths, read, plan_one, unreadable="fail", stop_at_first_error=False,
                 et=None, held=None):
    """file_changes.write_fields for photos the library does not hold: the same planning
    (`read` the fields to read of each file, `plan_one(path, held)` a Plan from what it
    holds, `unreadable`, `stop_at_first_error`, an `et` Fields (field_values.opened) and what the caller `held`
    of the files through it) and the same Result -- `changed` the files written, errors the photos
    that failed, details `written` {path: detail} of each written or already holding it, `stamps` {path key: (mtime,
    size) or None} of each file written, taken just after its write (file_changes.stamp_after_write), `conflicts`,
    `pictures_refused` when a write was made once more -- but only the files are written: no
    journal change (details["change"] is None), no row, nothing after."""
    if et is None:
        with field_values.opened(exiftool_path) as et:
            return _write_fields(et, photo_paths, read, plan_one, unreadable, stop_at_first_error, held)
    return _write_fields(et, photo_paths, read, plan_one, unreadable, stop_at_first_error, held)


def _write_fields(et, photo_paths, read, plan_one, unreadable, stop_at_first_error, fresh):
    result = Result(attempted=len(photo_paths))
    written = result.details["written"] = {}
    result.details.update(change=None, conflicts=[])
    # A clip is left out before anything is read of it, as a held photo's write leaves it out.
    stored = file_changes.leave_out_clips(result, [paths.stored(p) for p in photo_paths], unreadable)
    held = fresh if fresh is not None else et.read(stored, read)
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
        after = {field: core_fields.field_values(value) for field, value in plan.after.items()}
        before = {field: now.get(field, []) for field in after}
        if core_fields.reads_same(before, after):
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
        # Its stamp now, before the read back: the reply's, never looked at after a share that may have stopped (#3306).
        result.details.setdefault("stamps", {})[paths.key(path)] = file_changes.stamp_after_write(path)
        wrote.append((path, before, after))
    _read_back(et, wrote, result)
    logger.info("Wrote %d file(s) only, no row of any library (%d failed)", result.changed, len(result.errors))
    return result


def _write_one(et, path, before, after, just_read, result):
    """Bring one file from `before` to `after`. None when it was, else why it was not; a file
    that changed outside since it was read is never overwritten."""
    if not just_read:
        try:
            now = et.read_one(path, list(after))
        except field_values.Unreadable as e:
            return "could not be read: %s" % e
        if not core_fields.reads_same(now, before):
            result.details["conflicts"].append(path)
            return ("changed since it was read: it holds neither what the edit found nor what it was to leave,"
                    " and is not overwritten")
    def again():
        logger.warning("A Just-look photo's write changed the picture's bytes; the new file was discarded, and it is "
                       "written once more")
        result.details["pictures_refused"] = result.details.get("pictures_refused", 0) + 1
    try:
        et.write(path, after, again=again)
    except Exception as error:
        # Settled by what the file holds after it, as the journaled write settles it; after a failure only, who holds the
        # file, if it is held (access denied is a holder's only of a file there and not read-only: lock_owners.looks_held).
        try:
            now = et.read_back(path, list(after))
        except field_values.Unreadable:
            return lock_owners.explain(error, path, writable=_writable_if_denied(error, path))
        if core_fields.reads_same(now, after):
            return None
        error = lock_owners.explain(error, path, writable=_writable_if_denied(error, path))
        if core_fields.reads_same(now, before):
            return error
        result.details["conflicts"].append(path)
        return "a write failed (%s) and left it holding neither what it held nor what it was to hold" % error
    return None


def _writable_if_denied(error, path):
    """`writable` for lock_owners.explain: the file's stat is taken only for an error that could be a held file's (an
    access denied needs it, looks_held); a share that stopped answering has no use for an unbounded look (#2992)."""
    return lock_owners.looks_held(error, writable=True) and file_changes.writable(path)


def _read_back(et, wrote, result):
    """Read the files written back, once. One that holds what it held before, though ExifTool
    said it wrote it, changed nothing: not counted (the journaled write's #276). A value
    ExifTool keeps differently from how it was written is the file's: nothing is recorded
    here that could disagree. Never raises: the files are written either way."""
    if not wrote:
        return
    try:
        held = et.read([path for path, _b, _a in wrote], sorted({field for _p, _b, after in wrote for field in after}))
    except Exception as e:
        logger.warning("Could not read back %d file(s) written: %s", len(wrote), e)
        return
    for path, before, after in wrote:
        now = held.get(paths.key(path))
        if now is None or isinstance(now, Exception):
            continue
        kept = {field: now.get(field, []) for field in after}
        if core_fields.reads_same(kept, before) and not core_fields.reads_same(kept, after):
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


def delete(photo_path, for_good=False, fresh=True):
    """Send a photo to the Recycle Bin -- through this PC where its place has none (recycle_bin.delete_file, #694) -- and forget
    nothing: it has no row. details: `removed` None; `through_this_pc`, `no_bin_reason`, `copy` and `for_good` as recycle_bin
    says them."""
    result = Result(attempted=1)
    went = send(photo_path, for_good, result, fresh)
    if went is None:
        return result
    result.changed = 1
    result.details.update(removed=None)
    return result


def send(photo_path, for_good, result, fresh=True):
    """recycle_bin.delete_file(photo_path, for_good, fresh) into `result`: what it did in its details (`through_this_pc`,
    `no_bin_reason`, `copy`, `for_good`, `why_for_good`), and the dict; or None, `result` refused -- a file deleted for good
    only once confirmed, unconfirmed (recycle_bin.NotKept: details `not_kept`, its name, and `bin_full`, 1 when only this PC's Bin is full) -- or failed. The one way a delete of one file is told."""
    try:
        went = recycle_bin.delete_file(photo_path, for_good=for_good, fresh=fresh)
    except recycle_bin.NotKept as e:
        result.refuse(str(e))
        result.details["not_kept"] = [os.path.basename(photo_path)]
        result.details["bin_full"] = 1 if e.full else 0
        return None
    except Exception as e:
        result.fail(photo_path, e)
        return None
    result.details.update(through_this_pc=went["through_this_pc"], no_bin_reason=went["reason"], copy=went["copy"],
                          for_good=went["for_good"], why_for_good=went["why_for_good"])
    return went
