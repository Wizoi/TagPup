"""The three gating checks of docs/AI_PIPELINE.md, phase 0. Numbers only: nothing it prints or writes names a photo
or a person.

    set TAGPUP_HOME=<the owner's home>      (the libraries are read through the home's machine map)
    .venv/Scripts/python.exe scripts/measure_pipeline_holdouts.py faces  --source data/photo_index.db --out <folder>
    .venv/Scripts/python.exe scripts/measure_pipeline_holdouts.py vectors --source data/photo_index.db --out <folder>
    .venv/Scripts/python.exe scripts/measure_pipeline_holdouts.py models --source data/photo_index.db --out <folder>
        --work <folder> [--candidates model:weights:size ...] [--packages <folder>]

* `faces` (c), read-only, no model: every named face of the library (services.identify.named_faces, today's FaceNet
  vectors) is matched against the named faces of every OTHER photo -- held out by photo, never by face, so a face is
  never matched with its own photo's -- by the closest single face of each person (tagpup.core.holdout, which the
  gate of phase 3 reads too). The top person, its likeness and
  its margin over the second-best person give the precision of the top suggestion at each likeness cut and each
  margin cut, with the counts behind each point. A face whose person has no face in another photo cannot be matched
  by any recogniser and is counted apart.
* `vectors` (a): the stratified sample of scripts/measure_pipeline_stages.py, copied at the paths its rows have into
  a sandbox's copy of the library, embedded again by the runtime's CLIP model one photo at a time (the indexer's
  way, ClipModel.input_of and embed_input) and in batches of 8 and 16 (the picture the model saw, captured by a hook on its visual
  tower, stacked and run at fp16): the cosine of each against the vector the library holds. Expected 0.999 or more.
* `models` (b), phase 5: a copy of the library in `--work` (kept between runs, so a stopped run is taken up where it
  stopped) given every photo's vector under each candidate model (a model key of its own, in the copy only), and a
  held-out set of its photos with a tag that is not a person, weighted to many faces, scored by Suggest
  (services.suggester, a Just look run) under each model, left out by photo and by folder, at Suggest's neighbour cut
  and at none: precision and recall of the tags offered and of its first 1 and 5 against the photo's own tags (people
  left out: they come from faces), the word path alone, seconds a photo, the vector's dimension, the card's memory.
  One file a candidate in `--out`, written as it is done. SigLIP 2's tokenizer needs `transformers`, which the venv
  does not hold: `--packages` names a folder holding it.

Each check that loads a model takes the card in the owner's line (tagpup.runtime), in a sandbox deleted after.
"""
import argparse
import collections
import json
import os
import random
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sandbox  # noqa: E402

import _root  # noqa: E402,F401
import numpy as np  # noqa: E402
from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core import holdout, paths, processes, suggesting, vocabulary  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import identify  # noqa: E402

# ---- (c) the named faces ------------------------------------------------------------------------------------------

def faces_check(library, report):
    """(c), read-only: the precision of the top suggestion over the named faces, held out by photo and, stricter, by
    folder (an event's other photos: bursts and copies of one moment are in one folder); over every named face, and
    over the faces a person decided alone (services.identify.decided_faces: a name clustering gave at 0.80 or more
    would grade the curve by its own cut)."""
    started = time.time()
    _stamp, (ids, refs, matrix) = identify.named_faces(library)
    _stamp, (decided_ids, _refs, _matrix) = identify.decided_faces(library)
    people_ids = sorted({ref.id for ref in refs})
    report("  %d named faces (%d decided), %d people" % (len(ids), len(decided_ids), len(people_ids)))
    photo_of = {}
    for person in people_ids:
        for face in identify.person_faces(library, person, limit=-1)["faces"]:
            photo_of[face["id"]] = face["photo_path"]
    known = [i for i, face_id in enumerate(ids) if face_id in photo_of]
    decided = set(decided_ids)
    matrix = np.asarray(matrix, dtype=np.float32)
    answer = {"named_faces": len(ids), "decided_faces": len(decided_ids), "people": len(people_ids),
              "without_a_photo": len(ids) - len(known)}
    for label, rows in (("named", known), ("decided", [i for i in known if ids[i] in decided])):
        people = np.array([refs[i].id for i in rows])
        photos = [photo_of[ids[i]] for i in rows]
        answer[label] = {
            "by_photo": holdout.hold_out(matrix[rows], people, holdout.numbered(paths.key(p) for p in photos)),
            "by_folder": holdout.hold_out(matrix[rows], people,
                                          holdout.numbered(paths.key(os.path.dirname(p)) for p in photos))}
    answer["seconds"] = time.time() - started
    return answer


# ---- (a) the vectors again ---------------------------------------------------------------------------------------

def cosines(a, b):
    """The cosine of each row of `a` with the same row of `b`."""
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    return (a * b).sum(axis=1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1))


def cosine_summary(values, floor=0.999):
    """sandbox.summary of the cosines, with the least and how many fall below `floor`."""
    values = [float(v) for v in values]
    found = sandbox.summary(values)
    found.update(min=min(values) if values else None, below=sum(1 for v in values if v < floor), floor=floor)
    return found


def turned_apart(values, kinds, floor=0.999):
    """The cosines of the photos turned by their Orientation and of the rest, apart, and what each photo below
    `floor` is (its stratum, turned, megapixels): a vector kept from before CLIP saw photos upright reads low."""
    turned = [float(v) for v, kind in zip(values, kinds) if kind["turned"]]
    upright = [float(v) for v, kind in zip(values, kinds) if not kind["turned"]]
    return {"turned": cosine_summary(turned, floor), "not_turned": cosine_summary(upright, floor),
            "below": [dict(kind, cosine=float(v)) for v, kind in zip(values, kinds) if v < floor]}


def _model_sandbox(source, report):
    """A sandbox with the library's copy, its roots placed in it: (folder, the copy, {root: place})."""
    folder = tempfile.mkdtemp(prefix="tagpup_holdout_")
    report("sandbox %s" % folder)
    os.makedirs(os.path.join(folder, "data"))
    target = os.path.join(folder, "data", "measured.db")
    sandbox.copy_library(source, target)
    return folder, target, sandbox.place_roots(target, folder)


def vectors_check(source, rows, size, seed, report):
    """(a): stored against one at a time against batched, on the stratified sample."""
    import torch
    from tagpup.runtime import Runtime
    from tagpup.services import search
    chosen = sandbox.choose_sample(rows, size, rng=random.Random(seed))
    folder, target, placed = _model_sandbox(source, report)
    try:
        copies = sandbox.copy_at_rows(chosen, placed, tagpup_config.machine_roots())
        sandbox.enter(folder)
        runtime, library = Runtime(), Library(target)
        clip = runtime.clip(library)
        index = search.PhotoIndex(target, runtime.model_key(library))
        index.open()
        embeddings = search.PhotoEmbeddings(clip, index, remember=False)
        stored, single, pictures, skipped, kinds = [], [], [], {}, []
        with runtime.gpu_turn("measuring the vectors again (AI pipeline phase 0)", (clip,)):
            clip.load()
            seen = []
            hook = clip.model.visual.register_forward_pre_hook(lambda _m, inputs: seen.append(inputs[0].detach().cpu()))
            for row in chosen:
                kept = embeddings.cached(copies[row["path"]])
                if kept is None:
                    skipped["no vector held"] = skipped.get("no vector held", 0) + 1
                    continue
                try:
                    vector = clip.embed_input(clip.input_of(copies[row["path"]]))
                except OSError as problem:   # images.Unreadable, or a read that failed
                    skipped[type(problem).__name__] = skipped.get(type(problem).__name__, 0) + 1
                    seen.clear()
                    continue
                stored.append(kept)
                single.append(vector)
                kinds.append({"stratum": row["stratum"], "turned": row["turned"], "megapixels": row["megapixels"]})
                pictures.append(seen.pop())
                seen.clear()
            hook.remove()
            batched = {}
            for batch in (8, 16):
                found = []
                try:
                    for start in range(0, len(pictures), batch):
                        stack = torch.cat(pictures[start:start + batch]).to(clip.device)
                        with torch.inference_mode():
                            features = clip.model.encode_image(stack)
                            features /= features.norm(dim=-1, keepdim=True)
                        found += features.float().cpu().numpy().tolist()
                    batched[batch] = found
                except torch.cuda.OutOfMemoryError:
                    batched[batch] = "out of memory on the card"
        index.close()
        again = cosines(stored, single)
        answer = {"photos": len(stored), "skipped": skipped, "stored_vs_one_at_a_time": cosine_summary(again),
                  "turned": turned_apart(again, kinds)}
        for batch, found in batched.items():
            answer["stored_vs_batch_%d" % batch] = (cosine_summary(cosines(stored, found)) if isinstance(found, list)
                                                    else found)
            if isinstance(found, list):
                answer["one_at_a_time_vs_batch_%d" % batch] = cosine_summary(cosines(single, found))
        return answer
    finally:
        answer_deleted = sandbox.remove_sandbox(folder)
        report("sandbox deleted: %s" % answer_deleted)


# ---- (b) Suggest under each candidate photo model (phase 5) -----------------------------------------------------

#: docs/AI_PIPELINE.md, phase 5: `model:weights:size`, an empty size the model's own. The first is the libraries'
#: model and size today, whose vectors the copy already holds: the baseline, embedded by nothing here.
CANDIDATES = (
    "ViT-H-14:laion2b_s32b_b79k:512",
    "ViT-H-14:laion2b_s32b_b79k:224",
    "ViT-H-14-quickgelu:dfn5b:",
    "ViT-B-16-SigLIP2:webli:",
    "ViT-L-16-SigLIP2-256:webli:",
    "ViT-SO400M-14-SigLIP2:webli:",
)

#: Suggest's own neighbour cut (suggester.SuggestionModel.suggest), and none: a cut read on one model's likenesses is
#: not another's (phase 5 reads its own again), so every model is scored at both.
CUTS = (0.35, 0.0)

#: The neighbours Suggest reads (suggester.SuggestionModel.suggest).
NEIGHBOURS = 15

#: A pass that cannot read more than this share of its photos stops and scores nothing: a share gone is not a library
#: of fewer photos.
UNREAD_LIMIT = 0.01


def candidate(text):
    """{"name", "pretrained", "size"} of `model:weights:size`, size None for the model's own."""
    parts = [part.strip() for part in text.split(":")]
    if len(parts) != 3 or not parts[0] or not parts[1] or not (parts[2] == "" or parts[2].isdigit()):
        raise ValueError("A candidate is model:weights:size, the size empty for the model's own: %r" % text)
    return {"name": parts[0], "pretrained": parts[1], "size": int(parts[2]) if parts[2] else None}


def label_of(model):
    """The candidate as the output names it: "ViT-H-14 laion2b_s32b_b79k 512"."""
    return "%s %s %s" % (model["name"], model["pretrained"], model["size"] or "native")


def candidate_settings(own, model):
    """The library's settings `own` with the candidate's model in them: a LibrarySettings for this run, the copy's own
    settings unchanged."""
    from tagpup.services.settings import LibrarySettings
    return LibrarySettings(dict(own.values, **{"model.name": model["name"], "model.pretrained": model["pretrained"],
                                               "model.force_image_size": str(model["size"] or "")}))


class LeavingOut:
    """A photo index whose search leaves out `left_out` (a set of ids): the photo being scored, whose own vector would
    be its nearest neighbour and hand it its own tags, or its whole folder (an event's photos, tagged together: a
    folder that has just arrived is untagged). The likenesses of each search's answer are kept in `seen`."""

    def __init__(self, index):
        self.index, self.left_out, self.seen = index, frozenset(), []

    def __getattr__(self, name):
        return getattr(self.index, name)

    def search(self, query_vector, k=15):
        found = [(sim, record) for sim, record in self.index.search(query_vector, k=k + len(self.left_out))
                 if record.get("id") not in self.left_out][:k]
        self.seen.append([sim for sim, _record in found])
        return found


class NoFaceLooks:
    """The run's face model, never run: the scores are of tags, not people, and a photo with no face on file would be
    sent through detection, the face model on the card beside CLIP."""

    def __init__(self, faces):
        self.faces = faces

    def __getattr__(self, name):
        return getattr(self.faces, name)

    def ready(self):
        pass

    def detect_and_embed_faces(self, *_args, **_kwargs):
        return []


def tag_scores(offered, truths):
    """{"photos", "offered", "right", "truth", "precision", "recall", "photos_with_a_right_tag"} of the tags `offered`
    for each photo against its own `truths` (two lists of sets, photo by photo)."""
    offered_count = sum(len(o) for o in offered)
    right = sum(len(o & t) for o, t in zip(offered, truths))
    truth = sum(len(t) for t in truths)
    return {"photos": len(offered), "offered": offered_count, "right": right, "truth": truth,
            "precision": right / offered_count if offered_count else None,
            "recall": right / truth if truth else None,
            "photos_with_a_right_tag": sum(1 for o, t in zip(offered, truths) if o & t)}


def _a_person(tag, face_roots):
    """Is `tag` under a branch that holds people (the tree's face roots, as Suggest leaves them out)?"""
    parts = vocabulary.segments(tag)
    return bool(parts) and vocabulary.key(parts[0]) in face_roots


def own_tags(record, taxonomy, face_roots):
    """The photo's own tags that are not people, each with the branches above it (taxonomy.expand_tag): what a
    suggestion is scored against."""
    return {part for tag in record.get("tags", []) if not _a_person(tag, face_roots)
            for part in taxonomy.expand_tag(tag)}


def neighbour_likeness(seen):
    """The likeness of the nearest neighbour of each search `seen` ([[likeness]]), and the share of every neighbour
    read at Suggest's cut or above."""
    every = [sim for sims in seen for sim in sims]
    return {"nearest": sandbox.summary([sims[0] for sims in seen if sims]),
            "share_at_0.35": sum(1 for sim in every if sim >= 0.35) / len(every) if every else None}


def suggest_scores(model, held, folders, truths):
    """Suggest's tags for each held-out photo of `held` [(record, vector)], left out by photo and by folder
    (`folders`: {id: the ids of its folder}), at each of CUTS: the tags offered (SuggestionModel.offered: what the
    panel shows and Apply All writes), its first and its first 5 by score, against `truths`; how many tags offered
    were words CLIP was asked about (zero-shot, by the suggester's own cut) rather than neighbours'; and the
    neighbours' likenesses."""
    leaving = LeavingOut(model.suggester.index)
    model.suggester.index = leaving
    model.suggester.faces = NoFaceLooks(model.suggester.faces)
    answer = {}
    for held_out in ("by_photo", "by_folder"):
        for cut in CUTS:
            offered, first, top5, from_words = [], [], [], 0
            leaving.seen = []
            for record, vector in held:
                leaving.left_out = frozenset([record["id"]]) if held_out == "by_photo" else folders[record["id"]]
                found = model.suggester.suggest_for_photo(record["path"], vector, k=NEIGHBOURS, min_sim=cut,
                                                          target_metadata=record)
                tags, _people, _caption = model.offered(found)
                offered.append({tag["tag"] for tag in tags})
                words = {item["tag"] for item in found.get("suggested_tags", []) if item.get("is_new_recommendation")}
                from_words += len(offered[-1] & words)
                ranked = [item["tag"] for item in found.get("suggested_tags", []) if not item.get("has_face_match")]
                first.append(set(ranked[:1]))
                top5.append(set(ranked[:5]))
            answer["%s, cut %.2f" % (held_out, cut)] = {
                "offered": tag_scores(offered, truths), "top_1": tag_scores(first, truths),
                "top_5": tag_scores(top5, truths), "offered_from_words": from_words,
                "neighbours": neighbour_likeness(leaving.seen)}
    return answer


def word_scores(model, clip, held, truths):
    """The word path alone: each of the library's tags that are not people, as CLIP's sentence for its last part
    (suggesting.clip_prompt), ranked by likeness to each held-out photo; its first and first 5 against `truths`. No
    cut: a ranking reads alike on any model's likenesses, where the suggester's 0.23 is today's model's."""
    tags = sorted(tag for tag in model.suggester.taxonomy.paths
                  if vocabulary.segments(tag) and not _a_person(tag, model.face_roots))
    words = np.array([clip.embed_text(suggesting.clip_prompt(vocabulary.segments(tag)[-1])) for tag in tags],
                     dtype=np.float32)
    words /= np.linalg.norm(words, axis=1, keepdims=True)
    first, top5 = [], []
    for _record, vector in held:
        order = np.argsort(-(words @ np.asarray(vector, dtype=np.float32)))[:5]
        first.append({tags[order[0]]})
        top5.append({tags[i] for i in order})
    return {"tags": len(tags), "top_1": tag_scores(first, truths), "top_5": tag_scores(top5, truths)}


def time_photos(clip, rows):
    """Decode, pad and resize (ClipModel.input_of) and the forward (embed_input) of each of `rows`, one at a time as
    the indexer runs a photo, the first untimed: seconds a photo."""
    reads, forwards, unread = [], [], 0
    for number, row in enumerate(rows):
        started = time.perf_counter()
        try:
            picture = clip.input_of(row["path"])
        except OSError:   # images.Unreadable, or a read that failed
            unread += 1
            continue
        read = time.perf_counter()
        clip.embed_input(picture)
        if number:
            reads.append(read - started)
            forwards.append(time.perf_counter() - read)
    return {"photos": len(reads), "unread": unread, "decode_pad_resize": sandbox.summary(reads),
            "forward": sandbox.summary(forwards), "a_photo": sandbox.summary([a + b for a, b in zip(reads, forwards)])}


class Unreachable(Exception):
    """More of a pass's photos could not be read than UNREAD_LIMIT allows."""


def embed_pass(clip, todo, keep, report, workers=6, batch=256):
    """Each of `todo` [(file, the copy's path)] embedded by `clip`: decoded, padded and resized (ClipModel.input_of,
    never loading the model) on `workers` threads ahead of the forward on this one, as the indexer reads ahead, and
    handed to `keep([(the copy's path, (mtime, size), vector)])` `batch` at a time, the last ones too when the pass is
    stopped: what was kept is not embedded again by the next run. A photo that cannot be read is counted by why; more
    than UNREAD_LIMIT of them raise Unreachable. {"embedded", "unread", "seconds"}."""
    unread, waiting, embedded = {}, [], 0
    limit = max(20, int(UNREAD_LIMIT * len(todo)))
    started = time.perf_counter()

    def read(item):
        stat = os.stat(item[0])
        return item, (stat.st_mtime, stat.st_size), clip.input_of(item[0], load=False)

    items = iter(todo)
    pending = collections.deque()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        def fill():
            while len(pending) < 2 * workers:
                item = next(items, None)
                if item is None:
                    return
                pending.append(pool.submit(read, item))
        try:
            fill()
            while pending:
                future = pending.popleft()
                fill()
                try:
                    item, stamp, picture = future.result()
                except OSError as problem:   # images.Unreadable, a file gone, a share that does not answer
                    unread[type(problem).__name__] = unread.get(type(problem).__name__, 0) + 1
                    if sum(unread.values()) > limit:
                        raise Unreachable("%d of %d photos could not be read (%s): the pass stops." % (
                            sum(unread.values()), len(todo), unread)) from problem
                    continue
                waiting.append((item[1], stamp, clip.embed_input(picture)))
                embedded += 1
                if len(waiting) >= batch:
                    keep(waiting)
                    waiting = []
                if embedded % 5000 == 0:
                    report("  %d of %d embedded, %.0f s" % (embedded, len(todo), time.perf_counter() - started))
        finally:
            for future in pending:
                future.cancel()
            if waiting:
                keep(waiting)
    return {"embedded": embedded, "unread": unread, "seconds": time.perf_counter() - started}


def card_use(samples, start):
    """{"before", "peak", "peak_over_before"} MiB of the card's memory (Sampler.gpu's samples) before `start` and
    from it: memory held before is the other programs'."""
    before = [mib for at, _use, mib in samples if at < start]
    during = [mib for at, _use, mib in samples if at >= start]
    if not before or not during:
        return {"samples": len(samples)}
    return {"before": max(before), "peak": max(during), "peak_over_before": max(during) - max(before)}


def slug(model):
    """The file name of a candidate's results."""
    return "model-" + "".join(c if c.isalnum() or c in ".-" else "_" for c in label_of(model)) + ".json"


def write_json(path, value):
    """`value` written whole or not at all: a run stopped while it writes leaves the file before it."""
    with open(path + ".part", "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=1)
    os.replace(path + ".part", path)


def run_candidate(runtime, library, target, model, sample, report):
    """One candidate, in the card's turn: every photo of the copy that holds no vector under its model embedded and
    kept in the copy (resumable: a stopped pass is taken up where it stopped), the timing photos timed one at a time,
    and the held-out photos scored by Suggest and by the word path. The result, or Unreachable; a model that does not
    load, or runs out of memory on the card, raises what it raised."""
    from tagpup.services import search, suggester
    from tagpup.store import embeddings as store_embeddings
    settings = candidate_settings(runtime.settings(library), model)
    key = runtime.model_key(library, settings)
    clip = runtime.clip(library, settings)
    result = {"candidate": label_of(model), "model_key": key}
    index = search.PhotoIndex(target, key)
    index.open()

    def keep(found):
        def store(conn):
            for path, (mtime, size), vector in found:
                store_embeddings.put(conn, path, key, mtime, size, np.asarray(vector, dtype=np.float32).tobytes())
        index.write_on_own_connection(store, label="vectors under %s" % key)

    sampler = sandbox.Sampler(interval=250).start()
    try:
        time.sleep(1.5)   # what the card holds before: the other programs'
        start = time.time()
        with runtime.gpu_turn("measuring a photo model (AI pipeline phase 5)", (clip,)):
            loading = time.perf_counter()
            clip.load()
            result["load_seconds"] = time.perf_counter() - loading
            result["timing"] = time_photos(clip, sample["timing"])
            copy_rows = {photo_id: (path, vectored) for photo_id, path, _mtime, _size, vectored in index.stamps()}
            todo = [(row["path"], copy_rows[row["id"]][0]) for row in sample["rows"]
                    if row["id"] in copy_rows and not copy_rows[row["id"]][1]]
            report("  %s: %d photos to embed of %d" % (label_of(model), len(todo), len(sample["rows"])))
            result["pass"] = embed_pass(clip, todo, keep, report)
            index.close()
            index.load()
            result.update(vectors=index.indexed, dimension=index.dim, rows_in_the_copy=index.count,
                          rows_in_the_copy_before=len(copy_rows))
            held = []
            for record in index.records(sample["held"]):
                kept = store_embeddings.get_by_id(index.conn, record["id"], key)
                if kept is not None:
                    held.append((record, np.frombuffer(kept.vector, dtype=np.float32).tolist()))
            model_run = suggester.model_for_run(index, clip, runtime.faces(library, settings),
                                                runtime.candidate_words(library),
                                                detect_max_side=settings.detect_max_side, remember=False)
            truths = [own_tags(record, model_run.suggester.taxonomy, model_run.face_roots) for record, _v in held]
            scoring = time.perf_counter()
            result["held_out"] = len(held)
            result["suggest"] = suggest_scores(model_run, held, sample["folders"], truths)
            result["words"] = word_scores(model_run, clip, held, truths)
            result["scoring_seconds"] = time.perf_counter() - scoring
    finally:
        sampler.stop()
        index.close()
    result["card_mib"] = card_use(sampler.gpu, start)
    return result


def models_check(source, work, out, candidates, census, size, timing, seed, report):
    """(b), phase 5: the copy of the library in `work` (made once, kept between runs), and each of `candidates`
    measured by run_candidate into `out`, one file each, written as it is done: a candidate whose file is there is
    not run again, and one that fails is reported and the next one run. `census` (rows, counts) is the source's."""
    from tagpup.runtime import Runtime
    from tagpup.services import search
    from tagpup.store.taxonomy import TagTaxonomy
    rows, counts = census
    target = os.path.join(work, "data", "measured.db")
    ready = os.path.join(work, "copy-is-ready")
    if not os.path.exists(ready):
        if os.path.exists(target):
            os.remove(target)   # a copy stopped part-way
        os.makedirs(os.path.dirname(target), exist_ok=True)
        sandbox.copy_library(source, target)
        sandbox.place_roots(target, work)
        open(ready, "w").close()
    sandbox.enter(work)
    library = Library(target)
    runtime = Runtime()

    # The held-out photos: of the photos with a tag that is not a person, many-face weighted (sandbox.choose_sample).
    index = search.PhotoIndex(target)
    index.open()
    taxonomy = TagTaxonomy(target)
    taxonomy.load()
    roots = taxonomy.people_roots()
    tagged = {}
    for record in index.records():
        tags = {tag for tag in record["tags"] if not _a_person(tag, roots)}
        if tags:
            tagged[record["id"]] = tags
    index.close()
    eligible = [row for row in rows if row["id"] in tagged]
    held = sandbox.choose_sample(eligible, size, rng=random.Random(seed))
    by_folder = collections.defaultdict(set)
    for row in rows:
        by_folder[row["folder"]].add(row["id"])
    sample = {"rows": rows, "held": [row["id"] for row in held],
              "folders": {row["id"]: frozenset(by_folder[row["folder"]]) for row in held},
              "timing": sandbox.choose_sample(rows, timing, rng=random.Random(seed))}
    write_json(os.path.join(out, "models-sample.json"), {
        "census": sandbox.census_counts(rows, counts), "versions": sandbox.versions(),
        "photos_with_a_tag_not_a_person": len(eligible),
        "tags_not_people": len({tag for tags in tagged.values() for tag in tags}),
        "their_folders": len({row["folder"] for row in eligible}),
        "held_out": len(held), "held_out_by_stratum": collections.Counter(row["stratum"] for row in held),
        "held_out_folders": len({row["folder"] for row in held}),
        "held_out_folder_photos": sandbox.summary([len(by_folder[row["folder"]]) for row in held]),
        "timing_photos": len(sample["timing"])})
    report("  %d photos, %d with a tag not a person, %d held out" % (len(rows), len(eligible), len(held)))

    done = {}
    for model in candidates:
        path = os.path.join(out, slug(model))
        if os.path.exists(path):
            report("%s: done before, kept" % label_of(model))
            continue
        report("%s" % label_of(model))
        try:
            result = run_candidate(runtime, library, target, model, sample, report)
        except Exception as problem:   # a model that does not load, the card out of memory, a share gone
            done[label_of(model)] = "%s: %s" % (type(problem).__name__, str(problem)[:300])
            report("  skipped: %s" % done[label_of(model)])
            continue
        write_json(path, result)
        done[label_of(model)] = "written"
    return done


def table(out, report):
    """Each candidate's results in `out`, one line each, for the eye."""
    for name in sorted(os.listdir(out)):
        if not (name.startswith("model-") and name.endswith(".json")):
            continue
        with open(os.path.join(out, name), encoding="utf-8") as handle:
            found = json.load(handle)
        scores = found["suggest"]
        line = ["%-34s" % found["candidate"], "dim %4d" % found["dimension"],
                "%.3f s" % found["timing"]["a_photo"]["mean"], "card +%s MiB" % found["card_mib"].get("peak_over_before")]
        for kind in ("by_photo, cut 0.35", "by_folder, cut 0.35", "by_folder, cut 0.00"):
            got = scores[kind]
            line.append("%s: top1 %.3f top5 P %.3f R %.3f offered P %.3f R %.3f" % (
                kind, got["top_1"]["precision"] or 0, got["top_5"]["precision"] or 0, got["top_5"]["recall"] or 0,
                got["offered"]["precision"] or 0, got["offered"]["recall"] or 0))
        line.append("words top1 %.3f top5 R %.3f" % (found["words"]["top_1"]["precision"] or 0,
                                                     found["words"]["top_5"]["recall"] or 0))
        report(" | ".join(line))


def models_main(args, source, library, name, out):
    """The `models` check: one run at a time on a work folder (its lock names the process), the census read once and
    kept in it, every candidate measured, and the copy removed once each is done."""
    if not args.work:
        raise SystemExit("models needs --work: a folder outside the repository for the copy, kept between runs.")
    candidates = [candidate(text) for text in args.candidates]
    if args.packages:
        sys.path.append(os.path.abspath(args.packages))
    work = sandbox.outside_the_repository(os.path.abspath(args.work))
    lock = os.path.join(work, "run.lock")
    if os.path.exists(lock):
        with open(lock, encoding="utf-8") as handle:
            holder = handle.read().strip()
        if holder.isdigit() and processes.is_alive(int(holder)):
            raise SystemExit("Process %s is measuring in %s: one run at a time." % (holder, work))
    with open(lock, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    try:
        kept = os.path.join(work, "census.json")
        if os.path.exists(kept):
            with open(kept, encoding="utf-8") as handle:
                census = json.load(handle)
        else:
            print("census of %s (read-only)" % name)
            census = sandbox.census(library)
            write_json(kept, census)
        done = models_check(source, work, out, candidates, census, args.sample, args.timing, args.seed, print)
        print(json.dumps(done, indent=1))
        table(out, print)
    finally:
        os.remove(lock)
    if all(os.path.exists(os.path.join(out, slug(model))) for model in candidates) and not args.keep_work:
        print("work folder deleted: %s" % sandbox.remove_sandbox(work))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("check", choices=("faces", "vectors", "models"))
    parser.add_argument("--source", required=True, help="the library, opened read-only and never written")
    parser.add_argument("--out", required=True, help="the folder for the JSON, outside the repository")
    parser.add_argument("--sample", type=int, default=200, help="vectors: photos; models: held-out photos")
    parser.add_argument("--work", help="models: the folder of the copy, kept between runs, outside the repository")
    parser.add_argument("--candidates", nargs="+", default=list(CANDIDATES), help="models: model:weights:size")
    parser.add_argument("--timing", type=int, default=100, help="models: photos timed one at a time")
    parser.add_argument("--packages", help="models: a folder of packages the venv lacks (transformers)")
    parser.add_argument("--keep-work", action="store_true", help="models: keep the copy when every candidate is done")
    parser.add_argument("--seed", type=int, default=20261016)
    args = parser.parse_args()
    out = sandbox.outside_the_repository(args.out)
    source = os.path.abspath(args.source)
    library = Library(source)
    name = os.path.splitext(os.path.basename(source))[0]
    output = {"check": args.check, "library": name, "at": time.strftime("%Y%m%dT%H%M%S")}
    if args.check == "faces":
        output["faces"] = faces_check(library, print)
        for label in ("named", "decided"):
            for held in ("by_photo", "by_folder"):
                found = output["faces"][label][held]
                print("  %s, held out %s: %d evaluated, %d with no face elsewhere" % (
                    label, held, found["evaluated"], found["own_person_only_in_this_group"]))
                for point in found["curve"]["likeness"]:
                    print("    likeness >= %.2f: %6d suggested, precision %s" % (
                        point["cut"], point["suggested"], "%.4f" % point["precision"] if point["precision"] else "-"))
    elif args.check == "models":
        models_main(args, source, library, name, out)
        return
    else:
        print("census of %s (read-only)" % name)
        rows, counts = sandbox.census(library)
        output["census"] = sandbox.census_counts(rows, counts)
        output["versions"] = sandbox.versions()
        output["vectors"] = vectors_check(source, rows, args.sample, args.seed, print)
        print(json.dumps(output["vectors"], indent=1)[:4000])
    path = os.path.join(out, "holdout-%s-%s-%s.json" % (args.check, name, output["at"]))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=1)
    print("written %s" % path)


if __name__ == "__main__":
    main()
