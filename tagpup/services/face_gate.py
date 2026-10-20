"""The gate of phase 3, read-only (docs/AI_PIPELINE.md, phase 3, "Gate", and slice C as built): the new face model and
FaceNet scored on the same named faces -- those holding a vector under both keys after the pass -- each named face matched
against the library's other named faces of every OTHER folder (tagpup.core.holdout, the way section 7 read FaceNet's curve),
by the margin of its top person over the second-best (clustering.margins) and by its likeness.

For each precision the owner may choose, the cut is read off each model's curve (holdout.operating_point) twice. On the
closed set: the faces whose person has a face in another folder, the precision right / named among them -- the comparison
between the models, and the verdict's. On the open set: every named face, a face alone in its folder too, whose person is
not in the gallery by construction, so every name given it is wrong -- the precision the owner would live with, right /
(named of both), and its cut. Each is said with what it names: right, wrong, left for review, the recall (named right of
the faces that could be), the library's unnamed faces it would name, and on the closed set the same by the face's shorter side (the bands, and the floors a new face could be made at:
tagpup.ml.scrfd_adaface.NEW_FACE_FLOOR is read here). A reading is "too thin" when its faces cannot tell the precision asked from what is
under it (holdout.too_thin). And the pass's counts, from the library's rows and journal. Nothing is written; a library is
opened read-only (db.readonly_uri), and nothing said names a person, a photo or a folder: counts only.
"""
import json

import numpy as np

from tagpup.core import holdout
from tagpup.core.validation import MTCNN_FACENET, SCRFD_ADAFACE
from tagpup.services import face_pass
from tagpup.store import db, journal
from tagpup.store import faces as store_faces

#: The model the library reads today, and the one the pass gave its faces.
OLD, NEW = MTCNN_FACENET, SCRFD_ADAFACE
#: The bands of a face's shorter side, in pixels of the picture as stored: [low, high).
BANDS = ((0, 30), (30, 40), (40, 50), (50, None))
#: The floors a new face could be made at (tagpup.ml.scrfd_adaface.NEW_FACE_FLOOR among them: a test of the gate fails
#: when the floor is moved to one not read here).
FLOORS = (30, 40, 50)
#: Under this many faces evaluated, a library's curve is too thin to read at all.
FEWEST = 100


def _matrix(blobs):
    matrix = np.array([np.frombuffer(blob, dtype=np.float32) for blob in blobs], dtype=np.float32).reshape(len(blobs), -1)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms > 0, norms, 1)


def _short_side(box):
    x1, y1, x2, y2 = json.loads(box)[:4]
    return min(x2 - x1, y2 - y1)


def read(db_path, old=OLD, new=NEW):
    """What the gate reads of one library, opened read-only: {"pass": the rows' counts (store.faces.pass_counts) and the
    journal's ("changes", "journal_matched", "journal_new" of the pass's kept changes), and "unnamed_nobody", the unnamed
    faces holding both vectors a person called nobody by hand (store.faces.manual_names), left out of the unnamed; "models":
    {key: the named faces matched against each other by it, a face a row: "right", "reachable", "likeness", "margin",
    "short"; "unnamed_likeness", "unnamed_margin" of the other unnamed faces holding both vectors against the named}},
    empty when no named face holds both. The unnamed are held out by photo (each against the named of other photos: it
    has no person, so no folder of its person to leave out), while the cut they are read at is the named faces' by folder."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        named = store_faces.under_both(conn, old, new, named=True)
        unnamed = store_faces.under_both(conn, old, new, named=False) if named else []
        nobody = {face_id for face_id, ref in store_faces.manual_names(conn).items() if ref is None} if unnamed else set()
        counts = store_faces.pass_counts(conn, old, new)
        kept = journal.kept_named(conn, face_pass.OPERATION % new)
    finally:
        conn.close()
    counts.update(changes=len(kept), journal_matched=sum(int(s.get("matched", 0)) for _id, s in kept),
                  journal_new=sum(int(s.get("new", 0)) for _id, s in kept),
                  unnamed_nobody=sum(row[0] in nobody for row in unnamed))
    unnamed = [row for row in unnamed if row[0] not in nobody]
    people = np.array([row[1] for row in named], dtype=np.int64)
    folders = holdout.numbered(row[3] for row in named)
    photos = np.array([row[2] for row in named], dtype=np.int64)
    unnamed_photos = np.array([row[2] for row in unnamed], dtype=np.int64)
    short = np.array([_short_side(row[4]) for row in named], dtype=np.float64)
    models = {}
    for key, column in ((old, 5), (new, 6)) if named else ():
        matrix = _matrix([row[column] for row in named])
        best, likeness, margin, reachable = holdout.top_two(matrix, people, folders)
        found = {"right": best == people, "reachable": reachable, "likeness": likeness, "margin": margin, "short": short,
                 "unnamed_likeness": np.empty(0), "unnamed_margin": np.empty(0)}
        if unnamed:
            _best, found["unnamed_likeness"], found["unnamed_margin"], _seen = holdout.matched_against(
                _matrix([row[column] for row in unnamed]), unnamed_photos, matrix, people, photos)
        models[key] = found
    return {"pass": counts, "models": models}


def pooled(readings):
    """The libraries' readings as one: each face was matched within its own library, so their rows are put end to end."""
    models, scored = {}, [reading for reading in readings if reading["models"]]
    for key in scored[0]["models"] if scored else ():
        parts = [reading["models"][key] for reading in scored]
        models[key] = {name: np.concatenate([part[name] for part in parts]) for name in parts[0]}
    counts = {name: sum(reading["pass"][name] for reading in readings) for name in readings[0]["pass"]}
    return {"pass": counts, "models": models}


def _at(found, kept, cut, by):
    """What naming the faces `kept` (a mask) at `cut` of `by` gives. On the faces whose person has a face in another folder
    (`faces`; the closed set): named right or wrong, left for review, its precision and recall. And `alone_named`, the faces
    whose person has none there (`alone`) that it would name: someone else, since their own is not in the gallery, so each
    is wrong; `open_precision` counts them: right / (named + alone_named)."""
    named = kept & (np.nan_to_num(found[by], nan=-np.inf) >= cut)
    could = kept & found["reachable"]
    right = int((named & could & found["right"]).sum())
    named_could = int((named & could).sum())
    alone_named = int((named & ~found["reachable"]).sum())
    return {"faces": int(could.sum()), "named": named_could, "right": right, "wrong": named_could - right,
            "review": int((could & ~named).sum()), "alone": int((kept & ~found["reachable"]).sum()),
            "alone_named": alone_named,
            "precision": right / named_could if named_could else None,
            "open_precision": right / (named_could + alone_named) if named_could + alone_named else None,
            "recall": right / int(could.sum()) if could.any() else None}


def _reading(found, faces, precision, by, cuts):
    """The cut by `by` for `precision` on the curve of the faces `faces` (a mask: the closed set's, or every face, the open
    set's: a face alone in its folder counts as named wrong), and what it names; None when no cut reaches it. "every": the
    cut is the lowest that names anything at all, so it is no cut -- it names every face it has a person for."""
    values = np.nan_to_num(found[by][faces], nan=-np.inf)
    point = holdout.operating_point(holdout.curve((found["right"] & found["reachable"])[faces], values, cuts), precision)
    if point is None:
        return None
    cut = point["cut"]
    return {"cut": cut, "every": bool(point["suggested"] == int(np.isfinite(values).sum())),
            "at": _at(found, np.ones(len(faces), dtype=bool), cut, by),
            "thin": bool(holdout.too_thin(point["suggested"], precision)),
            "unnamed_named": int((np.nan_to_num(found["unnamed_" + by], nan=-np.inf) >= cut).sum()),
            "unnamed": len(found["unnamed_" + by])}


def _floor(found, at, floor, cut, by):
    """What a floor at `floor` px would have left of the named right at `cut`, read twice: "recall_counting_under" counts an
    old face under the floor as missed (right_under taken from the right, over every face that could be named), and
    "recall_at_or_above" leaves those faces out (the floor stops the pass making a new face under it; an old face there is
    kept, so this is the recall among the faces at or above it)."""
    under = found["reachable"] & (found["short"] < floor)
    right_under = int((under & found["right"] & (np.nan_to_num(found[by], nan=-np.inf) >= cut)).sum())
    above = at["faces"] - int(under.sum())
    return {"floor": floor, "right_under": right_under, "faces_under": int(under.sum()),
            "recall_counting_under": (at["right"] - right_under) / at["faces"] if at["faces"] else None,
            "recall_at_or_above": (at["right"] - right_under) / above if above else None}


def _model(found, precision):
    """One model's reading at `precision`: {"closed": {by: reading}, "open": {by: reading}} by margin and by likeness
    (_reading), the closed set's margin reading by size too."""
    reachable = found["reachable"]
    answer = {"evaluated": int(reachable.sum()), "faces": len(reachable), "alone": int((~reachable).sum()),
              "closed": {}, "open": {}}
    for by, cuts in (("margin", holdout.FINE_MARGIN_CUTS), ("likeness", holdout.FINE_LIKENESS_CUTS)):
        answer["open"][by] = _reading(found, np.ones(len(reachable), dtype=bool), precision, by, cuts)
        reading = answer["closed"][by] = _reading(found, reachable, precision, by, cuts)
        if by == "margin" and reading is not None:
            cut, short = reading["cut"], found["short"]
            reading["bands"] = [dict(at, low=low, high=high, thin=bool(holdout.too_thin(at["named"], precision)))
                                for low, high in BANDS
                                for at in [_at(found, (short >= low) & ((short < high) if high else True), cut, by)]]
            reading["floors"] = [_floor(found, reading["at"], floor, cut, by) for floor in FLOORS]
    return answer


def score(reading, precisions, old=OLD, new=NEW):
    """The gate's answer for one reading (a library's, or pooled): {"pass", "thin", "precisions": {p: {"old", "new",
    "better"}}}; "better" is whether the new model names more right at its margin cut on the closed set (the comparison
    between the models: both are read on the same faces), or None when either is too thin or reaches no cut."""
    models = reading["models"]
    evaluated = min(int(found["reachable"].sum()) for found in models.values()) if models else 0
    answer = {"pass": reading["pass"], "evaluated": evaluated, "thin": evaluated < FEWEST, "precisions": {}}
    for precision in precisions:
        both = {"old": _model(models[old], precision), "new": _model(models[new], precision)} if models else {}
        cuts = [both[side]["closed"]["margin"] for side in ("old", "new")] if both else [None]
        # The same faces are evaluated by both, so naming more right at the precision is the higher recall.
        both["better"] = (None if answer["thin"] or any(cut is None or cut["thin"] for cut in cuts)
                          else cuts[1]["at"]["right"] > cuts[0]["at"]["right"])
        answer["precisions"][precision] = both
    return answer
