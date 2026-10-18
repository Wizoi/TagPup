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
import threading

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


def might_name_unasked(similarities, slack):
    """Which of `similarities` (an array) names_unasked could accept, were each off by
    as much as `slack`: for a rough first look that must miss none it would name
    (tagpup.services.identify.UnnamedFaces)."""
    return similarities >= NAME_WITHOUT_ASKING - slack


def looks_wrong(similarity):
    """Is a name on a face only `similarity`-like its person worth a second look? Below
    the value it would be offered at, it would not even have been suggested."""
    return similarity is not None and similarity < OFFER_A_NAME


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
        """(name, likeness) of the person `embedding` is most like, of `among` (everyone,
        without) but those in `skip`; (None, None) with nobody to compare."""
        table, columns = self.likeness_table([(embedding, year, photo)])
        return self.best(table[0], columns, among, skip)

    @staticmethod
    def best(row, columns, among=None, skip=()):
        """(name, likeness) of the highest of `row`, a row of likeness_table read by the `columns` it came with, of `among`
        (everyone in it, without) but those in `skip`; the first of a tie in `among`'s order, or the columns'; (None, None)
        with nobody to compare. The columns travel with the row, so a person added since cannot shift what it is read as."""
        names = [name for name in (columns if among is None else among) if name in columns and name not in skip]
        values = row[[columns[name] for name in names]] if names else row[:0]
        if np.isnan(values).all():
            return (None, None)
        first = int(np.nanargmax(values))   # the first of the highest, as a person by person look kept it
        return (names[first], float(values[first]))

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
