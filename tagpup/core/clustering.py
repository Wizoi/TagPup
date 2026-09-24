"""Who is who: how alike two faces must be for the app to act on it.

The score is the cosine similarity of two face embeddings, each scaled to unit length:
1 is the same picture, and faces of one person mostly score above 0.85. Each decision
has one value, here; the screens, the services and clustering read it rather than
writing their own. There were several values for one decision -- a face was offered a
name from 0.5 in TagPup and from 0.70 in TagTuner (docs/findings.md, #69, #75).

Measured on 2026-09-24 against `kr-track`, whose owner had marked 1,256 faces as
strangers and named 1,424 by hand, comparing each face with the closest single face of
someone named, never one in its own photo:

    cut    strangers reaching it   hand-named faces offered   right when offered
    0.50          68%                     99.8%                     98.2%
    0.70          4.9%                    96.6%                     99.1%
    0.80          0.4%                    81.9%                     99.3%
    0.85          0.1%                    63.8%                     99.2%

A wrong person is rarely the closest match at any cut; what a cut decides is how many
strangers reach someone, against how many of the owner's people are found.
"""
import math

#: A face is offered a name -- a person looks, and accepts or not -- when it is at least
#: this like one face already named. One stranger in twenty reaches it, and nearly all
#: of the owner's people do. The owner chose it (2026-09-24).
OFFER_A_NAME = 0.70

#: A face is given a name with no one looking -- automatch, the faces New Person
#: gathers, clustering naming a face whose photo's keywords name the person -- when it
#: is at least this like one face already named (clustering: that person's face of the
#: years around the photo). Four strangers in a thousand reach it; four in five of the
#: owner's people do, and those rightly. The owner chose it (2026-09-24).
NAME_WITHOUT_ASKING = 0.80


def distance(similarity):
    """The distance between two unit vectors whose cosine similarity is `similarity`:
    what the clustering code, which works in distances, compares against."""
    return math.sqrt(2.0 - 2.0 * similarity)
