# suggester.py
import os
import re
import json
import logging
from typing import List, Dict, Any, Tuple, Set, Optional
from taxonomy import TagTaxonomy
from index import PhotoIndex

logger = logging.getLogger("tagpup_cli.suggester")

def extract_path_hints(file_path: str) -> List[str]:
    """Extract folder names from the file's path as hints."""
    abs_path = os.path.abspath(file_path)
    # Get directories in the path
    dir_path = os.path.dirname(abs_path)
    parts = []
    
    # Split path into individual folder names
    while True:
        dir_path, folder = os.path.split(dir_path)
        if folder:
            # Skip generic folder names or drive letters
            if folder.lower() not in ["photos", "tagged", "untagged", "images", "pictures", "dcim"]:
                parts.append(folder)
        else:
            if dir_path:
                # Add root/drive if not empty and not just drive letter
                drive = dir_path.strip('\\/')
                if drive and len(drive) > 2:
                    parts.append(drive)
            break
            
    # Reverse to keep left-to-right order and get the last 3-4 folders for hints
    parts.reverse()
    hints = parts[-3:] if len(parts) >= 3 else parts
    return hints

class TagSuggester:
    def __init__(self, index: PhotoIndex, taxonomy: TagTaxonomy, embedder=None, candidate_tags: List[str] = None):
        self.index = index
        self.taxonomy = taxonomy
        self.embedder = embedder
        
        user_candidates = candidate_tags or []
        # Automatically extract people names from taxonomy as candidate tags
        people_candidates = []
        for path in self.taxonomy.paths:
            parts = path.split("/")
            if len(parts) >= 2 and parts[0].lower() in ["family", "friends", "pets"]:
                people_candidates.append(parts[-1])
                
        # Merge and deduplicate candidates
        seen = set()
        combined = []
        for c in (user_candidates + people_candidates):
            c_clean = c.strip()
            if c_clean and c_clean.lower() not in seen:
                seen.add(c_clean.lower())
                combined.append(c_clean)
                
        self.candidate_tags = combined
        self.candidate_embeddings = {}
        self.year_candidate_embeddings = {}

    def _precompute_candidates(self):
        """Precompute embeddings for candidate tags using a template."""
        if not self.embedder or not self.candidate_tags or self.candidate_embeddings:
            return
            
        logger.info(f"Precomputing embeddings for {len(self.candidate_tags)} candidate tags...")
        for tag in self.candidate_tags:
            try:
                # Prompts with templates like "a photo of a ..." improve CLIP zero-shot classification
                prompt = f"a photo of a {tag.lower()}"
                self.candidate_embeddings[tag] = self.embedder.embed_text(prompt)
            except Exception as e:
                logger.warning(f"Failed to embed candidate tag '{tag}': {e}")

    def _get_candidate_embeddings_for_year(self, year: Optional[int]) -> Dict[str, List[float]]:
        """Get standard or year-specific candidate embeddings."""
        self._precompute_candidates()
        
        if year is None:
            return self.candidate_embeddings
            
        if year in self.year_candidate_embeddings:
            return self.year_candidate_embeddings[year]
            
        logger.info(f"Computing era-aware candidate embeddings for year {year}...")
        year_embeddings = {}
        for tag in self.candidate_tags:
            is_person = False
            for path in self.taxonomy.paths:
                parts = path.split("/")
                if len(parts) >= 2 and parts[0].lower() in ["family", "friends", "pets"]:
                    if parts[-1].lower() == tag.lower() or path.lower() == tag.lower():
                        is_person = True
                        break
            
            try:
                if is_person:
                    prompt = f"a photo of {tag} in {year}"
                else:
                    prompt = f"a photo of a {tag.lower()} in {year}"
                year_embeddings[tag] = self.embedder.embed_text(prompt)
            except Exception as e:
                logger.warning(f"Failed to embed era-aware candidate tag '{tag}' for year {year}: {e}")
                if tag in self.candidate_embeddings:
                    year_embeddings[tag] = self.candidate_embeddings[tag]
                    
        self.year_candidate_embeddings[year] = year_embeddings
        return year_embeddings

    def suggest_for_photo(
        self, 
        photo_path: str, 
        embedding: List[float], 
        k: int = 15, 
        min_sim: float = 0.35,
        target_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Query index for nearest neighbors, expand hierarchical tags, aggregate and score tags."""
        import math
        import numpy as np
        from metadata import parse_year_from_metadata
        
        # Collect all known people names (from taxonomy and database) to suppress them from neighbor/CLIP suggestions
        known_people = set()
        for path in self.taxonomy.paths:
            parts = path.split("/")
            if len(parts) >= 2 and parts[0].lower() in ["family", "friends"]:
                known_people.add(parts[-1].lower())
        try:
            db_faces = self.index.get_all_faces()
            if db_faces:
                for f in db_faces:
                    name = f.get("name")
                    if name:
                        known_people.add(name.lower())
        except Exception as db_err:
            logger.warning(f"Failed to query known faces from database for suggestion pruning: {db_err}")

        # 1. Search index for neighbors
        neighbors = self.index.search(embedding, k=k)
        
        # Filter neighbors by minimum cosine similarity
        valid_neighbors = [(sim, meta) for sim, meta in neighbors if sim >= min_sim]
        
        # 2. Extract path hints
        path_hints = extract_path_hints(photo_path)
        path_hints_lower = [h.lower() for h in path_hints]
        
        # Extract year of the target image
        target_year = parse_year_from_metadata(target_metadata) if target_metadata else None
        # Default decay parameter: half-life of 5 years (ln(2)/5 = 0.1386)
        decay_lambda = 0.1386
        
        # 3. Aggregate tags from neighbors
        # We also expand hierarchical tags to their ancestors.
        tag_sim_scores: Dict[str, List[float]] = {}
        tag_counts: Dict[str, int] = {}
        
        total_sim = 0.0
        for sim, meta in valid_neighbors:
            weight = 1.0
            if target_year is not None:
                neighbor_year = parse_year_from_metadata(meta)
                if neighbor_year is not None:
                    diff_years = abs(target_year - neighbor_year)
                    weight = math.exp(-decay_lambda * diff_years)
            
            weighted_sim = sim * weight
            total_sim += weighted_sim
            
            # Only use non-people tags for propagation
            raw_tags = list(meta.get("tags", []))
            
            # Expand tags according to the taxonomy, excluding family and friends branches and known people
            expanded_tags = set()
            for tag in raw_tags:
                tag_parts = tag.split("/")
                if tag_parts and tag_parts[0].lower() in ["family", "friends"]:
                    continue
                if tag_parts and tag_parts[-1].lower() in known_people:
                    continue
                expanded = self.taxonomy.expand_tag(tag)
                for t in expanded:
                    t_parts = t.split("/")
                    if t_parts and t_parts[0].lower() in ["family", "friends"]:
                        continue
                    if t_parts and t_parts[-1].lower() in known_people:
                        continue
                    expanded_tags.add(t)
            
            # Record similarities for each tag
            for tag in expanded_tags:
                tag_sim_scores.setdefault(tag, []).append(weighted_sim)
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        
        # 4. Score calculation
        suggested_tags = []
        if total_sim > 0:
            for tag, scores in tag_sim_scores.items():
                count = tag_counts[tag]
                
                # Base score is the sum of similarity of neighbors containing this tag, 
                # divided by the sum of similarity of all neighbors.
                base_score = sum(scores) / total_sim
                
                # Check for folder path hints boost
                # We boost if the tag or any component of the tag matches a path hint
                boost = 0.0
                tag_parts = [p.lower() for p in tag.split("/")]
                
                for hint in path_hints_lower:
                    # Match exact folder name or check if folder name matches part of the tag
                    if hint in tag_parts or any(hint in p for p in tag_parts):
                        boost = 0.20  # Boost by 20% absolute
                        break
                        
                final_score = min(1.0, base_score + boost)
                
                suggested_tags.append({
                    "tag": tag,
                    "score": round(final_score, 2),
                    "source_count": count
                })

        # 4b. Face recognition suggestions
        try:
            from faces import FaceProcessor
            global _global_face_processor
            if "_global_face_processor" not in globals():
                _global_face_processor = FaceProcessor()
            processor = _global_face_processor
            detected_faces = processor.detect_and_embed_faces(photo_path)
            
            if not detected_faces and self.index and self.index.conn:
                try:
                    import json
                    norm_path = os.path.normpath(photo_path).replace("\\", "/")
                    cursor = self.index.conn.cursor()
                    cursor.execute("SELECT box, embedding, prob FROM faces WHERE LOWER(photo_path) = LOWER(?)", (norm_path,))
                    for row in cursor.fetchall():
                        box_json, emb_bytes, prob = row
                        box = json.loads(box_json)
                        emb = np.frombuffer(emb_bytes, dtype=np.float32).tolist()
                        detected_faces.append({
                            "box": box,
                            "embedding": emb,
                            "prob": prob
                        })
                except Exception as db_err:
                    logger.warning(f"Failed to query database faces fallback: {db_err}")
            
            if detected_faces:
                # Calculate areas and filter out tiny background/noise faces
                face_areas = []
                for f in detected_faces:
                    box = f.get("box", [0, 0, 0, 0])
                    area = (box[2] - box[0]) * (box[3] - box[1])
                    face_areas.append(area)
                max_area = max(face_areas) if face_areas else 0
                
                valid_detected_faces = []
                for area, f in zip(face_areas, detected_faces):
                    if area < 0.10 * max_area and area < 2000:
                        continue
                    valid_detected_faces.append(f)
                
                if valid_detected_faces:
                    db_faces = self.index.get_all_faces()
                    if db_faces:
                        # Group DB faces by name to compute mean embeddings
                        by_name = {}
                        for f in db_faces:
                            name = f["name"]
                            if name:
                                by_name.setdefault(name, []).append(f["embedding"])
                        
                        mean_embeddings = {}
                        for name, embs in by_name.items():
                            mean = np.mean(embs, axis=0)
                            norm = np.linalg.norm(mean)
                            if norm > 0:
                                mean_embeddings[name] = mean / norm
                        
                        for face in valid_detected_faces:
                            face_emb = np.array(face["embedding"], dtype=np.float32)
                            
                            # Find the closest person
                            best_dist = float('inf')
                            best_name = None
                            
                            for name, mean_emb in mean_embeddings.items():
                                dist = np.linalg.norm(face_emb - mean_emb)
                                if dist < best_dist:
                                    best_dist = dist
                                    best_name = name
                            
                            # Score matches with a high-confidence threshold of 0.90
                            if best_name and best_dist < 0.90:
                                # Calculate a confidence score between 0.50 and 1.0
                                if best_dist < 0.60:
                                    score = 1.0
                                else:
                                    score = max(0.50, round(1.0 - (best_dist - 0.60) / (0.90 - 0.60) * 0.50, 2))
                                
                                # Resolve leaf name to full taxonomy path if possible
                                resolved_path = best_name
                                for path in self.taxonomy.paths:
                                    parts = path.split("/")
                                    if len(parts) >= 2 and parts[-1].lower() == best_name.lower() and parts[0].lower() in ["family", "friends", "pets"]:
                                        resolved_path = path
                                        break
                                
                                # Boost or insert tag
                                found = False
                                for t in suggested_tags:
                                    if t["tag"].lower() == resolved_path.lower() or t["tag"].lower().endswith("/" + resolved_path.lower()):
                                        t["score"] = max(t["score"], score)
                                        found = True
                                        break
                                if not found:
                                    suggested_tags.append({
                                        "tag": resolved_path,
                                        "score": score,
                                        "source_count": 1,
                                        "has_face_match": True
                                    })
        except Exception as e:
            logger.warning(f"Failed to perform face matching suggestions for {photo_path}: {e}")
                
        # 5. Zero-shot candidate suggestions (new potential tags)
        active_candidates = self._get_candidate_embeddings_for_year(target_year)
        if active_candidates:
            image_np = np.array(embedding, dtype=np.float32)
            image_norm = np.linalg.norm(image_np)
            if image_norm > 0:
                image_np = image_np / image_norm
                
            for tag, tag_emb in active_candidates.items():
                # Skip if this tag is a person tag (to defer entirely to face matching)
                if tag.lower() in known_people:
                    continue
                    
                # Skip if this tag is already suggested by neighbors
                tag_lower = tag.lower()
                if any(t["tag"].lower() == tag_lower or t["tag"].lower().endswith("/" + tag_lower) for t in suggested_tags):
                    continue
                    
                tag_np = np.array(tag_emb, dtype=np.float32)
                tag_norm = np.linalg.norm(tag_np)
                if tag_norm > 0:
                    tag_np = tag_np / tag_norm
                    
                # Cosine similarity
                sim = float(np.dot(image_np, tag_np))
                
                # Zero-shot CLIP threshold. 0.23 is a solid default for prompt-matched visual concepts
                if sim >= 0.23:
                    # Automatically map person name back to their full hierarchical taxonomy path if it exists
                    resolved_tag = tag
                    for path in self.taxonomy.paths:
                        parts = path.split("/")
                        if len(parts) >= 2 and parts[-1].lower() == tag.lower() and parts[0].lower() in ["family", "friends"]:
                            resolved_tag = path
                            break
                            
                    suggested_tags.append({
                        "tag": resolved_tag,
                        "score": round(sim, 2),
                        "source_count": 0,
                        "is_new_recommendation": True
                    })

        # Prune redundant ancestor tags and redundant leaf-only tags (e.g. remove 'Jane Doe' if 'Family/Immediate/Jane Doe' is suggested)
        pruned_tags = []
        # Sort by length descending to process the most specific leaf tags first
        sorted_by_len = sorted(suggested_tags, key=lambda x: len(x["tag"]), reverse=True)
        for item in sorted_by_len:
            tag = item["tag"]
            is_redundant = False
            for active_item in pruned_tags:
                active_tag = active_item["tag"]
                # Check if active_tag is a descendant of tag (e.g., 'Family/Immediate/Laurel' starts with 'Family/Immediate/')
                if active_tag.startswith(tag + "/"):
                    is_redundant = True
                    break
                # Check if active_tag is a hierarchical tag whose leaf node matches the current tag (e.g. 'Family/Laurel' implies 'Laurel')
                active_parts = active_tag.split("/")
                if len(active_parts) >= 2 and active_parts[-1].lower() == tag.lower():
                    is_redundant = True
                    break
            if not is_redundant:
                pruned_tags.append(item)
        suggested_tags = pruned_tags

        # Sort suggestions by score descending, then source count descending
        suggested_tags.sort(key=lambda x: (-x["score"], -x.get("source_count", 0)))
        
        # We no longer extract captions from neighbors to avoid incorrect event/context copying.
        suggested_caption = None
        neighbor_people = []

        # Format list of nearest neighbors for output
        neighbors_output = []
        for sim, meta in valid_neighbors:
            neighbors_output.append({
                "path": meta["path"],
                "similarity": round(sim, 3)
            })
            
        return {
            "path": photo_path,
            "suggested_tags": suggested_tags,
            "suggested_caption": suggested_caption,
            "neighbor_people": neighbor_people,
            "path_hints": path_hints,
            "nearest_neighbors": neighbors_output
        }

    def apply_folder_consensus(self, suggestions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Group suggestions by folder and adjust scores based on tag consensus across the folder."""
        if not suggestions:
            return suggestions

        # 1. Group suggestions by their parent directory
        by_folder = {}
        for sugg in suggestions:
            folder = os.path.dirname(sugg["path"])
            by_folder.setdefault(folder, []).append(sugg)

        # 2. Process each folder
        for folder, folder_suggestions in by_folder.items():
            num_images = len(folder_suggestions)
            if num_images <= 1:
                # Can't calculate consensus on a single photo or empty folder
                continue

            # Count occurrences of each tag in the folder (using score >= 0.20 as valid suggestion indicator)
            tag_occurrences = {}
            for sugg in folder_suggestions:
                for item in sugg.get("suggested_tags", []):
                    if item.get("score", 0.0) >= 0.20:
                        tag = item["tag"]
                        tag_occurrences[tag] = tag_occurrences.get(tag, 0) + 1

            # Calculate consensus rate (fraction of images in the folder suggesting this tag)
            tag_consensus = {tag: count / num_images for tag, count in tag_occurrences.items()}

            # 3. Adjust scores
            for sugg in folder_suggestions:
                adjusted_tags = []
                for item in sugg.get("suggested_tags", []):
                    tag = item["tag"]
                    score = item["score"]
                    consensus_rate = tag_consensus.get(tag, 0.0)

                    is_context_tag = any(tag.startswith(prefix) for prefix in [
                        "Activity/", "School/", "Trips/", "Scenic/", "Location/", "Albums/"
                    ])

                    new_score = score
                    if consensus_rate >= 0.40:
                        # High consensus boost
                        new_score = min(1.0, score * 1.25)
                    elif is_context_tag:
                        if consensus_rate < 0.10:
                            # Severe penalty for isolated context outlier
                            new_score = score * 0.3
                        elif consensus_rate < 0.20:
                            # Moderate penalty for low consensus context outlier
                            new_score = score * 0.6

                    # Keep tag if the score is still reasonable
                    if new_score >= 0.15:
                        item["score"] = round(new_score, 2)
                        item["consensus_rate"] = round(consensus_rate, 2)
                        adjusted_tags.append(item)

                # Re-sort suggestions by score descending
                adjusted_tags.sort(key=lambda x: (-x["score"], -x.get("source_count", 0)))
                sugg["suggested_tags"] = adjusted_tags

        return suggestions

