"""Deleting the rows the old face detector (MTCNN, of `mtcnn-facenet`) left in a library that is on the new face model
(owner, 2026-10-26: "MTCNN rows should be deleted"; docs/OLD_MODEL_VESTIGES.md, rows 4 and 15). Two kinds, neither
read by anything in a library on the new model:

- the settings of the "Face detection" group (validation.SETTING_GROUPS["faces"]: MTCNN's smallest face, confidence,
  stage thresholds and cap), which LibrarySettings.faces reads only for `mtcnn-facenet` (settings.model_alone);
- the photos' records of detection under MTCNN's name (tagpup.store.faces_detected), which are read only under the
  library's own detector's name (tagpup.services.faces.detector_of: the new model's name is its key alone).

Live, read-only, 2026-10-26 (photo_index / kr-track / renton_parkrun): 3 / 3 / 3 settings rows, 0 / 175 / 139
faces_detected rows; each library on `scrfd-adaface`.

On the maintenance scaffold (tagpup.services.maintenance): a dry run plans and rehearses, nothing written; applied, one
change of the journal (OPERATION) deletes every row as the plan read it, refused whole when one changed since (a
detection recorded meanwhile, a setting changed), and its undo puts every row back, refused only where a row was
written since under its key (journal.KEYS holds `faces_detected` for this). A library on the old model is refused,
nothing written (ON_THE_OLD_MODEL): there its detector reads these rows. A library holding none plans nothing and
writes nothing.
"""
import json

from tagpup.core import validation
from tagpup.services import maintenance
from tagpup.store import db, faces_detected, journal
from tagpup.store import settings as store_settings

#: The name of the change, in History.
OPERATION = "delete old detector rows (mtcnn)"

#: The settings deleted: the "Face detection" group, MTCNN's (validation.SETTINGS, its group "faces").
OLD_SETTINGS = tuple(key for key, setting in validation.SETTINGS.items() if setting["group"] == "faces")

#: What a change counts, by kind (Edit.kind): settings rows and detection records.
KINDS = ("settings", "faces_detected")

#: Why a library still on the old model is refused.
ON_THE_OLD_MODEL = ("Nothing was written: the library is on %s, whose detector reads these rows. Its rows are deleted "
                    "once it is on %s.")


def old_detector(name):
    """Is `name` (a faces_detected detector name: its settings as JSON, faces_detected.detector) MTCNN's? Its names are
    its settings, and name no model; every other model's is its key alone ({"model": key})."""
    return "model" not in json.loads(name)


def _read(library):
    """(the library's face model, {key: value} of the old settings it holds, {photo id: row} of the records under
    MTCNN's names, {name: rows} of every detector name). Reads only."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        model = store_settings.face_model(conn)
        held = store_settings.held_on(conn)
        settings = {key: held[key] for key in OLD_SETTINGS if key in held}
        names = faces_detected.detectors(conn)
        rows = faces_detected.rows_under(conn, [name for name in names if old_detector(name)])
        return model, settings, rows, names
    finally:
        conn.close()


def _plan(library):
    model, settings, rows, names = _read(library)
    old_names = sum(1 for name in names if old_detector(name))
    planned = maintenance.Plan(
        size=len(settings) + len(rows),
        counts={"model": model, "settings": len(settings), "faces_detected": len(rows), "old_names": old_names,
                "kept": sum(count for name, count in names.items() if not old_detector(name))},
        ids={"settings": sorted(settings), "photos": sorted(rows)},
        work=(settings, rows))
    if model == validation.MTCNN_FACENET:
        planned.refused = ON_THE_OLD_MODEL % (model, validation.SCRFD_ADAFACE)
    return planned


def _edits(planned):
    """Each row, deleted while it is what the plan read: the whole row, so the undo puts it back as it was."""
    settings, rows = planned.work
    return ([journal.delete(store_settings.TABLE, (key,), {"value": value}, kind="settings")
             for key, value in sorted(settings.items())]
            + [journal.delete(faces_detected.TABLE, (photo_id,), row, kind="faces_detected")
               for photo_id, row in rows.items()])


def _remaining(library):
    _model, settings, rows, _names = _read(library)
    return {"settings": len(settings), "faces_detected": len(rows)}


def delete(library, apply=False):
    """Plan, and with `apply` make, the deletion of the old detector's rows in `library`, as one change of its journal
    (OPERATION). A Result on the maintenance scaffold: `changed` is rows deleted, details["changed"] by kind (KINDS)."""
    return maintenance.run(library, OPERATION, _plan, _edits, apply=apply, remaining=_remaining, kinds=KINDS)
