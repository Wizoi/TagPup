"""Retiring the photo vectors of the models a library is no longer on, once it is on its new photo model (owner,
2026-10-24; docs/AI_PIPELINE.md). After the new model's pass (tagpup.services.photo_reembed, `embed-photos`) and the
switch (`set-photo-model`), a library holds every photo's vector twice: under its own key
(tagpup.services.settings.photo_key_on), which search, Suggest and the views read, and under the old model's, which no
reader keyed by the library's model reads (photo_index: 59,519 of them, 244 MB, 2026-10-24). Retiring deletes the
vectors under every key but the library's own, and the words Suggest cached under each such key's model
(tag_embeddings) -- unless the model is the library's own (an Image size change keeps the model and its weights under
a new key) -- and touches no photo, keyword, face or tag.

`plan` reads only and says, in counts (PhotoVectorPlan.said, a key each), what goes and how many photos hold the own
key; it is refused, nothing written, while more of the library's photos than a switch allows
(settings.UNPASSED_SHARE_ALLOWED) hold no vector under its own key (NOT_COVERED): the vectors retired would be the most
complete the library holds. `apply` walks a key's vectors by rowid (walk_vectors), VECTOR_CHUNK a transaction (BEGIN
IMMEDIATE), the library's key read again in each (MODEL_MOVED: a switch beside the run ends it, nothing of the chunk
written), and records one change of the journal a key, holding no rows, VECTORS_OPERATION, its summary what was
committed, continued across runs (continued). Its undo is refused, VECTORS_RETIRED (vectors_check, by the operation's
name; DECISIONS 2026-10-23: a rowless change whose way back is the snapshot the CLI takes first). The live journals
also hold one such change of the old face model's vectors each (`retire vectors (mtcnn-facenet)`: photo_index 3248,
kr-track 348, renton_parkrun 1226, counted 2026-10-25), made by a command since deleted under the same name, refused
the same way. A switch back to a model whose vectors are retired is refused by the switch's own gate
(settings.photo_switch_on: no photo holds a vector under it), and so is the undo of the settings change that switched
away from it.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from tagpup.services import settings as library_settings
from tagpup.store import db, journal
from tagpup.store import embeddings as store_embeddings
from tagpup.store import photos as store_photos
from tagpup.store import taxonomy as store_taxonomy

logger = logging.getLogger(__name__)

#: Why retiring is refused while the library's own key does not cover its photos to a switch's rule.
NOT_COVERED = ("Nothing was written: %s The vectors under the other keys are retired only once all but %d%% of the "
               "photos hold one under the library's own model: switch the library to the model whose vectors it holds "
               "first (TagPup CLI: embed-photos, then set-photo-model).")

#: The name of the change retiring a model's vectors, with its key: one change, holding no rows, its summary the counts.
VECTORS_OPERATION = "retire vectors (%s)"

#: Vectors a transaction: each holds the write lock for one read by rowid and one delete (and the words, in a run's
#: first).
VECTOR_CHUNK = 5000

#: Why a run ends at a chunk: the library was switched to another model after the plan (settings, beside the run).
MODEL_MOVED = "the library's model changed since the plan"

#: What the owner is told to do after MODEL_MOVED.
SWITCH_BACK = "Switch the library back to %s first (its settings), then run it again to continue."

#: Why an undo of a retiring of a model's vectors is refused (vectors_check): the vectors under the key are gone.
VECTORS_RETIRED = ("the library's vectors under %s were retired: restore the snapshot taken before (TagPup CLI: snapshots "
                   "list, snapshots restore)")


def never():
    """The `stop` of a run nobody stops."""
    return False


@dataclass
class PhotoVectorPlan:
    """What retiring a key's vectors would do: how many vectors under `key` go, and their bytes (`model` is the
    library's own key), and the words Suggest cached under the key's model that go with them: (name, pretrained), or
    None when none go."""
    model: str
    key: str
    vectors: int = 0
    bytes: int = 0
    #: Why it is refused, nothing written; None when it may be applied.
    refused: Optional[str] = None
    #: The change a run stopped part-way left, which this one continues (continued), or None.
    continues: Optional[int] = None
    words: Optional[Tuple[str, str]] = None
    word_rows: int = 0
    word_count: int = 0

    def said(self):
        """What the owner is told, in counts."""
        if self.refused:
            return self.refused
        text = "%d vectors under %s (%.1f MB) are deleted." % (self.vectors, self.key, self.bytes / 1e6)
        if self.words:
            text += " %d words Suggest cached under %s:%s (%d rows) are deleted." % (
                self.word_count, self.words[0], self.words[1], self.word_rows)
        return text + " No photo, keyword, face or tag is touched."


@dataclass
class PhotoVectorChunk:
    """What one chunk's transaction committed: the vectors and bytes deleted, the last rowid read, and the words' rows
    deleted with it."""
    deleted: int = 0
    bytes: int = 0
    last: int = 0
    change_id: Optional[int] = None
    word_rows: int = 0


@dataclass
class PhotoVectorsRetired:
    """What `apply` committed, chunk by chunk."""
    plan: PhotoVectorPlan
    chunks: List[PhotoVectorChunk] = field(default_factory=list)
    #: Why the chunk under way was not written (rolled back whole; a run again continues), or None.
    error: Optional[str] = None
    stopped: bool = False

    @property
    def deleted(self):
        return sum(chunk.deleted for chunk in self.chunks)

    @property
    def bytes(self):
        return sum(chunk.bytes for chunk in self.chunks)

    @property
    def word_rows(self):
        return sum(chunk.word_rows for chunk in self.chunks)

    @property
    def change_id(self):
        return next((chunk.change_id for chunk in self.chunks if chunk.change_id is not None), None)

    def said(self):
        """What the owner is told: what was committed, never what was planned."""
        if self.plan.refused:
            return self.plan.refused
        text = "%d vectors under %s deleted (%.1f MB), in %d chunks." % (self.deleted, self.plan.key, self.bytes / 1e6,
                                                                        len(self.chunks))
        if self.error == MODEL_MOVED:
            text += " Nothing of the next chunk was written: %s. %s" % (MODEL_MOVED, SWITCH_BACK % self.plan.model)
        elif self.error:
            text += " The next chunk's write failed (%s): nothing of it was written; run it again to continue." % self.error
        elif self.stopped:
            text += " Stopped: run it again to continue."
        if self.plan.words:
            text += " %d rows of words cached under %s:%s deleted." % (self.word_rows, *self.plan.words)
        return text


def plan(library):
    """(the library's own key, (photos, photos holding a vector under it), [PhotoVectorPlan of each other key, by key]).
    Reads only: one scan of the vectors' lengths (store.embeddings.key_totals)."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        own = library_settings.photo_key_on(conn)
        photos, holds = store_photos.counts(conn, own)
        refused = None
        if not library_settings.covers(photos, holds):
            refused = NOT_COVERED % (library_settings.PHOTOS_HELD % (holds, photos, own, photos - holds),
                                     round(library_settings.UNPASSED_SHARE_ALLOWED * 100))
        seen = {store_embeddings.model_of(own)}
        plans = []
        for key, (vectors, size) in sorted(store_embeddings.key_totals(conn).items()):
            if key == own:
                continue
            pair = store_embeddings.model_of(key)
            planned = PhotoVectorPlan(own, key, vectors, size, refused=refused,
                                      continues=continued(conn, VECTORS_OPERATION % key))
            if pair not in seen:
                seen.add(pair)
                planned.word_rows, planned.word_count = store_taxonomy.tag_embedding_counts(conn, *pair)
                planned.words = pair if planned.word_rows else None
            plans.append(planned)
        return own, (photos, holds), plans
    finally:
        conn.close()


def apply(library, planned, stop=never, heard=None):
    """Delete the vectors `planned` (a PhotoVectorPlan of plan's) counted, and its words, VECTOR_CHUNK a transaction,
    until none is left or `stop` says so between two chunks; `heard(chunk, chunks)` is told of each chunk committed.
    Nothing is written for a refused plan. Returns what was committed (PhotoVectorsRetired)."""
    operation = VECTORS_OPERATION % planned.key
    return walk_vectors(library, PhotoVectorsRetired(planned), lambda conn, after: _write(conn, planned, operation, after),
                        stop, heard)


def walk_vectors(library, found, write, stop=never, heard=None):
    """The walk of a retiring of a key's vectors, `found` (PhotoVectorsRetired) of its plan, VECTOR_CHUNK a transaction:
    `write(conn, after)`, on the write connection, writes the chunk above rowid `after` and answers what it committed
    (PhotoVectorChunk), or, nothing of it written, None when the library's model moved since the plan (MODEL_MOVED). It
    ends at a chunk short of VECTOR_CHUNK, when `stop` says so between two chunks, or when a write fails (rolled back
    whole; a run again continues). `heard(chunk, chunks)` is told of each chunk committed. Nothing is written for a
    refused plan. Returns `found`."""
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
        if done.deleted:
            found.chunks.append(done)
            if heard is not None:
                heard(done, max(1, -(-planned.vectors // VECTOR_CHUNK)))
        if done.deleted < VECTOR_CHUNK:
            break
        after = done.last
    return found


def _write(conn, planned, operation, after):
    """One chunk, in one transaction: the next vectors under the plan's key above rowid `after`, deleted, with the
    plan's words in the first chunk of a run, and added to the change's counts. What it committed (PhotoVectorChunk),
    or None, nothing written, when the library's key is no longer the plan's (MODEL_MOVED)."""
    db.begin(conn, immediate=True)
    if library_settings.photo_key_on(conn) != planned.model:
        return None
    rows = store_embeddings.next_under(conn, planned.key, after, VECTOR_CHUNK)
    done = PhotoVectorChunk(last=rows[-1][0] if rows else after)
    if not rows:
        return done
    change_id = continued(conn, operation)
    if change_id is None:
        change_id = journal.record(conn, operation, [], {"deleted": 0, "bytes": 0, "words": 0})
    done.deleted = store_embeddings.delete_between(conn, planned.key, after, done.last)
    done.bytes = sum(size for _rowid, size in rows)
    if planned.words and not after:
        # Once a run, in its first chunk: a run continuing a stopped one finds them gone, and deletes none.
        done.word_rows = store_taxonomy.forget_model_tag_embeddings(conn, *planned.words)
    journal.add_counts(conn, change_id, {"deleted": done.deleted, "bytes": done.bytes, "words": done.word_rows})
    done.change_id = change_id
    return done


def continued(conn, operation):
    """The vectors-retiring change a run continues: the newest kept change called `operation` (journal.kept_named), not
    only while it is the journal's newest -- it holds no rows, so a change recorded since conflicts with nothing -- or
    None. Asked only while vectors under its key are left (plan, _write): a run that finished left none."""
    kept = journal.kept_named(conn, operation)
    return kept[-1][0] if kept else None


def vectors_check(_conn, _change_id, operation, _summary):
    """Why a retiring of a model's vectors (VECTORS_OPERATION) is not undone: never -- it holds no rows to put back, the
    vectors are gone: VECTORS_RETIRED, naming the key; the way back is the snapshot taken before. A check and not a
    sentence said before the journal's account, so a vectors' change undone already says so. Asked by
    tagpup.services.journal.why_not (UNDO_CHECKS)."""
    return VECTORS_RETIRED % key_of(operation, VECTORS_OPERATION)


def key_of(operation, named):
    """The model key in `operation` when it is `named` ("... (%s)") of some key; else None."""
    head, tail = named.split("%s")
    if operation and operation.startswith(head) and operation.endswith(tail) and len(operation) > len(head) + len(tail):
        return operation[len(head):len(operation) - len(tail)]
    return None
