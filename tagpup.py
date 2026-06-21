# tagpup.py
import os
import sys
import json
import logging
import configparser
from typing import List
import click
from rich.console import Console
from rich.table import Table

# Initialize Rich console and logging
console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("tagpup")

# Suppress verbose faiss loader and huggingface logs
logging.getLogger("faiss.loader").setLevel(logging.WARNING)
logging.getLogger("faiss").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").propagate = False

import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="huggingface_hub")

import os
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

# Add scripts folder to search path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))

# Load components
from metadata import MetadataExtractor
from embedder import ClipEmbedder
from index import PhotoIndex
from taxonomy import TagTaxonomy
from suggester import TagSuggester
from writer import MetadataWriter
from faces import FaceProcessor

# Default ExifTool path (uses local user profile dynamically to avoid hardcoded PII)
DEFAULT_EXIFTOOL_PATH = os.path.join(
    os.environ.get("USERPROFILE", "C:\\Users\\Username"),
    r"AppData\Local\Programs\ExifTool\exiftool.exe"
)

def get_config():
    """Load configuration parameters from config.ini."""
    config = configparser.ConfigParser(interpolation=None)
    config_path = os.path.join(os.path.dirname(__file__), "config.ini")
    
    if os.path.exists(config_path):
        config.read(config_path, encoding='utf-8')
    else:
        # Provide defaults if config doesn't exist
        config.add_section("paths")
        config.set("paths", "exiftool", DEFAULT_EXIFTOOL_PATH)
        config.set("paths", "data_dir", "data")
        config.set("paths", "embedding_cache_dir", "data/embedding_cache")
        config.add_section("model")
        config.set("model", "name", "ViT-B-32")
        config.set("model", "pretrained", "laion2b_s34b_b79k")
        
    return config

def get_exiftool_path(config) -> str:
    """Determine the ExifTool path, checking config, PATH, then default."""
    path = config.get("paths", "exiftool", fallback=DEFAULT_EXIFTOOL_PATH)
    path = os.path.expandvars(path)
    if os.path.exists(path):
        return path
        
    # Check if ExifTool is in the system PATH
    import shutil
    shutil_path = shutil.which("exiftool")
    if shutil_path:
        return shutil_path
        
    return path

def get_db_paths(config, test_mode=False):
    data_dir = config.get("paths", "data_dir", fallback="data")
    prefix = "test_" if test_mode else ""
    return (
        os.path.join(data_dir, f"{prefix}photo_index.db"),
        os.path.join(data_dir, f"{prefix}photo_taxonomy.json")
    )

def scan_for_images(dir_path: str) -> List[str]:
    """Recursively scan directory for image files."""
    valid_exts = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
    images = []
    for root, _, files in os.walk(dir_path):
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in valid_exts:
                images.append(os.path.join(root, file))
    return images

@click.group()
@click.option("--test", is_flag=True, help="Use test database paths to avoid cluttering production index.")
@click.pass_context
def cli(ctx, test):
    """TagPup: AI-powered local photo tagging system."""
    ctx.ensure_object(dict)
    ctx.obj["test"] = test

@cli.command()
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option("--force-reembed", is_flag=True, help="Force recreation of embeddings.")
@click.option("--reset", is_flag=True, help="Delete existing index and taxonomy to start fresh.")
@click.option("--skip-faces", is_flag=True, help="Skip face detection during indexing.")
@click.pass_context
def index(ctx, directory: str, force_reembed: bool, reset: bool, skip_faces: bool):
    """Phase 1: Scan and index a tagged photo library."""
    config = get_config()
    exiftool_path = get_exiftool_path(config)
    cache_dir = config.get("paths", "embedding_cache_dir", fallback="data/embedding_cache")
    model_name = config.get("model", "name", fallback="ViT-B-32")
    pretrained = config.get("model", "pretrained", fallback="laion2b_s34b_b79k")

    test_mode = ctx.obj.get("test", False)
    db_path, tax_path = get_db_paths(config, test_mode)

    # Handle reset flag
    if reset:
        console.print("[bold red]Resetting index (deleting existing index and taxonomy files)...[/bold red]")
        for p in [db_path, tax_path]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                    console.print(f"  Removed {p}")
                except Exception as e:
                    console.print(f"[bold red]Failed to delete {p}: {e}[/bold red]")

    # Setup / Load components
    photo_index = PhotoIndex(db_path=db_path)
    photo_index.load()

    # Detect dimensionality mismatch between index and current model selection
    if photo_index.index is not None:
        expected_dim = 768 if "ViT-L" in model_name else (1024 if "ViT-H" in model_name else 512)
        if photo_index.index.d != expected_dim:
            console.print(f"[yellow]Warning: Index dimensionality ({photo_index.index.d}) does not match current model {model_name} expected dimensionality ({expected_dim}). Auto-resetting index...[/yellow]")
            if os.path.exists(db_path):
                try:
                    os.remove(db_path)
                except Exception:
                    pass
            photo_index = PhotoIndex(db_path=db_path)
            photo_index.load()

    taxonomy = TagTaxonomy(file_path=tax_path)
    taxonomy.load()

    preserve_full_frame = config.getboolean("model", "preserve_full_frame", fallback=False)
    max_aspect_ratio = config.getfloat("model", "max_aspect_ratio", fallback=2.0)
    force_image_size = config.get("model", "force_image_size", fallback=None)
    force_image_size = int(force_image_size) if force_image_size else None
    embedder = ClipEmbedder(model_name=model_name, pretrained=pretrained, cache_dir=cache_dir, preserve_full_frame=preserve_full_frame, max_aspect_ratio=max_aspect_ratio, force_image_size=force_image_size, photo_index=photo_index)
    if not test_mode:
        photo_index.migrate_disk_cache_to_sqlite(cache_dir)

    console.print(f"[bold cyan]Scanning directory:[/bold cyan] {directory}")
    all_images = scan_for_images(directory)
    console.print(f"Found {len(all_images)} image(s) total.")

    if not all_images:
        console.print("[yellow]No supported images found. Exiting.[/yellow]")
        return

    # Check for unchanged files using modification time and size
    existing_entries = {meta["path"]: meta for meta in photo_index.metadata}
    images_to_process = []
    skipped_count = 0

    if force_reembed:
        images_to_process = all_images
    else:
        for path in all_images:
            if path in existing_entries:
                try:
                    stat = os.stat(path)
                    saved = existing_entries[path]
                    if saved.get("mtime") == stat.st_mtime and saved.get("size") == stat.st_size:
                        skipped_count += 1
                        continue
                except Exception:
                    pass
            images_to_process.append(path)

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
    extractor = MetadataExtractor(exiftool_path=exiftool_path)
    
    batch_size = 500
    all_metadata = []
    
    from tqdm import tqdm
    for i in tqdm(range(0, len(images_to_process), batch_size), desc="Reading metadata"):
        batch = images_to_process[i:i+batch_size]
        batch_meta = extractor.batch_read(batch)
        all_metadata.extend(batch_meta)

    # Filter: Only index images with keywords/tags, people/faces, or captions/descriptions
    to_index_meta = []
    for meta in all_metadata:
        if meta["tags"] or meta["people"] or meta["captions"]:
            to_index_meta.append(meta)

    console.print(f"Filtered to [bold green]{len(to_index_meta)}[/bold green] images with existing tags, people, or captions.")
    
    # If no images have metadata to index
    if not to_index_meta:
        if skipped_count > 0:
            console.print("[green]No new tagged images found. Index remains current.[/green]")
        else:
            console.print("[yellow]No photos with existing tags or metadata. Indexing skipped.[/yellow]")
        photo_index.close()
        return

    # Initialize PathLocker for multi-process locking
    from index import PathLocker
    locker = PathLocker()
    face_processor = FaceProcessor() if not skip_faces else None
    
    try:
        # Generate Embeddings with incremental saving (batches of 100) to protect against halts/crashes
        console.print("[bold cyan]Generating embeddings...[/bold cyan]")
        batch_embeddings = []
        batch_metas = []
        batch_faces = {}  # Map path -> faces list
        paths_to_remove = set()
        total_new_indexed = 0

        for meta in tqdm(to_index_meta, desc="Generating embeddings"):
            path = meta["path"]
            
            # Acquire path-level lock to prevent duplicate concurrent work
            if not locker.acquire(path):
                continue
                
            try:
                emb = embedder.embed_image(path, force_recompute=force_reembed)
                
                # Extract and save face embeddings in the same pass (cached in memory until parent photo is saved)
                if face_processor:
                    faces = face_processor.detect_and_embed_faces(path)
                    batch_faces[path] = faces
                
                # If path already exists in current loaded index, mark it to remove before adding new version
                if path in existing_entries:
                    paths_to_remove.add(path)
                    
                batch_embeddings.append(emb)
                batch_metas.append(meta)
                
                # Learn new tags into taxonomy
                taxonomy.add_tags(meta["tags"])
                taxonomy.add_tags(meta["people"])
                
                # Save progress incrementally in batches of 100
                if len(batch_embeddings) >= 100:
                    if paths_to_remove:
                        photo_index.remove_paths(paths_to_remove)
                        paths_to_remove.clear()
                    photo_index.build_or_update(batch_embeddings, batch_metas, dim=len(batch_embeddings[0]), reload=False)
                    
                    # Save faces for the batch in a single transaction
                    if batch_faces:
                        photo_index.save_faces_batch(batch_faces)
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

        # Remove old entries for remaining modified files
        if paths_to_remove:
            photo_index.remove_paths(paths_to_remove)

        # Rebuild or update the FAISS index for the final batch
        if batch_embeddings:
            photo_index.build_or_update(batch_embeddings, batch_metas, dim=len(batch_embeddings[0]), reload=True)
            
            # Save the remaining face embeddings
            if batch_faces:
                photo_index.save_faces_batch(batch_faces)
                batch_faces.clear()
            
            taxonomy.save()
            for saved_meta in batch_metas:
                locker.release(saved_meta["path"])
            total_new_indexed += len(batch_embeddings)

        if total_new_indexed > 0:
            console.print("[bold green]Indexing successfully completed![/bold green]")
        else:
            console.print("[yellow]No new embeddings generated.[/yellow]")
    finally:
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
@click.option("--output", default="suggestions.json", help="Path to write the suggestions JSON file.")
@click.pass_context
def suggest(ctx, directory: str, k: int, min_sim: float, output: str):
    """Phase 2: Suggest tags for untagged photos."""
    config = get_config()
    cache_dir = config.get("paths", "embedding_cache_dir", fallback="data/embedding_cache")
    model_name = config.get("model", "name", fallback="ViT-B-32")
    pretrained = config.get("model", "pretrained", fallback="laion2b_s34b_b79k")

    # Load Index & Taxonomy
    test_mode = ctx.obj.get("test", False)
    if test_mode and output == "suggestions.json":
        output = "test_suggestions.json"

    db_path, tax_path = get_db_paths(config, test_mode)

    photo_index = PhotoIndex(db_path=db_path)
    if not photo_index.load():
        console.print("[bold red]Error:[/bold red] No photo index found. Please run 'index' first.")
        return
    
    try:
        # Detect dimensionality mismatch
        expected_dim = 768 if "ViT-L" in model_name else (1024 if "ViT-H" in model_name else 512)
        if photo_index.index is not None and photo_index.index.d != expected_dim:
            console.print(f"[bold red]Error:[/bold red] Index dimensionality ({photo_index.index.d}) does not match current model {model_name} expected dimensionality ({expected_dim}). Please run 'index' first to rebuild the index using the new model.")
            return

        taxonomy = TagTaxonomy(file_path=tax_path)
        taxonomy.load()

        # Load candidate tags from config
        candidate_str = config.get("candidates", "tags", fallback="")
        candidate_tags = [t.strip() for t in candidate_str.split(",") if t.strip()]

        preserve_full_frame = config.getboolean("model", "preserve_full_frame", fallback=False)
        max_aspect_ratio = config.getfloat("model", "max_aspect_ratio", fallback=2.0)
        force_image_size = config.get("model", "force_image_size", fallback=None)
        force_image_size = int(force_image_size) if force_image_size else None
        embedder = ClipEmbedder(model_name=model_name, pretrained=pretrained, cache_dir=cache_dir, preserve_full_frame=preserve_full_frame, max_aspect_ratio=max_aspect_ratio, force_image_size=force_image_size, photo_index=photo_index)
        if not test_mode:
            photo_index.migrate_disk_cache_to_sqlite(cache_dir)
        suggester = TagSuggester(photo_index, taxonomy, embedder=embedder, candidate_tags=candidate_tags)

        # Scan untagged photos
        console.print(f"[bold cyan]Scanning directory for untagged photos:[/bold cyan] {directory}")
        all_images = scan_for_images(directory)
        console.print(f"Found {len(all_images)} image(s) total.")

        if not all_images:
            console.print("[yellow]No supported images found. Exiting.[/yellow]")
            return

        # Batch read metadata for all untagged images
        console.print(f"[bold cyan]Reading metadata for {len(all_images)} image(s)...[/bold cyan]")
        exiftool_path = get_exiftool_path(config)
        extractor = MetadataExtractor(exiftool_path=exiftool_path)
        
        batch_size = 500
        metadata_map = {}
        from tqdm import tqdm
        for i in tqdm(range(0, len(all_images), batch_size), desc="Reading metadata"):
            batch = all_images[i:i+batch_size]
            batch_meta = extractor.batch_read(batch)
            for meta in batch_meta:
                metadata_map[meta["path"]] = meta

        # Process each untagged image
        console.print("[bold cyan]Generating suggestions...[/bold cyan]")
        suggestions_output = []
        
        for path in tqdm(all_images, desc="Generating suggestions"):
            try:
                emb = embedder.embed_image(path)
                meta = metadata_map.get(path)
                sugg = suggester.suggest_for_photo(path, emb, k=k, min_sim=min_sim, target_metadata=meta)
                suggestions_output.append(sugg)
            except Exception as e:
                logger.error(f"Error processing {path}: {e}")

        # Apply folder consensus post-processing to boost/decay scores
        console.print("[bold cyan]Applying event-level folder consensus...[/bold cyan]")
        suggestions_output = suggester.apply_folder_consensus(suggestions_output)

        # Write suggestions to JSON
        try:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(suggestions_output, f, indent=2)
            console.print(f"[bold green]Suggestions successfully written to {output}[/bold green]\n")
            
            # Display a summary table
            if suggestions_output:
                table = Table(title="Generated Tag Suggestions Summary")
                table.add_column("Photo File", style="green")
                table.add_column("Top Suggested Tags (Confidence)", style="magenta")
                table.add_column("Nearest Neighbors (Similarity)", style="cyan")

                for sugg in suggestions_output:
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
@click.option("-MinScore", "min_score", default=0.50, type=float, help="Write tags at or above this score threshold.")
def write(suggestions_file: str, live: bool, min_score: float):
    """Phase 3: Write suggested tags back to photos using ExifTool."""
    config = get_config()
    exiftool_path = get_exiftool_path(config)
    
    writer = MetadataWriter(exiftool_path=exiftool_path)
    writer.write_tags_to_photos(suggestions_file, live=live, min_score=min_score)

@cli.command()
@click.argument("query")
@click.option("--k", default=10, help="Number of results to return.")
@click.pass_context
def search(ctx, query: str, k: int):
    """Semantic text search across indexed library."""
    config = get_config()
    cache_dir = config.get("paths", "embedding_cache_dir", fallback="data/embedding_cache")
    model_name = config.get("model", "name", fallback="ViT-B-32")
    pretrained = config.get("model", "pretrained", fallback="laion2b_s34b_b79k")

    # Load Index
    test_mode = ctx.obj.get("test", False)
    db_path, _ = get_db_paths(config, test_mode)
    photo_index = PhotoIndex(db_path=db_path)
    if not photo_index.load():
        console.print("[bold red]Error:[/bold red] No photo index found. Please run 'index' first.")
        return
        
    try:
        # Detect dimensionality mismatch
        expected_dim = 768 if "ViT-L" in model_name else (1024 if "ViT-H" in model_name else 512)
        if photo_index.index is not None and photo_index.index.d != expected_dim:
            console.print(f"[bold red]Error:[/bold red] Index dimensionality ({photo_index.index.d}) does not match current model {model_name} expected dimensionality ({expected_dim}). Please run 'index' first to rebuild the index using the new model.")
            return

        # Embed text query
        preserve_full_frame = config.getboolean("model", "preserve_full_frame", fallback=False)
        max_aspect_ratio = config.getfloat("model", "max_aspect_ratio", fallback=2.0)
        force_image_size = config.get("model", "force_image_size", fallback=None)
        force_image_size = int(force_image_size) if force_image_size else None
        embedder = ClipEmbedder(model_name=model_name, pretrained=pretrained, cache_dir=cache_dir, preserve_full_frame=preserve_full_frame, max_aspect_ratio=max_aspect_ratio, force_image_size=force_image_size, photo_index=photo_index)
        console.print(f"Embedding query: '[bold yellow]{query}[/bold yellow]'")
        query_vector = embedder.embed_text(query)

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
    config = get_config()
    
    test_mode = ctx.obj.get("test", False)
    db_path, tax_path = get_db_paths(config, test_mode)

    photo_index = PhotoIndex(db_path=db_path)
    if not photo_index.load():
        console.print("[bold red]Error:[/bold red] No photo index found. Please run 'index' first.")
        return
        
    try:
        taxonomy = TagTaxonomy(file_path=tax_path)
        taxonomy.load()

        total_indexed = len(photo_index.metadata)
        
        # Tag and people distribution
        tag_counts = {}
        people_counts = {}
        
        for meta in photo_index.metadata:
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

@cli.command()
@click.argument("photo_path", type=click.Path(exists=True, dir_okay=False))
def inspect(photo_path: str):
    """Inspect metadata found in a single image (useful for debugging)."""
    config = get_config()
    exiftool_path = get_exiftool_path(config)

    console.print(f"Inspecting file: [bold cyan]{photo_path}[/bold cyan]")
    extractor = MetadataExtractor(exiftool_path=exiftool_path)
    meta = extractor.batch_read([photo_path])[0]

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
    config = get_config()
    
    test_mode = ctx.obj.get("test", False)
    db_path, _ = get_db_paths(config, test_mode)

    photo_index = PhotoIndex(db_path=db_path)
    if not photo_index.load():
        console.print("[bold red]Error:[/bold red] No photo index found.")
        return
        
    try:
        # Filter paths and gather metadata
        matches = []
        filter_path = os.path.abspath(folder) if folder else None
        
        for meta in photo_index.metadata:
            path = meta["path"]
            abs_path = os.path.abspath(path)
            if filter_path:
                if abs_path.startswith(filter_path):
                    matches.append(meta)
            else:
                matches.append(meta)

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

    config = get_config()
    
    test_mode = ctx.obj.get("test", False)
    db_path, _ = get_db_paths(config, test_mode)

    photo_index = PhotoIndex(db_path=db_path)
    if not photo_index.load():
        console.print("[bold red]Error:[/bold red] No photo index found.")
        return

    try:
        to_remove = set()
        if path:
            abs_target = os.path.abspath(path)
            for meta in photo_index.metadata:
                if os.path.abspath(meta["path"]) == abs_target:
                    to_remove.add(meta["path"])
                    
        if folder:
            abs_folder = os.path.abspath(folder)
            for meta in photo_index.metadata:
                if os.path.abspath(meta["path"]).startswith(abs_folder):
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
    config = get_config()
    test_mode = ctx.obj.get("test", False)
    db_path, _ = get_db_paths(config, test_mode)

    photo_index = PhotoIndex(db_path=db_path)
    if not photo_index.load():
        console.print("[bold red]Error:[/bold red] No photo index found. Please run 'index' first.")
        return

    try:
        # Find all files in the directory that are already indexed in photos
        console.print(f"[bold cyan]Scanning directory for photos to index faces:[/bold cyan] {directory}")
        all_images = scan_for_images(directory)
        indexed_paths = {meta["path"] for meta in photo_index.metadata}
        
        target_images = [img for img in all_images if img in indexed_paths]
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
            cursor = photo_index.conn.cursor()
            cursor.execute("SELECT DISTINCT photo_path FROM faces")
            already_processed = {row[0] for row in cursor.fetchall()}
            to_process = [img for img in target_images if img not in already_processed]

        if not to_process:
            console.print("[bold green]All faces are already indexed![/bold green]")
            return

        console.print(f"Extracting face embeddings for [bold yellow]{len(to_process)}[/bold yellow] photo(s)...")
        processor = FaceProcessor()
        
        from tqdm import tqdm
        count_faces = 0
        for path in tqdm(to_process, desc="Detecting and embedding faces"):
            faces = processor.detect_and_embed_faces(path)
            photo_index.save_faces_for_path(path, faces)
            count_faces += len(faces)

        console.print(f"[bold green]Successfully indexed {count_faces} faces across {len(to_process)} photos.[/bold green]")
    finally:
        photo_index.close()


@cli.command("cluster-faces")
@click.pass_context
def cluster_faces(ctx):
    """Run self-tuning identity resolution to cluster and name faces using photo tags."""
    config = get_config()
    test_mode = ctx.obj.get("test", False)
    db_path, tax_path = get_db_paths(config, test_mode)

    photo_index = PhotoIndex(db_path=db_path)
    if not photo_index.load():
        console.print("[bold red]Error:[/bold red] No photo index found.")
        return

    taxonomy = TagTaxonomy(file_path=tax_path)
    taxonomy.load()

    try:
        processor = FaceProcessor()
        resolved_stats = processor.cluster_and_resolve_identities(photo_index, taxonomy)
        
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

