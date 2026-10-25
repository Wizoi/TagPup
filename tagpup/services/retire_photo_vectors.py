"""Retiring the photo vectors of the models a library is no longer on, once it is on its new photo model (owner,
2026-10-24; docs/AI_PIPELINE.md). After the new model's pass (tagpup.services.photo_reembed, `embed-photos`) and the
switch (`set-photo-model`), a library holds every photo's vector twice: under its own key
(tagpup.services.settings.photo_key_on), which search, Suggest and the views read, and under the old model's, which no
reader keyed by the library's model reads (photo_index: 59,519 of them, 244 MB, 2026-10-24). Retiring deletes the
vectors under every key but the library's own, and the words Suggest cached under each such key's model
(tag_embeddings) -- unless the model is the library's own (an Image size change keeps the model and its weights under
a new key) -- and touches no photo, keyword, face or tag.

`plan` reads only and says, in counts, what goes (PhotoVectorPlan.said, a key each) and how many photos hold the own
key; it is refused, nothing written, while more of the library's photos than a switch allows
(settings.UNPASSED_SHARE_ALLOWED) hold no vector under its own key (NOT_COVERED): the vectors retired would be the most
complete the library holds. `apply` is the face vectors' walk (retire_faces.walk_vectors): retire_faces.VECTOR_CHUNK
vectors a transaction (BEGIN IMMEDIATE), the library's key read again in each (retire_faces.MODEL_MOVED: a switch
beside the run ends it, nothing of the chunk written), one rowless change of the journal a key,
retire_faces.VECTORS_OPERATION, continued across runs (retire_faces.continued), its summary what was committed. Its
undo is refused, retire_faces.VECTORS_RETIRED (retire_faces.vectors_check, by the operation's name): the way back is
the snapshot the CLI takes first. A switch back to a model whose vectors are retired is refused by the switch's own
gate (settings.photo_switch_on: no photo holds a vector under it), and so is the undo of the settings change that
switched away from it.
"""
from dataclasses import dataclass
from typing import Optional, Tuple

from tagpup.services import retire_faces
from tagpup.services import settings as library_settings
from tagpup.store import db, journal
from tagpup.store import embeddings as store_embeddings
from tagpup.store import photos as store_photos
from tagpup.store import taxonomy as store_taxonomy

#: Why retiring is refused while the library's own key does not cover its photos to a switch's rule.
NOT_COVERED = ("Nothing was written: %s The vectors under the other keys are retired only once all but %d%% of the "
               "photos hold one under the library's own model: switch the library to the model whose vectors it holds "
               "first (TagPup CLI: embed-photos, then set-photo-model).")


@dataclass
class PhotoVectorPlan(retire_faces.VectorPlan):
    """What retiring a key's vectors would do (VectorPlan; `model` is the library's own key), and the words Suggest
    cached under the key's model that go with them: (name, pretrained), or None when none go."""
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
class PhotoVectorChunk(retire_faces.VectorChunk):
    """What one chunk's transaction committed (VectorChunk), and the words' rows deleted with it."""
    word_rows: int = 0


@dataclass
class PhotoVectorsRetired(retire_faces.VectorsRetired):
    """What `apply` committed, chunk by chunk (VectorsRetired), with the words' rows."""

    @property
    def word_rows(self):
        return sum(chunk.word_rows for chunk in self.chunks)

    def said(self):
        text = super().said()
        if self.plan.refused or not self.plan.words:
            return text
        return text + " %d rows of words cached under %s:%s deleted." % (self.word_rows, *self.plan.words)


def plan(library):
    """(the library's own key, (photos, photos holding a vector under it), [PhotoVectorPlan of each other key, by key]).
    Reads only: one scan of the vectors' lengths (store.embeddings.key_totals)."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        own = library_settings.photo_key_on(conn)
        photos, holds = store_photos.counts(conn, own)
        refused = None
        if photos - holds > library_settings.UNPASSED_SHARE_ALLOWED * photos:
            refused = NOT_COVERED % (library_settings.PHOTOS_HELD % (holds, photos, own, photos - holds),
                                     round(library_settings.UNPASSED_SHARE_ALLOWED * 100))
        seen = {store_embeddings.model_of(own)}
        plans = []
        for key, (vectors, size) in sorted(store_embeddings.key_totals(conn).items()):
            if key == own:
                continue
            pair = store_embeddings.model_of(key)
            planned = PhotoVectorPlan(own, key, vectors, size, refused=refused,
                                      continues=retire_faces.continued(conn, retire_faces.VECTORS_OPERATION % key))
            if pair not in seen:
                seen.add(pair)
                planned.word_rows, planned.word_count = store_taxonomy.tag_embedding_counts(conn, *pair)
                planned.words = pair if planned.word_rows else None
            plans.append(planned)
        return own, (photos, holds), plans
    finally:
        conn.close()


def apply(library, planned, stop=retire_faces.never, heard=None):
    """Delete the vectors `planned` (a PhotoVectorPlan of plan's) counted, and its words, retire_faces.VECTOR_CHUNK a
    transaction, until none is left or `stop` says so between two chunks; `heard(chunk, chunks)` is told of each chunk
    committed. Nothing is written for a refused plan. Returns what was committed (PhotoVectorsRetired)."""
    operation = retire_faces.VECTORS_OPERATION % planned.key
    return retire_faces.walk_vectors(library, PhotoVectorsRetired(planned),
                                     lambda conn, after: _write(conn, planned, operation, after), stop, heard)


def _write(conn, planned, operation, after):
    """One chunk, in one transaction: the next vectors under the plan's key above rowid `after`, deleted, with the
    plan's words in the first chunk of a run, and added to the change's counts. What it committed (PhotoVectorChunk),
    or None, nothing written, when the library's key is no longer the plan's (MODEL_MOVED)."""
    db.begin(conn, immediate=True)
    if library_settings.photo_key_on(conn) != planned.model:
        return None
    rows = store_embeddings.next_under(conn, planned.key, after, retire_faces.VECTOR_CHUNK)
    done = PhotoVectorChunk(last=rows[-1][0] if rows else after)
    if not rows:
        return done
    change_id = retire_faces.continued(conn, operation)
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
