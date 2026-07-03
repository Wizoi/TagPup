# embedder.py
import os
import json
import hashlib
import logging
import threading
from typing import List, Union, Optional, Any
from PIL import Image
# Disable Pillow image size check limit to support large photos / panoramas
Image.MAX_IMAGE_PIXELS = 500000000
import torch
import numpy as np
import open_clip

logger = logging.getLogger("tagpup_cli.embedder")

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

    def __init__(self, model_name: str = "ViT-B-32", pretrained: str = "laion2b_s34b_b79k", cache_dir: str = "data/embedding_cache", preserve_full_frame: bool = False, max_aspect_ratio: float = 2.0, force_image_size: Optional[int] = None, photo_index: Optional[Any] = None):
        self.model_name = model_name
        self.pretrained = pretrained
        self.cache_dir = cache_dir
        self.preserve_full_frame = preserve_full_frame
        self.max_aspect_ratio = max_aspect_ratio
        self.force_image_size = force_image_size
        self.photo_index = photo_index
        
        # Lazy initialization
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = None
        self.preprocess = None
        self.tokenizer = None
        self.model_lock = ClipEmbedder._shared_model_lock
        
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir, exist_ok=True)

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

    def _get_cache_path(self, file_path: str) -> str:
        """Get the cache file path based on MD5 of absolute file path."""
        abs_path = os.path.abspath(file_path)
        path_hash = hashlib.md5(abs_path.encode('utf-8')).hexdigest()
        return os.path.join(self.cache_dir, f"{path_hash}.json")

    def get_cached_embedding(self, file_path: str) -> Optional[List[float]]:
        """Retrieve embedding from cache if file is unchanged."""
        if not os.path.exists(file_path):
            return None
            
        # Try database cache first if photo_index is available
        if self.photo_index is not None and self.photo_index.conn is not None:
            try:
                stat = os.stat(file_path)
                abs_path = os.path.abspath(file_path)
                cursor = self.photo_index.conn.cursor()
                cursor.execute("""
                    SELECT mtime, size, model_name, pretrained, preserve_full_frame, max_aspect_ratio, force_image_size, embedding
                    FROM embedding_cache WHERE path = ?
                """, (abs_path,))
                row = cursor.fetchone()
                if row:
                    mtime, size, model_name, pretrained, preserve_full_frame, max_aspect_ratio, force_image_size, emb_bytes = row
                    preserve_full_frame_bool = bool(preserve_full_frame)
                    
                    if (mtime == stat.st_mtime and 
                        size == stat.st_size and 
                        model_name == self.model_name and 
                        pretrained == self.pretrained and 
                        preserve_full_frame_bool == self.preserve_full_frame and 
                        max_aspect_ratio == self.max_aspect_ratio and 
                        force_image_size == self.force_image_size):
                        return np.frombuffer(emb_bytes, dtype=np.float32).tolist()
            except Exception as e:
                logger.warning(f"Failed to read/validate database cache for {file_path}: {e}")
            return None

        # Fallback to disk-based cache
        cache_path = self._get_cache_path(file_path)
        if not os.path.exists(cache_path):
            return None
            
        try:
            stat = os.stat(file_path)
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            # Verify modification time, size, and model settings to ensure cache matches current settings
            if (data.get("mtime") == stat.st_mtime and 
                data.get("size") == stat.st_size and 
                data.get("model_name") == self.model_name and 
                data.get("pretrained") == self.pretrained and
                data.get("preserve_full_frame", False) == self.preserve_full_frame and
                data.get("max_aspect_ratio", 2.0) == self.max_aspect_ratio and
                data.get("force_image_size") == self.force_image_size):
                return data.get("embedding")
        except Exception as e:
            logger.warning(f"Failed to read/validate cache for {file_path}: {e}")
            
        return None

    def save_to_cache(self, file_path: str, embedding: List[float]):
        """Save embedding to cache with file stats."""
        try:
            stat = os.stat(file_path)
            abs_path = os.path.abspath(file_path)
            
            # Try database cache first if photo_index is available
            if self.photo_index is not None and self.photo_index.conn is not None:
                emb_bytes = np.array(embedding, dtype=np.float32).tobytes()
                cursor = self.photo_index.conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO embedding_cache (
                        path, mtime, size, model_name, pretrained, 
                        preserve_full_frame, max_aspect_ratio, force_image_size, embedding
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    abs_path,
                    stat.st_mtime,
                    stat.st_size,
                    self.model_name,
                    self.pretrained,
                    1 if self.preserve_full_frame else 0,
                    self.max_aspect_ratio,
                    self.force_image_size,
                    emb_bytes
                ))
                self.photo_index.conn.commit()
                return

            # Fallback to disk-based cache
            cache_path = self._get_cache_path(file_path)
            data = {
                "path": abs_path,
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "model_name": self.model_name,
                "pretrained": self.pretrained,
                "preserve_full_frame": self.preserve_full_frame,
                "max_aspect_ratio": self.max_aspect_ratio,
                "force_image_size": self.force_image_size,
                "embedding": embedding
            }
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(data, f)
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
            with Image.open(file_path) as img:
                # Convert palette images, grayscale, etc. to RGB
                if img.mode != "RGB":
                    img = img.convert("RGB")
                
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
                
            self.save_to_cache(file_path, embedding)
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
