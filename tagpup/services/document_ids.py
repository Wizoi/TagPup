"""Give every indexed photo the identity new ones get when they are indexed.

Indexing records `XMP-xmpMM:DocumentID` and mints one where a photo has none, so a
renamed or moved file can be matched back to the row holding its embedding and its
faces. Rows indexed before that have `document_id` NULL, and would fill in only as those
photos were indexed again -- which for a settled library is never. This reads each such
photo's identity, mints one into a file that has none, and records them.

Two changes of the journal, each undoable (docs/ARCHITECTURE.md, phase 7.5):

- the identities the files hold already, recorded in their rows: a change of rows on the
  maintenance scaffold (tagpup.services.maintenance), each row still without one when it
  is written, or left out and reported;
- the identities minted, a change of photo files (tagpup.services.file_changes): each
  file planned as holding none and to hold its new one, written, and recorded in its row
  -- identity, mtime and size -- as it is marked done. Undone, the identity comes out of
  the file again.

A dry run rehearses the first and reads, writing nothing. Rows whose file has moved away
are counted and left alone: the row is not wrong, it points where the file no longer is,
which is the problem an identity solves and this pass cannot solve after the fact. Only
rows without an identity are read, so a run stopped part way is picked up by the next.

scripts/backfill_document_ids.py runs it.
"""
import os

from tagpup.core import paths
from tagpup.core.result import Result
# Looked up at call time, as exiftool_session.ExifToolSession, so a test standing in for
# ExifTool there reaches this too.
from tagpup.files import exiftool_session, identity
from tagpup.services import file_changes, maintenance
from tagpup.store import db, journal
from tagpup.store import photos as store_photos

#: What the two changes are recorded as.
RECORD, MINT = "backfill_document_ids", "backfill_document_ids: mint"


class NoIdentityColumn(Exception):
    """A library from before `photos.document_id`."""


def without_identity(library):
    """(photos with no identity recorded whose file is there, those whose file is not),
    as stored. NoIdentityColumn for a library from before the column."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        try:
            found = store_photos.without_identity(conn)
        except Exception as e:
            raise NoIdentityColumn(str(e)) from None
    finally:
        conn.close()
    there, gone = [], []
    for path in found:
        (there if os.path.exists(path) else gone).append(path)
    return there, gone


def read_identities(photo_paths, exiftool_path=None, batch_size=200, on_progress=None):
    """({path: the identity its file holds}, [paths whose file holds none], [(path, why)
    could not be read]), each path as given. ExifTool answers with forward slashes
    whatever it was handed, so its answer is mapped back to the spelling asked about
    (paths.key): a row recorded under ExifTool's spelling matched nothing, and a run
    reported 60 photos done and wrote none."""
    as_asked = {paths.key(p): p for p in photo_paths}
    found, lacking, failed = {}, [], []
    with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
        for start in range(0, len(photo_paths), batch_size):
            batch = photo_paths[start:start + batch_size]
            try:
                results = et.get_tags(batch, tags=[identity.DOCUMENT_ID_FIELD])
            except Exception:
                # One unreadable file fails the whole batch; read the rest one at a time.
                results = []
                for one in batch:
                    try:
                        results.extend(et.get_tags([one], tags=[identity.DOCUMENT_ID_FIELD]))
                    except Exception as e:
                        failed.append((one, str(e)))
            for row in results:
                reported = row.get("SourceFile")
                if not reported:
                    continue
                path = as_asked.get(paths.key(reported), reported)
                held = identity.read_document_id(row)
                if held:
                    found[path] = held
                else:
                    lacking.append(path)
            if on_progress:
                on_progress(min(start + batch_size, len(photo_paths)), len(photo_paths), len(found),
                            len(lacking), len(failed))
    return found, lacking, failed


def record_edits(library, found, minted=()):
    """The journal's edits recording each identity in `found` ({path: identity}) on its
    row, found by paths.sql_equals in any spelling, while the row still has none. A row of
    `minted` -- whose file was just written -- takes the file's mtime and size too. A path
    with no row has nothing to record."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        rows = store_photos.rows_of(conn, list(found))
    finally:
        conn.close()
    minted = {paths.key(p) for p in minted}
    edits = []
    for path, document_id in found.items():
        row = rows.get(paths.key(path))
        if row is None:
            continue
        photo_id, _stored, held = row
        values = {"document_id": document_id}
        if paths.key(path) in minted:
            try:
                stat = os.stat(path)
                values.update(mtime=stat.st_mtime, size=stat.st_size)
            except OSError:
                pass
        edits.append(journal.update("photos", (photo_id,), {"document_id": held}, values,
                                    kind="recorded", skippable=True))
    return edits


def record(library, found, minted=()):
    """Record identities (record_edits) as one change of the journal. Returns the rows
    written, not the identities asked about."""
    edits = record_edits(library, found, minted)
    if not edits:
        return 0
    return journal.apply(library.path, RECORD, edits, {"counts": {"identities": len(found)}}).changed


def _mint(library, photo_paths, exiftool_path):
    """Write a new identity into each photo, as one change of photo files."""
    def plan_one(_path, held):
        if held.get(identity.DOCUMENT_ID_FIELD):
            return file_changes.skip("holds an identity already")
        return file_changes.Plan(after={identity.DOCUMENT_ID_FIELD: [identity.mint_document_id()]})

    return file_changes.write_fields(library, MINT, exiftool_path, photo_paths, [identity.DOCUMENT_ID_FIELD],
                                     plan_one, summary={"photos": len(photo_paths)})


def backfill(library, exiftool_path=None, apply=False, limit=0, batch_size=200, on_progress=None):
    """Read, and with `apply` record and mint, the identity of every photo whose row has
    none. A Result: `attempted` the photos read; `changed` the rows recorded and the files
    minted into. details: `counts` (without, on_disk, gone, held, lacking, unreadable),
    `record` (the maintenance scaffold's Result details: its change, or its rehearsal),
    `mint` (the change of photo files, or None), `remaining` (photos on disk still
    without an identity, after an apply)."""
    there, gone = without_identity(library)
    if limit:
        there = there[:limit]
    result = Result(attempted=len(there), details={"dry_run": not apply, "mint": None})
    found, lacking, failed = read_identities(there, exiftool_path, batch_size, on_progress) if there else ({}, [], [])
    result.details["counts"] = {"on_disk": len(there), "gone": len(gone), "held": len(found),
                                "lacking": len(lacking), "unreadable": len(failed)}
    for path, why in failed:
        result.fail(path, why)

    def plan(_library):
        return maintenance.Plan(size=len(found), counts={"identities": len(found)},
                                work=record_edits(library, found))

    recorded = maintenance.run(library, RECORD, plan, lambda planned: planned.work, apply=apply)
    result.details["record"] = recorded.details
    result.changed += recorded.changed
    result.skipped += recorded.skipped
    result.errors += recorded.errors
    if recorded.refused:
        result.refuse(recorded.refused)
    if apply and lacking and not recorded.refused:
        minted = _mint(library, lacking, exiftool_path)
        result.details["mint"] = {"change": minted.details.get("change"), "changed": minted.changed,
                                  "conflicts": minted.details.get("conflicts", [])}
        result.changed += minted.changed
        result.skipped += minted.skipped
        result.errors += minted.errors
    if apply:
        result.details["remaining"] = len(without_identity(library)[0])
    return result
