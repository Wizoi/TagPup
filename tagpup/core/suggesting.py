"""Which suggested tags are worth a person's time: the value a tag's score must reach.

A tag's score is its share of a photo's nearest neighbours, weighted by how alike they
are, after the folder's consensus (scripts/suggester.py). The app showed a tag from 0.6;
the CLI's `write`, the writer and the runner wrote from 0.5, so the same suggestions put
tags on files that the app never showed (docs/findings.md, #70). There is one value now,
here, for showing and for writing.

Measured on 2026-09-24 on `photo_index`: 907 photos already tagged, in 60 folders, each
suggested for with its own folder left out of its neighbours -- as a folder Suggest is
run on for the first time, whose photos carry no tags yet. People are left out; their
values are in tagpup.core.clustering.

    score at least   tags offered   right    real tags found
        0.50             774        78.8%        22.9%
        0.60             653        85.0%        20.8%
        0.70             560        88.2%        18.5%
        0.80             487        91.6%        16.7%

The tags between 0.5 and 0.6 were right 45% of the time. Few real tags are found at any
value: a folder's own tags are often its own, and neighbours elsewhere cannot know them.

What CLIP is asked about a photo is here too: the words (zero_shot_words) and the
sentence each is put in (clip_prompt). The server and the CLI each merged the tree's
words into config.ini's, and the suggester wrote the sentences (docs/findings.md, #74).
"""
from tagpup.core import vocabulary

#: A suggested tag is shown in the panel, applied by Apply All, and written by the CLI,
#: the writer and the runner when its score is at least this. Six in seven are right.
#: The owner chose it (2026-09-24).
OFFER_A_TAG = 0.60


def zero_shot_words(configured, tag_paths, face_roots):
    """The words CLIP scores a photo against: `configured` (config.ini's candidates, in
    order), then the leaf of each of `tag_paths` in sorted order, once each whatever the
    case -- but no one filed under one of `face_roots` (lowercased). People are matched
    by their faces, not by asking CLIP whether a photo looks like a name (#66)."""
    words, seen = [], set()
    for word in list(configured) + [
            parts[-1] for parts in (vocabulary.segments(p) for p in sorted(tag_paths))
            if parts and parts[0].lower() not in face_roots]:
        word = word.strip()
        if word and word.lower() not in seen:
            seen.add(word.lower())
            words.append(word)
    return words


def clip_prompt(word, year=None, person=False):
    """The sentence CLIP embeds for `word`, of photos taken in `year` when it is given.
    A person's name keeps its case; a thing is lower-cased. It is also the key a word's
    embedding is kept under in the library, so a change here computes them all again."""
    thing = word if person else "a " + word.lower()
    return "a photo of %s in %s" % (thing, year) if year is not None else "a photo of %s" % thing
