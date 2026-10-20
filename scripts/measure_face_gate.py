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


def _named(at):
    return ("named %d (right %d, wrong %d), review %d, precision %s, recall %s; of the faces alone in their folder %d "
            "named" % (at["named"], at["right"], at["wrong"], at["review"], _p(at["precision"]), _p(at["recall"]),
                       at["alone_named"]))


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
        out.append("  at precision %.2f:" % precision)
        for by in ("margin", "likeness"):
            for side, key in (("old", face_gate.OLD), ("new", face_gate.NEW)):
                found = both[side][by]
                if found is None:
                    out.append("    %-8s %-7s no cut reaches it" % (by, LABELS[key]))
                    continue
                out.append("    %-8s %-7s cut %.3f%s: %s; unnamed it would name %d of %d" % (
                    by, LABELS[key], found["cut"], " (too thin)" if found["thin"] else "", _named(found["at"]),
                    found["unnamed_named"], found["unnamed"]))
        verdict = {True: "yes", False: "no", None: "cannot tell (too thin, or no cut)"}[both["better"]]
        out.append("    the new model names more faces right at its margin cut: %s" % verdict)
        for side, key in (("old", face_gate.OLD), ("new", face_gate.NEW)):
            found = both[side]["margin"]
            if found is None:
                continue
            bands = "; ".join("%s px %d faces, right %d, wrong %d, recall %s" % (
                "%d-%d" % (band["low"], band["high"] - 1) if band["high"] else "%d+" % band["low"], band["faces"],
                band["right"], band["wrong"], _p(band["recall"])) for band in found["bands"])
            could = found["at"]["faces"]
            floors = "; ".join("%d px: recall %s (%d right under it)" % (
                floor["floor"], _p((found["at"]["right"] - floor["right_under"]) / could if could else None),
                floor["right_under"]) for floor in found["floors"])
            out.append("    %-7s by size: %s" % (LABELS[key], bands))
            out.append("    %-7s were faces under a floor never made: %s" % (LABELS[key], floors))
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
