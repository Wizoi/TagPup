"""What the measurement scripts share: a private copy of a library, a free port, a clean-up that tells.

scripts/measure_identify_faces.py, measure_suggest_folder.py and measure_select_list.py each run the app in a
sandbox: the library copied through SQLite's backup API under a temporary TAGPUP_HOME, the library's roots
placed at empty sandbox folders by the sandbox's own machine map, a server on a free port, all deleted
afterwards. The sandbox is a home of its own (`environment`, `enter`: tagpup.config.own_home_environment, what
a test's home sets too): a sandbox's server once wrote its records of where it answers into the owner's
%LOCALAPPDATA%\\TagPup\\servers (docs/findings.md, #796). Its turn on the graphics card is the one folder it
shares with the owner's apps (#810): a sandbox that loads models takes its turn in the owner's line, as every
process on the machine does (tagpup.ml.gpu), so it never sits on the 10 GB card beside the owner's index (#750).
Side effects, wanted: a sandbox waiting for the card makes the owner's idle server give up its models (its next
Suggest loads them again, about 15 s), and a measurement that loads models measures a cold load.
measure_pipeline_stages.py and measure_pipeline_holdouts.py share the census and the sample of
docs/AI_PIPELINE.md's phase 0 here too (below). A script may not import another script, so what they share is here, a helper of scripts/
(tests/test_layers.py, HELPERS) beside code_snapshot, which copies the code.
"""
import os
import random
import shutil
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup.store import db as tagpup_db  # noqa: E402
from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.ml import gpu  # noqa: E402
from tagpup.core import paths, processes  # noqa: E402
from tagpup.services import identify as identify_service  # noqa: E402
from tagpup.services import inspect as inspect_service  # noqa: E402
from tagpup.services import photos as photo_service  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402

#: The repository: a measurement's output goes outside it.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    """A port nothing is on, so a run can never collide with a server you are using."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def environment(sandbox, **more):
    """The environment of a process run in `sandbox` -- a server, a CLI command: this one's, with the sandbox as
    its home and every folder of the user's own a TagPup process touches moved into it, and `more`."""
    env = dict(os.environ)
    env.update(tagpup_config.own_home_environment(sandbox))
    env.pop(gpu.ENV, None)   # the card is the machine's: the owner's line, not a home's own (#810)
    env.update(more)
    return env


def enter(sandbox):
    """Make this process's own work happen in `sandbox`, as `environment` does for one it starts."""
    os.environ.update(tagpup_config.own_home_environment(sandbox))
    os.environ.pop(gpu.ENV, None)


def copy_library(source_db, target):
    """Copy a library to `target` through SQLite's backup API, the original opened read-only.

    A live library has a write-ahead log beside it, and copying the .db on its own captures a file
    that is missing whatever is still in the WAL. Returns the size of the copy in GB."""
    started = time.time()
    source = tagpup_db.connect(tagpup_db.readonly_uri(source_db), uri=True)
    destination = tagpup_db.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    size = os.path.getsize(target) / 1e9
    print("  copied %.1f GB in %.1fs" % (size, time.time() - started))
    return size


def place_roots(db_path, sandbox):
    """Make the sandbox's copy of the library safe if it holds roots: write the sandbox's OWN
    machine map (machine_roots.json in its home) placing each root at an empty folder of the sandbox,
    never at the real photos -- a converted library copy would otherwise point at them through the
    machine's map, and a sandbox server would read them, or write a tag into them. Fails loudly
    (roots_service.SandboxError) when the copy has a root the sandbox did not place. {name: place}."""
    map_path = os.path.join(sandbox, tagpup_config.MACHINE_ROOTS_FILE)
    machine = roots_service.Machine(
        lambda: tagpup_config.machine_roots(map_path),
        lambda name, place: tagpup_config.add_machine_root(name, place, path=map_path),
        lambda: map_path)
    placed = roots_service.place_in_sandbox(Library(db_path), sandbox, machine)
    for name, place in placed.items():
        print("  root %s placed at %s, by the sandbox's own map" % (name, place))
    return placed


def remove_sandbox(sandbox):
    """Delete the sandbox, and say so if it cannot be.

    Windows releases a dead process's file handles a moment after it exits, so the
    first attempt can fail on a database the server still had open. Retried rather
    than ignored: this directory holds a copy of the whole library, and the first
    version of this quietly left 2.7 GB in the temp directory every run because the
    failure was swallowed.
    """
    for _attempt in range(10):
        shutil.rmtree(sandbox, ignore_errors=True)
        if not os.path.exists(sandbox):
            return True
        time.sleep(0.5)

    size = 0
    for root, _dirs, files in os.walk(sandbox):
        size += sum(os.path.getsize(os.path.join(root, f)) for f in files)
    print("\nWARNING: could not delete the sandbox. %.1f GB left at:\n  %s"
          % (size / 1e9, sandbox), file=sys.stderr)
    return False

# ---- What the pipeline measurements share (measure_pipeline_stages, measure_pipeline_holdouts) ----------------------
#
# docs/AI_PIPELINE.md, phase 0. The sample is drawn from a library read-only, through the services and the home's own
# machine map (run with TAGPUP_HOME at the owner's home): its photos, the faces each already has, and the size of each
# picture as its header gives it. Nothing here writes; nothing here names a photo in what it returns for output.

#: The sample's strata, by the faces a photo already has (owner, 2026-10-16: weighted to many faces); "worst" is the
#: photos with the most.
STRATA = ("none", "1-3", "4-9", "10+", "worst")

#: The share of the sample, after the worst photos, each stratum is drawn to; 10 or more is raised until it and the
#: worst make at least a third of the sample (the brief).
SHARES = {"10+": 0.35, "4-9": 0.25, "1-3": 0.25, "none": 0.15}

#: The bins of the resolution distribution, in megapixels: (label, up to).
MEGAPIXEL_BINS = (("under 2", 2), ("2-6", 6), ("6-12", 12), ("12-20", 20), ("20-30", 30), ("30 and over", None))


def stratum(faces):
    """The stratum of a photo that has `faces` faces."""
    if not faces:
        return "none"
    if faces <= 3:
        return "1-3"
    if faces <= 9:
        return "4-9"
    return "10+"


def _spread(pool, wanted, rng):
    """`wanted` of `pool` spread evenly over its megapixels, smallest and largest included."""
    if len(pool) <= wanted:
        return list(pool)
    ordered = sorted(pool, key=lambda row: (row["megapixels"], rng.random()))
    if wanted == 1:
        return [ordered[rng.randrange(len(ordered))]]
    picks = sorted({round(i * (len(ordered) - 1) / (wanted - 1)) for i in range(wanted)})
    return [ordered[i] for i in picks]


def choose_sample(rows, size, worst=10, rng=None):
    """The sample: the `worst` photos by faces (10 or more), then each stratum's share of the rest (SHARES), each
    spread over its megapixels, and one photo turned by its Orientation in each stratum that has one. `rows` are
    {"id", "faces", "megapixels", "turned", ...}; each chosen row comes back with its "stratum"."""
    rng = rng or random.Random(0)
    ranked = sorted(rows, key=lambda row: (-row["faces"], row["id"]))
    chosen = [dict(row, stratum="worst") for row in ranked[:worst] if row["faces"] >= 10]
    taken = {row["id"] for row in chosen}
    rest = max(0, size - len(chosen))
    quotas = {"10+": max(round(SHARES["10+"] * rest), -(-size // 3) - len(chosen))}
    left, others = rest - quotas["10+"], [name for name in SHARES if name != "10+"]
    weight = sum(SHARES[name] for name in others)
    for name in others:
        quotas[name] = round(left * SHARES[name] / weight)
    for name, wanted in quotas.items():
        pool = [row for row in rows if row["id"] not in taken and stratum(row["faces"]) == name]
        picked = _spread(pool, max(0, wanted), rng)
        turned = [row for row in pool if row.get("turned")]
        if picked and turned and not any(row.get("turned") for row in picked):
            picked[len(picked) // 2] = turned[rng.randrange(len(turned))]
        chosen += [dict(row, stratum=name) for row in picked]
        taken.update(row["id"] for row in picked)
    return chosen


#: Two boxes are one face at this overlap or more (docs/AI_PIPELINE.md, phase 3's carrying rule: a face kept or lost between two detections).
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


def summary(values):
    """{"n", "mean", "median", "p10", "p90", "max", "sum"} of `values` (None left out); n 0 and nothing else for none."""
    values = sorted(v for v in values if v is not None)
    if not values:
        return {"n": 0}

    def at(share):
        return values[min(len(values) - 1, int(round(share * (len(values) - 1))))]
    return {"n": len(values), "mean": sum(values) / len(values), "median": at(0.5), "p10": at(0.1), "p90": at(0.9),
            "max": values[-1], "sum": sum(values)}


def megapixel_bins(values):
    """{bin label: how many of `values` (megapixels) fall in it} (MEGAPIXEL_BINS), every bin listed."""
    counts = {label: 0 for label, _top in MEGAPIXEL_BINS}
    for value in values:
        for label, top in MEGAPIXEL_BINS:
            if top is None or value < top:
                counts[label] += 1
                break
    return counts


def library_photos(library):
    """(id, native path) of every photo of `library`, read-only, under the roots it holds as the home's machine map
    places them: what the census starts from. Each of the three libraries holds roots (2026-10-16); one with none,
    or one whose roots this machine does not place, is refused with the sentence."""
    problem = roots_service.problem(library)
    if problem:
        raise SystemExit(problem)
    names = [entry["name"] for entry in roots_service.listing(library)["roots"]]
    if not names:
        raise SystemExit("The library holds no root: the census reads a library's photos by its roots.")
    places = tagpup_config.machine_roots()
    found = {}
    for name in names:
        answer = inspect_service.photos(library, folder=places[name][0], reveal=True, limit=10 ** 9)
        found.update(zip(answer["ids"], answer["paths"]))
    return sorted(found.items())


def census(library, deep=False, report=print):
    """The photos of `library` a measurement may draw from, and the counts behind them, read-only:
    (rows, counts). Each row {"id", "path", "faces", "megapixels", "turned", "folder"}: a photo whose file is
    there and whose header reads, with the faces it has in play (a named face, or an unnamed one not excluded:
    services.identify.photos_waiting) and its size (services.photos.box_shape: "turned" is an Orientation of 2 to
    8). The counts say how many photos are gone, not looked at (a share that did not answer), fail to open
    (with `deep`: services.inspect.files_that_fail_to_open, which reads each file's end and header), and have no
    readable header. `path` is for the copy alone and never written into a measurement's output."""
    held = library_photos(library)
    report("  %d photos held" % len(held))
    missing = inspect_service.missing_files(library, limit=10 ** 9)
    gone = {photo_id for folder in missing["by_folder"] for photo_id in folder["ids"]}
    counts = {"photos": len(held), "gone": len(gone), "not_looked_at": missing["not_looked_at"]}
    failing = set()
    if deep:
        failed = inspect_service.files_that_fail_to_open(library)
        failing = {photo_id for photo_id, _path, _why in failed["files"]}
        counts["fail_to_open"], counts["unreached"] = failed["count"], failed["unreached"]
    report("  %d gone, %d not looked at, %s failing to open" % (len(gone), missing["not_looked_at"],
                                                               counts.get("fail_to_open", "not checked")))
    in_play = {paths.key(row["path"]): row["unmatched_count"] + row["matched_count"]
               for row in identify_service.photos_waiting(library, show_matched=True)}
    rows, headless = [], 0
    for number, (photo_id, path) in enumerate(held, 1):
        if photo_id in gone or photo_id in failing:
            continue
        shape = photo_service.box_shape(path)
        if not shape["size"]:
            headless += 1
            continue
        width, height = shape["size"]
        rows.append({"id": photo_id, "path": path, "faces": in_play.get(paths.key(path), 0),
                     "megapixels": width * height / 1e6, "turned": shape["turned"],
                     "folder": paths.key(os.path.dirname(path))})
        if number % 5000 == 0:
            report("  %d of %d headers read" % (number, len(held)))
    counts["no_header"] = headless
    counts["readable"] = len(rows)
    return rows, counts


def census_counts(rows, counts):
    """What a census says, for output: the counts, the strata (photos, faces and megapixels a photo), the
    resolution distribution and the photos turned by their Orientation. No path, no name."""
    strata = {}
    for name in STRATA[:-1]:
        members = [row for row in rows if stratum(row["faces"]) == name]
        strata[name] = {"photos": len(members), "faces": summary([row["faces"] for row in members]),
                        "megapixels": summary([row["megapixels"] for row in members])}
    return dict(counts, strata=strata, most_faces=sorted((row["faces"] for row in rows), reverse=True)[:10],
                megapixels=summary([row["megapixels"] for row in rows]),
                megapixel_bins=megapixel_bins([row["megapixels"] for row in rows]),
                turned=sum(1 for row in rows if row["turned"]))


def owner_relative(path, places):
    """(root name, the path under it) of the native `path`, by the machine map `places` ({name: [place, ...]}):
    where its copy goes under the sandbox's place of the same root, so that the copy's row describes it. None
    for a path under no root."""
    for name, spots in places.items():
        for spot in spots:
            if paths.is_under(path, spot):
                return name, os.path.relpath(path, spot)
    return None


def copy_at_rows(rows, placed, places):
    """Copy each photo of `rows` to the sandbox's place of its root (`placed`, place_roots'), at the path it has
    under the root, its stamp kept (shutil.copy2): the copy of the library holds a row and a vector that describe
    it. {native path: the copy's path}."""
    copies = {}
    for row in rows:
        name, under = owner_relative(row["path"], places)
        target = os.path.join(placed[name], under)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(row["path"], target)
        copies[row["path"]] = target
    return copies


def parse_smi(line):
    """(utilisation %, memory used MiB) of one line of nvidia-smi's --format=csv,noheader,nounits; None for a line
    that is not two numbers."""
    try:
        utilisation, memory = (float(part) for part in line.split(","))
    except ValueError:
        return None
    return utilisation, memory


def window(samples, start, end):
    """What the card did between `start` and `end` (epoch seconds), from Sampler.gpu's (time, util, MiB): the
    samples, the share at 0% and under 10% utilisation, the mean, and the peak memory."""
    inside = [(util, mib) for at, util, mib in samples if start <= at <= end]
    if not inside:
        return {"samples": 0}
    return {"samples": len(inside), "idle_share": sum(1 for util, _ in inside if util == 0) / len(inside),
            "under_10_share": sum(1 for util, _ in inside if util < 10) / len(inside),
            "mean_utilisation": sum(util for util, _ in inside) / len(inside),
            "peak_memory_mib": max(mib for _, mib in inside)}


def _system_times():
    """(idle, busy) 100-ns ticks of every processor since start (Windows)."""
    import ctypes
    idle, kernel, user = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    return idle.value, kernel.value + user.value - idle.value


def _process_ticks(pid):
    """The processor time process `pid` has used, in 100-ns ticks, or None (Windows)."""
    import ctypes
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        created, ended, kernel, user = (ctypes.c_ulonglong() for _ in range(4))
        if not ctypes.windll.kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ended),
                                                      ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return kernel.value + user.value
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


class Sampler:
    """What the card and the processors did while a measurement ran: nvidia-smi's utilisation and memory every
    `interval` milliseconds (`gpu`: [(epoch, util %, MiB)]; its utilisation is the share of its own sample period a
    kernel ran), and every interval the machine's processor use and the measured process's (`cpu`: [(epoch, machine
    %, process %)], both of all processors; the rest of the machine is the difference). `watch(pid)` names the
    process, or `watch(file)` a file the process writes its own id into: the venv's python.exe is a launcher, and
    the interpreter doing the work is its child (measured 2026-10-16: the launcher's own use read 0%). Nothing about any other process's use of the card is to be had from nvidia-smi on Windows: memory used
    before the measurement starts is theirs."""

    def __init__(self, interval=500):
        self.interval = interval
        self.gpu, self.cpu = [], []
        self.pid = None
        self._smi = None
        self._stop = threading.Event()
        self._threads = []

    def watch(self, pid):
        self.pid = pid

    def watched(self):
        """The id of the process measured: the one named, or the one in the file named once it is written."""
        if isinstance(self.pid, str):
            try:
                with open(self.pid, encoding="utf-8") as handle:
                    return int(handle.read().strip())
            except (OSError, ValueError):
                return None
        return self.pid

    def start(self):
        try:
            self._smi = processes.start(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
                                         "--format=csv,noheader,nounits", "-lms", str(self.interval)],
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError:
            self._smi = None
        if self._smi is not None:
            self._threads.append(threading.Thread(target=self._read_smi, daemon=True))
        self._threads.append(threading.Thread(target=self._read_cpu, daemon=True))
        for thread in self._threads:
            thread.start()
        return self

    def _read_smi(self):
        for line in self._smi.stdout:
            found = parse_smi(line)
            if found is not None:
                self.gpu.append((time.time(), found[0], found[1]))

    def _read_cpu(self):
        processors = os.cpu_count() or 1
        before, process_before, then = _system_times(), None, time.time()
        while not self._stop.wait(self.interval / 1000):
            after, now = _system_times(), time.time()
            idle, busy = after[0] - before[0], after[1] - before[1]
            machine = 100.0 * busy / (idle + busy) if idle + busy else 0.0
            pid = self.watched()
            ticks = _process_ticks(pid) if pid else None
            process = None
            if ticks is not None and process_before is not None:
                process = 100.0 * (ticks - process_before) / ((now - then) * 1e7 * processors)
            self.cpu.append((now, machine, process))
            before, process_before, then = after, ticks, now

    def stop(self):
        self._stop.set()
        if self._smi is not None and self._smi.poll() is None:
            self._smi.terminate()
            try:
                self._smi.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._smi.kill()
        for thread in self._threads:
            thread.join(timeout=5)

    def processor(self, start, end):
        """{"machine_mean", "process_mean", "others_mean"} processor use % between `start` and `end`."""
        inside = [(machine, process) for at, machine, process in self.cpu if start <= at <= end]
        if not inside:
            return {"samples": 0}
        machine = sum(m for m, _ in inside) / len(inside)
        measured = [p for _, p in inside if p is not None]
        process = sum(measured) / len(measured) if measured else None
        return {"samples": len(inside), "machine_mean": machine, "process_mean": process,
                "others_mean": None if process is None else max(0.0, machine - process)}


def versions():
    """The versions of what the models run on (docs/AI_PIPELINE.md, phase 0). Imports torch: call it where the
    models are wanted anyway."""
    import importlib.metadata as metadata
    found = {}
    for name in ("torch", "open_clip_torch", "facenet-pytorch", "faiss-cpu", "scikit-learn", "Pillow", "numpy"):
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            found[name] = None
    return found


def outside_the_repository(folder):
    """Refuse an output folder inside the repository: what a measurement writes is the owner's, not the code's."""
    if paths.same(folder, REPO) or paths.is_under(folder, REPO):
        raise SystemExit("Write the measurements outside the repository (%s is inside it)." % folder)
    os.makedirs(folder, exist_ok=True)
    return folder
