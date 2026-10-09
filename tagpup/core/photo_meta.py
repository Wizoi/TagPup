"""What a photo's metadata says about the photo itself: its rating, its camera, its size and
where it was taken -- the columns of `photo_meta` (tagpup.store.derived), read from the raw
metadata one way.

The raw metadata is ExifTool's record of the fields the indexer asks for
(tagpup.core.fields.METADATA_FIELDS), each under its group's name ("XMP:Rating") and, since
tagpup.files.metadata.structured, again bare ("Rating"). What the libraries hold, counted on
photo_index's 68,466 rows on 2026-10-02:

* Rating: an integer, always (0 to 5; -1 is "rejected" in the XMP standard and none holds it).
  XMP:Rating in 58,341 rows and EXIF:Rating in 57,155; where both are there they agree. The bare
  name is the same value again, and in no row is it the only one.
* Make and Model: text, EXIF:Make in 65,012 rows and EXIF:Model in 63,020; XMP:Make and XMP:Model
  beside them in 228. One Make is the empty text.
* The lens: NONE. Not one of photo_index's 68,324 rows holds a lens field (counted 2026-10-09): the indexer asks ExifTool
  for no LensModel, LensMake or LensID (tagpup.core.fields.METADATA_FIELDS), so a library's rows name no lens until a photo
  is read with those fields asked for -- which changes what every read records and is the owner's to decide, as the size's
  is. `gear` reads them where a row has them: ExifTool's names EXIF:LensModel (the lens's own name, "EF24-70mm f/2.8L II
  USM"), XMP:LensModel and Composite:LensID, with EXIF:LensMake ("Canon", "Sigma") before a name that does not say it. Run
  without print conversion a LensID is a number, and a number is no lens.
* Make and Model, as a photo is called by them (`gear`'s camera): the model of a Canon says the make ("Canon EOS R6m2"),
  that of a Pixel or a Sony does not ("Pixel 8 Pro" of "Google", "DSC-X8" of "SONY"); 13,542 of the 62,877 photos
  holding both have a model that lacks its make. The camera is the model alone when it starts with the make's first word or holds a whole word of the make
  that names a maker, else the make and the model; it is the one name of a camera everywhere (`tagpup.core.fields.camera_of` is this).
* GPS: ExifTool answers numbers (it is run without print conversion). Composite:GPSLatitude and
  Composite:GPSLongitude are signed decimal degrees -- west and south are negative; EXIF:GPSLatitude
  is the magnitude alone, its sign in a Ref field the indexer does not ask for -- so only the
  composite pair is read. 1,571 rows have both; 64 have a longitude and no latitude; the text
  "" stands in for a coordinate in 46; and 83 latitudes and 107 longitudes are exactly 0, which a
  camera without a fix writes as the pair 0, 0 (a place in the Gulf of Guinea no family photograph
  was taken in): the pair is no place.
* Width and height: NOT in the metadata. ImageWidth, ImageHeight, ExifImageWidth and Orientation
  are not among the fields asked for, so no row holds one (0 of 68,466). The extraction reads
  them where a row has them -- a library read with more fields -- and `photo_meta`'s columns are
  empty until the indexer asks for them, which changes what every read records and is the
  owner's to decide (docs/ARCHITECTURE.md, "Phase 9").

Every value is checked and a value that is not what its column holds is left out, not guessed: a
rating that is not a whole number from -1 to 5, a coordinate that is not a finite number in range,
a size that is not a positive whole number, a make that is not text. One bad value does not cost the
photo the others, but a latitude without its longitude (or the reverse) is no coordinate.

Pure: this reads a dict and touches nothing.
"""
import collections
import json
import math
import re

Meta = collections.namedtuple("Meta", "rating make model width height latitude longitude")

#: What a photo whose metadata says none of it holds.
EMPTY = Meta(None, None, None, None, None, None, None)

#: A rating of a photo, the fields it is read from (the first that holds a valid one) and the
#: range the XMP standard gives it: -1 rejected, 0 unrated, 1 to 5 stars.
RATING_FIELDS = ("XMP:Rating", "EXIF:Rating", "Rating")
RATINGS = range(-1, 6)

#: The camera: the first of each that holds text.
MAKE_FIELDS = ("EXIF:Make", "XMP:Make", "Make")
MODEL_FIELDS = ("EXIF:Model", "XMP:Model", "Model")

#: The lens: the first of these that holds text, and the make of the lens (put before a name that does not say it).
LENS_FIELDS = ("EXIF:LensModel", "XMP:LensModel", "LensModel", "Composite:LensID", "LensID")
LENS_MAKE_FIELDS = ("EXIF:LensMake", "XMP:LensMake", "LensMake")

#: The camera and the lens a photo was taken with, as they are named and searched (tagpup.store.search_index): not columns of
#: `photo_meta`, which a facet would read; there is no facet, and the words of them are the derived table. None for either
#: the metadata does not say.
Gear = collections.namedtuple("Gear", "camera lens")
NO_GEAR = Gear(None, None)

#: The size, as (width field, height field) pairs, the first pair that holds two positive whole
#: numbers: the picture's own size, then the size the EXIF block declares. The pair is read
#: together, so a width of one never goes with a height of another.
SIZE_FIELDS = (("File:ImageWidth", "File:ImageHeight"),
               ("EXIF:ExifImageWidth", "EXIF:ExifImageHeight"),
               ("ExifImageWidth", "ExifImageHeight"),
               ("ImageWidth", "ImageHeight"))

#: The Orientation of the picture, as it is stored. 5 to 8 are the quarter turns: the picture is
#: stored on its side, and shown with its width and height the other way round.
ORIENTATION_FIELDS = ("EXIF:Orientation", "Orientation")
TURNED = range(5, 9)

#: Signed decimal degrees.
LATITUDE_FIELD, LONGITUDE_FIELD = "Composite:GPSLatitude", "Composite:GPSLongitude"


def _number(value):
    """`value` as a finite number, or None: a number, or the text of one. Never a bool, which is
    an int in Python and a yes in a JSON file."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (list, tuple)):
        value = value[0] if len(value) == 1 else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = float(text)
        except ValueError:
            return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    return None


def _whole(value):
    """`value` as a whole number, or None: 5, 5.0 and "5" are, 5.5 is not."""
    number = _number(value)
    if number is None or number != int(number):
        return None
    return int(number)


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


def _lens(raw):
    """The lens a photo was taken with, or None: its name, with the lens maker's before it when the name lacks it."""
    name = _first(raw, LENS_FIELDS, _text)
    if name is None:
        return None
    maker = _first(raw, LENS_MAKE_FIELDS, _text)
    return name if not maker or maker.lower() in name.lower() else "%s %s" % (maker, name)


def _rating(value):
    found = _whole(value)
    return found if found in RATINGS else None


def _size(raw):
    """(width, height) as the picture is shown, or (None, None)."""
    for width_field, height_field in SIZE_FIELDS:
        width, height = _whole(raw.get(width_field)), _whole(raw.get(height_field))
        if width and height and width > 0 and height > 0:
            turn = _first(raw, ORIENTATION_FIELDS, _whole)
            return (height, width) if turn in TURNED else (width, height)
    return None, None


def _place(raw):
    """(latitude, longitude) of a photo, or (None, None): both numbers, in range, and not the pair
    0, 0 a camera without a fix writes."""
    latitude, longitude = _number(raw.get(LATITUDE_FIELD)), _number(raw.get(LONGITUDE_FIELD))
    if latitude is None or longitude is None:
        return None, None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180) or (latitude == 0 and longitude == 0):
        return None, None
    return float(latitude), float(longitude)


def extract(raw):
    """The Meta of a photo's raw metadata (the dict a row holds). EMPTY for anything that is not a
    dict: a row never read holds {}, and a damaged one may hold anything."""
    if not isinstance(raw, dict):
        return EMPTY
    width, height = _size(raw)
    latitude, longitude = _place(raw)
    return Meta(_first(raw, RATING_FIELDS, _rating), _first(raw, MAKE_FIELDS, _text),
                _first(raw, MODEL_FIELDS, _text), width, height, latitude, longitude)


def gear(raw):
    """The Gear of a photo's raw metadata (the dict a row holds). NO_GEAR for anything that is not a dict."""
    if not isinstance(raw, dict):
        return NO_GEAR
    return Gear(camera_name(_first(raw, MAKE_FIELDS, _text), _first(raw, MODEL_FIELDS, _text)), _lens(raw))


def load(text):
    """The dict a row's raw_metadata column holds: the JSON text, or None. {} for a row with none, or whose text is
    not JSON (extract and gear say nothing of it)."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {}


def from_json(text):
    """The Meta of a row's raw_metadata as the column holds it: the JSON text, or None. EMPTY
    for a row with none, or whose text is not JSON."""
    return extract(load(text))
