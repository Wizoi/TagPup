"""Read again, for the fields ExifTool is asked for now, the photos whose rows were read before they were.

A row records what a read of its file found; when a field is added to tagpup.core.fields.METADATA_FIELDS -- the lens,
2026-10-09: 0 of 68,324 rows of photo_index held one -- the rows already read lack it, and the indexer does not read a
file again until its modified time or size changes. A row says which read it comes from: `TagPup:ReadGeneration` in its
raw_metadata (fields.READ_GENERATION; docs/findings.md, #1012). This takes the rows whose generation is lower, reads
those files, through ExifTool's session and for metadata only (no picture is decoded, no model loaded, no graphics card used, no file written)
and records what each holds, as refresh_rows records a re-read -- tags, people, captions, raw_metadata, modified time and
size, through the same reading and the same edits (tagpup.services.refresh_rows.read_files, edits_for). Faces, names,
embeddings and tags other than what the file itself holds are untouched; a person named on a face stays in the row's people.

The rows it writes go through the journal like any change, so the derived tables (photo_meta, search_gear: the lens words)
are rebuilt for exactly those photos by the same writer, with no separate doctor run (tagpup.store.derived).

What is not read, each counted and said:

* a row never read (nothing in raw_metadata but what writes recorded): reading it is indexing it, which this is not;
* a photo on a network share, unless `folder` is given (the owner names the folder) or `shares` is: a share is never
  touched unnamed. A share that does not answer in time is taken as away (tagpup.files.shares) and its photos are left;
* a photo whose file is missing, or ExifTool could not read, or changed on disk since its row was made (its modified time or
  size differs from the row's, or differs after the read from before it). A changed file is `sync`'s: it reads the row again
  with these same fields, and sees the size change library.reread_resized_pictures is about, which writing the new stamp
  here would hide. Left alone, none of them fails the run;
* a row the app saved while its file was read (the edit is skippable, as the refresh's): left for the next run.

A dry run (the default) reads no file but a sample of `sample` photos spread through the rows, timed: the rate, an estimate
for the rest, and how many of the sample hold a lens. Applied, it reads and writes `chunk` photos at a time, each chunk one
change of the journal, so that an interruption costs the chunk being read and the next run takes up where it stopped (the
rows written have their generation; the others still lack it). Two runs at once, or a run beside the app, are held apart by
the rows themselves: each row is written only while it is what the run read, and a row the other wrote first is skipped.
"""
import json
import os
import time
from collections import Counter, namedtuple

from tagpup.core import fields, paths, photo_meta
from tagpup.core.result import Result
from tagpup.files import shares
from tagpup.services import folder_scope, maintenance, refresh_rows
from tagpup.store import db
from tagpup.store import photos as store_photos

OPERATION = "reread_fields"

#: Photos read, and written as one change of the journal, at a time.
CHUNK = 2000

#: Photos a dry run reads to time the reading.
SAMPLE = 100

#: Seconds a look at a share that may be away is waited for.
PROBE_SECONDS = 10

#: A chunk of this many files of which ExifTool read none: something is wrong with ExifTool or the files' place, and the run
#: stops instead of going through the rest the same way.
NOTHING_READ_FROM = 10

Candidate = namedtuple("Candidate", "id path mtime size")


def candidates(conn, scope_ids=None):
    """(the rows to read again as Candidates, in path order; Counter of the others) of the library open on `conn`, or of the
    photos in `scope_ids`: "rows", "current" (already read with the fields), "never_read"."""
    counts, found = Counter(rows=0, current=0, never_read=0), []
    for chunk in store_photos.raw_in_chunks(conn):
        for photo_id, path, mtime, size, raw_json in chunk:
            if scope_ids is not None and photo_id not in scope_ids:
                continue
            counts["rows"] += 1
            try:
                generation = fields.read_generation(json.loads(raw_json or "{}"))
            except ValueError:
                generation = 0
            if generation >= fields.READ_GENERATION:
                counts["current"] += 1
            elif generation == 0:
                counts["never_read"] += 1
            else:
                found.append(Candidate(photo_id, path, mtime, size))
    found.sort(key=lambda c: paths.key(c.path))
    return found, counts


def sort_out(found, allow_shares):
    """(the candidates to read, each with the stamp of its file now, as [(Candidate, (mtime, size))]; Counter of the rest)
    -- "on_shares" (a share nobody named), "share_away", "missing", "changed" (its stamp is not its row's)."""
    ready, counts = [], Counter()
    networked, away = {}, {}
    for candidate in found:
        share = shares.share_of(candidate.path)
        if share not in networked:
            networked[share] = shares.on_a_network_drive(candidate.path)
        if networked[share]:
            if not allow_shares:
                counts["on_shares"] += 1
                continue
            if share not in away:
                # One look a share, bounded: a share that does not answer is away, and its photos are left, not waited for.
                verdict, _ = shares.bounded(candidate.path, lambda path=candidate.path: os.stat(path), PROBE_SECONDS)
                away[share] = verdict == "away"
            if away[share]:
                counts["share_away"] += 1
                continue
        try:
            stat = os.stat(candidate.path)
        except OSError:
            counts["missing"] += 1
            continue
        if not store_photos.describes(candidate.mtime, candidate.size, (stat.st_mtime, stat.st_size)):
            counts["changed"] += 1
            continue
        ready.append((candidate, (stat.st_mtime, stat.st_size)))
    return ready, counts


def _spread(items, count):
    """`count` of `items`, evenly spread through them."""
    if len(items) <= count:
        return list(items)
    step = len(items) / float(count)
    return [items[int(i * step)] for i in range(count)]


def _read_sample(conn, ready, sample, exiftool_path):
    """The timing of reading `sample` of the photos in `ready`: a dict of how many were read, could not be, hold a lens,
    the seconds and files a second."""
    chosen = [candidate.path for candidate, _stamp in _spread(ready, sample)]
    if not chosen:
        return {"read": 0, "unreadable": 0, "with_lens": 0, "seconds": 0.0, "per_second": None}
    started = time.perf_counter()
    records = refresh_rows.read_files(conn, chosen, exiftool_path)
    seconds = time.perf_counter() - started
    readable = [r for r in records.values() if r.get("raw_metadata") and not r.get("read_error")]
    return {"read": len(chosen), "unreadable": len(chosen) - len(readable),
            "with_lens": sum(1 for r in readable if photo_meta.gear(r["raw_metadata"]).lens),
            "seconds": round(seconds, 2), "per_second": round(len(chosen) / seconds, 1) if seconds else None}


def reread_fields(library, exiftool_path, apply=False, folder=None, shares_named=False, sample=SAMPLE, chunk=CHUNK,
                  progress=None):
    """Plan, and with `apply` make, the re-read of the photos of `library` (or under `folder`) read before the fields now asked
    for. A Result: `changed` is rows written, details["counts"] what was found and set aside (see the module's docstring),
    details["sample"] and ["estimate_seconds"] on a dry run, details["changes"] the journal's change ids when applied,
    details["remaining"] the rows still to read afterwards. `progress(stage, counts)` is called with "found" and, applied, "chunk".
    A folder the library holds no photo under is refused, as the face backfills' are."""
    result = Result(details={"dry_run": not apply, "changes": [], "counts": {}, "sample": None, "estimate_seconds": None})
    scope_ids = None
    if folder is not None:
        try:
            scope_ids = set(folder_scope.resolve(library, folder).photo_ids)
        except folder_scope.NoPhotosThere as why:
            result.refuse(str(why))
            return result
    allow_shares = shares_named or folder is not None
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        try:
            found, counts = candidates(conn, scope_ids)
        except paths.UnmappedRoot as why:
            result.fail("the library's folders", why)
            return result
        ready, rest = sort_out(found, allow_shares)
        counts.update(rest)
        counts["to_read"] = len(ready)
        result.details["counts"] = dict(counts)
        result.attempted = len(ready)
        if progress is not None:
            progress("found", dict(counts))
        if not apply:
            if ready:
                sampled = _read_sample(conn, ready, sample, exiftool_path)
                result.details["sample"] = sampled
                if sampled["per_second"]:
                    result.details["estimate_seconds"] = round(len(ready) / sampled["per_second"])
            return result
    finally:
        conn.close()
    _apply(library, exiftool_path, ready, chunk, result, progress)
    counts_after = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        left, _ = candidates(counts_after, scope_ids)
        result.details["remaining"] = len(left)
    finally:
        counts_after.close()
    return result


def _apply(library, exiftool_path, ready, chunk, result, progress):
    """Read and write `ready` a chunk at a time; stops at the first chunk that fails to be written, or whose files ExifTool
    read none of."""
    counts = result.details["counts"]
    done = Counter()
    for start in range(0, len(ready), chunk):
        part = ready[start:start + chunk]
        outcome = _chunk(library, exiftool_path, part)
        done.update(outcome.counts)
        written = _write(library, outcome, result)
        done["written"] += written
        if progress is not None:
            progress("chunk", {"done": min(start + chunk, len(ready)), "total": len(ready), "written": done["written"]})
        if not result.ok:
            break
        if outcome.counts["unreadable"] == len(part) and len(part) >= NOTHING_READ_FROM:
            result.fail("the files", "ExifTool read none of %d files in a row: is ExifTool's path right, or their place "
                        "unplugged? Stopped; what was read before stays written." % len(part))
            break
    counts.update({"written": done["written"], "unreadable": done["unreadable"], "changed_while_read": done["changed"],
                   "gone_while_read": done["missing"]})


Chunk = namedtuple("Chunk", "counts records to_write found identities ids")


def _chunk(library, exiftool_path, part):
    """Read the files of `part` ([(Candidate, stamp)]) and sort what they held against their rows."""
    counts, ids, found, identities, to_write = Counter(), {}, {}, {}, []
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        # Each row as it is BEFORE its file is read: the write holds a row to this, and a row the app saves while the
        # files are read is skipped, not overwritten with the older read (as refresh_rows does).
        rows = {candidate.path: store_photos.row_as_recorded(conn, candidate.path) for candidate, _ in part}
        records = refresh_rows.read_files(conn, [candidate.path for candidate, _ in part], exiftool_path)
        for candidate, stamp in part:
            path, record = candidate.path, records.get(candidate.path)
            if record is None or not record.get("raw_metadata") or record.get("read_error"):
                counts["missing" if not os.path.exists(path) else "unreadable"] += 1
                continue
            if (record["mtime"], record["size"]) != stamp:
                counts["changed"] += 1   # it changed between looking and reading: sync's
                continue
            stored = rows[path]
            if stored is None:
                continue
            tags, _people, captions, raw, mtime, size, document_id = stored
            different, _old, _now = refresh_rows.differences(conn, path, record, stored)
            if different:
                to_write.append(path)
                ids[path] = candidate.id
                identities[path] = document_id
                found[path] = {"mtime": mtime, "size": size, "tags": tags, "captions": captions, "raw_metadata": raw}
    finally:
        conn.close()
    return Chunk(counts, records, to_write, found, identities, ids)


def _write(library, outcome, result):
    """Record a chunk as one change of the journal; the rows written. A row the app saved since it was read is skipped."""
    if not outcome.to_write:
        return 0
    planned = maintenance.Plan(size=len(outcome.to_write), counts={"rows": len(outcome.to_write)},
                               summary={"fields": "lens", "generation": fields.READ_GENERATION},
                               work=(outcome.records, outcome.to_write, {}, outcome.found, outcome.identities, outcome.ids))
    done = maintenance.run(library, OPERATION, lambda _library: planned,
                           lambda plan: refresh_rows.edits_for(*plan.work), apply=True, kinds=("from_files",))
    if done.details.get("change") is not None:
        result.details["changes"].append(done.details["change"])
    result.changed += done.changed
    result.skipped.extend(done.skipped)
    result.errors.extend(done.errors)
    if done.refused:
        result.fail("the write", done.refused)
    return done.changed
