"""When a photo was taken, as its metadata says, and the year of it.

It was read in seven places: three year parsers and four sort keys, with three lists of
fields between them. One also counted ModifyDate, the date the file was last edited,
which is not when the photo was taken. In both libraries on 2026-09-23 no photo had only
that, so dropping it changed nothing; taking XMP:CreateDate everywhere moved seven
photos whose only date it is from after the dated ones to their place among them.
tests/test_dates.py fails the build on a list of these fields anywhere else.
"""
import datetime
import re
from pathlib import PurePath

#: The fields that say when a photo was taken, the most specific first.
DATE_TAKEN_FIELDS = (
    "EXIF:DateTimeOriginal", "DateTimeOriginal", "XMP:DateTimeOriginal",
    "EXIF:CreateDate", "CreateDate", "XMP:CreateDate",
)

#: The fields a time shift moves, where a photo holds them: each of DATE_TAKEN_FIELDS
#: under its group, as a write names it.
SHIFTED_FIELDS = tuple(field for field in DATE_TAKEN_FIELDS if ":" in field)

#: Years outside this range are not taken to be years: a four-digit run in a file name
#: is as often a counter (IMG_0001) as a date.
EARLIEST, LATEST = 1800, 2100

#: What a photo whose year is not known is shown and grouped under. TagTuner's server,
#: the folder scan's records (scripts/metadata.py) and TagTuner's page each spelled it;
#: the page keeps a copy that tests/test_rules_have_one_owner.py holds to this (#74).
UNKNOWN_YEAR = "Unknown"

_YEAR_AT_START = re.compile(r"^(\d{4})")
_FOUR_DIGITS = re.compile(r"\d{4}")

#: An EXIF date and time, and whatever follows the seconds: a fraction, a time zone.
_EXIF_TIME = re.compile(r"^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})(.*)$")


def shifted(value, minutes):
    """An EXIF date and time ("2024:07:04 10:00:00") moved by `minutes`, whatever follows
    the seconds kept as it was; None for a value that is not one, which a shift leaves
    alone as ExifTool's own shift did."""
    found = _EXIF_TIME.match(str(value).strip()) if value is not None else None
    if not found:
        return None
    try:
        when = datetime.datetime(*(int(part) for part in found.groups()[:6]))
        moved = when + datetime.timedelta(minutes=minutes)
    except (ValueError, OverflowError):
        return None
    return moved.strftime("%Y:%m:%d %H:%M:%S") + found.group(7)


def shown_year(year):
    """A photo's year as the pages show it: `year` as it is, or UNKNOWN_YEAR for none."""
    return year if year else UNKNOWN_YEAR


def _value(raw_metadata, field):
    value = (raw_metadata or {}).get(field)
    if isinstance(value, list):
        value = value[0] if value else None
    return value


def date_taken(raw_metadata):
    """The first Date Taken field that has a value, as ExifTool gave it
    ("2026:06:27 12:00:00"), or None."""
    for field in DATE_TAKEN_FIELDS:
        value = _value(raw_metadata, field)
        if value:
            return str(value).strip()
    return None


def date_taken_sort_key(raw_metadata, mtime=0.0):
    """Sorts photos by when they were taken, and those without a date after them by file time.

    A string, as the folder view has always sorted: the date as ExifTool gives it, or
    "mtime_<file time>", which sorts after every date.
    """
    taken = date_taken(raw_metadata)
    return taken if taken is not None else "mtime_%s" % (mtime,)


def year_taken(raw_metadata):
    """The year of the first Date Taken field that starts with one, or None."""
    for field in DATE_TAKEN_FIELDS:
        match = _YEAR_AT_START.match(str(_value(raw_metadata, field) or "").strip())
        if match and EARLIEST <= int(match.group(1)) <= LATEST:
            return int(match.group(1))
    return None


def year_in_name(path):
    """A year in the photo's file name, else in its folders from the nearest outward."""
    if not path:
        return None
    for part in reversed(PurePath(path).parts):
        for digits in _FOUR_DIGITS.findall(part):
            if EARLIEST <= int(digits) <= LATEST:
                return int(digits)
    return None


def photo_year(raw_metadata, path=None):
    """The year a photo was taken: its Date Taken if it has one, else a year in its name."""
    return year_taken(raw_metadata) or year_in_name(path)


def record_year(meta):
    """The year a photo was taken, from a record of it: the record's "year", as the
    library records it (photos.year) and the reader gives it; else, for a record without
    one or a raw_metadata dict itself, photo_year. What the suggester's time decay and
    clustering's years go by."""
    if "year" in meta:
        return meta["year"]
    return photo_year(meta.get("raw_metadata", meta), meta.get("path"))
