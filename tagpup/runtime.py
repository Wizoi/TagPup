"""The composition root: each library's settings, made into the process's long-lived
objects.

    runtime = Runtime()
    app = tagpup.web.app.create_app("tagpup", runtime=runtime)
    runtime.warm_up_in_background(libraries)

A library's settings are its own (tagpup.services.settings; docs/ARCHITECTURE.md, phase
7.6), and `library_settings` is where an entry point reads them: the first time a
library holding none is opened, it is stamped with the defaults. Nothing reads
config.ini (tests/test_config_single_owner.py).

A Runtime builds each CLIP model and each set of face models once per set of settings,
the first time a library with those settings asks: two libraries on one model share it,
and a library on another gets its own. A model no library it serves uses any more, and
no run holds, is unloaded; so is every model none has used for `idle_after` seconds
(the web server's `--release-models-after`), and the next Suggest loads them again.
It keeps each library's photo index for Suggest, brought up
to date when a run begins, and a run keeps the one it began with to its end.

A model is loaded onto the graphics card only with the process's turn on it
(tagpup.ml.gpu; docs/findings.md, #750): a Suggest run takes one the first time it uses a
model -- a run over photos the index has already read, their vectors kept and their
faces' detection recorded (tagpup.store.faces_detected, #773), uses none and never waits --
and holds it to its end; the CLI's index takes one for its run (gpu_turn); the warm-up only
when no one has to wait for it. The web server keeps its turn while its models stay
loaded between runs, and gives it up, unloading them, as soon as another process waits. Nothing below it builds a model or reads a
setting: a service that uses a model is given it (docs/ARCHITECTURE.md, "The layers,
revisited").

It replaces scripts/suggest_models.py, which the web launcher used to fill a module-level
slot in tagpup.jobs.suggestions with (docs/findings.md, #112), and the class attribute
every ClipEmbedder shared its model through. `tests/test_models_single_owner.py` fails a
module that builds a model anywhere else.

`background(runtime)` is what the web server -- the always-on process -- runs beside its
requests (docs/ARCHITECTURE.md, phase 8): the recurring jobs, the release of idle caches,
and the folder watcher (tagpup.jobs.watching). Each is registered once, in BACKGROUND.
"""
import collections
import logging
import os
import threading
import time

from tagpup import config as tagpup_config
from tagpup.core import library as libraries
from tagpup.core.idle import IdleCaches
from tagpup.core.library import Library
from tagpup.files import images
from tagpup.ml import gpu
from tagpup.jobs import indexing as indexing_jobs
from tagpup.jobs import recurring, watching
from tagpup.services import damaged_photos, file_changes, folder_ids, indexing, search
from tagpup.services import settings as library_settings_service
from tagpup.services import suggester as suggestions
from tagpup.services import sync as sync_service
from tagpup.store import embeddings as store_embeddings

logger = logging.getLogger(__name__)


def library_settings(library):
    """The library's settings (tagpup.services.settings.LibrarySettings), a library
    holding none stamped first, with the defaults.

    The first time this process reads them, a change of photo files a crash left half
    done is settled, with the ExifTool they name (tagpup.services.file_changes.settle_once):
    the changes of rows are settled when the library is first opened (schema.ensure), but
    files need ExifTool, which only the library's settings say where to find."""
    settings = library_settings_service.of(library)
    file_changes.settle_once(library, tagpup_config.exiftool_path(settings.exiftool))
    return settings


def peek_settings(library):
    """The library's settings, writing nothing: for a read-only look (the MCP server's
    inspections, tools/doctor.py). A library never stamped reads as stamping would make it."""
    return library_settings_service.read(library)


#: Set, a process a test started runs the recurring jobs, which it otherwise never does;
#: set NO_JOBS, no process runs them (a sandbox measuring the app, say). The folder watcher
#: follows the same two: no watcher where no job runs.
RUN_JOBS = "TAGPUP_RUN_JOBS"
NO_JOBS = "TAGPUP_NO_JOBS"


def runs_recurring_jobs():
    """Does this web server run the recurring jobs (tagpup.jobs.recurring)? Only the web
    server asks: the always-on process runs them, nothing else (owner, 2026-09-26). Not
    when NO_JOBS is set, nor in a test run (tagpup.ml.under_test) unless RUN_JOBS is: a
    server a test starts would snapshot its library in the background."""
    if os.environ.get(NO_JOBS):
        return False
    if os.environ.get(RUN_JOBS):
        return True
    from tagpup.ml import under_test
    return not under_test()


def home_libraries(test_mode=False):
    """Every library in the home's data folder that is there: the test libraries in test
    mode, else the rest (tagpup.core.library.picker_names)."""
    folder = tagpup_config.data_dir()
    files = os.listdir(folder) if os.path.isdir(folder) else []
    found = [Library(tagpup_config.library_path(libraries.for_mode(name + ".db", test_mode)))
             for name in libraries.picker_names(files, test_mode)]
    return [library for library in found if os.path.exists(library.path)]


def recurring_jobs(runtime=None, libraries=None, clock=None):
    """The recurring jobs' runner (tagpup.jobs.recurring.Runner) over `libraries`, a
    callable read at each look -- by default every library in the home's data folder --
    handing each job the process's `runtime`."""
    return recurring.Runner(libraries or home_libraries, clock=clock or time.time, given={"runtime": runtime})


#: How long the models, the vectors and the other caches may go unused before the web
#: server lets them go (minutes): a ViT-H-14 keeps a few GB of GPU memory, photo_index's
#: vectors 270 MB, and the always-on process would hold them all day.
RELEASE_MODELS_AFTER_MINUTES = 5

#: The runtime's own entries in its idle registry (Runtime.idle).
MODELS, PHOTO_INDEXES = "models", "photo indexes"


# ---- What the always-on process runs beside its requests ---------------------------------

class Every:
    """`call()` every `seconds` on a daemon thread of its own, until stop(): a background
    task (BACKGROUND). Never busy: what it calls is quick, and an update need not wait."""

    def __init__(self, name, call, seconds):
        self.name, self._call, self._seconds = name, call, seconds
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            return self._thread
        stop = self._stop = threading.Event()

        def loop():
            while not stop.wait(self._seconds):
                try:
                    self._call()
                except Exception:
                    logger.exception("%s failed", self.name)

        self._thread = threading.Thread(target=loop, name=self.name, daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, timeout=30):
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def busy(self):
        return False


#: What the web server runs beside its requests, by name: each is make(runtime) -> a task
#: with start(), stop(timeout) -> whether it ended, and busy() -> whether it is doing work
#: an update must let finish; or None when this process runs no such thing. The web
#: server starts each as it starts and stops each as it stops (tagpup_web.main), and an
#: update stops them and waits for none to be busy before it moves the server onto a new
#: version (tagpup.web.lifecycle). One place, so sync's watcher (phase 8c) is one entry.
BACKGROUND = {}


def background_task(name):
    """Register the function it decorates as the background task `name`: make(runtime)."""
    def register(make):
        if name in BACKGROUND:
            raise ValueError("a background task named %r is registered already" % name)
        BACKGROUND[name] = make
        return make
    return register


@background_task("recurring jobs")
def _recurring_jobs_task(runtime):
    """The recurring jobs of every library in the data folder (tagpup.jobs.recurring), in
    the process that runs them (runs_recurring_jobs)."""
    return recurring_jobs(runtime) if runs_recurring_jobs() else None


@background_task("release idle caches")
def _idle_caches_task(runtime):
    """Let go of each cache in the runtime's idle registry -- the models, the vectors, the
    web's caches -- once unused for the idle period (Runtime.idle)."""
    if runtime is None or not runtime.idle_after:
        return None
    return Every("ReleaseIdleCachesThread", runtime.release_idle, max(1.0, min(60.0, runtime.idle_after / 4)))


@background_task("folder watcher")
def _folder_watcher_task(runtime):
    """Each library's folders watched, and a folder synced when its notifications settle
    (tagpup.jobs.watching); the whole library at start. In the process that runs the
    recurring jobs, and never in one a test started unless it asks (runs_recurring_jobs):
    NO_JOBS means no watcher either."""
    if not runs_recurring_jobs():
        return None
    return watching.Watcher(home_libraries, watch_folders,
                            lambda library, folder: sync(library, folder=folder, apply=True), images.is_photo,
                            recent=lambda library: sync_service.synced_whole_within(library, watching.CATCH_UP_SKIP),
                            written=check_damaged)


def watch_folders(library):
    """The folders to watch for `library`: its roots, from its settings read without
    stamping them, and the folders it holds photos in (tagpup.services.sync.watch_folders)."""
    return sync_service.watch_folders(library, peek_settings(library).roots)


class Background:
    """The background tasks one process runs: [(name, task)]."""

    def __init__(self, tasks):
        self._tasks = list(tasks)

    def names(self):
        return [name for name, _task in self._tasks]

    def task(self, name):
        """The task registered as `name` this process runs, or None: the Activity page asks
        the folder watcher what it watches and the recurring jobs' runner to run a job now."""
        for each, task in self._tasks:
            if each == name:
                return task
        return None

    def start(self):
        for name, task in self._tasks:
            logger.info("Starting %s", name)
            task.start()

    def stop(self, timeout=30):
        """Stop each, waiting up to `timeout` in all -- one deadline, not one per task, so
        a drain's waits add up to what it was given. The names of those still running."""
        deadline = time.monotonic() + timeout
        still = []
        for name, task in self._tasks:
            if not task.stop(max(0.0, deadline - time.monotonic())):
                still.append(name)
        return still

    def busy(self):
        """The names of those doing work now that an update must let finish."""
        return [name for name, task in self._tasks if task.busy()]


def background(runtime, registry=None):
    """The background tasks this process runs (BACKGROUND, or `registry`), made for `runtime`."""
    tasks = []
    for name, make in (BACKGROUND if registry is None else registry).items():
        task = make(runtime)
        if task is not None:
            tasks.append((name, task))
    return Background(tasks)


def exiftool(library, settings=None):
    """The ExifTool program to run for `library`: the one it names, else the machine's
    (tagpup.config.exiftool_path)."""
    return tagpup_config.exiftool_path((settings or library_settings(library)).exiftool)


#: Called with (library, folder) when this process's index queue has finished a folder, indexed or not: rows were
#: written even when clustering failed afterwards. The server puts here the dropping of its cached scan of the
#: folder, which describes it as it was before (tagpup.web.tagpup_routes; docs/findings.md, #341). A process with
#: no pages has none.
folder_indexed = []


def index_folder(library, subfolders=True):
    """How this process adds a folder to `library` from its index queue: the CLI's `index`
    in a process of its own, from this code (tagpup.services.indexing.index_folder); with
    its subfolders unless not `subfolders`. Whoever queued it, the folder's cached scans are dropped
    when it ends (folder_indexed)."""
    def index(folder, cluster, report):
        try:
            return indexing.index_folder(library, folder, tagpup_config.CODE_ROOT, cluster=cluster, report=report,
                                         subfolders=subfolders)
        finally:
            for told in list(folder_indexed):
                try:
                    told(library, folder)
                except Exception:
                    logger.exception("Could not tell that %s was indexed", folder)
    return index


def sync(library, folder=None, apply=False, index_new=True):
    """Bring `library` in step with its folders, or with `folder` (tagpup.services.sync):
    a dry run unless `apply`, with the ExifTool the library names. A dry run reads the
    library's settings without stamping them, and writes nothing. Applied, the folders of
    new files go on this process's index queue for the library (tagpup.jobs.indexing),
    unless not `index_new`; they are indexed one at a time, after the sync returns.

    What the entry points call -- the CLI's `sync`, the MCP server's tool, the route --
    and what a recurring job calls as sync(library, apply=True)."""
    settings = library_settings(library) if apply else peek_settings(library)
    queue = None
    if index_new:
        # A folder the library holds is indexed without its subfolders: they may be
        # folders to review, or ignored.
        # One job, one run of the indexer, for every folder of new files.
        def queue(folders):
            return indexing_jobs.queue_for(library).start(folders, index_folder(library, subfolders=False),
                                                          together=True)
    return sync_service.sync(library, folder, apply, exiftool(library, settings), queue,
                             roots=settings.roots, ignored=settings.ignored,
                             reread_resized=settings.reread_resized_pictures)


def mark_folders(library, apply=False):
    """Write a `.tagpup` marker in each leaf folder of `library` and record the ids
    (tagpup.services.folder_ids.mark): a dry run unless `apply`. Needs no setting of the library's,
    so it stamps none."""
    return folder_ids.mark(library, apply)


def follow_folder_markers(library, apply=False, rehearse=False):
    """Follow the marked folders that moved (tagpup.services.folder_ids.follow): a dry run
    unless `apply`, looking beside each folder that is gone (a sync also looks where it found files)."""
    settings = library_settings(library) if apply else peek_settings(library)
    return folder_ids.follow(library, apply=apply, exiftool_path=exiftool(library, settings), rehearse=rehearse)


def check_damaged(library, photo_paths=None):
    """Read `library`'s photos recorded damaged -- those of `photo_paths`, or every one --
    again now (tagpup.services.damaged_photos.check_again), and index again, on this
    process's index queue, the folders of those that read whole. check_again's answer, with
    `queued`, the folders queued."""
    done = damaged_photos.check_again(library, photo_paths)
    done["queued"] = 0
    if done["folders"]:
        outcome = indexing_jobs.queue_for(library).start(done["folders"], index_folder(library, subfolders=False),
                                                        together=True)
        done["queued"] = outcome.changed
    return done


def review(library):
    """The folders under the library's roots to review (tagpup.services.sync.review), from
    its settings, read without stamping them."""
    settings = peek_settings(library)
    return sync_service.review(library, settings.roots, settings.ignored)


def include(library, folder):
    """Index a folder to review, with its subfolders, on this process's index queue
    (tagpup.services.sync.include)."""
    settings = library_settings(library)

    def queue(folders):
        return indexing_jobs.queue_for(library).start(folders, index_folder(library))
    return sync_service.include(library, folder, settings.roots, queue, settings.ignored)


def _frozen(settings):
    """A settings dict as a key: its items, a list as a tuple."""
    return tuple(sorted((key, tuple(value) if isinstance(value, list) else value)
                        for key, value in settings.items()))


def _build_clip(settings):
    # Here, not at the top: open_clip and torch take seconds to import.
    from tagpup.ml.clip import ClipModel
    return ClipModel(**settings)


def _build_faces(settings):
    from tagpup.ml.faces import FaceModel
    return FaceModel(**settings)


def _part(settings, part):
    """_frozen(settings.<part>), or None for a part holding a value it cannot read: a
    command that runs no face model is not failed by a face setting (the 5.5 review)."""
    try:
        return _frozen(getattr(settings, part))
    except (ValueError, TypeError):
        return None


class RunModel:
    """What begin() hands a suggestion run: the run's model (tagpup.services.suggester.
    SuggestionModel), whatever it answers, and `end()`, which the run calls when it is
    done (tagpup.jobs.suggestions). Until then the runtime keeps what the run holds -- its
    photo index, its models -- however the library's settings change meanwhile."""

    def __init__(self, model, end):
        self._model = model
        self._end = end

    def __getattr__(self, name):
        return getattr(self._model, name)

    def end(self):
        self._end()


class _RunTurn:
    """One suggestion run's turn on the graphics card: taken the first time the run uses a
    model on the card (ensure), not before -- a run over photos whose vectors the library
    holds, and whose faces' detection it recorded (found or not: store.faces_detected),
    runs no model, and waits for no one -- and held until the run ends
    (close)."""

    def __init__(self, card, what, models, cancelled=None, report=None):
        self.card, self.what = card, what
        self.cancelled, self.report = cancelled, report
        self.needed = any(gpu.on_the_card(getattr(model, "device", None)) for model in models)
        self._hold = None
        self._closed = False
        self._lock = threading.Lock()

    def ensure(self):
        if not self.needed or self._hold is not None:
            return
        with self._lock:
            if self._hold is not None:
                return
            if self._closed:
                raise gpu.Cancelled("%s has ended" % self.what)
            waited = []

            def say(line):
                waited.append(line)
                if self.report is not None:
                    self.report(line)
            self._hold = self.card.hold(self.what, cancelled=self.cancelled, report=say)
            if waited and self.report is not None:
                self.report(None)

    def close(self):
        with self._lock:
            self._closed = True
            hold, self._hold = self._hold, None
        if hold is not None:
            hold.release()


class _OnTheCard:
    """A run's view of a model: each use takes the run's turn on the graphics card first
    (_RunTurn.ensure); everything else is the model's. `ready()` takes it without using
    the model: what PhotoEmbeddings and the suggester call before they look again for what
    an index may have written while they waited."""

    def __init__(self, model, turn):
        self._model, self._turn = model, turn

    def __getattr__(self, name):
        return getattr(self._model, name)

    def ready(self):
        self._turn.ensure()

    def load(self, *args, **kwargs):
        self._turn.ensure()
        return self._model.load(*args, **kwargs)

    def embed_image(self, *args, **kwargs):
        self._turn.ensure()
        return self._model.embed_image(*args, **kwargs)

    def embed_picture(self, *args, **kwargs):
        self._turn.ensure()
        return self._model.embed_picture(*args, **kwargs)

    def embed_text(self, *args, **kwargs):
        self._turn.ensure()
        return self._model.embed_text(*args, **kwargs)

    def detect_and_embed_faces(self, *args, **kwargs):
        self._turn.ensure()
        return self._model.detect_and_embed_faces(*args, **kwargs)


class _Lease:
    """One run's hold on a photo index and a pair of models."""

    def __init__(self, index, clip_key, faces_key):
        self.index, self.clip_key, self.faces_key = index, clip_key, faces_key
        self.ended = False


def _unload(models):
    """Let each model's weights go: a model dropped still held its gigabytes on the GPU."""
    for model in models:
        unload = getattr(model, "unload", None)
        if callable(unload):
            try:
                unload()
            except Exception as e:
                logger.error("Could not unload a model: %s", e)


class Runtime:
    """What lives as long as the process: the models, one per set of settings a library
    it serves names, and each library's photo index for Suggest.

    `clip` and `faces` stand in for every library's models: a test's fakes, which are
    then never built. `build_clip` and `build_faces` make a model from its settings
    (tagpup.ml.clip.ClipModel's and tagpup.ml.faces.FaceModel's keyword arguments): a
    test's, to see which settings each library's model was made from. `read_only` reads
    each library's settings without stamping one that holds none (peek_settings): for a
    command that only looks.

    It lets go of what nothing uses. A model is kept while a library it has served names
    its settings, or a run holds it; when a library's settings change (seen at its next
    ask, or told by `settings_changed`) or it is forgotten, a model no longer so kept is
    dropped and unloaded -- it used to stay on the GPU beside its successor, one more for
    each change. A library's photo index replaced after its model changed is closed when
    the last run holding it ends, never under one.

    `idle_after`, seconds: what the process keeps only while it is used is registered in
    `idle` (tagpup.core.idle.IdleCaches) -- here the models, let go when none has been
    asked for in that long and no run holds one, and each library's photo index (its
    vectors), let go when not asked for in that long and no run holds it -- and
    release_idle() lets go of each so idle; the next Suggest builds them again. The web
    registers its own caches in the same `idle`. None keeps everything for the process's
    life (the CLI's, which is short). `clock` is time.monotonic, or a test's.
    """

    def __init__(self, clip=None, faces=None, build_clip=None, build_faces=None, read_only=False,
                 idle_after=None, clock=None, card=None, keep_models=False):
        self._clip = clip
        self._faces = faces
        #: The process's turn on the graphics card (tagpup.ml.gpu), or a test's: each model
        #: built is handed it. With `keep_models` -- the web server, which keeps its models
        #: between runs, for `idle_after` or, without one, for good (--release-models-after
        #: 0) -- the turn is kept with them and given up, every model unloaded first, when
        #: another process waits for it; without (the CLI), given up as each run ends (#774).
        self.card = card if card is not None else gpu.card()
        self.card.keep_when_idle = bool(keep_models)
        self.card.on_release = self._unload_every_model
        if keep_models:
            # What a waiter is told while no run holds it: given up within a second or two.
            self.card.idle_what = "the TagPup server's models, kept between runs (letting go)"
        self._build_clip = build_clip or _build_clip
        self._build_faces = build_faces or _build_faces
        self._read_only = read_only
        self.idle_after = idle_after
        #: What the process keeps only while it is used, and the one timer's registry.
        self.idle = IdleCaches(idle_after, clock=clock or time.monotonic)
        self.idle.register(MODELS, self._release_models, in_use=lambda: bool(self._held))
        self.idle.register(PHOTO_INDEXES, self._release_indexes)
        self._clips = {}
        self._face_models = {}
        #: Guards the models, what each library uses, the indexes and the runs' leases.
        self._lock = threading.RLock()
        #: library.key -> (its CLIP settings, its face settings), as last asked.
        self._uses = {}
        #: ("clip" or "faces", settings) -> how many runs hold that model.
        self._held = collections.Counter()
        #: library.key -> its photo index now.
        self._indexes = {}
        #: id(index) -> how many runs hold it; and the indexes replaced while one did.
        self._index_runs = collections.Counter()
        self._retired = {}

    # ---- A library's settings --------------------------------------------------------

    def settings(self, library):
        """The library's settings, read now (library_settings; peek_settings for a
        read-only runtime): a change made in the dialog, or by another process, is seen
        by the next run."""
        return peek_settings(library) if self._read_only else library_settings(library)

    def model_key(self, library, settings=None):
        """The name the library's CLIP model's vectors are kept under (tagpup.store.embeddings)."""
        return store_embeddings.model_key(**(settings or self.settings(library)).embedder)

    def candidate_words(self, library):
        """The library's words CLIP is asked about a photo, before the tree's."""
        return self.settings(library).candidate_words

    def exiftool(self, library):
        """The ExifTool program to run for `library`."""
        return tagpup_config.exiftool_path(self.settings(library).exiftool)

    def sync(self, library, folder=None, apply=False):
        """Sync `library` (the module's sync): what the recurring sync job calls, handed
        this Runtime by the entry point that runs the jobs."""
        return sync(library, folder=folder, apply=apply)

    def settings_changed(self, library):
        """The library's settings have changed (tagpup.web.settings_routes, after a save):
        what it used and nothing else uses is let go now, not at its next ask -- the
        models unloaded, its photo index closed or, while a run holds it, closed when
        that run ends."""
        settings = self.settings(library)
        with self._lock:
            self._note(library, settings)
            index = self._indexes.get(library.key)
            if index is not None and index.model != self.model_key(library, settings):
                del self._indexes[library.key]
                self._retire(index)
            dropped = self._release()
        _unload(dropped)

    # ---- The models ------------------------------------------------------------------

    def clip(self, library, settings=None):
        """The CLIP model the library's settings name, built the first time any library
        on those settings asks for it. Its weights load on first use, or in warm_up."""
        if self._clip is not None:
            return self._clip
        settings = settings or self.settings(library)
        self.idle.used(MODELS)
        with self._lock:
            model = self._model(self._clips, self._build_clip, settings.embedder)
            dropped = self._note(library, settings) and self._release()
        _unload(dropped or ())
        return model

    def faces(self, library, settings=None):
        """The face models the library's settings name, built the first time any library
        on those settings asks for them. Their weights load on first use, or in warm_up."""
        if self._faces is not None:
            return self._faces
        settings = settings or self.settings(library)
        self.idle.used(MODELS)
        with self._lock:
            model = self._model(self._face_models, self._build_faces, settings.faces)
            dropped = self._note(library, settings) and self._release()
        _unload(dropped or ())
        return model

    def _model(self, built, build, settings):
        key = _frozen(settings)
        if key not in built:
            model = built[key] = build(settings)
            try:
                model.gpu = self.card
            except AttributeError:
                pass
        return built[key]

    def _unload_every_model(self):
        """Unload every model, keeping each built: the turn on the graphics card is being
        given up (tagpup.ml.gpu.Card.on_release), and the next use loads it again -- once
        this process has a turn again."""
        with self._lock:
            models = list(self._clips.values()) + list(self._face_models.values())
        for given in (self._clip, self._faces):
            if given is not None:
                models.append(given)
        _unload(models)

    def gpu_turn(self, what, models, cancelled=None, report=None):
        """A turn on the graphics card for `what` ("indexing Regatta (harbour)"), for as long
        as `models` are used: a Hold to release, waited for in order behind any other
        process (tagpup.ml.gpu.Card.hold, with `cancelled` and `report`); nothing to wait
        for when none of them runs on the card. What the CLI's index takes before its first
        photo."""
        if not any(gpu.on_the_card(getattr(model, "device", None)) for model in models if model is not None):
            return gpu.NoHold()
        return self.card.hold(what, cancelled=cancelled, report=report)

    def _note(self, library, settings):
        """Record which models `library` uses now. True when that changed from before."""
        if library is None:
            return False
        uses = (_part(settings, "embedder"), _part(settings, "faces"))
        before = self._uses.get(library.key)
        self._uses[library.key] = uses
        return before is not None and before != uses

    def _release(self):
        """Drop each model no library uses and no run holds; returns them, to unload
        outside the lock. Under the lock."""
        dropped = []
        for kind, built, position in (("clip", self._clips, 0), ("faces", self._face_models, 1)):
            used = {uses[position] for uses in self._uses.values()}
            for key in list(built):
                if key not in used and not self._held[(kind, key)]:
                    dropped.append(built.pop(key))
        if dropped:
            logger.info("Letting go of %d model(s) no library uses any more.", len(dropped))
        return dropped

    def release_idle(self):
        """Let go of what has not been used for `idle_after` seconds and nothing holds (the
        `idle` registry): the always-on process otherwise keeps a few GB of GPU memory and
        each opened library's vectors all day for a Suggest that may not come. The names
        let go."""
        released = self.idle.release_idle()
        if released:
            logger.info("Let go of %s, unused for %d minutes; the next use makes them again.",
                        ", ".join(released), round(self.idle_after / 60))
        return released

    def models_state(self):
        """What the process has loaded: {"loaded": ["CLIP", "faces"] (those in memory), "in_use":
        a run holds one, "last_used": seconds since a model was last asked for, or None
        when none has been since they were let go, "release_after": the idle period in
        seconds, or None}. What the Activity page shows."""
        with self._lock:
            clips = list(self._clips.values()) + ([self._clip] if self._clip is not None else [])
            faces = list(self._face_models.values()) + ([self._faces] if self._faces is not None else [])
            in_use = bool(self._held)

        def loaded(models):
            return any(callable(getattr(m, "loaded", None)) and m.loaded() for m in models)
        names = [name for name, models in (("CLIP", clips), ("faces", faces)) if loaded(models)]
        return {"loaded": names, "in_use": in_use, "last_used": self.idle.idle_for(MODELS) if names else None,
                "release_after": self.idle_after}

    def unload_models_now(self):
        """The owner's Unload models now: every model let go and the turn on the graphics
        card given up at once -- unless a Suggest run holds one, when nothing is touched.
        Returns {"unloaded": [names that were loaded], "busy": bool}."""
        before = self.models_state()
        if not self.idle.release_now(MODELS):
            return {"unloaded": [], "busy": True}
        return {"unloaded": before["loaded"], "busy": False}

    def _release_models(self):
        """Every model, when no run holds one (the registry asks in_use first). How many."""
        with self._lock:
            if self._held:
                return 0
            dropped = list(self._clips.values()) + list(self._face_models.values())
            self._clips.clear()
            self._face_models.clear()
        _unload(dropped)
        # Nothing left on the card to keep the turn for.
        self.card.release_if_idle()
        return len(dropped)

    def _release_indexes(self):
        """Each library's photo index no run holds: closed, its vectors let go; the next
        ask makes and loads it again. How many."""
        with self._lock:
            idle = [(key, index) for key, index in self._indexes.items() if not self._index_runs[id(index)]]
            for key, _index in idle:
                del self._indexes[key]
            for _key, index in idle:
                self._retire(index)
        return len(idle)

    def warm_up(self, libraries=()):
        """Load the CLIP model and the face models of one set of settings, so the first
        Suggest does not pay for them: those of `libraries` -- the startup library, or
        every library in the data folder -- that most of them share (the first, on a
        tie). One set, not one per distinct set: each is gigabytes on the GPU, and a set
        no one opens would sit there. It stamps no library: their settings are read as
        they are (peek_settings).

        The old server's warm-up made a PhotoIndex on the startup library and loaded it in
        the background, keeping it open until the process ended -- and a thread that
        started after the file had gone made an empty library in its place
        (docs/findings.md, #99). The models are the process's; a library is a request's.

        Only when no one has to wait for the graphics card (tagpup.ml.gpu.Card.try_hold):
        with an index or a Suggest holding it or waiting for it, the warm-up is skipped and
        the first Suggest loads the models (#750).
        """
        wanted = None
        if self._clip is not None or self._faces is not None:
            wanted = (None, None)
        else:
            counts, first = collections.Counter(), {}
            for library in libraries:
                try:
                    settings = peek_settings(library)
                    key = (_frozen(settings.embedder), _frozen(settings.faces))
                except Exception as e:
                    logger.error("Could not read the settings of %s to warm its models: %s", library.name, e)
                    continue
                counts[key] += 1
                first.setdefault(key, (library, settings))
            if counts:
                # most_common keeps the first seen of equal counts first.
                wanted = first[counts.most_common(1)[0][0]]
        if wanted is None:
            return
        library, settings = wanted
        try:
            models = (self.clip(library, settings), self.faces(library, settings))
        except Exception as e:
            logger.error("Could not make the models to warm them up: %s", e)
            return
        hold = gpu.NoHold()
        if any(gpu.on_the_card(getattr(model, "device", None)) for model in models):
            hold = self.card.try_hold("warming Suggest's models (TagPup server)")
            if hold is None:
                holder = gpu.read_holder(self.card.where)
                logger.info("Not warming the models: the graphics card is in use by %s, or a process waits "
                            "for it. The first Suggest loads them.",
                            (holder or {}).get("what") or "another TagPup program")
                return
        with hold:
            logger.info("Background thread starting CLIP model warmup...")
            try:
                clip = models[0]
                clip.load()
                clip.embed_text("warmup")
                logger.info("Background CLIP model warmup completed successfully.")
            except Exception as e:
                logger.error("Error warming up CLIP model: %s", e)

            try:
                logger.info("Background thread starting Face model warmup...")
                # Building the model loads nothing; its weights load on first use. So a
                # warm-up that only built it reported the face models warm while the first
                # Suggest still paid for loading them.
                models[1].load()
                logger.info("Background Face model warmup completed successfully.")
            except Exception as e:
                logger.error("Error warming up Face models: %s", e)

    def warm_up_in_background(self, libraries=()):
        """warm_up on a daemon thread; returns the thread."""
        thread = threading.Thread(target=self.warm_up, args=(list(libraries),), name="WarmupModelsThread",
                                  daemon=True)
        thread.start()
        return thread

    # ---- Each library's photo index ---------------------------------------------------

    def _index(self, library, model_key):
        """The library's photo index under `model_key`, made if it has none or its model
        has changed (the one replaced retired). Under the lock; not loaded here."""
        self.idle.used(PHOTO_INDEXES)
        index = self._indexes.get(library.key)
        if index is not None and index.model != model_key:
            del self._indexes[library.key]
            self._retire(index)
            index = None
        if index is None:
            index = self._indexes[library.key] = search.PhotoIndex(library.path, model_key)
        return index

    def _retire(self, index):
        """An index no longer the library's: closed now, or, while a run holds it, when
        the last such run ends. A run's connection was closed under it. Under the lock."""
        if self._index_runs[id(index)]:
            self._retired[id(index)] = index
        else:
            self._index_runs.pop(id(index), None)
            index.close()

    def photo_index(self, library, settings=None):
        """This library's photo index for Suggest, made once and brought up to date.

        The startup library's was made at startup and never reloaded, so photos indexed
        and tags saved since never reached Suggest. Every other library had none, and
        loaded its whole index again on every run. Each library keeps one, and it is
        loaded again only when the photos table has changed -- or made again when the
        library's CLIP model has, the old one closed once no run holds it.
        """
        model_key = self.model_key(library, settings)
        with self._lock:
            photo_index = self._index(library, model_key)
        photo_index.reload_if_changed()
        return photo_index

    def forget(self, library):
        """Drop a library: its photo index (closed, or when the last run holding it ends)
        and the models no other library uses."""
        with self._lock:
            self._uses.pop(library.key, None)
            index = self._indexes.pop(library.key, None)
            if index is not None:
                self._retire(index)
            dropped = self._release()
        _unload(dropped)

    # ---- What a suggestion run is given ----------------------------------------------

    def begin(self, library, remember=True, what=None, cancelled=None, report=None):
        """Ready the suggester for a run over `library`, on the run's thread: what
        tagpup.jobs.suggestions calls (tagpup.services.suggester.SuggestionModel). The
        library's settings are read once for the run, and what it is given -- its photo
        index, its models -- is held until it ends (RunModel.end): a model change
        meanwhile leaves the run on what it began with. Not `remember`: a run that only
        looks (Just look, a folder the library does not hold) reads the library and keeps
        nothing in it.

        The models it is given take the process's turn on the graphics card the first
        time the run uses one (_RunTurn), as `what` ("Suggest Regatta (harbour)"), and
        hold it until the run ends: `report(line)` hears the waiting line meanwhile, and
        None once the turn is had; `cancelled()` true stops the wait (gpu_turns.Cancelled)."""
        settings = self.settings(library)
        model_key = self.model_key(library, settings)
        with self._lock:
            photo_index = self._index(library, model_key)
            clip, faces = self.clip(library, settings), self.faces(library, settings)
            lease = _Lease(photo_index, _frozen(settings.embedder), _frozen(settings.faces))
            self._index_runs[id(photo_index)] += 1
            self._held[("clip", lease.clip_key)] += 1
            self._held[("faces", lease.faces_key)] += 1
        turn = _RunTurn(self.card, what or "Suggest in %s" % library.name, (clip, faces), cancelled, report)
        clip, faces = _OnTheCard(clip, turn), _OnTheCard(faces, turn)

        def end():
            turn.close()
            self._end(lease)
        try:
            photo_index.reload_if_changed()
            if remember:
                model = suggestions.model_for_run(photo_index, clip, faces, settings.candidate_words)
            else:
                model = suggestions.model_for_run(photo_index, clip, faces, settings.candidate_words,
                                                  remember=False)
        except BaseException:
            end()
            raise
        return RunModel(model, end)

    def _end(self, lease):
        """A run is done: what it held, and nothing else uses, is let go."""
        with self._lock:
            if lease.ended:
                return
            lease.ended = True
            self.idle.used(MODELS)
            self.idle.used(PHOTO_INDEXES)
            for kind, key in (("clip", lease.clip_key), ("faces", lease.faces_key)):
                self._held[(kind, key)] -= 1
                if self._held[(kind, key)] <= 0:
                    del self._held[(kind, key)]
            index_id = id(lease.index)
            self._index_runs[index_id] -= 1
            if self._index_runs[index_id] <= 0:
                del self._index_runs[index_id]
                retired = self._retired.pop(index_id, None)
                if retired is not None:
                    retired.close()
            dropped = self._release()
        _unload(dropped)

    def embeddings(self, library, photo_index):
        """The photos' vectors under the library's CLIP model, kept in `photo_index`."""
        return search.PhotoEmbeddings(self.clip(library), photo_index)
