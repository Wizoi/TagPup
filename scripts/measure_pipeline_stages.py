"""Time the indexer's work stage by stage, an event folder indexed by the CLI, and `cluster-faces`
(docs/AI_PIPELINE.md, phase 0 and section 7). Numbers only: nothing it prints or writes names a photo or a person.

    set TAGPUP_HOME=<the owner's home>      (the census reads the library through the home's machine map)
    .venv/Scripts/python.exe scripts/measure_pipeline_stages.py --source data/photo_index.db --out <folder>
    .venv/Scripts/python.exe scripts/measure_pipeline_stages.py --mode event --source data/kr-track.db --out <folder>
    .venv/Scripts/python.exe scripts/measure_pipeline_stages.py --mode cluster --source data/photo_index.db --out <folder>
    .venv/Scripts/python.exe scripts/measure_pipeline_stages.py --mode census --source data/photo_index.db --out <folder>
    .venv/Scripts/python.exe scripts/measure_pipeline_stages.py --source data/photo_index.db --out <folder> --detect-max-side 2048
    .venv/Scripts/python.exe scripts/measure_pipeline_stages.py --mode compare --out <folder> <off.json> <capped.json> ...

Each run is in a sandbox (scripts/sandbox.py): the library copied through SQLite's backup API (the original opened
read-only), the code snapshotted, the copy's roots placed inside the sandbox, a separate process with the sandbox as
its home and TAGPUP_NO_JOBS=1, the whole deleted after -- also on Ctrl+C, the child killed first; what could not be
deleted is said. The graphics card is taken in the owner's line (tagpup.ml.gpu, by the CLI's own turn).

* `census`: read-only. Which photos are readable (gone, not looked at, failing to open), the strata by the faces
  each photo has, the resolution distribution, the photos turned by their Orientation. Every mode starts with it.
* `stages` (the default): a sample of 200 to 500 photos drawn in strata (none, 1-3, 4-9, 10 or more, the worst), a
  third or more at 10 or more, spread over megapixels, copied into a new folder under a root of the copy, and indexed
  by tagpup_cli's own `index` in a child process with a timer around each stage. The timers are hooks on the real
  objects, not copies of the code: the `seen` callback of ClipModel.embed_image (decode for CLIP), a forward hook on
  CLIP's visual tower (pad and resize before it, the forward after it), a wrapper on the MTCNN object's `detect`
  (decode for faces before it, MTCNN inside it), forward hooks on the recogniser, Pillow's save and thumbnail while
  faces run (the crop's JPEG), and wrappers on PhotoIndex.build_or_update and services.faces.record_batch (the
  database writes), MetadataExtractor.batch_read and IdentityWriter.give (as tagpup_cli holds them). Each GPU stage
  ends in torch.cuda.synchronize(). The first photo is the warm-up, reported apart.
* `event`: one folder of a library (kr-track; `--folder-number`, by the size order of services.inspect.folders, else
  the one nearest 60 photos) copied into a new folder of the copy and indexed by the snapshot's tagpup_cli.py, as the
  owner's index runs: wall time and seconds a photo.
* `cluster`: the snapshot's `tagpup_cli.py cluster-faces` on the copy, timed.
* `--detect-max-side N` (stages, event): the copy's faces.detect_max_side set to N first (a journaled change of the
  copy alone; phase 1a), so the index detects at that cap. A stages run keeps each photo's boxes and the source's
  stored faces of it (box, named, excluded: numbers only), by its number in the sample (the seed draws the same one).
* `compare`: reads stages runs of one sample (one without a cap, others with), and says per stratum the faces each
  finds, which faces the run without a cap finds and a capped run loses (IoU under 0.5 with every capped box), named
  or not by the stored face they match, with their longest side, the faces a capped run finds that the other does
  not, MTCNN's and the photo's seconds, the whole run's seconds a photo and the card's peak memory.

While the child runs, nvidia-smi is read every 0.5 s and the processors' use (the machine's and the child's) with it:
the card's idle share is read from that, and the memory and load before the child starts are the other programs'.
"""
import time

STARTED = time.time()   # the child's process start is measured to here

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import random  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from code_snapshot import REPO_ROOT, copy_code  # noqa: E402
import sandbox  # noqa: E402
from sandbox import environment  # noqa: E402

import _root  # noqa: E402,F401
from tagpup.core import paths, processes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import inspect as inspect_service  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402

MODULES_IMPORTED = time.time()

#: The stages of one photo, in the order the indexer runs them.
STAGES = ("decode for CLIP", "pad and resize", "CLIP forward", "identity write", "decode for faces", "MTCNN",
          "crop and tensor", "crop JPEG encode", "recogniser forward")


# ---- The child: the CLI's index, timed -------------------------------------------------------------------------

class Timeline:
    """What the hooks saw: a record a photo, the calls of each timed function, and the fixed costs."""

    def __init__(self, sync):
        self.sync = sync
        self.photos = []
        self.calls = {}
        self.current = None
        self.in_faces = False
        self.encoding = 0.0

    def begin(self, path):
        self.current = {"key": paths.key(path)}
        self.photos.append(self.current)
        return self.current

    def mark(self, name):
        self.sync()
        if self.current is not None:
            self.current[name] = time.perf_counter()

    def note(self, label, seconds):
        self.calls.setdefault(label, []).append(seconds)

    def total(self, label):
        return sum(self.calls.get(label, ()))


def _timed(owner, name, label, timeline, per_photo=False):
    """Replace `owner.name` with a wrapper noting each call's seconds under `label` (and, with `per_photo`, on the
    photo being indexed)."""
    original = getattr(owner, name)

    def timed(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            seconds = time.perf_counter() - started
            timeline.note(label, seconds)
            if per_photo and timeline.current is not None:
                timeline.current[label] = timeline.current.get(label, 0.0) + seconds
    setattr(owner, name, timed)


def _watch_clip(model, timeline):
    """Hooks on one CLIP model the runtime built: its load, its decode (embed_image's `seen`) and its visual tower."""
    load, embed = model._init_model, model.embed_image

    def init_model():
        if model.model is not None:
            return load()
        started = time.perf_counter()
        load()
        timeline.sync()
        timeline.note("CLIP load", time.perf_counter() - started)
        model.model.visual.register_forward_pre_hook(lambda _m, _i: timeline.mark("clip_in"))
        model.model.visual.register_forward_hook(lambda _m, _i, _o: timeline.mark("clip_out"))

    def embed_image(file_path, seen=None):
        record = timeline.begin(file_path)
        loaded = timeline.total("CLIP load")
        record["t0"] = time.perf_counter()

        def looked(picture):
            record["decoded"] = time.perf_counter()
            record["upright"] = list(picture.size)
            if seen is not None:
                seen(picture)
        vector = embed(file_path, seen=looked)
        timeline.sync()
        record["clip_end"] = time.perf_counter()
        record["clip_load"] = timeline.total("CLIP load") - loaded
        return vector
    model._init_model, model.embed_image = init_model, embed_image


def _watch_faces(model, timeline):
    """Hooks on the face models the runtime built: their load, MTCNN's detect, the recogniser's forward."""
    load, find = model._init_models, model.detect_and_embed_faces

    def init_models():
        if model.mtcnn is not None:
            return load()
        started = time.perf_counter()
        load()
        timeline.sync()
        timeline.note("face models load", time.perf_counter() - started)
        detect = model.mtcnn.detect

        def detect_timed(picture, *args, **kwargs):
            timeline.mark("detect_in")
            if timeline.current is not None:
                timeline.current["stored"] = list(picture.size)
            try:
                return detect(picture, *args, **kwargs)
            finally:
                timeline.mark("detect_out")
        model.mtcnn.detect = detect_timed
        model.resnet.register_forward_pre_hook(lambda _m, _i: timeline.mark("resnet_in"))
        model.resnet.register_forward_hook(lambda _m, _i, _o: timeline.mark("resnet_out"))

    def detect_and_embed_faces(img_path, *, max_side):
        if timeline.current is None or timeline.current["key"] != paths.key(img_path):
            timeline.begin(img_path)
        record = timeline.current
        loaded = timeline.total("face models load")
        timeline.encoding, timeline.in_faces = 0.0, True
        record["f0"] = time.perf_counter()
        try:
            found = find(img_path, max_side=max_side)
        finally:
            timeline.in_faces = False
        timeline.sync()
        record["f_end"] = time.perf_counter()
        record["encode"] = timeline.encoding
        record["faces_load"] = timeline.total("face models load") - loaded
        record["faces_found"] = len(found)
        record["boxes"] = [list(face["box"]) for face in found]
        record["detection_failed"] = bool(getattr(found, "failed", False))
        return found
    model._init_models, model.detect_and_embed_faces = init_models, detect_and_embed_faces


def _watch_pillow(timeline):
    """Pillow's save and thumbnail, timed while faces run: the crop's JPEG (images.crop_jpeg)."""
    from PIL import Image
    for name in ("save", "thumbnail"):
        original = getattr(Image.Image, name)

        def wrapped(self, *args, _original=original, **kwargs):
            if not timeline.in_faces:
                return _original(self, *args, **kwargs)
            started = time.perf_counter()
            try:
                return _original(self, *args, **kwargs)
            finally:
                timeline.encoding += time.perf_counter() - started
        setattr(Image.Image, name, wrapped)


def stages_of(record):
    """{stage: seconds} of one photo's record (STAGES), from the marks the hooks left; a stage not reached is left
    out."""
    found = {}

    def span(start, end, minus=0.0):
        if start in record and end in record:
            return max(0.0, record[end] - record[start] - minus)
        return None
    found["decode for CLIP"] = span("t0", "decoded", record.get("clip_load", 0.0))
    found["pad and resize"] = span("decoded", "clip_in")
    found["CLIP forward"] = span("clip_in", "clip_end")
    found["identity write"] = record.get("identity write")
    found["decode for faces"] = span("f0", "detect_in", record.get("faces_load", 0.0))
    found["MTCNN"] = span("detect_in", "detect_out")
    after = "resnet_in" if "resnet_in" in record else "f_end"
    found["crop and tensor"] = span("detect_out", after, record.get("encode", 0.0))
    found["crop JPEG encode"] = record.get("encode")
    found["recogniser forward"] = span("resnet_in", "f_end")
    return {name: seconds for name, seconds in found.items() if seconds is not None}


def child(plan_path):
    """Run tagpup_cli's index over the plan's folder with the timers in place; write what they saw beside the plan."""
    with open(plan_path, encoding="utf-8") as handle:
        plan = json.load(handle)
    clock = time.time() - time.perf_counter()     # perf_counter -> epoch, for the sampler's window
    with open(plan["pid_file"], "w", encoding="utf-8") as handle:   # the interpreter, not the venv's launcher
        handle.write(str(os.getpid()))
    fixed = {"process start": STARTED - plan["spawned"], "script imports": MODULES_IMPORTED - STARTED}
    started = time.perf_counter()
    import tagpup_cli
    fixed["CLI imports"] = time.perf_counter() - started
    started = time.perf_counter()
    import torch
    import open_clip  # noqa: F401
    import facenet_pytorch  # noqa: F401
    fixed["model libraries import"] = time.perf_counter() - started
    sync = torch.cuda.synchronize if torch.cuda.is_available() else (lambda: None)
    timeline = Timeline(sync)

    from tagpup.runtime import Runtime
    from tagpup.services import faces as face_records
    from tagpup.services import search
    clip_of, faces_of = Runtime.clip, Runtime.faces

    def clip(self, library, settings=None):
        model = clip_of(self, library, settings)
        if not getattr(model, "_measured", False):
            model._measured = True
            _watch_clip(model, timeline)
        return model

    def faces(self, library, settings=None):
        model = faces_of(self, library, settings)
        if not getattr(model, "_measured", False):
            model._measured = True
            _watch_faces(model, timeline)
        return model
    Runtime.clip, Runtime.faces = clip, faces
    _watch_pillow(timeline)
    _timed(search.PhotoIndex, "build_or_update", "photo rows write", timeline)
    _timed(face_records, "record_batch", "faces write", timeline)
    _timed(search.PhotoIndex, "open", "library open", timeline)
    _timed(Runtime, "gpu_turn", "wait for the card", timeline)
    _timed(tagpup_cli.MetadataExtractor, "batch_read", "metadata read", timeline)
    _timed(tagpup_cli.IdentityWriter, "give", "identity write", timeline, per_photo=True)

    began = time.perf_counter()
    error = None
    try:
        tagpup_cli.cli.main(args=["--db", plan["db"], "index", plan["folder"]], standalone_mode=False)
    except (Exception, SystemExit) as problem:   # the CLI's own exits too; an interrupt ends the child
        error = type(problem).__name__
    ended = time.perf_counter()

    # Suggest's fixed cost, not the index's: the index reads no vector (#1089).
    runtime = Runtime()
    index = search.PhotoIndex(plan["db"], runtime.model_key(Library(plan["db"])))
    started = time.perf_counter()
    index.load()
    fixed["vector load (Suggest's)"] = time.perf_counter() - started
    index.close()

    labels = plan["labels"]
    photos = []
    for record in timeline.photos:
        label = labels.get(record["key"], {})
        upright, stored = record.get("upright"), record.get("stored")
        photos.append({"stratum": label.get("stratum"), "stored_faces": label.get("faces"),
                       "number": label.get("number"), "boxes": record.get("boxes"), "stored": label.get("stored"),
                       "megapixels": label.get("megapixels"), "faces_found": record.get("faces_found"),
                       "detection_failed": record.get("detection_failed"),
                       "sideways": bool(upright and stored and upright != stored and upright == stored[::-1]),
                       "total": (record.get("f_end", record.get("clip_end", 0)) - record["t0"]) if "t0" in record
                       else None, "stages": stages_of(record)})
    result = {"error": error, "fixed": fixed, "calls": timeline.calls, "photos": photos, "versions": sandbox.versions(),
              "cli": {"start": clock + began, "end": clock + ended, "seconds": ended - began},
              "loop": {"start": clock + timeline.photos[0]["t0"] if timeline.photos else None,
                       "end": clock + max((r.get("f_end", r.get("clip_end", 0)) for r in timeline.photos), default=0)}}
    with open(plan["result"], "w", encoding="utf-8") as handle:
        json.dump(result, handle)


# ---- The parent: the census, the sandbox, the child, the samples ------------------------------------------------

def stage_table(photos):
    """{stratum and "all": {"photos", "faces_found", "megapixels", "total", stage: summary}} of the photos after the
    first (the warm-up)."""
    steady = photos[1:]
    table = {}
    for name in sandbox.STRATA + ("all",):
        members = [p for p in steady if name == "all" or p["stratum"] == name]
        if not members:
            continue
        row = {"photos": len(members), "faces_found": sandbox.summary([p["faces_found"] for p in members]),
               "megapixels": sandbox.summary([p["megapixels"] for p in members]),
               "total": sandbox.summary([p["total"] for p in members])}
        for stage in STAGES:
            row[stage] = sandbox.summary([p["stages"].get(stage) for p in members])
        table[name] = row
    return table


def print_table(table):
    columns = ("photos",) + STAGES + ("total",)
    print("\n%-7s %6s " % ("stratum", "photos") + " ".join("%9s" % c[:9] for c in columns[1:]))
    for name, row in table.items():
        means = ["%9.3f" % row[c]["mean"] if row[c].get("n") else "%9s" % "-" for c in columns[1:]]
        print("%-7s %6d " % (name, row["photos"]) + " ".join(means))
    print("(mean seconds a photo; the first photo, the warm-up, left out)")


def run_child(sandbox_dir, plan, sampler, timeout):
    """Start the snapshot's copy of this script as the child, sampled while it runs; its result."""
    plan_path = os.path.join(sandbox_dir, "plan.json")
    plan["result"] = os.path.join(sandbox_dir, "result.json")
    plan["pid_file"] = os.path.join(sandbox_dir, "child.pid")
    plan["spawned"] = time.time()
    with open(plan_path, "w", encoding="utf-8") as handle:
        json.dump(plan, handle)
    log = open(os.path.join(sandbox_dir, "child.log"), "w", encoding="utf-8")
    try:
        process = processes.start([sys.executable, os.path.join(sandbox_dir, "scripts", os.path.basename(__file__)),
                                   "--child", plan_path], cwd=sandbox_dir, stdout=log, stderr=subprocess.STDOUT,
                                  env=environment(sandbox_dir, TAGPUP_NO_JOBS="1"))
        sampler.watch(plan["pid_file"])
        try:
            process.wait(timeout=timeout)
        finally:
            if process.poll() is None:
                processes.kill_tree(process.pid)
    finally:
        log.close()
    with open(plan["result"], encoding="utf-8") as handle:
        return json.load(handle)


def run_cli(sandbox_dir, arguments, sampler, timeout):
    """The snapshot's tagpup_cli.py with `arguments`, as the owner runs it: (wall seconds, exit code, start, end)."""
    with open(os.path.join(sandbox_dir, "cli.log"), "w", encoding="utf-8") as log:
        started = time.time()
        process = processes.start([sys.executable, os.path.join(sandbox_dir, "tagpup_cli.py")] + arguments,
                                  cwd=sandbox_dir, stdout=log, stderr=subprocess.STDOUT,
                                  env=environment(sandbox_dir, TAGPUP_NO_JOBS="1"))
        sampler.watch(None)   # the venv's launcher, not the interpreter: the machine's use alone is read
        try:
            code = process.wait(timeout=timeout)
        finally:
            if process.poll() is None:
                processes.kill_tree(process.pid)
        ended = time.time()
    return ended - started, code, started, ended


def build(sandbox_dir, source, detect_max_side=None):
    """The code and the library's copy, its roots placed in the sandbox, its detector capped at `detect_max_side` when
    given (a journaled change of the copy alone): (the copy's path, {root: place})."""
    os.makedirs(os.path.join(sandbox_dir, "data"))
    copy_code(sandbox_dir, REPO_ROOT, launchers=True)
    target = os.path.join(sandbox_dir, "data", "measured.db")
    sandbox.copy_library(source, target)
    if detect_max_side:
        changed = library_settings.change(Library(target), {"faces.detect_max_side": str(detect_max_side)},
                                          acknowledged=["faces"])
        if changed.refused or not changed.changed:
            raise SystemExit("the copy's cap was not set: %s" % (changed.refused or "nothing changed"))
    return target, sandbox.place_roots(target, sandbox_dir)


def stored_faces(library, photo_ids):
    """{photo id: [[x1, y1, x2, y2], named, excluded]} of `library`'s faces of `photo_ids`, read-only, through the
    service the MCP tool answers with (services.inspect.faces_in_photo: no name, no BLOB); a photo at a time, for the
    sample's few hundred."""
    return {photo_id: [[face["box"], face["named"], face["excluded"]]
                       for face in inspect_service.faces_in_photo(library, photo_id)["faces"]]
            for photo_id in photo_ids}


# ---- Compare: the faces a cap loses and gains, against the run without one ----------------------------------------

#: Two boxes are one face at this overlap or more (docs/AI_PIPELINE.md, phase 3's carrying rule).
SAME_FACE = 0.5


def iou(a, b):
    """The overlap of two boxes (x1, y1, x2, y2): their intersection over their union, 0 for none."""
    across = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    down = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    shared = across * down
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - shared
    return shared / union if union > 0 else 0.0


def pairs(boxes, others):
    """{index in boxes: index in others}: each box paired once with the other it overlaps most, the largest overlaps
    first, at SAME_FACE or more."""
    candidates = sorted(((iou(a, b), i, j) for i, a in enumerate(boxes) for j, b in enumerate(others)), reverse=True)
    paired, taken = {}, set()
    for overlap, i, j in candidates:
        if overlap < SAME_FACE:
            break
        if i not in paired and j not in taken:
            paired[i] = j
            taken.add(j)
    return paired


def compared(off, capped):
    """{stratum and "all": counts} of a capped run's photos against the run without a cap, photo by photo by number:
    faces found by each, lost (named, unnamed, their longest side), gained."""
    by_number = {photo["number"]: photo for photo in off["photos"]}
    table = {}
    for photo in capped["photos"]:
        before = by_number.get(photo["number"])
        if before is None or before["stratum"] != photo["stratum"] or photo["boxes"] is None or before["boxes"] is None:
            continue
        named = [face[0] for face in before.get("stored") or () if face[0] and face[1] and not face[2]]
        named_off = set(pairs(before["boxes"], named))
        kept = pairs(before["boxes"], photo["boxes"])
        gained = len(photo["boxes"]) - len(kept)
        for name in (photo["stratum"], "all"):
            row = table.setdefault(name, {"photos": 0, "found_off": 0, "named_off": 0, "found": 0, "lost_named": 0,
                                          "lost_unnamed": 0, "gained": 0, "lost_sides": []})
            row["photos"] += 1
            row["found_off"] += len(before["boxes"])
            row["named_off"] += len(named_off)
            row["found"] += len(photo["boxes"])
            row["gained"] += gained
            for i, box in enumerate(before["boxes"]):
                if i in kept:
                    continue
                row["lost_named" if i in named_off else "lost_unnamed"] += 1
                row["lost_sides"].append(max(box[2] - box[0], box[3] - box[1]))
    for row in table.values():
        row["lost_sides"] = sandbox.summary(row["lost_sides"])
    return table


def run_numbers(run):
    """{stratum and "all": {"mtcnn", "total"}} mean seconds a photo (the warm-up left out), and the run's own."""
    numbers = {name: {"mtcnn": row["MTCNN"].get("mean"), "total": row["total"].get("mean")}
               for name, row in run["table"].items()}
    photos = len(run["photos"])
    numbers["run"] = {"seconds_a_photo": run["cli"]["seconds"] / photos if photos else None,
                      "peak_memory_mib": run["card"].get("peak_memory_mib"),
                      "before_memory_mib": (run.get("before") or {}).get("card", {}).get("peak_memory_mib")}
    return numbers


def compare(files, out):
    """Print and write the comparison of stages runs `files` of one sample: the one without a cap against each."""
    runs = []
    for path in files:
        with open(path, encoding="utf-8") as handle:
            run = json.load(handle)
        # A run of the script before phase 1a, or one without faces, has no photo's number and boxes: every row of
        # the table would be empty, and an empty table reads as "nothing lost".
        if not any(photo.get("number") is not None and photo.get("boxes") is not None for photo in run.get("photos") or ()):
            raise SystemExit("%s holds no photo's number and faces found, so there is nothing to compare: run that "
                             "sample again with this script, faces on." % path)
        runs.append(run)
    off = [run for run in runs if not run.get("detect_max_side")]
    if len(off) != 1:
        raise SystemExit("compare takes one run without a cap and any with one")
    off = off[0]
    output = {"mode": "compare", "off": run_numbers(off), "caps": {}}
    for run in sorted((r for r in runs if r.get("detect_max_side")), key=lambda r: r["detect_max_side"]):
        output["caps"][str(run["detect_max_side"])] = {"faces": compared(off, run), "numbers": run_numbers(run)}
    print("%-6s %-7s %6s %11s %7s %6s %6s %6s %6s %8s %7s %7s" % (
        "cap", "stratum", "photos", "found", "named", "lost_n", "lost_u", "gained", "side50", "mtcnn_s", "total_s",
        "run_s"))
    for cap, entry in output["caps"].items():
        for name, row in entry["faces"].items():
            numbers = entry["numbers"].get(name, {})
            print("%-6s %-7s %6d %11s %7d %6d %6d %6d %6s %8.3f %7.3f %7s" % (
                cap, name, row["photos"], "%d/%d" % (row["found"], row["found_off"]), row["named_off"], row["lost_named"],
                row["lost_unnamed"], row["gained"], row["lost_sides"].get("median", "-"),
                numbers.get("mtcnn") or 0, numbers.get("total") or 0,
                "%.3f" % entry["numbers"]["run"]["seconds_a_photo"] if name == "all" else ""))
        print("%-6s peak card %s MiB (before the run %s)" % (
            cap, entry["numbers"]["run"]["peak_memory_mib"], entry["numbers"]["run"]["before_memory_mib"]))
    print("off    by stratum mtcnn/total: %s; run %.3f s a photo, peak card %s MiB (before %s)" % (
        {name: (round(v["mtcnn"] or 0, 3), round(v["total"] or 0, 3)) for name, v in output["off"].items() if name != "run"},
        output["off"]["run"]["seconds_a_photo"], output["off"]["run"]["peak_memory_mib"],
        output["off"]["run"]["before_memory_mib"]))
    path = os.path.join(out, "pipeline-compare-%s.json" % time.strftime("%Y%m%dT%H%M%S"))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=1)
    print("written %s" % path)


def new_folder(placed, name):
    """A folder the copy has never held, under the first of its roots' places."""
    folder = os.path.join(sorted(placed.values())[0], name)
    os.makedirs(folder)
    return folder


def event_photos(library, number, report):
    """(the photos of one folder of `library`, its number): folder `number` of services.inspect.folders' order,
    else the one nearest 60 photos (the run of 2026-09-27; its log is gone, so its folder cannot be named)."""
    folders = inspect_service.folders(library, reveal=True, limit=10 ** 6)["folders"]
    if number is None:
        number = min(folders, key=lambda f: (abs(f["photos"] - 60), f["folder"]))["folder"]
    chosen = folders[number - 1]
    listed = inspect_service.photos(library, folder=chosen["path"], reveal=True, limit=10 ** 6)
    inside = [path for path in listed["paths"] if paths.same(os.path.dirname(path), chosen["path"])]
    report("  folder %d of %d: %d photos" % (number, len(folders), len(inside)))
    return inside, number


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--child", help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=("census", "stages", "event", "cluster", "compare"), default="stages")
    parser.add_argument("--detect-max-side", type=int, help="stages, event: the copy's detector capped at this long side")
    parser.add_argument("runs", nargs="*", help="compare: the stages runs' JSON, one without a cap")
    parser.add_argument("--source", help="the library, opened read-only and never written")
    parser.add_argument("--out", help="the folder for the JSON, outside the repository")
    parser.add_argument("--sample", type=int, default=300, help="photos in the sample (200 to 500)")
    parser.add_argument("--worst", type=int, default=10, help="the photos with the most faces, in the sample")
    parser.add_argument("--seed", type=int, default=20261016)
    parser.add_argument("--deep", action="store_true", help="also open every file's end and header (the doctor's "
                                                            "count of files that fail to open)")
    parser.add_argument("--folder-number", type=int, help="event: which folder, by services.inspect.folders' order")
    parser.add_argument("--timeout", type=int, default=7200, help="seconds the child may take")
    parser.add_argument("--keep", action="store_true", help="leave the sandbox (it holds copies of photos)")
    args = parser.parse_args()
    if args.child:
        return child(args.child)
    if args.mode == "compare":
        return compare(args.runs, sandbox.outside_the_repository(args.out or "."))
    if not args.source or not args.out:
        parser.error("--source and --out are needed")
    out = sandbox.outside_the_repository(args.out)
    source = os.path.abspath(args.source)
    library = Library(source)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    output = {"mode": args.mode, "library": os.path.splitext(os.path.basename(source))[0], "at": stamp,
              "detect_max_side": args.detect_max_side}

    print("census of %s (read-only)" % output["library"])
    rows, counts = sandbox.census(library, deep=args.deep or args.mode == "census")
    output["census"] = sandbox.census_counts(rows, counts)
    unreadable = counts["gone"] + counts["not_looked_at"] + counts.get("fail_to_open", 0) + counts["no_header"]
    print("  readable %d, unreadable %d (gone %d, not looked at %d, failing %s, no header %d)" % (
        counts["readable"], unreadable, counts["gone"], counts["not_looked_at"], counts.get("fail_to_open", "-"),
        counts["no_header"]))
    if counts["readable"] < 0.95 * counts["photos"]:
        output["stopped"] = "fewer than 95% of the photos are readable: a share asleep or files missing"
        print("STOPPED: " + output["stopped"])
    sandbox_dir, sampler = None, None
    try:
        if args.mode != "census" and "stopped" not in output:
            sandbox_dir = tempfile.mkdtemp(prefix="tagpup_pipeline_")
            print("sandbox %s" % sandbox_dir)
            db_path, placed = build(sandbox_dir, source, args.detect_max_side)
            sampler = sandbox.Sampler().start()
            time.sleep(10)   # the card and the processors before the child: the other programs' share
            output["before"] = {"card": sandbox.window(sampler.gpu, 0, time.time()),
                                "processors": sampler.processor(0, time.time())}
            if args.mode == "stages":
                chosen = sandbox.choose_sample(rows, args.sample, args.worst, random.Random(args.seed))
                random.Random(args.seed).shuffle(chosen)   # the warm-up photo of any stratum
                folder = new_folder(placed, "pipeline-sample")
                labels = {}
                stored = stored_faces(library, [row["id"] for row in chosen])
                for number, row in enumerate(chosen):
                    target = os.path.join(folder, "%04d" % number, os.path.basename(row["path"]))
                    os.makedirs(os.path.dirname(target))
                    shutil.copy2(row["path"], target)
                    labels[paths.key(target)] = {"stratum": row["stratum"], "faces": row["faces"],
                                                 "megapixels": row["megapixels"], "number": number,
                                                 "stored": stored.get(row["id"], [])}
                output["sample"] = sandbox.census_counts(chosen, {"photos": len(chosen)})
                output["sample"]["by_stratum"] = {name: sum(1 for r in chosen if r["stratum"] == name)
                                                  for name in sandbox.STRATA}
                result = run_child(sandbox_dir, {"db": db_path, "folder": folder, "labels": labels}, sampler,
                                   args.timeout)
                output.update(result)
                output["table"] = stage_table(result["photos"])
                start, end = result["loop"]["start"], result["loop"]["end"]
                print_table(output["table"])
            elif args.mode == "event":
                photos, output["folder_number"] = event_photos(library, args.folder_number, print)
                folder = new_folder(placed, "pipeline-event")
                for path in photos:
                    shutil.copy2(path, folder)
                seconds, code, start, end = run_cli(sandbox_dir, ["--db", db_path, "index", folder], sampler,
                                                    args.timeout)
                output["index"] = {"photos": len(photos), "seconds": seconds, "exit": code,
                                   "seconds_a_photo": seconds / len(photos) if photos else None}
                print("  index: %d photos in %.1f s, %.2f s a photo (exit %d)" % (
                    len(photos), seconds, output["index"]["seconds_a_photo"] or 0, code))
            else:
                seconds, code, start, end = run_cli(sandbox_dir, ["--db", db_path, "cluster-faces"], sampler,
                                                    args.timeout)
                output["cluster_faces"] = {"seconds": seconds, "exit": code}
                print("  cluster-faces: %.1f s (exit %d)" % (seconds, code))
            sampler.stop()
            output["card"] = sandbox.window(sampler.gpu, start, end)
            output["processors"] = sampler.processor(start, end)
            output["samples"] = {"interval_ms": sampler.interval, "gpu": sampler.gpu, "cpu": sampler.cpu}
            print("  card: %s" % json.dumps(output["card"]))
            print("  processors: %s" % json.dumps(output["processors"]))
    finally:
        if sampler is not None:
            sampler.stop()
        if sandbox_dir:
            if args.keep:
                print("sandbox left at %s" % sandbox_dir)
            else:
                output["sandbox_deleted"] = sandbox.remove_sandbox(sandbox_dir)
        capped = "-cap%d" % args.detect_max_side if args.detect_max_side else ""
        path = os.path.join(out, "pipeline-%s-%s%s-%s.json" % (args.mode, output["library"], capped, stamp))
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(output, handle, indent=1)
        print("written %s" % path)


if __name__ == "__main__":
    main()
