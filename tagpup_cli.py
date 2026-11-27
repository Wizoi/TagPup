# tagpup_cli.py
import contextlib
import functools
from collections import Counter
import os
import sys
import time
import logging
import click
from rich.console import Console
from rich.table import Table

# Initialize Rich console and logging with colors for warnings and errors
console = Console()
class ColorFormatter(logging.Formatter):
    RED = "\033[91m"
    YELLOW = "\033[93m"
    RESET = "\033[0m"
    def format(self, record):
        orig_levelname = record.levelname
        if record.levelno >= logging.ERROR:
            record.levelname = f"{self.RED}{orig_levelname}{self.RESET}"
        elif record.levelno == logging.WARNING:
            record.levelname = f"{self.YELLOW}{orig_levelname}{self.RESET}"
        val = super().format(record)
        record.levelname = orig_levelname
        return val

from tagpup.core import progress_line  # noqa: E402

if progress_line.asked():
    # Started by the server, which reads this process's stdout as facts (tagpup.core.progress_line): the log goes to
    # the run's own file alone (TAGPUP_LOG_TO, below), a library's warnings with it.
    logging.basicConfig(level=logging.INFO, handlers=[])
    logging.captureWarnings(True)
else:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ColorFormatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s"))
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler]
    )
logger = logging.getLogger("tagpup_cli")

# Started for a run -- the index queue's indexer (tagpup.services.indexing) -- the CLI
# also logs to a file of that run's own in data/logs, <kind>-<library>-<run>.log, each line
# carrying the run's tags (tagpup.core.runs), and quietly: its stdout is its parent's
# progress bar. Run by hand, to the console alone.
if os.environ.get("TAGPUP_LOG_TO"):
    from tagpup import logs as _tagpup_logs
    from tagpup.core import runs as _tagpup_runs
    _tagpup_logs.to_file(_tagpup_logs.run_log(os.environ["TAGPUP_LOG_TO"], _tagpup_runs.current()), quiet=True)

# Suppress verbose faiss loader and huggingface logs
logging.getLogger("faiss.loader").setLevel(logging.WARNING)
logging.getLogger("faiss").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").propagate = False

import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="huggingface_hub")

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

# Load components
from tagpup.files.field_values import IdentityWriter
from tagpup.store.taxonomy import TagTaxonomy
from tagpup.core import gpu_turns
from tagpup.core import paths
from tagpup.core import read_ahead
from tagpup.core import runs as run_tags
from tagpup import logs as tagpup_logs
from tagpup.services.indexing import INDEXER_LOG
from tagpup.store import db as tagpup_db
from tagpup import config as tagpup_config
from tagpup import runtime as runtimes
from tagpup.runtime import Runtime
from tagpup.services import settings as library_settings
from tagpup.services import libraries as library_actions
from tagpup.services import faces as face_records
from tagpup.services import bake_reading
from tagpup.services import bake_after_index
from tagpup.services import tags_from_faces as tags_from_faces_service
from tagpup.services import identities
from tagpup.services import indexing as indexing_service
from tagpup.services import job_runs
from tagpup.services import thumbnails
from tagpup.services import photos as photo_actions
from tagpup.services import photo_reembed
from tagpup.services import retire_photo_vectors
from tagpup import launcher
from tagpup.services import journal as library_journal
from tagpup.services import snapshots as library_snapshots
from tagpup.services import roots as library_roots
from tagpup.core.result import SHARE_AWAY, NotFound, NotHeld, Refused
from tagpup.services import maintenance
from tagpup.services import metadata_check, segment_census
from tagpup.services import refresh_rows
from tagpup.services import file_changes
from tagpup.services import search as search_service
from tagpup.services.search import PhotoEmbeddings, PhotoIndex
from tagpup.store.locks import PathLocker
from tagpup.store import taxonomy as store_taxonomy
from tagpup.files import images as image_files
from tagpup.core import library as libraries
from tagpup.core.library import Library

def get_runtime():
    """The models a command runs (tagpup.runtime), from the settings of the library it is
    given: each is built the first time the command asks for it, and loaded the first
    time it is used."""
    return Runtime()

def library_index(runtime, db_path):
    """The library's photos, with their vectors under its CLIP model."""
    return PhotoIndex(db_path=db_path, model=runtime.model_key(Library(db_path)))


#: The index's read-ahead (tagpup.core.read_ahead; docs/AI_PIPELINE.md, phase 1b): the photos after the one the
#: graphics card is on are decoded and made ready for the models by this many threads, this many photos ahead, the
#: decoded pictures of them all and of the photo in hand within this many bytes (a larger photo is read alone).
READ_THREADS = 2
READ_AHEAD = 3
READ_AHEAD_BYTES = 1 << 30

#: How many photos the index records in one transaction (PhotoIndex.build_or_update), their faces in the next:
#: what a stop loses at most.
PHOTO_CHUNK = 100


def read_ahead_bytes(width, height, clip=None, faces=None):
    """What a photo of `width` x `height` is reserved while it is read ahead: the larger of what each half it goes
    through holds at once -- CLIP's (`clip`, when its vector is to be made: ClipModel.input_bytes), whose pictures
    are let go before the faces' are made, and the faces' (`faces`: its picture_bytes), held until the card
    has detected them. Each half says its own; this only takes the larger."""
    halves = [faces.picture_bytes(width, height)] if faces is not None else []
    if clip is not None:
        halves.append(clip.input_bytes(width, height))
    return max(halves, default=0)


def _kept_with_no_model(metas, embeddings, unexamined):
    """What the index keeps of each of `metas` [records of field_values.Fields.whole] with no model, asked before any
    model is built or the graphics card's turn taken: (looked, complete). `looked` is {paths.key: {"vector", "stale",
    "decoded"}}: the vector kept by its stamp, or the Stale one (PhotoEmbeddings.kept), and of a stale one its decode,
    (stamp, fingerprint, zero tail, picture size) -- or what the decode raised -- which the index's read uses and never decodes again.
    `complete` is True when every photo is kept by its stamp or by its fingerprint (photo_fingerprints.same_picture: only
    its metadata changed): none is in `unexamined`, none holds a bake to read, none has no vector. Then the index builds
    no model, imports no torch and takes no turn: the photos are stamped again with what they are. Asked in order and
    stopped at the first photo that needs a model: its decode, and those read ahead of it, are made again by the index.

    `embeddings` is the library's (PhotoEmbeddings, its model None). The stale photos are decoded on the read-ahead's
    threads, within its bound."""
    looked = {}
    for meta in metas:
        key = paths.key(meta["path"])
        if key in unexamined or bake_reading.holds(bake_reading.parsed(meta)):
            return looked, False
        kept, stale = embeddings.kept(meta["path"])
        if kept is None and stale is None:
            return looked, False
        looked[key] = {"vector": kept, "stale": stale, "decoded": None}

    def read(meta, begun, reserve):
        width, height = image_files.picture_size(meta["path"])
        reserve(width * height * 3)
        stamp, upright = embeddings.opened(meta["path"])
        return (stamp, image_files.fingerprint(upright), upright.info.get(image_files.ZERO_TAIL_INFO, 0),
                (width, height))
    stale = [meta for meta in metas if looked[paths.key(meta["path"])]["stale"] is not None]
    with read_ahead.ReadAhead(stale, lambda meta: looked[paths.key(meta["path"])], read, threads=READ_THREADS,
                              ahead=READ_AHEAD, budget=READ_AHEAD_BYTES) as decodes:
        for meta, begun, result in decodes:
            try:
                decoded = result.result()
            except Exception as error:
                # The index's read raises it again, in the photo's turn: said as the index says it, and no model asked.
                begun["decoded"] = error
                continue
            if not begun["stale"].same(decoded[1]):
                return looked, False
            begun["decoded"] = decoded
    return looked, True


def get_exiftool_path(db_path, read_only=False) -> str:
    """The ExifTool the library names, else the machine's (tagpup.runtime.exiftool).
    `read_only` reads it without stamping the library (inspect)."""
    library = Library(db_path)
    return runtimes.exiftool(library, runtimes.peek_settings(library) if read_only else None)

def get_db_path(test_mode=False, cli_db=None):
    """The library the command works on: --db (a name in the data folder, or a path;
    a name's test_ twin in test mode), else TAGPUP_DB_PATH, which the indexing service
    sets for the indexer it runs. There is no default: a command that indexes, writes
    or resets a library says which one (docs/findings.md, #100).

    The tag tree is in the library. It had a JSON file of its own, named one way here
    and another in the servers (docs/findings.md, #13), and the name decided which
    library the tree was saved to: test mode's saved into photo_index.db (#61).
    """
    if cli_db:
        db_name = cli_db if cli_db.endswith(".db") else (cli_db + ".db")
        if os.path.isabs(db_name) or "/" in db_name.replace("\\", "/"):  # not a path: is --db a name or a location
            return db_name
        return os.path.join(tagpup_config.data_dir(), libraries.for_mode(db_name, test_mode))

    env_db = os.environ.get("TAGPUP_DB_PATH")
    if env_db:
        return env_db

    raise click.UsageError("Say which library: --db <name or path> (a file in the data folder, or a path)")

class _Commands(click.Group):
    """The CLI's commands, a folder removed from the library while one runs answered as a sentence: NotHeld (a row
    asked for in a folder the library no longer holds: removed in an app while an index of it ran) stops the run, and
    leaves it as click's "Error: <sentence>" on one line, exit 1 -- the server's queue still sees a failure -- not as a
    traceback whose last line is the sentence."""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except NotHeld as e:
            raise click.ClickException(str(e)) from None


@click.group(cls=_Commands)
@click.option("--db", type=str, help="The library to work on: a name in the data folder (e.g. 'my_photos') or a path. Required.")
@click.option("--test", is_flag=True, help="Use test database paths to avoid cluttering production index.")
@click.pass_context
def cli(ctx, db, test):
    """TagpupCLI: AI-powered local photo tagging command-line interface."""
    ctx.ensure_object(dict)
    ctx.obj["test"] = test
    ctx.obj["db"] = db
    if db or os.environ.get("TAGPUP_DB_PATH"):
        # A library made by a newer TagPup is not opened by this one, whatever the command: it would misread
        # it (tagpup.store.schema.NewerLibrary). A sentence and exit 1, not a traceback.
        newer_path = get_db_path(test, db)
        problem = library_actions.newer_problem(newer_path)
        if problem and ctx.invoked_subcommand not in library_actions.RECOVERY_COMMANDS:
            raise click.ClickException(problem)
        if problem:
            # The recovery commands (the journal's read, the snapshots) still work, as it is, with a note.
            click.echo(library_actions.newer_note(newer_path), err=True)
            ctx.with_resource(library_actions.reading_newer())

#: The exit code of a folder refused as typed: click's own for a usage error (_folders_now's BadParameter).
USAGE_EXIT = 2


def _resolve_directories(library, kwargs):
    """A folder typed in an old place's spelling is the first place's folder: spelled so before the command
    walks or records it. One that does not exist at the first place is refused, naming both places and the root,
    and nothing is done (findings #483, #485)."""
    typed = kwargs.get("directories")
    canonical = library_roots.canonicaliser(library) if typed else None
    if canonical is None:
        return
    resolved = []
    for directory in typed:
        found = canonical(directory)
        refusal = photo_actions.folder_refusal(found) if found != directory else None
        if refusal:
            # The share's sentence when it did not answer (a wait, not a folder gone); the old place's otherwise (#2145).
            said = (refusal.refused if refusal.refused_kind == SHARE_AWAY
                    else library_roots.old_place_sentence(library, directory, found))
            console.print(said, markup=False, soft_wrap=True)
            raise SystemExit(USAGE_EXIT)   # as _folders_now refuses the same folder: one refusal, one code (#2145)
        resolved.append(found)
    kwargs["directories"] = tuple(resolved)


def _holds_the_roots(command):
    """A command that runs for as long as an index does, run holding one map: every path it writes is
    spelled by the roots and the places it started with, however the machine's map is edited
    meanwhile (tagpup.services.roots.pinned), and the library's roots changed by another process
    stops it, with a sentence and the exit code the server's queue turns into one. A `--reset`
    deletes the library, and its roots with it: nothing is held then."""
    @functools.wraps(command)
    def run(ctx, *args, **kwargs):
        library = Library(get_db_path(ctx.obj.get("test", False), ctx.obj.get("db")))
        try:
            with contextlib.ExitStack() as held:
                if not kwargs.get("reset"):
                    held.enter_context(library_roots.pinned(library))
                    _resolve_directories(library, kwargs)
                return command(ctx, *args, **kwargs)
        except library_roots.RootsChanged:
            console.print(library_roots.STOPPED, markup=False, soft_wrap=True)
            raise SystemExit(library_roots.EXIT_ROOTS_CHANGED) from None
        except library_roots.Unplaced as why:
            console.print("Not run: %s" % why, markup=False, soft_wrap=True)
            raise SystemExit(1) from None
    run.holds_the_roots = True
    return run


#: What a run of `index` typed by hand that stopped before its end says, as its row's failure (job_runs.run's `stopped`).
INDEX_STOPPED = "the index stopped before it finished; the console said why"


def _recorded_as_an_index(command):
    """Claimed once `_holds_the_roots` has let the command begin: a run refused before it starts leaves no row.
    A run of `index` typed by hand is a run of the job `index` in the library's job_runs, as one the server's queue
    starts is (that one claimed by the queue: its index tag in this process's environment, runs.process_tags). Claimed
    before the command records its folders added, so a removal of a folder in an app meanwhile is refused while it runs
    (tagpup.services.libraries.removal_refusal), and ended as the command ends; the Activity page lists it. Not
    recorded (another writer for longer than the claim tries, a library behind), it runs all the same. A `--reset`
    deletes the library, its runs with it: nothing is recorded."""
    @functools.wraps(command)
    def run(ctx, *args, **kwargs):
        library = Library(get_db_path(ctx.obj.get("test", False), ctx.obj.get("db")))
        if (kwargs.get("reset") or not os.path.exists(library.path)
                or any(tag.startswith("index:") for tag in run_tags.process_tags())):
            return command(ctx, *args, **kwargs)
        tag = run_tags.index_tag(library.name)
        # Its lines in the file the Activity page's "Logs for this run" is sent to (tagpup.logs.run_log), as a queued run's.
        own = tagpup_logs.to_file(tagpup_logs.run_log(INDEXER_LOG, tag), quiet=True)
        with run_tags.running(tag), job_runs.run(library, job_runs.INDEX, library.name, stopped=INDEX_STOPPED,
                                                 changed={"run": tag, "folders": len(kwargs.get("directories") or ())}):
            try:
                return command(ctx, *args, **kwargs)
            except SystemExit as stop:
                if stop.code:
                    raise
            finally:
                logging.getLogger().handlers[:] = [each for each in logging.getLogger().handlers
                                                   if getattr(each, "baseFilename", None) != own]
    return run


def _needs_ffmpeg(when=lambda kwargs: True):
    """A command that indexes, or makes posters, refused before it opens anything when ffmpeg is not found or does not
    run (tagpup.config.ffmpeg_problem; owner, 2026-11-06: a prerequisite, checked early): the sentence says what to
    install, exit 1, nothing written. `when` says from the command's options whether this run needs it (a sync's
    dry run does not)."""
    def wrap(command):
        @functools.wraps(command)
        def run(ctx, *args, **kwargs):
            if when(kwargs):
                problem = tagpup_config.ffmpeg_problem()
                if problem:
                    console.print("Not run: %s" % problem, markup=False, soft_wrap=True)
                    raise SystemExit(1)
            return command(ctx, *args, **kwargs)
        return run
    return wrap


#: The line an index ends with, counts only: what became of each photo's vector (photo_fingerprints.same_picture), the
#: four adding up to the photos the run indexed; and the photos never examined for faces whose detection ran (one that
#: failed, NotDetected, records nothing and is owed still).
KEPT_LINE = ("Photo vectors: %d kept (the file as it was), %d kept with the picture unchanged (only the file's metadata "
             "changed: stamped again, no model run), %d read from a bake, %d worked out; %d photo(s) never examined for "
             "faces looked at.")


#: Where --folders-file leaves the folders it read, for DIRECTORIES' check to take them with the typed ones.
_LISTED = "tagpup.folders_file"


def _folders_in_file(ctx, param, path):
    """--folders-file: folders written in a file, UTF-8, one a line, as the server hands them to the indexer
    (indexing.FOLDERS_FILE): a sync's few hundred on the command line passed Windows's 32,767 characters (#2149). Read
    before DIRECTORIES is checked (eager); a file that cannot be read is a sentence and exit 2, and nothing is run. Each
    line is a folder as written, to its last character: only the line's end is taken off."""
    if path is None:
        return None
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            lines = handle.read().split("\n")
    except (OSError, UnicodeDecodeError) as why:
        raise click.BadParameter("The list of folders could not be read: %s." % (getattr(why, "strerror", None) or why),
                                 ctx=ctx, param=param) from None
    ctx.meta[_LISTED] = tuple(line[:-1] if line.endswith("\r") else line for line in lines if line not in ("", "\r"))
    return path


def _folders_now(ctx, param, directories):
    """Each DIRECTORY, and each folder of --folders-file, refused unless it is a folder now, with the one sentence of why
    (photos.folder_refusal): a folder on a network share that did not answer says the share is not answering, within a
    moment, not after Windows's wait and not as a folder that does not exist (#2129). Neither given is refused."""
    directories = tuple(directories) + ctx.meta.pop(_LISTED, ())
    if not directories:
        raise click.MissingParameter(ctx=ctx, param=param)
    for directory in directories:
        refusal = photo_actions.folder_refusal(directory)
        if refusal:
            raise click.BadParameter(refusal.refused, ctx=ctx, param=param)
    return directories


@cli.command()
@click.argument("directories", nargs=-1, type=str, callback=_folders_now)
@click.option("--folders-file", type=str, is_eager=True, expose_value=False, callback=_folders_in_file,
              help="A file of folders to index as well, UTF-8, one a line (how the server hands over a sync's folders).")
@click.option("--force-reembed", is_flag=True, help="Force recreation of embeddings.")
@click.option("--reset", is_flag=True, help="Delete existing index and taxonomy to start fresh.")
@click.option("--skip-faces", is_flag=True, help="Skip face detection during indexing.")
@click.option("--no-subfolders", is_flag=True,
              help="Only the photos directly in each DIRECTORY, not in its subfolders.")
@click.pass_context
@_needs_ffmpeg()
@_holds_the_roots
@_recorded_as_an_index
def index(ctx, directories, force_reembed: bool, reset: bool, skip_faces: bool, no_subfolders: bool = False):
    """Phase 1: Scan and index a tagged photo library: one or more DIRECTORIES, in one run
    (sync hands it every folder of new files at once, so one indexer loads the models; the server, in --folders-file)."""
    del _NOT_SAYING[:]
    if progress_line.asked():
        sys.stdout, sys.stderr = _Pipe(sys.stdout), _Pipe(sys.stderr)
    runtime = get_runtime()

    test_mode = ctx.obj.get("test", False)
    cli_db = ctx.obj.get("db")
    db_path = get_db_path(test_mode, cli_db)
    # A reset deletes the library, and its settings with it; the new one is stamped
    # with them again, not with the defaults. Read without stamping the one deleted.
    kept_settings = runtimes.peek_settings(Library(db_path)).values if reset and os.path.exists(db_path) else None

    # Handle reset flag
    if reset:
        console.print("[bold red]Resetting index (deleting the library, its tag tree with it)...[/bold red]")
        # The database holds every face named by hand, and this deletes it.
        if os.path.exists(db_path):
            console.print(f"  Backed up to {tagpup_db.backup(db_path, 'index-reset')}")
            try:
                os.remove(db_path)
                console.print(f"  Removed {db_path}")
                # Its photos start again from id 1: no thumbnail of the old library is theirs.
                thumbnails.clear(Library(db_path))
            except Exception as e:
                console.print(f"[bold red]Failed to delete {db_path}: {e}[/bold red]")
        if kept_settings is not None and not os.path.exists(db_path):
            library_settings.stamp(Library(db_path), kept_settings, operation=library_settings.FROM_REPLACED)

    settings = runtime.settings(Library(db_path))
    exiftool_path = runtimes.exiftool(Library(db_path), settings)

    # Setup / Load components
    # Connected, its vectors not read: the index needs their stamps alone.
    photo_index = library_index(runtime, db_path)
    photo_index.open()

    taxonomy = TagTaxonomy(db_path)
    taxonomy.load()

    # Each photo with the stamp its folder's listing gave (images.stamps_under / stamps_in), by paths.key.
    # A photo in a folder the library ignores (its Library settings) is not its (tagpup.store.folders.holds), and is
    # left out as sync and Suggest leave it: a folder removed while a folder above it stays added is ignored, and an
    # index of the folder above brought it back (record_indexed now refuses it, and would stop the whole run).
    on_disk, ignored, left_out = {}, settings.ignored, 0
    in_ignored = library_actions.ignored_photos(ignored)
    for directory in directories:
        console.print(f"[bold cyan]Scanning directory:[/bold cyan] {directory}")
        for key, stamp in (image_files.stamps_in(directory) if no_subfolders
                           else image_files.stamps_under(directory)).items():
            if ignored and in_ignored(stamp[0]):
                left_out += 1
                continue
            on_disk.setdefault(key, stamp)
    all_images = [stamp[0] for stamp in on_disk.values()]
    console.print(f"Found {len(all_images)} image(s) total.")
    if left_out:
        console.print(f"Left out {left_out} image(s) in folders this library ignores (its Library settings).")

    if not all_images:
        console.print("[yellow]No supported images found. Exiting.[/yellow]")
        return

    # Indexing a folder is adding it (tagpup.services.libraries.record_added): the vectors
    # kept as each photo is embedded, before its row is recorded, make its row, which
    # tagpup.store.photos.ensure_row does only in a folder the library holds or was given.
    # As the index stores it: a folder typed as "." is no full path, and was not added.
    library_actions.record_added(Library(db_path), [paths.stored(d) for d in directories],
                                 subfolders=not no_subfolders)

    # Check for unchanged files using modification time and size
    # By paths.key: a folder indexed under one spelling and scanned under another is
    # the same photos, and keyed by the raw string every one of them was re-embedded
    # and given a second row.
    # The stamps alone: no photo's JSON is read to learn whether its file changed.
    # A photo never examined for faces by the library's face model (no face row, no record of a look) is the index's work
    # even when its vector is current: its faces are detected on the picture as shown, its vector kept. Asked by the
    # model's name, no model made (a run with nothing to do imports none, #1089). Not with --skip-faces; --force-reembed
    # works every photo again.
    # A photo owed only its fingerprint (its vector current by its stamp, its picture never fingerprinted) is not indexed
    # again: its folder goes to the bake after the index (`bake_only`), which decodes it on the processor and writes its
    # file from the library, the fingerprint row following the write (photo_fingerprints.NO_FINGERPRINT).
    bake_only = {}
    if force_reembed:
        images_to_process, unexamined = all_images, set()
    else:
        # The work the index owes (photo_fingerprints.owed, sync's answer too): no vector, a stale stamp, never examined,
        # no fingerprint.
        owed = photo_index.owed(on_disk, None if skip_faces else
                                face_records.detector_named(settings.faces))
        unexamined = {key for key, reasons in owed.items() if PhotoIndex.UNEXAMINED in reasons}
        for key, reasons in owed.items():
            if reasons == (PhotoIndex.NO_FINGERPRINT,):
                bake_only.setdefault(paths.key(os.path.dirname(on_disk[key][0])), os.path.dirname(on_disk[key][0]))
        current = photo_index.current(on_disk, {key: reasons for key, reasons in owed.items()
                                                if reasons != (PhotoIndex.NO_FINGERPRINT,)})
        images_to_process = [path for path in all_images if paths.key(path) not in current]
        if bake_only and not skip_faces:
            unbaked = sum(1 for reasons in owed.values() if reasons == (PhotoIndex.NO_FINGERPRINT,))
            console.print(BAKE_ONLY_LINE % (unbaked, len(bake_only)), markup=False, soft_wrap=True, highlight=False)
    skipped_count = len(all_images) - len(images_to_process)

    if skipped_count > 0:
        console.print(f"[green]Skipped {skipped_count} unchanged image(s) already present in the index.[/green]")

    if not images_to_process:
        console.print("[bold green]All images are up to date! Index is current.[/bold green]")
        # Print taxonomy stats and return
        roots = taxonomy.get_root_categories()
        if roots:
            console.print("\n[bold]Root categories in library:[/bold]")
            for root, count in sorted(roots.items(), key=lambda x: -x[1]):
                console.print(f"  • {root}: {count} path(s)")
        photo_index.close()
        if bake_only and not skip_faces:
            _bake_after_index(Library(db_path), settings, face_records.detector_named(settings.faces),
                              list(bake_only.values()), {}, os.environ.get(indexing_service.STOP_FILE))
        return

    # The vectors kept, under the library's model's key (tagpup.store.embeddings.model_key: another model's are never
    # compared), read with no model: a model is built below only when a photo needs one.
    embeddings = PhotoEmbeddings(None, photo_index, model_key=runtime.model_key(Library(db_path), settings))

    # Extract metadata in batches of 500
    console.print(f"[bold cyan]Reading metadata in batches for {len(images_to_process)} image(s)...[/bold cyan]")
    # Read only: a photo is given its identity below, once its picture has decoded. One ExifTool for every batch
    # (runtime.fields): a batch a share stopped answering in leaves the rest of that batch unread, and the next batch
    # answers again.
    batch_size = 500
    all_metadata = []

    from tqdm import tqdm
    # The bar for a person; the server is told the photos read, a batch at a time (tagpup.core.progress_line).
    _say(indexing_service.phase_fact("metadata", 0, len(images_to_process)))
    with runtimes.fields(Library(db_path), settings) as reader:
        for i in tqdm(range(0, len(images_to_process), batch_size), desc=indexing_service.READING_METADATA,
                      disable=progress_line.asked()):
            batch = images_to_process[i:i+batch_size]
            # The bakes in the files are read in the same command (bake_reading).
            batch_meta = reader.whole(batch, people=store_taxonomy.people_vocabulary(db_path), bakes=True)
            all_metadata.extend(batch_meta)
            _say(indexing_service.phase_fact("metadata", min(i + batch_size, len(images_to_process)),
                                             len(images_to_process)))

    # Every scanned photo is indexed, tagged or not.
    #
    # Indexing used to require an existing tag, person or caption, which made the index
    # a record of what had already been organised rather than of the library. An
    # untagged photo has exactly the things the rest of the system wants: a visual
    # embedding that makes it findable by search and usable as a neighbour, and faces
    # that TagTuner can group and identify. Requiring it to be tagged first inverted
    # the order of work -- you had to label a photo before the tools that help you
    # label it would look at it.
    #
    # Untagged photos contribute no tags when they turn up as a suggestion neighbour,
    # and the scoring denominator counts only neighbours that do contribute, so they
    # cannot dilute a suggestion.
    # A photo ExifTool did not answer for (a share gone away) is left as it was, unread: its record has no stamp
    # (field_values.Fields.whole), and the next index reads it again.
    left_unread = Counter(m["unread"] for m in all_metadata if m["mtime"] is None)
    to_index_meta = [m for m in all_metadata if m["mtime"] is not None]
    for why, count in sorted(left_unread.items()):
        console.print(f"[bold yellow]{count} photo(s) were not read: {why}.[/bold yellow] They are left as they were; "
                      f"the next index reads them.")
    # A clip (kind 'video') is shown CLIP its frames after the photos, in the same turn of the graphics card (_index_clips):
    # no face looked for, no identity and no bake written to its file.
    clips = [m for m in to_index_meta if m.get("kind") == "video"]
    to_index_meta = [m for m in to_index_meta if m.get("kind") != "video"]
    tagged_count = sum(1 for m in to_index_meta if m["tags"] or m["people"] or m["captions"])

    console.print(
        f"Indexing [bold green]{len(to_index_meta)}[/bold green] image(s) "
        f"([bold]{tagged_count}[/bold] already tagged, "
        f"[bold]{len(to_index_meta) - tagged_count}[/bold] untagged)."
    )
    if clips:
        console.print(f"Indexing [bold green]{len(clips)}[/bold green] video(s) from their frames.")

    # If there is nothing at all to index
    if not to_index_meta and not clips:
        if skipped_count > 0:
            console.print("[green]No new images found. Index remains current.[/green]")
        else:
            console.print("[yellow]No photos found to index.[/yellow]")
        photo_index.close()
        if bake_only and not skip_faces:
            _bake_after_index(Library(db_path), settings, face_records.detector_named(settings.faces),
                              list(bake_only.values()), {}, os.environ.get(indexing_service.STOP_FILE))
        return

    # Whether any photo needs a model, decided before one is built: a run where every photo is kept, by its stamp or by
    # its fingerprint (another program wrote its metadata: a watcher's sync queues its folder), stamps them again with no
    # model -- CLIP's library and the face model's (open_clip, torch) take seconds to import (#1089) -- and takes no turn
    # on the graphics card, so it never waits behind Suggest or another index for it. Each stale photo's decode is kept
    # for the read below, which does not decode it again.
    looked, complete = ({}, False) if force_reembed else _kept_with_no_model(
        to_index_meta, embeddings, set() if skip_faces else unexamined)
    # A run with a clip builds the model: a clip is owed by its stamp alone (no decode to fingerprint), so one listed is
    # one to make, and _index_clips asks again, with its lock held, whether another index made it meanwhile.
    complete = complete and not clips
    early = {key: answer["decoded"] for key, answer in looked.items() if answer["decoded"] is not None}
    face_processor = None
    if not complete:
        embeddings = runtime.embeddings(Library(db_path), photo_index)
        face_processor = runtime.faces(Library(db_path), settings) if to_index_meta and not skip_faces else None

    # Per-photo write locks, shared with every other indexer of the libraries in this
    # folder, wherever each was started from.
    locker = PathLocker(lock_dir=Library(db_path).locks)
    # The indexer records what it reads, so it may give a photo its identity -- after the
    # photo's picture has decoded in full, never before (IdentityWriter); a rename that knows the file is told what the
    # write left (file_changes.OutsideWrites, #1957).
    identity_writer = IdentityWriter(exiftool_path, around=file_changes.OutsideWrites(Library(db_path)).around)
    unreadable, incomplete = [], []

    # The index queue's Cancel, while this waits for the graphics card (tagpup.jobs.indexing).
    stop_file = os.environ.get(indexing_service.STOP_FILE)
    turn = photos = bar = None
    total_new_indexed = 0
    clip_counts, clips_unread = Counter(), []
    after = upto = 0
    # The folders a recorded batch held a photo of, in order, and the fingerprint of each recorded photo's picture the
    # index knows, with the stamp it was recorded under (_recorded): what the bake after the index writes, and the decodes
    # it takes again for none of the photos whose file is as the index recorded it.
    touched, handed = dict(bake_only), {}
    try:
        # One process at a time has a model on the graphics card (tagpup.ml.gpu): its turn is
        # taken before the first photo, waited for in order -- each change of who has it said
        # on stdout, the server's progress line -- and held to the end of the run. Stopped
        # while it waits, nothing has been indexed (docs/findings.md, #750).
        names = ", ".join(os.path.basename(d.rstrip("/\\")) or d for d in directories)
        waited = []

        def say_waiting(line):
            waited.append(line)
            _say(progress_line.fact("waiting", sentence=line), line)
        try:
            # Not taken when every photo is kept with no model (`complete`).
            turn = None if complete else runtime.gpu_turn(
                "indexing %s (%s)" % (names, Library(db_path).name), (embeddings.clip, face_processor),
                cancelled=lambda: bool(stop_file) and os.path.exists(stop_file), report=say_waiting)
        except gpu_turns.Cancelled:
            console.print(indexing_service.CANCELLED_WAITING, markup=False, soft_wrap=True)
            raise SystemExit(indexing_service.EXIT_CANCELLED) from None
        if waited:
            console.print("Took the graphics card.", markup=False, soft_wrap=True)
        # The faces this run makes are those above this id and up to the one read before the turn is let go: every writer
        # of faces holds the card's turn, which is the machine's (tagpup.ml.gpu), so no other run's face is between them --
        # while the models run on the card: on a machine whose models run on the processor there is no turn (gpu.NoHold), and
        # two runs of one library at once would count each other's faces among the new ones (nothing is written).
        after = face_records.newest_face(Library(db_path))

        # Generate Embeddings with incremental saving (batches of 100) to protect against halts/crashes
        console.print("[bold cyan]Generating embeddings...[/bold cyan]")
        batch_embeddings = []
        batch_metas = []
        batch_faces = {}  # Map path -> faces list
        given = set()  # The paths of the photos given their identity, read again before their batch is recorded
        total_new_indexed = 0
        locked_out = []
        failed_photos = []

        # A photo's bake gives what it holds only under the library's own keys (tagpup.services.bake_reading): the photo
        # model's, and the face model's, the one the runtime hands for the library's setting (Runtime.faces).
        photo_key = embeddings.model_key
        face_key = face_processor.key if face_processor else None
        tally = bake_reading.Tally(card=runtimes.on_the_card((embeddings.clip, face_processor)))
        # What became of each photo's vector, counted as its batch is recorded (a run killed before then recorded
        # nothing): kept by its stamp, kept by its fingerprint (the picture unchanged: stamped again, no model run),
        # worked out; and the photos never examined for faces that were looked at.
        vectors, batch_vectors = Counter(), Counter()
        vectors["stamp"] = skipped_count

        def begin(meta):
            """As a photo enters the read-ahead, in this thread: its lock, the vector the library keeps of it, and its
            bake. None when another live process has the photo: it is passed over, and said below. A skip here used to be
            silent, which is how 348 photos held by long-dead runs stayed out of the index for three months without
            anyone noticing."""
            path = meta["path"]
            if not locker.acquire(path):
                locked_out.append(path)
                return None
            # A vector whose stamp no longer describes the file is `stale`: kept if the decode's fingerprint is its
            # row's (photo_fingerprints.same_picture), else worked out. A run that built no model keeps what it read
            # before (_kept_with_no_model): read again, it could ask for a model this run does not have.
            if complete:
                kept, stale = looked[paths.key(path)]["vector"], looked[paths.key(path)]["stale"]
            else:
                kept, stale = (None, None) if force_reembed else embeddings.kept(path)
            # --force-reembed works every photo out again: no bake is read.
            found = bake_reading.parsed({} if force_reembed else meta)
            loads = kept is None and stale is None and not bake_reading.gives_vector(found, photo_key)
            if loads:
                # Loaded here, on the thread that has the graphics card: a read makes CLIP's input with the loaded
                # model's preprocessing. Not for a photo whose bake, or whose fingerprint, may give its vector.
                embeddings.clip.load()
            return {"vector": kept, "stale": stale, "examine": paths.key(path) in unexamined, "bake": found,
                    "clip": loads}

        def read(meta, begun, reserve):
            """On a thread of the read-ahead: the photo decoded and made ready for the models, its bake weighed against
            the picture's fingerprint, nothing written and nothing on the graphics card. A picture that does not decode
            raises images.Unreadable, handed to the loop below in the photo's turn. Every answer carries the picture's
            size from its header (images.picture_size), which its row records (photos.width, height)."""
            path = meta["path"]
            decoded = early.get(paths.key(path)) if begun["stale"] is not None else None
            if isinstance(decoded, Exception):
                raise decoded
            if (decoded is not None and tuple(decoded[0]) == tuple(begun["stale"].file_stamp)
                    and begun["stale"].same(decoded[1])):
                # Decoded as the run began (_kept_with_no_model), of the file as `begin` found it, its picture the
                # vector's: not decoded again. A file replaced since (the wait for the graphics card can be long behind
                # Suggest) has another stamp: decoded below, its old vector never recorded as the new picture's.
                picture = None
                if face_processor and begun["examine"]:
                    reserve(read_ahead_bytes(*decoded[3], None, face_processor))
                    picture = face_processor.picture(path)
                return None, decoded[2], picture, {"stamp": decoded[0], "upright": None, "fingerprint": decoded[1],
                                                   "took": bake_reading.Taken(), "same": True,
                                                   "picture_size": decoded[3]}
            width, height = image_files.picture_size(path)
            decodes = begun["vector"] is None or bake_reading.holds(begun["bake"])
            reserve(read_ahead_bytes(width, height, embeddings.clip if decodes else None, face_processor))
            stamp = upright = fingerprint = prepared = None
            if decodes:
                # Reads the file once and decodes the whole picture, upright; a vector kept from the file as it is was
                # made by such a decode, and a bake is the fingerprint's of one.
                stamp, upright = embeddings.opened(path)
                fingerprint = image_files.fingerprint(upright)
                zeros = upright.info.get(image_files.ZERO_TAIL_INFO, 0)
            else:
                zeros = image_files.zero_tail_of(path)
            # Faces are looked for when the vector is worked out, or the photo was never examined; a kept vector's are
            # its photo's already.
            looks = begun["vector"] is None or begun["examine"]
            if begun["stale"] is not None and begun["stale"].same(fingerprint):
                # The picture is the vector's: only the file's metadata changed. Nothing for CLIP, no bake read, and
                # no face picture but a never-examined photo's.
                picture = face_processor.picture(path) if face_processor and begun["examine"] else None
                return None, zeros, picture, {"stamp": stamp, "upright": None, "fingerprint": fingerprint,
                                              "took": bake_reading.Taken(), "same": True,
                                              "picture_size": (width, height)}
            picture = face_processor.picture(path) if face_processor and looks else None
            took = bake_reading.taken(begun["bake"], meta, fingerprint, picture, photo_key=photo_key, face_key=face_key)
            if begun["vector"] is None and took.vector is None and begun["clip"]:
                # Never loading CLIP: it was loaded in begin, on the thread that has the card, and a read still under
                # way when the index stops must not load it again after the turn is given back.
                prepared = embeddings.prepared_from(stamp, upright)
            if begun["vector"] is not None or took.vector is not None or prepared is not None:
                upright = None
            return prepared, zeros, picture, {"stamp": stamp, "upright": upright, "fingerprint": fingerprint,
                                              "took": took, "same": False, "picture_size": (width, height)}

        # The photos are read ahead and handed back in their order; the graphics card, the identity written into a
        # file and every write of the library stay on this thread, one photo after the other, as before.
        photos = read_ahead.ReadAhead(to_index_meta, begin, read, threads=READ_THREADS, ahead=READ_AHEAD,
                                      budget=READ_AHEAD_BYTES)
        # The bar is moved by hand: tqdm iterating `photos` kept the last photo it handed on, and its pictures, while
        # it waited for the next one -- outside what the read-ahead counts.
        # The photos and then the clips, one count.
        total = len(to_index_meta) + len(clips)
        bar = tqdm(total=total, desc=indexing_service.GENERATING_EMBEDDINGS, disable=progress_line.asked())
        # The server is told each time the whole percent moves (a fact a photo would be thousands of lines), and at
        # least every job_runs.PROGRESS_EVERY seconds: a percent of photo_index's 59,523 photos at under 2 a second is
        # over activity.STUCK_AFTER, and the run would read as stuck; a clip takes about 2 s.
        said = {"done": 0, "percent": 0, "at": time.monotonic()}

        def step():
            bar.update(1)
            said["done"] += 1
            percent = said["done"] * 100 // total
            if percent != said["percent"] or time.monotonic() - said["at"] >= job_runs.PROGRESS_EVERY:
                said.update(percent=percent, at=time.monotonic())
                _say(indexing_service.phase_fact("embeddings", said["done"], total))
        _say(indexing_service.phase_fact("embeddings", 0, total))
        for meta, begun, result in photos:
            step()
            if begun is None:
                continue
            path = meta["path"]
            try:
                try:
                    prepared, zeros, picture, decoded = result.result()
                except image_files.Unreadable as damaged:
                    # Nothing is written into it: no identity, no row.
                    unreadable.append((path, damaged))
                    logger.info(f"Not indexed, and not written to: {path} does not decode: {damaged}")
                    locker.release(path)
                    continue
                if begun["stale"] is not None and not decoded["same"]:
                    # Its picture changed (its decode's fingerprint is not its row's): its old faces are forgotten
                    # before its new vector is kept, with the faces or without (--skip-faces). A stop after the kept
                    # vector would leave them on it, never owed again; forgotten first, the photo is owed its faces
                    # (never examined) after any stop, and its batch records the new picture's. A stale vector with no
                    # fingerprint row is not known to be another picture, and keeps its faces.
                    try:
                        forgotten = face_records.forget_for_new_picture(db_path, path)
                    except Exception as failed:
                        # No vector is kept: its stale stamp stays owed, and the next index forgets them.
                        logger.error(f"Not indexed: the old faces of {path} were not forgotten: {failed}")
                        locker.release(path)
                        continue
                    logger.info(f"Its picture changed: {forgotten} old faces of {path} forgotten")
                took, on_card, kind, examined = decoded["took"], False, None, False
                if took.verdict == bake_reading.UNREADABLE:
                    logger.info(f"Its bake is not read, and it is worked out: {path}: {begun['bake'].reason}")
                if begun["vector"] is not None:
                    emb, kind = begun["vector"], "stamp"
                elif decoded["same"]:
                    # Kept, and stamped again with the file as this run read it, as its row is recorded (record_indexed).
                    emb, kind = begun["stale"].vector, "same"
                elif took.vector is not None:
                    # As a vector worked out is kept: stamped with the file as it was opened.
                    emb, kind = took.vector, "baked"
                    embeddings.keep(path, emb, decoded["stamp"])
                else:
                    if prepared is None:
                        # Its bake might have given the vector, and did not (the picture changed after it was baked):
                        # CLIP is loaded here, on the thread that has the card.
                        embeddings.clip.load()
                        prepared = embeddings.prepared_from(decoded["stamp"], decoded["upright"])
                    emb, on_card, kind = embeddings.made(path, prepared), True, "made"
                # The picture's size in its stored pixels, the frame its face boxes are in: recorded with its row
                # (record_indexed), so a rule of the background asks no file.
                meta["width"], meta["height"] = decoded["picture_size"]
                if decoded["fingerprint"] is not None:
                    meta["fingerprint"] = (image_files.FINGERPRINT_KEY, decoded["fingerprint"])
                else:
                    # Not decoded: its vector was kept by its stamp, and is recorded again with the fingerprint row it
                    # has (store.embeddings.put records or forgets one with every vector) -- stamped again, as its row
                    # is, after an identity written into the file.
                    meta["fingerprint"] = embeddings.fingerprint_of(path)
                if zeros >= image_files.ZERO_TAIL:
                    # It decodes, perhaps grey below a line: indexed, and left as it is.
                    incomplete.append((path, zeros))
                    logger.info(f"Possibly an incomplete copy, indexed and not written to: {path} ends in "
                                f"{zeros} zero bytes")
                else:
                    # Decoded: only now may the photo be written to. Its record keeps the stamp from before its
                    # read, and the batch reads it again before it is recorded (_read_again).
                    if identity_writer.give(meta, decoded=True):
                        given.add(path)

                # Extract and save face embeddings in the same pass (cached in memory until parent photo is saved): of a
                # photo whose vector was worked out, or one never examined; a kept vector's photo keeps its faces.
                if face_processor and (begun["examine"] or not (begun["vector"] is not None or decoded["same"])):
                    detected = took.detected
                    if detected is None:
                        detected = face_processor.detect_and_embed(picture)
                        on_card = on_card or face_records.ran(detected)
                    faces = face_processor.new_faces(detected, picture)
                    batch_faces[path] = faces
                    # Looked at only when the detection ran: one that failed records nothing (_record_detection).
                    examined = begun["examine"] and face_records.ran(detected)
                if not decoded["same"]:
                    tally.add(took.verdict, from_file=took.vector is not None or took.detected is not None,
                              on_card=on_card)

                # A photo already indexed is not removed first: build_or_update
                # updates its row in place. Deleting the row cascaded to its faces
                # and took every name given by hand, and every exclusion, with it.

                batch_embeddings.append(emb)
                batch_metas.append(meta)
                batch_vectors[kind] += 1
                batch_vectors["examined"] += examined

                # The nodes of the tags and people a photo names are made where the batch is recorded
                # (derived.make_nodes, taxonomy.ensure_node): the one writer of them.

                # Save progress incrementally in batches of PHOTO_CHUNK
                if len(batch_embeddings) >= PHOTO_CHUNK:
                    _read_again(photo_index.conn, batch_metas, given, Library(db_path), settings)
                    photo_index.build_or_update(batch_embeddings, batch_metas, dim=len(batch_embeddings[0]))
                    _recorded(batch_metas, touched, handed)

                    # Save faces for the batch in a single transaction
                    if batch_faces:
                        # --force-reembed means redo the work; without it the
                        # existing face rows, and the curation on them, are kept.
                        face_records.record_batch(photo_index.conn, batch_faces, model=face_processor.key,
                                                  overwrite=force_reembed,
                                                  detector=face_records.detector_of(face_processor))
                        batch_faces.clear()

                    # Release locks for saved images
                    for saved_meta in batch_metas:
                        locker.release(saved_meta["path"])

                    total_new_indexed += len(batch_embeddings)
                    vectors.update(batch_vectors)
                    batch_vectors.clear()
                    batch_embeddings = []
                    batch_metas = []
            except NotHeld:
                # The folder is no longer the library's (removed in an app while this ran): the batch made nothing, and
                # every photo after it would be refused too. The run stops, saying why.
                raise
            except Exception as e:
                logger.error(f"Error indexing {path}: {e}")
                failed_photos.append(path)
                locker.release(path)
            finally:
                # The decoded pictures go with the photo: what the read-ahead counts as held is given back when the
                # next photo is asked for, and nothing here keeps them past that.
                result = prepared = picture = decoded = None

        # Rebuild or update the FAISS index for the final batch
        if batch_embeddings:
            # Not reloaded: nothing below searches this index, and a reload reads every vector again.
            _read_again(photo_index.conn, batch_metas, given, Library(db_path), settings)
            photo_index.build_or_update(batch_embeddings, batch_metas, dim=len(batch_embeddings[0]))
            _recorded(batch_metas, touched, handed)

            # Save the remaining face embeddings
            if batch_faces:
                face_records.record_batch(photo_index.conn, batch_faces, model=face_processor.key, overwrite=force_reembed,
                                          detector=face_records.detector_of(face_processor))
                batch_faces.clear()
            
            for saved_meta in batch_metas:
                locker.release(saved_meta["path"])
            total_new_indexed += len(batch_embeddings)
            vectors.update(batch_vectors)
            batch_vectors.clear()
        photos.close()
        clip_counts, clips_unread = _index_clips(clips, embeddings, locker, photo_index, force_reembed, locked_out,
                                                 failed_photos, step)
        upto = face_records.newest_face(Library(db_path))

        if locked_out:
            console.print(
                f"[bold yellow]{len(locked_out)} photo(s) were skipped because another "
                f"process holds their lock.[/bold yellow] They are NOT in the index. "
                f"If no other indexer is running, these locks are stale -- rerun to take "
                f"them over."
            )
            for p in locked_out[:5]:
                console.print(f"  [yellow]-[/yellow] {p}")
            if len(locked_out) > 5:
                console.print(f"  [dim]... and {len(locked_out) - 5} more[/dim]")
        if getattr(locker, "stolen", 0):
            console.print(
                f"[cyan]Recovered {locker.stolen} lock(s) abandoned by a previous run.[/cyan]"
            )

        if failed_photos:
            _say(progress_line.fact("failed", photos=len(failed_photos)), indexing_service.FAILED + str(len(failed_photos)))
        if total_new_indexed > 0 or clip_counts["made"]:
            console.print("[bold green]Indexing successfully completed![/bold green]")
        else:
            console.print("[yellow]No new embeddings generated.[/yellow]")
        if tally.said():
            _say(progress_line.fact("bake_read", sentence=tally.said()), tally.said())
        console.print(KEPT_LINE % (vectors["stamp"], vectors["same"], vectors["baked"], vectors["made"],
                                   vectors["examined"]),
                      markup=False, soft_wrap=True, highlight=False)
        if clips:
            console.print(CLIPS_LINE % (clip_counts["stamp"], clip_counts["made"], clip_counts["later"],
                                        clip_counts["unread"]), markup=False, soft_wrap=True, highlight=False)
        if clips_unread:
            console.print(CLIPS_UNREAD % len(clips_unread), markup=False, soft_wrap=True, highlight=False)
            for p, problem in clips_unread[:5]:
                console.print(f"  - {p}: {problem}", markup=False, soft_wrap=True, highlight=False)
            if len(clips_unread) > 5:
                console.print(f"  ... and {len(clips_unread) - 5} more", markup=False)
        if unreadable:
            console.print(
                f"[bold yellow]{len(unreadable)} photo(s) could not be read: the file is damaged.[/bold yellow] "
                f"They are NOT in the index, and nothing was written to them. Restore them from a backup.")
            for p, damaged in unreadable[:5]:
                console.print(f"  [yellow]-[/yellow] {p}: {damaged}")
            if len(unreadable) > 5:
                console.print(f"  [dim]... and {len(unreadable) - 5} more[/dim]")
        if incomplete:
            console.print(
                f"[bold yellow]{len(incomplete)} photo(s) may be incomplete copies:[/bold yellow] each ends in "
                f"zero bytes, as an interrupted copy leaves a file. They are indexed, and nothing was written "
                f"to them. Compare them with a backup.")
            for p, zeros in incomplete[:5]:
                console.print(f"  [yellow]-[/yellow] {p}: the last {zeros:,} bytes are zeros")
            if len(incomplete) > 5:
                console.print(f"  [dim]... and {len(incomplete) - 5} more[/dim]")
    except (Exception, KeyboardInterrupt):
        _owe_the_recorded(db_path, touched, handed)
        raise
    finally:
        if bar is not None:
            bar.close()
        if photos is not None:
            photos.close()
        if turn is not None:
            turn.release()
        identity_writer.close()
        locker.release_all()
        photo_index.close()

    # Print taxonomy stats
    roots = taxonomy.get_root_categories()
    if roots:
        console.print("\n[bold]Root categories detected in library:[/bold]")
        for root, count in sorted(roots.items(), key=lambda x: -x[1]):
            console.print(f"  • {root}: {count} path(s)")

    # An index is followed by matching the faces it made (owner, DECISIONS 2026-10-23), here, so that every way of
    # indexing -- both apps' queue and sync's, which run this command, and this command by hand -- does it alike. Only
    # after a run that recorded a photo: one that recorded none made no face. The card's turn was let go above: the
    # matching is the processor's work, and writes nothing. Before the bake: a bake stopped by a Ctrl+C, or a slow one,
    # does not leave this run's faces unmatched.
    if total_new_indexed:
        try:
            _say(indexing_service.phase_fact("faces"), indexing_service.MATCHING)
            _match_new_faces(db_path, after, upto)
        except (Exception, KeyboardInterrupt):
            _owe_the_recorded(db_path, touched, handed)
            raise

    # The files of the folders it recorded are written from the library (docs/PLAN_KEEP_IN_STEP.md, cut 2), the card let
    # go above: every way of indexing does it, and the owner runs no bake. Not with --skip-faces: no face was looked for.
    if touched and not skip_faces:
        _bake_after_index(Library(db_path), settings, face_records.detector_named(settings.faces),
                          list(touched.values()), handed, stop_file)


#: How many clips the index records in one transaction (PhotoIndex.build_or_update): what a stop loses at most. Each
#: clip's vector is written with its frames, all or none (store.embeddings.put).
CLIP_CHUNK = 20

#: The line an index of clips ends with, counts only (_index_clips): what became of each clip's vector, the four adding
#: up to the clips it recorded.
CLIPS_LINE = ("Video vectors: %d kept (the file as it was), %d made from their frames, %d left for the next index (ffmpeg "
              "could not read the frames now, or the file changed as it was read), %d that ffmpeg cannot read.")

#: What the index says of the clips ffmpeg cannot read as they are (search.NoFrames.lasting), before it names a few.
CLIPS_UNREAD = ("%d video(s) could not be read by ffmpeg: they are in the index without a vector, so search and Suggest "
                "do not find them, and every index of their folder tries them again. Play one to see whether it is "
                "damaged.")


def _index_clips(clips, embeddings, locker, photo_index, force_reembed, locked_out, failed, step):
    """Each of `clips` [records of field_values.Fields.whole, kind 'video'] recorded with the vectors of its frames
    (PhotoEmbeddings.prepared, vectors): frame 0's is its vector, frames 1 to 4 go beside it, CLIP_CHUNK clips a
    transaction, so a stop leaves each clip with all of them or none, owed still. Run on the thread that has the
    graphics card, after the photos; ffmpeg reads the next clips' frames on the read-ahead's threads, never loading the
    model. A clip another live process holds joins `locked_out`, unrecorded. One whose vector is kept by its stamp is
    recorded with the vector it holds. One whose file is not as its record was read (its stamp as ffmpeg ran is not the
    record's) is recorded with no vector, and is owed still. One ffmpeg gives no frame of is recorded with no vector:
    the file's as it is (NoFrames.lasting) is listed, and every index of its folder tries it again (its vector owed
    still); the moment's (a share away, too slow) is left for the next run. One that fails otherwise joins `failed`,
    unrecorded. `step()` after each clip, the run's progress. Returns (Counter of "stamp", "made", "later" and
    "unread": the clips recorded, by what became of their vector; [(path, problem)] of the clips ffmpeg cannot read)."""
    counts, waiting, unread, vectors, metas = Counter(), Counter(), [], [], []

    def record():
        if metas:
            photo_index.build_or_update(vectors, metas)
            for meta in metas:
                locker.release(meta["path"])
            counts.update(waiting)
            waiting.clear()
            del vectors[:], metas[:]

    def begin(meta):
        if not locker.acquire(meta["path"]):
            locked_out.append(meta["path"])
            return None
        # Asked again with the lock held: another index of the folder may have made it while this one waited.
        kept = not force_reembed and embeddings.kept(meta["path"], compare=False)[0] is not None
        if not kept:
            embeddings.clip.load()   # here, on the thread that has the card: a read never loads it
        return {"kept": kept}

    def read(meta, begun, reserve):
        if begun["kept"]:
            return None
        reserve(embeddings.frames_bytes())
        return embeddings.prepared(meta["path"], load=False)

    def vector_of(meta, begun, result):
        """(vector or None, what became of it, the problem of a clip ffmpeg cannot read or None)."""
        try:
            prepared = result.result()
        except search_service.NoFrames as missed:
            logger.info(f"No vector: ffmpeg gave no frame of {meta['path']}: {missed.problem}")
            return (None, "unread", missed.problem) if missed.lasting else (None, "later", None)
        if begun["kept"]:
            return None, "stamp", None
        if not prepared.of_the_file(meta["mtime"], meta["size"]):
            logger.info(f"No vector: {meta['path']} changed after its metadata was read")
            return None, "later", None
        found = embeddings.vectors(prepared)
        meta["frames"], meta["fingerprint"] = found[1:], None
        return found[0], "made", None

    with read_ahead.ReadAhead(clips, begin, read, threads=READ_THREADS, ahead=READ_AHEAD,
                              budget=READ_AHEAD_BYTES) as ahead:
        for meta, begun, result in ahead:
            step()
            if begun is None:
                continue
            try:
                vector, why, problem = vector_of(meta, begun, result)
            except Exception as e:
                logger.error(f"Error indexing {meta['path']}: {e}")
                failed.append(meta["path"])
                locker.release(meta["path"])
                continue
            finally:
                result = None   # its frames go with it
            if problem:
                unread.append((meta["path"], problem))
            vectors.append(vector)
            metas.append(meta)
            waiting[why] += 1
            if len(metas) >= CLIP_CHUNK:
                record()
        record()
    return counts, unread


#: What the index says of the photos it owes only their files' bake (photo_fingerprints.NO_FINGERPRINT), counts only.
BAKE_ONLY_LINE = ("%d photo(s) in %d folder(s) hold a current vector whose picture was never fingerprinted: their files "
                  "are baked from the library after the index, on the processor.")


def _recorded(metas, touched, handed):
    """A batch of `metas` was recorded: its folders join `touched` ({paths.key: native folder}), and the fingerprint of
    each photo's picture the index holds joins `handed` ({paths.key(path): ((mtime, size), digest)}), with the stamp its
    row was recorded under: its decode's, or the row a vector kept by its stamp was recorded again with (the picture its
    stamp still describes). Another key's row is not. The bake uses a digest only while the file is at that stamp."""
    for meta in metas:
        folder = os.path.dirname(meta["path"])
        touched.setdefault(paths.key(folder), folder)
        found = meta.get("fingerprint")
        if found is not None and found[0] == image_files.FINGERPRINT_KEY:
            handed[paths.key(meta["path"])] = ((meta["mtime"], meta["size"]), found[1])


def _owe_the_recorded(db_path, touched, handed):
    """The index ended (an error, a Ctrl+C) before its bake took the rows of the photos it recorded away: take them away
    now, so those photos are owed their files' bake and the next index writes it. A killed process runs nothing, and
    leaves them in step with an unbaked file (0 instances; the bake itself covers every end from its start)."""
    try:
        bake_after_index.owe_first(Library(db_path), list(touched.values()), handed)
    except Exception:
        logger.exception("The fingerprints the index recorded could not be taken away")


#: The first line of this index that could not be said (_say, _Pipe), after which none is: emptied as each index begins,
#: so an index run in-process after another (a test's CliRunner) says its lines.
_NOT_SAYING = []


def _stop_saying(error):
    """Nothing more of this index is said: `error` is logged once (to the run's own file), and the work goes on."""
    if not _NOT_SAYING:
        _NOT_SAYING.append(error)
        logger.warning("The index's progress lines stop here, its work goes on: %s: %s", type(error).__name__, error)


class _Pipe:
    """The child's stdout, or its stderr (the same pipe: indexing.index_folder), while the server reads it: the one
    writer of everything the index says there -- a fact, a person's line, a library's print. The first write the pipe
    refuses (its reader gone: a repo-run server restarted on a .py save, #1564; Windows says OSError 22) silences the
    index (_stop_saying), every writer with it, and the index goes on to its end; the process exits as it would have."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, text):
        if not _NOT_SAYING:
            try:
                return self._stream.write(text)
            except (OSError, ValueError) as error:
                _stop_saying(error)
        return len(text)

    def flush(self):
        if not _NOT_SAYING:
            try:
                self._stream.flush()
            except (OSError, ValueError) as error:
                _stop_saying(error)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _say(fact, person=None):
    """Say `fact` (tagpup.core.progress_line) to the server that asked for facts; run by hand, say `person`, the words a
    person reads, or nothing when a bar or the log already tells them. Best effort: a line that cannot be said
    (_Pipe, and a console by hand that refuses it) silences the index; the work goes on."""
    if _NOT_SAYING:
        return
    try:
        if progress_line.asked():
            print(progress_line.say(fact), flush=True)
        elif person is not None:
            console.print(person, markup=False, soft_wrap=True, highlight=False)
    except (OSError, ValueError) as error:
        _stop_saying(error)


def _bake_after_index(library, settings, detector, folders, handed, stop_file):
    """The bake after an index (tagpup.services.bake_after_index), its line said in counts. One that fails is logged
    and said, and the index stands: the next index of the folder bakes it. A Ctrl+C keeps what was written. Each
    folder's line is a fact for the server; by hand the log alone shows it."""
    try:
        with runtimes.fields(library, settings) as fields:
            baked = bake_after_index.bake_folders(library, fields, detector, folders, handed,
                                                  stop=lambda: bool(stop_file) and os.path.exists(stop_file), each=_say)
    except Exception as error:
        logger.exception("The bake after the index stopped")
        line = (bake_after_index.SAID + "stopped (%s); the photos are indexed, and the next index of their folders "
                "writes it." % type(error).__name__)
        _say(progress_line.fact("baked", sentence=line), line)
        return
    _say(baked.fact(), baked.said())


def _read_again(conn, metas, given, library, settings):
    """The fields of each photo of `metas` that was `given` its identity, read from the file again (refresh_rows.
    read_files, stamped before its own read), so its row is recorded as the file is after the write: the next index
    finds it current and reads nothing, and its vector and faces are kept. The batch read was up to 500 photos
    earlier, and a keyword saved into the file since then is read here (docs/findings.md, #1599). A photo left unread
    now (no stamp: a share gone away mid-run, tests/test_a_share_asleep.py) keeps the record of the batch read, stamped
    before that read: the next index reads it once more. One ExifTool refuses is recorded as Fields.whole records it."""
    again = [meta["path"] for meta in metas if meta["path"] in given]
    if not again:
        return
    with runtimes.fields(library, settings) as fields:
        records = refresh_rows.read_files(conn, again, fields)
    for meta in metas:
        record = records.get(meta["path"])
        if record is not None and record["mtime"] is not None:
            meta.update(record)


def _match_new_faces(db_path, after, upto):
    """Say what Re-examine would name among the faces this index made, those whose ids are above `after` and at most
    `upto` (tagpup.services.faces.automatch_new), on a line of its own that the server's index queue ends its message with
    (tagpup.services.indexing.NEW_FACES). It writes nothing, so a failure is said on the same line and the run still
    exits 0: the photos are indexed, and their faces wait for Re-examine or cluster-faces. Its fact carries whether it
    offers Re-examine (tagpup.services.indexing.new_faces_fact, matching_failed_fact)."""
    try:
        made = indexing_service.new_faces_fact(face_records.automatch_new(Library(db_path), after, upto))
    except Exception as e:
        logger.exception("Matching the new faces after the index failed")
        made = indexing_service.matching_failed_fact(e)
    _say(made, indexing_service.NEW_FACES + made["sentence"])

@cli.command()
@click.option("--apply", "apply_", is_flag=True, help="Back the library up, then compact it. Without it, only says how much would be freed.")
@click.pass_context
def compact(ctx, apply_: bool):
    """Give back the space the library holds free: pages left empty by deleted rows and
    dropped columns, which the file keeps until it is rewritten. Close the apps first;
    the rewrite needs the file to itself."""
    db_path = get_db_path(ctx.obj.get("test", False), ctx.obj.get("db"))
    if not os.path.exists(db_path):
        raise click.ClickException("There is no library at %s." % db_path)
    size, free = tagpup_db.space(db_path)
    console.print(f"{os.path.basename(db_path)}: {size / 1e6:,.0f} MB, of which {free / 1e6:,.0f} MB is free.")
    if not apply_:
        console.print("Nothing changed. --apply backs the library up, then compacts it.")
        return
    copy = tagpup_db.backup(db_path, "compact")
    console.print(f"Backed up to [bold cyan]{copy}[/bold cyan].")
    try:
        before, after = tagpup_db.compact(db_path)
    except Exception as e:
        raise click.ClickException("Could not compact the library (is an app using it?): %s" % e) from e
    console.print(f"Compacted: {before / 1e6:,.0f} MB -> {after / 1e6:,.0f} MB.")


def _existing_library(ctx):
    """The Library the command names, which must be there."""
    db_path = get_db_path(ctx.obj.get("test", False), ctx.obj.get("db"))
    if not os.path.exists(db_path):
        raise click.ClickException("There is no library at %s." % db_path)
    library = Library(db_path)
    # A library holding a root this machine does not place is told of at once, and not by the
    # first photo that fails to open: its paths are refused until the map says where it is.
    unplaced = library_roots.problem(library)
    if unplaced:
        console.print("Warning: %s" % unplaced, markup=False, soft_wrap=True)
    return library


#: How `history` says what a change did to a row.
DONE = {"insert": "inserted", "update": "updated", "delete": "deleted"}


def _rows_line(rows):
    """{table: {action: n}} as "face_crops: 3 deleted; faces: 3 deleted"."""
    return "; ".join("%s: %s" % (table, ", ".join("%d %s" % (n, DONE[action]) for action, n in sorted(actions.items())))
                     for table, actions in sorted(rows.items())) or "no rows"


def _files_line(files):
    """{state: n} of a change's photo files as "photo files: 3 done, 1 conflict"."""
    if not files:
        return ""
    return "photo files: %s" % ", ".join("%d %s" % (n, state) for state, n in sorted(files.items()))


@cli.command()
@click.option("--change", "change_id", type=int, default=None, help="One change, with the keys of every row it wrote.")
@click.option("--limit", default=20, type=int, help="How many changes to list, newest first.")
@click.option("--reveal", is_flag=True, help="With --change: each column's value before and after. Values can name people.")
@click.pass_context
def history(ctx, change_id, limit, reveal):
    """The library's journal: the changes bulk operations applied, newest first, each
    undoable with `undo`."""
    library = _existing_library(ctx)
    try:
        found = library_journal.history(library, change_id, reveal, limit)
    except Exception as e:
        raise click.ClickException(str(e)) from e
    if not found["changes"]:
        console.print("The journal of %s is empty." % library.name)
        return
    for entry in found["changes"]:
        console.print("%d  %s  %s  made %s%s  (schema %d)  %s%s" % (
            entry["id"], entry["operation"], entry["status"], entry["created"],
            ", undone %s" % entry["undone"] if entry["undone"] else "", entry["schema_version"],
            _rows_line(entry["rows"]) if entry["rows"] or not entry.get("files") else "",
            _files_line(entry.get("files"))), markup=False, soft_wrap=True)
    for entry in found["changes"]:
        scope = entry["summary"].get("scope")
        if scope and change_id is not None:
            console.print("  scope: %s%s" % (scope, ": " + entry["summary"]["folder"] if entry["summary"].get("folder") else ""),
                          markup=False, soft_wrap=True)
    if change_id is not None:
        entry = found["changes"][0]
        for table, keys in sorted(entry.get("keys", {}).items()):
            console.print("  %s: %s" % (table, ", ".join("/".join(str(k) for k in key) for key in keys)),
                          markup=False)
        for row in entry.get("values", []):
            console.print("  %s %s %s: %s -> %s" % (row["action"], row["table"], "/".join(str(k) for k in row["key"]),
                                                   row["old"], row["new"]), markup=False)


@cli.command("metadata-check")
@click.option("--sample", default=metadata_check.SAMPLE, type=int, show_default=True,
              help="How many of the library's JPEGs to compare, drawn with --seed.")
@click.option("--seed", default=segment_census.SEED, type=int, show_default=True,
              help="The draw's seed: the census's, so both read the same photos.")
@click.option("--reveal", is_flag=True, help="List each difference's photo, operation and values. Paths and values can "
                                             "name people.")
@click.pass_context
def metadata_check_command(ctx, sample, seed, reveal):
    """Compare TagPup's own JPEG writer with ExifTool's on copies of a sample of the library's
    JPEGs: seven writes each (a keyword added, the last taken off, a person added, a caption set
    and cleared, Date Taken moved 90 minutes, a turn right), each written by both on fresh copies
    in a sandbox in the temporary folder, removed at the end. It never writes the library or its
    photos. Counts only unless --reveal. Exit status 1 when the writers differ, 2 when the run
    stopped early (Ctrl+C, the sandbox's disk full)."""
    library = _existing_library(ctx)
    try:
        found = metadata_check.check(library, get_exiftool_path(library.path, read_only=True), sample=sample,
                                     seed=seed, reveal=reveal, say=lambda line: console.print(line, markup=False))
    except Refused as e:
        raise click.ClickException(str(e)) from e
    for line in metadata_check.lines(found):
        console.print(line, markup=False, soft_wrap=True)
    if found["differences"]:
        raise SystemExit(1)
    if found["stopped"]:
        raise SystemExit(2)


def _say_rehearsal(result):
    rehearsal = result.details.get("rehearsal") or {}
    if result.refused:
        console.print("Refused: %s" % result.refused, markup=False, soft_wrap=True)
    elif rehearsal and result.details.get("files"):
        # A change of photo files: each read, none written.
        console.print("Rehearsed: %d photo file(s) would be put back%s" % (
            rehearsal["rows"], "." if rehearsal["exact"] else "; these would be refused, and left as they are: %s"
            % "; ".join(rehearsal["differences"])), markup=False, soft_wrap=True)
    elif rehearsal:
        for note in rehearsal.get("notes", []):
            console.print(note, markup=False, soft_wrap=True)
        exact = rehearsal["exact"] and rehearsal["derived_exact"]
        console.print("Rehearsed: %d row(s); %s" % (
            rehearsal["rows"], "every one came back exactly." if exact else
            "NOT every row came back exactly: %s" % "; ".join(rehearsal["differences"])), markup=False,
            soft_wrap=True)


@cli.command()
@click.argument("change_id", type=int)
@click.option("--apply", "apply_", is_flag=True, help="Write the undo. Without it, only rehearses it.")
@click.pass_context
def undo(ctx, change_id, apply_):
    """Undo change CHANGE_ID of the library's journal (`history` lists them): only where
    every row is still what the change left, and no newer change touched the same rows.
    A rehearsal unless --apply."""
    library = _existing_library(ctx)
    with runtimes.fields(library, runtimes.peek_settings(library)) as fields:
        result = library_journal.undo(library, change_id, apply=apply_, fields=fields)
    _say_rehearsal(result)
    if result.refused:
        raise SystemExit(1)
    if not apply_:
        console.print("Nothing changed. --apply undoes it.")
        return
    console.print("Undid change %d: %d %s written back." % (
        change_id, result.changed, "file(s)" if result.details.get("files") else "row(s)"))
    for what, error in result.errors:
        console.print("[yellow]%s: %s[/yellow]" % (what, error))




def _not_looked_at(counts):
    """What `sync` says of the folders it could not look at, or None when it looked at every one."""
    if not counts.get("folders_not_looked_at"):
        return None
    return ("%d folder(s) not looked at (share not answering or not listable): %d row(s) under them are neither missing nor moved;"
            " not in step until a sync looks at them." % (counts["folders_not_looked_at"], counts["not_looked_at"]))


#: What `sync` says of the work the index owes (photo_fingerprints.owed), counts only: a photo may owe two (a stale
#: vector, and never examined).
OWED_LINE = ("%d photo(s) in %d folder(s) the index owes work: %d with no photo vector, %d whose file changed since its "
             "vector was made (the index keeps a vector whose picture is the same), %d never examined for faces, %d whose "
             "file the index's bake writes (its picture never fingerprinted). Their folders go to the index.")
#: Said after OWED_LINE of the folders among them sync holds back.
OWED_HELD_BACK = (" %d of those folder(s) are held back: a new file in each may be a copy of a missing photo; settle that"
                  " first.")

#: What `sync` says of each thing it counts.
SYNC_FOUND = (("new", "new file(s), in %d folder(s)", "new_folders"), ("changed", "changed file(s)", None),
              ("moved", "moved file(s)", None), ("missing", "missing file(s), in %d folder(s)", "missing_folders"))


@cli.command()
@click.option("--folder", default=None, type=click.Path(file_okay=False),
              help="Only the photos under this folder. Without it, every folder the library holds photos in.")
@click.option("--apply", "apply_", is_flag=True,
              help="Write the rows, index the new files, and record the run. Without it, only says what it would do.")
@click.pass_context
@_needs_ffmpeg(when=lambda kwargs: kwargs["apply_"])
def sync(ctx, folder, apply_):
    """Bring the library in step with its folders: rows read again for files changed
    outside the apps, rows following files that moved, new files indexed, and the rows of
    files looked for and not found removed (with their faces, undoably) when every folder
    was looked at and their root is there; a sync of one --folder never removes. A dry run
    unless --apply; counts only."""
    library = _existing_library(ctx)
    runtime = get_runtime()
    result = runtime.sync(library, folder=folder, apply=apply_)
    if result.refused:
        console.print("Refused: %s" % result.refused, markup=False, soft_wrap=True)
        raise SystemExit(1)
    counts = result.details["counts"]
    console.print("%d row(s), %d photo file(s) on disk in %d folder(s) walked." % (
        counts["rows"], counts["files"], counts["folders_walked"]))
    for what, text, second in SYNC_FOUND:
        console.print("  %d %s" % (counts[what], text % counts[second] if second else text))
    if _not_looked_at(counts):
        console.print("  " + _not_looked_at(counts), markup=False, soft_wrap=True)
    if counts["review_folders"]:
        console.print("  %d folder(s) under the library's roots hold %d photo(s) and no indexed one: to review in"
                      " TagTuner (gear, Folders to review), never indexed on their own."
                      % (counts["review_folders"], counts["review_photos"]))
    if counts["missing"]:
        console.print("  %d folder(s) wholly gone, %d of them a folder the library holds, not there at all."
                      % (counts["folders_gone"], counts["roots_gone"]), markup=False, soft_wrap=True)
        # Applied, what the change removed (a row another sync or Remove Folder took meanwhile is skipped); a dry run,
        # what it would.
        removed = ((result.details["changed"]["removed"], result.details["changed"]["removed_faces"],
                    result.details["changed"]["removed_named"]) if apply_ else
                   (counts["to_remove"], counts["to_remove_faces"], counts["to_remove_named"]))
        console.print("  %d of the missing row(s) %s removed, with %d face(s), %d of them named (undoably); the rest"
                      " are kept: on a drive or share not confirmed there (an unplugged drive), a possible copy, found by"
                      " the second look (moved while the folders were walked), a sync of one folder, or a folder not"
                      " looked at."
                      % ((removed[0], "were" if apply_ else "would be") + removed[1:]), markup=False, soft_wrap=True)
    if counts["unreadable"]:
        console.print("  %d changed file(s) could not be read." % counts["unreadable"])
    if counts.get("to_check"):
        console.print(OWED_LINE % (counts["to_check"], counts["owed_folders"], counts["owed_no_vector"],
                                   counts["owed_stale"], counts["owed_unexamined"], counts["owed_no_fingerprint"])
                      + (OWED_HELD_BACK % counts["owed_held_back"] if counts["owed_held_back"] else ""),
                      markup=False, soft_wrap=True)
    if not apply_:
        console.print(maintenance.rehearsed(result), markup=False, soft_wrap=True)
        console.print("In step." if result.details["in_step"] else
                      "Nothing changed. --apply brings what it looked at in step." if _not_looked_at(counts) else
                      "Nothing changed. --apply brings it in step.")
        return
    changed = result.details["changed"]
    console.print("Wrote %d row(s): %d read again, %d moved, %d removed with %d face(s), %d of them named. %s" % (
        result.changed, changed["from_files"], changed["relinked"], changed["removed"], changed["removed_faces"],
        changed["removed_named"], maintenance.recorded(result, library.path)),
        markup=False, soft_wrap=True)
    for line in maintenance.skipped(result) + maintenance.failed(result) + result.details["warnings"]:
        console.print(line, markup=False, soft_wrap=True)
    if result.details["queued"]:
        console.print("Indexing %d folder(s) with new files or work owed, one at a time..." % result.details["queued"])
        queue = runtime.index_queues.of(library)
        queue.wait()
        for folder_path in result.details["reveal"]["new_folders"]:
            console.print("  %s" % queue.status(folder_path)["message"], markup=False)
    console.print("In step." if result.details["in_step"] else
                  "Not in step: some folders were not looked at (share not answering or not listable)." if _not_looked_at(counts) else
                  "Not yet in step: sync again once indexing is done.")
    if result.errors:
        raise SystemExit(1)


@contextlib.contextmanager
def _break_is_an_interrupt():
    """Ctrl+Break (SIGBREAK, Windows) stops a long write as Ctrl+C does, so that it ends as asked
    (file_changes withdraws the files not reached) instead of being killed with them planned."""
    import signal
    if not hasattr(signal, "SIGBREAK"):
        yield
        return

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    try:
        previous = signal.signal(signal.SIGBREAK, interrupt)
    except ValueError:      # not the main thread: nothing to install
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGBREAK, previous)


@cli.command("tags-from-faces")
@click.option("--apply", "apply_", is_flag=True,
              help="Write the keywords into the photo files. Without it, only says how many photos it would write.")
@click.option("--guesses", is_flag=True,
              help="Also write people only a guess backs (clustering's or automatch's): the keyword makes the guess a decided reference.")
@click.option("--folder", default=None, type=click.Path(file_okay=False),
              help="Only the photos under this folder, at any depth (a second location of a root works). Without it, the whole "
                   "library. A folder the library holds no photo under is refused.")
@click.pass_context
def tags_from_faces(ctx, apply_, guesses, folder):
    """Put on a photo the people its faces name and its keywords do not: a face named, the photo
    saying "No people tags" (#861). A dry run unless --apply; counts only, never names.

    --apply CHANGES PHOTO FILES: it writes each person's keyword into the photo's file with
    ExifTool, one change of the journal (`history` lists it, `undo` takes it back). Ctrl+C or Ctrl+Break
    stops it: the files written stay written and undoable, the rest are not written.
    Run it on a small library first; TagPup and TagTuner may stay open (a file changed meanwhile
    is a conflict, reported and not overwritten: run it again). Left, and counted: a person the
    a photo whose keywords already name the person under a root the
    tree does not file people under; and, unless --guesses, a person only a guess backs.

    --folder limits the count and the write to the photos under one folder and its subfolders."""
    library = _existing_library(ctx)
    planned = tags_from_faces_service.plan(library, guesses=guesses, folder=folder)
    if planned.refused:
        console.print("Refused: %s" % planned.refused, markup=False, soft_wrap=True)
        raise SystemExit(1)
    counts = planned.details["counts"]
    if folder:
        console.print("Only the photos under the folder given, and its subfolders.")
    console.print("%d photo(s) list a person from a face alone (%d people): their keywords do not name them."
                  % (counts["photos_with_a_person_on_a_face_alone"], counts["people"]))
    if counts["people_from_a_guess_only"]:
        console.print("  %d of the people: from a guess only (clustering's or automatch's, no face of that name named by hand): "
                      "skipped. --guesses writes them." % counts["people_from_a_guess_only"], markup=False, soft_wrap=True)
    if guesses:
        console.print("  WARNING: --guesses writes people only a guess backs; the keyword makes each guess a decided reference "
                      "for the faces compared with it afterwards (#640).", markup=False, soft_wrap=True)
    console.print("  %d photo file(s) would be written, for %d people."
                  % (counts["photos_to_write"], counts["people_to_write"]))
    if not apply_:
        console.print("Nothing changed. --apply writes the keywords into those %d PHOTO FILE(S), one change "
                      "of the journal that `undo` takes back." % counts["photos_to_write"], markup=False, soft_wrap=True)
        return
    if not counts["photos_to_write"]:
        console.print("Nothing to write.")
        return
    exiftool = get_exiftool_path(library.path)
    console.print("Writing %d photo file(s)..." % counts["photos_to_write"])
    changes = []
    try:
        with _break_is_an_interrupt():
            result = tags_from_faces_service.apply(library, planned, exiftool, on_planned=changes.append)
    except KeyboardInterrupt as stopped:
        console.print(file_changes.what_a_stop_left(library, stopped, changes[-1] if changes else None, "photo file"),
                      markup=False, soft_wrap=True)
        raise SystemExit(1) from None
    console.print("Wrote %d photo file(s) in %d change(s) of the journal (`history` lists them)."
                  % (result.changed, len(result.details.get("changes", []))), markup=False)
    if result.refused:
        console.print("Refused: %s" % result.refused, markup=False, soft_wrap=True)
    if result.errors:
        reasons = {}
        for _photo, why in result.errors:
            reasons[why] = reasons.get(why, 0) + 1
        console.print("%d photo(s) could not be written; run it again once they can:" % len(result.errors), markup=False)
        for why, count in sorted(reasons.items(), key=lambda pair: -pair[1])[:5]:
            console.print("  %d x %s" % (count, why), markup=False, soft_wrap=True)
    if result.refused or result.errors:
        raise SystemExit(1)


def _machine():
    """This machine's map of the roots (machine_roots.json in the TagPup home), handed to the
    service: tagpup.services does not import tagpup.config."""
    return library_roots.Machine(tagpup_config.machine_roots, tagpup_config.add_machine_root,
                                 tagpup_config.machine_roots_path)


@cli.group("roots", invoke_without_command=True)
@click.pass_context
def roots_command(ctx):
    """The library's roots (tagpup.services.roots): the places its photos' paths are held
    relative to, and where this machine keeps each. `roots check` says whether the rows agree
    with them."""
    if ctx.invoked_subcommand is not None:
        return
    library = _existing_library(ctx)
    try:
        found = library_roots.listing(library, _machine())
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    if not found["roots"]:
        console.print("%s has no roots: every path in it is this machine's own." % library.name, markup=False)
    for entry in found["roots"]:
        where = ", ".join(entry["locations"]) if entry["mapped"] else "NOT PLACED on this machine (%s)" % found["map"]
        console.print("%s  address %s  added %s  kept at %s" % (entry["name"], entry["address"] or "(none)",
                                                               entry["added"], where), markup=False, soft_wrap=True)


@roots_command.command("check")
@click.pass_context
def roots_check(ctx):
    """Whether every rooted row converts back (its root is the library's and this machine
    places it). Reads only."""
    library = _existing_library(ctx)
    try:
        problems = library_roots.check(library)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    if not problems:
        console.print("The library's roots are in order.", markup=False)
        return
    for problem in problems:
        console.print(problem, markup=False, soft_wrap=True)
    raise SystemExit(1)


@cli.group("thumbs")
def thumbs_command():
    """The thumbnail cache (tagpup.services.thumbnails): a small picture of each photo, kept beside the library
    so that browsing it by folder, keyword, person or date does not decode every photo's file again. Derived and
    not bounded: every one can be made again from its photo."""


def _megabytes(size):
    if size < 1e6:
        return "%d KB" % round(size / 1e3)
    return "%.1f MB" % (size / 1e6) if size < 1e9 else "%.2f GB" % (size / 1e9)


@thumbs_command.command("warm")
@click.option("--folder", default=None, help="Only the photos under this folder, at any depth.")
@click.option("--limit", type=click.IntRange(min=1), default=None,
              help="Stop after making this many; run it again to go on from there.")
@click.option("--apply", "apply_", is_flag=True,
              help="Make the thumbnails. Without it, only counts what is there and what would be made.")
@click.pass_context
@_needs_ffmpeg()
def thumbs_warm(ctx, folder, limit, apply_):
    """Make the thumbnails the library's photos lack, and the poster frames of its videos (through ffmpeg), ahead of
    the first time each is asked for. A dry run unless
    --apply: it counts the photos that have a thumbnail for their file as it is now, those that need one (and
    about how much they will take), those whose file is not there, damaged ones, which are not decoded, and
    reads nothing but the library and the files' sizes and times. It can be stopped and run again: what is made is
    kept, and the run goes on from it (--limit makes that many at a time). Writes only the cache folder; --apply
    first brings a library behind this version up to date."""
    library = _existing_library(ctx)
    if folder:
        canonical = library_roots.canonicaliser(library)
        folder = canonical(paths.stored(folder)) if canonical else paths.stored(folder)
    if apply_:
        applied = library_actions.bring_up_to_date(library.path)
        if applied:
            console.print("Brought the library up to date: %s." % ", ".join(applied), markup=False, soft_wrap=True)
    held, size = thumbnails.stats(library)
    console.print("Thumbnail cache of %s: %d thumbnail(s), %s, in %s." % (library.name, held, _megabytes(size), library.thumbs),
                  markup=False, soft_wrap=True)

    def progress(counts):
        console.print("  looked at %d photo(s): %d made, %d already there, %d to make" % (
            counts["photos"], counts["made"], counts["present"], counts["to_make"]), markup=False)

    try:
        with library_roots.pinned(library):
            counts = thumbnails.warm(library, tagpup_config.locate_ffmpeg, folder=folder, limit=limit, apply=apply_,
                                     progress=progress if apply_ else None)
    except library_roots.RootsChanged:
        console.print(library_roots.STOPPED, markup=False, soft_wrap=True)
        raise SystemExit(library_roots.EXIT_ROOTS_CHANGED) from None
    except (library_roots.Unplaced, ValueError) as why:
        console.print("Not run: %s" % why, markup=False, soft_wrap=True)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        console.print("Stopped. What was made is kept: run it again to go on.", markup=False)
        raise SystemExit(130) from None
    console.print("%d photo(s) looked at: %d already have a thumbnail, %d need one, %d have no file, %d are damaged "
                  "(not decoded), %d could not be reached." % (
                      counts["photos"], counts["present"], counts["to_make"], counts["missing"], counts["damaged"], counts["failed"]),
                  markup=False, soft_wrap=True)
    if counts["too_long"]:
        console.print("%d video(s) took too long to make a poster of (not damaged): run it again to try them once more."
                      % counts["too_long"], markup=False, soft_wrap=True)
    if not apply_:
        shown = counts["to_make"] if limit is None else min(counts["to_make"], limit)
        console.print("--apply would make %d, about %s at %s each. Nothing was written." % (
            shown, _megabytes(shown * counts["average"]), _megabytes(counts["average"])), markup=False, soft_wrap=True)
        return
    console.print("Made %d, %s." % (counts["made"], _megabytes(counts["bytes_made"])), markup=False)
    if counts["posters"]:
        console.print("Of them, %d are the poster frames of videos." % counts["posters"], markup=False)
    if counts["swept"]:
        console.print("Deleted %d thumbnail(s) of photos the library no longer holds." % counts["swept"], markup=False)
    if counts["away"]:
        console.print("Not walked: %s did not answer (taken as away) and its %d photo(s) were left; run it again when it "
                      "is back." % (", ".join(counts["away"]), counts["skipped_away"]), markup=False, soft_wrap=True)
    if counts["unwritable"]:
        console.print("Stopped at the first thumbnail that could not be kept: the cache folder cannot be written "
                      "(read-only or full), and nothing more was decoded.", markup=False, soft_wrap=True)
        raise SystemExit(1)
    if counts["stopped"]:
        console.print("Stopped at --limit; run it again to go on.", markup=False)


@cli.command("embed-photos")
@click.option("--model", "model", required=True, metavar="NAME:WEIGHTS",
              help="The photo model whose vectors to make, as open_clip names it: ViT-SO400M-14-SigLIP2:webli.")
@click.option("--apply", "apply_", is_flag=True, help="Make the vectors. Without it, only counts what it would make.")
@click.pass_context
@_needs_ffmpeg(when=lambda kwargs: kwargs["apply_"])
@_holds_the_roots
def embed_photos(ctx, model, apply_):
    """Make another photo model's vectors for the library's photos, beside its own model's, so the library can be
    switched to it in the gear (which refuses while too few photos hold one). A dry run unless --apply: counts the
    photos that hold none yet, and the time it would take. With --apply they are embedded on a turn of the graphics
    card, waited for behind any other run. The library's model and settings stay as they are, and nothing reads the
    new vectors until the switch. A video's vector is made of its frames, read by ffmpeg (needed with --apply), as
    the index makes it. Faces are not touched. Ctrl+C stops it after the photo under way; what was made is kept, and
    the next run makes the rest."""
    library = _existing_library(ctx)
    runtime = get_runtime()
    try:
        wanted = photo_reembed.model_of(model)
        planned = photo_reembed.plan(library, runtime, wanted)
    except ValueError as why:
        console.print("Not run: %s" % why, markup=False, soft_wrap=True)
        raise SystemExit(1) from None
    console.print("%s: %d photo(s), %d hold a vector under %s already, %d hold none (%d of them have no file)." % (
        library.name, planned["photos"], planned["held"], planned["key"], planned["to_embed"], planned["gone"]),
        markup=False, soft_wrap=True)
    if not apply_:
        console.print("--apply would embed %d, about %d minute(s). Nothing was written." % (
            planned["to_embed"] - planned["gone"], round(planned["seconds"] / 60)), markup=False, soft_wrap=True)
        return
    applied = library_actions.bring_up_to_date(library.path)
    if applied:
        console.print("Brought the library up to date: %s." % ", ".join(applied), markup=False, soft_wrap=True)

    def progress(counts):
        console.print("  embedded %d of %d, %d kept, %.0f s" % (
            counts["embedded"], counts["to_embed"], counts["kept"], counts["seconds"]), markup=False)

    with _stop_after_this_photo() as asked:
        try:
            counts = photo_reembed.run(library, runtime, wanted, progress=progress, should_stop=asked.is_set,
                                       report=lambda line: console.print(line or "Took the graphics card.",
                                                                         markup=False, soft_wrap=True))
        except gpu_turns.Cancelled:
            console.print("Stopped while it waited for the graphics card: nothing was written.", markup=False)
            raise SystemExit(1) from None
        except photo_reembed.Unreachable as stopped:
            console.print(str(stopped), markup=False, soft_wrap=True)
            _embedded_lines(stopped.counts)
            console.print("What was kept is kept: run it again to go on once the photos can be read.", markup=False,
                          soft_wrap=True)
            raise SystemExit(1) from None
    _embedded_lines(counts)
    now = photo_reembed.plan(library, runtime, wanted)
    console.print("%d of the library's %d photos now hold a vector under %s." % (now["held"], now["photos"], now["key"]),
                  markup=False, soft_wrap=True)
    if counts["stopped"]:
        console.print("Stopped: what was kept is kept; run it again to continue.", markup=False)
        raise SystemExit(130)


@cli.command("set-photo-model")
@click.argument("model", metavar="NAME:WEIGHTS")
@click.option("--apply", "apply_", is_flag=True,
              help="Switch: one change of the library's journal. Without it, only says whether the switch would pass.")
@click.pass_context
@_holds_the_roots
def set_photo_model(ctx, model, apply_):
    """Switch the library to the photo model NAME:WEIGHTS (ViT-SO400M-14-SigLIP2:webli) at its own image size, as the
    gear does: one change of the journal, refused while more than 5% of the photos hold no vector under the new model
    (embed-photos makes them). A dry run unless --apply: says the counts and whether it would pass. Search and Suggest
    read the new model's vectors from their next run; `undo <change> --apply` switches back, held to the same gate."""
    library = _existing_library(ctx)
    try:
        wanted = photo_reembed.model_of(model)
    except ValueError as why:
        console.print("Not run: %s" % why, markup=False, soft_wrap=True)
        raise SystemExit(1) from None
    result = photo_reembed.switch(library, wanted, apply=apply_)
    held = result.details.get("photo_vectors")
    if held:
        console.print(held["said"], markup=False, soft_wrap=True)
    words = result.details["tag_words"]
    console.print("Suggest holds %d word(s) (%d row(s)) made under %s:%s, of the %d it holds under the library's model: "
                  "it makes the other %d at its first run after the switch." % (
                      words["words"], words["rows"], wanted[0], wanted[1], words["own"],
                      max(words["own"] - words["words"], 0)), markup=False, soft_wrap=True)
    if result.refused:
        console.print(result.refused, markup=False, soft_wrap=True)
        raise SystemExit(1)
    if not result.details.get("changed"):
        console.print("The library's photo model is %s:%s already: nothing to change." % wanted, markup=False)
        return
    if not apply_:
        console.print("--apply would switch it. Nothing was written.", markup=False)
        return
    change = result.details["change"]
    console.print("Switched %s to %s:%s: change %d. Undo with `undo %d --apply` (the undo is held to the same gate)." % (
        library.name, wanted[0], wanted[1], change, change), markup=False, soft_wrap=True)


#: What `embed-photos` says of the clips ffmpeg cannot read as they are (search.NoFrames.lasting), before it names a few.
CLIPS_NOT_EMBEDDED = ("%d video(s) could not be read by ffmpeg: they hold no vector under the new model, and the next run "
                      "tries them again. Play one to see whether it is damaged.")


def _embedded_lines(counts):
    """What `embed-photos` says of a run: what it kept, and why the rest was not."""
    console.print("Embedded %d of %d: %d kept, %d not kept (the photo was removed meanwhile, or the write failed); %d "
                  "file(s) gone, %d could not be read%s (the next run tries them again)." % (
                      counts["embedded"], counts["to_embed"], counts["kept"], counts["not_kept"], counts["gone"],
                      sum(counts["unread"].values()),
                      (": " + ", ".join("%s x%d" % item for item in sorted(counts["unread"].items())))
                      if counts["unread"] else ""), markup=False, soft_wrap=True)
    unread = counts["clips_unread"]
    if unread:
        console.print(CLIPS_NOT_EMBEDDED % len(unread), markup=False, soft_wrap=True, highlight=False)
        for path, problem in unread[:5]:
            console.print(f"  - {path}: {problem}", markup=False, soft_wrap=True, highlight=False)
        if len(unread) > 5:
            console.print(f"  ... and {len(unread) - 5} more", markup=False)


@contextlib.contextmanager
def _stop_after_this_photo(what="photo"):
    """Ctrl+C (and Ctrl+Break) asks a run to stop before its next photo (or `what`: a chunk of photos), so the one under
    way is committed whole or not begun: the threading.Event the run asks. A second Ctrl+C stops at once (its
    transaction is rolled back)."""
    import signal
    import threading
    asked = threading.Event()
    kinds = (signal.SIGINT, signal.SIGBREAK)
    previous = {kind: signal.getsignal(kind) for kind in kinds}

    def stop(_signum, _frame):
        if asked.is_set():
            raise KeyboardInterrupt
        asked.set()
        console.print("Stopping after this %s (Ctrl+C again stops at once)..." % what, markup=False)

    for kind in kinds:
        signal.signal(kind, stop)
    try:
        yield asked
    finally:
        for kind in kinds:
            signal.signal(kind, previous[kind])


#: Why `retire-photo-vectors --apply` is refused while an app runs: a server's record names no library, so any
#: refuses it.
APP_RUNNING = ("Not run: TagPup is running (%d app server(s)). Stop the app first (close it from its tray or its "
               "window): a running app keeps the vectors it read in memory, and its Suggest and search beside this run "
               "would read vectors this run deletes. Its record names no library, so any running app refuses it.")

#: Why `retire-photo-vectors --apply` stopped between two chunks: an app started during the run (launcher.running, asked
#: a chunk).
APP_STARTED = ("Stopped: TagPup was started during the run (%d app server(s)); the chunks written stand. Stop the app, then "
               "run it again to go on, as the same change.")

#: How long the snapshot `retire-photo-vectors --apply` takes first stands: it is a daily (snapshots.KEEP, three dailies).
SNAPSHOT_STANDS = ("while it stands: it is a daily, three are kept, and the app's own daily snapshots push it out in about "
                   "three days (TagPup CLI: snapshots list)")


@cli.command("retire-photo-vectors")
@click.option("--apply", "apply_", is_flag=True,
              help="Write: after a snapshot of the library, taken even when one is from today (it is the way back), one "
                   "change of the journal a model, which is not undone. Without it, only says what it would do.")
@click.pass_context
def retire_photo_vectors_command(ctx, apply_):
    """Once the library is on its new photo model (after embed-photos and set-photo-model): delete the photo vectors of
    every other model, which no reader keyed by the library's model reads, and the words Suggest cached under those
    models (docs/AI_PIPELINE.md; owner, 2026-10-24). No photo, keyword, face or tag is touched. Refused while more of
    the library's photos than a switch allows hold no vector under its own model. A dry run unless --apply; counts
    only. --apply takes a snapshot first, always: it is the way back (the change's undo is refused, and so are a switch
    back to a retired model and the undo of the switch away from it). Ctrl+C stops it after the chunk under way, and
    so does an app started during the run; run it again to go on, as the same change. --apply is refused while an app
    runs; an MCP server keeps no record of itself and is not seen: close it first. After it, `compact` gives the space
    back to the disk."""
    library = _existing_library(ctx)
    say = functools.partial(console.print, markup=False, soft_wrap=True)
    say("Retiring the vectors of the photo models %s is not on%s." % (library.name, "" if apply_ else " (a dry run)"))
    own, (photos, holds), plans = retire_photo_vectors.plan(library)
    say("The library's photo model: %s. %s" % (own, library_settings.PHOTOS_HELD % (holds, photos, own, photos - holds)))
    if not plans:
        say("Nothing to retire: no vector under another key is left.")
        return
    if plans[0].refused:
        say(plans[0].refused)
        raise SystemExit(1)
    for planned in plans:
        say(planned.said())
    if not apply_:
        say(VECTORS_DRY_RUN)
        return
    retired = _retire_vectors_applied(library, plans, say)
    if retired[-1].error or retired[-1].stopped:
        raise SystemExit(1)
    for done in retired:
        say("The switch back to %s is no longer possible (the undo of the settings change is refused: no photo holds a "
            "vector under it); the snapshot is the way back; `compact`, with the apps closed, gives the space back."
            % done.plan.key)


#: What a dry run of retire-photo-vectors says --apply does.
VECTORS_DRY_RUN = ("Nothing changed. --apply writes it, after a snapshot of the library (taken even when one is from "
                   "today: the oldest daily may go): one change of the journal, which is not undone; the snapshot is the "
                   "way back %s. Then `compact` gives the space back to the disk." % SNAPSHOT_STANDS)


def _retire_vectors_applied(library, plans, say):
    """--apply of retire-photo-vectors: `plans` (PhotoVectorPlan, a key's vectors each) applied in turn by
    retire_photo_vectors.apply (PhotoVectorsRetired), each said. Refused while an app runs (APP_RUNNING). A
    snapshot first, forced, as it is the way back, unless the run continues a change a stopped run left (its first run
    took one). Ctrl+C, or an app started during the run, stops it after the chunk under way, and the plans after it are
    not begun. Returns [PhotoVectorsRetired], the last the one that ended the run."""
    servers = launcher.running()
    if servers:
        say(APP_RUNNING % len(servers))
        raise SystemExit(1)
    found, started = [], []
    with _stop_after_this_photo("chunk of vectors") as asked:
        continuing = [planned.continues for planned in plans if planned.continues is not None]
        if continuing:
            # The snapshot the first run took holds every vector; one taken now holds fewer and pushes a daily out.
            say("Continuing change %s: the snapshot taken before its first run is the way back %s; none taken."
                % (", ".join(map(str, continuing)), SNAPSHOT_STANDS))
        else:
            try:
                taken = library_snapshots.take(library, force=True)
            except Exception as problem:
                say("Nothing was written: the snapshot of the library could not be taken (%s: %s)."
                    % (type(problem).__name__, problem))
                raise SystemExit(1) from None
            say("Took a snapshot of the library first (%s; %d older removed)."
                % (", ".join(taken.details["taken"]), taken.details["removed"]))

        def heard(chunk, chunks):
            heard.number += 1
            say("Chunk %d of %d: %d vectors deleted." % (heard.number, chunks, chunk.deleted))

        def stop():
            if asked.is_set():
                return True
            started[:] = launcher.running()   # an app started during the run: asked before each chunk
            return bool(started)
        for planned in plans:
            heard.number = 0
            retired = retire_photo_vectors.apply(library, planned, stop=stop, heard=heard)
            found.append(retired)
            say(retired.said())
            if retired.error or retired.stopped:
                break
    if found[-1].stopped and started:
        say(APP_STARTED % len(started))
    changes = [retired.change_id for retired in found if retired.change_id is not None]
    if changes:
        say("Written: change %s, which is not undone: the snapshot taken first is the way back %s. `compact`, with the "
            "apps closed, gives the space back to the disk." % (", ".join(map(str, changes)), SNAPSHOT_STANDS))
    return found


@cli.group()
def snapshots():
    """The library's snapshots, its backup: three daily, one weekly and one monthly, in
    data/backups/<library>/, taken by the recurring job `snapshots`."""


@snapshots.command("list")
@click.pass_context
def snapshots_list(ctx):
    """Each snapshot of the library: when it was taken, its size, and how many of the
    journal's changes were made since -- what restoring it would lose."""
    library = _existing_library(ctx)
    found = library_snapshots.listing(library)
    if not found["snapshots"]:
        console.print("%s has no snapshots yet; the Activity page's Run now takes one." % library.name, markup=False)
        return
    table = Table(title="Snapshots of %s" % library.name)
    for column in ("Name", "Taken", "Size", "Changes since", "Schema"):
        table.add_column(column)
    for snapshot in found["snapshots"]:
        table.add_row(snapshot["name"], snapshot["taken"], "%s MB" % format(snapshot["bytes"] // 1_000_000, ","),
                      str(snapshot["changes_since"]),
                      "%d (too old to restore)" % snapshot["schema"] if snapshot["too_old"] else str(snapshot["schema"]))
    console.print(table)
    console.print("They take %s MB." % format(found["bytes"] // 1_000_000, ","), markup=False)


@snapshots.command("restore")
@click.argument("name")
@click.option("--apply", "apply_", is_flag=True, help="Restore it, after a snapshot of the library as it is. Without it, only says what would be lost.")
@click.pass_context
def snapshots_restore(ctx, name, apply_):
    """Put the library back as snapshot NAME (`snapshots list` names them, daily/20260926_090000)
    held it. A dry run unless --apply, saying how many of the journal's changes since the
    snapshot would be lost. Photo files written since keep what was written."""
    library = _existing_library(ctx)
    try:
        result = library_snapshots.restore(library, name, apply=apply_)
    except NotFound as e:
        raise click.ClickException(str(e)) from e
    lost = result.details["lost"]
    console.print("Restoring %s would lose %d change(s) of the journal%s" % (
        name, len(lost), ":" if lost else "."), markup=False)
    for change in lost:
        console.print("  %d  %s  %s" % (change["id"], change["created"], change["operation"]), markup=False)
    if "needs" in result.details:   # not for a snapshot refused for its schema, which is refused before the disk is asked
        console.print("It needs about %s MB free on the disk -- the library as it is, snapshotted first, and the copy"
                      " back -- and the disk has %s MB free. photo_index takes about half a minute." % (
                          format(result.details["needs"] // 1_000_000, ","), format(result.details["free"] // 1_000_000, ",")),
                      markup=False, soft_wrap=True)
    if result.refused:
        console.print("Refused: %s" % result.refused, markup=False, soft_wrap=True)
        raise SystemExit(1)
    if not apply_:
        console.print("Nothing changed. --apply restores it, after a snapshot of the library as it is.", markup=False)
        return
    console.print("Restored %s from %s. The library as it was is %s: `snapshots restore %s --apply` puts it back."
                  % (library.name, name, result.details["before_restore"], result.details["before_restore"]),
                  markup=False)


@cli.command("cluster-faces")
@click.pass_context
def cluster_faces(ctx):
    """Run self-tuning identity resolution to cluster and name faces using photo tags. Run by hand: `index` matches only
    the faces it made, and writes nothing."""
    _resolve_identities(get_runtime(), get_db_path(ctx.obj.get("test", False), ctx.obj.get("db")))


def _resolve_identities(runtime, db_path):
    """Cluster and name the library's faces from its photos' tags (tagpup.services.identities.resolve), saying what it
    named: `cluster-faces`."""
    photo_index = library_index(runtime, db_path)
    if not photo_index.load():
        console.print("[bold red]Error:[/bold red] No photo index found.")
        return

    try:
        unpaired = []

        def heard(stage, done=0, total=1):
            if stage == "left":
                # The causes set_names refuses for: a face or its photo named meanwhile, a person merged away (#1583). Two
                # counts: the faces left as they were, and those that lost their guess and are unnamed.
                kept, unnamed_now = done, total
                console.print(f"[bold yellow]{kept + unnamed_now} face(s) were not given what this run found: the library "
                              f"changed under them while it ran" + (f"; {unnamed_now} of them lost the guess they carried and"
                                                                   f" are unnamed" if unnamed_now else "")
                              + ".[/bold yellow]")
            if stage == "unpaired":
                # The reason first, and short: the indexer's progress line shows 90 characters.
                unpaired.append(total)
                console.print(f"[yellow]People with no known face, not told apart: {done} guess(es) taken back, "
                              f"{total} left unnamed.[/yellow]")
        resolved_stats = identities.resolve(photo_index, on_step=heard)

        if not resolved_stats:
            # Tagging is no advice for faces whose photo is tagged and whose people cannot be told apart.
            console.print("[yellow]No face was newly named." + ("" if unpaired else " Try tagging photos with people names first.")
                          + "[/yellow]")
            return

        table = Table(title="Resolved Face Identities Summary")
        table.add_column("Person (tag)", style="green")
        table.add_column("Faces Named", justify="right", style="cyan")

        for name, count in sorted(resolved_stats.items(), key=lambda x: -x[1]):
            table.add_row(name, str(count))

        console.print(table)
        console.print("[bold green]Self-tuning identity resolution complete![/bold green]")
    finally:
        photo_index.close()

if __name__ == "__main__":
    try:
        cli()
    except library_actions.NewerLibrary as e:   # one a command opened without the group having seen it
        console.print(str(e), markup=False, soft_wrap=True)
        raise SystemExit(1) from None

