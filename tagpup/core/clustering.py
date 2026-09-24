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

A face is compared with a person's closest single face of the years around its photo
(`closest_in_era`), never with an average of them. Clustering averaged them (a mean),
TagTuner's person grid took their geometric median, and the suggester averaged every
face with no years at all (docs/findings.md, #71). Measured the same day on `kr-track`,
against the same person's other photos:

    compared with       cut    own faces below   strangers at or above
    mean                0.70        9.7%                1.2%
    geometric median    0.70        9.9%                1.1%
    closest face        0.70        3.1%                2.7%
    closest face        0.80       18.1%                0.2%

The median gains nothing over the mean, and the closest face tells the owner's people
from strangers better than either. The same two values serve every decision: a name
is flagged as possibly wrong below OFFER_A_NAME -- it would not even be offered -- and
clustering keeps a name it gave only while the face reaches NAME_WITHOUT_ASKING, the
value it named at *(owner, 2026-09-24)*. The grid flagged below 0.85 of the median,
which is half of every correctly named face, and clustering unnamed below 0.80 of the
mean, a third of them.
"""
import math

import numpy as np

from tagpup.core import paths

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


#: Faces at least this alike are grouped together before anyone is named: clustering's
#: radius, and TagTuner's grouping of the nameless (DBSCAN, as a distance). Measured on
#: `kr-track` (2026-09-24), each hand-named face against the nearest face of its own
#: person, of anyone else, and of a stranger, never one of its own photo:
#:
#:     similarity   own person's reaches   someone else's   a stranger's
#:       0.80              81%                  3.4%             0.6%
#:       0.85              63%                  1.8%             0.1%
#:       0.885             46%                  0.6%             0.0%
#:       0.90              38%                  0.5%             0.0%
#:
#: Grouping chains faces, so each that reaches someone else can pull two people into one
#: group; naming, from anchors and keywords, does the rest. It was 0.48 as a distance in
#: two places. The owner kept it (2026-09-24).
GROUPING = 0.885


def band(similarity):
    """The band a face this alike a person is shown in: "likely" when it could be named
    unasked, "possible" when it would be offered, else None. TagTuner's candidates,
    suggestions and diagnostics had four sets of numbers for this (0.75/0.60, 0.85,
    0.8/0.65, 0.8); the server now names the band and the page shows it *(owner,
    2026-09-24)*."""
    if names_unasked(similarity):
        return "likely"
    if is_offered(similarity):
        return "possible"
    return None


def distance(similarity):
    """The distance between two unit vectors whose cosine similarity is `similarity`:
    what the clustering code, which works in distances, compares against."""
    return math.sqrt(2.0 - 2.0 * similarity)



# ---- The decisions ---------------------------------------------------------------------
#
# Callers ask these; the values above are this module's alone
# (tests/test_face_values_have_one_owner.py).

def is_offered(similarity):
    """Is a face `similarity`-like the person worth offering as its name?"""
    return similarity is not None and similarity >= OFFER_A_NAME


def names_unasked(similarity):
    """May a face `similarity`-like the person be given their name with no one looking --
    and keep a name clustering gave it?"""
    return similarity is not None and similarity >= NAME_WITHOUT_ASKING


def looks_wrong(similarity):
    """Is a name on a face only `similarity`-like its person worth a second look? Below
    the value it would be offered at, it would not even have been suggested."""
    return similarity is not None and similarity < OFFER_A_NAME


# ---- Faces in the background ---------------------------------------------------------

def background_faces(boxes):
    """Indexes of the faces of one photo, by their boxes ([x1, y1, x2, y2], or None),
    that are noise in the background: under a tenth the area of the largest and under
    2,000 pixels outright. Clustering names none of them and the suggester offers no
    name for them; each wrote this rule itself (docs/findings.md, #74)."""
    areas = [(box[2] - box[0]) * (box[3] - box[1]) if box else 0 for box in boxes]
    largest = max(areas, default=0)
    return {i for i, area in enumerate(areas) if area < 0.10 * largest and area < 2000}


# ---- Everyone's faces ----------------------------------------------------------------

def _window(age):
    """Years either side of a photo that a person's faces are drawn from, by how old the
    person was then -- years since their first photo: faces change fastest in the young."""
    return 1 if age <= 4 else 2 if age <= 12 else 3 if age <= 16 else 4 if age <= 20 else 5


def _unit(embedding):
    vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


class KnownFaces:
    """The faces named for each person, and how like one of them a face is.

    A face is compared with a person's closest single face from the years around its
    photo -- the window for the person's age then, widened to five years until it holds
    five faces, else all of them -- and never with a face of its own photo. Every
    screen, clustering and the suggester ask this, rather than averaging faces each in a
    way of its own (#71).
    """

    def __init__(self):
        self._faces = {}     # name -> [(unit embedding, year or None, photo key or None)]
        self._arrays = {}    # name -> (matrix, years, photos), made when first asked

    @classmethod
    def of(cls, rows):
        """KnownFaces from (name, embedding, year, photo) rows; year and photo may be None."""
        known = cls()
        for name, embedding, year, photo in rows:
            known.add(name, embedding, year, photo)
        return known

    def add(self, name, embedding, year=None, photo=None):
        if not name or embedding is None:
            return
        self._faces.setdefault(name, []).append(
            (_unit(embedding), year, paths.key(photo) if photo else None))
        self._arrays.pop(name, None)

    def names(self):
        return list(self._faces)

    def __contains__(self, name):
        return name in self._faces

    def _of(self, name):
        arrays = self._arrays.get(name)
        if arrays is None:
            faces = self._faces[name]
            arrays = self._arrays[name] = (
                np.stack([e for e, _y, _p in faces]),
                np.array([np.nan if y is None else y for _e, y, _p in faces], dtype=float),
                np.array([p or "" for _e, _y, p in faces], dtype=object))
        return arrays

    def _chosen(self, name, year, photo):
        """Which of `name`'s faces a face of `photo`, taken in `year`, is compared with."""
        _matrix, years, photos = self._of(name)
        usable = photos != paths.key(photo) if photo else np.ones(len(photos), dtype=bool)
        if year is None:
            return usable
        dated = usable & ~np.isnan(years)
        if not dated.any():
            return usable
        width = _window(year - np.nanmin(np.where(usable, years, np.nan)))
        while True:
            in_era = dated & (np.abs(years - year) <= width)
            if in_era.sum() >= 5 or width >= 5:
                break
            width += 1
        return in_era if in_era.sum() >= 5 else usable

    def likeness(self, name, embedding, year=None, photo=None):
        """How like `embedding` the closest of `name`'s faces is, from the years around
        `year`, leaving out `photo`'s own faces. None when they have no face to compare."""
        return self.likeness_many(name, [(embedding, year, photo)])[0]

    #: Faces compared at once by likeness_many: a block of this many by all of a
    #: person's faces is the largest matrix it holds.
    BLOCK = 1024

    def likeness_many(self, name, faces):
        """likeness for each of `faces`, [(embedding, year, photo)], against `name`: one
        matrix product for all of them, so a grid of thousands is one pass rather than
        thousands."""
        if name not in self._faces or not faces:
            return [None] * len(faces)
        matrix = self._of(name)[0]
        found, masks = [], {}
        for start in range(0, len(faces), self.BLOCK):
            block = faces[start:start + self.BLOCK]
            similarities = np.stack([_unit(e) for e, _y, _p in block]) @ matrix.T
            for row, (_e, year, photo) in zip(similarities, block):
                key = (year, paths.key(photo) if photo else None)
                if key not in masks:
                    masks[key] = self._chosen(name, year, photo)
                chosen = masks[key]
                found.append(float(row[chosen].max()) if chosen.any() else None)
        return found

    def most_like(self, embedding, year=None, photo=None, among=None, skip=()):
        """(name, likeness) of the person `embedding` is most like, of `among` (everyone,
        without) but those in `skip`; (None, None) with nobody to compare."""
        best = (None, None)
        for name in (among if among is not None else self._faces):
            if name in skip:
                continue
            similarity = self.likeness(name, embedding, year, photo)
            if similarity is not None and (best[1] is None or similarity > best[1]):
                best = (name, similarity)
        return best
