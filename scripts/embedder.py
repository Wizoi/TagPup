# embedder.py
import os
import logging
import threading
from typing import List, Optional, Any
from PIL import Image
import torch
import numpy as np
import open_clip

import _root  # noqa: F401
from tagpup import config as tagpup_config
from tagpup.files import images
from tagpup.store import embeddings as store_embeddings

logger = logging.getLogger("tagpup_cli.embedder")


def output_dim(model_name):
    """How long the vectors `model_name` makes are, from open_clip's own description of
    the model; None for one it does not describe (from the hub), known only once loaded.

    The CLI guessed it from the name in three places -- 768 for ViT-L, 1024 for ViT-H,
    else 512 -- and ViT-bigG-14's are 1280: every index cleared the library's vectors,
    and Suggest and search refused to run (docs/findings.md, #74).
    """
    if model_name.startswith("hf-hub:"):
        return None   # open_clip would fetch its description from the hub to answer
    try:
        config = open_clip.get_model_config(model_name)
    except Exception:
        return None
    return config.get("embed_dim") if config else None


def stored_mismatch(photo_index, model_name):
    """(stored, made) when the vectors `photo_index` holds for these settings are not the
    length `model_name` makes; None when they are, or when either is not known."""
    stored = photo_index.index.d if photo_index.index is not None else None
    made = output_dim(model_name)
    if stored is None or made is None or stored == made:
        return None
    return (stored, made)

def pad_to_square(image: Image.Image, background_color=(0, 0, 0)) -> Image.Image:
    """Pad the image to a square with a solid background color (default black) to preserve entire frame."""
    width, height = image.size
    if width == height:
        return image
    elif width > height:
        result = Image.new(image.mode, (width, width), background_color)
        result.paste(image, (0, (width - height) // 2))
        return result
    else:
        result = Image.new(image.mode, (height, height), background_color)
        result.paste(image, ((height - width) // 2, 0))
        return result

class ClipEmbedder:
    _shared_model = None
    _shared_preprocess = None
    _shared_tokenizer = None
    _shared_model_lock = threading.Lock()

    def __init__(self, photo_index: Optional[Any] = None, **settings):
        """`settings` are tagpup.config.embedder_settings()'s: the config's, where the model
        is chosen, for any not given. The embedder had defaults of its own -- ViT-B-32
        among them -- beside the config's (docs/findings.md, #74)."""
        chosen = tagpup_config.embedder_settings()
        unknown = set(settings) - set(chosen)
        if unknown:
            raise TypeError("ClipEmbedder has no setting %s" % ", ".join(sorted(unknown)))
        chosen.update(settings)
        self.model_name = chosen["model_name"]
        self.pretrained = chosen["pretrained"]
        self.preserve_full_frame = chosen["preserve_full_frame"]
        self.max_aspect_ratio = chosen["max_aspect_ratio"]
        self.force_image_size = chosen["force_image_size"]
        #: The name its vectors are kept under in a library (tagpup.store.embeddings).
        self.model_key = store_embeddings.model_key(**chosen)
        self.photo_index = photo_index
        
        # Lazy initialization
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = None
        self.preprocess = None
        self.tokenizer = None
        self.model_lock = ClipEmbedder._shared_model_lock
        

    def _init_model(self):
        """Lazily load the CLIP model."""
        with ClipEmbedder._shared_model_lock:
            if ClipEmbedder._shared_model is not None:
                self.model = ClipEmbedder._shared_model
                self.preprocess = ClipEmbedder._shared_preprocess
                self.tokenizer = ClipEmbedder._shared_tokenizer
                return
                
            logger.info(f"Loading CLIP model {self.model_name} (pretrained on {self.pretrained}) on {self.device.upper()}...")
            try:
                kwargs = {}
                if self.force_image_size is not None:
                    kwargs["force_image_size"] = self.force_image_size
                if self.device == "cuda":
                    kwargs["precision"] = "fp16"
                model, _, preprocess = open_clip.create_model_and_transforms(
                    self.model_name, 
                    pretrained=self.pretrained, 
                    device=self.device,
                    **kwargs
                )
                ClipEmbedder._shared_model = model
                ClipEmbedder._shared_preprocess = preprocess
                ClipEmbedder._shared_tokenizer = open_clip.get_tokenizer(self.model_name)
                ClipEmbedder._shared_model.eval()
                
                if self.device == "cuda" and os.name != "nt" and hasattr(torch, "compile"):
                    try:
                        logger.info("Compiling CLIP model for CUDA acceleration...")
                        ClipEmbedder._shared_model = torch.compile(ClipEmbedder._shared_model)
                    except Exception as compile_err:
                        logger.warning(f"Failed to compile CLIP model: {compile_err}. Using standard model.")
                        
                self.model = ClipEmbedder._shared_model
                self.preprocess = ClipEmbedder._shared_preprocess
                self.tokenizer = ClipEmbedder._shared_tokenizer
                logger.info("CLIP model loaded successfully.")
            except Exception as e:
                logger.error(f"Failed to load CLIP model: {e}", exc_info=True)
                raise e

    def get_cached_embedding(self, file_path: str) -> Optional[List[float]]:
        """The vector kept in the library for this file under these model settings, if
        the file is as it was when it was computed. Without a library, nothing is kept:
        the JSON files that stood in for one were imported into it, and retired with it
        on 2026-09-24."""
        if not os.path.exists(file_path):
            return None
            
        # In the library, when there is one.
        if self.photo_index is not None and self.photo_index.conn is not None:
            try:
                stat = os.stat(file_path)
                row = store_embeddings.get(self.photo_index.conn, file_path, self.model_key)
                if row and row.mtime == stat.st_mtime and row.size == stat.st_size:
                    return np.frombuffer(row.vector, dtype=np.float32).tolist()
            except Exception as e:
                logger.warning(f"Failed to read/validate database cache for {file_path}: {e}")
            return None

    def save_to_cache(self, file_path: str, embedding: List[float], stamp=None):
        """Keep the vector in the library, stamped with `stamp`: the file's (mtime, size)
        as it was opened to be embedded, else as it is now. Taken after, a photo rotated
        while it was embedded kept the vector from before the turn as current (#86)."""
        try:
            if stamp is None:
                stamp = store_embeddings.stamp_of(file_path)
            if stamp is None:
                return

            # In the library, when there is one.
            if self.photo_index is not None and self.photo_index.conn is not None:
                def store(conn):
                    store_embeddings.put(conn, file_path, self.model_key, stamp[0], stamp[1],
                                         np.array(embedding, dtype=np.float32).tobytes())

                # On a connection of its own. Every worker in the suggestion pool
                # writes its embedding here, and they were all going through the
                # index's shared connection -- whose single transaction state they
                # interleaved into, telling the loser the database was locked.
                writer = getattr(self.photo_index, "write_on_own_connection", None)
                if writer is not None:
                    writer(store, label="embedding cache for %s" % os.path.basename(file_path))
                else:
                    store(self.photo_index.conn)
                    self.photo_index.conn.commit()
                return

        except Exception as e:
            logger.warning(f"Failed to write cache for {file_path}: {e}")

    def embed_image(self, file_path: str, force_recompute: bool = False) -> List[float]:
        """Embed a single image, utilizing the cache unless force_recompute is True."""
        if not force_recompute:
            cached = self.get_cached_embedding(file_path)
            if cached is not None:
                return cached

        self._init_model()
        
        try:
            stamp = store_embeddings.stamp_of(file_path)
            # Upright, as the photo is seen. A camera turned on its side stores the
            # pixels sideways with an Orientation tag, and so does Rotate now; CLIP
            # was describing the sideways picture.
            img = images.opened(file_path, upright=True)

            # Pad to square to preserve full frame if configured and within aspect ratio limit
            if self.preserve_full_frame:
                width, height = img.size
                aspect = max(width, height) / min(width, height)
                if aspect <= self.max_aspect_ratio:
                    img = pad_to_square(img)

            image_input = self.preprocess(img).unsqueeze(0).to(self.device)
            if self.device == "cuda":
                image_input = image_input.half()

            with self.model_lock:
                with torch.no_grad():
                    image_features = self.model.encode_image(image_input)
                    # L2 normalize the features
                    image_features /= image_features.norm(dim=-1, keepdim=True)
                    embedding = image_features[0].cpu().numpy().tolist()
                
            self.save_to_cache(file_path, embedding, stamp)
            return embedding
        except Exception as e:
            logger.error(f"Error embedding image {file_path}: {e}")
            raise e

    def embed_text(self, text: str) -> List[float]:
        """Embed a text query for semantic search."""
        self._init_model()
        try:
            text_input = self.tokenizer([text]).to(self.device)
            with self.model_lock:
                with torch.no_grad():
                    text_features = self.model.encode_text(text_input)
                    text_features /= text_features.norm(dim=-1, keepdim=True)
                    return text_features[0].cpu().numpy().tolist()
        except Exception as e:
            logger.error(f"Error embedding text '{text}': {e}")
            raise e
