"""The three gating checks of docs/AI_PIPELINE.md, phase 0. Numbers only: nothing it prints or writes names a photo
or a person.

    set TAGPUP_HOME=<the owner's home>      (the libraries are read through the home's machine map)
    .venv/Scripts/python.exe scripts/measure_pipeline_holdouts.py faces  --source data/photo_index.db --out <folder>
    .venv/Scripts/python.exe scripts/measure_pipeline_holdouts.py vectors --source data/photo_index.db --out <folder>
    .venv/Scripts/python.exe scripts/measure_pipeline_holdouts.py size   --source data/kr-track.db --out <folder>

* `faces` (c), read-only, no model: every named face of the library (services.identify.named_faces, today's FaceNet
  vectors) is matched against the named faces of every OTHER photo -- held out by photo, never by face, so a face is
  never matched with its own photo's -- by the closest single face of each person (the rule of
  tagpup.core.clustering's measurement; without the years window KnownFaces adds). The top person, its likeness and
  its margin over the second-best person give the precision of the top suggestion at each likeness cut and each
  margin cut, with the counts behind each point. A face whose person has no face in another photo cannot be matched
  by any recogniser and is counted apart.
* `vectors` (a): the stratified sample of scripts/measure_pipeline_stages.py, copied at the paths its rows have into
  a sandbox's copy of the library, embedded again by the runtime's CLIP model one photo at a time (the indexer's
  way, ClipModel.embed_image) and in batches of 8 and 16 (the picture the model saw, captured by a hook on its visual
  tower, stacked and run at fp16): the cosine of each against the vector the library holds. Expected 0.999 or more.
* `size` (b): a sandbox's copy of the library given a second set of vectors at image size 224 (a model key of its
  own, in the copy only; the library's own are at its setting), and a held-out set of its tagged photos, weighted to
  many faces, scored by Suggest (services.suggester, a Just look run) at each size, each photo's own vector left out
  of its neighbours: precision and recall of the tags offered against the photo's own tags (people left out: they
  come from faces, not from the image size).

Each check that loads a model takes the card in the owner's line (tagpup.runtime), in a sandbox deleted after.
"""
import argparse
import json
import os
import random
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sandbox  # noqa: E402

import _root  # noqa: E402,F401
import numpy as np  # noqa: E402
from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core import paths, vocabulary  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import identify  # noqa: E402

#: The likeness cuts of the face curve; tagpup.core.clustering's two naming cuts among them.
LIKENESS_CUTS = tuple(round(0.40 + 0.05 * i, 2) for i in range(12))
#: The margin cuts: the top person's likeness over the second-best person's.
MARGIN_CUTS = tuple(round(0.02 * i, 2) for i in range(16))


# ---- (c) the named faces ------------------------------------------------------------------------------------------

def top_two(matrix, people, photos, block=1024):
    """For each row of `matrix` (unit vectors): (its best person, that likeness, the second-best person's likeness,
    whether its own person had a face to compare), each person's likeness being their closest face in another photo.
    `people` and `photos` are an integer for each row. Rows are compared a block at a time."""
    order = np.argsort(people, kind="stable")
    matrix, people, photos = matrix[order], people[order], photos[order]
    starts = np.flatnonzero(np.r_[True, people[1:] != people[:-1]])
    names = people[starts]
    best, first, second, reachable = (np.empty(len(people), dtype=names.dtype), np.empty(len(people)),
                                      np.empty(len(people)), np.empty(len(people), dtype=bool))
    for start in range(0, len(people), block):
        rows = slice(start, start + block)
        likeness = matrix[rows] @ matrix.T
        likeness[photos[rows][:, None] == photos[None, :]] = -np.inf
        by_person = np.maximum.reduceat(likeness, starts, axis=1)
        top = np.argsort(-by_person, axis=1)[:, :2]
        picked = np.take_along_axis(by_person, top, axis=1)
        best[rows] = names[top[:, 0]]
        first[rows] = picked[:, 0]
        second[rows] = picked[:, 1] if picked.shape[1] > 1 else -np.inf
        own = np.searchsorted(names, people[rows])
        reachable[rows] = np.isfinite(by_person[np.arange(len(own)), own])
    back = np.empty_like(order)
    back[order] = np.arange(len(order))
    return best[back], first[back], second[back], reachable[back]


def precision_curve(correct, likeness, margin):
    """The precision of the top suggestion at each cut: [{"cut", "suggested", "right", "precision", "share"}] for
    LIKENESS_CUTS (a suggestion is its likeness at the cut or above) and for MARGIN_CUTS (its margin)."""
    def curve(values, cuts):
        points = []
        for cut in cuts:
            kept = values >= cut
            suggested, right = int(kept.sum()), int((correct & kept).sum())
            points.append({"cut": cut, "suggested": suggested, "right": right,
                           "precision": right / suggested if suggested else None,
                           "share": suggested / len(values) if len(values) else None})
        return points
    return {"likeness": curve(likeness, LIKENESS_CUTS), "margin": curve(margin, MARGIN_CUTS)}


def hold_out(matrix, people, groups):
    """The curve and its counts for faces matched only against faces of another group (a photo, a folder): `people`
    and `groups` an integer a row."""
    best, first, second, reachable = top_two(matrix, people, groups)
    correct = best == people
    return {"faces": len(people), "own_person_only_in_this_group": int((~reachable).sum()),
            "evaluated": int(reachable.sum()), "top_right": int(correct[reachable].sum()),
            "curve": precision_curve(correct[reachable], first[reachable], (first - second)[reachable])}


def _numbered(keys):
    """An integer for each key, equal keys equal."""
    seen = {}
    return np.array([seen.setdefault(key, len(seen)) for key in keys])


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
            "by_photo": hold_out(matrix[rows], people, _numbered(paths.key(p) for p in photos)),
            "by_folder": hold_out(matrix[rows], people, _numbered(paths.key(os.path.dirname(p)) for p in photos))}
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
        stored, single, pictures, skipped = [], [], [], {}
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
                    vector = clip.embed_image(copies[row["path"]])
                except OSError as problem:   # images.Unreadable, or a read that failed
                    skipped[type(problem).__name__] = skipped.get(type(problem).__name__, 0) + 1
                    seen.clear()
                    continue
                stored.append(kept)
                single.append(vector)
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
        answer = {"photos": len(stored), "skipped": skipped,
                  "stored_vs_one_at_a_time": cosine_summary(cosines(stored, single))}
        for batch, found in batched.items():
            answer["stored_vs_batch_%d" % batch] = (cosine_summary(cosines(stored, found)) if isinstance(found, list)
                                                    else found)
            if isinstance(found, list):
                answer["one_at_a_time_vs_batch_%d" % batch] = cosine_summary(cosines(single, found))
        return answer
    finally:
        answer_deleted = sandbox.remove_sandbox(folder)
        report("sandbox deleted: %s" % answer_deleted)


# ---- (b) Suggest at 224 and at the library's size ---------------------------------------------------------------

class LeavingOut:
    """A photo index whose search leaves out one photo, `left_out` (an id): the photo being scored, whose own vector
    would be its nearest neighbour and hand it its own tags."""

    def __init__(self, index):
        self.index, self.left_out = index, None

    def __getattr__(self, name):
        return getattr(self.index, name)

    def search(self, query_vector, k=15):
        return [(sim, record) for sim, record in self.index.search(query_vector, k=k + 1)
                if record.get("id") != self.left_out][:k]


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


def _scored(runtime, library, settings, index, held, report):
    """The tags Suggest offers each held-out photo, and its top 5 by score, at `settings`' model."""
    from tagpup.services import suggester
    clip = runtime.clip(library, settings)
    with runtime.gpu_turn("Suggest at one image size (AI pipeline phase 0)", (clip,)):
        model = suggester.model_for_run(index, clip, runtime.faces(library, settings),
                                        runtime.candidate_words(library), remember=False)
        leaving = LeavingOut(index)
        model.suggester.index = leaving
        offered, top5, truths = [], [], []
        for record, vector in held:
            leaving.left_out = record["id"]
            found = model.suggester.suggest_for_photo(record["path"], vector, k=15, min_sim=0.35,
                                                      target_metadata=record)
            tags, _people, _caption = model.offered(found)
            offered.append({tag["tag"] for tag in tags})
            ranked = [item for item in found.get("suggested_tags", []) if not item.get("has_face_match")]
            top5.append({item["tag"] for item in ranked[:5]})
            truths.append({part for tag in record.get("tags", []) if not _a_person(tag, model.face_roots)
                           for part in model.suggester.taxonomy.expand_tag(tag)})
    report("  scored %d photos" % len(held))
    return {"offered": tag_scores(offered, truths), "top_5": tag_scores(top5, truths)}


def size_check(source, rows, holdout, seed, report):
    """(b): the library's photos embedded at 224 into a copy, and the held-out photos scored at 224 and at the
    library's own size."""
    from tagpup.runtime import Runtime
    from tagpup.services import search
    from tagpup.services.settings import LibrarySettings
    places = tagpup_config.machine_roots()
    candidates = [row for row in rows if row["faces"] > 0]
    chosen = sandbox.choose_sample(candidates, holdout, rng=random.Random(seed))
    folder, target, placed = _model_sandbox(source, report)
    try:
        copies = sandbox.copy_at_rows(chosen, placed, places)
        where = {}
        for row in rows:
            name, under = sandbox.owner_relative(row["path"], places)
            where[row["path"]] = os.path.join(placed[name], under)
        sandbox.enter(folder)
        runtime, library = Runtime(), Library(target)
        own = runtime.settings(library)
        small = LibrarySettings(dict(own.values, **{"model.force_image_size": "224"}))
        answer = {"own_size": own.embedder["force_image_size"], "photos": len(rows), "held_out": len(chosen)}
        if answer["own_size"] == 224:
            raise SystemExit("The library's own size is 224: nothing to compare.")

        # 224: every photo embedded from its file (read-only), kept in the copy under the 224 key.
        clip = runtime.clip(library, small)
        index = search.PhotoIndex(target, runtime.model_key(library, small))
        index.open()
        keeper, small_vectors, seconds, skipped = search.PhotoEmbeddings(clip, index), {}, [], {}
        with runtime.gpu_turn("embedding at 224 (AI pipeline phase 0)", (clip,)):
            clip.load()
            for row in rows:
                started = time.perf_counter()
                try:
                    stat = os.stat(row["path"])
                    vector = clip.embed_image(row["path"])
                except OSError as problem:
                    skipped[type(problem).__name__] = skipped.get(type(problem).__name__, 0) + 1
                    continue
                seconds.append(time.perf_counter() - started)
                keeper.keep(where[row["path"]], vector, stamp=(stat.st_mtime, stat.st_size))
                small_vectors[paths.key(where[row["path"]])] = vector
        index.close()
        answer.update(embedded_at_224=len(small_vectors), skipped=skipped, seconds_a_photo_at_224=sandbox.summary(seconds))
        report("  %d photos embedded at 224" % len(small_vectors))

        results = {}
        for label, settings in (("224", small), (str(answer["own_size"]), own)):
            index = search.PhotoIndex(target, runtime.model_key(library, settings))
            index.load()
            records = {paths.key(r["path"]): r for r in index.records()}
            vectors = search.PhotoEmbeddings(runtime.clip(library, settings), index, remember=False)
            held = []
            for row in chosen:
                record = records.get(paths.key(copies[row["path"]]))
                vector = (small_vectors.get(paths.key(copies[row["path"]])) if label == "224"
                          else vectors.cached(copies[row["path"]]))
                if record is not None and vector is not None and record.get("tags"):
                    held.append((record, vector))
            results[label] = _scored(runtime, library, settings, index, held, report)
            index.close()
        answer["scores"] = results
        return answer
    finally:
        report("sandbox deleted: %s" % sandbox.remove_sandbox(folder))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("check", choices=("faces", "vectors", "size"))
    parser.add_argument("--source", required=True, help="the library, opened read-only and never written")
    parser.add_argument("--out", required=True, help="the folder for the JSON, outside the repository")
    parser.add_argument("--sample", type=int, default=200, help="vectors: photos; size: held-out photos")
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
    else:
        print("census of %s (read-only)" % name)
        rows, counts = sandbox.census(library)
        output["census"] = sandbox.census_counts(rows, counts)
        output["versions"] = sandbox.versions()
        if args.check == "vectors":
            output["vectors"] = vectors_check(source, rows, args.sample, args.seed, print)
        else:
            output["size"] = size_check(source, rows, args.sample, args.seed, print)
        print(json.dumps(output.get("vectors") or output.get("size"), indent=1)[:4000])
    path = os.path.join(out, "holdout-%s-%s-%s.json" % (args.check, name, output["at"]))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=1)
    print("written %s" % path)


if __name__ == "__main__":
    main()
