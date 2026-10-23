"""A library's settings: what it holds, with the defaults filled in, and changing them
(docs/ARCHITECTURE.md, phase 7.6).

They lived in config.ini, one file for the machine (no longer read, by anything), which nothing in the pages showed:
the CLIP model a library's vectors were made with, the face-detection thresholds,
Suggest's candidate words, the rename format and the ExifTool program. A library opened
on another machine, or with the file edited, was read with settings it was not made
with, and nothing said so. Now each library holds its own (tagpup.store.settings), and
this is their one owner:

- `of` gives a library's settings, every one declared in
  tagpup.core.validation.SETTINGS, a default for any the library does not hold. A
  library that holds none (or only some) is given the defaults for the rest; one
  that holds none is stamped with them first, once.
- `stamp` writes them all, as one journaled change named for where they came from; a
  new library is stamped with the defaults when it is made (tagpup.services.libraries).
- `change` writes the ones asked, as a journaled change, so each is in the library's
  history and can be undone (tagpup.services.journal) -- refused, with nothing written,
  when a value is not one the validator allows, or when it changes a locked setting
  (the CLIP model, face detection, ExifTool) whose group the caller does not name as
  acknowledged. The lock is here, not in the page: a script, the CLI or a tool that set
  model.name unasked left Suggest finding nothing among vectors made with the old model.
- A stamp is a library's first settings: undoing one is refused (STAMPS;
  tagpup.services.journal), as it would leave the library holding none.

Nothing reads config.ini (tests/test_config_single_owner.py). The owner's file may still
be on disk; it is ignored, not deleted.
"""
import os
from dataclasses import dataclass
from typing import Dict

from tagpup.core import paths, validation
from tagpup.core.result import Result
from tagpup.services import sync as sync_service
from tagpup.store import db, journal, schema
from tagpup.store import faces as store_faces
from tagpup.store import settings as store_settings

#: The value each setting has when a library does not say: what a new library is stamped with.
DEFAULTS = validation.setting_defaults()

#: The journal's name for each kind of change to the settings.
WITH_DEFAULTS = "stamp settings with the defaults"
FROM_REPLACED = "stamp settings from the library it replaced"
CHANGE = "change settings"

#: The library's root folders and the folders it ignores (phase 8's sync).
ROOTS = "library.roots"
IGNORED = "library.ignored"
FACE_MODEL = "faces.model"

#: What a stamp writes: every setting but the roots. A library has no roots until the
#: owner sets them (the gear dialog, an ordinary change, which can be
#: undone); nothing sets them on its own (owner, 2026-09-26).
STAMPED = {key: value for key, value in DEFAULTS.items() if key != ROOTS}

#: A stamp no code makes any more: the live libraries' journals hold one each, so it is
#: still known as a stamp (and not undone).
RETIRED_STAMPS = frozenset({"stamp settings from config.ini"})

#: The stamps: a library's first settings, which cannot be undone -- undone, the library
#: held none, and the next read stamped it again.
STAMPS = frozenset({WITH_DEFAULTS, FROM_REPLACED}) | RETIRED_STAMPS
NOT_UNDONE = "A library's first settings cannot be undone; change them instead."

_TRUE = frozenset({"true", "yes", "on", "1"})

#: The share of a library's faces that may be in photos none of whose faces holds the new model's vector -- the pass has
#: not reached them, or found no face in them (store.faces.pass_counts' not_passed; the rows do not tell which) -- when the
#: library is switched to it (settings.change refuses a switch above it). Those faces hold no vector under the model switched to, so they
#: take no part in comparing, clustering or the gallery of named faces until the pass reaches their photos. Not zero:
#: a pass leaves a few photos it could not read or that were added after it (live, 2026-10-20: 16 of kr-track's 11,457
#: faces, 7 of renton_parkrun's 3,644, both under 0.2%), and the owner switches with those and finishes them by a pass
#: again; a library its pass has not walked (photo_index, all of its faces) is far above it. 5% is the default the owner
#: may change here: a library a twentieth of whose faces would drop out of Identify is one the owner should pass first.
UNPASSED_SHARE_ALLOWED = 0.05

#: Why a switch back to FaceNet is refused once none of the library's faces holds a vector under it (switch_to).
NONE_HELD = ("Nothing was written: none of the library's %d faces holds a vector under %s (retired, or never made): "
             "switched to it, no face would take part in comparing, clustering or the gallery of named faces. The snapshot "
             "taken before they were retired holds them (TagPup CLI: snapshots list, snapshots restore).")


def switch_to(library, model):
    """(refusal or None, what the faces hold under `model`): whether the library may be switched to the face model `model`
    (UNPASSED_SHARE_ALLOWED), and one sentence saying how many of its faces hold a vector under it and how many named
    faces hold none, said either way. FaceNet, which made every face the libraries hold before the new model's pass, is refused only
    when none of the library's faces holds a vector under it (NONE_HELD: its vectors retired, tagpup.services.retire_faces
    .apply_vectors), and its pass's count is not read for it (not_passed 0: a face in a photo the new model's pass has not
    reached is no part of a switch back); its sentence says only how many faces hold no FaceNet vector (those the new model
    made while the library was on it), and is left out when none do. A library with no faces is not refused. Read-only, by
    tagpup.store.faces.vector_counts and, for a switch to another model, pass_counts."""
    back = model == validation.MTCNN_FACENET
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        held = store_faces.vector_counts(conn, model)
        passed = held["faces"] and not back
        not_passed = store_faces.pass_counts(conn, validation.MTCNN_FACENET, model)["not_passed"] if passed else 0
    finally:
        conn.close()
    held["not_passed"] = not_passed
    if not held["faces"]:
        return None, held
    if back:
        if held["without"] == held["faces"]:
            return NONE_HELD % (held["faces"], model), held
        if held["without"]:
            held["said"] = ("%d of the library's %d faces hold%s no vector under %s and take%s no part in comparing, "
                            "clustering or the gallery of named faces while the library is on it." % (
                                held["without"], held["faces"], "s" if held["without"] == 1 else "", model,
                                "s" if held["without"] == 1 else ""))
        return None, held
    said = ("%d of the library's %d faces hold a vector under %s; %d named face%s hold%s none, and would leave the named "
            "faces Identify compares with until the pass reaches %s; %d face%s %s in photos where no face holds one (the pass "
            "has not reached them, or found no face in them)." % (
                held["faces"] - held["without"], held["faces"], model, held["named_without"],
                "" if held["named_without"] == 1 else "s", "s" if held["named_without"] == 1 else "",
                "it" if held["named_without"] == 1 else "them", not_passed, "" if not_passed == 1 else "s",
                "is" if not_passed == 1 else "are"))
    held["said"] = said
    if not_passed <= UNPASSED_SHARE_ALLOWED * held["faces"]:
        return None, held
    return ("Nothing was written: %s A switch allows at most %d%% of the faces in such photos: "
            "finish the pass first (TagPup CLI: face-pass <folder> --apply, for each folder of the library)." % (
                said, round(UNPASSED_SHARE_ALLOWED * 100))), held


def undo_refusals(library, change_ids):
    """{change id: why it is not undone now} of each of `change_ids` whose undo would put back a face model (faces.model)
    the library may not be switched to now (switch_to, the check settings.change makes): an undo of a switch back to
    FaceNet, once faces were added that hold FaceNet's vector alone; an undo putting FaceNet back once no face holds its
    vector (its vectors retired). A change that inserted the faces.model row (a library stamped before it was a setting:
    photo_index's switch, change 3244) puts the default back, undone: the row goes. The refusal is the switch's own
    sentence and counts. Reads only: the journal's old values and inserted keys, one read each; each model checked once
    (a switch back to FaceNet reads every face: 0.35 s on photo_index, 2026-10-23)."""
    change_ids = list(change_ids)
    if not change_ids:
        return {}
    restored = journal.old_values(library.path, change_ids, store_settings.TABLE, (FACE_MODEL,), "value")
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        inserted = journal.recorded_values(conn, change_ids, store_settings.TABLE, "insert", "key")
    finally:
        conn.close()
    for change_id, keys in inserted.items():
        if FACE_MODEL in keys:
            restored[change_id] = DEFAULTS[FACE_MODEL]
    checked, reasons = {}, {}
    for change_id, model in restored.items():
        model = _trim(model)
        if model not in checked:
            checked[model] = switch_to(library, model)[0]
        if checked[model]:
            reasons[change_id] = checked[model]
    return reasons


def model_alone(model):
    """The face models' settings (LibrarySettings.faces) of `model`, a model that takes none of MTCNN's arguments
    (tagpup.ml.scrfd_adaface): `model` alone. The one maker of them, so a library switched to it and a face pass's look
    (tagpup.runtime.Runtime.look_at_faces) name one set, and share one model built."""
    return {"model": model}


def _trim(text):
    """Trimmed as the validator trims (tagpup.core.validation.BLANK): a value it allowed
    parses here."""
    return validation.trim(str(text))


def _float(text):
    return float(_trim(text))


@dataclass(frozen=True)
class LibrarySettings:
    """A library's settings: `values`, {key: text} for every declared setting, and
    whether the library holds them (`stamped`) or they are what stamping it would give."""
    values: Dict[str, str]
    stamped: bool = True

    def __getitem__(self, key):
        return self.values[key]

    @property
    def embedder(self):
        """The CLIP model's settings: tagpup.ml.clip.ClipModel's keyword arguments, and
        tagpup.store.embeddings.model_key's, which names the vectors they make."""
        size = _trim(self.values["model.force_image_size"])
        return {
            "model_name": _trim(self.values["model.name"]),
            "pretrained": _trim(self.values["model.pretrained"]),
            "preserve_full_frame": _trim(self.values["model.preserve_full_frame"]).lower() in _TRUE,
            "max_aspect_ratio": _float(self.values["model.max_aspect_ratio"]),
            "force_image_size": int(size) if size else None,
        }

    @property
    def faces(self):
        """The face models' settings, what one set of face models is told apart by (tagpup.runtime): `model`
        (faces.model) and, for MTCNN and FaceNet, tagpup.ml.faces.FaceModel's keyword arguments; the other model
        (tagpup.ml.scrfd_adaface) takes none of MTCNN's, so it is `model` alone. The cap a detection looks at is not one
        of them: detect_max_side."""
        model = _trim(self.values["faces.model"])
        if model != validation.MTCNN_FACENET:
            return model_alone(model)
        return {
            "model": model,
            "min_face_size": int(_trim(self.values["faces.min_face_size"])),
            "confidence_threshold": _float(self.values["faces.confidence_threshold"]),
            "mtcnn_thresholds": [_float(x) for x in self.values["faces.mtcnn_thresholds"].split(",")],
        }

    @property
    def detect_max_side(self):
        """The longest side of the picture the face detector looks at (faces.detect_max_side), or None when the library
        sets no cap (the default): an argument of each detection (tagpup.ml.faces.FaceModel.detect_and_embed_faces),
        so libraries differing only in it share one set of face models (docs/AI_PIPELINE.md, phase 1a)."""
        cap = _trim(self.values["faces.detect_max_side"])
        return int(cap) if cap else None

    @property
    def candidate_words(self):
        """The words CLIP is asked about a photo, before the tree's
        (tagpup.core.suggesting.zero_shot_words), in order."""
        return [_trim(word) for word in self.values["candidates.tags"].split(",") if _trim(word)]

    @property
    def rename_format(self):
        """The pattern Smart Rename names photos with."""
        return self.values["renaming.format"]

    @property
    def roots(self):
        """The library's root folders, as given, in order; [] for none."""
        return _folders(self.values[ROOTS])

    @property
    def ignored(self):
        """The folders sync never offers to include, as given, in order."""
        return _folders(self.values[IGNORED])

    @property
    def exiftool(self):
        """The ExifTool program the library names, or "" for the one the machine has
        (tagpup.config.exiftool_path finds it)."""
        return _trim(self.values["paths.exiftool"])


def _folders(text):
    return validation.folder_list(text)


def excluded_under(library, new_roots, old_roots=(), ignored=()):
    """The folders a change of roots excludes (owner, 2026-09-26: "any folders not added
    assume excluded"): each folder under one of `new_roots`, and under none of
    `old_roots`, that holds photos and no indexed photo now, and is not ignored already
    -- the folders sync would list to review (tagpup.services.sync.review). Only a folder
    that appears later is offered for review. One walk; reads no file."""
    if not new_roots:
        return []
    found = sync_service.review(library, list(new_roots), list(ignored))["folders"]

    def under(folder, roots):
        return any(paths.same(folder, root) or paths.is_under(folder, root) for root in roots)

    return [entry["path"] for entry in found if under(entry["path"], new_roots) and not under(entry["path"], old_roots)]


def _with_excluded(library, held, roots_text, ignored_text=None):
    """(the ignored folders' text once the roots are `roots_text`, how many were added):
    the held ignored folders (or `ignored_text`), and the folders the new roots exclude."""
    old = _folders(held.get(ROOTS, ""))
    new = [root for root in _folders(roots_text) if not any(paths.same(root, other) for other in old)]
    ignored = _folders(held.get(IGNORED, "") if ignored_text is None else ignored_text)
    added = excluded_under(library, new, old, ignored)
    return validation.FOLDER_SEPARATOR.join(ignored + added), len(added)


def _setting_edit(held, key, value):
    if key not in held:
        return journal.insert(store_settings.TABLE, {"key": key, "value": value})
    return journal.update(store_settings.TABLE, (key,), {"value": held[key]}, {"value": value})


def _as_text(value):
    """A value as the table holds it: text, a boolean as true or false, None as empty."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(validation.trim(str(item)) for item in value)
    return validation.trim(str(value))


def _filled(held):
    return {key: held.get(key, default) for key, default in DEFAULTS.items()}


def _stamping(found):
    """What stamping gives: the defaults, with those of `found` ({key: value}, or None) that
    are settings of a library's and that the validator allows."""
    values = dict(STAMPED)
    for key, value in (found or {}).items():
        if key not in values:
            continue   # the roots are never stamped; a key that is no setting is nothing
        text = _as_text(value)
        if not validation.problem(validation.setting_kind(key), text):
            values[key] = text
    return values


def read(library):
    """The library's settings without writing anything: those it holds (a default for any
    it does not), or, for one never stamped -- or not made yet -- the defaults
    (`stamped` False)."""
    held = store_settings.read_only(library.path) if os.path.exists(library.path) else {}
    return LibrarySettings(_filled(held), stamped=bool(held))


def of(library):
    """The library's settings, a default for any it does not hold. A library holding none
    is stamped with the defaults first (`stamp`)."""
    held = store_settings.read(library.path)
    if not held:
        stamp(library)
        held = store_settings.read(library.path)
    return LibrarySettings(_filled(held), stamped=bool(held))


def stamp(library, found=None, operation=WITH_DEFAULTS):
    """Write every setting to a library that holds none, as one journaled change: the
    defaults, but for those `found` ({key: value}: the settings of the library this one
    replaces) gives an allowed value for. Named `operation` (WITH_DEFAULTS, or
    FROM_REPLACED for a library made again in place of one deleted). A library already
    stamped, by another process meanwhile say, is left as it is (changed 0)."""
    values = _stamping(found)
    if operation not in STAMPS - RETIRED_STAMPS:
        raise ValueError("%r is not a stamp" % operation)
    result = Result(attempted=len(values), details={"operation": operation})
    if store_settings.read(library.path):
        return result
    edits = [journal.insert(store_settings.TABLE, {"key": key, "value": value}) for key, value in values.items()]
    try:
        applied = journal.apply(library.path, operation, edits, summary={"settings": len(values)})
    except journal.Refusal:
        # Another process stamped it between the read and the write: its stamp stands.
        return result
    result.changed = applied.changed
    result.details["change"] = applied.change_id
    return result


def _acknowledged(named):
    """The group names `named` gives (a list of them, or one), and the first that is no
    group of settings (validation.SETTING_GROUPS), or None."""
    if named is None:
        return set(), None
    names = [named] if isinstance(named, str) else named
    if not isinstance(names, (list, tuple, set, frozenset)):
        return set(), repr(named)
    for name in names:
        if name not in validation.SETTING_GROUPS:
            return set(), str(name)
    return set(names), None


def unacknowledged(keys, acknowledged=()):
    """The locked groups `keys` touch that `acknowledged` does not name, in the
    declaration's order: [{"group", "title", "consequences"}]."""
    touched = {validation.SETTINGS[key]["group"] for key in keys if validation.SETTINGS[key]["locked"]}
    return [{"group": name, "title": group["title"], "consequences": list(group["consequences"])}
            for name, group in validation.SETTING_GROUPS.items()
            if name in touched and name not in acknowledged]


def _lock_refusal(missing):
    """What a change refused for its locked groups says: each group, and what changing it means."""
    parts = ["Nothing was written: %s locked, and changing %s means --" % (
        "this setting is" if len(missing) == 1 else "these settings are", "it" if len(missing) == 1 else "them")]
    for group in missing:
        parts.append(" %s: %s" % (group["title"], " ".join(group["consequences"])))
    parts.append(" Name %s as acknowledged to change %s: %s." % (
        "it" if len(missing) == 1 else "each", "it" if len(missing) == 1 else "them",
        ", ".join(group["group"] for group in missing)))
    return "".join(parts)


def change(library, values, acknowledged=(), apply=True):
    """Change the settings in `values` ({key: value}) as one journaled change, so it is in
    the library's history and can be undone. Refused, with nothing written, for a key
    that is no setting, a value the validator refuses (its message), a library never
    stamped, or a setting changed by someone else since it was read -- and for a change
    to a locked setting whose group (validation.SETTING_GROUPS: "clip", "face-model",
    "faces", "exiftool") `acknowledged` does not name: the refusal lists each such group and what
    changing it means, and details["unacknowledged"] holds them. A change of the face model
    (faces.model) to one whose pass has not reached more than UNPASSED_SHARE_ALLOWED of the
    library's faces is refused first; either way details["face_vectors"] holds what its faces
    hold under the model switched to, and, for a library with faces, its sentence ("said"; for a switch back to
    FaceNet, only when a face holds no FaceNet vector, and refused when none holds one: switch_to). Every caller -- the
    dialog, the CLI, a tool -- names what it acknowledges, as the dialog asks the owner
    to tick each consequence. `changed` is the settings whose value changed;
    details["locked"] says whether any was a locked one, after which what the models
    made is from the old values.

    A change of the roots (library.roots) adds, in the same change, every folder under a
    new root that holds photos and no indexed photo to the ignored folders
    (excluded_under; owner, 2026-09-26): details["ignored_added"]. Without `apply`, a dry
    run: the same checks and details, nothing written (details["dry_run"]) -- nor a library
    behind this version migrated: details["behind"] counts the migrations it lacks."""
    result = Result(attempted=len(values or {}))
    if not isinstance(values, dict) or not values:
        result.refuse("Say which settings to change.")
        return result
    acknowledged, unknown = _acknowledged(acknowledged)
    if unknown is not None:
        result.refuse("There is no group of settings called %s to acknowledge." % unknown)
        return result
    wanted = {}
    for key, value in values.items():
        if key not in validation.SETTINGS:
            result.refuse("There is no setting called %s." % key)
            return result
        text = _as_text(value)
        problem = validation.problem(validation.setting_kind(key), text)
        if problem:
            result.refuse(problem)
            return result
        wanted[key] = text
    if apply:
        held = store_settings.read(library.path)
    else:
        # A dry run reads without migrating a library behind this version, and says so.
        held = store_settings.read_only(library.path)
        result.details["behind"] = len(schema.pending(library.path))
    if not held:
        result.refuse("The library's settings have not been stamped yet: open it first.")
        return result
    if ROOTS in wanted and wanted[ROOTS] != held.get(ROOTS):
        ignored, added = _with_excluded(library, held, wanted[ROOTS], wanted.get(IGNORED))
        result.details["ignored_added"] = added
        if added:
            wanted[IGNORED] = ignored
    edits, changed = [], []
    for key, text in wanted.items():
        if key not in held:
            edits.append(journal.insert(store_settings.TABLE, {"key": key, "value": text}))
        elif held[key] != text:
            edits.append(journal.update(store_settings.TABLE, (key,), {"value": held[key]}, {"value": text}))
        else:
            continue
        changed.append(key)
    result.details.update({"changed": changed, "locked": any(validation.SETTINGS[k]["locked"] for k in changed)})
    if FACE_MODEL in changed:
        # Before the consequences are asked for: ticking them cannot make a library its pass has not reached switchable.
        refusal, result.details["face_vectors"] = switch_to(library, _trim(wanted[FACE_MODEL]))
        if refusal:
            result.refuse(refusal)
            return result
    missing = unacknowledged(changed, acknowledged)
    if missing:
        result.details["unacknowledged"] = missing
        result.refuse(_lock_refusal(missing))
        return result
    result.details["dry_run"] = not apply
    if not edits or not apply:
        return result
    try:
        applied = journal.apply(library.path, CHANGE, edits, summary={"settings": sorted(changed)})
    except journal.Refusal as e:
        result.refuse("Nothing was written: %s" % e)
        return result
    result.changed = applied.changed
    result.details["change"] = applied.change_id
    return result


def ignore_folder(library, folder):
    """Add `folder` to the library's ignored folders, as a change of its settings, so it is
    in the library's history and can be undone: Ignore, on a folder sync lists to review.
    Refused for a folder the rules do not take as one; `changed` 0 for one ignored
    already."""
    result = Result(attempted=1)
    problem = validation.problem("folder", folder)
    if problem:
        result.refuse(problem)
        return result
    ignored = of(library).ignored
    if any(paths.same(folder, other) for other in ignored):
        return result
    return change(library, {IGNORED: validation.FOLDER_SEPARATOR.join(ignored + [paths.stored(folder)])})


def described(settings, app=None):
    """The settings as the dialog is made from them: each group, in order, with its title,
    whether it is locked and what changing it does, and each of its settings' declaration
    (label, type, default, info, locked, consequences) with the library's value. With
    `app` ("tagpup" or "tuner"), only the groups that app's gear shows."""
    groups = []
    for name, group in validation.SETTING_GROUPS.items():
        if app is not None and group.get("app") != app:
            continue
        members = [(key, declared) for key, declared in validation.SETTINGS.items() if declared["group"] == name]
        if not members:
            continue
        groups.append({
            "name": name,
            "title": group["title"],
            "locked": any(declared["locked"] for _key, declared in members),
            "consequences": list(group["consequences"]),
            "settings": [{"key": key, "kind": validation.setting_kind(key), "label": declared["label"],
                          "type": declared["type"], "default": declared["default"], "info": declared["info"],
                          "locked": declared["locked"], "consequences": list(declared["consequences"]),
                          "value": settings.values[key]} for key, declared in members],
        })
    return {"stamped": settings.stamped, "groups": groups}
