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
is flagged as possibly wrong below the likeness a name is offered at (Cuts.offer_a_name) -- it would not even be offered -- and
clustering keeps a name it gave only while the face reaches the likeness it names unasked at (Cuts.name_without_asking), the
value it named at *(owner, 2026-09-24)*. The grid flagged below 0.85 of the median,
which is half of every correctly named face, and clustering unnamed below 0.80 of the
mean, a third of them.

A likeness is not enough on its own: a face 0.85 like one cousin and 0.84 like the other is either. The margin -- how
much more like the face its person is than the next person named -- told right from wrong far better on photo_index's
FaceNet vectors (docs/AI_PIPELINE.md, sections 3.1 and 7, held out by folder; #1532 for the curve's bias):

    precision by likeness     0.9837 at 0.70     0.9846 at 0.80
    precision by margin       0.9954 at 0.02     0.9983 at 0.04

So a name resting on likeness alone needs both: the likeness, and the margin over the second-best of everyone named
(`margins`). It gates the names given from now on and the names offered; it never takes away a name a face carries
or one given by hand (owner, 2026-10-17: applied to the names already given it would unname about 18,500 of
photo_index's 55,088).
"""
import math
import threading
from dataclasses import dataclass

import numpy as np

from tagpup.core import paths, validation

@dataclass(frozen=True)
class Cuts:
    """The likeness and the margin over the next person (`margins`) at which one face model's vectors offer a name and
    give one with no one looking. A likeness is the cosine of two vectors of one model, and two models' cosines are not on
    one scale (AdaFace's faces of one person sit far lower than FaceNet's), so each model has its own (CUTS, cuts_of):
    every decision below is a model's, asked of the cuts of the model whose vectors were compared. Callers ask these;
    the values are this module's alone (tests/test_face_values_have_one_owner.py)."""
    #: A face is offered a name -- a person looks, and accepts or not -- when it is at least this like one face named.
    offer_a_name: float
    #: A face is given a name with no one looking -- automatch, the faces New Person gathers, clustering naming a face
    #: whose photo's keywords name the person -- when it is at least this like one face named.
    name_without_asking: float
    #: ... and only when it is at least this much more like that person than the next person named.
    name_margin: float
    #: A face is offered a name only when it is at least this much more like that person than the next one.
    offer_margin: float
    #: Faces at least this alike are grouped together before anyone is named: cluster-faces' radius
    #: (tagpup.services.identities) and Identify's grouping of the nameless (tagpup.ml.grouping), as a distance there
    #: (group_distance).
    group_at: float
    #: Where the values come from.
    source: str

    def band(self, similarity, margin):
        """The band a face this alike a person, by this margin over the next person, is shown in: "likely" when it
        could be named unasked, "possible" when it would be offered, else None. TagTuner's candidates, suggestions and
        diagnostics had four sets of numbers for this (0.75/0.60, 0.85, 0.8/0.65, 0.8); the server now names the band
        and the page shows it *(owner, 2026-09-24)*."""
        if self.names_unasked(similarity, margin):
            return "likely"
        if self.is_offered(similarity, margin):
            return "possible"
        return None

    def is_offered(self, similarity, margin):
        """Is a face `similarity`-like the person, and `margin` more like them than the next person (`margins`), worth
        offering as its name?"""
        return similarity is not None and similarity >= self.offer_a_name and margin >= self.offer_margin

    def names_unasked(self, similarity, margin):
        """May a face `similarity`-like the person, and `margin` more like them than the next person (`margins`), be
        given their name with no one looking? A name the face carries is asked no margin -- the margin never takes a
        name away (owner, 2026-10-17) --: its caller passes math.inf."""
        return similarity is not None and similarity >= self.name_without_asking and margin >= self.name_margin

    def gathers_for_a_new_person(self, similarity, margin):
        """Is a nameless face `similarity`-like the face New Person was opened on, and `margin` more like it than like
        anyone named, gathered for the new person? As like as naming unasked asks, since they are named together; but
        gathering is an offer the owner looks over before naming, so the margin is the offer's (owner, 2026-10-17)."""
        return similarity is not None and similarity >= self.name_without_asking and margin >= self.offer_margin

    def names_from_the_tag(self, similarity, margin):
        """May a face be given the name of a person its photo's keywords name, with no one looking? The keyword says
        the person is in the photo, so the likeness need only be one a name is offered at (#902); it does not say which
        face is theirs, which is what the margin says, so that is the margin of naming unasked (owner, 2026-10-17)."""
        return similarity is not None and similarity >= self.offer_a_name and margin >= self.name_margin

    def might_name_unasked(self, similarities, slack):
        """Which of `similarities` (an array) names_unasked could accept, were each off by as much as `slack`: for a
        rough first look that must miss none it would name (tagpup.services.identify.UnnamedFaces)."""
        return similarities >= self.name_without_asking - slack

    @property
    def group_distance(self):
        """group_at as the distance between two unit vectors: what DBSCAN, which works in distances, is given."""
        return distance(self.group_at)

    def looks_wrong(self, similarity):
        """Is a name on a face only `similarity`-like its person worth a second look? Below the value it would be
        offered at, it would not even have been suggested."""
        return similarity is not None and similarity < self.offer_a_name


#: FaceNet's (MTCNN and FaceNet's vectors). The likenesses the owner chose on kr-track (2026-09-24): at 0.70 one
#: stranger in twenty reaches a face named and nearly all of the owner's people do; at 0.80 four strangers in a thousand
#: and four in five of the owner's people, rightly. The margins the owner chose (2026-10-17, phase 2b): precision 99.83%
#: at 0.04 and 99.54% at 0.02 on photo_index's named faces, against 98.46% by the likeness alone.
#: The grouping radius, measured on kr-track (2026-09-24), each hand-named face against the nearest face of its own
#: person, of anyone else, and of a stranger, never one of its own photo:
#:
#:     similarity   own person's reaches   someone else's   a stranger's
#:       0.80              81%                  3.4%             0.6%
#:       0.85              63%                  1.8%             0.1%
#:       0.885             46%                  0.6%             0.0%
#:       0.90              38%                  0.5%             0.0%
#:
#: Grouping chains faces, so each that reaches someone else can pull two people into one group; naming, from anchors
#: and keywords, does the rest. It was 0.48 as a distance in two places. The owner kept 0.885 (2026-09-24).
FACENET_CUTS = Cuts(offer_a_name=0.70, name_without_asking=0.80, name_margin=0.04, offer_margin=0.02, group_at=0.885,
                    source="owner, 2026-09-24 (likeness, grouping) and 2026-10-17 (margin, phase 2b)")

#: AdaFace's (SCRFD and AdaFace's vectors). Read off the phase 3 gate (docs/AI_PIPELINE.md, merge 3 slice C,
#: 2026-10-20): kr-track and renton_parkrun pooled, the open set (a face alone in its folder counted, every name it is
#: given wrong), at the precision the owner chose, 0.99 (2026-10-20): a margin of 0.205 and a likeness of 0.520 to name
#: unasked (FaceNet's on the same reading: 0.140 and 0.795). The two a name is offered at are the same reading at
#: precision 0.98 (owner, 2026-10-20: where FaceNet's 0.70 sits, an offer the owner looks over): a likeness of 0.310
#: and a margin of 0.090.
#: The grouping radius, 0.80: a read-only measurement (2026-10-20) on kr-track and renton_parkrun, 2,849 named faces
#: holding both vectors, pooled: AdaFace at 0.80 groups as impurely as FaceNet at 0.885 (33 wrong pairs, 1 to 2 impure
#: clusters; pair precision 99.68% against 99.48%, purity 0.9988 against 0.9976) and groups 1,646 named faces against
#: FaceNet's 1,243. AdaFace's curve is flat from 0.65 to 0.82. A thin sample, and the chaining over a whole library is
#: not yet seen: a read-only cluster-faces rehearsal on AdaFace's vectors is to follow.
ADAFACE_CUTS = Cuts(offer_a_name=0.310, name_without_asking=0.520, name_margin=0.205, offer_margin=0.090, group_at=0.80,
                    source="the phase 3 gate, open set, pooled (2026-10-20): naming at precision 0.99, offering at 0.98;"
                           " grouping at FaceNet's impurity on 2,849 named faces (2026-10-20)")

#: Each face model's cuts, by its key (tagpup.core.validation.FACE_MODELS): every model a library can be set to has
#: its own.
CUTS = {validation.MTCNN_FACENET: FACENET_CUTS, validation.SCRFD_ADAFACE: ADAFACE_CUTS}


def cuts_of(model):
    """The cuts the vectors of face model `model` (its key: tagpup.store.faces.model_key) are read by."""
    return CUTS[model]


def distance(similarity):
    """The distance between two unit vectors whose cosine similarity is `similarity`:
    what the clustering code, which works in distances, compares against."""
    return math.sqrt(2.0 - 2.0 * similarity)



def margins(table):
    """How much more like each face each person of `table` (a row a face, a column a person: a likeness, NaN or -inf
    where there is none) is than the most alike of the others: for the face's best person, the margin over the
    second-best; for anyone else, how far behind the best (negative, or 0 in a tie). NaN where there is no likeness; a
    person with nobody else to be behind has an infinite margin. The one place a margin is worked out: every gate's
    margin comes from here (tests/test_face_values_have_one_owner.py)."""
    table = np.asarray(table, dtype=np.float64)
    flat = table.ndim == 1
    values = np.atleast_2d(table)
    present = np.isfinite(values)
    values = np.where(present, values, -np.inf)
    found = np.full(values.shape, np.nan)
    if not values.shape[1]:
        return found[0] if flat else found
    first = np.argmax(values, axis=1)
    best = values[np.arange(len(values)), first]
    second = (np.partition(values, values.shape[1] - 2, axis=1)[:, values.shape[1] - 2] if values.shape[1] > 1
              else np.full(len(values), -np.inf))
    others = np.where(np.arange(values.shape[1])[None, :] == first[:, None], second[:, None], best[:, None])
    with np.errstate(invalid="ignore"):
        found = np.where(present, values - others, np.nan)
    return found[0] if flat else found


def ranked(row, limit=None):
    """[(column, likeness, margin)] of the people of `row` (one face's row of a table `margins` reads) that have a
    likeness, the most alike first -- the first of a tie by column -- and at most `limit` of them."""
    row = np.asarray(row, dtype=np.float64)
    margin = margins(row)
    order = [int(column) for column in np.argsort(-np.where(np.isfinite(row), row, -np.inf), kind="stable")
             if np.isfinite(row[column])]
    return [(column, float(row[column]), float(margin[column])) for column in order[:limit]]


def best_of(table):
    """(the column, likeness and margin of each face's best person) of `table`, as arrays a row a face: the column -1
    and NaNs for a face with no likeness. For a block of faces at once, as `ranked` is for one."""
    table = np.atleast_2d(np.asarray(table, dtype=np.float64))
    if not table.shape[1]:
        return np.full(len(table), -1), np.full(len(table), np.nan), np.full(len(table), np.nan)
    present = np.isfinite(table)
    columns = np.argmax(np.where(present, table, -np.inf), axis=1)
    rows = np.arange(len(table))
    found = present[rows, columns]
    return (np.where(found, columns, -1), np.where(found, table[rows, columns], np.nan),
            np.where(found, margins(table)[rows, columns], np.nan))


class FacesByPerson:
    """The faces of a matrix kept one row a face -- the named or decided faces of tagpup.services.identify -- grouped by
    their person, so that a face's likeness to each person is to their closest face (`table`), in a table `margins` reads.
    `people` is each row's person: a person's id, or any number that tells them apart. `keys` are the people of the
    table's columns, in order of their id; `first` the row of each one's first face."""

    def __init__(self, people):
        people = np.asarray(people)
        self.order = np.argsort(people, kind="stable")
        ordered = people[self.order]
        self.starts = np.flatnonzero(np.r_[True, ordered[1:] != ordered[:-1]]) if len(ordered) else np.zeros(0, np.intp)
        self.keys = ordered[self.starts].tolist()
        self.first = self.order[self.starts].tolist()
        self.in_order = bool(np.array_equal(self.order, np.arange(len(self.order))))

    def grouped(self, matrix):
        """`matrix`'s rows (or a vector's entries) in the order `table` reads them as `grouped`: a matrix many blocks of
        faces are compared with is put in order once, not each product (a product of 512 faces with 55,000 named ones
        took 0.18 s to reorder, against 0.01 s to reduce, measured 2026-10-17). Rows already in that order are the
        matrix itself, not a copy."""
        return matrix if self.in_order else matrix[self.order]

    def table(self, similarities, grouped=False):
        """Each face's likeness to each person, from `similarities` (a row a face, a column a face of the matrix; in
        `grouped`'s order when `grouped`): a person's is their closest face's; -inf where none of their faces is."""
        similarities = np.atleast_2d(similarities)
        if not len(self.keys):
            return np.zeros((len(similarities), 0), dtype=similarities.dtype)
        return np.maximum.reduceat(similarities if grouped else similarities[:, self.order], self.starts, axis=1)


class FaceRows(tuple):
    """(ids, people, matrix) of the faces kept one row a face (tagpup.services.identify.named_faces, decided_faces), as
    their callers unpack them, holding their grouping by person (`by_person`): made once for the matrix, the first time it
    is asked, and kept with it for as long as the matrix is kept (tagpup.jobs.identify's cache) -- the strip asked it once a
    face, and each regrouped 55,000 named faces (about 0.34 s against 0.22 s for 40 faces). The rows are put in the order
    of their people's ids when made, so the grouping's matrix is the matrix itself: one copy of it is kept, not two (about
    110 MB on photo_index). Every caller reads a row's face and person from these same lists."""

    def __new__(cls, ids, people, matrix):
        if matrix is not None and len(people) > 1:
            order = np.argsort(np.asarray([person.id for person in people]), kind="stable")
            if not np.array_equal(order, np.arange(len(order))):
                ids, people, matrix = [ids[n] for n in order], [people[n] for n in order], matrix[order]
        rows = super().__new__(cls, (ids, people, matrix))
        rows._grouping = None
        rows._making = threading.Lock()
        return rows

    @classmethod
    def of(cls, rows):
        """`rows` as FaceRows: themselves, or a plain (ids, people, matrix) made into them."""
        return rows if isinstance(rows, cls) else cls(*rows)

    @property
    def by_person(self):
        """(FacesByPerson of the rows' people -- a person's id each --, the matrix in its `grouped` order, or None)."""
        return self._grouped()[:2]

    @property
    def ids_grouped(self):
        """The rows' face ids in `by_person`'s order, an array: to leave one face out of a product with the grouped matrix."""
        return self._grouped()[2]

    def _grouped(self):
        if self._grouping is None:
            with self._making:
                if self._grouping is None:
                    ids, people, matrix = self
                    grouping = FacesByPerson([person.id for person in people])
                    self._grouping = (grouping, grouping.grouped(matrix) if matrix is not None else None,
                                      grouping.grouped(np.asarray(ids)))
        return self._grouping


def singled_out(votes):
    """The one person (or face) `votes` ({who: how many votes}) gives more than any other, or None. A tie at the top is no
    evidence of who is who: the first of a tie by id order was written as a name, a coin toss (owner, 2026-10-15; #1374,
    #1454). Clustering's keyword vote asks it, and its process of elimination, whose candidates have a vote
    each: one face and one person left is the pairing, two of either are a tie."""
    ranked = sorted(votes.values(), reverse=True)
    if not ranked or (len(ranked) > 1 and ranked[0] == ranked[1]):
        return None
    return max(votes, key=votes.get)


# ---- Faces in the background ---------------------------------------------------------

#: A face smaller than this many square pixels is a speck in the background: not named by the tag alone,
#: and clustering names none of those a tenth of the largest's (background_faces).
BACKGROUND_AREA = 2000


def is_background_sized(box):
    """Is a face with this box ([x1, y1, x2, y2]) under BACKGROUND_AREA square pixels? A box that is
    not four numbers is not."""
    try:
        return (box[2] - box[0]) * (box[3] - box[1]) < BACKGROUND_AREA
    except (TypeError, IndexError):
        return False


def background_faces(boxes):
    """Indexes of the faces of one photo, by their boxes ([x1, y1, x2, y2], or None),
    that are noise in the background: under a tenth the area of the largest and under
    2,000 pixels outright. Clustering names none of them and the suggester offers no
    name for them; each wrote this rule itself (docs/findings.md, #74)."""
    areas = [(box[2] - box[0]) * (box[3] - box[1]) if box else 0 for box in boxes]
    largest = max(areas, default=0)
    return {i for i, area in enumerate(areas) if area < 0.10 * largest and area < BACKGROUND_AREA}


# ---- One face in two detections ------------------------------------------------------

#: Two boxes are one face at this overlap or more (docs/AI_PIPELINE.md, phase 3's carrying rule: a face kept or lost
#: between two detections).
SAME_FACE = 0.5


def iou(a, b):
    """The overlap of two boxes (x1, y1, x2, y2): their intersection over their union, 0 for none."""
    across = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    down = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    shared = across * down
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - shared
    return shared / union if union > 0 else 0.0


def pairs(boxes, others, at=SAME_FACE, first=()):
    """{index in boxes: index in others}: each box paired once with the other it overlaps most, the largest overlaps
    first, at `at` (SAME_FACE) or more; of equal overlaps the lower indexes first, so a tie is settled the same way
    every run. The boxes at the indexes in `first` are paired before the rest (the face pass: a named face before an
    unnamed duplicate of it, owner 2026-10-18)."""
    first = set(first)
    candidates = sorted(((overlap, i, j) for i, a in enumerate(boxes) for j, b in enumerate(others)
                         for overlap in (iou(a, b),) if overlap >= at),
                        key=lambda each: (each[1] not in first, -each[0], each[1], each[2]))
    paired, taken = {}, set()
    for _overlap, i, j in candidates:
        if i not in paired and j not in taken:
            paired[i] = j
            taken.add(j)
    return paired


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
        self._tables = {}    # None (everyone) or a tuple of people -> their faces in one matrix (_table), made when first asked
        self._building = threading.Lock()   # Suggest's workers share one KnownFaces: the matrix is made once (_table)
        self._photos = {}    # name -> the keys of the photos they have a face in, made when first asked
        self._eras = {}      # (name, year) -> _chosen for a photo they have no face in, made when first asked
        #: {name: what the caller calls that person}, for a caller that keys the people by something that is not what it shows
        #: (a person's id): see `add`'s label.
        self.labels = {}

    @classmethod
    def of(cls, rows):
        """KnownFaces from (name, embedding, year, photo) rows, or (name, embedding, year, photo, label); year and photo may
        be None. A `name` is any hashable that tells people apart (a person's id, a name)."""
        known = cls()
        for name, embedding, year, photo, *label in rows:
            known.add(name, embedding, year, photo, label[0] if label else None)
        return known

    def add(self, name, embedding, year=None, photo=None, label=None):
        if not name or embedding is None:
            return
        self._faces.setdefault(name, []).append(
            (_unit(embedding), year, paths.key(photo) if photo else None))
        self._arrays.pop(name, None)
        self._photos.pop(name, None)
        self._tables = {}
        if self._eras:
            self._eras = {}
        if label is not None:
            self.labels.setdefault(name, label)

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

    def _chosen(self, name, year, key):
        """Which of `name`'s faces a face of the photo `key` (its paths.key, or None), taken in `year`, is compared with.
        The same for every photo the person has no face in: worked out once a person and a year, and again only for a
        photo they have a face in, without it. Clustering's rounds asked it once a face and a person, 620,000 times on
        photo_index (#1530)."""
        _matrix, years, photos = self._of(name)
        if key is not None:
            inside = self._photos.get(name)
            if inside is None:
                inside = self._photos[name] = frozenset(p for _e, _y, p in self._faces[name] if p)
            if key in inside:
                return self._in_era(years, photos != key, year)
        found = self._eras.get((name, year))
        if found is None:
            found = self._eras[(name, year)] = self._in_era(years, np.ones(len(photos), dtype=bool), year)
        return found

    @staticmethod
    def _in_era(years, usable, year):
        """Of the faces `usable`, taken in `years`, those of the years around `year`: the window for the person's age then,
        widened to five years until it holds five faces, else every usable one."""
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

    #: Faces compared at once by likeness_table: a block of this many by all of the
    #: people's faces is the largest matrix it holds.
    BLOCK = 1024

    def likeness_many(self, name, faces):
        """likeness for each of `faces`, [(embedding, year, photo)], against `name`: `name`'s column of likeness_table,
        so a grid of thousands is one pass rather than thousands."""
        table, columns = self.likeness_table(faces, among=(name,))
        if name not in columns:
            return [None] * len(faces)
        return [value if value == value else None for value in table[:, columns[name]].tolist()]   # NaN is not itself

    def most_like(self, embedding, year=None, photo=None, among=None, skip=()):
        """(name, likeness, margin) of the person `embedding` is most like, of `among` (everyone,
        without) but those in `skip`; (None, None, None) with nobody to compare."""
        table, columns = self.likeness_table([(embedding, year, photo)])
        return self.best(table[0], columns, among, skip)

    @staticmethod
    def best(row, columns, among=None, skip=()):
        """(name, likeness, margin over the next of them, `margins`) of the highest of `row`, a row of likeness_table read
        by the `columns` it came with, of `among` (everyone in it, without) but those in `skip`; the first of a tie in
        `among`'s order, or the columns'; (None, None, None) with nobody to compare. The columns travel with the row, so a
        person added since cannot shift what it is read as."""
        if among is None:   # everyone: the skipped masked, not a list of everyone made a face (the last pass asks 96,000)
            names, values = list(columns), KnownFaces._without(row, columns, skip)
        else:
            names = [name for name in among if name in columns and name not in skip]
            values = row[[columns[name] for name in names]] if names else row[:0]
        column, likeness, margin = (float(each[0]) for each in best_of(values))
        if column < 0:
            return (None, None, None)
        return (names[int(column)], likeness, margin)

    @staticmethod
    def margin_of(row, columns, name, skip=()):
        """The margin (`margins`) of `name` over everyone else of `row`, a row of likeness_table read by its `columns`, but
        those in `skip`: NaN when `name` has no likeness in it."""
        if name not in columns:
            return float("nan")
        return float(margins(KnownFaces._without(row, columns, skip, keep=name))[columns[name]])

    @staticmethod
    def _without(row, columns, skip, keep=None):
        """`row` with the people of `skip` but `keep` left out (NaN, which `margins` reads as no likeness)."""
        values = np.array(row, dtype=np.float64)
        for name in skip:
            if name != keep and name in columns:
                values[columns[name]] = np.nan
        return values

    def likeness_table(self, faces, among=None):
        """likeness of each of `faces`, [(embedding, year, photo)], against every person, or the people `among` that are
        known: (an array, a row a face and a column a person, NaN where likeness is None; {person: their column}, for
        `best`). A block of faces against the people's faces is one matrix product, and the faces of one year share each
        person's years around it (`_chosen`, worked out once a year); only a person with a face in the face's own photo
        is chosen again, without it. Clustering's last pass asked likeness once a face and a person: 96,000 faces by 281
        people on photo_index, over half an hour (docs/findings.md, #1530)."""
        names, matrix, starts, by_photo, eras, columns = self._table(None if among is None else tuple(among))
        table = np.empty((len(faces), len(names)), dtype=np.float32)   # every row is written whole
        if not names or not faces:
            return table, columns
        for start in range(0, len(faces), self.BLOCK):
            block = faces[start:start + self.BLOCK]
            similarities = np.stack([_unit(e) for e, _y, _p in block]) @ matrix.T
            for number, (row, (_e, year, photo)) in enumerate(zip(similarities, block)):
                if year not in eras:
                    eras[year] = self._era(names, starts, year)
                left_out, nobody = eras[year]
                out = table[start + number]
                np.maximum.reduceat(row + left_out, starts, out=out)
                out += nobody
                key = paths.key(photo) if photo else None
                for column in by_photo.get(key, ()):
                    name = names[column]
                    picked = self._chosen(name, year, key)
                    segment = row[starts[column]:starts[column] + len(picked)]
                    out[column] = segment[picked].max() if picked.any() else np.nan
        return table, columns

    def _table(self, among):
        """(names, the faces of everyone or of `among` in one matrix, where each person's rows start, {photo key: the
        columns of the people with a face in it}, {year: `_era`} as each year is first asked, {name: column}), kept for
        each `among` until an `add`. Made under a lock: Suggest's workers ask most_like of one KnownFaces at once, and
        each made its own copy of everyone's faces (about 72 MB on photo_index), the last one kept."""
        table = self._tables.get(among)
        if table is None:
            with self._building:
                table = self._tables.get(among)
                if table is None:
                    table = self._tables[among] = self._build_table(among)
        return table

    def _build_table(self, among):
        """_table, made: everyone's makes each person's own arrays (`_of`) views of its matrix; one person's is theirs."""
        names = list(self._faces) if among is None else [name for name in dict.fromkeys(among) if name in self._faces]
        mats = [self._of(name)[0] for name in names]
        matrix = mats[0] if len(mats) == 1 else np.concatenate(mats) if mats else np.zeros((0, 0), np.float32)
        starts = np.cumsum([0] + [len(self._faces[name]) for name in names[:-1]]).astype(np.intp)
        by_photo = {}
        for column, name in enumerate(names):
            _matrix, years, photos = self._arrays[name]
            if among is None:
                self._arrays[name] = (matrix[starts[column]:starts[column] + len(photos)], years, photos)
            for key in {p for p in photos if p}:
                by_photo.setdefault(key, []).append(column)
        return names, matrix, starts, by_photo, {}, {name: column for column, name in enumerate(names)}

    def _era(self, names, starts, year):
        """`_chosen` for each person side by side, for a face of `year` from a photo none of them is in, as two addends:
        -inf on each face not compared with, NaN for each person with none to compare, else 0."""
        chosen = np.concatenate([self._chosen(name, year, None) for name in names])
        return (np.where(chosen, 0, -np.inf).astype(np.float32),
                np.where(np.logical_or.reduceat(chosen, starts), 0, np.nan).astype(np.float32))
