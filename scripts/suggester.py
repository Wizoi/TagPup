# suggester.py
import os
import logging
from typing import List, Dict, Any, Optional
from taxonomy import TagTaxonomy
from index import PhotoIndex

import _root  # noqa: F401
from tagpup.core import clustering, suggesting, vocabulary
from tagpup.store import faces as store_faces

import threading
logger = logging.getLogger("tagpup_cli.suggester")

_face_processor_lock = threading.Lock()
_global_face_processor = None

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
        # Exclude face-matched folders/people names from candidate tags 
        # to avoid redundant calculations and defer entirely to face matching.
        people_candidates = []
                
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
        self._people = None
        self._people_lock = threading.Lock()

    def _known_people(self):
        """(lower-cased names of everyone known, their named faces as KnownFaces).

        Loaded once per suggester -- one folder run -- rather than per photo. Both
        halves used to come from reading the whole faces table, twice a photo.
        """
        with self._people_lock:
            if self._people is None:
                names = set()
                face_roots = self.taxonomy.people_roots()   # the tree's (docs/findings.md, #66)
                for path in self.taxonomy.paths:
                    parts = vocabulary.segments(path)
                    if len(parts) >= 2 and parts[0].lower() in face_roots:
                        names.add(parts[-1].lower())
                known = clustering.KnownFaces()
                try:
                    known = self.index.known_faces()
                except Exception as db_err:
                    logger.warning(f"Failed to load known faces from database: {db_err}")
                names.update(name.lower() for name in known.names())
                self._people = (names, known)
            return self._people

    def _precompute_candidates(self):
        """Precompute embeddings for candidate tags using a template."""
        if not self.embedder or not self.candidate_tags or self.candidate_embeddings:
            return
            
        model_name = getattr(self.embedder, "model_name", "unknown")
        pretrained = getattr(self.embedder, "pretrained", "unknown")
        
        needed_tags = []
        for tag in self.candidate_tags:
            prompt = suggesting.clip_prompt(tag)
            cached_emb = None
            if self.index and hasattr(self.index, "get_tag_embedding"):
                cached_emb = self.index.get_tag_embedding(tag, prompt, model_name, pretrained)
                
            if cached_emb is not None:
                self.candidate_embeddings[tag] = cached_emb
            else:
                needed_tags.append((tag, prompt))
                
        if needed_tags:
            logger.info(f"Precomputing embeddings for {len(needed_tags)} candidate tags...")
            for tag, prompt in needed_tags:
                try:
                    emb = self.embedder.embed_text(prompt)
                    self.candidate_embeddings[tag] = emb
                    if self.index and hasattr(self.index, "save_tag_embedding"):
                        self.index.save_tag_embedding(tag, prompt, model_name, pretrained, emb)
                except Exception as e:
                    logger.warning(f"Failed to embed candidate tag '{tag}': {e}")

    def _get_candidate_embeddings_for_year(self, year: Optional[int]) -> Dict[str, List[float]]:
        """Get standard or year-specific candidate embeddings."""
        self._precompute_candidates()
        
        if year is None:
            return self.candidate_embeddings
            
        if year in self.year_candidate_embeddings:
            return self.year_candidate_embeddings[year]
            
        model_name = getattr(self.embedder, "model_name", "unknown")
        pretrained = getattr(self.embedder, "pretrained", "unknown")
        
        year_embeddings = {}
        needed_tags = []
        # The roots the library flags as holding faces. This listed family, friends and
        # pets, and so prompted for everyone under People as a thing (#66).
        face_roots = self.taxonomy.people_roots()
        for tag in self.candidate_tags:
            is_person = False
            for path in self.taxonomy.paths:
                parts = vocabulary.segments(path)
                if len(parts) >= 2 and parts[0].lower() in face_roots:
                    if parts[-1].lower() == tag.lower() or path.lower() == tag.lower():
                        is_person = True
                        break
            
            prompt = suggesting.clip_prompt(tag, year, person=is_person)
                
            cached_emb = None
            if self.index and hasattr(self.index, "get_tag_embedding"):
                cached_emb = self.index.get_tag_embedding(tag, prompt, model_name, pretrained)
                
            if cached_emb is not None:
                year_embeddings[tag] = cached_emb
            else:
                needed_tags.append((tag, prompt))
                
        if needed_tags:
            logger.info(f"Computing era-aware candidate embeddings for year {year} ({len(needed_tags)} tags needed)...")
            for tag, prompt in needed_tags:
                try:
                    emb = self.embedder.embed_text(prompt)
                    year_embeddings[tag] = emb
                    if self.index and hasattr(self.index, "save_tag_embedding"):
                        self.index.save_tag_embedding(tag, prompt, model_name, pretrained, emb)
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
        
        known_people, known_faces = self._known_people()

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

            # Only use non-people tags for propagation
            raw_tags = list(meta.get("tags", []))
            
            # Expand tags according to the taxonomy, leaving out the branches that hold people
            # (the tree's face roots, #66) and known people
            face_roots = self.taxonomy.people_roots()
            expanded_tags = set()
            for tag in raw_tags:
                tag_parts = vocabulary.segments(tag)
                if tag_parts and tag_parts[0].lower() in face_roots:
                    continue
                if tag_parts and tag_parts[-1].lower() in known_people:
                    continue
                expanded = self.taxonomy.expand_tag(tag)
                for t in expanded:
                    t_parts = vocabulary.segments(t)
                    if t_parts and t_parts[0].lower() in face_roots:
                        continue
                    if t_parts and t_parts[-1].lower() in known_people:
                        continue
                    expanded_tags.add(t)
            
            # The denominator counts only neighbours that actually contribute a tag.
            #
            # A tag's score is its share of the neighbourhood's similarity, so a
            # neighbour with nothing to say must not take a share. Counting every
            # neighbour was harmless while only tagged photos were indexed, but once
            # untagged photos are in the index they would join the neighbour set,
            # inflate this sum, and push every score down in proportion to how many
            # turned up -- quietly starving the 0.6 display and 0.75 auto-apply
            # thresholds. A neighbour whose tags are all people also contributes
            # nothing here, since those are deferred to face matching.
            if not expanded_tags:
                continue
            total_sim += weighted_sim

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
                tag_parts = [p.lower() for p in vocabulary.segments(tag)]
                
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
            detected_faces = []
            has_face_rows = False

            # Check database cache first
            if self.index and self.index.conn:
                try:
                    import json
                    for row in store_faces.in_photo(self.index.conn, photo_path):
                        box_json, emb_bytes, prob, excluded, name, name_source = row
                        has_face_rows = True
                        # A face someone excluded, or decided is nobody, is not to be
                        # named again by resemblance. It still counts as a face on
                        # file, so the photo is not sent through detection again.
                        if excluded or (name is None and name_source == "manual"):
                            continue
                        box = json.loads(box_json)
                        emb = np.frombuffer(emb_bytes, dtype=np.float32).tolist()
                        detected_faces.append({
                            "box": box,
                            "embedding": emb,
                            "prob": prob
                        })
                except Exception as db_err:
                    logger.warning(f"Failed to query database faces: {db_err}")
            
            # If not in database, detect and embed on-the-fly
            if not has_face_rows:
                from faces import FaceProcessor
                global _global_face_processor
                if _global_face_processor is None:
                    with _face_processor_lock:
                        if _global_face_processor is None:
                            _global_face_processor = FaceProcessor()
                processor = _global_face_processor
                detected_faces = processor.detect_and_embed_faces(photo_path)

                # Keep what we just computed. Detection and embedding are the expensive
                # part of this whole pipeline, and they were previously discarded when the
                # request ended -- so a photo could be suggested for repeatedly and never
                # contribute a single face to the database. Recording them means ordinary
                # tagging feeds TagTuner's identify queue, and an unindexed folder still
                # accumulates face data. Strictly additive: photos that already have face
                # rows are left untouched, so manual names and exclusions are safe.
                if detected_faces and self.index is not None:
                    try:
                        saved = self.index.save_faces_if_absent(photo_path, detected_faces)
                        if saved:
                            logger.info(f"Recorded {saved} newly detected face(s) for {photo_path}")
                    except Exception as save_err:
                        logger.warning(f"Could not record detected faces: {save_err}")

            if detected_faces:
                # Tiny faces in the background are noise: no name is offered for them
                # (tagpup.core.clustering).
                background = clustering.background_faces([f.get("box") for f in detected_faces])
                valid_detected_faces = [f for i, f in enumerate(detected_faces) if i not in background]
                
                if valid_detected_faces:
                    if known_faces.names():
                        for face in valid_detected_faces:
                            # The person the face is most like, by their closest face of the
                            # years around the photo, never one of its own; offered from the
                            # value every screen offers a name from, scored by that likeness
                            # (tagpup.core.clustering). It was a mean face with no years, a
                            # distance cut of 0.90, and a score made up between 0.5 and 1.
                            best_name, likeness = known_faces.most_like(face["embedding"], target_year, photo_path)
                            if best_name and clustering.is_offered(likeness):
                                score = round(likeness, 2)
                                
                                # Resolve leaf name to full taxonomy path if possible.
                                # This looked under a hardcoded "family"/"friends"/
                                # "pets" only, so anyone filed under People came back
                                # as a bare leaf and was suggested -- and written --
                                # in the one form the keywords must not hold.
                                resolved_path = self.taxonomy.find_person_path(best_name) or best_name

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
                    # Map a person's name back to the path they are filed under, under
                    # whichever root this library uses rather than an assumed one.
                    resolved_tag = self.taxonomy.find_person_path(tag) or tag

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
                active_parts = vocabulary.segments(active_tag)
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
            
        # Propose a tag by the path it is filed under.
        #
        # A keyword's hierarchy is most of its worth: "Activity/Cross Country" says
        # where the photo belongs in a way "Cross Country" does not. Zero-shot
        # candidates arrive as bare words, so a photo already tagged
        # "Activity/Cross Country" was offered "Cross Country" as though it were
        # something new -- the suggestion could not match what was there, and taking
        # it would have added a second, flatter copy.
        #
        # People are left alone here: they are resolved at the write boundary, against
        # the people roots specifically, and that path already works.
        suggested_tags = self._with_taxonomy_paths(suggested_tags)

        return {
            "path": photo_path,
            "suggested_tags": suggested_tags,
            "suggested_caption": suggested_caption,
            "neighbor_people": neighbor_people,
            "path_hints": path_hints,
            "nearest_neighbors": neighbors_output
        }

    def _with_taxonomy_paths(self, items):
        """Rewrite each suggested tag as the taxonomy path it belongs to.

        A bare name the taxonomy files under exactly one path becomes that path. One
        it files under two is left alone rather than guessed at, and one it does not
        know stays as it is -- a suggestion for a tag that does not exist yet is still
        a useful suggestion.
        """
        seen = {}
        for item in items:
            tag = item.get("tag")
            if not tag:
                continue
            if "/" not in tag:
                resolved = None
                try:
                    resolved = self.taxonomy.find_by_leaf(tag)
                except Exception:
                    resolved = None
                if resolved:
                    item = dict(item, tag=resolved)
            # Resolving can collide two suggestions onto one path; keep the stronger.
            existing = seen.get(item["tag"])
            if existing is None or item.get("score", 0.0) > existing.get("score", 0.0):
                seen[item["tag"]] = item
        return sorted(seen.values(),
                      key=lambda i: (-i.get("score", 0.0), i.get("tag", "")))

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

                    is_context_tag = any(tag.startswith(root + vocabulary.SEPARATOR)
                                         for root in vocabulary.CONTEXT_ROOTS)

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

