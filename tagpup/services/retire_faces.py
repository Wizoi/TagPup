"""Retiring the old face detector's faces, once a library is on the new face model (owner, 2026-10-22, photo_index: "we
have the new detector data and none of the old/manual data"; docs/AI_PIPELINE.md, phase 3).

After the new model's pass (tagpup.services.face_pass) and the switch (tagpup.services.settings.change), a library holds
faces of two kinds: those the pass matched, holding a vector under the new model's key beside FaceNet's, and those the
old detector alone found, holding FaceNet's vector alone, which no reader keyed by the library's model compares
(tagpup.store.faces.WITH_VECTOR). Retiring:

- deletes every face holding no vector under the library's model, decided or not, with its crop and its vectors under
  every key (store.faces.delete, the one writer of it: the photo's people are rebuilt, a photo left faceless is no longer
  one whose faces were detected);
- clears every decision of the faces kept -- the person (name, tag_id), who decided it (name_source), an exclusion and
  its reason (store.faces.UNDECIDED, written by store.faces.put_decisions, which rebuilds the photos' people);
- touches no keyword in a photo file and no node of the tag tree: a person named in a photo's keywords stays in its people.

`plan` reads only, and says what would change, in counts (Plan.said); it is refused, before anything is written, when the
library is not on the new model or more of its faces than a switch allows are in photos the pass has not reached
(settings.switch_to, the switch's own check and sentence). `apply` walks the photos the plan listed CHUNK at a time,
each chunk ONE transaction (BEGIN IMMEDIATE) in which the chunk's faces are read again (store.faces.retire_rows): a face
done since, by this run or another, no longer matches, and a photo removed since (sync, the indexer) has no faces to
read, and is left alone. In that transaction the deleted rows are recorded whole, children first, and the cleared
decisions as updates holding the old values (store.journal.record_removing), BEFORE the store writes them, with the
chunk's counts added to the change's summary: History says what was committed. The change, OPERATION, is opened applied
at the first chunk that writes, and added to while it is the journal's newest change and kept (journal.newest_kept): a
run stopped (`stop`, asked between chunks) or killed part-way leaves it whole, and the next run continues it; a change
recorded after it, or its undo, makes the next chunk open a new one. Undone from History, every deleted face comes back
with its crop and vectors, and every decision as it was.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from tagpup.core import validation
from tagpup.services import settings as library_settings
from tagpup.store import db, journal
from tagpup.store import faces as store_faces

logger = logging.getLogger(__name__)

#: The name of the change, with the key of the model whose faces are kept.
OPERATION = "retire faces (%s)"

#: The model a library must be on: the faces kept are those holding its vector.
MODEL = validation.SCRFD_ADAFACE

#: Photos a transaction: each holds the write lock for the reads, the journal's rows and the rebuild of their people.
CHUNK = 500

#: Why a run ends at a chunk: the library was switched to another model after the plan (settings, beside the run).
MODEL_MOVED = "the library's face model changed since the plan"

NOT_ON_THE_MODEL = ("Nothing was written: the library's face model is %s, not %s. Faces are retired only once a library "
                    "is on the new model (switch it in the library's settings after its pass).")


def _never():
    return False


@dataclass
class Plan:
    """What retiring would do: the counts of store.faces.retire_counts, and the photos to walk."""
    model: str
    counts: dict
    photo_ids: List[int] = field(default_factory=list)
    #: Why it is refused, nothing written; None when it may be applied.
    refused: Optional[str] = None

    def said(self):
        """What the owner is told, in counts."""
        if self.refused:
            return self.refused
        c = self.counts
        return ("%d of the library's %d faces hold no vector under %s and are deleted, with their crops and vectors "
                "(%d named automatically, %d decided by hand or excluded). Of the faces kept, the decisions are cleared: "
                "%d people named by hand, %d named automatically, %d decided to be nobody, %d exclusions. %d photos. "
                "No keyword in a photo file and no tag of the tree is touched." % (
                    c["deleted"], c["faces"], self.model, c["deleted_named"], c["deleted_decided"], c["cleared_by_hand"],
                    c["cleared_automatic"], c["cleared_nobody"], c["cleared_excluded"], c["photos"]))


@dataclass
class Chunk:
    """What one chunk's transaction committed."""
    photos: int
    deleted: int = 0
    cleared: int = 0
    #: Photos with nothing left to retire when the chunk was written: removed since, or done by another run.
    left_alone: int = 0
    change_id: Optional[int] = None


@dataclass
class Retired:
    """What `apply` committed, chunk by chunk."""
    plan: Plan
    chunks: List[Chunk] = field(default_factory=list)
    #: Photos of the chunk whose write failed, and why (the run stopped there; a run again continues).
    failed: int = 0
    error: Optional[str] = None
    #: The run was stopped between two chunks (`stop`).
    stopped: bool = False

    @property
    def deleted(self):
        return sum(chunk.deleted for chunk in self.chunks)

    @property
    def cleared(self):
        return sum(chunk.cleared for chunk in self.chunks)

    @property
    def left_alone(self):
        return sum(chunk.left_alone for chunk in self.chunks)

    @property
    def changes(self):
        found = []
        for chunk in self.chunks:
            if chunk.change_id is not None and chunk.change_id not in found:
                found.append(chunk.change_id)
        return found

    def said(self):
        """What the owner is told: what was committed, never what was planned."""
        if self.plan.refused:
            return self.plan.refused
        text = "%d faces deleted and %d faces' decisions cleared, in %d of %d photos; %d photos left alone (changed since)." % (
            self.deleted, self.cleared, sum(chunk.photos for chunk in self.chunks), len(self.plan.photo_ids),
            self.left_alone)
        if self.failed:
            text += " The write of %d photos failed (%s): nothing of them was written; run it again to continue." % (
                self.failed, self.error)
        elif self.stopped:
            text += " Stopped: run it again to continue."
        return text


def plan(library):
    """What retiring the old detector's faces of `library` would do (Plan). Reads only."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        model = store_faces.model_key(conn)
        if model != MODEL:
            return Plan(model, {}, refused=NOT_ON_THE_MODEL % (model, MODEL))
    finally:
        conn.close()
    # The switch's own check: a library more of whose faces are in photos the pass has not reached than a switch allows
    # would lose them all.
    refused, _held = library_settings.switch_to(library, model)
    if refused:
        return Plan(model, {}, refused=refused)
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return Plan(model, store_faces.retire_counts(conn, model), store_faces.retire_photos(conn, model))
    finally:
        conn.close()


def apply(library, planned, stop=_never):
    """Retire what `planned` (plan) listed, CHUNK photos a transaction, until done or `stop` says so between two chunks.
    Nothing is written for a refused plan. Returns what was committed (Retired)."""
    found = Retired(planned)
    if planned.refused:
        return found
    operation = OPERATION % planned.model
    photo_ids = planned.photo_ids
    for start in range(0, len(photo_ids), CHUNK):
        if stop():
            found.stopped = True
            break
        chunk = photo_ids[start:start + CHUNK]
        try:
            done = db.write_with_connection(
                library.path, lambda conn, chunk=chunk: _write(conn, planned.model, operation, chunk),
                label="retire faces")
        except Exception as problem:
            # Rolled back whole: the chunk's photos are as they were, and a run again continues from them.
            logger.exception("Retiring faces: the write of %d photos failed; nothing of them was written", len(chunk))
            found.failed, found.error = len(chunk), type(problem).__name__
            break
        if done is None:
            found.failed, found.error = len(chunk), MODEL_MOVED
            break
        found.chunks.append(done)
    return found


def _write(conn, model, operation, photo_ids):
    """One chunk, in one transaction: its faces read again, the deleted rows and the cleared decisions recorded, then
    written by the store's writers. What it committed (Chunk), or None, nothing written, when the library is no longer
    on `model`: its faces holding no vector under it are then not the old detector's."""
    db.begin(conn, immediate=True)
    if store_faces.model_key(conn) != model:
        return None
    rows = store_faces.retire_rows(conn, model, photo_ids)
    done = Chunk(len(photo_ids), left_alone=len(set(photo_ids) - {photo_id for _face, photo_id, _kept, _was in rows}))
    if not rows:
        return done
    gone = [face_id for face_id, _photo, kept, _was in rows if not kept]
    cleared = [(face_id, was) for face_id, _photo, kept, was in rows if kept]
    change_id = journal.newest_kept(conn, operation)
    if change_id is None:
        change_id = journal.record(conn, operation, [], {"deleted": 0, "cleared": 0})
    updates = []
    for face_id, was in cleared:
        old = {column: value for column, value in was.items() if value != store_faces.UNDECIDED[column]}
        updates.append(("faces", (face_id,), old, {column: store_faces.UNDECIDED[column] for column in old}))
    journal.record_removing(conn, change_id, store_faces.rows_of_faces(conn, gone), updates,
                            {"deleted": len(gone), "cleared": len(cleared)})
    done.deleted = store_faces.delete(conn, gone)
    done.cleared = len(store_faces.put_decisions(conn, {face_id: dict(store_faces.UNDECIDED) for face_id, _was in cleared}))
    done.change_id = change_id
    return done
