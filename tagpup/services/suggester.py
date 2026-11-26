"""Suggesting tags for a photo: TagSuggester, what a folder's run calls (SuggestionModel,
model_for_run), and what Suggest offered each photo as the runs (tagpup.jobs.suggestions) read
and keep it: in the library, by the photo's id (saved_in, keep, offer; tagpup.store.suggestions).

The rules it applies -- the value a tag is offered from, the words CLIP is asked about,
the sentence each is put in, how alike a face must be -- are tagpup.core.suggesting's
and tagpup.core.clustering's. The models are handed in (tagpup.runtime builds them): the
suggester made its own face model in a module-level slot, and a script filled another
slot with the rest (docs/findings.md, #112). It was scripts/suggester.py and
scripts/suggest_models.py.
"""
import collections
import json
import logging
import math
import os
import threading
from typing import Any, Dict, List, Optional

import numpy as np

from tagpup.core import clustering, dates, paths, suggesting, vocabulary
from tagpup.core.library import Library
from tagpup.files import images
from tagpup.ml import photo_models
from tagpup.services import identify
from tagpup.services.search import NoFrames
from tagpup.services import faces as face_records
from tagpup.store import db, person_ids
from tagpup.store import faces as store_faces
from tagpup.store import faces_detected as store_faces_detected
from tagpup.store import photos as store_photos
from tagpup.store import roots as store_roots
from tagpup.store import suggestions as store_suggestions
from tagpup.store.taxonomy import TagTaxonomy

logger = logging.getLogger("tagpup_cli.suggester")


class TextEmbeddingCache:
    """What CLIP made of candidate words' prompts, kept in this process for a run that keeps nothing
    in the library (a Just look run; `remember=False`). A run that saves keeps them in the library
    (PhotoIndex.save_tag_embedding), so its next run reads them; one that may not write used to
    embed every year's prompts again each time. Never persisted; bounded (the oldest is dropped past
    `limit`: a vector is 2 KB); one per library, dropped with it (forget_looking_text_embeddings)."""

    def __init__(self, limit=5000):
        self.limit = limit
        self._found = collections.OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            found = self._found.get(key)
        return None if found is None else found.tolist()

    def put(self, key, vector):
        with self._lock:
            self._found[key] = np.asarray(vector, dtype=np.float32)
            self._found.move_to_end(key)
            while len(self._found) > self.limit:
                self._found.popitem(last=False)

    def __len__(self):
        with self._lock:
            return len(self._found)


_looking_text = {}
_looking_text_lock = threading.Lock()


def looking_text_embeddings(db_path):
    """The library's TextEmbeddingCache, made the first time it is asked for."""
    with _looking_text_lock:
        return _looking_text.setdefault(paths.key(str(db_path)), TextEmbeddingCache())


def forget_looking_text_embeddings(db_path):
    """Let go of a library's: it is forgotten."""
    with _looking_text_lock:
        _looking_text.pop(paths.key(str(db_path)), None)


def saved_in(db_path, folder):
    """{photo as stored: entry} of what the library holds for the photos directly in
    `folder`; nothing for a library that is not there."""
    if not os.path.exists(db_path):
        return {}
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return store_suggestions.in_folder(conn, folder)
    finally:
        conn.close()


def keep(db_path, photo, found, model_key=None):
    """Keep `found` as what was suggested for `photo`, through the write lock."""
    db.write_with_connection(db_path, lambda conn: store_suggestions.put(conn, photo, found, model_key,
                                                                         kind=images.kind_of(photo)),
                             label="suggestions for %s" % os.path.basename(photo))


def offer(db_path, offered, label="folder consensus"):
    """Change what is offered for each photo in `offered`, [(photo, (tags, people,
    title))], leaving what the suggester made as it was. Returns rows changed."""
    return db.write_with_connection(
        db_path, lambda conn: sum(store_suggestions.offer(conn, photo, tags, people, title)
                                  for photo, (tags, people, title) in offered), label=label)


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

def _ends_with(tag, tail):
    """Is `tail` the last whole segments of `tag`, without case: "Family/Wren Okafor" ends with "Wren Okafor" and with
    itself, never with "Okafor" alone cut mid-segment."""
    have = [vocabulary.key(each) for each in vocabulary.segments(tag)]
    want = [vocabulary.key(each) for each in vocabulary.segments(tail)]
    return bool(want) and have[-len(want):] == want


def _is_above(branch, tag):
    """Is `branch` a path above `tag` (not the tag itself), without case: "Coast" is above "Coast/Pier"."""
    wanted = vocabulary.keyword_key(vocabulary.normalize(branch))
    return any(vocabulary.keyword_key(each) == wanted for each in vocabulary.lineage(tag)[:-1])


class TagSuggester:
    """What to suggest for a photo of one library: from its nearest neighbours' tags, the
    people its faces look like, and what CLIP says it shows.

    Given everything it uses. `index` is the library's (tagpup.services.search.PhotoIndex):
    its neighbours, its connection and its tag embeddings. `embedder` is the CLIP model
    (tagpup.ml.clip.ClipModel) the candidate words are embedded with, and `faces` the face
    model (tagpup.ml.scrfd_adaface.ScrfdAdaFace) a photo with no faces on file is detected with: both
    always, as a run is given them (model_for_run). The face model was a module-level one the
    suggester made for itself.
    """

    def __init__(self, index, taxonomy, *, embedder, faces, candidate_tags: List[str] = None,
                 remember=True, text_cache=None):
        self.index = index
        #: Where a prompt's embedding is kept when `remember` is False (TextEmbeddingCache).
        self.text_cache = text_cache
        #: False: the library is only read -- no face of a photo detected here is recorded
        #: and no candidate word's embedding is kept in it (a Just look run, where nothing
        #: is added to the library: tagpup.jobs.suggestions). What is computed stays in memory.
        self.remember = remember
        self.taxonomy = taxonomy
        #: The roots the tree files people under (#66), read once for the run: each photo's every neighbour asked the
        #: library again, a connection each (the module's people_roots).
        self.face_roots = taxonomy.people_roots()
        self.embedder = embedder
        self.faces = faces

        user_candidates = candidate_tags or []
        # Exclude face-matched folders/people names from candidate tags
        # to avoid redundant calculations and defer entirely to face matching.
        people_candidates = []

        # Merge and deduplicate candidates
        seen = set()
        combined = []
        for c in (user_candidates + people_candidates):
            c_clean = c.strip()
            if c_clean and vocabulary.keyword_key(c_clean) not in seen:
                seen.add(vocabulary.keyword_key(c_clean))
                combined.append(c_clean)

        self.candidate_tags = combined
        self.candidate_embeddings = {}
        self.year_candidate_embeddings = {}
        self._people = None
        self._people_lock = threading.Lock()

    def _known_people(self):
        """(lower-cased names of everyone known, their named faces as clustering.FaceRows -- the one gallery, keyed by the id of
        each person's node --, {that id: their Ref}; and
        {the id of each person the tree files: their tag}, which says exactly who a face resembles even when two people share a
        name).

        Loaded once per suggester -- one folder run -- rather than per photo. Both
        halves used to come from reading the whole faces table, twice a photo.
        """
        with self._people_lock:
            if self._people is None:
                names = set()
                for path in self.taxonomy.paths:
                    parts = vocabulary.segments(path)
                    if len(parts) >= 2 and vocabulary.key(parts[0]) in self.face_roots:
                        names.add(vocabulary.key(parts[-1]))
                # Read when the run begins (model_for_run): a read that fails fails the run, never "nobody is known".
                # Under the run's face model, as the photos' faces are (on_file): a switch while the run is under way
                # leaves it comparing the vectors of the model it began with, by that model's cuts.
                known = identify.named_faces(Library(self.index.db_path), self.faces.key)[1]
                labels = {person.id: person for person in known[1]}
                tags_by_id = self._tags_by_id()
                names.update(vocabulary.key(person.name) for person in labels.values())
                self._people = (names, known, labels, tags_by_id)
            return self._people

    def _tags_by_id(self):
        """{id: tag} of everyone the tree files as a person, read-only."""
        conn = db.connect(db.readonly_uri(self.index.db_path), uri=True)
        try:
            return {record["id"]: record["tag"] for record in person_ids.Directory.read(conn).records()}
        finally:
            conn.close()

    def knows_named_faces(self):
        """Does the library hold a named face to compare a photo's faces with?"""
        return self._known_people()[1][2] is not None

    def _precompute_candidates(self):
        """Precompute embeddings for candidate tags using a template."""
        if not self.candidate_tags or self.candidate_embeddings:
            return

        model_name, pretrained = self.embedder.model_name, self.embedder.pretrained

        needed_tags = []
        for tag in self.candidate_tags:
            prompt = suggesting.clip_prompt(tag)
            cached_emb = self.index.get_tag_embedding(tag, prompt, model_name, pretrained)
            if cached_emb is None and self.text_cache is not None:
                cached_emb = self.text_cache.get((prompt, model_name, pretrained))

            if cached_emb is not None:
                self.candidate_embeddings[tag] = cached_emb
            else:
                needed_tags.append((tag, prompt))

        if needed_tags:
            logger.info(f"Precomputing embeddings for {len(needed_tags)} candidate tags...")
            for tag, prompt in needed_tags:
                emb = self.embedder.embed_text(prompt)
                self.candidate_embeddings[tag] = emb
                if self.text_cache is not None:
                    self.text_cache.put((prompt, model_name, pretrained), emb)
                if self.remember:
                    self.index.save_tag_embedding(tag, prompt, model_name, pretrained, emb)

    def _get_candidate_embeddings_for_year(self, year: Optional[int]) -> Dict[str, List[float]]:
        """Get standard or year-specific candidate embeddings."""
        self._precompute_candidates()

        if year is None or not self.candidate_tags:
            return self.candidate_embeddings

        if year in self.year_candidate_embeddings:
            return self.year_candidate_embeddings[year]

        model_name, pretrained = self.embedder.model_name, self.embedder.pretrained

        year_embeddings = {}
        needed_tags = []
        # The roots the library flags as holding faces. This listed family, friends and
        # pets, and so prompted for everyone under People as a thing (#66).
        face_roots = self.face_roots
        for tag in self.candidate_tags:
            is_person = False
            for path in self.taxonomy.paths:
                parts = vocabulary.segments(path)
                if len(parts) >= 2 and vocabulary.key(parts[0]) in face_roots:
                    if (vocabulary.key(parts[-1]) == vocabulary.key(tag)
                            or vocabulary.keyword_key(path) == vocabulary.keyword_key(tag)):
                        is_person = True
                        break

            prompt = suggesting.clip_prompt(tag, year, person=is_person)

            cached_emb = self.index.get_tag_embedding(tag, prompt, model_name, pretrained)
            if cached_emb is None and self.text_cache is not None:
                cached_emb = self.text_cache.get((prompt, model_name, pretrained))

            if cached_emb is not None:
                year_embeddings[tag] = cached_emb
            else:
                needed_tags.append((tag, prompt))

        if needed_tags:
            logger.info(f"Computing era-aware candidate embeddings for year {year} ({len(needed_tags)} tags needed)...")
            for tag, prompt in needed_tags:
                emb = self.embedder.embed_text(prompt)
                year_embeddings[tag] = emb
                if self.text_cache is not None:
                    self.text_cache.put((prompt, model_name, pretrained), emb)
                if self.remember:
                    self.index.save_tag_embedding(tag, prompt, model_name, pretrained, emb)

        self.year_candidate_embeddings[year] = year_embeddings
        return year_embeddings

    def suggest_for_photo(
        self,
        photo_path: str,
        embedding: List[float],
        k: int = 15,
        min_sim: float = suggesting.NEIGHBOUR_CUT,
        target_metadata: Optional[Dict[str, Any]] = None,
        frames=None
    ) -> Dict[str, Any]:
        """Query index for nearest neighbors, expand hierarchical tags, aggregate and score tags. `frames`, called, gives a
        clip's frames (PhotoEmbeddings.frames_of: upright pictures, or NoFrames), whose faces offer its people; a clip
        given none (a measurement of tags alone, scripts/measure_pipeline_holdouts.py) is offered tags alone."""
        known_people, known_faces, _labels, tags_by_id = self._known_people()

        # 1. Search index for neighbors
        neighbors = self.index.search(embedding, k=k)

        # Filter neighbors by minimum cosine similarity
        valid_neighbors = [(sim, meta) for sim, meta in neighbors if sim >= min_sim]

        # 2. Extract path hints
        path_hints = extract_path_hints(photo_path)
        path_hints_lower = [h.lower() for h in path_hints]

        # Extract year of the target image
        target_year = dates.record_year(target_metadata) if target_metadata else None
        # Where and when, for who a face looks like: the year of its Date Taken as every matcher reads it (dates.era_year), and
        # its photo's id, the library's row at its path -- a run's record (the page's) carries none; a photo the library
        # does not hold (a look) has none, and no face of it to leave out.
        face_year = (dates.era_year(dates.date_taken(target_metadata["raw_metadata"], target_metadata.get("path"),
                                                     target_metadata.get("media"))) if target_metadata else None)
        photo_id = next(iter(store_roots.photo_ids_at(self.index.conn, [photo_path])), None) if self.index.conn else None
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
                neighbor_year = dates.record_year(meta)
                if neighbor_year is not None:
                    diff_years = abs(target_year - neighbor_year)
                    weight = math.exp(-decay_lambda * diff_years)

            weighted_sim = sim * weight

            # Only use non-people tags for propagation
            raw_tags = list(meta.get("tags", []))

            # Expand tags according to the taxonomy, leaving out the branches that hold people
            # (the tree's face roots, #66) and known people
            face_roots = self.face_roots
            expanded_tags = set()
            for tag in raw_tags:
                tag_parts = vocabulary.segments(tag)
                if tag_parts and vocabulary.key(tag_parts[0]) in face_roots:
                    continue
                if tag_parts and vocabulary.key(tag_parts[-1]) in known_people:
                    continue
                expanded = self.taxonomy.expand_tag(tag)
                for t in expanded:
                    t_parts = vocabulary.segments(t)
                    if t_parts and vocabulary.key(t_parts[0]) in face_roots:
                        continue
                    if t_parts and vocabulary.key(t_parts[-1]) in known_people:
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
                tag_parts = [vocabulary.key(p) for p in vocabulary.segments(tag)]

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

        # 4b. Face recognition suggestions. A read or a write of the library that fails here fails the photo,
        # which the run marks unread and tries again (tagpup.jobs.suggestions): never 'no face'.
        def on_file():
            """(whether the photo has faces on file, those to compare)."""
            detected, has_rows = [], False
            if self.index.conn:
                for row in store_faces.in_photo(self.index.conn, photo_path, self.faces.key):
                    box_json, emb_bytes, prob, excluded, name, name_source = row
                    has_rows = True
                    # A face someone excluded, or decided is nobody, is not to be
                    # named again by resemblance. It still counts as a face on
                    # file, so the photo is not sent through detection again.
                    if excluded or (name is None and name_source == "manual"):
                        continue
                    box = json.loads(box_json)
                    emb = np.frombuffer(emb_bytes, dtype=np.float32).tolist()
                    detected.append({
                        "box": box,
                        "embedding": emb,
                        "prob": prob
                    })
                # Detected before and none found: as good as faces on file (#773).
                if not has_rows:
                    has_rows = store_faces_detected.detected(
                        self.index.conn, photo_path, face_records.detector_of(self.faces))
            return has_rows, detected

        def offers_in(faces, size):
            """{person id: likeness} of the people the faces of ONE picture offer, the one rule of a photo and of each
            frame of a clip: a face not in the background (clustering.is_background, on `size`, the (width, height) its
            boxes are in) offers the person it is most like, by their closest face of the years around the photo, never
            one of its own (known_faces.likeness: an undated clip's face_year None compares every year), as far ahead
            of the next person as a name is offered at, by the cuts of the run's face model, whose vectors these are
            (clustering.Cuts.is_offered); two faces offering one person offer them at the better likeness. It was a
            mean face with no years, a distance cut of 0.90, and a score made up between 0.5 and 1."""
            offers = {}
            for face in faces:
                if face.get("embedding") is None or clustering.is_background(face["box"], size):
                    continue
                best_key, likeness, margin = clustering.best(known_faces.likeness(
                    clustering.unit(face["embedding"])[None, :], [face_year], [photo_id])[0], known_faces.columns)
                if best_key and clustering.cuts_of(self.faces.key).is_offered(likeness, margin):
                    offers[best_key] = max(likeness, offers.get(best_key, likeness))
            return offers

        # The pictures whose faces may offer a person: [(faces, the (width, height) their boxes are in)].
        pictures = []
        is_clip = images.kind_of(photo_path) not in store_photos.FACE_KINDS
        faces_unread = None
        if not is_clip:
            # Check database cache first
            has_face_rows, detected_faces = on_file()

            # A face model that waits for its turn on the graphics card (a Suggest run's,
            # tagpup.runtime) is waited for first, and the library asked again: the turn
            # may have been the index's, recording this photo's faces (#750).
            if not has_face_rows:
                self.faces.ready()
                has_face_rows, detected_faces = on_file()

            # If not in database, detect and embed on-the-fly
            if not has_face_rows:
                detected_faces = self.faces.detect_and_embed_faces(photo_path)

                # Keep what we just computed. Detection and embedding are the expensive
                # part of this whole pipeline, and they were previously discarded when the
                # request ended -- so a photo could be suggested for repeatedly and never
                # contribute a single face to the database. Recording them means ordinary
                # tagging feeds TagTuner's identify queue, and a folder added but not yet
                # indexed still accumulates face data. Strictly additive: photos that already have face
                # rows are left untouched, so manual names and exclusions are safe.
                if self.remember:
                    saved = face_records.record_detected(self.index.db_path, photo_path, detected_faces,
                                                         model=self.faces.key,
                                                         detector=face_records.detector_of(self.faces))
                    if saved:
                        logger.info(f"Recorded {saved} newly detected face(s) for {photo_path}")
            if detected_faces:
                # A face too small for its picture is in the background, on the photo's stored size, as cluster-faces
                # and the photo's strip ask it; a photo the library has no row for, a look, has no stored size and so
                # no face in the background.
                pictures.append((detected_faces, store_photos.picture_size(self.index.conn, photo_path)
                                 if self.index.conn else None))
        elif frames is not None and known_faces[2] is not None:
            # A clip (a kind the index does not look at for faces, store.photos.FACE_KINDS, by its name as the index
            # gave its row its kind): the people its frames show, each frame looked at as a photo is, the 40 px floor of
            # a new face on the frame's own pixels (new_faces) and the background on its size. Nothing of a face is
            # kept: a clip has no face on file, and the library refuses one (store.faces.insert; owner, 2026-11-13,
            # "PEOPLE ON CLIPS ARE TRANSIENT"). With no one named in the library there is no one to offer, and no
            # frame is read for it. Frames the file does not give as it is (NoFrames.lasting) leave the clip its tags,
            # the sentence kept beside them (`faces_unread`, said in the run's closing line), not as an error the run
            # would try again and again; frames not given for the moment -- a share away, ffmpeg past its deadline or
            # not found -- fail the clip, kept as nothing done, and the next run suggests for it again (the one
            # distinction of NoFrames, as photo_reembed draws it).
            try:
                shown = frames()
            except NoFrames as error:
                if not error.lasting:
                    raise
                shown, faces_unread = [], error.problem
            if shown:
                self.faces.ready()
            for number, frame in enumerate(shown, 1):
                picture = self.faces.picture_of_frame(frame, "%s, frame %d" % (photo_path, number))
                detected = self.faces.detect_and_embed(picture)
                # A frame the face model failed on (NotDetected) is not a frame with no one: not looked at, as a
                # photo's detection that failed is not run (face_records.ran), and not counted in the frames' `of`.
                if face_records.ran(detected):
                    pictures.append((self.faces.new_faces(detected, picture), frame.size))
            if shown and not pictures:
                # The model read none of them: the moment's, as frames not given for the moment are.
                raise RuntimeError("The face model could not look at any frame of the clip.")

        if pictures and known_faces[2] is not None:
            # A person any picture offers stands: no second threshold across a clip's frames (clustering.across_frames).
            for person, (likeness, seen) in clustering.across_frames(
                    [offers_in(faces, size) for faces, size in pictures]).items():
                # Resolve leaf name to full taxonomy path if possible.
                # This looked under a hardcoded "family"/"friends"/
                # "pets" only, so anyone filed under People came back
                # as a bare leaf and was suggested -- and written --
                # in the one form the keywords must not hold.
                # A name filed twice is not resolved to either
                # path, and not offered at all: a guess names the
                # wrong person (docs/findings.md, #27).
                # A person is the tag of their node: another person called alike is another tag. A face
                # whose person the tree no longer files (or never did: a name from before ids) suggests nobody.
                resolved_path = tags_by_id.get(person)
                if resolved_path is None:
                    continue
                score = round(likeness, 2)

                # Boost or insert tag
                item = next((t for t in suggested_tags if _ends_with(t["tag"], resolved_path)), None)
                if item is not None:
                    item["score"] = max(item["score"], score)
                else:
                    item = {"tag": resolved_path, "score": score, "source_count": 1, "has_face_match": True}
                    suggested_tags.append(item)
                if is_clip:
                    # In how many of the frames looked at (SuggestionModel.offered: the chip says it).
                    item["frames"] = {"seen": seen, "of": len(pictures)}

        # 5. Zero-shot candidate suggestions (new potential tags)
        active_candidates = self._get_candidate_embeddings_for_year(target_year)
        if active_candidates:
            image_np = np.array(embedding, dtype=np.float32)
            image_norm = np.linalg.norm(image_np)
            if image_norm > 0:
                image_np = image_np / image_norm
            word_cut = photo_models.word_cut(self.embedder.model_name, self.embedder.pretrained)

            for tag, tag_emb in active_candidates.items():
                # Skip if this tag is a person tag (to defer entirely to face matching)
                if vocabulary.key(tag) in known_people:
                    continue

                # Skip if this tag is already suggested by neighbors
                if any(_ends_with(t["tag"], tag) for t in suggested_tags):
                    continue

                tag_np = np.array(tag_emb, dtype=np.float32)
                tag_norm = np.linalg.norm(tag_np)
                if tag_norm > 0:
                    tag_np = tag_np / tag_norm

                # Cosine similarity
                sim = float(np.dot(image_np, tag_np))

                # The model's word cut: its cosines are on its own scale (tagpup.ml.photo_models).
                if sim >= word_cut:
                    # Map a person's name back to the path they are filed under, under
                    # whichever root this library uses rather than an assumed one.
                    filed = self.taxonomy.person_paths(tag)
                    if len(filed) > 1:
                        continue   # filed twice: no guess (docs/findings.md, #27)
                    resolved_tag = filed[0] if filed else tag

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
                if _is_above(tag, active_tag):
                    is_redundant = True
                    break
                # Check if active_tag is a hierarchical tag whose leaf node matches the current tag (e.g. 'Family/Laurel' implies 'Laurel')
                active_parts = vocabulary.segments(active_tag)
                if len(active_parts) >= 2 and vocabulary.key(active_parts[-1]) == vocabulary.key(tag):
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

        found = {
            "path": photo_path,
            "suggested_tags": suggested_tags,
            "suggested_caption": suggested_caption,
            "neighbor_people": neighbor_people,
            "path_hints": path_hints,
            "nearest_neighbors": neighbors_output
        }
        if faces_unread:
            found["faces_unread"] = faces_unread
        return found

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
            if len(vocabulary.segments(tag)) == 1:
                resolved = self.taxonomy.find_by_leaf(tag)
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

                    is_context_tag = (len(vocabulary.segments(tag)) > 1
                                      and vocabulary.root_of(tag) in vocabulary.CONTEXT_ROOTS)

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


class SuggestionModel:
    """What a suggestion run calls (tagpup.jobs.suggestions.SuggestionRuns.start): one
    library's suggester and its photos' vectors, readied for one run."""

    def __init__(self, suggester, embeddings, face_roots):
        self.suggester, self.embeddings = suggester, embeddings
        # Read once for the run: the caption files people under them (#66).
        self.face_roots = face_roots

    @property
    def model_key(self):
        """The model its suggestions are made with (tagpup.store.embeddings)."""
        return self.embeddings.model_key

    def suggest(self, photo, meta):
        """The suggestion for one photo. A clip's frames are read once: those its vector was made from, when it was
        made now; else read for its people (TagSuggester.suggest_for_photo's `frames`), only when it is a clip."""
        decoded = []
        vector = self.embeddings.of(photo, decoded=decoded)
        return self.suggester.suggest_for_photo(
            photo, vector, k=15, min_sim=suggesting.NEIGHBOUR_CUT, target_metadata=meta,
            frames=lambda: decoded or self.embeddings.frames_of(photo))

    def offered(self, suggestion):
        """What the panel shows for one suggestion: tags, people, title -- what Apply All and the writers take too.

        A person a face offered is shown whatever the score: the face model's own cut made the offer
        (clustering.cuts_of(model).is_offered, scored by the likeness), and is its one owner; the tag floor
        (suggesting.OFFER_A_TAG) was read on tags, and on FaceNet's likenesses never bit (its offers are from 0.70),
        while it hid the new model's from 0.310 (owner's decision, 2026-10-20). A tag is shown from the floor."""
        tags, people = [], []
        for item in suggestion.get("suggested_tags", []):
            score = item.get("score", 0.0)
            if item.get("has_face_match"):
                # A clip's: in how many of its frames looked at (the chip's title, web/tagpup/suggestions.js).
                people.append(dict({"name": item["tag"], "score": score},
                                   **({"frames": item["frames"]} if "frames" in item else {})))
            elif score >= suggesting.OFFER_A_TAG:
                tags.append({"tag": item["tag"], "score": score})
        return tags, people, suggesting.caption_from_tags(
            [t["tag"] for t in tags] + [p["name"] for p in people], self.face_roots)

    def consensus(self, suggestions):
        return self.suggester.apply_folder_consensus(suggestions)

    def what_it_lacks(self):
        """What the library had too little of to compare a photo with, for the run to say
        when it is done: [sentence]. A new library holds no vectors and no named faces, and
        its photos are then suggested nothing -- not an error, so the page is told why. The
        sentences' one owner: a run that saves and one that only looks both say these
        (tagpup.jobs.suggestions._lacks)."""
        notes = []
        if self.suggester.index.indexed == 0:
            notes.append("The library has no photo vectors yet, so no tags are suggested from similar photos.")
        if not self.suggester.knows_named_faces():
            notes.append("The library has no named faces yet, so no people are suggested.")
        return notes


def neighbours_model(photo_index, faces):
    """A suggester for the photos of `photo_index` (loaded) from their neighbours' tags alone: no word is asked of CLIP,
    so no model is loaded, and nothing is kept in the library (a Just look's `remember` False). `faces`, the face model
    whose faces on file are read. The measurement of Suggest's neighbour cut, the one cut the words do not pass
    (scripts/measure_pipeline_holdouts.py, clips; findings #2626)."""
    taxonomy = TagTaxonomy(photo_index.db_path)
    taxonomy.load()
    suggester = TagSuggester(photo_index, taxonomy, embedder=None, candidate_tags=[], faces=faces, remember=False)
    return SuggestionModel(suggester, None, suggester.face_roots)


def model_for_run(photo_index, clip, faces, configured_words, *, embeddings, remember=True):
    """Ready the suggester for one run over a library, on the run's thread: its tag tree,
    the words CLIP is asked about -- `configured_words`, the library's, and the tree's, but
    no one's name (tagpup.core.suggesting) -- and their embeddings, before the parallel
    photos need them. `photo_index` is the library's, loaded; `clip` and `faces` the
    models, and `embeddings` the photos' vectors under `clip` (tagpup.runtime.Runtime.embeddings, which gives it
    ffmpeg for a clip's frames). Not `remember`: nothing computed is kept in the library -- no
    vector, no face, no candidate word's embedding -- and what it reads of it is what it
    holds (a Just look run)."""
    taxonomy = TagTaxonomy(photo_index.db_path)
    taxonomy.load()
    candidates = suggesting.zero_shot_words(configured_words, taxonomy.paths, taxonomy.people_roots())
    text_cache = None if remember else looking_text_embeddings(photo_index.db_path)
    suggester = TagSuggester(photo_index, taxonomy, embedder=clip, candidate_tags=candidates, faces=faces,
                             remember=remember, text_cache=text_cache)
    suggester._precompute_candidates()
    # Who is known, read once for the run, now: a read that fails fails the run's start, with its sentence.
    suggester.knows_named_faces()
    return SuggestionModel(suggester, embeddings, suggester.face_roots)
