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
(settings.switch_to, the switch's own check and sentence). The names given by hand go only when the caller asks for them
by name (`refusal`, `clear_manual_names`; owner, 2026-10-22: manual names go too, but never by a run that did not say so).
`apply` walks the photos the plan listed CHUNK at a time,
each chunk ONE transaction (BEGIN IMMEDIATE) in which the chunk's faces are read again (store.faces.retire_rows): a face
done since, by this run or another, no longer matches, and a photo removed since (sync, the indexer) has no faces to
read, and is left alone. In that transaction the deleted rows are recorded whole, children first, and the cleared
decisions as updates holding the old values (store.journal.record_removing), BEFORE the store writes them, and what the
store's writers changed (rows deleted, rows whose decisions moved) is added to the change's summary after: History says
what was committed. The change, OPERATION, is opened applied
at the first chunk that writes, and added to while it is the journal's newest change and kept (journal.newest_kept): a
run stopped (`stop`, asked between chunks) or killed part-way leaves it whole, and the next run continues it; a change
recorded after it, or its undo, makes the next chunk open a new one. Undone from History, every deleted face comes back
with its crop and vectors, and every decision as it was; the journal refuses that undo once a later change wrote the same
rows (a face named since by Name faces from tags), naming the change. Two undos are refused besides, each way round
(`pass_check`, `retiring_check`): the pass's own change ("face pass (<key>)") while a retiring after it stands -- its new faces stand in
for the faces the retiring deleted, and undone first it would leave photos with no face at all under the model -- and the
retiring while a photo whose faces it deleted holds a face made since, by any writer -- the next face pass, or
Suggest, which detects again a photo it left faceless and records no change: undone, the old faces would come back
beside the new. A face made since is one whose id is above the newest the library held when the change was opened (its
summary's NEWEST_FACE; faces.id is AUTOINCREMENT).

Retiring the old model's VECTORS (owner, 2026-10-23, DECISIONS: photo_index's FaceNet vectors, 152,298 rows, 312 MB): once
every face of a library on the new model holds its vector, the vectors under FaceNet's key (OLD), which no reader keyed by
the library's model reads, are deleted -- no face, crop, decision, keyword or tag. `plan_vectors` reads only and is
refused, nothing written, for a library not on the new model and for one any face of which holds FaceNet's vector and none
of the new model's (its last vector would go: the old detector's faces are retired first). `apply_vectors` walks the
vectors by rowid, VECTOR_CHUNK a transaction (BEGIN IMMEDIATE), the library's model read again in each (MODEL_MOVED), and
the faces holding FaceNet's vector alone counted again in each (ALONE_SINCE: an undo of a retiring of the old detector's
faces between two chunks, beside the run, brings them back; their vectors are not walked into), and records ONE change of the journal with no rows, VECTORS_OPERATION, its summary the vectors and bytes deleted (what was
committed), continued across runs: the newest kept change of its name (continued), also after a change recorded since
(a setting): it holds no rows for one to conflict with, and the snapshot its first run took holds every vector. The way
back is the snapshot taken
before (the CLI forces one): its undo puts nothing back and is refused, VECTORS_RETIRED, and so is the undo of a retiring
of faces whose deleted vectors were under a key the library now holds none of -- undone, its faces would come back
holding a vector no reader reads and the library holds no other of (vectors_check, retiring_check). A switch back to such a model is
refused by the switch (settings.switch_to). The walk (walk_vectors) is the photo vectors' retiring's too
(tagpup.services.retire_photo_vectors).
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from tagpup.core import validation
from tagpup.services import face_pass
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
MODEL_MOVED = "the library's model changed since the plan"

#: What the owner is told to do after MODEL_MOVED: a run again on the other model is refused (NOT_ON_THE_MODEL).
SWITCH_BACK = "Switch the library back to %s first (its settings), then run it again to continue."

MANUAL_NAMES = ("Nothing was written: %d names given by hand would go (%d of faces kept, cleared; %d of faces deleted). "
                "They go only when asked for (TagPup CLI: retire-faces --apply --clear-manual-names).")

#: Why an undo of the pass's change is refused while a retiring after it stands (pass_check).
PASS_STANDS_IN = "change %d (retire faces) deleted the old faces this pass's new faces stand in for: undo it first"

#: Why an undo of a retiring is refused while photos it took faces from hold faces made since (retiring_check).
RETIRING_UNDER = ("%d photos this retiring took faces from have faces made since (by a face pass or Suggest): undone "
                  "now, the old faces would come back beside them")

#: The summary key of a retiring's change holding the newest face id when it was opened (store.faces.newest_id).
NEWEST_FACE = "newest_face"

#: What the plan says of the photos it leaves with no face (store.faces.retire_counts: emptied_photos, emptied_faces).
EMPTIED = ("%d photos end with no face at all (their %d faces are among those deleted): the new model's pass found no "
           "face in them, or has not reached them.")

NOT_ON_THE_MODEL = ("Nothing was written: the library's face model is %s, not %s. The old model's faces and vectors are "
                    "retired only once a library is on the new model (switch it in the library's settings after its pass).")

#: The model whose vectors are retired from a library on MODEL: FaceNet's, which made every face before the new model's pass.
OLD = validation.MTCNN_FACENET

#: The name of the change retiring a model's vectors, with its key: one change, holding no rows, its summary the counts.
VECTORS_OPERATION = "retire vectors (%s)"

#: Vectors a transaction: each holds the write lock for one read by rowid, one delete, and one bump of the faces generation
#: a vector (its trigger).
VECTOR_CHUNK = 5000

#: Why retiring the vectors is refused while a face holds the old model's vector alone (store.faces.alone_under).
ALONE = ("Nothing was written: %d faces hold a vector under %s and none under %s: its vectors retired, they would hold none, "
         "and take part in nothing. Retire the old detector's faces first (TagPup CLI: retire-faces).")

#: Why a run of apply_vectors ends at a chunk, nothing of it written: faces holding the old model's vector alone came back
#: after the plan (an undo of a retiring of the old detector's faces, from History or the MCP server, between two chunks).
ALONE_SINCE = ("The next chunk was not written: %d faces hold a vector under %s and none under %s again since the plan (a "
               "retiring of the old detector's faces was undone beside the run): its vectors retired, they would hold none. "
               "Retire the old detector's faces again first (TagPup CLI: retire-faces), then run this again to continue.")

#: Why an undo is refused once the vectors under a key are gone: the vectors-retiring's own, and a retiring of faces whose
#: deleted vectors were under it (vectors_check, retiring_check).
VECTORS_RETIRED = ("the library's vectors under %s were retired: restore the snapshot taken before (TagPup CLI: snapshots "
                   "list, snapshots restore)")


def never():
    """The `stop` of a run nobody stops."""
    return False


@dataclass
class Plan:
    """What retiring would do: the counts of store.faces.retire_counts, and the photos to walk."""
    model: str
    counts: dict
    photo_ids: List[int] = field(default_factory=list)
    #: Why it is refused, nothing written; None when it may be applied.
    refused: Optional[str] = None

    @property
    def names_by_hand(self):
        """The names given by hand the plan takes away: of the faces kept (cleared) and of the faces deleted."""
        return self.counts.get("cleared_by_hand", 0) + self.counts.get("deleted_by_hand", 0)

    def said(self):
        """What the owner is told, in counts."""
        if self.refused:
            return self.refused
        c = self.counts
        return ("%d of the library's %d faces hold no vector under %s and are deleted, with their crops and vectors "
                "(%d named automatically, %d decided by hand or excluded). Of the faces kept, the decisions are cleared: "
                "%d people named by hand, %d named automatically, %d decided to be nobody, %d exclusions. %d photos. "
                "%d names given by hand go. %s No keyword in a photo file and no tag of the tree is touched." % (
                    c["deleted"], c["faces"], self.model, c["deleted_named"], c["deleted_decided"], c["cleared_by_hand"],
                    c["cleared_automatic"], c["cleared_nobody"], c["cleared_excluded"], c["photos"], self.names_by_hand,
                    EMPTIED % (c["emptied_photos"], c["emptied_faces"])))


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
    #: Why nothing was written (refusal: the plan's own, or names given by hand not asked for); None when it ran.
    refused: Optional[str] = None

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
        if self.refused:
            return self.refused
        text = "%d faces deleted and %d faces' decisions cleared, in %d of %d photos; %d photos left alone (changed since)." % (
            self.deleted, self.cleared, sum(chunk.photos for chunk in self.chunks), len(self.plan.photo_ids),
            self.left_alone)
        if self.error == MODEL_MOVED:
            text += " %d photos were not written: %s. %s" % (self.failed, MODEL_MOVED, SWITCH_BACK % self.plan.model)
        elif self.failed:
            text += " The write of %d photos failed (%s): nothing of them was written; run it again to continue." % (
                self.failed, self.error)
        elif self.stopped:
            text += " Stopped: run it again to continue."
        return text


@dataclass
class VectorPlan:
    """What retiring the old model's vectors would do: how many vectors under `key` go, and their bytes."""
    model: str
    key: str
    vectors: int = 0
    bytes: int = 0
    #: Why it is refused, nothing written; None when it may be applied.
    refused: Optional[str] = None
    #: The change a run stopped part-way left, which this one continues (_continued), or None.
    continues: Optional[int] = None

    def said(self):
        """What the owner is told, in counts."""
        if self.refused:
            return self.refused
        return ("%d vectors under %s (%.1f MB) are deleted; every face keeps its vector under %s. No face, crop, decision, "
                "keyword or tag is touched." % (self.vectors, self.key, self.bytes / 1e6, self.model))


@dataclass
class VectorChunk:
    """What one chunk's transaction committed: the vectors and bytes deleted, the last rowid read."""
    deleted: int = 0
    bytes: int = 0
    last: int = 0
    change_id: Optional[int] = None


@dataclass
class VectorsRetired:
    """What `apply_vectors` committed, chunk by chunk."""
    plan: VectorPlan
    chunks: List[VectorChunk] = field(default_factory=list)
    #: Why the chunk under way was not written (rolled back whole; a run again continues), or None.
    error: Optional[str] = None
    stopped: bool = False
    #: Why the next chunk was refused, nothing of it written (ALONE_SINCE), or None.
    refused: Optional[str] = None

    @property
    def deleted(self):
        return sum(chunk.deleted for chunk in self.chunks)

    @property
    def bytes(self):
        return sum(chunk.bytes for chunk in self.chunks)

    @property
    def change_id(self):
        return next((chunk.change_id for chunk in self.chunks if chunk.change_id is not None), None)

    def said(self):
        """What the owner is told: what was committed, never what was planned."""
        if self.plan.refused:
            return self.plan.refused
        text = "%d vectors under %s deleted (%.1f MB), in %d chunks." % (self.deleted, self.plan.key, self.bytes / 1e6,
                                                                        len(self.chunks))
        if self.refused:
            text += " " + self.refused
        elif self.error == MODEL_MOVED:
            text += " Nothing of the next chunk was written: %s. %s" % (MODEL_MOVED, SWITCH_BACK % self.plan.model)
        elif self.error:
            text += " The next chunk's write failed (%s): nothing of it was written; run it again to continue." % self.error
        elif self.stopped:
            text += " Stopped: run it again to continue."
        return text


def plan_vectors(library):
    """What retiring the old model's (OLD) vectors of `library` would do (VectorPlan). Reads only: one scan of the
    vectors under OLD for the faces holding it alone, one for their count and bytes."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        model = store_faces.model_key(conn)
        if model != MODEL:
            return VectorPlan(model, OLD, refused=NOT_ON_THE_MODEL % (model, MODEL))
        alone = store_faces.alone_under(conn, OLD, model)
        if alone:
            return VectorPlan(model, OLD, refused=ALONE % (alone, OLD, model))
        vectors, size = store_faces.vector_total(conn, OLD)
        return VectorPlan(model, OLD, vectors, size, continues=continued(conn, VECTORS_OPERATION % OLD))
    finally:
        conn.close()


def apply_vectors(library, planned, stop=never, heard=None):
    """Delete the vectors `planned` (plan_vectors) counted, VECTOR_CHUNK a transaction, until none is left or `stop` says
    so between two chunks; `heard(chunk, chunks)` is told of each chunk committed (VectorChunk, and how many the plan
    makes). Nothing is written for a refused plan. Returns what was committed (VectorsRetired)."""
    operation = VECTORS_OPERATION % planned.key
    return walk_vectors(library, VectorsRetired(planned),
                        lambda conn, after: _write_vectors(conn, planned, operation, after), stop, heard)


def walk_vectors(library, found, write, stop=never, heard=None):
    """The walk of a retiring of a model's vectors, `found` (VectorsRetired) of its plan, VECTOR_CHUNK a transaction:
    `write(conn, after)`, on the write connection, writes the chunk above rowid `after` and answers what it committed
    (VectorChunk), or, nothing of it written, None when the library's model moved since the plan (MODEL_MOVED) or why
    the chunk was refused (a sentence). It ends at a chunk short of VECTOR_CHUNK, when `stop` says so between two
    chunks, or when a write fails (rolled back whole; a run again continues). `heard(chunk, chunks)` is told of each
    chunk committed. The face model's vectors (apply_vectors) and a photo model's
    (tagpup.services.retire_photo_vectors). Nothing is written for a refused plan. Returns `found`."""
    planned = found.plan
    if planned.refused:
        return found
    after = 0
    while True:
        if stop():
            found.stopped = True
            break
        try:
            done = db.write_with_connection(library.path, lambda conn, after=after: write(conn, after),
                                            label="retire vectors")
        except Exception as problem:
            # Rolled back whole: the chunk's vectors are as they were, and a run again continues from them.
            logger.exception("Retiring vectors: a chunk's write failed; nothing of it was written")
            found.error = type(problem).__name__
            break
        if done is None:
            found.error = MODEL_MOVED
            break
        if isinstance(done, str):
            found.refused = done
            break
        if done.deleted:
            found.chunks.append(done)
            if heard is not None:
                heard(done, max(1, -(-planned.vectors // VECTOR_CHUNK)))
        if done.deleted < VECTOR_CHUNK:
            break
        after = done.last
    return found


def _write_vectors(conn, planned, operation, after):
    """One chunk, in one transaction: the next VECTOR_CHUNK vectors under the plan's key above rowid `after`, deleted and
    added to the change's counts. What it committed (VectorChunk), or None, nothing written, when the library is no longer
    on the plan's model, or why not (ALONE_SINCE), nothing written, when a face holds the plan's key's vector alone (0.05 s
    on photo_index, 2026-10-23)."""
    db.begin(conn, immediate=True)
    if store_faces.model_key(conn) != planned.model:
        return None
    alone = store_faces.alone_under(conn, planned.key, planned.model)
    if alone:
        return ALONE_SINCE % (alone, planned.key, planned.model)
    rows = store_faces.vector_rowids_under(conn, planned.key, after, VECTOR_CHUNK)
    done = VectorChunk(last=rows[-1][0] if rows else after)
    if not rows:
        return done
    change_id = continued(conn, operation)
    if change_id is None:
        change_id = journal.record(conn, operation, [], {"deleted": 0, "bytes": 0})
    done.deleted = store_faces.delete_vectors(conn, [rowid for rowid, _size in rows])
    done.bytes = sum(size for _rowid, size in rows)
    journal.add_counts(conn, change_id, {"deleted": done.deleted, "bytes": done.bytes})
    done.change_id = change_id
    return done


def continued(conn, operation):
    """The vectors-retiring change a run continues: the newest kept change called `operation` (journal.kept_named), not
    only while it is the journal's newest -- it holds no rows, so a change recorded since conflicts with nothing -- or
    None. Asked only while vectors under its key are left (plan_vectors, _write_vectors): a run that finished left none."""
    kept = journal.kept_named(conn, operation)
    return kept[-1][0] if kept else None


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


def refusal(planned, clear_manual_names=False):
    """Why `planned` may not be applied, nothing written: the plan's own refusal, or names given by hand that would go
    without `clear_manual_names`. None when it may. What the caller asks before anything else it does for a write (the
    CLI's snapshot)."""
    if planned.refused:
        return planned.refused
    if planned.names_by_hand and not clear_manual_names:
        return MANUAL_NAMES % (planned.names_by_hand, planned.counts["cleared_by_hand"], planned.counts["deleted_by_hand"])
    return None


def apply(library, planned, stop=never, heard=None, clear_manual_names=False):
    """Retire what `planned` (plan) listed, CHUNK photos a transaction, until done or `stop` says so between two chunks;
    `heard(chunk, chunks)` is told of each chunk committed (Chunk, and how many the plan makes). Nothing is written for a
    plan `refusal` refuses: names given by hand go only with `clear_manual_names`. Returns what was committed
    (Retired)."""
    found = Retired(planned)
    found.refused = refusal(planned, clear_manual_names)
    if found.refused:
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
        if heard is not None:
            heard(done, -(-len(photo_ids) // CHUNK))
    return found


def pass_check(conn, change_id, operation, _summary):
    """Why a face pass's change ("face pass (<key>)") is not undone now, or None: a retiring under the same key after it
    stands (applied, not undone): PASS_STANDS_IN, naming the newest such. Undone, the pass would take the new model's
    vectors and faces from photos whose old faces are gone. The journal alone allows it -- each wrote other rows than the
    other did. Asked by tagpup.services.journal.why_not (UNDO_CHECKS) on the caller's connection."""
    key = _key_of(operation, face_pass.OPERATION)
    retirings = [kept_id for kept_id, _summary in journal.kept_named(conn, OPERATION % key) if kept_id > change_id]
    return PASS_STANDS_IN % retirings[-1] if retirings else None


def retiring_check(conn, change_id, _operation, summary):
    """Why a retiring of the old detector's faces ("retire faces (<key>)"), applied, is not undone now, or None:
    - its deleted vectors (its recorded face_embeddings rows) were under a key the library now holds no vector under:
      VECTORS_RETIRED, naming the key; undone, the faces would come back holding only a vector no reader reads, and the
      way back is the snapshot taken before. They are under ONE key, read at its first recorded row
      (journal.first_recorded_value; every row read was 0.15 s for change 3247's 596,845 on photo_index, 2026-10-23): a
      retiring is of a library on MODEL and deletes the faces holding no vector under it, and a face holds vectors under
      the library's models alone, which are validation.FACE_MODELS' two, so under FaceNet's;
    - a photo whose faces it deleted holds a face made since (an id above its summary's NEWEST_FACE), by any writer,
      journaled or not: RETIRING_UNDER, with how many photos; undone, the old faces would come back beside them (its
      photos read only when the library holds a face made since it).
    Asked by tagpup.services.journal.why_not (UNDO_CHECKS) after the journal's account, so only of a retiring that
    stands; the caller's connection."""
    model = journal.first_recorded_value(conn, change_id, "face_embeddings", "delete", "model")
    if model is not None and not store_faces.holds_vectors_under(conn, model):
        return VECTORS_RETIRED % model
    if store_faces.newest_id(conn) <= summary[NEWEST_FACE]:
        return None
    photos = journal.recorded_values(conn, [change_id], "faces", "delete", "photo_id").get(change_id, set())
    held = store_faces.photos_with_faces_after(conn, photos, summary[NEWEST_FACE])
    return RETIRING_UNDER % held if held else None


def vectors_check(_conn, _change_id, operation, _summary):
    """Why a retiring of a model's vectors (VECTORS_OPERATION) is not undone: never -- it holds no rows to put back, the
    vectors are gone: VECTORS_RETIRED, naming the key; the way back is the snapshot taken before. A check and not a
    sentence said before the journal's account, so a vectors' change undone already says so, as before."""
    return VECTORS_RETIRED % _key_of(operation, VECTORS_OPERATION)


def _key_of(operation, named):
    """The model key in `operation` when it is `named` ("... (%s)") of some key; else None."""
    head, tail = named.split("%s")
    if operation and operation.startswith(head) and operation.endswith(tail) and len(operation) > len(head) + len(tail):
        return operation[len(head):len(operation) - len(tail)]
    return None


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
        change_id = journal.record(conn, operation, [], {"deleted": 0, "cleared": 0,
                                                        NEWEST_FACE: store_faces.newest_id(conn)})
    updates = []
    for face_id, was in cleared:
        old = {column: value for column, value in was.items() if value != store_faces.UNDECIDED[column]}
        updates.append(("faces", (face_id,), old, {column: store_faces.UNDECIDED[column] for column in old}))
    journal.record_removing(conn, change_id, "faces", [(face_id,) for face_id in gone], updates)
    done.deleted = store_faces.delete(conn, gone)
    done.cleared = len(store_faces.put_decisions(conn, {face_id: dict(store_faces.UNDECIDED) for face_id, _was in cleared}))
    journal.add_counts(conn, change_id, {"deleted": done.deleted, "cleared": done.cleared})
    done.change_id = change_id
    return done
