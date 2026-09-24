# faces.py
import os
import threading
import json
import logging
import io
from typing import List, Dict, Any, Optional
from PIL import Image
Image.MAX_IMAGE_PIXELS = 500000000
import numpy as np

import torch

# Import facenet-pytorch elements
import warnings
warnings.filterwarnings("ignore", category=FutureWarning, message=".*weights_only.*")
from facenet_pytorch import MTCNN, InceptionResnetV1
from sklearn.cluster import DBSCAN
from tqdm import tqdm

try:
    from . import paths
except ImportError:  # imported as a top-level module
    import paths

import _root  # noqa: F401
from tagpup import config as tagpup_config
from tagpup.core import clustering
from tagpup.core.library import Library

logger = logging.getLogger("tagpup_cli.faces")


def resolution_trace_path(db_path):
    """Where clustering records how it named each face, for this library alone.

    It was face_resolution_trace.json beside the database -- one file for every
    library in the folder, so clustering one overwrote the record of another.
    """
    return Library(db_path).face_trace_file


class FaceProcessor:
    def __init__(self, device: str = None):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.mtcnn: Optional[MTCNN] = None
        self.resnet: Optional[InceptionResnetV1] = None
        self._init_lock = threading.Lock()

        settings = tagpup_config.face_settings()
        self.min_face_size = settings["min_face_size"]
        self.confidence_threshold = settings["confidence_threshold"]
        self.mtcnn_thresholds = settings["mtcnn_thresholds"]

    def _init_models(self):
        """Lazily initialize MTCNN detector and InceptionResnetV1 face embedder.

        Locked: the suggester shares one processor across a pool, and unlocked every
        worker saw no model yet and loaded its own copy onto the GPU.
        """
        with self._init_lock:
            if self.mtcnn is None:
                self._load_models()

    def _load_models(self):
        logger.info(f"Initializing face detection (MTCNN) and embedding models (InceptionResnetV1) on {self.device.upper()}...")
        # MTCNN options: keep_all=True detects multiple faces, post_process=False keeps crops raw
        self.mtcnn = MTCNN(
            keep_all=True, 
            device=self.device, 
            min_face_size=self.min_face_size, 
            thresholds=self.mtcnn_thresholds
        )
        # Resnet trained on VGGFace2 to extract 512-dimensional face features
        self.resnet = InceptionResnetV1(pretrained='vggface2', device=self.device).eval()
        if self.device == "cuda":
            self.resnet = self.resnet.half()
            
        # Conditionally compile model for CUDA acceleration (disabled on Windows due to lack of Triton support)
        if self.device == "cuda" and os.name != "nt" and hasattr(torch, "compile"):
            try:
                logger.info("Compiling InceptionResnetV1 model for CUDA acceleration...")
                self.resnet = torch.compile(self.resnet)
            except Exception as compile_err:
                logger.warning(f"Failed to compile InceptionResnetV1 model: {compile_err}. Using standard model.")
                
        logger.info("Face models loaded successfully.")

    def detect_and_embed_faces(self, img_path: str) -> List[Dict[str, Any]]:
        """Detect all faces in an image and generate 512-dimensional embeddings for each."""
        self._init_models()
        
        if not os.path.exists(img_path):
            return []
            
        try:
            with Image.open(img_path) as img:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                    
                width, height = img.size
                
                # Detect bounding boxes and probability scores
                boxes, probs = self.mtcnn.detect(img)
                
                if boxes is None or len(boxes) == 0:
                    return []
                    
                detected_faces = []
                face_crops_info = []
                for box, prob in zip(boxes, probs):
                    if prob < self.confidence_threshold:  # Configurable confidence threshold to filter out false face detections
                        continue
                        
                    x1, y1, x2, y2 = box
                    # Clamp coordinates to image boundaries
                    x1, y1 = max(0, int(x1)), max(0, int(y1))
                    x2, y2 = min(width, int(x2)), min(height, int(y2))
                    
                    if (x2 - x1) < 15 or (y2 - y1) < 15:
                        continue # Skip tiny/noise crops
                        
                    # Crop face from PIL image
                    face_crop = img.crop((x1, y1, x2, y2))
                    # Preprocess crop to match InceptionResnetV1 inputs (160x160 RGB normalized)
                    face_crop_resized = face_crop.resize((160, 160), Image.BILINEAR)
                    face_tensor = torch.tensor(np.array(face_crop_resized), dtype=torch.float32).permute(2, 0, 1)
                    # Normalize tensor elements from [0, 255] to [-1, 1] range as expected by facenet
                    face_tensor = (face_tensor - 127.5) / 128.0
                    
                    # Save a web-optimized face crop image (downscaled to max 256px if larger)
                    face_crop_thumb = face_crop.copy()
                    if max(face_crop_thumb.size) > 256:
                        try:
                            resample = Image.Resampling.LANCZOS
                        except AttributeError:
                            try:
                                resample = Image.LANCZOS
                            except AttributeError:
                                resample = Image.ANTIALIAS
                        face_crop_thumb.thumbnail((256, 256), resample)
                    
                    crop_buffer = io.BytesIO()
                    face_crop_thumb.save(crop_buffer, format="JPEG", quality=90)
                    crop_bytes = crop_buffer.getvalue()
                    
                    face_crops_info.append({
                        "box": [x1, y1, x2, y2],
                        "tensor": face_tensor,
                        "crop_image": crop_bytes,
                        "prob": float(prob)
                    })

                if face_crops_info:
                    # Stack all face tensors into a single batch and move to device
                    batch_tensors = torch.stack([x["tensor"] for x in face_crops_info]).to(self.device)
                    if self.device == "cuda":
                        batch_tensors = batch_tensors.half()

                    # Generate 512-dimensional embeddings in a single forward pass
                    with torch.no_grad():
                        emb_tensors = self.resnet(batch_tensors)
                        emb_tensors /= emb_tensors.norm(dim=-1, keepdim=True)
                        embeddings = emb_tensors.cpu().numpy().tolist()

                    for info, emb in zip(face_crops_info, embeddings):
                        detected_faces.append({
                            "box": info["box"],
                            "embedding": emb,
                            "name": None,
                            "crop_image": info["crop_image"],
                            "prob": info["prob"]
                        })
                    
                return detected_faces
        except Exception as e:
            logger.error(f"Error processing faces in {img_path}: {e}")
            return []

    def cluster_and_resolve_identities(self, photo_index: Any, taxonomy: Any, max_iterations: int = 5):
        """Analyze face embeddings, cluster them with DBSCAN, and assign names using photo tags."""
        logger.info("Starting self-tuning face identity resolution...")
        all_faces = photo_index.get_all_faces()
        if not all_faces:
            logger.info("No face embeddings found in the index.")
            return {}

        # Faces marked as not-a-person are dropped before anything else runs. They must
        # not cluster, vote, seed a centroid, or be offered as a candidate -- the whole
        # point is to stop crowd noise and bad crops polluting identity resolution.
        excluded_ids = set()
        if hasattr(photo_index, "get_excluded_face_ids"):
            excluded_ids = photo_index.get_excluded_face_ids()
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
        manual_names = {}
        if hasattr(photo_index, "get_manual_face_names"):
            manual_names = photo_index.get_manual_face_names()
        if manual_names:
            logger.info(
                f"Preserving {len(manual_names)} manually assigned face names "
                f"({sum(1 for v in manual_names.values() if v)} named, "
                f"{sum(1 for v in manual_names.values() if not v)} deliberately cleared)."
            )

        # Prepare embeddings for clustering
        embeddings = np.array([f["embedding"] for f in all_faces], dtype=np.float32)
        
        # DBSCAN parameters:
        # Since embeddings are L2 normalized, Cosine Distance = 0.5 * (Euclidean Distance)^2.
        # A cosine similarity cutoff of 0.85 equals a cosine distance of 0.15.
        # Euclidean eps = sqrt(2 * 0.15) = sqrt(0.3) ≈ 0.547.
        # We use metric='euclidean' and eps=0.48. min_samples=1 so single faces can form their own cluster.
        try:
            # pyrefly: ignore [missing-import] The codebase is written defensively to support both GPU and CPU execution. It wraps the import in a standard Python try/except ImportError block
            from cuml.cluster import DBSCAN as cuDBSCAN
            db = cuDBSCAN(eps=0.48, min_samples=1, metric='euclidean')
            labels = db.fit_predict(embeddings)
            logger.info("Using GPU-accelerated cuML DBSCAN for clustering.")
        except ImportError:
            # Fallback to CPU DBSCAN
            # n_jobs=-1 enables multi-threaded distance computation for large datasets.
            db = DBSCAN(eps=0.48, min_samples=1, metric='euclidean', n_jobs=-1)
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

        # Each face's photo metadata, keyed by the face's own photo_path. Built by
        # paths.key because the two columns need not share a spelling: a raw lookup of
        # faces.photo_path in a dict of photos.path missed every face whose row was
        # spelled differently, and those faces lost their photo's people tags -- the
        # anchors and votes this whole resolution runs on. Keying by the face's
        # spelling keeps every lookup below exact and costs one key() per photo.
        meta_by_key = {paths.key(meta["path"]): meta for meta in photo_index.metadata}

        # What counts as evidence of who is in a photo: its keywords, and names given
        # to its faces by hand. `people` also holds the names on its faces, and some of
        # those are this resolver's own guesses from the last run -- read as evidence,
        # a lone face it once named anchored the same name again, so a wrong guess
        # voted for itself on every run. So each photo's people, as seen below, loses
        # the names only its guessed faces gave it.
        from metadata import PeopleVocabulary, extract_people
        vocabulary = PeopleVocabulary.load(getattr(photo_index, "db_path", None),
                                           conn=getattr(photo_index, "conn", None))
        guessed_by_path = {}
        manual_by_path = {}
        for f in all_faces:
            if f["id"] in manual_names:
                if manual_names[f["id"]]:
                    manual_by_path.setdefault(f["photo_path"], set()).add(manual_names[f["id"]])
            elif f.get("name"):
                guessed_by_path.setdefault(f["photo_path"], set()).add(f["name"])

        meta_by_path = {}
        for face_path in {f["photo_path"] for f in all_faces}:
            meta = meta_by_key.get(paths.key(face_path))
            if meta is None:
                continue
            guessed = guessed_by_path.get(face_path, set())
            if guessed:
                written = set(extract_people(meta.get("raw_metadata") or {}, meta.get("tags") or [],
                                             vocabulary=vocabulary))
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
        for cluster_id, cluster_faces in tqdm(clusters.items(), desc="Resolving face identities"):
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
                # If there are direct anchors in the cluster, resolve to the one with the most anchor votes
                resolved_name = max(cluster_anchors, key=cluster_anchors.get)
                method = "direct_anchor_propagation"
                # Gather photos that triggered this anchor vote
                for face in cluster_faces:
                    path = face["photo_path"]
                    anchor_name = direct_anchors.get(face["id"])
                    if anchor_name == resolved_name:
                        trigger_photos.append(path)
            elif photo_people_tags:
                # Fall back to majority voting of people tags on all parent photos
                unique_tags, counts = np.unique(photo_people_tags, return_counts=True)
                tag_freqs = dict(zip(unique_tags, counts))
                best_tag = max(tag_freqs, key=tag_freqs.get)
                
                # Check if this tag appears on at least 50% of the photos holding this face cluster
                total_photos = len(photo_face_counts)
                if tag_freqs[best_tag] / total_photos >= 0.50:
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
                                "assigned_name": resolved_name,
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
                                "assigned_name": resolved_name,
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
                            "assigned_name": None,
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
                        "assigned_name": None,
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
                            "assigned_name": None,
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
        
        from metadata import parse_year_from_metadata
        photo_years = {}
        for path, meta in meta_by_path.items():
            photo_years[path] = parse_year_from_metadata(meta)
            
        for iteration in range(max_iterations):
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
                    resolved_by_name[name].append((face["embedding"], yr))
                    
            # Calculate minimum year (baseline) for each person
            y_min_by_name = {}
            for name, items in resolved_by_name.items():
                years = [y for _, y in items if y is not None]
                if years:
                    y_min_by_name[name] = min(years)

            era_centroid_cache = {}
            def get_era_centroid(name, target_year):
                cache_key = (name, target_year)
                if cache_key in era_centroid_cache:
                    return era_centroid_cache[cache_key]
                    
                items = resolved_by_name.get(name, [])
                if not items:
                    return None
                    
                y_min = y_min_by_name.get(name)
                
                if y_min is not None and target_year is not None:
                    age = target_year - y_min
                else:
                    age = 99
                    
                if age <= 4:
                    w = 1
                elif age <= 12:
                    w = 2
                elif age <= 16:
                    w = 3
                elif age <= 20:
                    w = 4
                else:
                    w = 5
                    
                window_embeddings = []
                if target_year is not None:
                    for emb, yr in items:
                        if yr is not None and (target_year - w) <= yr <= (target_year + w):
                            window_embeddings.append(emb)
                            
                current_w = w
                while len(window_embeddings) < 5 and current_w < 5:
                    current_w += 1
                    window_embeddings = []
                    if target_year is not None:
                        for emb, yr in items:
                            if yr is not None and (target_year - current_w) <= yr <= (target_year + current_w):
                                window_embeddings.append(emb)
                                
                if len(window_embeddings) < 5:
                    window_embeddings = [emb for emb, _ in items]
                    
                if not window_embeddings:
                    centroid = None
                else:
                    centroid = np.mean(window_embeddings, axis=0)
                    norm = np.linalg.norm(centroid)
                    if norm > 0:
                        centroid /= norm
                        
                era_centroid_cache[cache_key] = centroid
                return centroid

            new_resolved_names = {}
            
            for p_path, photo_faces in faces_by_photo.items():
                # Get photo metadata people tags
                meta = meta_by_path.get(p_path)
                photo_tags = set(meta.get("people", [])) if meta else set()
                
                # Map of face_id -> resolved_name in this photo
                face_resolved = {f["id"]: current_resolved_names.get(f["id"]) for f in photo_faces}
                
                # Validate existing assignments against era-aware centroids (clear if similarity < 0.80)
                for f in photo_faces:
                    name = face_resolved.get(f["id"])
                    if name and name in resolved_by_name:
                        target_yr = photo_years.get(f["photo_path"])
                        centroid = get_era_centroid(name, target_yr)
                        if centroid is not None:
                            dist = np.linalg.norm(np.array(f["embedding"]) - centroid)
                            if dist >= 0.63246:  # Cosine similarity < 0.80
                                face_resolved[f["id"]] = None
                
                # Calculate face areas and find maximum area
                face_areas = []
                for f in photo_faces:
                    box = f.get("box", [0, 0, 0, 0])
                    area = (box[2] - box[0]) * (box[3] - box[1])
                    face_areas.append((area, f["id"]))
                
                max_area = max(area for area, _ in face_areas) if face_areas else 0
                
                # Identify tiny/noise background faces and force them to None
                valid_photo_faces = []
                for area, fid in face_areas:
                    f = next(x for x in photo_faces if x["id"] == fid)
                    if area < 0.10 * max_area and area < 2000:
                        face_resolved[fid] = None
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
                        
                        # If a photo is trying to match 2 people to the same name for two different faces, do not match either.
                        for f in conf_faces:
                            face_resolved[f["id"]] = None

                # Now try to match unassigned valid faces to unused tags in this photo's metadata
                unassigned_faces = [f for f in valid_photo_faces if face_resolved.get(f["id"]) is None]
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
                                target_yr = photo_years.get(f["photo_path"])
                                centroid = get_era_centroid(tag, target_yr)
                                if centroid is not None:
                                    dist = np.linalg.norm(f_emb - centroid)
                                else:
                                    dist = 2.0
                                row_costs.append(dist)
                            cost_matrix.append(row_costs)
                        
                        cost_matrix = np.array(cost_matrix)
                        row_ind, col_ind = linear_sum_assignment(cost_matrix)
                        
                        # Assign matches as close as naming a face with no one looking
                        # allows (tagpup.core.clustering), as a distance.
                        for r, c in zip(row_ind, col_ind):
                            dist = cost_matrix[r, c]
                            if dist < clustering.distance(clustering.NAME_WITHOUT_ASKING):
                                f = unassigned_faces[r]
                                tag = known_unused[c]
                                face_resolved[f["id"]] = tag
                                
                    # Refresh lists for unknown matching
                    unassigned_faces = [f for f in valid_photo_faces if face_resolved.get(f["id"]) is None]
                    assigned_names = {name for name in face_resolved.values() if name}
                    unused_tags = photo_tags - assigned_names
                    unknown_unused = [t for t in unused_tags if t not in resolved_by_name]
                    
                    # 2. Match unknown tags by process of elimination
                    if len(unassigned_faces) == 1 and len(unknown_unused) == 1:
                        f = unassigned_faces[0]
                        tag = list(unknown_unused)[0]
                        face_resolved[f["id"]] = tag
                    elif len(unassigned_faces) == len(unknown_unused) and len(unassigned_faces) > 0:
                        for f, tag in zip(unassigned_faces, sorted(list(unknown_unused))):
                            face_resolved[f["id"]] = tag

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
                                "assigned_name": new_val,
                                "resolution_method": f"iterative_propagation_iteration_{iteration + 1}",
                                "assignment_order": assignment_counter,
                                "trigger_photos": [p_path]
                            }
                        else:
                            traces[fid] = {
                                "face_id": fid,
                                "photo_path": f["photo_path"],
                                "cluster_id": traces.get(fid, {}).get("cluster_id") if fid in traces else None,
                                "assigned_name": None,
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
                final_resolved_by_name[name].append((face["embedding"], yr))

        # Calculate final y_min for each name
        final_y_min_by_name = {}
        for name, items in final_resolved_by_name.items():
            years = [y for _, y in items if y is not None]
            if years:
                final_y_min_by_name[name] = min(years)

        final_era_centroid_cache = {}
        def get_final_era_centroid(name, target_year):
            cache_key = (name, target_year)
            if cache_key in final_era_centroid_cache:
                return final_era_centroid_cache[cache_key]
                
            items = final_resolved_by_name.get(name, [])
            if not items:
                return None
                
            y_min = final_y_min_by_name.get(name)
            
            if y_min is not None and target_year is not None:
                age = target_year - y_min
            else:
                age = 99
                
            if age <= 4:
                w = 1
            elif age <= 12:
                w = 2
            elif age <= 16:
                w = 3
            elif age <= 20:
                w = 4
            else:
                w = 5
                
            window_embeddings = []
            if target_year is not None:
                for emb, yr in items:
                    if yr is not None and (target_year - w) <= yr <= (target_year + w):
                        window_embeddings.append(emb)
                        
            current_w = w
            while len(window_embeddings) < 5 and current_w < 5:
                current_w += 1
                window_embeddings = []
                if target_year is not None:
                    for emb, yr in items:
                        if yr is not None and (target_year - current_w) <= yr <= (target_year + current_w):
                            window_embeddings.append(emb)
                            
            if len(window_embeddings) < 5:
                window_embeddings = [emb for emb, _ in items]
                
            if not window_embeddings:
                centroid = None
            else:
                centroid = np.mean(window_embeddings, axis=0)
                norm = np.linalg.norm(centroid)
                if norm > 0:
                    centroid /= norm
                    
            final_era_centroid_cache[cache_key] = centroid
            return centroid

        face_updates = []
        resolved_stats = {}

        # Names already spoken for in each photo. The iterative loop refuses to put one
        # person on two faces of the same photo; without tracking that here, this final
        # pass would hand the same name straight back to the face the loop just cleared.
        assigned_names_by_photo = {}
        for face in all_faces:
            name = manual_names.get(face["id"]) if face["id"] in manual_names                 else refined_resolved_names.get(face["id"])
            if name:
                assigned_names_by_photo.setdefault(face["photo_path"], set()).add(name)

        for face in all_faces:
            final_name = refined_resolved_names.get(face["id"])

            if final_name is None:
                p_path = face["photo_path"]
                names_taken_here = assigned_names_by_photo.setdefault(p_path, set())

                # If unresolved, check similarity to all known resolved people, skipping
                # anyone already matched to another face in this same photo.
                best_sim = -1.0
                best_name = None
                f_emb = np.array(face["embedding"])
                target_yr = photo_years.get(face["photo_path"])
                for name in final_resolved_by_name.keys():
                    if name in names_taken_here:
                        continue
                    mean_emb = get_final_era_centroid(name, target_yr)
                    if mean_emb is not None:
                        sim = np.dot(f_emb, mean_emb)
                        if sim > best_sim:
                            best_sim = sim
                            best_name = name

                # Check parent photo metadata for people tags
                meta = meta_by_path.get(p_path)
                photo_tags = set(meta.get("people", [])) if meta else set()

                if photo_tags:
                    # Photo is tagged with people. We only match if the best matching name is in those tags.
                    # Since we have confirmation via tags, we name without asking at the
                    # value for that (tagpup.core.clustering), to prevent false
                    # assignments in multi-face photos
                    if best_name in photo_tags and best_sim >= clustering.NAME_WITHOUT_ASKING:
                        final_name = best_name
                        names_taken_here.add(final_name)
                        traces[face["id"]] = {
                            "face_id": face["id"],
                            "photo_path": p_path,
                            "cluster_id": traces.get(face["id"], {}).get("cluster_id") if face["id"] in traces else None,
                            "assigned_name": final_name,
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
                            "assigned_name": None,
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
                        "assigned_name": None,
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
                    "assigned_name": final_name,
                    "resolution_method": "manual_override_preserved",
                    "assignment_order": None,
                    "trigger_photos": []
                }

            face_updates.append((final_name, face["id"]))
            if final_name:
                resolved_stats[final_name] = resolved_stats.get(final_name, 0) + 1

        # Apply name updates to the SQLite database
        if face_updates:
            photo_index.save_face_names(face_updates)
            
        # Save traces to JSON
        try:
            trace_path = resolution_trace_path(photo_index.db_path)
            with open(trace_path, "w", encoding="utf-8") as f:
                json.dump(list(traces.values()), f, indent=2)
            logger.info(f"Face resolution traces saved to {trace_path}")
        except Exception as e:
            logger.warning(f"Failed to save face resolution traces: {e}")

        logger.info("Face identity resolution completed successfully.")
        return resolved_stats
