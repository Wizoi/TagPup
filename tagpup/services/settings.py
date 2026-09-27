"""A library's settings: what it holds, with the defaults filled in, and changing them
(docs/ARCHITECTURE.md, phase 7.6).

They lived in config.ini, one file for the machine, which nothing in the pages showed:
the CLIP model a library's vectors were made with, the face-detection thresholds,
Suggest's candidate words, the rename format and the ExifTool program. A library opened
on another machine, or with the file edited, was read with settings it was not made
with, and nothing said so. Now each library holds its own (tagpup.store.settings), and
this is their one owner:

- `of` gives a library's settings, every one declared in
  tagpup.core.validation.SETTINGS, a default for any the library does not hold. A
  library that holds none is stamped first, once: from what config.ini says, if the
  home has one, else with the defaults.
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

The layer below the entry points never reads config.ini: `of` and `read` are handed what
it says (`config_ini`, a callable returning {key: value}, or None), and only
tagpup.runtime hands it (tests/test_config_single_owner.py).
"""
import os
import pathlib
from dataclasses import dataclass
from typing import Dict

from tagpup.core import paths, validation
from tagpup.core.result import Result
from tagpup.services import sync as sync_service
from tagpup.store import db, journal
from tagpup.store import photos as store_photos
from tagpup.store import settings as store_settings

#: The value each setting has when a library does not say: what a new library is stamped with.
DEFAULTS = validation.setting_defaults()

#: The journal's name for each kind of change to the settings.
FROM_CONFIG = "stamp settings from config.ini"
WITH_DEFAULTS = "stamp settings with the defaults"
FROM_REPLACED = "stamp settings from the library it replaced"
CHANGE = "change settings"
ROOTS_FROM_FOLDERS = "stamp library roots from its folders"

#: The library's root folders and the folders it ignores (phase 8's sync).
ROOTS = "library.roots"
IGNORED = "library.ignored"

#: What a stamp writes: every setting but the roots, which come from the library's own
#: folders once it holds photos (stamp_roots).
STAMPED = {key: value for key, value in DEFAULTS.items() if key != ROOTS}

#: The stamps: a library's first settings, which cannot be undone -- undone, the library
#: held none, and the next read stamped it again, from config.ini if one was still there.
#: Its roots are stamped on their own, once it holds photos (stamp_roots).
STAMPS = frozenset({FROM_CONFIG, WITH_DEFAULTS, FROM_REPLACED, ROOTS_FROM_FOLDERS})
NOT_UNDONE = "A library's first settings cannot be undone; change them instead."

_TRUE = frozenset({"true", "yes", "on", "1"})


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
        """The face models' settings: tagpup.ml.faces.FaceModel's keyword arguments."""
        return {
            "min_face_size": int(_trim(self.values["faces.min_face_size"])),
            "confidence_threshold": _float(self.values["faces.confidence_threshold"]),
            "mtcnn_thresholds": [_float(x) for x in self.values["faces.mtcnn_thresholds"].split(",")],
        }

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
    return [_trim(line) for line in str(text).split(validation.FOLDER_SEPARATOR) if _trim(line)]


def default_roots(folders):
    """The root folders a library holding photos in `folders` is first given: for each
    first-level folder of a drive (or share) that holds any of them, the deepest folder
    all of those are under -- the topmost common ancestors that are not a drive. A photo
    directly on a drive's root is under none of them. In the spelling first given,
    sorted by key."""
    groups = {}
    for folder in folders:
        stored = paths.stored(folder)
        parts = pathlib.PurePath(stored).parts
        if len(parts) < 2:
            continue   # a drive's root is never a library folder
        groups.setdefault((paths.key(parts[0]), paths.key(os.path.join(parts[0], parts[1]))), []).append(stored)
    roots = {}
    for members in groups.values():
        common = paths.stored(os.path.commonpath(members))
        roots.setdefault(paths.key(common), common)
    return [roots[key] for key in sorted(roots)]


def roots_from_folders(library):
    """default_roots of the folders `library` holds photos in, as the settings value (one a
    line); "" for a library holding none. Reads only."""
    if not os.path.exists(library.path):
        return ""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        held = [folder for folder, _count in store_photos.folders_held(conn)]
    finally:
        conn.close()
    return validation.FOLDER_SEPARATOR.join(default_roots(held))


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


def stamp_roots(library):
    """Give a library that holds no root folders the ones its photos' folders give
    (roots_from_folders), once, as a journaled stamp -- with, in the same change, every
    folder under them holding photos and no indexed photo added to the ignored folders
    (excluded_under). Nothing for a library that holds them already, or holds no photo
    yet. A Result: `changed` the settings rows written; details["ignored_added"]."""
    result = Result(attempted=1)
    held = store_settings.read(library.path)
    if not held or ROOTS in held:
        return result
    roots = roots_from_folders(library)
    if not roots:
        return result
    ignored, added = _with_excluded(library, held, roots)
    edits = [journal.insert(store_settings.TABLE, {"key": ROOTS, "value": roots})]
    if added:
        edits.append(_setting_edit(held, IGNORED, ignored))
    result.details["ignored_added"] = added
    try:
        applied = journal.apply(library.path, ROOTS_FROM_FOLDERS, edits,
                                summary={"roots": len(_folders(roots)), "ignored_added": added})
    except journal.Refusal:
        return result   # stamped by another process meanwhile
    result.changed = applied.changed
    result.details["change"] = applied.change_id
    return result


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
    """What stamping gives, from `found` -- {key: value} config.ini says, or None when
    the home has none: (values, the keys taken from it, the keys it held a value the
    validator refuses for, left at their default)."""
    # The roots are stamped from the library's own folders, once it holds photos (stamp_roots).
    values, taken, refused = dict(STAMPED), [], []
    for key, value in (found or {}).items():
        if key not in values:
            continue   # data_dir, default_db: where the libraries are is not a setting
        text = _as_text(value)
        if validation.problem(validation.setting_kind(key), text):
            refused.append(key)
            continue
        values[key] = text
        taken.append(key)
    return values, sorted(taken), sorted(refused)


def _found(config_ini):
    return config_ini() if callable(config_ini) else config_ini


def read(library, config_ini=None):
    """The library's settings without writing anything: those it holds, or, for one
    never stamped -- or not made yet -- what stamping it would give (`stamped` False)."""
    held = store_settings.read_only(library.path) if os.path.exists(library.path) else {}
    if held:
        values = _filled(held)
        if ROOTS not in held:
            # What stamping them would give: the roots, and the folders they exclude.
            values[ROOTS] = roots_from_folders(library)
            values[IGNORED] = _with_excluded(library, held, values[ROOTS])[0]
        return LibrarySettings(values, stamped=True)
    values, _taken, _refused = _stamping(_found(config_ini))
    values[ROOTS] = roots_from_folders(library)
    if values[ROOTS]:
        values[IGNORED] = _with_excluded(library, {IGNORED: values[IGNORED]}, values[ROOTS])[0]
    return LibrarySettings(values, stamped=False)


def of(library, config_ini=None):
    """The library's settings, a default for any it does not hold. A library holding
    none is stamped first (`stamp`), from what `config_ini` says -- a callable returning
    config.ini's {key: value}, or None when the home has none."""
    held = store_settings.read(library.path)
    if not held:
        stamp(library, _found(config_ini))
        held = store_settings.read(library.path)
    if held and ROOTS not in held and stamp_roots(library).changed:
        held = store_settings.read(library.path)
    return LibrarySettings(_filled(held), stamped=bool(held))


def stamp(library, found=None, operation=None):
    """Write every setting to a library that holds none, as one journaled change: from
    `found` ({key: value}, what config.ini says) where it gives an allowed value, else
    the default. Named `operation` (one of STAMPS) when given -- FROM_REPLACED for a
    library made again in place of one deleted, with that one's settings -- else
    FROM_CONFIG when there was a config.ini, WITH_DEFAULTS when not. A library already
    stamped, by another process meanwhile say, is left as it is (changed 0)."""
    values, taken, refused = _stamping(found)
    if operation is None:
        operation = FROM_CONFIG if found is not None else WITH_DEFAULTS
    if operation not in STAMPS:
        raise ValueError("%r is not a stamp" % operation)
    result = Result(attempted=len(values), details={"operation": operation, "from_config": taken,
                                                    "refused": refused})
    if store_settings.read(library.path):
        return result
    edits = [journal.insert(store_settings.TABLE, {"key": key, "value": value}) for key, value in values.items()]
    try:
        applied = journal.apply(library.path, operation, edits, summary={
            "settings": len(values), "from_config": taken, "refused": refused})
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
    to a locked setting whose group (validation.SETTING_GROUPS: "clip", "faces",
    "exiftool") `acknowledged` does not name: the refusal lists each such group and what
    changing it means, and details["unacknowledged"] holds them. Every caller -- the
    dialog, the CLI, a tool -- names what it acknowledges, as the dialog asks the owner
    to tick each consequence. `changed` is the settings whose value changed;
    details["locked"] says whether any was a locked one, after which what the models
    made is from the old values.

    A change of the roots (library.roots) adds, in the same change, every folder under a
    new root that holds photos and no indexed photo to the ignored folders
    (excluded_under; owner, 2026-09-26): details["ignored_added"]. Without `apply`, a dry
    run: the same checks and details, nothing written (details["dry_run"])."""
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
    held = store_settings.read(library.path)
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
