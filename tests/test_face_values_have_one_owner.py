"""Who a face looks like is decided in one place, tagpup.core.clustering's FaceRows.likeness -- the one gallery (#1737);
the values that decide whether a name looks right are held to it in test_rules_have_one_owner. The margin over the next
person is worked out in one place too, clustering.margins: no service ranks the people a face is like by itself
(TheMarginIsWorkedOutOnce).

A face was compared with a person's mean face in clustering, their geometric median in
TagTuner's grid, and a mean with no years in the suggester, each against a value of its
own; the grid flagged half of every correctly named face (docs/findings.md, #71, #75).
"""
import os
import re
import sys
import unittest

import numpy as np

from tagpup.core import clustering, validation
from tagpup.core.vocabulary import Ref

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shipped_sources import ROOT, python_sources  # noqa: E402

KEY = validation.MTCNN_FACENET


def face(*values):
    vector = np.zeros(8, dtype=np.float32)
    vector[:len(values)] = values
    return vector


def gallery(faces):
    """FaceRows of `faces`, [(Ref, vector, year or None, photo id)], a face id each from 1."""
    return clustering.FaceRows(list(range(1, len(faces) + 1)), [person for person, _v, _y, _p in faces],
                               np.stack([clustering.unit(vector) for _p, vector, _y, _photo in faces]), KEY,
                               [year for _p, _v, year, _photo in faces], [photo for _p, _v, _y, photo in faces])


def a_library_of_faces():
    """A person photographed over twenty years, one over six with undated photos, one with a single face, and one whose
    faces are all from long before the faces asked; 1,500 faces asked, a third from photos a known face is in, some
    undated."""
    rng = np.random.default_rng(7)
    years = {Ref(3, "Oda Castellane"): rng.integers(2005, 2025, 30).tolist(), Ref(1, "Pim Arkwright"): [2019],
             Ref(4, "Wren Halloway"): [None, None] + rng.integers(2015, 2021, 10).tolist(),
             Ref(2, "Lior Fenwick"): rng.integers(1985, 1989, 8).tolist()}
    centres = {person: rng.normal(size=64) for person in years}
    faces = []
    for person, taken in years.items():
        for year in taken:
            faces.append((person, centres[person] + rng.normal(scale=0.8, size=64), year, 100 + len(faces)))
    asked = [(clustering.unit((centres[list(centres)[n % 4]] if n % 5 else rng.normal(size=64))
                              + rng.normal(scale=0.8, size=64)),
              None if n % 7 == 0 else int(rng.integers(1984, 2025)),
              100 + int(rng.integers(len(faces))) if n % 3 == 0 else 5000 + n) for n in range(1500)]
    return gallery(faces), asked


class OneFaceIsAsMuchLikeAPersonWhoeverAsks(unittest.TestCase):
    """A person's page (likeness_of, one person's column), Suggest's best person and cluster-faces' pairing and last pass
    (likeness read by best and margin_of) give one answer, a face and a person, to float32's last bits (#1548, #1737)."""

    def test_the_page_the_suggester_and_cluster_faces_agree(self):
        known, asked = a_library_of_faces()
        vectors, years, photos = np.stack([v for v, _y, _p in asked]), [y for _v, y, _p in asked], [p for _v, _y, p in asked]
        table, columns = known.likeness(vectors, years, photos), known.columns
        self.assertEqual([1, 2, 3, 4], list(columns))   # the people in the order of their ids
        self.assertTrue(np.isfinite(table[:, columns[2]]).all())   # another era: all of theirs, not none
        for person in list(columns) + [99]:   # 99: nobody known
            column = known.likeness_of(person, vectors, years, photos)
            expected = table[:, columns[person]] if person in columns else np.full(len(asked), -np.inf)
            np.testing.assert_allclose(expected, column, rtol=0, atol=1e-6, err_msg=str(person))
        for number in range(len(asked)):
            row = table[number]
            best = clustering.best(row, columns)
            self.assertEqual(float(row[np.isfinite(row)].max()), best[1])
            ahead = sorted(row[np.isfinite(row)].tolist())
            self.assertAlmostEqual(ahead[-1] - ahead[-2], best[2], places=6)   # the margin over the second-best of everyone
            self.assertEqual(best[2], clustering.margin_of(row, columns, best[0]))
            among = [1, 4, best[0]]
            self.assertNotEqual(best[0], clustering.best(row, columns, among, {best[0]})[0])
        nobody = clustering.FaceRows([], [], None, KEY)
        self.assertEqual((None, None, None), clustering.best(nobody.likeness(vectors[:1])[0], nobody.columns))
        lone = gallery([(Ref(1, "Pim Arkwright"), vectors[0], 2019, 1)])
        self.assertEqual(float("inf"), clustering.best(lone.likeness(vectors[1:2])[0], lone.columns)[2])   # nobody else


class AFaceIsComparedWithTheClosestFaceOfItsYears(unittest.TestCase):
    ODA, WREN = Ref(1, "Oda Castellane"), Ref(2, "Wren Halloway")

    def like(self, faces, vector, year, photo, person):
        return float(gallery(faces).likeness_of(person.id, vector[None, :], [year], [photo])[0])

    def test_the_closest_face_not_an_average(self):
        # Two faces of one person far apart; a face beside one of them is like her.
        faces = [(self.ODA, face(1, 0), 2020, 1), (self.ODA, face(0, 1), 2020, 2)]
        self.assertAlmostEqual(1.0, self.like(faces, face(1, 0), 2020, 3, self.ODA), places=5)

    def test_never_a_face_of_the_same_photo(self):
        # Two faces in one photo are two people (owner, 2026-10-22).
        faces = [(self.ODA, face(1, 0), 2020, 1), (self.ODA, face(0, 1), 2020, 2)]
        self.assertAlmostEqual(0.0, self.like(faces, face(1, 0), 2020, 1, self.ODA), places=5)

    # From the years around the photo, every year for an undated one: the scenario P11
    # (tests/test_a_face_gets_one_nearest_person_everywhere.py), in every place a face is asked.


#: Where a shipped file other than clustering.py may order faces by likeness, and why: Identify's grid ranks the faces that
#: grouped with nothing before its cap, faces and not people: by the likeness its cards show to the person sought, or with
#: nobody sought by their best likeness to anyone named (the table's, as the card shows it), and asks no margin
#: (tagpup.services.identify). One argsort. The measurement script's one ranks tag words, not faces
#: (scripts/measure_pipeline_holdouts.py, word_scores).
RANKS_FACES = {os.path.join("scripts", "measure_pipeline_holdouts.py"): 1,   # ranks tag words, not faces
               os.path.join("tagpup", "services", "identify.py"): 1}

#: Any spelling of taking the top of a row: the numpy ones, and the Python ones a second margin could as well be written in.
TOP_OF_A_ROW = re.compile(r"\b(?:nan)?arg(?:max|sort|partition)\(|\.reduceat\(|\bnp\.(?:sort|partition)\(|\bnlargest\("
                          r"|\bsorted\(|\.sort\(|\bmax\([^\n]*\bkey=|\.partition\((?![\"'])")
NUMPY_TOP = re.compile(r"\b(?:nan)?arg(?:max|sort|partition)\(|\.reduceat\(|\bnp\.(?:sort|partition)\(")

#: A file that reads clustering's decisions is where a second margin would be written. The sorts each holds today, every one of
#: paths, names, cards or faces and none of people by likeness (read 2026-10-17, phase 2b); a new one is looked at and counted
#: here, or goes through clustering.ranked / best_of.
SORTS_OF_OTHER_THINGS = {
    os.path.join("scripts", "measure_pipeline_stages.py"): 2,   # its runs by detect_max_side, its roots' places; pairs from clustering
    os.path.join("tagpup", "services", "face_pass.py"): 3,   # photos and folders by path; pairs by the old face's index
    os.path.join("tagpup", "services", "faces.py"): 3,
    os.path.join("tagpup", "services", "identify.py"): 6,   # RANKS_FACES's one among them
    os.path.join("tagpup", "services", "suggester.py"): 4,
    os.path.join("tagpup", "store", "face_tags.py"): 5,
}
READS_CLUSTERING = re.compile(r"from tagpup\.core import [^\n]*\bclustering\b|\bimport clustering\b"
                              r"|from tagpup\.core\.clustering import|import tagpup\.core\.clustering\b")


class TheMarginIsWorkedOutOnce(unittest.TestCase):
    """The best person of a face, and its margin over the next, are clustering's (margins, ranked, best_of, FacesByPerson): a
    service that took the top of a row itself (an argmax, a sort, a partition, a reduceat over people) would have a margin of
    its own, or none (docs/AI_PIPELINE.md, phase 2b). Mutation: services.faces._closest_named back on its own argmax."""

    def sources(self):
        owner = os.path.normcase(os.path.join("tagpup", "core", "clustering.py"))
        for path in python_sources():
            if os.path.normcase(path) != owner:
                with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
                    yield path, handle.read()

    def test_no_shipped_file_but_clustering_takes_the_top_of_a_row(self):
        found = {path: len(NUMPY_TOP.findall(text)) for path, text in self.sources() if NUMPY_TOP.search(text)}
        self.assertEqual(RANKS_FACES, found)

    def test_a_file_that_reads_clustering_adds_no_sort_of_its_own(self):
        found = {path: len(TOP_OF_A_ROW.findall(text)) for path, text in self.sources()
                 if READS_CLUSTERING.search(text) and TOP_OF_A_ROW.search(text)}
        self.assertEqual(SORTS_OF_OTHER_THINGS, found)

    def test_each_spelling_of_a_second_margin_is_caught(self):
        """The guard's own mutations: each line a service could take the next person's likeness with."""
        for spelling in ("second = np.sort(row)[-2]", "second = sorted(values)[-2]", "heapq.nlargest(2, values)",
                         "values.partition(len(values) - 2)", "best = max(people, key=row.get)", "order = row.argsort()",
                         "np.partition(row, -2)", "np.argpartition(row, -2)", "np.nanargmax(row)", "values.sort()"):
            self.assertTrue(TOP_OF_A_ROW.search(spelling), spelling)
        self.assertIsNone(TOP_OF_A_ROW.search('name, _, rest = text.partition(":")'))   # a string's partition is no margin
        for importing in ("from tagpup.core import clustering", "from tagpup.core import dates, clustering as face_rules",
                          "from tagpup.core.clustering import margins", "import tagpup.core.clustering"):
            self.assertTrue(READS_CLUSTERING.search(importing), importing)


if __name__ == "__main__":
    unittest.main()
