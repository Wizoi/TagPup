"""What TagTuner keeps of Identify Faces between requests, per library: the grids and
the queue, the matrix of named faces, and how far along a grid being built has got.

Clustering a person's unmatched candidates is expensive (tens of thousands of
512-dimensional vectors for a large library), and the answer only changes when faces
are added or named, so it is cached against a cheap fingerprint of the faces table
rather than recomputed per request (tagpup.store.faces.fingerprint). The reads are
tagpup.services.identify's; this decides when to read again.

One GridCache and one BuildProgress per library, held by the web layer
(tagpup.core.per_library.PerLibrary) and handed in. The server kept them in dicts keyed by a
thread-local "active library" (docs/findings.md, #44).
"""
import collections
import threading
import time

from tagpup.services import identify

#: Where faces are shown once they have grouped with nothing, and the cap on them.
UNCLUSTERED_LIMIT = identify.UNCLUSTERED_LIMIT

#: The stages of building a person's grid, and roughly what share of the wait each one
#: is. Taken from measuring the real library, where the whole pass is about a minute:
#: reading the candidates is a second or two, grouping them is most of it, and ranking
#: the leftovers against the person is the next biggest piece.
#:
#: They only have to be close. Their job is to keep the bar moving forward at a
#: believable rate, not to predict the finish.
BUILD_STAGES = (
    ("reading", 0.06),
    ("grouping", 0.70),
    ("suggesting", 0.04),
    ("ranking", 0.14),
    ("building", 0.06),
)


class BuildProgress:
    """How far along each grid being built has got, so the screen can say so instead of
    sitting blank for a minute. Written by the request doing the work and read by a
    status request on another thread. Keyed by person, because two people's grids can
    be built at once."""

    def __init__(self):
        self._by_name = {}

    def report(self, name, stage, fraction, message):
        """`fraction` is progress within the stage, 0 to 1. The overall figure comes
        from the stage weights, so it only ever moves forward."""
        done = 0.0
        for stage_name, weight in BUILD_STAGES:
            if stage_name == stage:
                done += weight * max(0.0, min(1.0, fraction))
                break
            done += weight
        self._by_name[name] = {
            "name": name,
            "stage": stage,
            "message": message,
            "percent": int(round(done * 100)),
            "active": True,
            "updated": time.time(),
        }

    def done(self, name):
        """However a build ended, nothing is being built for this person any more.
        Left behind, a stale entry would keep a progress bar on screen for good."""
        self._by_name.pop(name, None)

    def of(self, name):
        """The progress the status route answers with: what was reported, or idle."""
        return self._by_name.get(name) or {"name": name, "active": False, "percent": 0}


class GridCache:
    """The cached Identify Faces answers of one library, each stamped with the
    fingerprint of the faces table it was read under: "queue", "named_matrix",
    "unnamed_faces" (New Person's) and "matches:<name>" for each grid."""

    def __init__(self):
        self._entries = {}
        self._lock = threading.Lock()

    def get(self, key, fingerprint):
        with self._lock:
            entry = self._entries.get(key)
        if entry and entry.get("fingerprint") == fingerprint:
            return entry.get("value")
        return None

    def put(self, key, fingerprint, value):
        with self._lock:
            self._entries[key] = {"fingerprint": fingerprint, "value": value}

    def entry(self, key):
        """The entry itself, {"fingerprint", "value"}, or None: for tests of what a
        write leaves stamped."""
        with self._lock:
            return self._entries.get(key)

    def keys(self):
        with self._lock:
            return list(self._entries)

    def clear(self):
        with self._lock:
            self._entries.clear()

    def drop(self, key):
        """Let go of one entry, before what replaces it is read."""
        with self._lock:
            self._entries.pop(key, None)

    def forget_faces(self, face_ids, expected_fingerprint, fingerprint_after):
        """Take faces out of the cached grids instead of discarding them.

        The per-person grid costs about fifty seconds to build on a real library,
        almost all of it DBSCAN over a hundred thousand candidates. It is cached
        against a fingerprint of the faces table -- which moves the instant anything
        is named or excluded, so every assignment and every ignored cluster threw the
        whole grid away and the next click paid for it again. That is the wrong shape
        for what actually happened: naming ten faces does not change what the other
        hundred thousand look like, it removes ten cards.

        So a removal is applied to the cached payloads, which are then re-stamped with
        `fingerprint_after`. Removals only -- a restored or unnamed face comes back
        into the pool with no cluster to belong to, and working out where it lands is
        the clustering pass itself, so those still invalidate.

        Two things are deliberately left to rebuild on their own: the queue listing,
        now 1.4s, and the named-face matrix, which genuinely changes when somebody is
        named. The suggestions already on the remaining cards keep the scores they
        were drawn with until the next full build; they were computed against a set
        of named faces that has since grown by the handful just assigned, which moves
        a score in the third decimal and never changes which card is in front of you.

        Both fingerprints come from the write itself, read inside its transaction
        (tagpup.store.faces.accounted_write): `expected_fingerprint` as it began and
        `fingerprint_after` just before its commit, so nothing another process wrote
        meanwhile is stamped as accounted for. An entry another write has re-stamped
        no longer carries `expected_fingerprint`, and is left to be rebuilt.
        """
        removed = {int(fid) for fid in face_ids}
        if not removed:
            return

        # New Person's pool loses the faces too: its rows are masked, not read again
        # (docs/findings.md, #5). Only from the state the write began at, as below.
        pool = self.entry("unnamed_faces")
        if pool and pool.get("fingerprint") == expected_fingerprint:
            self.put("unnamed_faces", fingerprint_after, pool["value"].without(removed))

        for key in self.keys():
            if not key.startswith("matches:"):
                continue
            entry = self.entry(key)
            value = entry.get("value") if entry else None
            if not isinstance(value, dict) or not isinstance(value.get("faces"), list):
                continue

            # Only an entry describing the table as it was a moment ago can be carried
            # forward. An older one was built before something this code knows nothing
            # about changed the pool -- a folder removed, a batch of photos indexed,
            # faces restored -- and those change what the clustering would say, not
            # merely which cards to drop. Left alone, it stays stamped with a
            # fingerprint that no longer matches and is rebuilt on the next request,
            # which is the right answer. Re-stamping it would revive it.
            if entry.get("fingerprint") != expected_fingerprint:
                continue

            faces = value["faces"]
            kept = [f for f in faces if f.get("id") not in removed]
            if len(kept) == len(faces):
                # This person's grid is untouched, but the table moved. Re-stamp it so
                # it stays usable rather than being rebuilt for somebody else's edit.
                self.put(key, fingerprint_after, value)
                continue

            # Survivors of a group that has lost all but one member are no longer a
            # group, and the pile they belong in is the unclustered one.
            stranded_before = sum(1 for f in kept if f.get("cluster_id") == -1)
            kept = dissolve_stranded_clusters(kept)
            shown_unclustered = sum(1 for f in kept if f.get("cluster_id") == -1)
            newly_stranded = shown_unclustered - stranded_before

            gone_unclustered = sum(
                1 for f in faces if f.get("id") in removed and f.get("cluster_id") == -1)
            # Faces that just fell out of a group join the unclustered pool, so they
            # count towards its total as well as towards what is on screen.
            total_unclustered = max(
                0, int(value.get("unclustered_total") or 0) - gone_unclustered + newly_stranded)

            # Unclustered faces are capped, so there can be more waiting behind the
            # ones on screen, and thinning the visible end without bringing the next
            # ones forward shrinks the grid towards empty while faces still need a
            # name. Only a removal that actually took unclustered faces can do that --
            # ignoring a cluster leaves the tail exactly as it was -- and even then it
            # is worth letting the tail wear down before paying for a rebuild, because
            # the rebuild is the whole minute this exists to avoid.
            #
            # An earlier version of this rule asked only whether more faces were
            # waiting, which is true from the moment the grid is built, so every
            # removal rebuilt and the cache never once got used.
            wearing_thin = shown_unclustered < UNCLUSTERED_LIMIT // 2
            if gone_unclustered and total_unclustered > shown_unclustered and wearing_thin:
                with self._lock:
                    self._entries.pop(key, None)
                continue

            updated = dict(value)
            updated["faces"] = kept
            updated["total_count"] = len(kept)
            updated["unclustered_total"] = total_unclustered
            updated["unclustered_shown"] = shown_unclustered
            updated["has_more"] = total_unclustered > shown_unclustered
            self.put(key, fingerprint_after, updated)


def dissolve_stranded_clusters(faces):
    """A cluster that has lost all but one member is not a cluster any more.

    Grouping needs `min_samples` faces that resemble each other -- two, here. Take
    faces out of a group and the survivors can fall below that, and re-running the
    clustering is exactly what the cached payload exists to avoid. Measured on this
    library: taking a tenth of the clustered faces out stranded 139 groups this way.

    It matters because of what the screen offers. A group is drawn with its own
    heading and a button that assigns every face under it to one person in a click;
    the Unclustered pile is drawn with a warning that these resembled nothing and
    have to be handled one at a time. A survivor left flying its old group's colours
    would get the first treatment while being, by the rule that built the group, the
    second thing.

    Only shrinking is possible, which is what makes this safe to do by hand. Removing
    faces can lower a neighbour count but never raise one, so a face that was not
    dense enough to anchor a group cannot become dense enough -- groups split, shrink
    and dissolve, and never merge or gain a member. Measured across removals of 10%,
    30% and 50% of the clustered faces: not one merge, and not one face pulled in from
    the unclustered pile.
    """
    survivors = collections.Counter(
        f.get("cluster_id") for f in faces if f.get("cluster_id") != -1)
    stranded = {cid for cid, count in survivors.items() if count < 2}
    if not stranded:
        return faces

    dissolved = []
    for face in faces:
        if face.get("cluster_id") in stranded:
            face = dict(face)
            face["cluster_id"] = -1
            face["cluster_name"] = "Unclustered"
            # Resemblance to the centroid of a group that no longer exists.
            face["similarity"] = 0.0
        dissolved.append(face)
    return dissolved


# ---- The reads, cached -------------------------------------------------------------------

def named_faces(library, cache):
    """(ids, names, matrix) of every named face (tagpup.services.identify.named_faces),
    built once per state of the faces table rather than once per request. Naming or
    excluding a face moves the fingerprint and the matrix is rebuilt; nothing else
    disturbs it."""
    cached = cache.get("named_matrix", identify.fingerprint(library))
    if cached is not None:
        return cached
    stamp, value = identify.named_faces(library)
    cache.put("named_matrix", stamp, value)
    return value


def unnamed_faces(library, cache):
    """Every nameless face in play (tagpup.services.identify.UnnamedFaces), read once per
    state of the faces table rather than each time New Person opens; naming or excluding
    faces masks them out (GridCache.forget_faces), anything else reads them again."""
    cached = cache.get("unnamed_faces", identify.fingerprint(library))
    if cached is not None:
        return cached
    # The stale pool goes first: holding it through the read was another 185 MB.
    cache.drop("unnamed_faces")
    stamp, value = identify.unnamed_faces(library)
    cache.put("unnamed_faces", stamp, value)
    return value


def queue(library, cache):
    """The Identify Faces queue (tagpup.services.identify.queue), cached against the
    faces fingerprint and the photos generation."""
    cached = cache.get("queue", identify.queue_stamp(library))
    if cached is not None:
        return cached
    stamp, value = identify.queue(library)
    cache.put("queue", stamp, value)
    return value


def grid(library, cache, progress, name):
    """A person's grid (tagpup.services.identify.grid), from the cache when the faces
    table has not moved since it was built; else built, its progress published under
    `name` from here until it is done, and cached."""
    key = "matches:%s" % name
    cached = cache.get(key, identify.fingerprint(library))
    if cached is not None:
        return cached
    # Nothing was cached, so this is the slow path: reading every candidate and
    # grouping it. Say so, from here until the answer goes out.
    try:
        stamp, value = identify.grid(
            library, name, lambda: named_faces(library, cache),
            on_progress=lambda stage, fraction, message: progress.report(name, stage, fraction, message))
    finally:
        progress.done(name)
    cache.put(key, stamp, value)
    return value
