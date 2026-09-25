"""CLIP: a photo or a text as a vector, from the settings it is given.

It embeds an image or a text and nothing else. Which settings it runs with is the
library's (tagpup.services.settings, LibrarySettings.embedder), made into a model by
tagpup.runtime; where a photo's vector is kept is the library's
(tagpup.store.embeddings), read and written by the service that asks
(tagpup.services.search.PhotoEmbeddings).

It was scripts/embedder.py's ClipEmbedder, which read config.ini for any setting not
given, kept the loaded model in a class attribute shared by every embedder in the
process, and read and wrote the library's cache itself (docs/ARCHITECTURE.md, "The
layers, revisited"). A model is now one object, built once by the runtime and shared by
handing it on.
"""
from tagpup.ml import refuse_in_tests
import logging
import os
import threading

import open_clip
import torch

from tagpup.files import images

logger = logging.getLogger("tagpup_cli.embedder")

#: The settings a model is made from: tagpup.services.settings.LibrarySettings.embedder's keys.
SETTINGS = ("model_name", "pretrained", "preserve_full_frame", "max_aspect_ratio", "force_image_size")


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


class ClipModel:
    """One CLIP model, loaded the first time it is used. Thread-safe: the suggestion
    runs embed from a pool."""

    def __init__(self, model_name, pretrained, preserve_full_frame, max_aspect_ratio,
                 force_image_size, device=None):
        self.model_name = model_name
        self.pretrained = pretrained
        self.preserve_full_frame = preserve_full_frame
        self.max_aspect_ratio = max_aspect_ratio
        self.force_image_size = force_image_size
        #: What it was made from: the name its vectors are kept under is made of these
        #: (tagpup.store.embeddings.model_key).
        self.settings = {"model_name": model_name, "pretrained": pretrained,
                         "preserve_full_frame": preserve_full_frame,
                         "max_aspect_ratio": max_aspect_ratio, "force_image_size": force_image_size}

        # Lazy initialization
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = None
        self.preprocess = None
        self.tokenizer = None
        #: Held while loading and while the model runs.
        self.model_lock = threading.Lock()

    def load(self):
        """Load the model now, if it is not loaded yet."""
        self._init_model()

    def _init_model(self):
        """Lazily load the CLIP model."""
        with self.model_lock:
            if self.model is not None:
                return
            refuse_in_tests("CLIP")

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
                model.eval()

                if self.device == "cuda" and os.name != "nt" and hasattr(torch, "compile"):
                    try:
                        logger.info("Compiling CLIP model for CUDA acceleration...")
                        model = torch.compile(model)
                    except Exception as compile_err:
                        logger.warning(f"Failed to compile CLIP model: {compile_err}. Using standard model.")

                self.preprocess = preprocess
                self.tokenizer = open_clip.get_tokenizer(self.model_name)
                self.model = model
                logger.info("CLIP model loaded successfully.")
            except Exception as e:
                logger.error(f"Failed to load CLIP model: {e}", exc_info=True)
                raise e

    def embed_image(self, file_path):
        """Embed one photo as it is now. Nothing is kept: see PhotoEmbeddings."""
        self._init_model()

        try:
            # Upright, as the photo is seen. A camera turned on its side stores the
            # pixels sideways with an Orientation tag, and so does Rotate now; CLIP
            # was describing the sideways picture.
            img = images.opened(file_path, upright=True)

            # Pad to square to preserve full frame if configured and within aspect ratio limit
            if self.preserve_full_frame:
                width, height = img.size
                aspect = max(width, height) / min(width, height)
                if aspect <= self.max_aspect_ratio:
                    img = images.pad_to_square(img)

            image_input = self.preprocess(img).unsqueeze(0).to(self.device)
            if self.device == "cuda":
                image_input = image_input.half()

            with self.model_lock:
                with torch.no_grad():
                    image_features = self.model.encode_image(image_input)
                    # L2 normalize the features
                    image_features /= image_features.norm(dim=-1, keepdim=True)
                    return image_features[0].cpu().numpy().tolist()
        except Exception as e:
            logger.error(f"Error embedding image {file_path}: {e}")
            raise e

    def embed_text(self, text):
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
