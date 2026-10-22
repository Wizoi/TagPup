"""The named-face hold-out (docs/AI_PIPELINE.md, section 7, and phase 3's gate): each named face matched against the named
faces of every OTHER group -- a photo, or stricter a folder (bursts and copies of one moment are in one folder) -- by the
closest face of each person, as tagpup.core.clustering names a face (FacesByPerson, best_of, its margins), of the years
around its own where the years are given (the one gallery's window, #1737), else over all years (phase 0's curve); the
face each was matched with is said, so the year gap to it can be read (year_gap); and the precision
of the top person at each cut of its likeness and of its margin over the second-best person. Phase 0 read FaceNet's curve
with it (scripts/measure_pipeline_holdouts.py); the gate reads both face models' curves on the same faces with it, and
each one's precision by the year gap (tagpup.services.face_gate). Pure: unit vectors and integers in, counts out.
"""
import numpy as np

from tagpup.core import clustering

#: The likeness cuts of phase 0's face curve; clustering's two naming cuts among them.
LIKENESS_CUTS = tuple(round(0.40 + 0.05 * i, 2) for i in range(12))
#: The margin cuts: the top person's likeness over the second-best person's.
MARGIN_CUTS = tuple(round(0.02 * i, 2) for i in range(16))
#: The cuts a threshold is read off at (the gate): every 0.005 of likeness and of margin from 0 to 1 (a margin is a
#: difference of two likenesses; AdaFace's run past 0.7 on the live libraries). A grid, so that two models' curves, and
#: the libraries' curves pooled, are read at the same points.
FINE_LIKENESS_CUTS = tuple(round(0.005 * i, 3) for i in range(201))
FINE_MARGIN_CUTS = FINE_LIKENESS_CUTS


def matched_against(queries, groups, gallery, people, gallery_groups, own=None, block=1024, years=None, gallery_years=None):
    """For each row of `queries` (unit vectors): (its best person among the `gallery`'s, that likeness, its margin over the
    second-best person, whether its own person had a face to compare, the gallery row of that person's face it was matched
    with -- their closest --, -1 for none), each person's likeness being their closest face of another group -- a query is
    never compared with a face of its own group (`groups`, `gallery_groups`: an integer a row) -- of the years around the
    query's own (`years`, `gallery_years`: a float a row, NaN undated; clustering.FacesByPerson.table: the one gallery's
    window, the window worked out from the faces of the other groups), all years where either is not given. The margin is
    the gates' (clustering.margins), so the curve is read off the rule it sets. `people` is the gallery's person a row;
    `own`, the queries' when they are named (else reachable is all False). A block of rows at a time."""
    count = len(queries)
    best, first, margin = np.full(count, -1, dtype=np.int64), np.empty(count), np.empty(count)
    chosen = np.full(count, -1, dtype=np.int64)
    reachable = np.zeros(count, dtype=bool)
    grouping = clustering.FacesByPerson(people, gallery_years if years is not None else None)
    grouped, grouped_groups = grouping.grouped(gallery), grouping.grouped(gallery_groups)
    names = np.asarray(grouping.keys, dtype=np.int64)
    column_of = {name: column for column, name in enumerate(grouping.keys)}
    for start in range(0, count, block):
        rows = slice(start, start + block)
        likeness = queries[rows] @ grouped.T
        likeness[groups[rows][:, None] == grouped_groups[None, :]] = -np.inf
        asked = None if years is None else years[rows]
        by_person = grouping.table(likeness, grouped=True, years=asked)
        columns, first[rows], margin[rows] = clustering.best_of(by_person)
        best[rows] = np.where(columns >= 0, names[columns] if len(names) else -1, -1)
        chosen[rows] = grouping.closest(likeness, columns, grouped=True, years=asked)
        if own is not None:
            mine = np.fromiter((column_of.get(person, -1) for person in own[rows].tolist()), dtype=np.intp)
            seen = by_person[np.arange(len(mine)), np.maximum(mine, 0)] if by_person.shape[1] else np.full(len(mine), -np.inf)
            reachable[rows] = (mine >= 0) & np.isfinite(seen)
    return best, first, margin, reachable, chosen


def year_gap(years, gallery_years, chosen):
    """The years between each query (`years`, a float a row, NaN undated) and the gallery face it was matched with
    (`chosen`, matched_against's: `gallery_years` of that row): NaN when either is undated or nothing was matched."""
    matched = np.where(chosen >= 0, np.asarray(gallery_years, dtype=np.float64)[np.maximum(chosen, 0)], np.nan)
    return np.abs(np.asarray(years, dtype=np.float64) - matched)


def curve(correct, values, cuts):
    """[{"cut", "suggested", "right", "precision", "share"}] of the suggestions whose `values` is at each cut or above."""
    points = []
    for cut in cuts:
        kept = values >= cut
        suggested, right = int(kept.sum()), int((correct & kept).sum())
        points.append({"cut": cut, "suggested": suggested, "right": right,
                       "precision": right / suggested if suggested else None,
                       "share": suggested / len(values) if len(values) else None})
    return points


def precision_curve(correct, likeness, margin, likeness_cuts=LIKENESS_CUTS, margin_cuts=MARGIN_CUTS):
    """The precision of the top suggestion at each cut: {"likeness": curve, "margin": curve}."""
    return {"likeness": curve(correct, likeness, likeness_cuts), "margin": curve(correct, margin, margin_cuts)}


def hold_out(matrix, people, groups, likeness_cuts=LIKENESS_CUTS, margin_cuts=MARGIN_CUTS):
    """The curve and its counts for faces matched only against faces of another group (a photo, a folder): `people`
    and `groups` an integer a row."""
    best, first, margin, reachable, _chosen = matched_against(matrix, groups, matrix, people, groups, own=people)
    correct = best == people
    return {"faces": len(people), "own_person_only_in_this_group": int((~reachable).sum()),
            "evaluated": int(reachable.sum()), "top_right": int(correct[reachable].sum()),
            "curve": precision_curve(correct[reachable], first[reachable], margin[reachable], likeness_cuts, margin_cuts)}


def numbered(keys):
    """An integer for each key, equal keys equal."""
    seen = {}
    return np.array([seen.setdefault(key, len(seen)) for key in keys], dtype=np.int64)


def too_thin(faces, precision):
    """Is a precision read from `faces` too thin to tell `precision` from what is under it? Thin when the standard error of
    a proportion at `precision`, sqrt(p(1 - p) / n), is more than half of the error it allows, 1 - p: when n is under
    4p / (1 - p) (76 faces at 0.95, 196 at 0.98, 396 at 0.99)."""
    return faces < 4 * precision / (1 - precision)


def operating_point(points, precision):
    """The point of a `curve` the cut is read off at for `precision`: going down from the highest cut, the last one before
    the precision first falls under it (a cut with no suggestion passes), so every cut at it or above holds `precision`.
    None when even the highest cut that suggests anything falls under it."""
    found = None
    for point in reversed(points):
        if point["suggested"] and point["precision"] < precision:
            break
        if point["suggested"]:
            found = point
    return found
