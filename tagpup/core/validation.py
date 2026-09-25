"""What may be set: every kind of input TagPup is handed, and the rules each is held to.

A tag, a person's name, a caption, a library's name, a Smart Rename grouping, a folder,
a time shift and each setting a library holds are each a kind here, with its rules.
The checks were written twice -- once in Python where a value is written, once in the
page that asks first -- and the library-name rule was a pattern the page repeated
without the reserved names. Now the rules are data: patterns, forbidden text, lengths,
ranges, choices and the message each gives. Every service checks what it is given
against them before it writes and refuses with the rule's message (tagpup.core.result),
so the pages, the CLI and the MCP tools are held to the same rules. The pages check
early from the same data, which /api/rules publishes (tagpup.web.rules_routes) and
web/common/validate.js applies (ruleProblem); tests/validation_cases.json runs through
both, so the two cannot disagree.

Asked where a value is set -- typed, created, merged into -- never where one is read:
a photo holding a bad tag from another program must still open, and lose it.

A rule is a dict naming its check ("rule") and the check's parameters. The checks are
the few below, each written in Python here and in JavaScript in validate.js; a new
kind is new data, a new check is code in both. Rules run in order and the first that
fails gives the message; "{value}" in a message is the value, trimmed. A "pattern" is
matched against the whole value, a "forbid_pattern" anywhere in it: Python's "$" also
matches before a final line break and JavaScript's does not, so no pattern that
must agree relies on it.
"""
import hashlib
import json
import re

from tagpup.core import library, renaming, vocabulary

#: A backslash. Spelled by its code so that no escape in this file can be mangled in
#: transit (CLAUDE.md); every pattern below is a regular expression's source text,
#: which Python's re and JavaScript's RegExp read alike.
BS = chr(92)


def _char(code):
    """A character in a pattern, by its code: \\x1f, \\u2028."""
    return BS + ("x%02x" % code if code < 0x100 else "u%04x" % code)


#: A tab, a line break, any other control character, and the invisible byte-order mark:
#: none can be seen in a tag, or typed back to find it.
CONTROL = "[%s-%s%s-%s%s%s%s]" % (_char(0x00), _char(0x1f), _char(0x7f), _char(0x9f),
                                  _char(0x2028), _char(0x2029), _char(0xfeff))

#: What XML cannot hold, and so neither can the XMP a caption is written to: a control
#: character other than a tab or a line break. Captions written by other programs hold
#: tabs and line breaks, and keep them.
NOT_IN_XML = "[%s-%s%s%s%s-%s%s%s]" % (_char(0x00), _char(0x08), _char(0x0b), _char(0x0c),
                                       _char(0x0e), _char(0x1f), _char(0xfffe), _char(0xffff))

#: Nothing between two separators of a tag, or before the first, or after the last.
EMPTY_LEVEL = "(?:^|/)%ss*(?:/|$)" % BS

#: Other programs' separators between the levels of a tag (tagpup.core.vocabulary).
OTHER_SEPARATORS = ("|", BS)

#: A full path on this machine: a drive and a separator (D:/Photos, D:\\Photos), a share
#: (\\\\server\\photos), or a POSIX root; then anything.
FULL_PATH = "(?:[A-Za-z]:[/%s]|[/%s]{2}[^/%s]|/)[%ss%sS]*" % (BS * 2, BS * 2, BS * 2, BS, BS)

#: What a library's name may hold: it is a file name and the first part of its URL.
LIBRARY_NAME = "[A-Za-z0-9_-]+"

#: The longest caption IPTC keeps: Caption-Abstract holds 2,000 bytes.
CAPTION_BYTES = 2000

#: What is blank: trimmed from around a value, and all a blank value holds. Python's
#: str.strip() and JavaScript's String.trim() strip different characters -- Python the
#: separators U+001C to U+001F and U+0085, JavaScript the byte-order mark U+FEFF -- so a
#: grouping of a lone U+FEFF was refused by the page and accepted by the server, and one
#: of U+0085 the reverse (docs/findings.md). Both languages trim exactly these, which
#: /api/rules publishes: every character either one calls whitespace.
BLANK = "".join(chr(code) for code in (
    [0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680]
    + list(range(0x2000, 0x200b)) + [0x2028, 0x2029, 0x202f, 0x205f, 0x3000, 0xfeff]))


def trim(text):
    """`text` without the BLANK characters around it: as the rules trim a value, in both
    languages (validate.js's trimBlank)."""
    return text.strip(BLANK)


def _text_rules(what, levels):
    """A tag's rules (`levels`), or a name's: one level of a tag."""
    rules = [{"rule": "forbid_pattern", "pattern": CONTROL,
              "message": "%s cannot contain a tab, a line break or another control character." % what}]
    for mark in OTHER_SEPARATORS:
        rules.append({"rule": "forbid_text", "text": mark,
                      "message": '%s cannot contain "%s": other programs read it as a break between levels.%s'
                                 % (what, mark, ' Use "/" instead.' if levels else "")})
    rules.append({"rule": "required", "message": "%s cannot be empty." % what})
    if levels:
        rules.append({"rule": "forbid_pattern", "pattern": EMPTY_LEVEL,
                      "message": '%s cannot have an empty level, as in "A//B" or "A/".' % what})
    else:
        rules.append({"rule": "forbid_text", "text": vocabulary.SEPARATOR,
                      "message": 'A name cannot contain "/": it separates the levels of a tag.'})
    return rules


def _required(what):
    return {"rule": "required", "message": "%s cannot be empty." % what}


def _no_controls(what):
    return {"rule": "forbid_pattern", "pattern": CONTROL,
            "message": "%s cannot contain a tab, a line break or another control character." % what}


def _between(what, low, high):
    return {"rule": "range", "min": low, "max": high,
            "message": "%s must be between %s and %s." % (what, low, high)}


#: Each kind of input, by the name the services and the pages ask for it by.
KINDS = {
    # Set on a photo, created in the tree, merged into, moved to.
    "tag": {"rules": _text_rules("A tag", levels=True)},
    # A person's name, and one level of a tag: a tree node's own name.
    "name": {"rules": [
        # Before the rest, as the server always asked: a person called one of TagTuner's
        # lists opened that list instead of themselves (docs/findings.md, #68).
        {"rule": "reserved", "names": sorted(vocabulary.NOT_A_PERSON),
         "message": "'{value}' is the name of one of TagTuner's lists, not a person's."},
    ] + _text_rules("A name", levels=False)},
    # A photo's caption, which is its title too: written to XMP, IPTC and EXIF.
    "caption": {"rules": [
        {"rule": "forbid_pattern", "pattern": NOT_IN_XML,
         "message": "A caption cannot contain a control character other than a tab or a line break."},
        {"rule": "max_bytes", "max": CAPTION_BYTES,
         "message": "A caption cannot be longer than 2,000 bytes, the most IPTC keeps."},
    ]},
    # A new library's name, without ".db": its file name and its URL's first part.
    "library name": {"rules": [
        {"rule": "required", "message": "A library needs a name."},
        {"rule": "pattern", "pattern": LIBRARY_NAME,
         "message": "A library's name can hold only letters, numbers, underscores and hyphens."},
        # A library is reached at /<its name>/, so one called any of these could be
        # created and never opened (docs/findings.md, #73). Whatever the case.
        {"rule": "reserved", "names": sorted(library.ROUTES),
         "message": "'{value}' is the name of one of the app's own pages; choose another"},
    ]},
    # What Smart Rename names photos by, before their number.
    "grouping": {"rules": [
        {"rule": "required", "message": "Enter a grouping name to rename by"},
        # A grouping holding " - " put a separator before the photo's number, and
        # editing a caption later took its second half for the number (#29). A dash
        # without spaces, as in "2019-06", separates nothing.
        {"rule": "forbid_text", "text": renaming.SEPARATOR,
         "message": ('A grouping cannot contain " - ": it separates the parts of a photo\'s name. '
                     'A dash without spaces, as in 2019-06, is fine.')},
    ]},
    # A folder of photos: indexed, scanned, renamed in.
    "folder": {"rules": [
        {"rule": "required", "message": "Choose a folder."},
        _no_controls("A folder's path"),
        {"rule": "pattern", "pattern": FULL_PATH,
         "message": "A folder is named by its full path, as in D:/Photos."},
    ]},
    # Shift Date Taken: minutes, later or earlier.
    "time shift": {"rules": [
        {"rule": "integer", "message": "A time shift is a whole number of minutes."},
    ]},
}

#: The groups the settings dialog shows them in, in order, and what changing one does.
#: A group whose change has consequences is locked: shown, not editable, opened only
#: through Change..., which lists each consequence to be acknowledged before it saves
#: (docs/ARCHITECTURE.md, phase 7.6). Every setting of a group carries the group's lock
#: and consequences in its own declaration below, so the validator and the dialog read
#: one declaration.
SETTING_GROUPS = {
    "clip": {
        "title": "The CLIP model",
        "consequences": [
            "Every photo's vector was made with the model it has now, so Suggest finds nothing "
            "until every folder is indexed again.",
            "Indexing a folder again makes a vector for each of its photos with the new model; "
            "on a large library that takes hours.",
        ],
    },
    "faces": {
        "title": "Face detection",
        "consequences": [
            "Only photos indexed after the change are detected with it; the faces already found stay as they are.",
            "Indexing a folder again applies it to that folder.",
        ],
    },
    "exiftool": {
        "title": "ExifTool",
        "consequences": [
            "Every read and every write of a photo file goes through this program: tags, captions, "
            "dates, renames and rotations.",
            "A program that is not ExifTool, or that is missing, makes every one of them fail.",
        ],
    },
    "suggest": {"title": "Suggest", "consequences": []},
    "renaming": {"title": "Smart Rename", "consequences": []},
}


def _setting(group, label, value_type, default, info, rules):
    """A setting's declaration: its group, its label, its type, the value a new library
    is stamped with, what it changes and when (the info button's text), whether it is
    locked and what changing it does (its group's), and the rules its value is held to."""
    consequences = list(SETTING_GROUPS[group]["consequences"])
    return {"group": group, "label": label, "type": value_type, "default": default, "info": info,
            "locked": bool(consequences), "consequences": consequences, "rules": rules}


#: Every setting a library holds (tagpup.services.settings), each as a kind of its own --
#: "setting faces.min_face_size" -- declared once: what the settings dialog is made from,
#: what a new library is stamped with, and what a value is held to. The ranges are what
#: the program can run with, not what is sensible. The keys are config.ini's section and
#: key, from which a library in use is stamped once.
SETTINGS = {
    "model.name": _setting(
        "clip", "Model", "text", "ViT-H-14",
        "The CLIP architecture Suggest compares photos and words with (an open_clip model name). "
        "Every photo's vector is made with it when its folder is indexed.",
        [_required("The CLIP model"), _no_controls("The CLIP model")]),
    "model.pretrained": _setting(
        "clip", "Weights", "text", "laion2b_s32b_b79k",
        "The trained weights the model is loaded with (an open_clip pretrained tag for the model above).",
        [_required("The CLIP weights"), _no_controls("The CLIP weights")]),
    "model.preserve_full_frame": _setting(
        "clip", "Keep the full frame", "boolean", "true",
        "Show the model the whole photo, padded to the widest aspect ratio below, rather than "
        "the square crop from its middle.",
        [{"rule": "boolean", "message": "Keep the full frame is true or false."}]),
    "model.max_aspect_ratio": _setting(
        "clip", "Widest aspect ratio", "number", "1.4",
        "With the full frame kept, how far a photo is padded towards square: 1 is square.",
        [{"rule": "number", "message": "The widest aspect ratio is a number."},
         # 1 is square; below it is the same ratio turned.
         _between("The widest aspect ratio", 1, 4)]),
    "model.force_image_size": _setting(
        "clip", "Image size", "integer", "512",
        "The size in pixels a photo is scaled to before the model sees it. Empty is the model's own size.",
        [{"rule": "optional"},
         {"rule": "integer", "message": "The image size is a whole number of pixels."},
         _between("The image size", 32, 2048)]),
    "faces.min_face_size": _setting(
        "faces", "Smallest face", "integer", "20",
        "The smallest face, in pixels, that indexing looks for. Smaller finds more faces in crowds, "
        "and more that are not faces.",
        [{"rule": "integer", "message": "The smallest face is a whole number of pixels."},
         _between("The smallest face", 1, 2000)]),
    "faces.confidence_threshold": _setting(
        "faces", "Confidence", "number", "0.85",
        "How sure the detector must be that it found a face before the face is kept, from 0 to 1.",
        [{"rule": "number", "message": "The confidence is a number."},
         _between("The confidence", 0, 1)]),
    "faces.mtcnn_thresholds": _setting(
        "faces", "Stage thresholds", "list", "0.6, 0.7, 0.7",
        "The face detector's (MTCNN's) threshold at each of its three stages, from 0 to 1.",
        [{"rule": "list", "separator": ",", "count": 3,
          "message": "Face detection takes three thresholds, one for each stage.",
          "each": [{"rule": "number", "message": "Each threshold is a number."},
                   _between("Each threshold", 0, 1)]}]),
    "paths.exiftool": _setting(
        "exiftool", "ExifTool program", "path", "",
        "The ExifTool program every photo file is read and written with. Empty is the one its "
        "installer puts in your profile, else the one on PATH.",
        [{"rule": "optional"}, _no_controls("ExifTool's path")]),
    "candidates.tags": _setting(
        "suggest", "Candidate words", "list",
        "Landscape, Portrait, Nature, Urban, Sunset, Sunrise, Night, Ocean, Mountain, Forest, Animal, "
        "Cat, Dog, Food, Indoor, Outdoor, Vehicle, Flower, Architecture, Party, Wedding, Beach, Sports, Concert",
        "Words Suggest asks CLIP about each photo, beside every tag in the tree that is not a person, "
        "separated by commas. Accepted, a word is written as a tag. From the next Suggest.",
        [{"rule": "list", "separator": ",", "skip_empty": True, "each": KINDS["tag"]["rules"]}]),
    "renaming.format": _setting(
        "renaming", "Rename format", "text", "{grouping} - {index} - {caption}",
        "The name Smart Rename gives each photo: {grouping} is the name you type, {index} the photo's "
        "number, {caption} its caption. From the next rename.",
        [_required("The rename format"), _no_controls("The rename format"),
         # Without the number every photo is given the same name.
         {"rule": "must_contain", "text": "{index}",
          "message": "The rename format must hold {index}, or every photo is given the same name."}]),
}

SETTING = "setting "

for _key, _setting_declared in SETTINGS.items():
    KINDS[SETTING + _key] = _setting_declared


def setting_kind(key):
    """The kind a setting's value is checked as: "faces.min_face_size" -> its kind."""
    return SETTING + key


def setting_defaults():
    """{key: the value a new library is stamped with}, for every setting."""
    return {key: declared["default"] for key, declared in SETTINGS.items()}


# ---- The checks ------------------------------------------------------------------------
# Each takes the rule and the value, and says whether the value fails it. validate.js
# has each again, under the same name.

def _text(value):
    """A value as text: None is empty; true and false as JavaScript spells them."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _is_blank(value):
    return not isinstance(value, (list, tuple)) and not trim(_text(value))


_INTEGER = re.compile(r"[+-]?[0-9]+")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_BOOLEANS = frozenset({"true", "false", "yes", "no", "on", "off", "1", "0"})


def _number(value):
    """The value as a number, or None when it is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if value == value and abs(value) != float("inf") else None
    text = trim(_text(value))
    return float(text) if _NUMBER.fullmatch(text) else None


def _fails_integer(rule, value):
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return _number(value) is None or float(value) != int(value)
    return not _INTEGER.fullmatch(trim(_text(value)))


def _fails_range(rule, value):
    number = _number(value)
    return number is None or not rule["min"] <= number <= rule["max"]


def _items(rule, value):
    if isinstance(value, (list, tuple)):
        items = [trim(_text(item)) for item in value]
    else:
        items = [trim(item) for item in _text(value).split(rule["separator"])]
    return [item for item in items if item] if rule.get("skip_empty") else items


CHECKS = {
    "required": lambda rule, value: _is_blank(value),
    "forbid_pattern": lambda rule, value: re.search(rule["pattern"], _text(value)) is not None,
    "forbid_text": lambda rule, value: rule["text"] in _text(value),
    "pattern": lambda rule, value: re.fullmatch(rule["pattern"], _text(value)) is None,
    "must_contain": lambda rule, value: rule["text"] not in _text(value),
    "reserved": lambda rule, value: trim(_text(value)).lower() in rule["names"],
    "max_bytes": lambda rule, value: len(_text(value).encode("utf-8", "surrogatepass")) > rule["max"],
    "integer": _fails_integer,
    "number": lambda rule, value: _number(value) is None,
    "boolean": lambda rule, value: not isinstance(value, bool) and trim(_text(value)).lower() not in _BOOLEANS,
    "range": _fails_range,
}


def _check(rules, value):
    for rule in rules:
        name = rule["rule"]
        if name == "optional":
            if _is_blank(value):
                return None
            continue
        if name == "list":
            items = _items(rule, value)
            if "count" in rule and len(items) != rule["count"]:
                return rule["message"]
            for item in items:
                found = _check(rule["each"], item)
                if found:
                    return found
            continue
        if CHECKS[name](rule, value):
            return rule["message"].replace("{value}", trim(_text(value)))
    return None


def problem(kind, value):
    """Why `value` cannot be set as a `kind`, or None if it can. An unknown kind is a
    KeyError: a misspelt kind would otherwise allow everything."""
    return _check(KINDS[kind]["rules"], value)


def first_problem(kind, values):
    """The first reason any of `values` cannot be set as a `kind`, or None."""
    for value in values:
        found = problem(kind, value)
        if found:
            return found
    return None


def published():
    """The rules as data, for /api/rules: {"version", "blank", "kinds": {kind: {"rules",
    ...}}}; `blank` is what the checks trim (BLANK). The version is the rules' own
    digest, so it changes whenever a rule does and a page holding an older copy can tell."""
    kinds = {kind: dict(declared) for kind, declared in KINDS.items()}
    text = json.dumps({"blank": BLANK, "kinds": kinds}, sort_keys=True, ensure_ascii=True)
    return {"version": hashlib.sha256(text.encode("ascii")).hexdigest()[:12], "blank": BLANK, "kinds": kinds}
