"""Who is who across a library: its faces clustered, and each cluster named from the
people its photos' keywords name and the names given by hand.

The rules each step applies -- how alike two faces must be, which faces are background,
how a person's closest face of the years around a photo is found -- are
tagpup.core.clustering's. This is the order they are applied in, over one library's
faces, and the names it writes back. It was FaceProcessor.cluster_and_resolve_identities
in scripts/faces.py, beside the face models it never used (docs/ARCHITECTURE.md, "The
layers, revisited").

How each face was named is recorded in the library's trace file (resolution_trace_path).

**People are IDS while it works**: the node each face carries, each photo lists (photo_people) and each keyword's path is, and
they are written as those ids: a person renamed meanwhile is the same person, one merged away meanwhile is nobody
(store_faces.set_names). Two people who share a leaf under two groups are two people here too, each guessed on their own photos
(docs/findings.md, #1357: keyed by name, the two were pooled into one and neither was ever guessed). A face a person decided by
hand is never written at all (docs/ARCHITECTURE.md, "People by id, stage 2"). The names are looked up only for what the run
tells: its trace and its summary.
"""
import collections
import json
import logging

import numpy as np
from tqdm import tqdm

from tagpup.core import clustering, dates, paths
from tagpup.core import vocabulary as tag_vocabulary
from tagpup.core.library import Library
from tagpup.store import person_ids
from tagpup.store import faces as store_faces
from tagpup.store import people as store_people
from tagpup.store import taxonomy as store_taxonomy

logger = logging.getLogger("tagpup_cli.faces")


def resolution_trace_path(db_path):
    """Where clustering records how it named each face, for this library alone.

    It was face_resolution_trace.json beside the database -- one file for every
    library in the folder, so clustering one overwrote the record of another.
    """
    return Library(db_path).face_trace_file


def _all_faces(photo_index):
    """Every face's id, photo, box, vector, person (the Ref it carries, and its id) and probability; [] if they cannot be read."""
    if photo_index.conn is None:
        return []
    try:
        return [{
            "id": face_id,
            "photo_path": photo_path,
            "box": json.loads(box_json),
            "embedding": np.frombuffer(emb_bytes, dtype=np.float32),
            "person": person,
            "person_id": person.id if person else None,
            "prob": prob,
        } for face_id, photo_path, box_json, emb_bytes, person, prob in store_faces.for_clustering(photo_index.conn)]
    except Exception as e:
        logger.error(f"Error retrieving faces: {e}")
        return []


def _manual_face_names(photo_index):
    """face_id -> the person's id for every face a person decided by hand. A manual entry of None is a deliberate
    "this is nobody I want labelled", and as binding."""
    if photo_index.conn is None:
        return {}
    try:
        return {face_id: person.id if person else None
                for face_id, person in store_faces.manual_names(photo_index.conn).items()}
    except Exception as e:
        logger.warning(f"Could not read manual face names: {e}")
        return {}


def _excluded_face_ids(photo_index):
    """Faces marked as not-a-person, which must not influence identity resolution."""
    if photo_index.conn is None:
        return set()
    try:
        return store_faces.excluded_ids(photo_index.conn)
    except Exception as e:
        logger.warning(f"Could not read excluded faces: {e}")
        return set()


def _save_face_names(photo_index, face_updates, as_read):
    """Write the people resolved, (the person's id or None, face id) each. A face is written only while it is still what was
    read (`as_read`: {face id: the person's id it carried}, store_faces.set_names): one a person named meanwhile keeps its
    name. Returns (the ids of the faces written, how many were left, {face id: the tag of the person written} of those)."""
    if photo_index.conn is None or not face_updates:
        return [], 0, {}
    try:
        conn = photo_index.conn
        wanted = {face_id: person for person, face_id in face_updates}
        written, left = store_faces.set_names(conn, wanted, as_read)
        tags = person_ids.read(conn).by_id   # in the write's transaction: each person written is a node of it
        given = {face_id: tags[wanted[face_id]].tag for face_id in written if wanted[face_id] is not None}
        conn.commit()
        return written, len(left), given
    except Exception as e:
        logger.error(f"Error updating face names: {e}")
        photo_index.conn.rollback()
        raise e


#: How many clusters, photos or faces resolve goes through between two calls of its `on_step`.
STEP = 2000


#: How many rounds the names propagate through the photos' people before the last matching.
MAX_ITERATIONS = 5


def resolve(photo_index, on_step=None):
    """Cluster a library's faces with DBSCAN, name each cluster from the people its photos'
    tags name, and write them back; returns {the tag of each person: faces it newly named}.

    `photo_index` is the library's, loaded (tagpup.services.search.PhotoIndex): its
    connection, its photos' metadata and its path. Names given by hand are never
    overwritten, and excluded faces take no part.

    `on_step(stage, done, total)`, if given, hears where it has got -- "reading", "grouping" (one call of
    DBSCAN: no count inside it), "voting", "propagating", "matching" and "saving", then "unpaired" (total: the faces
    left unnamed because their photo names people no face is known for and nothing tells which is whose, #1374, #1454;
    done: how many of them carried a guess this run took back, #1455) and "left" (done: the faces not written because
    the library changed under the run: a person named the face, or another face of its photo, or merged the person
    away) -- and may raise to stop it. Every stage before "saving" only reads; the names are written in one commit
    after it is announced, so a stop is nothing written or all of it, never part (the job behind the apps' button,
    tagpup.jobs.naming_faces).
    """
    step = on_step or (lambda stage, done=0, total=1: None)
    logger.info("Starting self-tuning face identity resolution...")
    step("reading", 0, 1)
    all_faces = _all_faces(photo_index)
    if not all_faces:
        logger.info("No face embeddings found in the index.")
        return {}

    # Faces marked as not-a-person are dropped before anything else runs. They must
    # not cluster, vote, seed a centroid, or be offered as a candidate -- the whole
    # point is to stop crowd noise and bad crops polluting identity resolution.
    excluded_ids = _excluded_face_ids(photo_index)
    if excluded_ids:
        before = len(all_faces)
        all_faces = [f for f in all_faces if f["id"] not in excluded_ids]
        logger.info(f"Excluding {before - len(all_faces)} face(s) marked as not-a-person.")
        if not all_faces:
            logger.info("Every face is excluded; nothing to resolve.")
            return {}

    # Faces a person decided by hand. These are never overwritten here: re-clustering
    # re-derives every name from scratch, and without this a single run silently
    # discards every correction made in TagTuner. They are also the most reliable
    # evidence available, so they seed the anchor set rather than merely surviving.
    manual_names = _manual_face_names(photo_index)
    if manual_names:
        logger.info(
            f"Preserving {len(manual_names)} manually assigned face names "
            f"({sum(1 for v in manual_names.values() if v)} named, "
            f"{sum(1 for v in manual_names.values() if not v)} deliberately cleared)."
        )

    # Prepare embeddings for clustering
    step("grouping", 0, 1)
    embeddings = np.array([f["embedding"] for f in all_faces], dtype=np.float32)
    
    # DBSCAN parameters:
    # Since embeddings are L2 normalized, Cosine Distance = 0.5 * (Euclidean Distance)^2.
    # A cosine similarity cutoff of 0.85 equals a cosine distance of 0.15.
    # Euclidean eps = sqrt(2 * 0.15) = sqrt(0.3) ≈ 0.547.
    # Euclidean, at clustering's radius (tagpup.core.clustering.GROUPING, as a
    # distance). min_samples=1 so single faces can form their own cluster.
    try:
        # pyrefly: ignore [missing-import] The codebase is written defensively to support both GPU and CPU execution. It wraps the import in a standard Python try/except ImportError block
        from cuml.cluster import DBSCAN as cuDBSCAN
        db = cuDBSCAN(eps=clustering.distance(clustering.GROUPING), min_samples=1, metric='euclidean')
        labels = db.fit_predict(embeddings)
        logger.info("Using GPU-accelerated cuML DBSCAN for clustering.")
    except ImportError:
        # Fallback to CPU DBSCAN. Imported here: scikit-learn takes a second to import,
        # and the face records beside this need none of it.
        from sklearn.cluster import DBSCAN
        # n_jobs=-1 enables multi-threaded distance computation for large datasets.
        db = DBSCAN(eps=clustering.distance(clustering.GROUPING), min_samples=1, metric='euclidean', n_jobs=-1)
        labels = db.fit_predict(embeddings)
    
    # Group face index records by cluster ID
    clusters = {}
    for face, label in zip(all_faces, labels):
        if label == -1: # Noise (unclustered face)
            continue
        if label not in clusters:
            clusters[label] = []
        clusters[label].append(face)

    logger.info(f"Clustered {len(all_faces)} faces into {len(clusters)} distinct visual identities.")

    # Each face's photo metadata, by paths.key of the photo's path. A face's path
    # is its photo row's since faces point at photos by id; before, a face could
    # spell its photo apart from the row, and a raw lookup missed it and lost the
    # photo's people tags -- the anchors and votes this whole resolution runs on.
    # Read once for the run: the index keeps no records (PhotoIndex.records).
    # Each photo's people are the ids it lists (photo_people), not the names "people" spells: two people called alike are two.
    listed = store_people.listed_ids(photo_index.conn)
    meta_by_key = {paths.key(meta["path"]): dict(meta, people=listed.get(meta["id"], [])) for meta in photo_index.records()}

    # What counts as evidence of who is in a photo: its keywords, and names given
    # to its faces by hand. `people` also holds the names on its faces, and some of
    # those are this resolver's own guesses from the last run -- read as evidence,
    # a lone face it once named anchored the same name again, so a wrong guess
    # voted for itself on every run. So each photo's people, as seen below, loses
    # the names only its guessed faces gave it.
    vocabulary = store_taxonomy.people_vocabulary(getattr(photo_index, "db_path", None),
                                                  conn=getattr(photo_index, "conn", None))
    guessed_by_path = {}
    manual_by_path = {}
    for f in all_faces:
        if f["id"] in manual_names:
            if manual_names[f["id"]]:
                manual_by_path.setdefault(f["photo_path"], set()).add(manual_names[f["id"]])
        elif f["person_id"] is not None:
            guessed_by_path.setdefault(f["photo_path"], set()).add(f["person_id"])

    meta_by_path = {}
    for face_path in {f["photo_path"] for f in all_faces}:
        meta = meta_by_key.get(paths.key(face_path))
        if meta is None:
            continue
        guessed = guessed_by_path.get(face_path, set())
        if guessed:
            written = {ref.id for ref in tag_vocabulary.extract_refs(meta.get("tags") or [], vocabulary)}
            written |= manual_by_path.get(face_path, set())
            meta = dict(meta, people=[p for p in meta.get("people", [])
                                      if p not in guessed or p in written])
        meta_by_path[face_path] = meta
    
    # Precompute face counts per photo to avoid O(N) scanning inside the cluster loop
    face_counts_by_photo = {}
    for f in all_faces:
        p_path = f["photo_path"]
        face_counts_by_photo[p_path] = face_counts_by_photo.get(p_path, 0) + 1

    # Traces map to record the resolution path for each face: face_id -> dict
    traces = {}
    assignment_counter = 0

    # Phase 1: Identify direct anchors (faces in photos containing exactly 1 face and exactly 1 person tag)
    direct_anchors = {}
    for face in all_faces:
        p_path = face["photo_path"]
        meta = meta_by_path.get(p_path)
        if meta:
            people = meta.get("people", [])
            if face_counts_by_photo.get(p_path, 0) == 1 and len(people) == 1:
                direct_anchors[face["id"]] = people[0]

    # A human decision outranks an inferred one, so manual assignments are anchors
    # too. This is what lets hand-matching a few faces of a sparsely sampled person
    # pull the rest of their photos in on the next run.
    for face_id, name in manual_names.items():
        if name:
            direct_anchors[face_id] = name

    # Phase 2: Cluster voting using direct anchors
    initial_resolved_names = {}
    for number, (cluster_id, cluster_faces) in enumerate(tqdm(clusters.items(), desc="Resolving face identities")):
        if number % STEP == 0:
            step("voting", number, len(clusters))
        # Count direct anchor names present in this cluster
        cluster_anchors = {}
        photo_people_tags = []
        
        # Map containing parent photos of the cluster and how many faces they have
        photo_face_counts = {}
        for face in cluster_faces:
            path = face["photo_path"]
            if path not in photo_face_counts:
                photo_face_counts[path] = face_counts_by_photo.get(path, 0)
            
            # Gather direct anchor assignment if it exists
            anchor_name = direct_anchors.get(face["id"])
            if anchor_name:
                cluster_anchors[anchor_name] = cluster_anchors.get(anchor_name, 0) + 1
            
            # Also gather people tags on parent photos of this cluster (for majority vote fallback)
            meta = meta_by_path.get(path)
            if meta:
                people = meta.get("people", [])
                photo_people_tags.extend(people)

        resolved_name = None
        method = "unassigned"
        trigger_photos = []
        
        if cluster_anchors:
            # The cluster is the person with the most anchors in it. Anchors that tie name nobody and leave its faces to
            # likeness and elimination: the first anchor read was a coin toss (#1468; the vote below, #1454)
            resolved_name = clustering.singled_out(cluster_anchors)
            method = "direct_anchor_propagation"
            # Gather photos that triggered this anchor vote
            for face in cluster_faces:
                path = face["photo_path"]
                anchor_name = direct_anchors.get(face["id"])
                if anchor_name == resolved_name:
                    trigger_photos.append(path)
        elif photo_people_tags:
            # Fall back to majority voting of people tags on all parent photos: a tie names nobody (#1454)
            tag_freqs = collections.Counter(photo_people_tags)
            best_tag = clustering.singled_out(tag_freqs)

            # Check if this tag appears on at least 50% of the photos holding this face cluster
            total_photos = len(photo_face_counts)
            if best_tag is not None and tag_freqs[best_tag] / total_photos >= 0.50:
                resolved_name = best_tag
                method = "cluster_majority_vote"
                for face in cluster_faces:
                    path = face["photo_path"]
                    people = meta_by_path.get(path, {}).get("people", []) if meta_by_path.get(path) else []
                    if resolved_name in people:
                        trigger_photos.append(path)

        # Assign resolved name to all faces in this cluster
        if resolved_name:
            assignment_counter += 1
            for face in cluster_faces:
                p_path = face["photo_path"]
                meta = meta_by_path.get(p_path)
                photo_people = meta.get("people", []) if meta else []
                
                if resolved_name in photo_people:
                    initial_resolved_names[face["id"]] = resolved_name
                    
                    # Record trace
                    face_direct_anchor = direct_anchors.get(face["id"])
                    if face_direct_anchor == resolved_name:
                        traces[face["id"]] = {
                            "face_id": face["id"],
                            "photo_path": p_path,
                            "cluster_id": int(cluster_id),
                            "person_id": resolved_name,
                            "resolution_method": "direct_anchor",
                            "assignment_order": assignment_counter,
                            "trigger_photos": [p_path]
                        }
                    else:
                        truncated_triggers = trigger_photos[:5]
                        if len(trigger_photos) > 5:
                            truncated_triggers.append(f"...and {len(trigger_photos) - 5} more")
                        traces[face["id"]] = {
                            "face_id": face["id"],
                            "photo_path": p_path,
                            "cluster_id": int(cluster_id),
                            "person_id": resolved_name,
                            "resolution_method": method,
                            "assignment_order": assignment_counter,
                            "trigger_photos": truncated_triggers
                        }
                else:
                    # Parent photo is not tagged with this person: skip assignment
                    traces[face["id"]] = {
                        "face_id": face["id"],
                        "photo_path": p_path,
                        "cluster_id": int(cluster_id),
                        "person_id": None,
                        "resolution_method": "strict_tag_enforcement_override",
                        "assignment_order": None,
                        "trigger_photos": []
                    }
        else:
            for face in cluster_faces:
                traces[face["id"]] = {
                    "face_id": face["id"],
                    "photo_path": face["photo_path"],
                    "cluster_id": int(cluster_id),
                    "person_id": None,
                    "resolution_method": "unassigned",
                    "assignment_order": None,
                    "trigger_photos": []
                }

    # Step 2 & 3: Run iterative propagation loop to bootstrap unknown identities by process of elimination
    current_resolved_names = {face["id"]: initial_resolved_names.get(face["id"]) for face in all_faces}
    
    # Metadata conflict override: If a photo has people tags, clear any initial face assignments that do not match the photo's tags
    override_count = 0
    for face in all_faces:
        fid = face["id"]
        p_path = face["photo_path"]
        meta = meta_by_path.get(p_path)
        if meta:
            photo_tags = set(meta.get("people", []))
            if photo_tags:
                curr_name = current_resolved_names.get(fid)
                if curr_name and curr_name not in photo_tags:
                    current_resolved_names[fid] = None
                    override_count += 1
                    traces[fid] = {
                        "face_id": fid,
                        "photo_path": p_path,
                        "cluster_id": traces.get(fid, {}).get("cluster_id") if fid in traces else None,
                        "person_id": None,
                        "resolution_method": "metadata_conflict_override",
                        "assignment_order": None,
                        "trigger_photos": []
                    }
    if override_count > 0:
        logger.info(f"Metadata conflict override: Unassigned {override_count} initial face labels that did not match parent photo people tags.")
    
    # Pre-group faces by photo once (since photo_path values never change during iterations)
    faces_by_photo = {}
    for face in all_faces:
        p_path = face["photo_path"]
        if p_path not in faces_by_photo:
            faces_by_photo[p_path] = []
        faces_by_photo[p_path].append(face)

    logger.info("Resolving multi-face photo conflicts and applying metadata consensus (iterative loop)...")
    refined_resolved_names = {}
    
    photo_years = {}
    for path, meta in meta_by_path.items():
        photo_years[path] = dates.record_year(meta)

    # photo -> its faces left unnamed in the last round because it names two or more people no face is known for (#1374).
    unpaired = {}

    for iteration in range(MAX_ITERATIONS):
        step("propagating", iteration, MAX_ITERATIONS)
        # 2a. Group embeddings and their photo years by name
        # Prioritize direct anchors to build unpolluted centroids
        resolved_by_name = {}
        names_with_anchors = set(direct_anchors.values())
        for face in all_faces:
            name = current_resolved_names.get(face["id"])
            if name:
                if name in names_with_anchors and face["id"] not in direct_anchors:
                    continue
                if name not in resolved_by_name:
                    resolved_by_name[name] = []
                p_path = face["photo_path"]
                yr = photo_years.get(p_path)
                resolved_by_name[name].append((face["embedding"], yr, p_path))
                
        # Who each name is, by the faces it is on so far (tagpup.core.clustering, #71).
        known = clustering.KnownFaces.of((name, e, y, p) for name, items in resolved_by_name.items()
                                         for e, y, p in items)

        def closest_to(name, embedding, photo_path):
            return known.likeness(name, embedding, photo_years.get(photo_path), photo_path)

        new_resolved_names = {}
        
        for number, (p_path, photo_faces) in enumerate(faces_by_photo.items()):
            if number and number % STEP == 0:
                step("propagating", iteration, MAX_ITERATIONS)
            # Get photo metadata people tags
            meta = meta_by_path.get(p_path)
            photo_tags = set(meta.get("people", [])) if meta else set()
            unpaired.pop(p_path, None)

            # Map of face_id -> resolved_name in this photo
            face_resolved = {f["id"]: current_resolved_names.get(f["id"]) for f in photo_faces}
            
            # A name clustering gave is kept while the face still reaches the value
            # it was named at, against the person's closest face (tagpup.core.clustering)
            # -- unless the photo's own keywords name the person: the tag bears the name
            # out, whatever the likeness (docs/findings.md, #855). That took away the
            # names faces-from-tags had written at 0.70 to 0.80.
            for f in photo_faces:
                name = face_resolved.get(f["id"])
                if name and name in resolved_by_name and name not in photo_tags:
                    similarity = closest_to(name, f["embedding"], f["photo_path"])
                    if similarity is not None and not clustering.names_unasked(similarity):
                        face_resolved[f["id"]] = None

            # A decision made by hand holds through every step below: its face keeps
            # its name -- or its "nobody" -- and the name counts as taken in the photo.
            # It was put back only at the end, after a tie between the hand-named face
            # and another could give the other the same name.
            decided = {f["id"] for f in photo_faces if f["id"] in manual_names}
            for fid in decided:
                face_resolved[fid] = manual_names[fid]
            
            # Tiny faces in the background are noise: they keep no name
            # (tagpup.core.clustering).
            background = clustering.background_faces([f.get("box") for f in photo_faces])
            valid_photo_faces = []
            for i, f in enumerate(photo_faces):
                if i in background:
                    face_resolved[f["id"]] = None
                else:
                    valid_photo_faces.append(f)
            
            # Count occurrences of resolved names in this photo (only for valid faces)
            resolved_counts = {}
            for f in valid_photo_faces:
                name = face_resolved.get(f["id"])
                if name:
                    resolved_counts[name] = resolved_counts.get(name, 0) + 1
            
            # Find names that appear multiple times in the same photo (conflicts)
            conflicting_names = {name for name, count in resolved_counts.items() if count > 1}
            
            if conflicting_names:
                for conf_name in conflicting_names:
                    # Find all valid faces in this photo that resolved to this name
                    conf_faces = [f for f in valid_photo_faces if face_resolved.get(f["id"]) == conf_name]
                    
                    # If a photo is trying to match 2 people to the same name for two
                    # different faces, match neither -- unless one was named by hand,
                    # which keeps it.
                    held = any(f["id"] in decided for f in conf_faces)
                    for f in conf_faces:
                        if not (held and f["id"] in decided):
                            face_resolved[f["id"]] = None

            # Now try to match unassigned valid faces to unused tags in this photo's metadata
            unassigned_faces = [f for f in valid_photo_faces if face_resolved.get(f["id"]) is None
                                and f["id"] not in decided]
            assigned_names = {name for name in face_resolved.values() if name}
            unused_tags = photo_tags - assigned_names
            
            if unassigned_faces and unused_tags:
                # Separate unused tags into known and unknown
                known_unused = [t for t in unused_tags if t in resolved_by_name]
                unknown_unused = [t for t in unused_tags if t not in resolved_by_name]
                
                # 1. Match known tags using Hungarian algorithm
                if known_unused:
                    from scipy.optimize import linear_sum_assignment
                    
                    cost_matrix = []
                    for f in unassigned_faces:
                        f_emb = np.array(f["embedding"])
                        row_costs = []
                        for tag in known_unused:
                            similarity = closest_to(tag, f_emb, f["photo_path"])
                            row_costs.append(2.0 if similarity is None else -similarity)
                        cost_matrix.append(row_costs)
                    
                    cost_matrix = np.array(cost_matrix)
                    row_ind, col_ind = linear_sum_assignment(cost_matrix)
                    
                    # Assign matches as close as naming a face with no one looking
                    # allows (tagpup.core.clustering).
                    for r, c in zip(row_ind, col_ind):
                        if clustering.names_unasked(-cost_matrix[r, c]):
                            f = unassigned_faces[r]
                            tag = known_unused[c]
                            face_resolved[f["id"]] = tag
                            
                # Refresh lists for unknown matching
                unassigned_faces = [f for f in valid_photo_faces if face_resolved.get(f["id"]) is None
                                and f["id"] not in decided]
                assigned_names = {name for name in face_resolved.values() if name}
                unused_tags = photo_tags - assigned_names
                unknown_unused = [t for t in unused_tags if t not in resolved_by_name]
                
                # 2. Match unknown tags by process of elimination: every other face of the photo is somebody, so the one
                # face left is the one person left. Two faces and two such people are a tie and not paired: nothing tells
                # which is whose, and pairing them by the order of their ids wrote a coin toss as a name (owner, 2026-10-15,
                # #1374).
                face_left = clustering.singled_out(dict.fromkeys((f["id"] for f in unassigned_faces), 1))
                person_left = clustering.singled_out(dict.fromkeys(unknown_unused, 1))
                if face_left is not None and person_left is not None:
                    face_resolved[face_left] = person_left
                elif len(unassigned_faces) == len(unknown_unused) > 1:
                    unpaired[p_path] = {f["id"] for f in unassigned_faces}

            # Store refined assignments and update traces if resolved names changed
            for f in photo_faces:
                fid = f["id"]
                old_val = current_resolved_names.get(fid)
                new_val = face_resolved.get(fid)
                new_resolved_names[fid] = new_val
                
                if old_val != new_val:
                    if new_val:
                        assignment_counter += 1
                        traces[fid] = {
                            "face_id": fid,
                            "photo_path": f["photo_path"],
                            "cluster_id": traces.get(fid, {}).get("cluster_id") if fid in traces else None,
                            "person_id": new_val,
                            "resolution_method": f"iterative_propagation_iteration_{iteration + 1}",
                            "assignment_order": assignment_counter,
                            "trigger_photos": [p_path]
                        }
                    else:
                        traces[fid] = {
                            "face_id": fid,
                            "photo_path": f["photo_path"],
                            "cluster_id": traces.get(fid, {}).get("cluster_id") if fid in traces else None,
                            "person_id": None,
                            "resolution_method": "unassigned",
                            "assignment_order": None,
                            "trigger_photos": []
                        }
        
        # Check if there were any changes in assignment compared to the previous iteration
        changed = False
        for fid, val in new_resolved_names.items():
            if current_resolved_names.get(fid) != val:
                changed = True
                break
        
        if not changed:
            logger.info(f"Identity resolution loop converged at iteration {iteration + 1}.")
            break
            
        current_resolved_names = new_resolved_names
        
    refined_resolved_names = current_resolved_names

    # Step 4: Build database updates and calculate final statistics
    # Compute mean embeddings for the final resolved names to classify remaining unassigned faces
    # Prioritize direct anchors to build unpolluted centroids
    final_resolved_by_name = {}
    names_with_anchors = set(direct_anchors.values())
    for face in all_faces:
        name = refined_resolved_names.get(face["id"])
        if name:
            if name in names_with_anchors and face["id"] not in direct_anchors:
                continue
            if name not in final_resolved_by_name:
                final_resolved_by_name[name] = []
            p_path = face["photo_path"]
            yr = photo_years.get(p_path)
            final_resolved_by_name[name].append((face["embedding"], yr, p_path))

    final_known = clustering.KnownFaces.of((name, e, y, p) for name, items in final_resolved_by_name.items()
                                           for e, y, p in items)

    face_updates = []
    resolved_stats = {}
    as_read = {f["id"]: f["person_id"] for f in all_faces}
    unpaired_ids = set().union(*unpaired.values())
    left_unpaired = []

    # Names already spoken for in each photo. The iterative loop refuses to put one
    # person on two faces of the same photo; without tracking that here, this final
    # pass would hand the same name straight back to the face the loop just cleared.
    assigned_names_by_photo = {}
    for face in all_faces:
        name = manual_names.get(face["id"]) if face["id"] in manual_names                 else refined_resolved_names.get(face["id"])
        if name:
            assigned_names_by_photo.setdefault(face["photo_path"], set()).add(name)

    for number, face in enumerate(all_faces):
        if number % STEP == 0:
            step("matching", number, len(all_faces))
        final_name = refined_resolved_names.get(face["id"])

        if final_name is None:
            p_path = face["photo_path"]
            names_taken_here = assigned_names_by_photo.setdefault(p_path, set())

            # If unresolved, check similarity to all known resolved people, skipping
            # anyone already matched to another face in this same photo.
            best_name, best_sim = final_known.most_like(
                face["embedding"], photo_years.get(p_path), p_path, skip=names_taken_here)
            best_sim = -1.0 if best_sim is None else best_sim

            # Check parent photo metadata for people tags
            meta = meta_by_path.get(p_path)
            photo_tags = set(meta.get("people", [])) if meta else set()

            if photo_tags:
                # Photo is tagged with people. We only match if the best matching name is in those tags.
                # The tag bears the name out, so the face is named from where it is worth offering
                # (tagpup.core.clustering.is_offered), not only from where it is named with no one
                # looking: the loop above keeps a tag-confirmed name whatever the likeness (#855), and a
                # face that reaches this pass unnamed -- its cluster has no anchor -- was left unnamed at
                # 0.73 and 0.78 (docs/findings.md, #902). A name already taken by another face of the photo
                # is skipped (`skip`), to prevent false assignments in multi-face photos.
                if best_name in photo_tags and clustering.is_offered(best_sim):
                    final_name = best_name
                    names_taken_here.add(final_name)
                    traces[face["id"]] = {
                        "face_id": face["id"],
                        "photo_path": p_path,
                        "cluster_id": traces.get(face["id"], {}).get("cluster_id") if face["id"] in traces else None,
                        "person_id": final_name,
                        "resolution_method": f"final_matching_tagged_photo (similarity={best_sim:.4f})",
                        "assignment_order": None,
                        "trigger_photos": []
                    }
                else:
                    final_name = None
                    traces[face["id"]] = {
                        "face_id": face["id"],
                        "photo_path": p_path,
                        "cluster_id": traces.get(face["id"], {}).get("cluster_id") if face["id"] in traces else None,
                        "person_id": None,
                        "resolution_method": f"final_matching_tagged_photo_failed (max_similarity={best_sim:.4f})",
                        "assignment_order": None,
                        "trigger_photos": []
                    }
            else:
                # Photo is untagged. Under strict tag enforcement, we do not assign any identity.
                final_name = None
                traces[face["id"]] = {
                    "face_id": face["id"],
                    "photo_path": p_path,
                    "cluster_id": traces.get(face["id"], {}).get("cluster_id") if face["id"] in traces else None,
                    "person_id": None,
                    "resolution_method": "untagged_photo_strict_tag_enforcement",
                    "assignment_order": None,
                    "trigger_photos": []
                }
        
        if face["id"] in manual_names:
            # Keep the human's answer, including a deliberate "no name".
            final_name = manual_names[face["id"]]
            traces[face["id"]] = {
                "face_id": face["id"],
                "photo_path": face["photo_path"],
                "cluster_id": traces.get(face["id"], {}).get("cluster_id") if face["id"] in traces else None,
                "person_id": final_name,
                "resolution_method": "manual_override_preserved",
                "assignment_order": None,
                "trigger_photos": []
            }
        elif final_name is None and face["id"] in unpaired_ids:
            left_unpaired.append(face["id"])
            traces[face["id"]]["resolution_method"] = "unpaired_people_with_no_known_face"

        # A face a person decided is not written; nor is one that already carries what was found: the person, or nobody.
        carried = face["person"]
        unchanged = carried is None if final_name is None else carried is not None and carried.id == final_name
        if face["id"] not in manual_names and not unchanged:
            face_updates.append((final_name, face["id"]))

    # Apply name updates to the SQLite database: the last place to stop
    step("saving", 0, 1)
    written, left = [], 0
    if face_updates:
        written, left, given = _save_face_names(photo_index, face_updates, as_read)
        for tag in given.values():
            resolved_stats[tag] = resolved_stats.get(tag, 0) + 1
        logger.info("Wrote %d face(s); %d left as they were.", len(written), left)
    # What the run did to them (#1455): a guess it wrote off is taken back; a face it meant to write off and found changed
    # under it is the "left" below, not one it left unnamed.
    meant = {face_id for _, face_id in face_updates}
    written = set(written)
    left_unpaired = [face_id for face_id in left_unpaired if face_id not in meant or face_id in written]
    if left_unpaired:
        taken_back = sum(1 for face_id in left_unpaired if face_id in written)
        logger.info("%d face(s) left unnamed, %d guess(es) taken back: their photo names people no face is known for.",
                    len(left_unpaired), taken_back)
        step("unpaired", taken_back, len(left_unpaired))
    if left:
        step("left", left, len(face_updates))   # the library changed under them while this ran
        
    # Save traces to JSON
    try:
        trace_path = resolution_trace_path(photo_index.db_path)
        names = person_ids.read(photo_index.conn).node_names
        for trace in traces.values():
            trace["assigned_name"] = names.get(trace["person_id"])   # what the person is called now, for a reader of the file
        with open(trace_path, "w", encoding="utf-8") as f:
            json.dump(list(traces.values()), f, indent=2)
        logger.info(f"Face resolution traces saved to {trace_path}")
    except Exception as e:
        logger.warning(f"Failed to save face resolution traces: {e}")

    logger.info("Face identity resolution completed successfully.")
    return resolved_stats
