# tagpup_cli.py
import contextlib
import functools
from collections import Counter
import os
import sys
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
    _tagpup_logs.prune_run_logs(os.environ["TAGPUP_LOG_TO"])
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
from tagpup.files.metadata import IdentityWriter, MetadataExtractor
from tagpup.store.taxonomy import TagTaxonomy
from tagpup.core import gpu_turns
from tagpup.core import paths
from tagpup.core import read_ahead
from tagpup.store import db as tagpup_db
from tagpup import config as tagpup_config
from tagpup import runtime as runtimes
from tagpup.runtime import Runtime
from tagpup.services import settings as library_settings
from tagpup.services import libraries as library_actions
from tagpup.services import faces as face_records
from tagpup.services import tags_from_faces as tags_from_faces_service
from tagpup.services import identities
from tagpup.services import indexing as indexing_service
from tagpup.services import thumbnails
from tagpup.services import embedding_repair
from tagpup.services import journal as library_journal
from tagpup.services import snapshots as library_snapshots
from tagpup.services import roots as library_roots
from tagpup.core.result import NotFound
from tagpup.services import maintenance
from tagpup.services import refresh_rows
from tagpup.jobs import indexing as indexing_jobs
from tagpup.services.search import PhotoIndex
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


def read_ahead_bytes(width, height, clip=None, faces=None, max_side=None):
    """What a photo of `width` x `height` is reserved while it is read ahead: the larger of what each half it goes
    through holds at once -- CLIP's (`clip`, when its vector is to be made: ClipModel.input_bytes), whose pictures
    are let go before the faces' are made, and the faces' (`faces`: FaceModel.picture_bytes), held until the card
    has detected them. Each half says its own; this only takes the larger."""
    halves = [faces.picture_bytes(width, height, max_side=max_side)] if faces is not None else []
    if clip is not None:
        halves.append(clip.input_bytes(width, height))
    return max(halves, default=0)


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

@click.group()
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
        if found != directory and not os.path.isdir(found):
            console.print(library_roots.old_place_sentence(library, directory, found), markup=False, soft_wrap=True)
            raise SystemExit(1)
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


@cli.command()
@click.argument("directories", nargs=-1, required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--force-reembed", is_flag=True, help="Force recreation of embeddings.")
@click.option("--reset", is_flag=True, help="Delete existing index and taxonomy to start fresh.")
@click.option("--skip-faces", is_flag=True, help="Skip face detection during indexing.")
@click.option("--no-subfolders", is_flag=True,
              help="Only the photos directly in each DIRECTORY, not in its subfolders.")
@click.pass_context
@_holds_the_roots
def index(ctx, directories, force_reembed: bool, reset: bool, skip_faces: bool, no_subfolders: bool = False):
    """Phase 1: Scan and index a tagged photo library: one or more DIRECTORIES, in one run
    (sync hands it every folder of new files at once, so one indexer loads the models)."""
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
    on_disk = {}
    for directory in directories:
        console.print(f"[bold cyan]Scanning directory:[/bold cyan] {directory}")
        for key, stamp in (image_files.stamps_in(directory) if no_subfolders
                           else image_files.stamps_under(directory)).items():
            on_disk.setdefault(key, stamp)
    all_images = [stamp[0] for stamp in on_disk.values()]
    console.print(f"Found {len(all_images)} image(s) total.")

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
    if force_reembed:
        images_to_process = all_images
    else:
        current = photo_index.current(on_disk)
        images_to_process = [path for path in all_images if paths.key(path) not in current]
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
        return

    # Only now that a photo is to be embedded: the CLIP model's library (open_clip, torch) takes seconds to import,
    # and a run that finds nothing to index -- every sync, every watcher run -- needs none of it (docs/findings.md, #1089).
    # A model's vectors are kept under its own key (tagpup.store.embeddings.model_key): another model's are never compared.
    embeddings = runtime.embeddings(Library(db_path), photo_index)

    # Extract metadata in batches of 500
    console.print(f"[bold cyan]Reading metadata in batches for {len(images_to_process)} image(s)...[/bold cyan]")
    # Read only: a photo is given its identity below, once its picture has decoded.
    extractor = MetadataExtractor(exiftool_path=exiftool_path)

    batch_size = 500
    all_metadata = []
    
    from tqdm import tqdm
    for i in tqdm(range(0, len(images_to_process), batch_size), desc="Reading metadata"):
        batch = images_to_process[i:i+batch_size]
        batch_meta = extractor.batch_read(batch, people=store_taxonomy.people_vocabulary(db_path))
        all_metadata.extend(batch_meta)

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
    # (MetadataExtractor.batch_read), and the next index reads it again.
    left_unread = Counter(m["unread"] for m in all_metadata if m["mtime"] is None)
    to_index_meta = [m for m in all_metadata if m["mtime"] is not None]
    for why, count in sorted(left_unread.items()):
        console.print(f"[bold yellow]{count} photo(s) were not read: {why}.[/bold yellow] They are left as they were; "
                      f"the next index reads them.")
    tagged_count = sum(1 for m in to_index_meta if m["tags"] or m["people"] or m["captions"])

    console.print(
        f"Indexing [bold green]{len(to_index_meta)}[/bold green] image(s) "
        f"([bold]{tagged_count}[/bold] already tagged, "
        f"[bold]{len(to_index_meta) - tagged_count}[/bold] untagged)."
    )

    # If there is nothing at all to index
    if not to_index_meta:
        if skipped_count > 0:
            console.print("[green]No new images found. Index remains current.[/green]")
        else:
            console.print("[yellow]No photos found to index.[/yellow]")
        photo_index.close()
        return

    # Per-photo write locks, shared with every other indexer of the libraries in this
    # folder, wherever each was started from.
    locker = PathLocker(lock_dir=Library(db_path).locks)
    face_processor = runtime.faces(Library(db_path), settings) if not skip_faces else None
    # The library's cap on the picture the detector looks at, as the run began: one argument of each detection, not
    # part of the model (two libraries differing only in it share one).
    detect_max_side = settings.detect_max_side
    # The indexer records what it reads, so it may give a photo its identity -- after the
    # photo's picture has decoded in full, never before (IdentityWriter).
    identity_writer = IdentityWriter(exiftool_path)
    unreadable, incomplete = [], []

    # The index queue's Cancel, while this waits for the graphics card (tagpup.jobs.indexing).
    stop_file = os.environ.get(indexing_service.STOP_FILE)
    turn = photos = bar = None
    total_new_indexed = 0
    try:
        # One process at a time has a model on the graphics card (tagpup.ml.gpu): its turn is
        # taken before the first photo, waited for in order -- each change of who has it said
        # on stdout, the server's progress line -- and held to the end of the run. Stopped
        # while it waits, nothing has been indexed (docs/findings.md, #750).
        names = ", ".join(os.path.basename(d.rstrip("/\\")) or d for d in directories)
        waited = []

        def say_waiting(line):
            waited.append(line)
            console.print(line, markup=False, soft_wrap=True)
        try:
            turn = runtime.gpu_turn("indexing %s (%s)" % (names, Library(db_path).name),
                                    (embeddings.clip, face_processor),
                                    cancelled=lambda: bool(stop_file) and os.path.exists(stop_file),
                                    report=say_waiting)
        except gpu_turns.Cancelled:
            console.print(indexing_service.CANCELLED_WAITING, markup=False, soft_wrap=True)
            raise SystemExit(indexing_service.EXIT_CANCELLED) from None
        if waited:
            console.print("Took the graphics card.", markup=False, soft_wrap=True)

        # Generate Embeddings with incremental saving (batches of 100) to protect against halts/crashes
        console.print("[bold cyan]Generating embeddings...[/bold cyan]")
        batch_embeddings = []
        batch_metas = []
        batch_faces = {}  # Map path -> faces list
        given = set()  # The paths of the photos given their identity, read again before their batch is recorded
        total_new_indexed = 0
        locked_out = []

        def begin(meta):
            """As a photo enters the read-ahead, in this thread: its lock, and the vector the library keeps of it.
            None when another live process has the photo: it is passed over, and said below. A skip here used to be
            silent, which is how 348 photos held by long-dead runs stayed out of the index for three months without
            anyone noticing."""
            path = meta["path"]
            if not locker.acquire(path):
                locked_out.append(path)
                return None
            kept = None if force_reembed else embeddings.cached(path)
            if kept is None:
                # Loaded here, on the thread that has the graphics card: a read makes CLIP's input with the loaded
                # model's preprocessing.
                embeddings.clip.load()
            return {"vector": kept}

        def read(meta, begun, reserve):
            """On a thread of the read-ahead: the photo decoded and made ready for the models, nothing written and
            nothing on the graphics card. A picture that does not decode raises images.Unreadable, handed to the
            loop below in the photo's turn."""
            path = meta["path"]
            width, height = image_files.picture_size(path)
            reserve(read_ahead_bytes(width, height, embeddings.clip if begun["vector"] is None else None,
                                     face_processor, detect_max_side))
            looked = {}
            prepared = None
            if begun["vector"] is None:
                # Reads the file once and decodes the whole picture; a vector kept from the file as it is was made
                # by such a decode.
                # Never loading CLIP: it was loaded in begin, on the thread that has the card, and a read still under
                # way when the index stops must not load it again after the turn is given back.
                prepared = embeddings.prepared(path, load=False, seen=lambda picture: looked.update(
                    zeros=picture.info.get(image_files.ZERO_TAIL_INFO, 0)))
            zeros = looked["zeros"] if "zeros" in looked else image_files.zero_tail_of(path)
            picture = face_processor.picture(path, max_side=detect_max_side) if face_processor else None
            return prepared, zeros, picture

        # The photos are read ahead and handed back in their order; the graphics card, the identity written into a
        # file and every write of the library stay on this thread, one photo after the other, as before.
        photos = read_ahead.ReadAhead(to_index_meta, begin, read, threads=READ_THREADS, ahead=READ_AHEAD,
                                      budget=READ_AHEAD_BYTES)
        # The bar is moved by hand: tqdm iterating `photos` kept the last photo it handed on, and its pictures, while
        # it waited for the next one -- outside what the read-ahead counts.
        bar = tqdm(total=len(to_index_meta), desc="Generating embeddings")
        for meta, begun, result in photos:
            bar.update(1)
            if begun is None:
                continue
            path = meta["path"]
            try:
                try:
                    prepared, zeros, picture = result.result()
                except image_files.Unreadable as damaged:
                    # Nothing is written into it: no identity, no row.
                    unreadable.append((path, damaged))
                    logger.info(f"Not indexed, and not written to: {path} does not decode: {damaged}")
                    locker.release(path)
                    continue
                emb = begun["vector"] if prepared is None else embeddings.made(path, prepared)
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

                # Extract and save face embeddings in the same pass (cached in memory until parent photo is saved)
                if face_processor:
                    faces = face_processor.detect_and_embed(picture)
                    batch_faces[path] = faces

                # A photo already indexed is not removed first: build_or_update
                # updates its row in place. Deleting the row cascaded to its faces
                # and took every name given by hand, and every exclusion, with it.

                batch_embeddings.append(emb)
                batch_metas.append(meta)

                # The nodes of the tags and people a photo names are made where the batch is recorded
                # (derived.make_nodes, taxonomy.ensure_node): the one writer of them.

                # Save progress incrementally in batches of 100
                if len(batch_embeddings) >= 100:
                    _read_again(photo_index.conn, batch_metas, given, exiftool_path)
                    photo_index.build_or_update(batch_embeddings, batch_metas, dim=len(batch_embeddings[0]))

                    # Save faces for the batch in a single transaction
                    if batch_faces:
                        # --force-reembed means redo the work; without it the
                        # existing face rows, and the curation on them, are kept.
                        face_records.record_batch(photo_index.conn, batch_faces, overwrite=force_reembed,
                                                  detector=face_records.detector_of(face_processor, detect_max_side))
                        batch_faces.clear()

                    # Release locks for saved images
                    for saved_meta in batch_metas:
                        locker.release(saved_meta["path"])

                    total_new_indexed += len(batch_embeddings)
                    batch_embeddings = []
                    batch_metas = []
            except Exception as e:
                logger.error(f"Error indexing {path}: {e}")
                locker.release(path)
            finally:
                # The decoded pictures go with the photo: what the read-ahead counts as held is given back when the
                # next photo is asked for, and nothing here keeps them past that.
                result = prepared = picture = None

        # Rebuild or update the FAISS index for the final batch
        if batch_embeddings:
            # Not reloaded: nothing below searches this index, and a reload reads every vector again.
            _read_again(photo_index.conn, batch_metas, given, exiftool_path)
            photo_index.build_or_update(batch_embeddings, batch_metas, dim=len(batch_embeddings[0]))
            
            # Save the remaining face embeddings
            if batch_faces:
                face_records.record_batch(photo_index.conn, batch_faces, overwrite=force_reembed,
                                          detector=face_records.detector_of(face_processor, detect_max_side))
                batch_faces.clear()
            
            for saved_meta in batch_metas:
                locker.release(saved_meta["path"])
            total_new_indexed += len(batch_embeddings)

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

        if total_new_indexed > 0:
            console.print("[bold green]Indexing successfully completed![/bold green]")
        else:
            console.print("[yellow]No new embeddings generated.[/yellow]")
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

    # An index is followed by clustering (owner, DECISIONS 2026-10-16), here, so that every way of indexing -- both
    # apps' queue and sync's, which run this command, and this command by hand -- does it alike. Only after a run that
    # recorded a photo: one that recorded none (every sync and watcher run that found nothing new, a folder added
    # again) changed nothing clustering reads. The card's turn was let go above, its models unloaded: clustering is
    # the processor's work and holds no turn while another library's index waits for the card.
    if total_new_indexed:
        _cluster_after_index(runtime, db_path)


def _read_again(conn, metas, given, exiftool_path):
    """The fields of each photo of `metas` that was `given` its identity, read from the file again (refresh_rows.
    read_files, stamped before its own read), so its row is recorded as the file is after the write: the next index
    finds it current and reads nothing, and its vector and faces are kept. The batch read was up to 500 photos
    earlier, and a keyword saved into the file since then is read here (docs/findings.md, #1599). A photo not read in
    full now -- a share gone away mid-run (tests/test_a_share_asleep.py) -- keeps the record of the batch read, stamped
    before that read: the next index reads it once more."""
    again = [meta["path"] for meta in metas if meta["path"] in given]
    if not again:
        return
    records = refresh_rows.read_files(conn, again, exiftool_path)
    for meta in metas:
        record = records.get(meta["path"])
        if record is not None and record["mtime"] is not None and not record.get("read_error"):
            meta.update(record)


def _cluster_after_index(runtime, db_path):
    """Cluster the library's faces after an index (`cluster-faces`'s work), said on stdout first as the server's index
    queue reads it (tagpup.services.indexing.CLUSTERING_STARTS: it refuses writes of faces until this process ends). A
    failure is said and exits with EXIT_CLUSTERING_FAILED: the photos indexed stay indexed. The library's roots changed
    by another process meanwhile is the index's one roots stop (_holds_the_roots), not a failure."""
    console.print(indexing_service.CLUSTERING_STARTS, markup=False, soft_wrap=True)
    try:
        _resolve_identities(runtime, db_path)
    except library_roots.RootsChanged:
        raise
    except Exception as e:
        logger.exception("Resolving face identities after the index failed")
        console.print("The photos are indexed, but resolving face identities failed: %s: %s" % (type(e).__name__, e),
                      markup=False, soft_wrap=True)
        raise SystemExit(indexing_service.EXIT_CLUSTERING_FAILED) from None

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
    result = library_journal.undo(library, change_id, apply=apply_,
                                  exiftool_path=get_exiftool_path(library.path, read_only=True))
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


#: What `sync` says of each thing it counts.
SYNC_FOUND = (("new", "new file(s), in %d folder(s)", "new_folders"), ("changed", "changed file(s)", None),
              ("moved", "moved file(s)", None), ("missing", "missing file(s), in %d folder(s)", "missing_folders"))


@cli.command()
@click.option("--folder", default=None, type=click.Path(file_okay=False),
              help="Only the photos under this folder. Without it, every folder the library holds photos in.")
@click.option("--apply", "apply_", is_flag=True,
              help="Write the rows, index the new files, and record the run. Without it, only says what it would do.")
@click.pass_context
def sync(ctx, folder, apply_):
    """Bring the library in step with its folders: rows read again for files changed
    outside the apps, rows following files that moved, and new files indexed. Missing
    files are reported, never removed. A dry run unless --apply; counts only."""
    library = _existing_library(ctx)
    result = runtimes.sync(library, folder=folder, apply=apply_)
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
        console.print("  %d folder(s) wholly gone, %d of them a folder the library holds, not there at all (an "
                      "unplugged drive looks the same); their rows are kept."
                      % (counts["folders_gone"], counts["roots_gone"]), markup=False, soft_wrap=True)
        if counts["folders_gone"]:
            console.print("  A folder renamed in Explorer looks the same: its rows are kept; rename it back "
                          "by hand.")
    if counts["unreadable"]:
        console.print("  %d changed file(s) could not be read." % counts["unreadable"])
    if counts.get("size_changed"):
        console.print("  %d of the changed file(s) changed SIZE: their vectors and face boxes may be of an older picture "
                      "(a keyword write changes the size too); they are left as they are." % counts["size_changed"])
    if not apply_:
        console.print(maintenance.rehearsed(result), markup=False, soft_wrap=True)
        console.print("In step." if result.details["in_step"] else
                      "Nothing changed. --apply brings what it looked at in step." if _not_looked_at(counts) else
                      "Nothing changed. --apply brings it in step.")
        return
    changed = result.details["changed"]
    console.print("Wrote %d row(s): %d read again, %d moved. %s" % (
        result.changed, changed["from_files"], changed["relinked"], maintenance.recorded(result, library.path)),
        markup=False, soft_wrap=True)
    for line in maintenance.skipped(result) + maintenance.failed(result) + result.details["warnings"]:
        console.print(line, markup=False, soft_wrap=True)
    if result.details["queued"]:
        console.print("Indexing %d folder(s) with new files, one at a time..." % result.details["queued"])
        queue = indexing_jobs.queue_for(library)
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
    try:
        with _break_is_an_interrupt():
            result = tags_from_faces_service.apply(library, planned, exiftool)
    except KeyboardInterrupt as stopped:
        written, left = getattr(stopped, "written", None), getattr(stopped, "left", 0)
        if left:
            # The file under way could not be read (the Ctrl+C ended ExifTool too): its count is not known yet.
            console.print("Stopped: %s photo file(s) written, recorded as change %s of the journal; %d more is settled the "
                          "next time the library is opened, and then `undo` takes the change back. The rest were not "
                          "written. Run it again for them."
                          % ("?" if written is None else written, getattr(stopped, "change", None), left),
                          markup=False, soft_wrap=True)
            raise SystemExit(1) from None
        console.print("Stopped: %s photo file(s) written, recorded as change %s of the journal (`undo` takes it back); "
                      "the rest were not written. Run it again for them."
                      % ("?" if written is None else written, getattr(stopped, "change", None) or "none"), markup=False,
                      soft_wrap=True)
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
def thumbs_warm(ctx, folder, limit, apply_):
    """Make the thumbnails the library's photos lack, ahead of the first time each is asked for. A dry run unless
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
            counts = thumbnails.warm(library, folder=folder, limit=limit, apply=apply_,
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
    if not apply_:
        shown = counts["to_make"] if limit is None else min(counts["to_make"], limit)
        console.print("--apply would make %d, about %s at %s each. Nothing was written." % (
            shown, _megabytes(shown * counts["average"]), _megabytes(counts["average"])), markup=False, soft_wrap=True)
        return
    console.print("Made %d, %s." % (counts["made"], _megabytes(counts["bytes_made"])), markup=False)
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


@cli.command("repair-vectors")
@click.option("--apply", "apply_", is_flag=True,
              help="Make the vectors again and replace those that differ. Without it, only counts the turned photos.")
@click.pass_context
def repair_vectors(ctx, apply_):
    """Make the vectors of turned photos (an EXIF Orientation of 2 to 8) describe the photo as it is shown. A dry run
    unless --apply: it reads each photo's header (never the picture) and counts the turned ones that hold a vector for
    their file as it is. With --apply each is embedded again on a turn of the graphics card, and its vector replaced
    when the new one differs; run again it finds each already matching. A photo whose file changed meanwhile is
    embedded as it now is. Face vectors and boxes are not touched."""
    library = _existing_library(ctx)
    if apply_:
        applied = library_actions.bring_up_to_date(library.path)
        if applied:
            console.print("Brought the library up to date: %s." % ", ".join(applied), markup=False, soft_wrap=True)
    def progress(counts):
        console.print("  looked at %d photo(s): %d turned" % (counts["photos"], counts["turned"]) + (
            ", %d made again, %d already matched" % (counts["replaced"], counts["already_upright"])
            if "replaced" in counts else ""), markup=False)

    def say_waiting(line):
        console.print(line, markup=False, soft_wrap=True)

    try:
        with library_roots.pinned(library):
            counts = embedding_repair.repair(library, get_runtime(), apply=apply_, progress=progress,
                                             report=say_waiting)
    except library_roots.RootsChanged:
        console.print(library_roots.STOPPED, markup=False, soft_wrap=True)
        raise SystemExit(library_roots.EXIT_ROOTS_CHANGED) from None
    except (library_roots.Unplaced, ValueError) as why:
        console.print("Not run: %s" % why, markup=False, soft_wrap=True)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        console.print("Stopped. What was replaced is kept: run it again to go on.", markup=False)
        raise SystemExit(130) from None
    console.print("%d photo(s) looked at: %d turned, of which %d hold a vector for their file as it is, %d hold none and "
                  "%d hold one for an earlier file (the next look at those makes one upright); %d have no file, %d could "
                  "not be read, %d have a header that does not read." % (
                      counts["photos"], counts["turned"], counts["to_recompute"], counts["no_vector"],
                      counts["stale_stamp"], counts["missing"], counts["failed"], counts["no_header"]),
                  markup=False, soft_wrap=True)
    if counts["away"]:
        console.print("Not walked: %s did not answer (taken as away) and its %d photo(s) were left; run it again when it "
                      "is back." % (", ".join(counts["away"]), counts["skipped_away"]), markup=False, soft_wrap=True)
    if not apply_:
        console.print("--apply would make %d vector(s) again. Nothing was written." % counts["to_recompute"],
                      markup=False, soft_wrap=True)
        return
    console.print("Replaced %d (read back as written), %d already matched, %d not kept (the file changed meanwhile or the "
                  "write failed; run it again), %d damaged, %d could not be made%s." % (
                      counts["replaced"], counts["already_upright"], counts["not_kept"], counts["damaged"],
                      counts["unreadable"], (": " + ", ".join("%s x%d" % item for item in counts["reasons"].items()))
                      if counts["reasons"] else ""), markup=False, soft_wrap=True)
    console.print("A running TagPup keeps the vectors it loaded until a photo is edited or the app is restarted: "
                  "restart it to search by the new ones.", markup=False, soft_wrap=True)


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
    """Run self-tuning identity resolution to cluster and name faces using photo tags (`index` runs it after an index
    that recorded a photo)."""
    _resolve_identities(get_runtime(), get_db_path(ctx.obj.get("test", False), ctx.obj.get("db")))


def _resolve_identities(runtime, db_path):
    """Cluster and name the library's faces from its photos' tags (tagpup.services.identities.resolve), saying what it
    named: `cluster-faces`, and `index` after an index that recorded a photo."""
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

