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
  when a value is not one the validator allows.

The layer below the entry points never reads config.ini: `of` and `read` are handed what
it says (`config_ini`, a callable returning {key: value}, or None), and only
tagpup.runtime hands it (tests/test_config_single_owner.py).
"""
from dataclasses import dataclass
from typing import Dict

from tagpup.core import validation
from tagpup.core.result import Result
from tagpup.store import journal
from tagpup.store import settings as store_settings

#: The value each setting has when a library does not say: what a new library is stamped with.
DEFAULTS = validation.setting_defaults()

#: The journal's name for each kind of change to the settings.
FROM_CONFIG = "stamp settings from config.ini"
WITH_DEFAULTS = "stamp settings with the defaults"
CHANGE = "change settings"

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
    def exiftool(self):
        """The ExifTool program the library names, or "" for the one the machine has
        (tagpup.config.exiftool_path finds it)."""
        return _trim(self.values["paths.exiftool"])


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
    values, taken, refused = dict(DEFAULTS), [], []
    for key, value in (found or {}).items():
        if key not in DEFAULTS:
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
    never stamped, what stamping it would give (`stamped` False)."""
    held = store_settings.read_only(library.path)
    if held:
        return LibrarySettings(_filled(held), stamped=True)
    values, _taken, _refused = _stamping(_found(config_ini))
    return LibrarySettings(values, stamped=False)


def of(library, config_ini=None):
    """The library's settings, a default for any it does not hold. A library holding
    none is stamped first (`stamp`), from what `config_ini` says -- a callable returning
    config.ini's {key: value}, or None when the home has none."""
    held = store_settings.read(library.path)
    if not held:
        stamp(library, _found(config_ini))
        held = store_settings.read(library.path)
    return LibrarySettings(_filled(held), stamped=bool(held))


def stamp(library, found=None):
    """Write every setting to a library that holds none, as one journaled change: from
    `found` ({key: value}, what config.ini says) where it gives an allowed value, else
    the default. Named FROM_CONFIG when there was a config.ini, WITH_DEFAULTS when not.
    A library already stamped, by another process meanwhile say, is left as it is
    (changed 0)."""
    values, taken, refused = _stamping(found)
    operation = FROM_CONFIG if found is not None else WITH_DEFAULTS
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


def change(library, values):
    """Change the settings in `values` ({key: value}) as one journaled change, so it is in
    the library's history and can be undone. Refused, with nothing written, for a key
    that is no setting, a value the validator refuses (its message), a library never
    stamped, or a setting changed by someone else since it was read. `changed` is the
    settings whose value changed; details["locked"] says whether any was a locked one,
    after which what the models made is from the old values."""
    result = Result(attempted=len(values or {}))
    if not isinstance(values, dict) or not values:
        result.refuse("Say which settings to change.")
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
    if not edits:
        return result
    try:
        applied = journal.apply(library.path, CHANGE, edits, summary={"settings": sorted(changed)})
    except journal.Refusal as e:
        result.refuse("Nothing was written: %s" % e)
        return result
    result.changed = applied.changed
    result.details["change"] = applied.change_id
    return result


def described(settings):
    """The settings as the dialog is made from them: each group, in order, with its title,
    whether it is locked and what changing it does, and each of its settings' declaration
    (label, type, default, info, locked, consequences) with the library's value."""
    groups = []
    for name, group in validation.SETTING_GROUPS.items():
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
