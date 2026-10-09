"""Read again, for the fields ExifTool is asked for now, the photos whose rows were read before they were.

A row records what a read of its file found; when a field is added to tagpup.core.fields.METADATA_FIELDS -- the lens,
2026-10-09: 0 of 68,324 rows of photo_index held one -- the rows already read lack it, and the indexer does not read a
file again until its modified time or size changes. A row says which read it comes from: `TagPup:ReadGeneration` in its
raw_metadata (fields.READ_GENERATION; docs/findings.md, #1019). This takes the rows whose generation is lower, reads
those files, through ExifTool's session and for metadata only (no picture is decoded, no model loaded, no graphics card
used, no file written), and records in each row ONE thing: its raw_metadata as the file holds it now (and a document_id,
where the row has none and the file has one). Never the tags, captions, people, modified time or size: this is a lens
re-read, not a refresh. A row whose tags, captions or people differ from the file's is "disagrees" and left alone, for
`sync` or refresh_rows (tagpup.services.refresh_rows) to bring in step; reading and comparing are refresh_rows' own
(read_files, differences). Faces, names and embeddings are untouched.

The rows it writes go through the journal like any change, so the derived tables (photo_meta, search_gear: the lens words)
are rebuilt for exactly those photos by the same writer, with no separate doctor run (tagpup.store.derived).

What is not read, each counted and said:

* a row never read (nothing in raw_metadata but what writes recorded): reading it is indexing it, which this is not;
* a row whose raw_metadata holds an ExifTool error ("damaged"): its file was unreadable when it was read, and is not tried
  again on every run; it is a job for the Activity page's damaged photos. The dry run's sample does not include them;
* a photo on a network share, unless `folder` is given (the owner names the folder) or `shares` is: a share is never
  touched unnamed. Every look at a share is bounded (tagpup.files.shares); after the first that does not answer, the rest of
  that share's photos are left for the run;
* a photo whose file is missing, or ExifTool could not read, or changed on disk since its row was made (its modified time or
  size differs from the row's, or differs after the read from before it). A changed file is `sync`'s: it reads the row again
  with these same fields, and sees the size change library.reread_resized_pictures is about, which writing the new stamp
  here would hide. Left alone, none of them fails the run;
* a row the app saved while its file was read (the edit is skippable, as the refresh's): left for the next run.

Two things stop a run, because going on would only repeat them: a chunk whose every file is gone (the place is unplugged or
renamed: stopped after that one chunk, not after all of them), and ExifTool that cannot be started, found out when a run so far
has read none of 10 or more files. Files that are merely unreadable do not: they are counted, and the run ends as it began.

A dry run (the default) reads no file but a sample of `sample` photos spread through the rows, timed: the rate, an estimate
for the rest, how many of the sample hold a lens and how many disagree. Applied, it reads and writes `chunk` photos at a
time, each chunk one change of the journal, so that an interruption costs the chunk being read and the next run takes up
where it stopped (the rows written have their generation; the others still lack it). Two runs at once, or a run beside the
app, are held apart by the rows themselves: each row is written only while it is what the run read, and a row the other
wrote first is skipped.
"""
import json
import os
import time
from collections import Counter, namedtuple

from tagpup.core import fields, paths, photo_meta
from tagpup.core.result import Result
from tagpup.files import metadata, shares
from tagpup.services import folder_scope, maintenance, refresh_rows
from tagpup.store import db, journal
from tagpup.store import photos as store_photos

OPERATION = "reread_fields"

#: Photos read, and written as one change of the journal, at a time.
CHUNK = 2000

#: Photos a dry run reads to time the reading.
SAMPLE = 100

#: Seconds a look at a share that may be away is waited for.
PROBE_SECONDS = 10

#: Files of which a run read none, from which ExifTool is looked at (can it be started?), and a place whose files are all
#: gone is taken as away.
NOTHING_READ_FROM = 10

#: What a row may differ from its file in and still be written: nothing but these are ever written.
WRITTEN = ("raw_metadata", "document_id")

#: What a row may not differ from its file in to be written: it is sync's or refresh_rows' to settle.
DISAGREES = ("tags", "people", "captions")

Candidate = namedtuple("Candidate", "id path mtime size")


def candidates(conn, scope_ids=None):
    """(the rows to read again as Candidates, in path order; Counter of the others) of the library open on `conn`, or of the
    photos in `scope_ids`: "rows", "current" (already read with the fields), "never_read", "damaged"."""
    counts, found = Counter(rows=0, current=0, never_read=0, damaged=0), []
    for chunk in store_photos.raw_in_chunks(conn):
        for photo_id, path, mtime, size, raw_json in chunk:
            if scope_ids is not None and photo_id not in scope_ids:
                continue
            counts["rows"] += 1
            try:
                raw = json.loads(raw_json or "{}")
            except ValueError:
                raw = {}
            generation = fields.read_generation(raw)
            if generation >= fields.READ_GENERATION:
                counts["current"] += 1
            elif generation == 0:
                counts["never_read"] += 1
            elif any(raw.get(key) for key in metadata.READ_ERROR_KEYS):
                counts["damaged"] += 1
            else:
                found.append(Candidate(photo_id, path, mtime, size))
    found.sort(key=lambda c: paths.key(c.path))
    return found, counts


class Places:
    """Which places (a drive, or a server and share) are networked, and which stopped answering in this run: every look at a
    networked path is bounded, and once one place did not answer none of its paths is looked at again."""

    def __init__(self):
        self.networked, self.away = {}, set()

    def of(self, path):
        share = shares.share_of(path)
        if share not in self.networked:
            self.networked[share] = shares.on_a_network_drive(path)
        return share

    def is_networked(self, path):
        return self.networked[self.of(path)]

    def is_away(self, path):
        return self.of(path) in self.away

    def look(self, path, call):
        """("ok", the answer) | ("error", the OSError) | ("away", None). A path off the network is looked at directly."""
        share = self.of(path)
        if share in self.away:
            return "away", None
        if not self.networked[share]:
            try:
                return "ok", call()
            except OSError as problem:
                return "error", problem
        verdict, answer = shares.bounded(path, call, PROBE_SECONDS)
        if verdict == "away":
            self.away.add(share)
        return verdict, answer

    def exists(self, path):
        """True | False | None (its place did not answer)."""
        verdict, _ = self.look(path, lambda: os.stat(path))
        return None if verdict == "away" else verdict == "ok"


def sort_out(found, allow_shares, places=None):
    """(the candidates to read, each with the stamp of its file now, as [(Candidate, (mtime, size))]; Counter of the rest)
    -- "on_shares" (a share nobody named), "share_away", "missing", "changed" (its stamp is not its row's)."""
    ready, counts = [], Counter()
    places = places or Places()
    for candidate in found:
        if places.is_networked(candidate.path) and not allow_shares:
            counts["on_shares"] += 1
            continue
        verdict, stat = places.look(candidate.path, lambda path=candidate.path: os.stat(path))
        if verdict == "away":
            counts["share_away"] += 1
            continue
        if verdict == "error":
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
    """The timing of reading `sample` of the photos in `ready`: a dict of how many were read, could not be, hold a lens, would
    be left because the row disagrees with its file, the seconds and files a second."""
    chosen = [candidate.path for candidate, _stamp in _spread(ready, sample)]
    if not chosen:
        return {"read": 0, "unreadable": 0, "with_lens": 0, "disagrees": 0, "seconds": 0.0, "per_second": None}
    started = time.perf_counter()
    records = refresh_rows.read_files(conn, chosen, exiftool_path)
    seconds = time.perf_counter() - started
    readable = {path: r for path, r in records.items() if r.get("raw_metadata") and not r.get("read_error")}
    disagrees = 0
    for path, record in readable.items():
        stored = store_photos.row_as_recorded(conn, path)
        if stored and set(refresh_rows.differences(conn, path, record, stored)[0]) & set(DISAGREES):
            disagrees += 1
    return {"read": len(chosen), "unreadable": len(chosen) - len(readable), "disagrees": disagrees,
            "with_lens": sum(1 for r in readable.values() if photo_meta.gear(r["raw_metadata"]).lens),
            "seconds": round(seconds, 2), "per_second": round(len(chosen) / seconds, 1) if seconds else None}


def reread_fields(library, exiftool_path, apply=False, folder=None, shares_named=False, sample=SAMPLE, chunk=CHUNK,
                  progress=None):
    """Plan, and with `apply` make, the re-read of the photos of `library` (or under `folder`) read before the fields now asked
    for. A Result: `changed` is rows written, details["counts"] what was found and set aside (see the module's docstring),
    details["fields"] how many rows differ from their file in each field (only "raw_metadata" and "document_id" are written),
    details["sample"] and ["estimate_seconds"] on a dry run, details["changes"] the journal's change ids when applied,
    details["remaining"] the rows still to read afterwards. `progress(stage, counts)` is called with "found" and, applied,
    "chunk". A folder the library holds no photo under is refused, as the face backfills' are."""
    result = Result(details={"dry_run": not apply, "changes": [], "counts": {}, "fields": {}, "sample": None,
                             "estimate_seconds": None})
    scope_ids = None
    if folder is not None:
        try:
            scope_ids = set(folder_scope.resolve(library, folder).photo_ids)
        except folder_scope.NoPhotosThere as why:
            result.refuse(str(why))
            return result
    allow_shares = shares_named or folder is not None
    places = Places()
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        try:
            found, counts = candidates(conn, scope_ids)
        except paths.UnmappedRoot as why:
            result.fail("the library's folders", why)
            return result
        ready, rest = sort_out(found, allow_shares, places)
        counts.update(rest)
        counts["to_read"] = len(ready)
        result.details["counts"] = dict(counts)
        result.attempted = len(ready)
        if progress is not None:
            progress("found", dict(counts))
        if found and not ready and counts["missing"] + counts["share_away"] == len(found) >= NOTHING_READ_FROM:
            result.fail("the files", "none of the %d files is there or answers: is the drive or share unplugged, or the "
                        "folder renamed? Nothing was read." % len(found))
            return result
        if not apply:
            if ready:
                sampled = _read_sample(conn, ready, sample, exiftool_path)
                result.details["sample"] = sampled
                if sampled["per_second"]:
                    result.details["estimate_seconds"] = round(len(ready) / sampled["per_second"])
            return result
    finally:
        conn.close()
    _apply(library, exiftool_path, ready, chunk, result, progress, places)
    after = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        left, _ = candidates(after, scope_ids)
        result.details["remaining"] = len(left)
    finally:
        after.close()
    return result


def _apply(library, exiftool_path, ready, chunk, result, progress, places):
    """Read and write `ready` a chunk at a time. Stops at a chunk that fails to be written, at one whose every file is gone,
    and when a run that has read none of 10 or more files finds ExifTool cannot be started."""
    counts = result.details["counts"]
    done, differing = Counter(), Counter()
    checked = False
    for start in range(0, len(ready), chunk):
        part = [(c, stamp) for c, stamp in ready[start:start + chunk]]
        kept = [(c, stamp) for c, stamp in part if not places.is_away(c.path)]
        done["share_away"] += len(part) - len(kept)
        if not kept:
            continue
        outcome = _chunk(library, exiftool_path, kept, places)
        done.update(outcome.counts)
        differing.update(outcome.fields)
        done["written"] += _write(library, outcome, result)
        if progress is not None:
            progress("chunk", {"done": min(start + chunk, len(ready)), "total": len(ready), "written": done["written"],
                               "fields": dict(outcome.fields)})
        if not result.ok:
            break
        if outcome.counts["missing"] == len(kept) >= min(NOTHING_READ_FROM, len(ready)):
            result.fail("the files", "every one of %d files of a chunk is gone: is the drive or share unplugged, or the "
                        "folder renamed? Stopped after that chunk; what was read before stays written." % len(kept))
            break
        if done["read"] == 0 and done["unreadable"] >= NOTHING_READ_FROM and not checked:
            checked = True
            starts, why = metadata.exiftool_starts(exiftool_path)
            if not starts:
                result.fail("ExifTool", "cannot be started, and %d files were read by none: is its path right? (%s). Stopped; "
                            "what was read before stays written." % (done["unreadable"], why))
                break
    counts.update({"written": done["written"], "unreadable": done["unreadable"], "changed_while_read": done["changed"],
                   "gone_while_read": done["missing"], "disagrees": done["disagrees"],
                   "share_away": counts.get("share_away", 0) + done["share_away"]})
    result.details["fields"] = dict(differing)


Chunk = namedtuple("Chunk", "counts fields records to_write found identities ids")


def _chunk(library, exiftool_path, part, places):
    """Read the files of `part` ([(Candidate, stamp)]) and sort what they held against their rows: which are to be written
    (only their raw_metadata, and a document_id), which disagree and are left, which could not be read."""
    counts, differing, ids, found, identities, to_write = Counter(), Counter(), {}, {}, {}, []
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        # Each row as it is BEFORE its file is read: the write holds a row to this, and a row the app saves while the
        # files are read is skipped, not overwritten with the older read (as refresh_rows does).
        rows = {candidate.path: store_photos.row_as_recorded(conn, candidate.path) for candidate, _ in part}
        records = refresh_rows.read_files(conn, [candidate.path for candidate, _ in part], exiftool_path)
        for candidate, stamp in part:
            path, record = candidate.path, records.get(candidate.path)
            if record is None or not record.get("raw_metadata") or record.get("read_error"):
                there = places.exists(path)
                counts["share_away" if there is None else "unreadable" if there else "missing"] += 1
                continue
            counts["read"] += 1
            if (record["mtime"], record["size"]) != stamp:
                counts["changed"] += 1   # it changed between looking and reading: sync's
                continue
            stored = rows[path]
            if stored is None:
                continue
            tags, _people, captions, raw, mtime, size, document_id = stored
            different, _old, _now = refresh_rows.differences(conn, path, record, stored)
            differing.update(different)
            if set(different) & set(DISAGREES):
                counts["disagrees"] += 1
                continue
            if set(different) & set(WRITTEN):
                to_write.append(path)
                ids[path] = candidate.id
                identities[path] = document_id
                found[path] = {"mtime": mtime, "size": size, "tags": tags, "captions": captions, "raw_metadata": raw}
    finally:
        conn.close()
    return Chunk(counts, differing, records, to_write, found, identities, ids)


def edits_for(records, to_write, found, identities, ids):
    """The edits of the rows in `to_write`: each its raw_metadata as its file holds it, and its document_id where it has
    none, nothing else. `found` is each row as it was before its file was read, held to by the write."""
    edits = []
    for path in to_write:
        record = records[path]
        values, expect = {"raw_metadata": json.dumps(record["raw_metadata"])}, dict(found[path])
        if identities.get(path) is None and record.get("document_id"):
            values["document_id"] = record["document_id"]
            expect["document_id"] = None
        edits.append(journal.update("photos", (ids[path],), expect, values, kind="from_files", skippable=True))
    return edits


def _write(library, outcome, result):
    """Record a chunk as one change of the journal; the rows written. A row the app saved since it was read is skipped."""
    if not outcome.to_write:
        return 0
    planned = maintenance.Plan(size=len(outcome.to_write), counts={"rows": len(outcome.to_write)},
                               summary={"fields": "lens", "generation": fields.READ_GENERATION},
                               work=(outcome.records, outcome.to_write, outcome.found, outcome.identities, outcome.ids))
    done = maintenance.run(library, OPERATION, lambda _library: planned, lambda plan: edits_for(*plan.work), apply=True,
                           kinds=("from_files",))
    if done.details.get("change") is not None:
        result.details["changes"].append(done.details["change"])
    result.changed += done.changed
    result.skipped.extend(done.skipped)
    result.errors.extend(done.errors)
    if done.refused:
        result.fail("the write", done.refused)
    return done.changed
