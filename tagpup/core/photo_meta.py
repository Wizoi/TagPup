"""The camera and the lens a photo was taken with, named as a person calls them: read from the raw metadata one way.

The raw metadata is ExifTool's record of the fields the indexer asks for
(tagpup.core.fields.METADATA_FIELDS), each under its group's name ("XMP:Make") and, since
tagpup.files.metadata.structured, again bare ("Make"). What the libraries hold, counted on
photo_index's 68,466 rows on 2026-10-02:

* Make and Model: text, EXIF:Make in 65,012 rows and EXIF:Model in 63,020; XMP:Make and XMP:Model
  beside them in 228. One Make is the empty text.
* The lens: NONE in the libraries as they stand. Not one of photo_index's 68,324 rows holds a lens field (counted 2026-10-09):
  the indexer asked ExifTool for no LensModel, LensMake or LensID before then. It asks now (tagpup.core.fields.METADATA_FIELDS),
  so a photo read from now on has them; the photos read before are read again when they change (sync), not by a command. `gear` reads them where a row has them: ExifTool's names EXIF:LensModel (the lens's own name, "EF24-70mm f/2.8L II
  USM"), XMP:LensModel and Composite:LensID, with EXIF:LensMake ("Canon", "Sigma") before a name that does not say it. Run
  without print conversion a LensID is a number (Canon, Tamron: 137, 61182), the lens's eight bytes as hex pairs (Nikon) or
  readable text (Google: "Pixel 8 Pro back camera 6.9mm f/1.68", on 13 of 1,499 kr-track photos); the first two are no lens.
* Make and Model, as a photo is called by them (`gear`'s camera): the model of a Canon says the make ("Canon EOS R6m2"),
  that of a Pixel or a Sony does not ("Pixel 8 Pro" of "Google", "DSC-X8" of "SONY"); 13,542 of the 62,877 photos
  holding both have a model that lacks its make. The camera is the model alone when it starts with the make's first word or holds a whole word of the make
  that names a maker, else the make and the model; it is the one name of a camera everywhere (`tagpup.core.fields.camera_of` is this).

(This module also read a photo's rating, size and place, for a table of them, `photo_meta`, that nothing read; migration 30
dropped the table and this module's code for it. The rating, GPS and size are in the raw metadata still.)

Pure: this reads a dict and touches nothing.
"""
import collections
import re

#: The camera: the first of each that holds text.
MAKE_FIELDS = ("EXIF:Make", "XMP:Make", "Make")
MODEL_FIELDS = ("EXIF:Model", "XMP:Model", "Model")

#: The lens: the first of these that holds text, and the make of the lens (put before a name that does not say it).
LENS_FIELDS = ("EXIF:LensModel", "XMP:LensModel", "LensModel", "Composite:LensID", "LensID")
#: Of those, the ones that name a lens only sometimes: a LensID is text for Google, a number for Canon and Tamron, and for
#: Nikon the lens's eight bytes as hex pairs ("A1 40 18 37 2C 34 A4 06"), which is no name.
LENS_ID_FIELDS = ("Composite:LensID", "LensID")
LENS_MAKE_FIELDS = ("EXIF:LensMake", "XMP:LensMake", "LensMake")

#: The camera and the lens a photo was taken with, as they are named and searched (tagpup.store.search_index): None for either
#: the metadata does not say.
Gear = collections.namedtuple("Gear", "camera lens")
NO_GEAR = Gear(None, None)


def _first(raw, fields, valid):
    """The first value among `fields` of `raw` that `valid` makes something of."""
    for field in fields:
        if field in raw:
            found = valid(raw[field])
            if found is not None:
                return found
    return None


def _text(value):
    """`value` as the text of a make or a model, or None: trimmed, without the NULs a camera pads
    it with, and not empty. A number is not a make."""
    if isinstance(value, (list, tuple)):
        value = value[0] if len(value) == 1 else None
    if not isinstance(value, str):
        return None
    return value.replace("\x00", " ").strip() or None


#: What a make says of no maker: "Corp.", "Company", "Imaging", "Digital Camera". A model holding one is not holding the make.
GENERIC = frozenset(("company", "corp", "corporation", "co", "ltd", "inc", "digital", "camera", "cameras", "electronics",
                     "imaging", "technologies", "technology", "international", "group"))


def _words(text):
    return [word.lower() for word in re.findall(r"[^\W_]+", text)]


def camera_name(make, model):
    """The camera as a person calls it, the one name of it (Image Details, the search, Shift Date Taken's list of cameras):
    the model alone when it STARTS with the make's first word, without case ("Canon EOS R6m2" of "Canon"; "HTCONE" of "HTC";
    "LG-TP260" of "LG Electronics"), or holds a whole word of the make that names a maker -- not under three letters, not a
    GENERIC one -- ("KODAK CX4310 DIGITAL CAMERA" of "EASTMAN KODAK COMPANY"); else the make and the model ("Google Pixel
    8 Pro", "OLYMPUS IMAGING CORP. FE45,X40"); else whichever there is, else None."""
    if not make or not model:
        return make or model or None
    made = _words(make)
    if made and model.lower().startswith(made[0]):
        return model
    named = {word for word in made if len(word) >= 3 and word not in GENERIC}
    return model if named & set(_words(model)) else "%s %s" % (make, model)


def _only_hex(text):
    """Is `text` hex pairs or digits and blanks alone -- a lens's id bytes, not its name?"""
    return all(part.isalnum() and all(c in "0123456789abcdefABCDEF" for c in part) for part in text.split())


def _lens(raw):
    """The lens a photo was taken with, or None: its name, with the lens maker's before it when the name lacks it."""
    name = None
    for field in LENS_FIELDS:
        if field in raw:
            name = _text(raw[field])
            if name is not None and field in LENS_ID_FIELDS and _only_hex(name):
                name = None
            if name is not None:
                break
    if name is None:
        return None
    maker = _first(raw, LENS_MAKE_FIELDS, _text)
    return name if not maker or maker.lower() in name.lower() else "%s %s" % (maker, name)


def gear(raw):
    """The Gear of a photo's raw metadata (the dict a row holds). NO_GEAR for anything that is not a dict."""
    if not isinstance(raw, dict):
        return NO_GEAR
    return Gear(camera_name(_first(raw, MAKE_FIELDS, _text), _first(raw, MODEL_FIELDS, _text)), _lens(raw))
