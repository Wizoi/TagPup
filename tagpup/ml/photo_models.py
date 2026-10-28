"""The photo models TagPup knows, by (open_clip model name, weights), and what each needs that the model does not say of
itself: the word cut Suggest's zero-shot words are held to, and whether it is shown a photo at a size of the library's
choosing (model.force_image_size) or only at its own. No torch: the settings' gate and Suggest read it without loading a
model (tagpup.ml.clip loads one).

The numbers are the phase 5 spike's (docs/AI_PIPELINE.md, section 7, "Phase 5 model spike (2026-10-24)"): SigLIP's
cosines are lower than the first model's (OpenCLIP ViT-H-14, deleted 2026-10-28: no library holds its vectors), so the
word cut a word must reach is 0.11 under SO400M; the neighbour cut (0.35) and the panel's floor (0.60) are not here.
"""

#: (model name, weights): {"word_cut": the least cosine a zero-shot word keeps, "sizes": whether the library may force
#: an image size}. SigLIP 2 SO400M is measured at its native 224 only (the spike ran it so; open_clip builds its
#: position embedding for that size).
MODELS = {
    ("ViT-SO400M-14-SigLIP2", "webli"): {"word_cut": 0.11, "sizes": False},
}

def holds(name, pretrained):
    """Does the table hold the pair? The settings' gate refuses a photo model it does not (a pair typed in the gear that
    is no model TagPup measured has no word cut to hold its words to)."""
    return (name, pretrained) in MODELS


def listing():
    """The pairs the table holds, spelled for a sentence: "ViT-SO400M-14-SigLIP2 (webli), ..."."""
    return ", ".join("%s (%s)" % pair for pair in MODELS)


def word_cut(name, pretrained):
    """The least cosine a zero-shot word must reach under the model `name` with weights `pretrained`, a pair the table
    holds (the settings refuse any other)."""
    return MODELS[(name, pretrained)]["word_cut"]


def takes_a_size(name, pretrained):
    """May a library show the model photos at a size of its own (model.force_image_size)? False for a model measured at
    its native size only."""
    return MODELS[(name, pretrained)]["sizes"]
