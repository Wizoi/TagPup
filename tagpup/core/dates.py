"""When a photo was taken, as its metadata says, and the year of it.

It was read in seven places: three year parsers and four sort keys, with three lists of
fields between them. One also counted ModifyDate, the date the file was last edited,
which is not when the photo was taken. In both libraries on 2026-09-23 no photo had only
that, so dropping it changed nothing; taking XMP:CreateDate everywhere moved seven
photos whose only date it is from after the dated ones to their place among them.
tests/test_dates.py fails the build on a list of these fields anywhere else.

A clip whose file holds no date (its QuickTime dates zeros, no XMP date) is dated by the date in its file name
(`name_date`), at midnight: a real date always wins, a photo is never dated by its name, and a folder's date never
dates one (owner, 2026-11-13). The path and the kind ('photo' or 'video': photos.kind, as the index gave the row it by
images.kind_of) reach the rule as arguments of each reader (date_taken, year_taken, date_taken_sort_key), so the row
(store.photos.date_photos), the page's record and the folder view's order give one date; a clip is told from a photo by
its kind alone, never by the fields its metadata holds (#2952).
A clip's year is the year of that date alone (photo_year): one with no Date Taken has no year, so the owner finds it
among the undated (owner, 2026-11-13, #2941); a photo with none takes a year from its file or folder names.

And how TagPup spells a time it records (`recorded`): the journal's, the runs', the Activity page's. It was spelled by
hand in fourteen places; tests/test_rules_have_one_owner.py fails the build on a copy.
"""
import datetime
import re
import time
from pathlib import PurePath

#: The fields that say when a photo was taken, the most specific first.
DATE_TAKEN_FIELDS = (
    "EXIF:DateTimeOriginal", "DateTimeOriginal", "XMP:DateTimeOriginal",
    "EXIF:CreateDate", "CreateDate", "XMP:CreateDate",
)

#: The fields a time shift moves, where a photo holds them: each of DATE_TAKEN_FIELDS
#: under its group, as a write names it.
SHIFTED_FIELDS = tuple(field for field in DATE_TAKEN_FIELDS if ":" in field)

#: The fields that say when a clip was taken, read before a photo's, a clip being a record holding either:
#: CreationDate (Apple's Keys, local time with its zone) as given, else CreateDate, which QuickTime keeps in UTC, as
#: local time. A clip is read by its XMP dates first (CLIP_XMP_DATE_FIELDS), then these, and no photo field: its bare
#: CreateDate is the QuickTime copy, in UTC, and none of the 58 zero-date photo_index clips holds an EXIF or XMP date
#: (counted 2026-11-08). A date of zeros ("0000:00:00 00:00:00", a camera
#: that wrote none) is no date: a clip holding no other is dated by its file name (name_date). A camera's header is
#: never rewritten by the reader or the indexer, and stays UTC until a Date Taken save sets it (a save sets every mvhd,
#: tkhd and mdhd whatever it held: DECISIONS 2026-11-13); a header TagPup wrote holds the local date's digits as UTC,
#: which is why the reader prefers the XMP dates (DECISIONS 2026-11-02 item 8 stands for a camera's).
CLIP_DATE_FIELDS = ("QuickTime:CreationDate", "QuickTime:CreateDate")

#: A clip's XMP dates, read before its QuickTime ones: the Date Taken a save writes into its XMP
#: (tagpup.core.fields.date_taken_fields), and the one Adobe's tools wrapped it with (80 photo_index clips hold one,
#: 39 a DateTimeOriginal and 41 a CreateDate; counted 2026-11-08). XMP is the copy the index reads, the QuickTime copy
#: the fallback, as a clip's title and keywords are (DECISIONS 2026-11-08, A5 (D) REVISED).
CLIP_XMP_DATE_FIELDS = ("XMP:DateTimeOriginal", "XMP:CreateDate")

#: The kind of a clip (photos.kind, images.kind_of): what the dating rules tell a clip from a photo by.
VIDEO = "video"

#: What a QuickTime date with no date in it starts with.
_ZERO_DATE = "0000:00:00"

#: Years outside this range are not taken to be years: a four-digit run in a file name
#: is as often a counter (IMG_0001) as a date.
EARLIEST, LATEST = 1800, 2100

#: The first year a photo's Date Taken is a year to match a face by (its era); the last is the current year, read when
#: asked, so a photo taken this year is never undated. A year outside them is undated for that use (owner, 2026-10-22,
#: #1738: photo_index's named faces carried years from 1827 to 2079, a wrong clock or a counter read as a year). A rule in
#: code, not in the row: the row keeps its date, the library shows it as it is. Narrower than EARLIEST-LATEST, which only
#: tells a year in a name from a counter.
ERA_FIRST = 1950

#: What a photo whose year is not known is shown and grouped under. TagTuner's server,
#: the folder scan's records (scripts/metadata.py) and TagTuner's page each spelled it;
#: the page keeps a copy that tests/test_rules_have_one_owner.py holds to this (#74).
UNKNOWN_YEAR = "Unknown"

#: A date in a file name: YYYY-MM-DD, YYYY_MM_DD, YYYY.MM.DD or YYYYMMDD (one separator, the same both times, or
#: none), of a year 1900-2099, with no digit on either side (a longer run of digits is a counter, not a date).
_NAME_DATE = re.compile(r"(?<!\d)((?:19|20)\d\d)([-_.]?)(\d\d)\2(\d\d)(?!\d)")

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


def _clip_date(raw_metadata, field):
    """A clip's date field's value, stripped, or None for none or a date of zeros."""
    value = str(_value(raw_metadata, field) or "").strip()
    return value if value and not value.startswith(_ZERO_DATE) else None


def _local_of_utc(value):
    """A QuickTime CreateDate ("2021:07:04 18:30:00", UTC) as local time in the same spelling; None for one that is not
    a date and time."""
    found = _EXIF_TIME.match(value)
    if not found:
        return None
    try:
        when = datetime.datetime(*(int(part) for part in found.groups()[:6]), tzinfo=datetime.timezone.utc)
        return when.astimezone().strftime("%Y:%m:%d %H:%M:%S")
    except (ValueError, OverflowError, OSError):
        return None


def name_date(path):
    """The date in the file name of `path` as a Date Taken at midnight ("2019:07:04 00:00:00"): the first _NAME_DATE in
    the name whose month and day are a day of its year; None for a name holding none. The file name alone, never a
    folder's (owner, 2026-11-13: 11 photo_index clips dated only in their folder stay undated, for the owner to date by
    hand). What a clip whose file holds no date is dated by (_dates_given): 669 of photo_index's 680, each named
    YYYY-MM-DD (counted 2026-11-13)."""
    for found in _NAME_DATE.finditer(PurePath(path).name if path else ""):
        year, month, day = int(found.group(1)), int(found.group(3)), int(found.group(4))
        try:
            datetime.date(year, month, day)
        except ValueError:
            continue
        return "%04d:%02d:%02d 00:00:00" % (year, month, day)
    return None


def _clip_dates(raw_metadata):
    """Each date a clip's record holds: its XMP dates (CLIP_XMP_DATE_FIELDS), then its QuickTime ones
    (CLIP_DATE_FIELDS), a date of zeros none."""
    written = (_clip_date(raw_metadata, field) for field in CLIP_XMP_DATE_FIELDS)
    yield from (value for value in written if value)
    creation, create = (_clip_date(raw_metadata, field) for field in CLIP_DATE_FIELDS)
    if creation:
        yield creation
    if create:
        local = _local_of_utc(create)
        if local:
            yield local


def _dates_given(raw_metadata, path, kind):
    """Each date the record of the photo at `path`, of `kind` (photos.kind), holds, as read, the most specific first: the
    one rule date_taken and year_taken read by. A clip's own dates (_clip_dates), else, holding none, the date in its file
    name (name_date); a photo's DATE_TAKEN_FIELDS, never its name. `path` None reads the record's own dates alone."""
    if kind == VIDEO:
        given = list(_clip_dates(raw_metadata))
        named = None if given else name_date(path)
        yield from given or ([named] if named else [])
        return
    for field in DATE_TAKEN_FIELDS:
        value = _value(raw_metadata, field)
        if value:
            yield str(value).strip()


def date_taken(raw_metadata, path, kind):
    """The first Date Taken of the photo at `path`, of `kind` (photos.kind), that has a value (_dates_given), as ExifTool
    gave it ("2026:06:27 12:00:00"; a clip's UTC CreateDate as local time; a clip holding none, its file name's date at
    midnight), or None. `path` None is the file's own dates alone: what a conversion carries into its copy
    (tagpup.services.convert)."""
    return next(_dates_given(raw_metadata, path, kind), None)


def date_taken_sort_key(raw_metadata, path, kind, mtime=0.0):
    """Sorts photos by when they were taken (date_taken of the photo at `path`, of `kind`), and those without a date
    after them by file time.

    A string, as the folder view has always sorted: the date as ExifTool gives it, or
    "mtime_<file time>", which sorts after every date.
    """
    return taken_sort_key(date_taken(raw_metadata, path, kind), mtime)


def taken_sort_key(taken, mtime=0.0):
    """The folder view's order of a photo taken at `taken` (date_taken's spelling, None for none) whose file time is
    `mtime`: the date, or "mtime_<file time>", after every date. What a page sorts a folder's records by (their
    `order`, tagpup.web.tagpup_routes), given by the server so the page holds no rule of its own."""
    return taken if taken is not None else "mtime_%s" % (mtime,)


def year_taken(raw_metadata, path, kind):
    """The year of the first Date Taken of the photo at `path`, of `kind` (_dates_given), that starts with one, or
    None."""
    for value in _dates_given(raw_metadata, path, kind):
        match = _YEAR_AT_START.match(value)
        if match and EARLIEST <= int(match.group(1)) <= LATEST:
            return int(match.group(1))
    return None


def era_year(taken):
    """The year of `taken` (photos.taken, the photo's Date Taken: 'YYYY:MM:DD HH:MM:SS', some with a time zone after) as
    a year to match a face by: from ERA_FIRST to the current year, else None (undated); None for a photo with no Date
    Taken, never a year read from its file or folder name as a photo's photos.year falls back to (a clip dated by its file
    name's date, name_date, has that date for its Date Taken)."""
    match = _YEAR_AT_START.match(str(taken or "").strip())
    year = int(match.group(1)) if match else None
    return year if year is not None and ERA_FIRST <= year <= datetime.date.today().year else None


def year_in_name(path):
    """A year in the photo's file name, else in its folders from the nearest outward."""
    if not path:
        return None
    for part in reversed(PurePath(path).parts):
        for digits in _FOUR_DIGITS.findall(part):
            if EARLIEST <= int(digits) <= LATEST:
                return int(digits)
    return None


def photo_year(raw_metadata, path, kind):
    """The year the photo at `path`, of `kind` (photos.kind), was taken: the year of its Date Taken (year_taken), else, for
    a photo, a year in its file or folder names (year_in_name). A clip's is the year of its Date Taken alone, the date in its file name included
    (name_date, a whole date: `2019-07-04 jetty.mp4`), never a folder's year nor a year alone in its name (`2016-07
    slipway.mp4`): a clip with no Date Taken has no year, undated where the owner looks for the clips to date by hand
    (owner, 2026-11-13, #2941; photo_index's 11 such clips, 2 of them with a year in their folder only, 9 with a year
    alone in their name, counted read-only 2026-11-13)."""
    year = year_taken(raw_metadata, path, kind)
    if year is not None or kind == VIDEO:
        return year
    return year_in_name(path)


def record_year(meta):
    """The year a photo was taken, from a record of it: the record's "year", as the
    library records it (photos.year) and the reader gives it. Both builders of a record (services.photos.page_record,
    services.search._record) always set it, so a record without one is not read. What the suggester's time decay and
    clustering's years go by.

    A number or None, whatever the record's shape: the library gives its year as a
    number, the page's record of a photo as text ("2020", or "Unknown"), and Suggest
    compares the two -- given text, it failed on every photo of a folder."""
    year = meta["year"]
    if isinstance(year, int):
        return year
    text = str(year).strip() if year is not None else ""
    return int(text) if text.isdigit() else None


#: How every time TagPup records is spelled: local time, "YYYY-MM-DD HH:MM:SS".
RECORDED = "%Y-%m-%d %H:%M:%S"


def recorded(at=None):
    """`at`, seconds since the epoch (now by default), as TagPup records a time."""
    return time.strftime(RECORDED, time.localtime(at))


def recorded_seconds(text):
    """A time TagPup recorded, as seconds since the epoch; ValueError for a text that is not one, TypeError for None."""
    return time.mktime(time.strptime(text, RECORDED))
