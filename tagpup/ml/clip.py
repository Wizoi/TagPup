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
from tagpup.ml import free_device_memory, gpu, refuse_in_tests
import contextlib
import logging
import threading

import open_clip
import torch

from tagpup.files import images

logger = logging.getLogger("tagpup_cli.embedder")

#: The settings a model is made from: tagpup.services.settings.LibrarySettings.embedder's keys.
SETTINGS = ("model_name", "pretrained", "preserve_full_frame", "max_aspect_ratio", "force_image_size")


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
        #: The process's turn on the graphics card (tagpup.ml.gpu), which the runtime hands
        #: it; this process's own, card(), when none was.
        self.gpu = None

    def load(self):
        """Load the model now, if it is not loaded yet."""
        self._init_model()

    def loaded(self):
        """Are the weights in memory now?"""
        return self.model is not None

    def unload(self):
        """Let the weights go, and the GPU memory they held (tagpup.runtime drops a model
        no library it serves uses any more). Used again, it loads again."""
        with self.model_lock:
            if self.model is None:
                return
            self.model = self.preprocess = self.tokenizer = None
        logger.info("Unloaded CLIP model %s (%s).", self.model_name, self.pretrained)
        free_device_memory()

    def _init_model(self):
        """Lazily load the CLIP model: on the graphics card only with this process's turn
        on it (tagpup.ml.gpu), waited for before the model's lock is taken -- a turn being
        given up unloads this model under that lock."""
        if self.model is not None:
            return
        refuse_in_tests("CLIP")
        if gpu.on_the_card(self.device):
            (self.gpu or gpu.card()).ensure("loading CLIP %s" % self.model_name)
        with self.model_lock:
            if self.model is not None:
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
                model.eval()

                self.preprocess = preprocess
                self.tokenizer = open_clip.get_tokenizer(self.model_name)
                self.model = model
                logger.info("CLIP model loaded successfully.")
            except Exception as e:
                logger.error(f"Failed to load CLIP model: {e}", exc_info=True)
                raise e

    @contextlib.contextmanager
    def _running(self):
        """The model's lock, held with the model loaded: the weights, the preprocessing and the tokenizer are read
        inside it. `unload` takes the lock to drop all three, so a read made after `_init_model` returned and
        before the lock was taken saw None (a text embedded while the idle release ran raised "'NoneType' object is
        not callable"); the model is loaded again, outside the lock, when that happened."""
        for _attempt in range(3):
            self._init_model()
            with self.model_lock:
                if self.model is not None:
                    yield
                    return
        raise RuntimeError("The CLIP model was let go as often as it was loaded; try again.")

    def input_of(self, file_path, seen=None, load=True):
        """What the model is shown of a photo as it is now, made on the processor (picture_input): nothing runs on the
        graphics card, so the indexer makes it on a thread of its own ahead of the photo the card is on
        (tagpup_cli.py's index; docs/AI_PIPELINE.md, phase 1b).

        The picture is decoded in full first (tagpup.files.images.opened), and handed to `seen` when given; a photo
        whose picture does not decode raises images.Unreadable, as it is: the indexer writes nothing into a photo
        this has not read. `load`: see picture_input."""
        # Upright, as the photo is seen. A camera turned on its side stores the
        # pixels sideways with an Orientation tag, and so does Rotate now; CLIP
        # was describing the sideways picture.
        img = images.opened(file_path, upright=True)
        if seen is not None:
            seen(img)
        return self.picture_input(img, load=load)

    def input_bytes(self, width, height):
        """The most bytes input_of holds at once for a photo of `width` x `height` (RGB): the picture decoded and the
        upright copy exif_transpose makes of it (a copy for every photo, turned or not), and then, when the settings
        keep the full frame, that copy and the square it is padded to. What the index's read-ahead reserves for this
        half (tagpup_cli.read_ahead_bytes); said here, beside what it counts, so that a change in how CLIP is shown a
        photo changes it too. 0 for a picture whose size is not known (a damaged file's header)."""
        if not width or not height:
            return 0
        picture = 3 * width * height
        held = 2 * picture
        if self.preserve_full_frame and max(width, height) / min(width, height) <= self.max_aspect_ratio:
            held = max(held, picture + 3 * max(width, height) ** 2)
        return held

    def picture_input(self, img, load=True):
        """What the model is shown of a decoded picture (an RGB Pillow image, upright): padded to a square when the
        settings keep the full frame, then the model's own preprocessing (resized, cut, normalised), as a tensor on
        the processor. The preprocessing is the loaded model's, read under its lock and run outside it: the resize
        of a large photo no longer holds the model from another thread's forward pass.

        `load` False never loads the model: a thread that does not have the graphics card (the index's read-ahead,
        which loads CLIP on the thread that has it) raises RuntimeError when the model is not loaded -- after the
        index gave its turn back, a read still under way loaded CLIP onto a card that was no longer its own. True
        (Suggest's PhotoEmbeddings.of) loads it on first use, as before (_running)."""
        # Pad to square to preserve full frame if configured and within aspect ratio limit
        if self.preserve_full_frame:
            width, height = img.size
            aspect = max(width, height) / min(width, height)
            if aspect <= self.max_aspect_ratio:
                img = images.pad_to_square(img)
        if load:
            with self._running():
                preprocess = self.preprocess
        else:
            with self.model_lock:
                preprocess = self.preprocess
            if preprocess is None:
                raise RuntimeError("CLIP is not loaded, and a read ahead does not load it.")
        return preprocess(img)

    def embed_input(self, image_input):
        """The vector of what picture_input made, on the model's device: the graphics card's work for a photo."""
        with self._running():
            batch = image_input.unsqueeze(0).to(self.device)
            if self.device == "cuda":
                batch = batch.half()
            with torch.inference_mode():
                image_features = self.model.encode_image(batch)
                # L2 normalize the features
                image_features /= image_features.norm(dim=-1, keepdim=True)
                return image_features[0].cpu().numpy().tolist()

    def embed_text(self, text):
        """Embed a text query for semantic search."""
        try:
            with self._running():
                text_input = self.tokenizer([text]).to(self.device)
                with torch.inference_mode():
                    text_features = self.model.encode_text(text_input)
                    text_features /= text_features.norm(dim=-1, keepdim=True)
                    return text_features[0].cpu().numpy().tolist()
        except Exception as e:
            logger.error(f"Error embedding text '{text}': {e}")
            raise e
