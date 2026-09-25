"""Identify Faces, read: the photos still waiting, a photo's faces and who they resemble,
the queue of people with nameless faces, a person's grid of candidates, and the faces
ruled out. TagTuner's screen, which meets the real library head on: 225,000 faces.

These were TagTuner's handlers. Each opens the library read-only, reads what the screen
needs in one go, and returns it; the fingerprint of the faces table it read under comes
back beside anything worth caching (tagpup.jobs.identify keeps the caches). Every query
kept its shape: each was tuned against that library (docs/findings.md, #45, #49, #50).

`named()` is how a read gets every named face as unit vectors -- (ids, names, matrix)
-- built once per state of the table rather than per click: reading all 35,758 of them
from SQLite to answer one click cost half a second, every click.
"""
import json
import logging
import os

import numpy as np

from tagpup.core import clustering as face_rules
from tagpup.core import dates, vocabulary
from tagpup.core.result import NotFound
from tagpup.ml import grouping
from tagpup.store import db, faces, generations, photos

logger = logging.getLogger(__name__)

#: How many unclustered faces a person's grid shows at once.
#:
#: They are capped because a person in many group photos can have tens of thousands of
#: unclustered candidates, and rendering a card for each one locks up the browser. The
#: cap keeps them reachable a screenful at a time; `has_more` tells the client there
#: are further faces behind it.
UNCLUSTERED_LIMIT = 500

# A photo's year as the Identify views show it: `photos.year`, or dates.UNKNOWN_YEAR.
shown_year = dates.shown_year


def _reading(library):
    return db.connect(db.readonly_uri(library.path), uri=True)


def _box(box_json):
    try:
        return json.loads(box_json) if box_json else []
    except Exception:
        return []


def fingerprint(library):
    """The faces table's fingerprint now (tagpup.store.faces.fingerprint)."""
    conn = _reading(library)
    try:
        return faces.fingerprint(conn)
    finally:
        conn.close()


def named_faces(library):
    """(fingerprint, (ids, names, matrix)): every face that carries a name, as unit
    vectors, with the names beside them; matrix None when nobody has been named yet.

    Not one averaged face per person: the diagnostics panel scores a candidate against
    the best single named face, and a suggestion that scored the same pair differently
    would be two numbers for one comparison on one screen. Averaging is also the more
    cautious of the two -- it drags down when somebody's named faces vary in light and
    angle, which at a cross-country meet they always do -- and that caution was costing
    real matches.

    The ids are carried so a caller can leave a particular face out of its own answer:
    the matrix is shared, so it cannot be rebuilt to exclude one row. The fingerprint is
    read first, on the same connection, so the matrix is stamped with a state no newer
    than its rows.
    """
    conn = _reading(library)
    try:
        stamp = faces.fingerprint(conn)
        ids, names, vecs = [], [], []
        for face_id, person, blob in faces.for_named_matrix(conn):
            try:
                vec = np.frombuffer(blob, dtype=np.float32)
            except Exception:
                # One damaged row is not a reason to refuse every comparison.
                continue
            norm = np.linalg.norm(vec)
            if norm == 0:
                continue
            ids.append(face_id)
            names.append(person)
            vecs.append(vec / norm)
    finally:
        conn.close()
    return stamp, (ids, names, np.vstack(vecs) if vecs else None)


# ---- The photos, and one photo ----------------------------------------------------------

def photos_waiting(library):
    """Every photo with a face still unnamed, newest first, as the sidebar lists them."""
    conn = _reading(library)
    try:
        rows = faces.photos_with_unnamed(conn)
    finally:
        conn.close()
    listed = []
    for p_path, unmatched_count, matched_count, mtime, year_taken in rows:
        listed.append({
            "path": p_path,
            "filename": os.path.basename(p_path),
            "unmatched_count": unmatched_count,
            "matched_count": matched_count,
            "mtime": mtime if mtime is not None else 0.0,
            "year": shown_year(year_taken),
            "folder": os.path.dirname(p_path),
        })
    return listed


def photo_details(library, photo_path, named):
    """One photo for the panel: its people, tags, caption and year, and each face with
    how like the closest named face it is. A photo with no row still answers, with
    nothing on it: the panel opens on any file the page names."""
    conn = _reading(library)
    try:
        photo_row = photos.details(conn, photo_path)
        face_rows = faces.in_photo_with_names(conn, photo_path)
    finally:
        conn.close()

    people, tags, caption, mtime, year_taken = [], [], None, 0.0, None
    if photo_row:
        try:
            people = json.loads(photo_row[0]) if photo_row[0] else []
        except Exception:
            people = []
        try:
            tags = json.loads(photo_row[1]) if photo_row[1] else []
        except Exception:
            tags = []
        try:
            captions = json.loads(photo_row[2]) if photo_row[2] else []
            caption = captions[0] if captions else None
        except Exception:
            caption = None
        mtime = photo_row[3] if photo_row[3] is not None else 0.0
        year_taken = photo_row[4]

    # Every named face, from the matrix shared with face_matches. This read all of
    # them from SQLite on every click of a face card -- 35,826 rows, half a second --
    # for the one number per face shown beside it.
    _known_ids, _known_names, known_matrix = named()

    found = []
    for fid, box_str, fname, emb_bytes in face_rows:
        try:
            box = json.loads(box_str)
        except Exception:
            # Clean brackets and split if stored directly
            try:
                box = [int(x) for x in box_str.replace("[", "").replace("]", "").split(",")]
            except Exception:
                box = [0, 0, 0, 0]
        max_sim = 0.0
        if emb_bytes is not None and known_matrix is not None:
            sims = np.dot(known_matrix, np.frombuffer(emb_bytes, dtype=np.float32))
            if len(sims) > 0:
                max_sim = float(np.max(sims))
        found.append({"id": fid, "box": box, "name": fname, "max_similarity": max_sim})

    return {
        "path": photo_path,
        "filename": os.path.basename(photo_path),
        "people": people,
        "tags": tags,
        "caption": caption,
        "faces": found,
        "year": shown_year(year_taken),
    }


# ---- Who a face resembles -------------------------------------------------------------------

def _embedding_of(conn, face_id):
    row = faces.embedding_row(conn, face_id)
    if not row:
        raise NotFound("Face not found")
    return np.frombuffer(row[0], dtype=np.float32)


def face_matches(library, face_id, named):
    """The five people a face most resembles, best first, each with the similarity and
    its band. Selecting a card asks this."""
    conn = _reading(library)
    try:
        target_emb = _embedding_of(conn, face_id)
    finally:
        conn.close()

    known_ids, names, embeddings_matrix = named()
    if embeddings_matrix is None:
        return []

    similarities = np.dot(embeddings_matrix, target_emb)
    sorted_indices = np.argsort(similarities)[::-1]

    # The face itself is skipped rather than excluded from the matrix, which is shared
    # and cannot be rebuilt per face; a face is not a suggestion for itself.
    top_matches, seen = [], set()
    for idx in sorted_indices:
        if known_ids[idx] == face_id:
            continue
        name = names[idx]
        if name in seen:
            continue
        seen.add(name)
        top_matches.append({
            "name": name,
            "similarity": float(similarities[idx]),
            "band": face_rules.band(float(similarities[idx])),
        })
        if len(top_matches) >= 5:
            break
    return top_matches


def unnamed_like(library, face_id):
    """{"matches": [...]}: the other nameless faces alike enough to be gathered for New
    Person without a look at each (tagpup.core.clustering.names_unasked), best first."""
    conn = _reading(library)
    try:
        target_emb = _embedding_of(conn, face_id)
        faces_rows = faces.unnamed_except(conn, face_id)
    finally:
        conn.close()
    if not faces_rows:
        return {"matches": []}

    face_ids, photo_paths, boxes, embeddings_list = [], [], [], []
    for fid, photo_path, box_json, emb_bytes in faces_rows:
        face_ids.append(fid)
        photo_paths.append(photo_path)
        boxes.append(_box(box_json))
        embeddings_list.append(np.frombuffer(emb_bytes, dtype=np.float32))

    similarities = np.dot(np.array(embeddings_list, dtype=np.float32), target_emb)
    matches = []
    for idx in np.argsort(similarities)[::-1]:
        sim = float(similarities[idx])
        if face_rules.names_unasked(sim):
            matches.append({
                "id": face_ids[idx],
                "photo_path": photo_paths[idx],
                "filename": os.path.basename(photo_paths[idx]),
                "box": boxes[idx],
                "similarity": sim,
            })
    return {"matches": matches}


# ---- One person's faces, and the faces ruled out ---------------------------------------------

def person_faces(library, name, limit=100, page=1):
    """A page of a person's faces, each scored against their closest other face of the
    years around it, never one of its own photo (tagpup.core.clustering, #71), and
    flagged when the name looks wrong. A negative limit is no limit."""
    offset = (page - 1) * limit
    conn = _reading(library)
    try:
        total_count = faces.count_named(conn, name)
        all_matched_rows = faces.person_embeddings(conn, name)
        rows = faces.person_page(conn, name, limit, offset)
    finally:
        conn.close()

    known = face_rules.KnownFaces()
    for emb_bytes, _mtime, year, photo_path in all_matched_rows:
        if emb_bytes and len(emb_bytes) > 0:
            known.add(name, np.frombuffer(emb_bytes, dtype=np.float32), year, photo_path)

    # The page's faces against the person's, in one pass (KnownFaces.likeness_many).
    likenesses = iter(known.likeness_many(name, [
        (np.frombuffer(r[5], dtype=np.float32), r[6], r[1]) for r in rows if r[5] is not None and len(r[5]) > 0]))

    found = []
    for r in rows:
        similarity = next(likenesses) if r[5] is not None and len(r[5]) > 0 else None
        found.append({
            "id": r[0],
            "photo_path": r[1],
            "filename": os.path.basename(r[1]),
            "box": _box(r[2]),
            "prob": r[3],
            "mtime": r[4] if r[4] is not None else 0.0,
            "year": shown_year(r[6]),
            # Shown as it is; whether the name looks wrong is decided here, not on the
            # page (tagpup.core.clustering.looks_wrong).
            "similarity": 1.0 if similarity is None else similarity,
            "possibly_wrong": face_rules.looks_wrong(similarity),
        })

    has_more = (offset + len(found)) < total_count if limit >= 0 else False
    return {"faces": found, "total_count": total_count, "has_more": has_more,
            "page": page, "limit": limit}


def excluded(library, default_reason):
    """Every face marked as not a person, with its reason, so exclusions can be reviewed
    and undone; `default_reason` stands in for a row that has none."""
    conn = _reading(library)
    try:
        rows = faces.excluded_for_review(conn)
    finally:
        conn.close()
    found = []
    for r in rows:
        found.append({
            "id": r[0],
            "photo_path": r[1],
            "filename": os.path.basename(r[1]) if r[1] else "",
            "box": _box(r[2]),
            "prob": r[3],
            "mtime": r[4] if r[4] is not None else 0.0,
            "year": shown_year(r[5]),
            "reason": r[6] or default_reason,
            "similarity": 0.0,
        })
    return {"faces": found, "total_count": len(found)}


# ---- The queue ---------------------------------------------------------------------------

def queue_stamp(library):
    """What the queue is cached against: the faces fingerprint and the photos generation.
    The queue groups nameless faces by the people their photo lists, which a tag saved in
    TagPup changes without touching a face (docs/findings.md, #58). Only the queue
    follows the photos; a person's grid takes up to a minute to build, and would be
    rebuilt after every photo written."""
    conn = _reading(library)
    try:
        return _queue_stamp(conn)
    finally:
        conn.close()


def _queue_stamp(conn):
    return (faces.fingerprint(conn), generations.value(conn, "photos"))


def queue(library):
    """(stamp, [{"name", "count", "unit", "photos"}]): who is waiting to be identified,
    and how many faces. Counting only -- no clustering here.

    This used to run DBSCAN over every tag group and over the whole unknown group on
    each request. On a real library that is tens of thousands of 512-dimensional
    vectors per call, which made the queue effectively unopenable. Clustering is what
    the per-person view is for; the queue only needs to know who is waiting and how
    many photos they are waiting in.
    """
    conn = _reading(library)
    try:
        stamp = _queue_stamp(conn)
        unmatched_rows = faces.identify_candidates(conn)
        matched_by_photo = faces.names_by_photo(conn)
        excluded_count = faces.count_excluded(conn)
    finally:
        conn.close()

    tag_candidates, unknown_candidates, photo_unmatched_tags = {}, [], {}
    for r in unmatched_rows:
        photo_path, people_json, embedding_length = r[1], r[2], r[3]
        # A face with no embedding cannot take part in identifying, so it is not
        # waiting for anybody and must not be counted as though it were.
        if not embedding_length:
            continue
        people = []
        if people_json:
            try:
                people = json.loads(people_json)
            except Exception:
                pass
        matched_names = matched_by_photo.get(photo_path, set())
        unmatched_tags = [p for p in people if p not in matched_names]
        if unmatched_tags:
            photo_unmatched_tags.setdefault(photo_path, set()).update(unmatched_tags)
            for tag in unmatched_tags:
                tag_candidates.setdefault(tag, []).append(photo_path)
        else:
            unknown_candidates.append(photo_path)

    tag_photos = {tag: set(candidates) for tag, candidates in tag_candidates.items()
                  if len(candidates) >= 2}

    # Tags with a single unmatched candidate cannot form a group of their own. They
    # used to vanish from the UI entirely; they are surfaced under "Ungrouped" so that
    # every nameless face stays reachable. A face only belongs there when EVERY
    # unmatched tag on its photo is a single-candidate tag -- otherwise it is already
    # reachable under the tag that does form a group. This matches the rule the detail
    # view applies, so the count shown in the queue is the number of faces it opens.
    single_candidate_tags = {t for t, c in tag_candidates.items() if len(c) == 1}
    ungrouped_photos = set()
    for tag in single_candidate_tags:
        photo_path = tag_candidates[tag][0]
        photo_tags = photo_unmatched_tags.get(photo_path, set())
        if photo_tags and photo_tags <= single_candidate_tags:
            ungrouped_photos.add(photo_path)

    # Identify Faces counts faces throughout, because a face is the unit of work here:
    # one photo of a start line holds thirty, and clearing it is thirty decisions. The
    # sidebar used to count photos for people, faces for Excluded, and photos for
    # Unknown Faces -- three units in one list, so "710 photos" sat beside "4,739
    # faces" in the panel.
    people_counts = []
    for tag, candidates in tag_candidates.items():
        if len(tag_photos.get(tag, ())) > 0:
            people_counts.append({"name": tag, "count": len(candidates), "unit": "face",
                                  "photos": len(tag_photos[tag])})
    people_counts.sort(key=lambda x: x["count"], reverse=True)

    # Unknown Faces first, then Ungrouped, as the two catch-all buckets.
    if len(unknown_candidates) > 0:
        people_counts.insert(0, {
            "name": vocabulary.BUCKETS["unknown"],
            "count": len(unknown_candidates),
            "unit": "face",
            "photos": len(set(unknown_candidates)),
        })
    if len(ungrouped_photos) > 0:
        ungrouped_faces = sum(
            len(tag_candidates[tag]) for tag in single_candidate_tags
            if tag_candidates[tag][0] in ungrouped_photos)
        people_counts.append({
            "name": vocabulary.BUCKETS["ungrouped"],
            "count": ungrouped_faces or len(ungrouped_photos),
            "unit": "face",
            "photos": len(ungrouped_photos),
        })

    # Excluded faces take no part in identifying, but the bucket has to be reachable
    # from somewhere or an exclusion could never be reviewed or undone.
    if excluded_count:
        people_counts.append({
            "name": vocabulary.BUCKETS["excluded"], "count": excluded_count, "unit": "face"})
    return stamp, people_counts


# ---- One person's grid -----------------------------------------------------------------------

def _empty_grid():
    return {"faces": [], "total_count": 0, "has_more": False}


def grid(library, name, named, on_progress=None):
    """(fingerprint, payload): the nameless faces that are candidates for `name` -- or
    for the Unknown Faces and Ungrouped buckets -- grouped by resemblance, each group
    offered the person it most resembles, and the leftovers ranked and capped.

    The slow path of the screen, the better part of a minute on a real library.
    `on_progress(stage, fraction, message)` hears each stage as it starts and, while
    grouping, how far along it is; the stages are tagpup.jobs.identify's.
    """
    report = on_progress or (lambda stage, fraction, message: None)
    report("reading", 0.0, "Reading the faces still unnamed")

    conn = _reading(library)
    try:
        stamp = faces.fingerprint(conn)
        unmatched_rows = faces.unnamed_for_matching(conn)
        matched_by_photo = faces.names_by_photo(conn) if unmatched_rows else {}
    finally:
        conn.close()
    if not unmatched_rows:
        return stamp, _empty_grid()

    # How many unmatched candidates each tag has library-wide, so "Ungrouped" can
    # recognise the tags that cannot form a group. Mirrors the queue listing.
    tag_candidate_counts = {}
    if name == vocabulary.BUCKETS["ungrouped"]:
        for r in unmatched_rows:
            try:
                r_people = json.loads(r[7] or "[]")
            except Exception:
                continue
            for tag in r_people:
                if tag not in matched_by_photo.get(r[1], set()):
                    tag_candidate_counts[tag] = tag_candidate_counts.get(tag, 0) + 1

    candidate_rows = []
    for r in unmatched_rows:
        photo_path, people_json = r[1], r[7]
        people = []
        if people_json:
            try:
                people = json.loads(people_json)
            except Exception:
                pass
        matched_names = matched_by_photo.get(photo_path, set())
        unmatched_tags = [p for p in people if p not in matched_names]

        if name == vocabulary.BUCKETS["unknown"]:
            # Photos with no unmatched tags
            if not unmatched_tags:
                candidate_rows.append(r)
        elif name == vocabulary.BUCKETS["ungrouped"]:
            # Faces whose photo names someone, but where that name has only this one
            # unmatched candidate in the whole library, so it can never form a group
            # of its own. Without this bucket these faces are unreachable.
            if unmatched_tags and all(
                    tag_candidate_counts.get(tag, 0) <= 1 for tag in unmatched_tags):
                candidate_rows.append(r)
        elif name in unmatched_tags:
            candidate_rows.append(r)

    if not candidate_rows:
        return stamp, _empty_grid()

    # A group is offered a name from the value every screen offers one from
    # (tagpup.core.clustering.is_offered): below it a suggestion is more distraction
    # than help. The number is always shown, and its band says how sure it is.
    report("reading", 0.7, "Reading the faces already named")
    _known_ids, known_names, known_matrix = named()

    def reference_faces(person):
        """Every face already named as this person; None when nobody has been named
        yet -- the ordinary case for somebody being identified for the first time, and
        the reason this cannot simply replace the keyword queue."""
        if known_matrix is None:
            return None
        rows = [i for i, n in enumerate(known_names) if n == person]
        return known_matrix[rows] if rows else None

    def suggest_for_all(centroids):
        """Who does each of these groups most resemble? Scored against the best single
        named face, matching the diagnostics panel exactly, so the badge on a card and
        the number in the panel cannot disagree.

        Answered for every group in one pass. The per-group version ran a (35,758 x
        512) matrix against one vector at a time, once per cluster: on this library
        6,151 of those, measured at 8.4s, for arithmetic BLAS does in a fraction of a
        second when handed the whole batch. Chunked, because the full product is
        groups x named faces and nobody needs all of it at once -- only its row maxima.
        """
        results = [(None, 0.0)] * len(centroids)
        if known_matrix is None or not len(centroids):
            return results
        block = np.asarray(centroids, dtype=np.float32)
        norms = np.linalg.norm(block, axis=1)
        usable = norms > 0
        block = np.where(usable[:, None], block / np.where(norms > 0, norms, 1)[:, None], block)
        chunk = 512
        for start in range(0, len(block), chunk):
            stop = min(start + chunk, len(block))
            sims = np.dot(block[start:stop], known_matrix.T)
            best = np.argmax(sims, axis=1)
            scores = sims[np.arange(stop - start), best]
            for offset in range(stop - start):
                i = start + offset
                if not usable[i]:
                    continue
                score = float(scores[offset])
                results[i] = (known_names[int(best[offset])] if face_rules.is_offered(score) else None,
                              score)
        return results

    def other_unaccounted_names(row):
        """Which other people the candidate's photo still has no face for. A photo naming
        two unaccounted people offers both its faces under both names, which is right but
        reads as noise until you are told why."""
        try:
            people = json.loads(row[7] or "[]")
        except Exception:
            return []
        matched = matched_by_photo.get(row[1], set())
        return [p for p in people if p not in matched and p != name]

    valid_rows, embs = [], []
    for r in candidate_rows:
        if r[5] and len(r[5]) > 0:
            emb = np.frombuffer(r[5], dtype=np.float32)
            norm = np.linalg.norm(emb)
            embs.append(emb / norm if norm > 0 else emb)
            valid_rows.append(r)
    if not embs:
        return stamp, _empty_grid()

    embs = np.array(embs)
    report("grouping", 0.0, "Grouping %s faces that look alike" % format(len(embs), ","))

    # Group the candidates. The slow part of this screen by a wide margin, and the
    # reason it reports progress at all (tagpup.ml.grouping).
    labels = grouping.cluster_candidates(
        embs,
        on_progress=lambda done, total: report(
            "grouping", done / total if total else 1.0,
            "Grouping %s faces that look alike" % format(total, ",")))

    # Noise (label == -1) is kept rather than discarded: those faces are real and
    # still need a name, and dropping them silently made them unreachable from
    # anywhere in the UI. They are emitted last, as singletons, so the confident
    # groups stay at the top.
    cluster_groups, noise_indices = {}, []
    for idx, label in enumerate(labels):
        if label == -1:
            noise_indices.append(idx)
        else:
            cluster_groups.setdefault(label, []).append(idx)
    sorted_labels = sorted(cluster_groups.keys(), key=lambda l: len(cluster_groups[l]), reverse=True)

    # Each cluster's centre, and how much each of its members looks like it.
    cluster_centroids, cluster_sims_by_label = [], {}
    for label in sorted_labels:
        cluster_embs = embs[cluster_groups[label]]
        centroid = np.mean(cluster_embs, axis=0)
        cnorm = np.linalg.norm(centroid)
        if cnorm > 0:
            centroid = centroid / cnorm
        cluster_centroids.append(centroid)
        cluster_sims_by_label[label] = np.dot(cluster_embs, centroid)

    # Who each group looks like, from the faces already named, for every group at
    # once. The queue itself is keyword-driven and can only offer a name the photo
    # mentions, which is no help at all for a photo naming nobody.
    report("suggesting", 0.0,
           "Working out who %s groups resemble" % format(len(cluster_centroids), ","))
    cluster_suggestions = suggest_for_all(cluster_centroids)

    def card(r, similarity, cluster_id, cluster_name, suggested, global_idx):
        suggested_name, suggested_sim = suggested
        return {
            "id": r[0],
            "photo_path": r[1],
            "filename": os.path.basename(r[1]),
            "box": _box(r[2]),
            "prob": r[3],
            "mtime": r[4] if r[4] is not None else 0.0,
            "year": shown_year(r[6]),
            "similarity": similarity,
            "cluster_id": cluster_id,
            "cluster_name": cluster_name,
            "other_names": other_unaccounted_names(r),
            "suggested_name": suggested_name,
            "suggested_similarity": round(suggested_sim, 3),
            "suggestion_strength": face_rules.band(suggested_sim) if suggested_name else None,
            "_emb_idx": global_idx,
        }

    cards = []
    for cluster_idx, label in enumerate(sorted_labels):
        cluster_sims = cluster_sims_by_label[label]
        for local_idx, global_idx in enumerate(cluster_groups[label]):
            cards.append(card(valid_rows[global_idx], float(cluster_sims[local_idx]), int(label),
                              "Cluster %d" % (cluster_idx + 1), cluster_suggestions[cluster_idx],
                              global_idx))

    # The unclustered faces, flagged so the UI can rank them lowest. Capped -- see
    # UNCLUSTERED_LIMIT. The person being sought is needed before the cap rather than
    # after it: ranking has to see every candidate to choose the strongest 500.
    unclustered_total = len(noise_indices)
    seeking = reference_faces(name)

    # Rank the whole set, then take the top of it. This used to slice the first 500
    # off an unordered list and sort those, so with 4,739 unclustered faces the 500 on
    # screen were an arbitrary sample that happened to be sorted -- the strongest
    # matches in the other 4,239 were never shown, and no amount of clearing the
    # queue reached them because the next pass sliced the same way. Scoring every
    # face first costs one matrix multiply against the named faces, which is cheaper
    # than building 4,739 cards and throwing most away.
    report("ranking", 0.0,
           "Ranking %s faces that grouped with nothing" % format(len(noise_indices), ","))
    ranked = list(noise_indices)
    if known_matrix is not None and len(ranked):
        block = embs[ranked]                          # (n, dim), already unit
        order = np.argsort(-np.max(np.dot(block, known_matrix.T), axis=1))
        ranked = [ranked[i] for i in order]
    if seeking is not None and len(ranked):
        block = embs[ranked]
        order = np.argsort(-np.max(np.dot(block, seeking.T), axis=1))
        ranked = [ranked[i] for i in order]

    report("building", 0.0, "Building the grid")

    # Each unclustered face is its own group of one, so its centroid is itself.
    shown_unclustered = ranked[:UNCLUSTERED_LIMIT]
    lone_suggestions = suggest_for_all(
        [embs[i] for i in shown_unclustered]) if shown_unclustered else []
    for position, global_idx in enumerate(shown_unclustered):
        cards.append(card(valid_rows[global_idx], 0.0, -1, "Unclustered",
                          lone_suggestions[position], global_idx))

    # Rank against the person being sought, where there is anything to rank against.
    # Until now `similarity` meant similarity to a face's own cluster centroid -- a
    # number about the crowd it arrived with, not about this person -- so a hundred
    # candidates came back in no useful order. Ranked, not filtered: two candidates
    # can both be genuinely this person in different photos (measured here: 0.826 and
    # 0.822 for one), so a cutoff would discard a real match to tidy the list.
    if seeking is not None:
        for face in cards:
            # Against the best of this person's faces, not their average: a candidate
            # matching any one of them well is a candidate worth looking at, and the
            # panel scores it the same way.
            face["person_similarity"] = round(
                float(np.max(np.dot(seeking, embs[face.pop("_emb_idx")]))), 3)
            face["band"] = face_rules.band(face["person_similarity"])
        cards.sort(key=lambda x: x["person_similarity"], reverse=True)
    else:
        # Nobody named yet: how like its own group a face is, in the same bands.
        for face in cards:
            face.pop("_emb_idx", None)
            face["band"] = face_rules.band(face["similarity"])
        cards.sort(key=lambda x: x["similarity"], reverse=True)

    return stamp, {
        "faces": cards,
        "total_count": len(cards),
        "unclustered_total": unclustered_total,
        "unclustered_shown": min(unclustered_total, UNCLUSTERED_LIMIT),
        "has_more": unclustered_total > UNCLUSTERED_LIMIT,
        "page": 1,
        "limit": -1,
    }
