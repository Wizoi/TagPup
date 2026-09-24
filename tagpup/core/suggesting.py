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
"""

#: A suggested tag is shown in the panel, applied by Apply All, and written by the CLI,
#: the writer and the runner when its score is at least this. Six in seven are right.
#: The owner chose it (2026-09-24).
OFFER_A_TAG = 0.60
