# faces.py
import os
import json
import logging
from typing import List, Dict, Any, Tuple, Optional
from PIL import Image
import numpy as np
import torch

# Import facenet-pytorch elements
import warnings
warnings.filterwarnings("ignore", category=FutureWarning, message=".*weights_only.*")
from facenet_pytorch import MTCNN, InceptionResnetV1
from sklearn.cluster import DBSCAN
from tqdm import tqdm

logger = logging.getLogger("tagpup.faces")

class FaceProcessor:
    def __init__(self, device: str = None):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.mtcnn: Optional[MTCNN] = None
        self.resnet: Optional[InceptionResnetV1] = None

    def _init_models(self):
        """Lazily initialize MTCNN detector and InceptionResnetV1 face embedder."""
        if self.mtcnn is not None:
            return
            
        logger.info(f"Initializing face detection (MTCNN) and embedding models (InceptionResnetV1) on {self.device.upper()}...")
        # MTCNN options: keep_all=True detects multiple faces, post_process=False keeps crops raw
        self.mtcnn = MTCNN(keep_all=True, device=self.device, min_face_size=20, thresholds=[0.6, 0.7, 0.7])
        # Resnet trained on VGGFace2 to extract 512-dimensional face features
        self.resnet = InceptionResnetV1(pretrained='vggface2', device=self.device).eval()
        if self.device == "cuda":
            self.resnet = self.resnet.half()
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
                for box, prob in zip(boxes, probs):
                    if prob < 0.85:  # High confidence threshold to filter out false face detections
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
                    face_crop = face_crop.resize((160, 160), Image.BILINEAR)
                    face_tensor = torch.tensor(np.array(face_crop), dtype=torch.float32).permute(2, 0, 1)
                    # Normalize tensor elements from [0, 255] to [-1, 1] range as expected by facenet
                    face_tensor = (face_tensor - 127.5) / 128.0
                    face_tensor = face_tensor.unsqueeze(0).to(self.device)
                    if self.device == "cuda":
                        face_tensor = face_tensor.half()
                    
                    # Generate 512-dimensional embedding
                    with torch.no_grad():
                        emb_tensor = self.resnet(face_tensor)
                        # L2 normalization of face vector
                        emb_tensor /= emb_tensor.norm(dim=-1, keepdim=True)
                        emb = emb_tensor[0].cpu().numpy().tolist()
                        
                    detected_faces.append({
                        "box": [x1, y1, x2, y2],
                        "embedding": emb,
                        "name": None
                    })
                    
                return detected_faces
        except Exception as e:
            logger.error(f"Error processing faces in {img_path}: {e}")
            return []

    def cluster_and_resolve_identities(self, photo_index: Any, taxonomy: Any):
        """Analyze face embeddings, cluster them with DBSCAN, and assign names using photo tags."""
        logger.info("Starting self-tuning face identity resolution...")
        all_faces = photo_index.get_all_faces()
        if not all_faces:
            logger.info("No face embeddings found in the index.")
            return {}

        # Prepare embeddings for clustering
        embeddings = np.array([f["embedding"] for f in all_faces], dtype=np.float32)
        
        # DBSCAN parameters:
        # Since embeddings are L2 normalized, Cosine Distance = 0.5 * (Euclidean Distance)^2.
        # A cosine similarity cutoff of 0.85 equals a cosine distance of 0.15.
        # Euclidean eps = sqrt(2 * 0.15) = sqrt(0.3) ≈ 0.547.
        # We use metric='euclidean' and eps=0.55. min_samples=1 so single faces can form their own cluster.
        db = DBSCAN(eps=0.55, min_samples=1, metric='euclidean')
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

        # Build path-to-metadata lookup map to easily retrieve tags for face photos
        meta_by_path = {meta["path"]: meta for meta in photo_index.metadata}
        
        # Precompute face counts per photo to avoid O(N) scanning inside the cluster loop
        face_counts_by_photo = {}
        for f in all_faces:
            p_path = f["photo_path"]
            face_counts_by_photo[p_path] = face_counts_by_photo.get(p_path, 0) + 1

        # Step 1: Initial resolution based on cluster majority vote (stored in memory first)
        initial_resolved_names = {}
        for cluster_id, cluster_faces in tqdm(clusters.items(), desc="Resolving face identities"):
            # Gather all people tags present on the parent photos of this face cluster
            photo_people_tags = []
            anchor_votes = {} # High confidence votes from photos with only 1 face and 1 person tag
            
            # Map containing parent photos of the cluster and how many faces they have
            photo_face_counts = {}
            for face in cluster_faces:
                path = face["photo_path"]
                if path not in photo_face_counts:
                    # Look up total faces in this parent photo from precomputed dict
                    photo_face_counts[path] = face_counts_by_photo.get(path, 0)

            for face in cluster_faces:
                path = face["photo_path"]
                meta = meta_by_path.get(path)
                if not meta:
                    continue
                    
                people = meta.get("people", [])
                photo_people_tags.extend(people)
                
                # Anchor rule: If a photo contains exactly 1 face and exactly 1 person tag,
                # that face is almost certainly that person.
                if photo_face_counts[path] == 1 and len(people) == 1:
                    person_name = people[0]
                    anchor_votes[person_name] = anchor_votes.get(person_name, 0) + 1

            resolved_name = None
            
            # Scenario A: We have anchor photos
            if anchor_votes:
                # Select the name with the most anchor votes
                resolved_name = max(anchor_votes, key=anchor_votes.get)
            # Scenario B: No anchor photos, but we can do majority voting of people tags on all parent photos
            elif photo_people_tags:
                unique_tags, counts = np.unique(photo_people_tags, return_counts=True)
                tag_freqs = dict(zip(unique_tags, counts))
                best_tag = max(tag_freqs, key=tag_freqs.get)
                
                # Check if this tag appears on at least 50% of the photos holding this face cluster
                total_photos = len(photo_face_counts)
                if tag_freqs[best_tag] / total_photos >= 0.50:
                    resolved_name = best_tag

            # Assign resolved name to all faces in this cluster
            for face in cluster_faces:
                initial_resolved_names[face["id"]] = resolved_name

        # Step 2 & 3: Run iterative propagation loop to bootstrap unknown identities by process of elimination
        current_resolved_names = {face["id"]: initial_resolved_names.get(face["id"]) for face in all_faces}
        
        logger.info("Resolving multi-face photo conflicts and applying metadata consensus (iterative loop)...")
        refined_resolved_names = {}
        
        for iteration in range(5):
            # 2a. Compute mean embeddings for current resolved names
            mean_embeddings = {}
            embeddings_by_name = {}
            for face in all_faces:
                name = current_resolved_names.get(face["id"])
                if name:
                    if name not in embeddings_by_name:
                        embeddings_by_name[name] = []
                    embeddings_by_name[name].append(face["embedding"])
                    
            for name, embs in embeddings_by_name.items():
                mean_embeddings[name] = np.mean(embs, axis=0)
                norm = np.linalg.norm(mean_embeddings[name])
                if norm > 0:
                    mean_embeddings[name] /= norm
            
            # 2b. Group faces by photo
            faces_by_photo = {}
            for face in all_faces:
                p_path = face["photo_path"]
                if p_path not in faces_by_photo:
                    faces_by_photo[p_path] = []
                faces_by_photo[p_path].append(face)

            new_resolved_names = {}
            
            for p_path, photo_faces in faces_by_photo.items():
                # Get photo metadata people tags
                meta = meta_by_path.get(p_path)
                photo_tags = set(meta.get("people", [])) if meta else set()
                
                # Map of face_id -> resolved_name in this photo
                face_resolved = {f["id"]: current_resolved_names.get(f["id"]) for f in photo_faces}
                
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
                        
                        # Calculate distance from each face to the mean embedding of the conflicting name
                        distances = []
                        for f in conf_faces:
                            dist = np.linalg.norm(np.array(f["embedding"]) - mean_embeddings[conf_name])
                            distances.append((dist, f))
                        
                        # Sort faces by distance (closest stays assigned, others get unassigned)
                        distances.sort(key=lambda x: x[0])
                        # The closest face keeps the name
                        best_face = distances[0][1]
                        for dist, f in distances[1:]:
                            face_resolved[f["id"]] = None

                # Now try to match unassigned valid faces to unused tags in this photo's metadata
                unassigned_faces = [f for f in valid_photo_faces if face_resolved.get(f["id"]) is None]
                assigned_names = {name for name in face_resolved.values() if name}
                unused_tags = photo_tags - assigned_names
                
                if unassigned_faces and unused_tags:
                    # Separate unused tags into known and unknown
                    known_unused = [t for t in unused_tags if t in mean_embeddings]
                    unknown_unused = [t for t in unused_tags if t not in mean_embeddings]
                    
                    # 1. Match known tags using Hungarian algorithm
                    if known_unused:
                        from scipy.optimize import linear_sum_assignment
                        
                        cost_matrix = []
                        for f in unassigned_faces:
                            f_emb = np.array(f["embedding"])
                            row_costs = []
                            for tag in known_unused:
                                dist = np.linalg.norm(f_emb - mean_embeddings[tag])
                                row_costs.append(dist)
                            cost_matrix.append(row_costs)
                        
                        cost_matrix = np.array(cost_matrix)
                        row_ind, col_ind = linear_sum_assignment(cost_matrix)
                        
                        # Assign matches if distance is within threshold (e.g. 1.15)
                        for r, c in zip(row_ind, col_ind):
                            dist = cost_matrix[r, c]
                            if dist < 1.15:
                                f = unassigned_faces[r]
                                tag = known_unused[c]
                                face_resolved[f["id"]] = tag
                                
                    # Refresh lists for unknown matching
                    unassigned_faces = [f for f in valid_photo_faces if face_resolved.get(f["id"]) is None]
                    assigned_names = {name for name in face_resolved.values() if name}
                    unused_tags = photo_tags - assigned_names
                    unknown_unused = [t for t in unused_tags if t not in mean_embeddings]
                    
                    # 2. Match unknown tags by process of elimination
                    if len(unassigned_faces) == 1 and len(unknown_unused) == 1:
                        f = unassigned_faces[0]
                        tag = list(unknown_unused)[0]
                        face_resolved[f["id"]] = tag
                    elif len(unassigned_faces) == len(unknown_unused) and len(unassigned_faces) > 0:
                        for f, tag in zip(unassigned_faces, sorted(list(unknown_unused))):
                            face_resolved[f["id"]] = tag

                # Store refined assignments
                for f in photo_faces:
                    new_resolved_names[f["id"]] = face_resolved.get(f["id"])
            
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
        face_updates = []
        resolved_stats = {}
        for face in all_faces:
            final_name = refined_resolved_names.get(face["id"])
            face_updates.append((final_name, face["id"]))
            if final_name:
                resolved_stats[final_name] = resolved_stats.get(final_name, 0) + 1

        # Apply name updates to the SQLite database
        if face_updates:
            photo_index.save_face_names(face_updates)
            
        logger.info("Face identity resolution completed successfully.")
        return resolved_stats
