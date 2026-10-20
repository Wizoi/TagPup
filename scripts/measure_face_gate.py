"""The gate of phase 3, read-only (docs/AI_PIPELINE.md, phase 3, "Gate", slice C): the new face model against FaceNet on the
same held-out named faces of each library given, and pooled, at each precision asked. Counts only: nothing printed or
written names a person, a photo or a folder. Opens each library read-only and writes nothing to it; no model is loaded.

    .venv/Scripts/python.exe scripts/measure_face_gate.py --source data/kr-track.db --source data/renton_parkrun.db
        [--precision 0.95 --precision 0.98 --precision 0.99] [--out <folder outside the repository>]

What it reads and how: tagpup.services.face_gate.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sandbox  # noqa: E402

import _root  # noqa: E402,F401
import numpy as np  # noqa: E402
from tagpup.core import holdout  # noqa: E402
from tagpup.services import face_gate, face_pass  # noqa: E402

LABELS = {face_gate.OLD: "FaceNet", face_gate.NEW: "AdaFace"}
SHOWN_MARGINS = (0.0, 0.02, 0.04, 0.06, 0.1, 0.2)


def _p(value):
    return "-" if value is None else "%.4f" % value


def _cut(found):
    return "cut %.3f%s%s" % (found["cut"], " (no cut: it names every face)" if found["every"] else "",
                             " (too thin)" if found["thin"] else "")


def _closed(at):
    return ("named %d (right %d, wrong %d), review %d, precision %s, recall %s; beside them it names %d faces alone in "
            "their folder, all wrong: precision with them %s" % (
                at["named"], at["right"], at["wrong"], at["review"], _p(at["precision"]), _p(at["recall"]),
                at["alone_named"], _p(at["open_precision"])))


def _open(at):
    left = at["alone"] - at["alone_named"]
    return ("named %d (right %d, wrong %d, %d of them alone in their folder), review %d (%d of them alone), precision %s, "
            "recall %s" % (at["named"] + at["alone_named"], at["right"], at["wrong"] + at["alone_named"],
                           at["alone_named"], at["review"] + left, left, _p(at["open_precision"]), _p(at["recall"])))


def _curve(found):
    reachable = found["reachable"]
    values = np.nan_to_num(found["margin"][reachable], nan=-np.inf)
    return "  ".join("%.2f: %s (%d)" % (point["cut"], _p(point["precision"]), point["suggested"])
                     for point in holdout.curve(found["right"][reachable], values, SHOWN_MARGINS))


def lines(name, reading, answer):
    """The text of one library's (or the pooled) answer."""
    counts = answer["pass"]
    out = ["%s: %d of %d photos passed; faces: %d matched, %d unmatched (%d named: the pass's review list), %d new, "
           "%d in photos not passed; the journal: %d changes, %d matched, %d new. Skipped, too small and failed are not "
           "kept by the library: the face-pass run's own line says them." % (
               name, counts["photos_passed"], counts["photos"], counts["matched"], counts["unmatched"],
               counts["unmatched_named"], counts["new"], counts["not_passed"], counts["changes"],
               counts["journal_matched"], counts["journal_new"])]
    models = reading["models"]
    if not models:
        return out + ["  no named face holds both models' vectors: nothing to score"]
    faces = len(models[face_gate.OLD]["reachable"])
    out.append("  named faces holding both vectors: %d; evaluated (their person in another folder) %d%s" % (
        faces, answer["evaluated"], ": TOO THIN to read (under %d)" % face_gate.FEWEST if answer["thin"] else ""))
    for key in (face_gate.OLD, face_gate.NEW):
        out.append("  %s by margin, precision (named): %s" % (LABELS[key], _curve(models[key])))
    for precision, both in answer["precisions"].items():
        for kind, title, said in (
                ("closed", "closed set, the faces whose person has a face in another folder (the models compared)", _closed),
                ("open", "open set, every named face: the names of faces whose person is not in the gallery (alone in "
                 "their folder) are all wrong", _open)):
            out.append("  at precision %.2f, %s:" % (precision, title))
            for by in ("margin", "likeness"):
                for side, key in (("old", face_gate.OLD), ("new", face_gate.NEW)):
                    found = both[side][kind][by]
                    if found is None:
                        out.append("    %-8s %-7s no cut reaches it" % (by, LABELS[key]))
                        continue
                    out.append("    %-8s %-7s %s: %s; unnamed it would name %d of %d" % (
                        by, LABELS[key], _cut(found), said(found["at"]), found["unnamed_named"], found["unnamed"]))
        verdict = {True: "yes", False: "no", None: "cannot tell (too thin, or no cut)"}[both["better"]]
        out.append("    the new model names more faces right at its margin cut, on the closed set: %s" % verdict)
        for side, key in (("old", face_gate.OLD), ("new", face_gate.NEW)):
            found = both[side]["closed"]["margin"]
            if found is None:
                continue
            bands = "; ".join("%s px %d faces, right %d, wrong %d, recall %s%s" % (
                "%d-%d" % (band["low"], band["high"] - 1) if band["high"] else "%d+" % band["low"], band["faces"],
                band["right"], band["wrong"], _p(band["recall"]), " (thin)" if band["thin"] else "")
                for band in found["bands"])
            floors = "; ".join("%d px: recall %s counting the %d faces under it as missed (%d right), %s among the faces "
                               "at or above it" % (floor["floor"], _p(floor["recall_counting_under"]),
                                                   floor["faces_under"], floor["right_under"],
                                                   _p(floor["recall_at_or_above"])) for floor in found["floors"])
            out.append("    %-7s by size (closed set; thin: too few named to tell the precision): %s" % (LABELS[key], bands))
            out.append("    %-7s were faces under a floor never made: %s. The floor applies only to a detection no old "
                       "face took, so an old face under it is not lost: the first reading is the bound, the second the "
                       "recall of the faces it keeps" % (LABELS[key], floors))
    return out


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value.item() if isinstance(value, np.generic) else value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", action="append", required=True, help="a library, opened read-only and never written")
    parser.add_argument("--precision", action="append", type=float, help="a precision the owner may choose (repeat)")
    parser.add_argument("--out", help="a folder outside the repository for the answer as JSON")
    args = parser.parse_args(argv)
    precisions = args.precision or [0.95, 0.98, 0.99]
    if any(not 0 < p < 1 for p in precisions):
        raise SystemExit("A precision is between 0 and 1, not 0 or 1.")
    print("the gate: %s against %s, held out by folder, the floor today %d px (read-only)" % (
        LABELS[face_gate.NEW], LABELS[face_gate.OLD], face_pass.NEW_FACE_FLOOR))
    readings, answers = [], {}
    for source in args.source:
        name = os.path.splitext(os.path.basename(source))[0]
        readings.append(face_gate.read(os.path.abspath(source)))
        answers[name] = face_gate.score(readings[-1], precisions)
        print("\n".join(lines(name, readings[-1], answers[name])))
    if len(readings) > 1:
        both = face_gate.pooled(readings)
        answers["pooled"] = face_gate.score(both, precisions)
        print("\n".join(lines("pooled", both, answers["pooled"])))
    if args.out:
        path = os.path.join(sandbox.outside_the_repository(args.out), "face-gate-%s.json" % time.strftime("%Y%m%dT%H%M%S"))
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(_jsonable(answers), handle, indent=1)
        print("written %s" % path)
    return answers


if __name__ == "__main__":
    main()
