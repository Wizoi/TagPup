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
        
        face_updates = [] # List of tuples (name, face_id) for DB updates
        resolved_stats = {} # Statistics for user logging

        for cluster_id, cluster_faces in clusters.items():
            # Gather all people tags present on the parent photos of this face cluster
            photo_people_tags = []
            anchor_votes = {} # High confidence votes from photos with only 1 face and 1 person tag
            
            # Map containing parent photos of the cluster and how many faces they have
            photo_face_counts = {}
            for face in cluster_faces:
                path = face["photo_path"]
                if path not in photo_face_counts:
                    # Count total faces in this parent photo
                    photo_face_counts[path] = sum(1 for f in all_faces if f["photo_path"] == path)

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
                logger.debug(f"Cluster {cluster_id}: Resolved as '{resolved_name}' via {anchor_votes[resolved_name]} anchor photo(s).")
            # Scenario B: No anchor photos, but we can do majority voting of people tags on all parent photos
            elif photo_people_tags:
                unique_tags, counts = np.unique(photo_people_tags, return_counts=True)
                tag_freqs = dict(zip(unique_tags, counts))
                best_tag = max(tag_freqs, key=tag_freqs.get)
                
                # Check if this tag appears on at least 50% of the photos holding this face cluster
                total_photos = len(photo_face_counts)
                if tag_freqs[best_tag] / total_photos >= 0.50:
                    resolved_name = best_tag
                    logger.debug(f"Cluster {cluster_id}: Resolved as '{resolved_name}' via majority consensus ({tag_freqs[best_tag]}/{total_photos} photos).")

            if resolved_name:
                resolved_stats[resolved_name] = resolved_stats.get(resolved_name, 0) + len(cluster_faces)
                for face in cluster_faces:
                    face_updates.append((resolved_name, face["id"]))
            else:
                # Clear name for cluster if unresolved
                for face in cluster_faces:
                    face_updates.append((None, face["id"]))

        # Apply name updates to the SQLite database
        if face_updates:
            photo_index.save_face_names(face_updates)
            
        logger.info("Face identity resolution completed successfully.")
        return resolved_stats
