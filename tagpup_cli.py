# tagpup_cli.py
import contextlib
import functools
import os
import sys
import json
import logging
from typing import List
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
from tagpup.core import paths
from tagpup.store import db as tagpup_db
from tagpup import config as tagpup_config
from tagpup import runtime as runtimes
from tagpup.runtime import Runtime
from tagpup.services import settings as library_settings
from tagpup.services import libraries as library_actions
from tagpup.services import faces as face_records
from tagpup.services import identities
from tagpup.services import damaged_photos
from tagpup.core import runs as run_tags
from tagpup.services import journal as library_journal
from tagpup.services import snapshots as library_snapshots
from tagpup.services import roots as library_roots
from tagpup.core.result import NotFound
from tagpup.services import tagging
from tagpup.services import maintenance
from tagpup.jobs import indexing as indexing_jobs
from tagpup.services.search import PhotoIndex, stored_mismatch
from tagpup.services.suggester import TagSuggester
from tagpup.store.locks import PathLocker
from tagpup.store import faces as store_faces
from tagpup.store import taxonomy as store_taxonomy
from tagpup.core import suggesting
from tagpup.files import images as image_files
from tagpup.core import library as libraries
from tagpup.core.library import Library

def get_runtime(read_only=False):
    """The models a command runs (tagpup.runtime), from the settings of the library it is
    given: each is built the first time the command asks for it, and loaded the first
    time it is used. `read_only` for a command that only looks (stats, list-index,
    search): it reads the library's settings without stamping one that holds none, as
    the MCP server's inspections and the doctor do -- a look was a journaled change."""
    return Runtime(read_only=read_only)

def _stamp(path):
    """(mtime, size) of the file now, or None when it cannot be read."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime, stat.st_size)

def library_index(runtime, db_path, read_only=False):
    """The library's photos, with their vectors under its CLIP model. `read_only` for a
    command that only looks: the library is not migrated (docs/findings.md, #243)."""
    return PhotoIndex(db_path=db_path, model=runtime.model_key(Library(db_path)), read_only=read_only)

def default_suggestions_file(db_path):
    """Where `suggest` writes when not told: beside the library, named for it, as the
    app's own files are. It was suggestions.json in whatever folder the command was run
    from, which left one at the checkout's root (docs/findings.md, #102)."""
    folder = os.path.dirname(os.path.abspath(db_path))
    return os.path.join(folder, os.path.splitext(os.path.basename(db_path))[0] + "_suggestions.json")

def say_if_behind(photo_index):
    """Tell the person a library a look did not migrate is behind this version of TagPup."""
    if photo_index.behind:
        console.print(f"[yellow]This library has not had {len(photo_index.behind)} of this version's"
                      " migrations; a look does not apply them. Indexing it, or opening it in TagPup,"
                      " brings it up to date.[/yellow]")

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

def scan_for_images(dir_path: str) -> List[str]:
    """Recursively scan directory for image files, in the form the index stores.

    Walked from paths.stored(dir_path), not from the string as typed: os.walk joins
    onto whatever it is given, so a folder typed as D:/Photos produced
    "D:/Photos\\a.jpg" -- both separators in one row -- and a relative folder
    produced relative rows.
    """
    return image_files.photos_under(dir_path)

def _outcome_text(outcome):
    if outcome.error is not None:
        return "failed: %s" % outcome.error
    result = outcome.result
    text = "%s, %d changed" % ("done" if result.ok else "failed: %s" % result.message(), result.changed)
    for what, why in result.skipped:
        text += "; %s skipped: %s" % (what, why)
    return text


@click.group()
@click.option("--db", type=str, help="The library to work on: a name in the data folder (e.g. 'my_photos') or a path. Required.")
@click.option("--test", is_flag=True, help="Use test database paths to avoid cluttering production index.")
@click.pass_context
def cli(ctx, db, test):
    """TagpupCLI: AI-powered local photo tagging command-line interface."""
    ctx.ensure_object(dict)
    ctx.obj["test"] = test
    ctx.obj["db"] = db

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
            except Exception as e:
                console.print(f"[bold red]Failed to delete {db_path}: {e}[/bold red]")
        if kept_settings is not None and not os.path.exists(db_path):
            library_settings.stamp(Library(db_path), kept_settings, operation=library_settings.FROM_REPLACED)

    settings = runtime.settings(Library(db_path))
    exiftool_path = runtimes.exiftool(Library(db_path), settings)
    model_name = settings.embedder["model_name"]

    # Setup / Load components
    photo_index = library_index(runtime, db_path)
    photo_index.load()

    # The library's vectors against the length this model makes (tagpup.services.search.stored_mismatch).
    mismatch = stored_mismatch(photo_index, model_name)
    if mismatch:
        console.print(f"[yellow]Warning: Index dimensionality ({mismatch[0]}) does not match current model {model_name} dimensionality ({mismatch[1]}). Clearing cached photo embeddings to reindex with the new model...[/yellow]")
        try:
            photo_index.clear_clip_embeddings()
        except Exception as e:
            console.print(f"[bold red]Failed to clear CLIP embeddings: {e}[/bold red]")

    taxonomy = TagTaxonomy(db_path)
    taxonomy.load()

    embeddings = runtime.embeddings(Library(db_path), photo_index)

    all_images, seen_images = [], set()
    for directory in directories:
        console.print(f"[bold cyan]Scanning directory:[/bold cyan] {directory}")
        for found in (image_files.photos_in(directory) if no_subfolders else scan_for_images(directory)):
            if paths.key(found) not in seen_images:
                seen_images.add(paths.key(found))
                all_images.append(found)
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
    existing_entries = {paths.key(meta["path"]): meta for meta in photo_index.records()}
    images_to_process = []
    skipped_count = 0

    if force_reembed:
        images_to_process = all_images
    else:
        for path in all_images:
            saved = existing_entries.get(paths.key(path))
            if saved is not None:
                try:
                    stat = os.stat(path)
                    if (saved.get("mtime") == stat.st_mtime and 
                        saved.get("size") == stat.st_size and 
                        saved.get("has_embedding", False)):
                        skipped_count += 1
                        continue
                except Exception:
                    pass
            images_to_process.append(path)

    # A photo found damaged before, and unchanged since, is not read again: it would fail
    # again (docs/findings.md, #407). Replaced or changed, it is read at once.
    # --force-reembed reads them again all the same.
    damaged_before = damaged_photos.records(Library(db_path))
    passed_over = {} if force_reembed else damaged_photos.unreadable(damaged_before)
    if passed_over:
        still = []
        for path in images_to_process:
            stamp_then = passed_over.get(paths.key(path))
            if stamp_then is not None and damaged_photos.describes(stamp_then, _stamp(path)):
                continue
            still.append(path)
        if len(still) < len(images_to_process):
            console.print(f"[yellow]Passed over {len(images_to_process) - len(still)} photo(s) found damaged before "
                          f"and unchanged since; restore them from a backup (the Activity page lists them).[/yellow]")
        images_to_process = still

    # A photo whose faces are still to be detected -- indexed from a damaged file, whole
    # now -- is indexed even with a vector: Suggest may have made one since
    # (tagpup.store.faces_pending).
    if not skip_faces:
        chosen = {paths.key(path) for path in images_to_process}
        again = []
        for path in damaged_photos.faces_to_detect(Library(db_path)):
            if paths.key(path) in chosen or not os.path.exists(path):
                continue
            if any(paths.same(os.path.dirname(path), directory)
                   or (not no_subfolders and paths.is_under(path, directory)) for directory in directories):
                again.append(path)
        if again:
            console.print(f"[cyan]Detecting the faces of {len(again)} photo(s) again: each was indexed from a "
                          f"damaged copy.[/cyan]")
            images_to_process += again
            skipped_count -= len(again)

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
    to_index_meta = all_metadata
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
    # The indexer records what it reads, so it may give a photo its identity -- after the
    # photo's picture has decoded in full, never before (IdentityWriter).
    identity_writer = IdentityWriter(exiftool_path)
    unreadable, incomplete = [], []
    # Recorded as found (tagpup.services.damaged_photos), each with the stamp its file had
    # when it was read -- and only a file that kept it while it was read: one still being
    # copied is read again when it has settled. A photo recorded before and read whole now
    # is forgotten.
    recorded_before = {paths.key(each.path): each for each in damaged_before}
    found_by = (run_tags.current() or (None,))[-1]

    def found_damaged(path, stamp, kind, detail, zeros):
        if stamp is None or _stamp(path) != stamp:
            logger.info(f"{path} changed while it was read; it is read again once it has settled.")
            return
        damaged_photos.remember(Library(db_path), [(path, stamp, kind, detail, zeros)], run=found_by)
    
    try:
        # Generate Embeddings with incremental saving (batches of 100) to protect against halts/crashes
        console.print("[bold cyan]Generating embeddings...[/bold cyan]")
        batch_embeddings = []
        batch_metas = []
        batch_faces = {}  # Map path -> faces list
        total_new_indexed = 0
        locked_out = []

        for meta in tqdm(to_index_meta, desc="Generating embeddings"):
            path = meta["path"]
            
            # Acquire path-level lock to prevent duplicate concurrent work.
            # A skip here means another live process has the photo. It used to be
            # silent, which is how 348 photos held by long-dead runs stayed out of
            # the index for three months without anyone noticing.
            if not locker.acquire(path):
                locked_out.append(path)
                continue
                
            try:
                read = {}
                stamp = _stamp(path)
                try:
                    # Reads the file once and decodes the whole picture; a vector kept from
                    # the file as it is was made by such a decode.
                    # A photo recorded damaged is embedded again whatever vector is kept: it
                    # may have been made from the damaged file, its stamp restored since.
                    emb = embeddings.of(path, force_recompute=force_reembed or paths.key(path) in recorded_before,
                                        seen=lambda picture: read.update(
                        zeros=picture.info.get(image_files.ZERO_TAIL_INFO, 0)))
                except image_files.Unreadable as damaged:
                    # Nothing is written into it: no identity, no row.
                    unreadable.append((path, damaged))
                    logger.info(f"Not indexed, and not written to: {path} does not decode: {damaged}")
                    found_damaged(path, stamp, damaged.kind, damaged.detail, damaged.zero_tail)
                    locker.release(path)
                    continue
                zeros = read["zeros"] if "zeros" in read else image_files.zero_tail_of(path)
                if zeros >= image_files.ZERO_TAIL:
                    # It decodes, perhaps grey below a line: indexed, and left as it is.
                    incomplete.append((path, zeros))
                    logger.info(f"Possibly an incomplete copy, indexed and not written to: {path} ends in "
                                f"{zeros} zero bytes")
                    found_damaged(path, stamp, damaged_photos.INCOMPLETE,
                                  "the last %d bytes are zeros" % zeros, zeros)
                else:
                    # Decoded: only now may the photo be written to. The row recorded below
                    # takes the stamp the write left, and the vector with it.
                    identity_writer.give(meta, decoded=True)
                    if paths.key(path) in recorded_before:
                        # Read whole now: forgotten, and what was made of the damaged file
                        # taken away -- its vectors, its faces unless decided -- before this
                        # run records the photo afresh.
                        damaged_photos.forget_to_reindex(Library(db_path), [recorded_before[paths.key(path)]])
                
                # Extract and save face embeddings in the same pass (cached in memory until parent photo is saved)
                if face_processor:
                    faces = face_processor.detect_and_embed_faces(path)
                    batch_faces[path] = faces
                
                # A photo already indexed is not removed first: build_or_update
                # updates its row in place. Deleting the row cascaded to its faces
                # and took every name given by hand, and every exclusion, with it.

                batch_embeddings.append(emb)
                batch_metas.append(meta)
                
                # Learn new tags into taxonomy
                taxonomy.add_tags(meta["tags"])
                # Not add_tags: people[] holds leaf names, and treating a leaf as a
                # whole path mints a bare root node beside the People/<name> that
                # already exists. This runs on every index, so it undid every cleanup.
                taxonomy.add_people(meta["people"])
                
                # Save progress incrementally in batches of 100
                if len(batch_embeddings) >= 100:
                    photo_index.build_or_update(batch_embeddings, batch_metas, dim=len(batch_embeddings[0]), reload=False)
                    
                    # Save faces for the batch in a single transaction
                    if batch_faces:
                        # --force-reembed means redo the work; without it the
                        # existing face rows, and the curation on them, are kept.
                        face_records.record_batch(photo_index.conn, batch_faces, overwrite=force_reembed)
                        batch_faces.clear()
                    
                    taxonomy.save()
                    
                    # Release locks for saved images
                    for saved_meta in batch_metas:
                        locker.release(saved_meta["path"])
                        
                    total_new_indexed += len(batch_embeddings)
                    batch_embeddings = []
                    batch_metas = []
            except Exception as e:
                logger.error(f"Error indexing {path}: {e}")
                locker.release(path)

        # Rebuild or update the FAISS index for the final batch
        if batch_embeddings:
            photo_index.build_or_update(batch_embeddings, batch_metas, dim=len(batch_embeddings[0]), reload=True)
            
            # Save the remaining face embeddings
            if batch_faces:
                face_records.record_batch(photo_index.conn, batch_faces, overwrite=force_reembed)
                batch_faces.clear()
            
            taxonomy.save()
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
        identity_writer.close()
        locker.release_all()
        photo_index.close()

    # Print taxonomy stats
    roots = taxonomy.get_root_categories()
    if roots:
        console.print("\n[bold]Root categories detected in library:[/bold]")
        for root, count in sorted(roots.items(), key=lambda x: -x[1]):
            console.print(f"  • {root}: {count} path(s)")

@cli.command()
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option("--k", default=15, help="Number of nearest neighbors to consider.")
@click.option("--min-sim", default=0.35, type=float, help="Cosine similarity cutoff.")
@click.option("--output", default=None,
              help="Path to write the suggestions JSON file (default: <library>_suggestions.json beside the library).")
@click.option("--add", "add_folder", is_flag=True,
              help="Add the folder to the library first when it does not hold it: each photo gets its row, as "
                   "TagPup's Add does. Without it, a folder the library does not hold is refused.")
@click.pass_context
def suggest(ctx, directory: str, k: int, min_sim: float, output: str, add_folder: bool = False):
    """Phase 2: Suggest tags for untagged photos."""
    runtime = get_runtime()

    # Load Index & Taxonomy
    test_mode = ctx.obj.get("test", False)
    cli_db = ctx.obj.get("db")
    db_path = get_db_path(test_mode, cli_db)
    output = output or default_suggestions_file(db_path)
    library = Library(db_path)
    # Suggest records faces and vectors for every photo it looks at, each on its row: only
    # in the folders the library holds (tagpup.services.libraries.not_in).
    settings = runtime.settings(library)
    refusal = library_actions.not_in(library, directory, settings.ignored)
    if refusal and not add_folder:
        console.print(f"[bold red]Error:[/bold red] {refusal} (--add adds it; `index` adds and reads it)")
        ctx.exit(1)
    if refusal:
        library_actions.record_added(library, [directory])
        console.print(f"Added the folder to {library.name}; `index` reads its photos into it.")
    model_name = settings.embedder["model_name"]

    photo_index = library_index(runtime, db_path)
    if not photo_index.load():
        console.print("[bold red]Error:[/bold red] No photo index found. Please run 'index' first.")
        return
    
    try:
        # The library's vectors against the length this model makes (tagpup.services.search.stored_mismatch).
        mismatch = stored_mismatch(photo_index, model_name)
        if mismatch:
            console.print(f"[bold red]Error:[/bold red] Index dimensionality ({mismatch[0]}) does not match current model {model_name} dimensionality ({mismatch[1]}). Please run 'index' first to rebuild the index using the new model.")
            return

        taxonomy = TagTaxonomy(db_path)
        taxonomy.load()

        # The library's words and the tree's, but no one's name (tagpup.core.suggesting).
        candidate_tags = suggesting.zero_shot_words(settings.candidate_words, taxonomy.paths, taxonomy.people_roots())

        embeddings = runtime.embeddings(library, photo_index)
        suggester = TagSuggester(photo_index, taxonomy, embedder=runtime.clip(library, settings),
                                 candidate_tags=candidate_tags, faces=runtime.faces(library, settings))

        # Scan untagged photos
        console.print(f"[bold cyan]Scanning directory for untagged photos:[/bold cyan] {directory}")
        all_images = scan_for_images(directory)
        console.print(f"Found {len(all_images)} image(s) total.")

        if not all_images:
            console.print("[yellow]No supported images found. Exiting.[/yellow]")
            return

        # Batch read metadata for all untagged images
        console.print(f"[bold cyan]Reading metadata for {len(all_images)} image(s)...[/bold cyan]")
        exiftool_path = runtimes.exiftool(library, settings)
        extractor = MetadataExtractor(exiftool_path=exiftool_path)
        
        batch_size = 500
        metadata_map = {}
        from tqdm import tqdm
        for i in tqdm(range(0, len(all_images), batch_size), desc="Reading metadata"):
            batch = all_images[i:i+batch_size]
            batch_meta = extractor.batch_read(batch, people=store_taxonomy.people_vocabulary(db_path))
            for meta in batch_meta:
                metadata_map[meta["path"]] = meta

        # Process each untagged image
        console.print("[bold cyan]Generating suggestions...[/bold cyan]")
        suggestions_output = []
        
        for path in tqdm(all_images, desc="Generating suggestions"):
            try:
                emb = embeddings.of(path)
                meta = metadata_map.get(path)
                sugg = suggester.suggest_for_photo(path, emb, k=k, min_sim=min_sim, target_metadata=meta)
                suggestions_output.append(sugg)
            except Exception as e:
                logger.error(f"Error processing {path}: {e}")

        # Apply folder consensus post-processing to boost/decay scores
        console.print("[bold cyan]Applying event-level folder consensus...[/bold cyan]")
        suggestions_output = suggester.apply_folder_consensus(suggestions_output)

        import numpy as np

        def convert_numpy_types(obj):
            if isinstance(obj, dict):
                return {k: convert_numpy_types(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_numpy_types(item) for item in obj]
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            elif isinstance(obj, np.integer):
                return int(obj)
            elif isinstance(obj, np.floating):
                return float(obj)
            elif isinstance(obj, np.bool_):
                return bool(obj)
            elif isinstance(obj, np.complexfloating):
                return complex(obj)
            elif isinstance(obj, np.float32):
                return float(obj)
            elif isinstance(obj, np.float64):
                return float(obj)
            elif isinstance(obj, np.int32):
                return int(obj)
            elif isinstance(obj, np.int64):
                return int(obj)
            # Handle any other numpy scalar types that might not be caught above
            elif hasattr(obj, 'dtype') and hasattr(obj, 'item'):
                # This catches numpy scalars like np.float32, np.int32 etc.
                try:
                    return obj.item()
                except:
                    return obj
            return obj

        # Write suggestions to JSON
        try:
            # Convert all numpy types to native Python types
            suggestions_converted = convert_numpy_types(suggestions_output)

            with open(output, "w", encoding="utf-8") as f:
                json.dump(suggestions_converted, f, indent=2)
            console.print(f"[bold green]Suggestions successfully written to {output}[/bold green]\n")
            
            # Display a summary table
            if suggestions_converted:
                table = Table(title="Generated Tag Suggestions Summary")
                table.add_column("Photo File", style="green")
                table.add_column("Top Suggested Tags (Confidence)", style="magenta")
                table.add_column("Nearest Neighbors (Similarity)", style="cyan")

                for sugg in suggestions_converted:
                    # Format suggested tags, limiting to top 5 for neatness
                    # Appends '*' for tags that are new recommendations
                    tags = sugg.get("suggested_tags", [])
                    tags_str = ", ".join([f"{t['tag']}{'*' if t.get('is_new_recommendation') else ''} ({t['score']:.2f})" for t in tags[:5]])
                    if len(tags) > 5:
                        tags_str += f" (+{len(tags) - 5} more)"
                    if not tags:
                        tags_str = "[yellow]No tags suggested[/yellow]"

                    # Format closest match
                    neighbors = sugg.get("nearest_neighbors", [])
                    neighbors_str = ", ".join([f"{os.path.basename(n['path'])} ({n['similarity']:.2f})" for n in neighbors[:2]])
                    if not neighbors:
                        neighbors_str = "[yellow]None[/yellow]"

                    table.add_row(
                        os.path.basename(sugg["path"]),
                        tags_str,
                        neighbors_str
                    )
                console.print(table)
        except Exception as e:
            console.print(f"[bold red]Error writing suggestions to disk: {e}[/bold red]")
    finally:
        photo_index.close()

@cli.command()
@click.argument("suggestions_file", type=click.Path(exists=True, dir_okay=False))
@click.option("-Live", "live", is_flag=True, help="Write tags to files for real (modifies files).")
@click.option("-MinScore", "min_score", default=suggesting.OFFER_A_TAG, type=float,
              help="Write tags at or above this score (default: the value the app shows them from).")
@click.option("--nobackup", is_flag=True,
              help="Kept for scripts that pass it: a write keeps no _original copies; it is one change, which undo reverses.")
@click.pass_context
def write(ctx, suggestions_file: str, live: bool, min_score: float, nobackup: bool):
    """Phase 3: Write suggested tags back to photos using ExifTool."""
    # The library: its taxonomy files people, and its index is told what was written.
    db_path = get_db_path(ctx.obj.get("test", False), ctx.obj.get("db"))
    exiftool_path = get_exiftool_path(db_path)

    write_suggestions_file(suggestions_file, db_path, exiftool_path, live=live,
                           min_score=min_score, nobackup=nobackup)


#: What `write` logs, under the name scripts/writer.py logged it.
writer_log = logging.getLogger("tagpup_cli.writer")


def write_suggestions_file(suggestions_file, db_path, exiftool_path, live=False,
                           min_score=suggesting.OFFER_A_TAG, nobackup=False) -> bool:
    """`write`: read the suggestions file, show what would be written, and with `live`
    and a typed YES write it (tagpup.services.tagging.write_suggestions). True when
    nothing failed.

    `db_path` is the library: people are filed by its tree, and its index is told what
    was written.
    """
    if not os.path.exists(suggestions_file):
        writer_log.error(f"Suggestions file not found: {suggestions_file}")
        return False

    try:
        with open(suggestions_file, "r", encoding="utf-8") as f:
            suggestions = json.load(f)
    except Exception as e:
        writer_log.error(f"Error loading suggestions file: {e}")
        return False

    if not isinstance(suggestions, list):
        # Might be a single entry wrapped or just invalid
        if isinstance(suggestions, dict):
            suggestions = [suggestions]
        else:
            writer_log.error("Invalid suggestions.json format. Expected array of objects.")
            return False

    library = Library(db_path)
    write_tasks, missing = tagging.suggestion_writes(library, suggestions, min_score)
    for path in missing:
        writer_log.warning(f"File path does not exist, skipping: {path}")

    if not write_tasks:
        print("No tags or captions met the minimum score threshold to be written.")
        return True

    # Print summary/preview
    print("\n--- Tag & Caption Writing Preview ---")
    for path, tags, caption in write_tasks:
        print(f"File: {path}")
        if tags:
            print(f"  Tags to append: {', '.join(tags)}")
        if caption:
            print(f"  Caption to set: \"{caption}\"")
    print(f"Total files to modify: {len(write_tasks)}")
    print(f"Write Mode: {'LIVE (files will be modified)' if live else 'PREVIEW (dry-run, no files changed)'}")
    print("-------------------------------------")

    if not live:
        print("To write these tags and captions for real, run with the -Live flag.")
        return True

    # Ask for confirmation
    confirm = input("Type 'YES' to confirm and write metadata to files: ").strip()
    if confirm != "YES":
        print("Aborted. No files were modified.")
        return False

    print("Writing metadata...")
    executable = exiftool_path
    if executable and not os.path.isabs(executable):
        executable = os.path.abspath(executable)

    try:
        result = tagging.write_suggestions(library, write_tasks, executable, nobackup=nobackup)
    except Exception as e:
        writer_log.error(f"ExifTool writer error: {e}", exc_info=True)
        return False
    if result.refused:
        # Nothing was written: a folder the library does not hold, say.
        raise click.ClickException(result.refused)
    for path, error in result.errors:
        writer_log.error(f"Failed to write metadata to {path}: {error}")

    print(f"Finished writing metadata. Success: {result.changed}, Errors: {len(result.errors)}")
    skipped = result.details.get(library_actions.SKIPPED_DAMAGED, 0)
    if skipped:
        print(f"Skipped {skipped} photo(s) found damaged; nothing was written to them. Restore them from a backup:")
        for path, why in result.skipped[-skipped:]:
            print(f"  {path}: {why}")
    change = result.details.get("change")
    if change:
        print(f"Recorded as change {change}; `undo {change}` shows what undoing it would put back.")
    return not result.errors

@cli.command()
@click.argument("query")
@click.option("--k", default=10, help="Number of results to return.")
@click.pass_context
def search(ctx, query: str, k: int):
    """Semantic text search across indexed library."""
    runtime = get_runtime(read_only=True)

    # Load Index
    test_mode = ctx.obj.get("test", False)
    cli_db = ctx.obj.get("db")
    db_path = get_db_path(test_mode, cli_db)
    model_name = runtime.settings(Library(db_path)).embedder["model_name"]
    photo_index = library_index(runtime, db_path, read_only=True)
    loaded = photo_index.load()
    say_if_behind(photo_index)
    if not loaded:
        console.print("[bold red]Error:[/bold red] No photo index found. Please run 'index' first.")
        return
        
    try:
        # The library's vectors against the length this model makes (tagpup.services.search.stored_mismatch).
        mismatch = stored_mismatch(photo_index, model_name)
        if mismatch:
            console.print(f"[bold red]Error:[/bold red] Index dimensionality ({mismatch[0]}) does not match current model {model_name} dimensionality ({mismatch[1]}). Please run 'index' first to rebuild the index using the new model.")
            return

        # Embed text query
        console.print(f"Embedding query: '[bold yellow]{query}[/bold yellow]'")
        query_vector = runtime.clip(Library(db_path)).embed_text(query)

        # Perform search
        results = photo_index.search(query_vector, k=k)

        if not results:
            console.print("[yellow]No matches found.[/yellow]")
            return

        # Print results
        table = Table(title=f"Search Results for '{query}'")
        table.add_column("Similarity", justify="right", style="cyan")
        table.add_column("Photo Path", style="green")
        table.add_column("Existing Tags", style="magenta")

        for sim, meta in results:
            tags_str = ", ".join(meta.get("tags", []) + meta.get("people", []))
            table.add_row(f"{sim:.3f}", meta["path"], tags_str)

        console.print(table)
    finally:
        photo_index.close()

@cli.command()
@click.pass_context
def stats(ctx):
    """Index statistics (tag counts, people, coverage)."""
    runtime = get_runtime(read_only=True)

    test_mode = ctx.obj.get("test", False)
    cli_db = ctx.obj.get("db")
    db_path = get_db_path(test_mode, cli_db)

    photo_index = library_index(runtime, db_path, read_only=True)
    loaded = photo_index.load()
    say_if_behind(photo_index)
    if not loaded:
        console.print("[bold red]Error:[/bold red] No photo index found. Please run 'index' first.")
        return
        
    try:
        taxonomy = TagTaxonomy(db_path)
        taxonomy.load()

        records = photo_index.records()
        total_indexed = len(records)
        
        # Tag and people distribution
        tag_counts = {}
        people_counts = {}
        
        for meta in records:
            for tag in meta.get("tags", []):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            for person in meta.get("people", []):
                people_counts[person] = people_counts.get(person, 0) + 1

        console.print("\n[bold underline]Index Statistics[/bold underline]")
        console.print(f"Total Indexed Photos: [bold green]{total_indexed}[/bold green]")
        console.print(f"Unique Tags Found: {len(tag_counts)}")
        console.print(f"Unique People Tagged: {len(people_counts)}")
        console.print(f"Total Taxonomy Paths: {len(taxonomy.paths)}")

        # Display Top Tags
        if tag_counts:
            top_tags_table = Table(title="Top 10 Tags")
            top_tags_table.add_column("Tag", style="magenta")
            top_tags_table.add_column("Count", justify="right", style="cyan")
            for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1])[:10]:
                top_tags_table.add_row(tag, str(count))
            console.print(top_tags_table)

        # Display Top People
        if people_counts:
            top_people_table = Table(title="Top 10 People")
            top_people_table.add_column("Person", style="green")
            top_people_table.add_column("Count", justify="right", style="cyan")
            for person, count in sorted(people_counts.items(), key=lambda x: -x[1])[:10]:
                top_people_table.add_row(person, str(count))
            console.print(top_people_table)

        # Root Taxonomy Stats
        roots = taxonomy.get_root_categories()
        if roots:
            roots_table = Table(title="Taxonomy Roots Coverage")
            roots_table.add_column("Root Category", style="yellow")
            roots_table.add_column("Path Count", justify="right", style="cyan")
            for root, count in sorted(roots.items(), key=lambda x: -x[1]):
                roots_table.add_row(root, str(count))
            console.print(roots_table)
    finally:
        photo_index.close()

@cli.command("export-tree")
@click.argument("output", type=click.Path(dir_okay=False))
@click.pass_context
def export_tree(ctx, output: str):
    """Write the library's tag tree to OUTPUT as JSON: a copy to keep or read. The tree
    itself lives in the library."""
    db_path = get_db_path(ctx.obj.get("test", False), ctx.obj.get("db"))
    if not os.path.exists(db_path):
        raise click.ClickException("There is no library at %s." % db_path)
    count = store_taxonomy.export_json(db_path, output)
    console.print(f"Wrote {count} tag(s) to [bold cyan]{output}[/bold cyan].")


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
    undoable with `undo` until it is pruned."""
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
    if change_id is not None:
        entry = found["changes"][0]
        for table, keys in sorted(entry.get("keys", {}).items()):
            console.print("  %s: %s" % (table, ", ".join("/".join(str(k) for k in key) for key in keys)),
                          markup=False)
        for row in entry.get("values", []):
            console.print("  %s %s %s: %s -> %s" % (row["action"], row["table"], "/".join(str(k) for k in row["key"]),
                                                   row["old"], row["new"]), markup=False)
    console.print("Changes stay undoable for %d days." % found["retention_days"])


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


@cli.command("prune-journal")
@click.option("--days", default=library_journal.RETENTION_DAYS, type=int, show_default=True,
              help="Changes older than this lose their values and can no longer be undone.")
@click.option("--apply", "apply_", is_flag=True, help="Prune. Without it, only says what would go.")
@click.pass_context
def prune_journal(ctx, days, apply_):
    """Let old changes go: each keeps its summary, and loses the values an undo needs."""
    library = _existing_library(ctx)
    result = library_journal.prune(library, days, apply=apply_)
    if not apply_:
        console.print("%d change(s) older than %d days would be pruned (%d value(s)). --apply prunes them."
                      % (result.attempted, days, result.details["values"]))
        return
    console.print("Pruned %d change(s), %d value(s)." % (result.changed, result.details["values"]))


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
    if counts["review_folders"]:
        console.print("  %d folder(s) under the library's roots hold %d photo(s) and no indexed one: to review in"
                      " TagTuner (gear, Folders to review), never indexed on their own."
                      % (counts["review_folders"], counts["review_photos"]))
    if counts["missing"]:
        console.print("  %d folder(s) wholly gone, %d of them a whole root (an unplugged drive looks the same);"
                      " their rows are kept." % (counts["folders_gone"], counts["roots_gone"]))
    if counts["unreadable"]:
        console.print("  %d changed file(s) could not be read." % counts["unreadable"])
    if counts.get("unreadable_files"):
        console.print("  %d photo(s) found damaged before, unchanged since, passed over: restore them from a"
                      " backup (the Activity page lists them)." % counts["unreadable_files"])
    if not apply_:
        console.print(maintenance.rehearsed(result), markup=False, soft_wrap=True)
        console.print("In step." if result.details["in_step"] else "Nothing changed. --apply brings it in step.")
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
    console.print("In step." if result.details["in_step"] else "Not yet in step: sync again once indexing is done.")
    if result.errors:
        raise SystemExit(1)
def _job_libraries(ctx):
    """The library --db names, or every library in the data folder."""
    if ctx.obj.get("db"):
        return [_existing_library(ctx)]
    return runtimes.home_libraries(ctx.obj.get("test", False))


@cli.group("settings", invoke_without_command=True)
@click.pass_context
def settings_command(ctx):
    """The library's settings (tagpup.services.settings): each key and its value, read
    without stamping the library. `settings set KEY VALUE` changes one."""
    if ctx.invoked_subcommand is not None:
        return
    library = _existing_library(ctx)
    found = runtimes.peek_settings(library)
    for key, value in found.values.items():
        console.print("%s = %s" % (key, value.replace(chr(10), " | ")), markup=False, soft_wrap=True)


@settings_command.command("set")
@click.argument("key")
@click.argument("value")
@click.option("--acknowledge", "acknowledged", multiple=True,
              help="A locked group whose consequences you accept (clip, faces, exiftool).")
@click.option("--apply", "apply_", is_flag=True, help="Write the change. Without it, only says what it would change.")
@click.pass_context
def settings_set(ctx, key, value, acknowledged, apply_):
    """Change the setting KEY to VALUE as one journaled change, undoable (`history`,
    `undo`). Folders (library.roots, library.ignored) are given one a line, or separated
    by |. Setting the roots also ignores every folder under a new root that holds photos
    and none indexed. A dry run unless --apply."""
    library = _existing_library(ctx)
    if key in (library_settings.ROOTS, library_settings.IGNORED):
        value = chr(10).join(part.strip() for part in value.replace("|", chr(10)).split(chr(10)) if part.strip())
    result = library_settings.change(library, {key: value}, acknowledged=list(acknowledged), apply=apply_)
    if result.refused:
        console.print("Refused: %s" % result.refused, markup=False, soft_wrap=True)
        raise SystemExit(1)
    if key == library_settings.ROOTS:
        # Taken, but said: a root on a drive not plugged in now is walked when it is back.
        for root in value.split(chr(10)):
            if root and not os.path.isdir(root):
                console.print("Warning: %s is not on disk now; sync finds nothing under it until it is." % root,
                              markup=False, soft_wrap=True)
    changed = result.details.get("changed", [])
    added = result.details.get("ignored_added", 0)
    if not changed:
        console.print("%s already holds that value; nothing to change." % key, markup=False)
        return
    also = " and %d folder(s) under the new roots added to library.ignored" % added if added else ""
    if not apply_:
        console.print("Would change %s%s. Nothing changed. --apply writes it." % (", ".join(changed), also),
                      markup=False, soft_wrap=True)
        if result.details.get("behind"):
            console.print("The library is behind this version by %d migration(s); --apply brings it up to date "
                          "first." % result.details["behind"], markup=False)
        return
    console.print("Changed %s%s. %s" % (", ".join(changed), also, maintenance.recorded(result, library.path)),
                  markup=False, soft_wrap=True)


def _machine():
    """This machine's map of the roots (machine_roots.json in the TagPup home), handed to the
    service: tagpup.services does not import tagpup.config."""
    return library_roots.Machine(tagpup_config.machine_roots, tagpup_config.add_machine_root,
                                 tagpup_config.machine_roots_path)


@cli.group("roots", invoke_without_command=True)
@click.pass_context
def roots_command(ctx):
    """The library's roots (tagpup.services.roots): the places its photos' paths are held
    relative to, and where this machine keeps each. `roots adopt` adds one and converts the
    paths under it -- a dry run unless --apply; nothing converts a library unasked."""
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


def _say_conversion(report):
    for table, counts in report["tables"].items():
        parts = ["%d row(s)" % counts["rows"], "%d to convert" % counts["convert"]]
        for key, label in (("already", "already rooted"), ("outside", "under no root (kept as they are)"),
                           ("respelled", "taking the location's spelling (same file)"),
                           ("duplicates", "already two rows of one file (merge them; they do not block)"),
                           ("share_spelled", "spelled by the share's address (REFUSED, see below)"),
                           ("irreversible", "NOT reversible or not an absolute path (REFUSED)"),
                           ("json", "with a path inside their JSON")):
            if counts[key]:
                parts.append("%d %s" % (counts[key], label))
        console.print("  %s: %s" % (table, ", ".join(parts)), markup=False, soft_wrap=True)
    if report["settings"]:
        console.print("  settings it rewrites: %s" % ", ".join(report["settings"]), markup=False)
    if report["share_spelled"]["rows"]:
        console.print("  rows spelled by the share's address, by folder (converting them would retarget them from "
                      "the master, the share, to this machine's copy; fix their spelling first, or adopt a root "
                      "whose location is the share):", markup=False, soft_wrap=True)
        for group in report["share_spelled"]["folders"]:
            console.print("    %6d  %s" % (group["count"], group["group"]), markup=False, soft_wrap=True)
    if report["outside"]:
        console.print("  rows under no root, by folder:", markup=False)
        for group in report["outside"]:
            console.print("    %6d  %s" % (group["count"], group["group"]), markup=False, soft_wrap=True)


@roots_command.command("adopt")
@click.option("--name", required=True, help="The root's name: 1 to 32 characters of a-z, 0-9, _ and -, as in pictures.")
@click.option("--address", default="", help="The share's own address, as in \\\\server\\Pictures\\Pictures.")
@click.option("--location", required=True, help="Where THIS machine keeps the root: a folder that holds the photos.")
@click.option("--apply", "apply_", is_flag=True,
              help="Back the library up, then convert it. Without it, only says what would change.")
@click.pass_context
def roots_adopt(ctx, name, address, location, apply_):
    """Adopt the root NAME for the library: every path under LOCATION becomes the root's name
    and the path under it, in one transaction, recorded as one change that `undo` reverses.
    Writes the machine's map (machine_roots.json) if it lacks the root. Refuses a wrong
    location, a root already adopted, a row that would not convert back. A dry run unless
    --apply."""
    library = _existing_library(ctx)
    # The dry run is always made first and printed in full, the warning with it, BEFORE anything
    # that holds the write lock runs: --apply tells the owner what it is about to do, then does it.
    result = library_roots.adopt(library, name, address, location, _machine(), apply=False)
    report = result.details.get("rehearsal")
    if report:
        console.print("%s root %s at %s:" % ("Would adopt" if not apply_ else "Adopting", report["root"],
                                              report["locations"][0]), markup=False, soft_wrap=True)
        _say_conversion(report)
        console.print("Run this with TagPup and TagTuner stopped: the backup holds the write lock for the length of "
                      "the copy (about %d s for a library this size, %.1f GB); an app writing meanwhile waits, "
                      "and is told why if it gives up.%s" % (
                          report["backup"]["seconds"], report["backup"]["bytes"] / 1e9,
                          " --apply holds the lock for the copy, starting now." if apply_ and not result.refused
                          else ""), markup=False, soft_wrap=True)
    if not result.refused and apply_:
        result = library_roots.adopt(library, name, address, location, _machine(), apply=True)
    if result.refused:
        console.print("Refused: %s" % result.refused, markup=False, soft_wrap=True)
        raise SystemExit(1)
    if not apply_:
        console.print("Nothing changed. --apply %swrites it." % (
            "writes %s and " % result.details["map"]["file"] if result.details["map"]["would_write"] else ""),
            markup=False, soft_wrap=True)
        return
    backup = result.details["backup"]
    console.print("Backup: %s taken first." % os.path.basename(backup["file"]), markup=False)
    if result.details["map"].get("written"):
        console.print("Wrote %s." % result.details["map"]["file"], markup=False, soft_wrap=True)
    console.print("Adopted: %d row(s) converted, change %d. `undo %d` reverses it." % (
        result.changed, result.details["change"], result.details["change"]), markup=False)


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


@cli.group(invoke_without_command=True)
@click.pass_context
def jobs(ctx):
    """The recurring jobs (snapshots, pruning the journal, sync): each one's last run and when
    it is due next, for the library --db names or every library in the data folder. The
    web server runs them; `jobs run NAME` runs one now, by hand."""
    if ctx.invoked_subcommand is not None:
        return
    from tagpup.jobs import recurring
    libraries = _job_libraries(ctx)
    if not libraries:
        console.print("There is no library in %s." % tagpup_config.data_dir(), markup=False)
        return
    table = Table(title="Recurring jobs")
    for column in ("Job", "Period", "Why", "Library", "Last run", "Outcome", "Changed", "Next due"):
        table.add_column(column)
    for library in libraries:
        for entry in recurring.status(library):
            last = entry["last"] or {}
            changed = ", ".join("%s %s" % (value, name) for name, value in sorted((last.get("changed") or {}).items()))
            table.add_row(entry["name"], entry["period"], entry["reason"], library.name, last.get("started") or "never",
                          last.get("outcome") or "", changed, entry["next_due"])
    console.print(table)


@jobs.command("run")
@click.argument("name")
@click.pass_context
def jobs_run(ctx, name):
    """Run the recurring job NAME now, for the library --db names or every library, whether
    or not it is due: unless another process is running it."""
    runner = runtimes.recurring_jobs(Runtime(), libraries=lambda: runtimes.home_libraries(ctx.obj.get("test", False)))
    if runner.registry.get(name) is None:
        raise click.ClickException("There is no recurring job %s; there are %s." % (name, ", ".join(runner.registry.names())))
    library = _existing_library(ctx) if ctx.obj.get("db") else None
    failed = False
    for outcome in runner.run(name, library):
        if outcome.ran:
            console.print("%s for %s: %s" % (name, outcome.library or "every library", _outcome_text(outcome)),
                          markup=False)
            failed = failed or outcome.error is not None or not outcome.result.ok
        else:
            console.print("%s for %s: not run, %s" % (name, outcome.library or "every library", outcome.why),
                          markup=False)
            failed = True
    # A job may have filled this process's index queue (sync's new files): the process
    # ends when this command does, so it waits for them, as `sync --apply` does.
    for each in ([library] if library else runner.libraries()):
        queue = indexing_jobs.queue_for(each)
        if queue.active()["busy"]:
            console.print("Indexing the new files' folders for %s..." % each.name, markup=False)
        queue.wait()
    if failed:
        raise SystemExit(1)


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
        console.print("%s has no snapshots yet; `jobs run snapshots` takes one." % library.name, markup=False)
        return
    table = Table(title="Snapshots of %s" % library.name)
    for column in ("Name", "Taken", "Size", "Changes since"):
        table.add_column(column)
    for snapshot in found["snapshots"]:
        table.add_row(snapshot["name"], snapshot["taken"], "%s MB" % format(snapshot["bytes"] // 1_000_000, ","),
                      str(snapshot["changes_since"]))
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


@cli.command()
@click.argument("photo_path", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def inspect(ctx, photo_path: str):
    """Inspect metadata found in a single image (useful for debugging)."""
    # Who the keywords name depends on the library's taxonomy.
    db_path = get_db_path(ctx.obj.get("test", False), ctx.obj.get("db"))
    exiftool_path = get_exiftool_path(db_path, read_only=True)

    console.print(f"Inspecting file: [bold cyan]{photo_path}[/bold cyan]")
    extractor = MetadataExtractor(exiftool_path=exiftool_path)
    meta = extractor.batch_read([photo_path], people=store_taxonomy.people_vocabulary(db_path))[0]

    console.print("\n[bold underline]Parsed Output[/bold underline]")
    console.print(f"Path: {meta['path']}")
    console.print(f"Tags: {meta['tags']}")
    console.print(f"People: {meta['people']}")
    console.print(f"Captions: {meta['captions']}")

    # Raw metadata output
    raw = meta.get("raw_metadata", {})
    if raw:
        table = Table(title="Raw Read Fields")
        table.add_column("ExifTool Tag", style="yellow")
        table.add_column("Value", style="green")
        for k, v in sorted(raw.items()):
            table.add_row(k, str(v))
        console.print(table)
    else:
        console.print("[yellow]No raw metadata fields read by ExifTool.[/yellow]")

@cli.command("list-index")
@click.option("--folder", default=None, help="Filter results to paths under this directory.")
@click.pass_context
def list_index(ctx, folder):
    """List all photos currently stored in the index, optionally filtered by folder."""
    runtime = get_runtime(read_only=True)

    test_mode = ctx.obj.get("test", False)
    cli_db = ctx.obj.get("db")
    db_path = get_db_path(test_mode, cli_db)

    photo_index = library_index(runtime, db_path, read_only=True)
    loaded = photo_index.load()
    say_if_behind(photo_index)
    if not loaded:
        console.print("[bold red]Error:[/bold red] No photo index found.")
        return
        
    try:
        # Filter paths and gather metadata
        # paths.is_under, not startswith: C:\Photos2 starts with C:\Photos.
        matches = [meta for meta in photo_index.records()
                   if not folder or paths.is_under(meta["path"], folder)]

        if not matches:
            console.print("[yellow]No matching photos found in the index.[/yellow]")
            return

        table = Table(title=f"Indexed Photos Summary ({len(matches)} matches)")
        table.add_column("Photo File", style="green")
        table.add_column("Tags", style="magenta")
        table.add_column("People", style="cyan")
        table.add_column("Caption", style="yellow")

        for meta in sorted(matches, key=lambda x: x["path"]):
            filename = os.path.basename(meta["path"])
            tags_str = ", ".join(meta.get("tags", [])) or "-"
            people_str = ", ".join(meta.get("people", [])) or "-"
            
            captions = meta.get("captions", [])
            caption_str = captions[0] if captions else "-"
            if len(caption_str) > 50:
                caption_str = caption_str[:47] + "..."

            table.add_row(filename, tags_str, people_str, caption_str)

        console.print(table)
    finally:
        photo_index.close()


@cli.command("remove")
@click.option("--path", default=None, help="Remove a specific image path from the index.")
@click.option("--folder", default=None, help="Remove all indexed images under this directory.")
@click.pass_context
def remove(ctx, path, folder):
    """Remove a specific photo or an entire folder of photos from the index."""
    if not path and not folder:
        console.print("[bold red]Error:[/bold red] You must specify either --path or --folder to remove items.")
        return

    runtime = get_runtime()

    test_mode = ctx.obj.get("test", False)
    cli_db = ctx.obj.get("db")
    db_path = get_db_path(test_mode, cli_db)

    photo_index = library_index(runtime, db_path)
    if not photo_index.load():
        console.print("[bold red]Error:[/bold red] No photo index found.")
        return

    try:
        to_remove = set()
        records = photo_index.records() if (path or folder) else []
        if path:
            for meta in records:
                if paths.same(meta["path"], path):
                    to_remove.add(meta["path"])

        if folder:
            # is_under, not startswith: removing C:\Photos must not take C:\Photos2.
            for meta in records:
                if paths.is_under(meta["path"], folder):
                    to_remove.add(meta["path"])

        if not to_remove:
            console.print("[yellow]No matching photos found in the index to remove.[/yellow]")
            return

        console.print(f"[bold yellow]Found {len(to_remove)} photo(s) to remove from the index.[/bold yellow]")
        for p in sorted(list(to_remove)):
            console.print(f"  • {p}")

        confirm = input("Type 'YES' to confirm deletion: ").strip()
        if confirm != "YES":
            console.print("Aborted. No changes were made.")
            return

        photo_index.remove_paths(to_remove)
        console.print("[bold green]Successfully removed the photos from the index.[/bold green]")
    finally:
        photo_index.close()


@cli.command("index-faces")
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option("--force", is_flag=True, help="Force re-detection of faces on already processed images.")
@click.pass_context
def index_faces(ctx, directory: str, force: bool):
    """Scan photos and extract/index face embeddings into the database."""
    runtime = get_runtime()
    test_mode = ctx.obj.get("test", False)
    cli_db = ctx.obj.get("db")
    db_path = get_db_path(test_mode, cli_db)

    photo_index = library_index(runtime, db_path)
    if not photo_index.load():
        console.print("[bold red]Error:[/bold red] No photo index found. Please run 'index' first.")
        return

    try:
        # Find all files in the directory that are already indexed in photos
        console.print(f"[bold cyan]Scanning directory for photos to index faces:[/bold cyan] {directory}")
        all_images = scan_for_images(directory)
        # Compared by key, and each photo carried forward under its row's spelling so
        # the faces recorded for it name the same path its photos row does.
        indexed_paths = {paths.key(meta["path"]): meta["path"] for meta in photo_index.records()}

        target_images = [indexed_paths[paths.key(img)] for img in all_images
                         if paths.key(img) in indexed_paths]
        console.print(f"Found {len(target_images)} photo(s) in directory that are in the photo index.")

        if not target_images:
            console.print("[yellow]No indexed photos found to extract faces from.[/yellow]")
            return

        # Determine which images need processing
        to_process = []
        if force:
            to_process = target_images
        else:
            # Query paths that already have face records in the faces table
            already_processed = {paths.key(p) for p in store_faces.photos_with_faces(photo_index.conn)}
            to_process = [img for img in target_images if paths.key(img) not in already_processed]

        if not to_process:
            console.print("[bold green]All faces are already indexed![/bold green]")
            return

        console.print(f"Extracting face embeddings for [bold yellow]{len(to_process)}[/bold yellow] photo(s)...")
        processor = runtime.faces(Library(db_path))
        
        from tqdm import tqdm
        count_faces = 0
        for path in tqdm(to_process, desc="Detecting and embedding faces"):
            faces = processor.detect_and_embed_faces(path)
            face_records.replace_detected(photo_index.conn, path, faces)
            count_faces += len(faces)

        console.print(f"[bold green]Successfully indexed {count_faces} faces across {len(to_process)} photos.[/bold green]")
    finally:
        photo_index.close()


@cli.command("cluster-faces")
@click.option("--reset", is_flag=True, help="Clear the names clustering gave to faces before clustering again. Names given by hand are kept.")
@click.option("--max-iterations", default=5, type=int, help="Maximum iterations for propagation loop (set to 0 for anchor only).")
@click.pass_context
def cluster_faces(ctx, reset: bool, max_iterations: int):
    """Run self-tuning identity resolution to cluster and name faces using photo tags."""
    runtime = get_runtime()
    test_mode = ctx.obj.get("test", False)
    cli_db = ctx.obj.get("db")
    db_path = get_db_path(test_mode, cli_db)

    photo_index = library_index(runtime, db_path)
    if not photo_index.load():
        console.print("[bold red]Error:[/bold red] No photo index found.")
        return

    if reset:
        try:
            cleared = face_records.clear_automatic_names(photo_index.conn)
            console.print(f"[bold yellow]Cleared {cleared} automatically assigned face name(s); names given by hand are kept.[/bold yellow]")
        except Exception as e:
            console.print(f"[bold red]Failed to reset face assignments: {e}[/bold red]")

    try:
        resolved_stats = identities.resolve(photo_index, max_iterations=max_iterations)
        
        if not resolved_stats:
            console.print("[yellow]No faces were resolved to identities. Try tagging photos with people names first.[/yellow]")
            return

        table = Table(title="Resolved Face Identities Summary")
        table.add_column("Person Name", style="green")
        table.add_column("Faces Linked", justify="right", style="cyan")

        for name, count in sorted(resolved_stats.items(), key=lambda x: -x[1]):
            table.add_row(name, str(count))

        console.print(table)
        console.print("[bold green]Self-tuning identity resolution complete![/bold green]")
    finally:
        photo_index.close()

if __name__ == "__main__":
    cli()

