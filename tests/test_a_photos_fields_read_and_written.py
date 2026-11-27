"""A photo's or a clip's fields read and written as the oracle reads them (scenario F12; docs/plans/own-metadata-t1.md,
sections 2 and 3): the one suite every later stage of the own metadata library is judged by.

Every item of the conformance corpus (tests/corpus.py, ITEMS: a layout of the libraries a read or a write turns on, each
naming its live count) is read through field_values.Fields alone, and each operation of it is written on a fresh copy,
on both sides a write takes -- journaled (given a `saving`: the own patch where it fits) and outside (none: ExifTool's
rewrite) -- read back, written again and undone; then a file cut short, not a photo, one bad file in a batch, a file
held open, one marked read-only and one on a share that does not answer. Every expectation is ExifTool's independent
read of the copy (field_values.oracle, outside the counted window), a sentence or a path the item names, or TagPup's
own rule (fields.as_patched, the segments a write's fields are held in: fields.EDITED_AS); no answer of ExifTool's is
typed here.

The backend is observed, never chosen: ANSWERS says who answers each (format, call) today, and every call of Fields
here counts the commands ExifTool is sent while it runs (ExifToolSession.execute, a replayed one too; a start's own
-ver is no command): OWN, none; EXIFTOOL, at least one. A stage that moves a format flips its line in the merge that
moves it: the suite then fails where a caller was forgotten (commands seen) or anything falls back to ExifTool (none
may: Q1), and S4 takes that format's refusals as its cases.

Deterministic under the tests' replay (tests/exiftool_replay.py): each item's bytes are its recipe's, a copy's name is
its item's, the oracle's whole read names no folder and no file's dates (field_values.WHOLE_READ), and the mint's id is
seeded by the item (exiftool_replay.seeded_mint). A second replayed run misses only the commands never kept: one that
failed (a file not a photo, the file held open) and the recipes that read a file by name (made once a generation).

How it fails: two runs at once each copy into a home of their own (FilesCase), sharing only the corpus and the kept
answers, whose makers take turns; a file held open is let go, and one made read-only made writable again, at the
test's end however it ends; the share-away stand-in is a patch of this test (setUp's), gone with it.

Breaks that fail these, each tried on a copy of the code (2026-11-27): segment_edits.new_header writing one byte more
into a segment the write's fields are not held in fails S2.1 (the segments outside the write); in_place.patch_jpeg
answering () with no byte written fails S2.1 (the path, and the targets as the oracle reads them).
"""
import io
import os
import stat
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baked_files  # noqa: E402
import corpus  # noqa: E402
import exiftool_fields  # noqa: E402
import exiftool_replay  # noqa: E402
import test_file_journal  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core import fields, paths  # noqa: E402
from tagpup.files import containers, exiftool_session, field_values, images, in_place, lock_owners, metadata  # noqa: E402
from tagpup.files import shares  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import metadata_check  # noqa: E402

EXIFTOOL_PATH = test_file_journal.EXIFTOOL
FFMPEG = config.locate_ffmpeg()

#: Who answers a call of each format today: TagPup's own code, or ExifTool. A stage that moves a format flips its line.
OWN, EXIFTOOL = "own", "ExifTool"
ANSWERS = {
    ("jpeg", "read"): EXIFTOOL, ("jpeg", "patch"): OWN, ("jpeg", "rewrite"): EXIFTOOL,
    ("png", "read"): EXIFTOOL, ("png", "write"): EXIFTOOL,
    ("tiff", "read"): EXIFTOOL, ("tiff", "write"): EXIFTOOL,
    ("clip", "read"): EXIFTOOL, ("clip", "write"): OWN,
}
FORMATS = {".jpg": "jpeg", ".png": "png", ".tif": "tiff"}

#: The suite's own operations beside metadata_check.OPERATIONS (decision 3 of the plan).
TITLE = "Gulls over the breakwater"
TAKEN = "2021-03-04T05:06:07"
BAKES = ("a bake of 0", "a bake of 1", "a bake of 40")
PHOTO = tuple(name for name, _planner in metadata_check.OPERATIONS) + ("title set", "Date Taken set", "a mint") + BAKES
CLIP = ("keyword added", "last keyword taken off", "person added", "title set", "Date Taken set", "preserved name set")
P, R = "patch", "rewrite"

#: {item: (its operations, the path a journaled write of each takes today, {operation: the other path})}; an
#: outside write is ExifTool's rewrite (a JPEG's, a PNG's, a TIFF's) or refused (a clip's, S2.1's one case of it).
WRITES = {
    "camera": (PHOTO, R, dict.fromkeys(("Date Taken shifted", "turned right", "mirrored"), P)),
    "tagged": (PHOTO, P, dict.fromkeys(BAKES + ("a mint",), R)),
    "tagged_ii": (("Date Taken shifted", "turned right", "keyword added"), P, {}),
    "iptc_alone": (("keyword added", "last keyword taken off", "preserved name set"), P, {}),
    "iptc_utf8": (("caption set", "caption cleared", "keyword added"), P, {}),
    "app13_without_iptc": (("caption set", "keyword added"), P, {}),
    "two_app13": (("last keyword taken off", "caption cleared"), R, {}),
    "two_exif": (("Date Taken shifted", "turned right"), R, {}),
    "two_xmp": (("keyword added",), R, {}),
    "extended_xmp": (("keyword added", "a bake of 40"), R, {}),
    "big_xmp": (("keyword added",), P, {}),
    "mpf": (("keyword added", "Date Taken shifted"), P, {}),
    "trailer": (("keyword added", "Date Taken shifted"), P, {}),
    "maker_note": (("Date Taken shifted", "turned right"), P, {}),
    "no_taken": (("Date Taken set",), R, {}),
    "ifd0_xmp": (("keyword added", "Date Taken shifted", "turned right", "last keyword taken off"), R, {}),
    "nul_xmp": (("keyword added",), R, {}),
    "no_wrapper": (("keyword added",), R, {}),
    "other_appn": (("keyword added", "Date Taken shifted"), P, {}),
    "thumb_turn": (("turned right", "mirrored"), P, {}),
    "png": (PHOTO, R, {}),
    "png_xmp_only": (("keyword added", "turned right"), R, {}),
    "tiff": (PHOTO, R, {}),
    "mp4": (CLIP, P, {}),
    "mov": (CLIP, P, {}),
    "mp4_bare": (("keyword added", "person added"), P, {}),
    "premiere_mp4": (("keyword added", "Date Taken set"), P, {}),
    **{name: (("Date Taken set",), P, {}) for name in corpus.ITEMS if name.startswith("clip_headers_")},
}
#: {(item, operation): why its planner plans nothing}: the item holds what the operation would write. S2 writes every
#: other (item, operation) of WRITES, so a planner answering nothing for one more fails it.
HOLDS_IT = "the item keeps a name already (Smart Rename's planner: metadata_check._preserved)"
SKIPPED = {
    ("camera", "last keyword taken off"): "the item holds no keyword",
    ("camera", "caption cleared"): "the item holds no caption",
    **{(name, "preserved name set"): HOLDS_IT for name in ("tagged", "png", "tiff")},
}
#: Items read as ExifTool reads them, written by no case of S2 (S3's).
READ_ONLY = ("cut_short", "no_picture")
#: The fields S1 and S3 read: every field the index reads by its group.
GROUPED = [field for field in fields.METADATA_FIELDS if ":" in field]
#: What a file that is not a photo is refused with by name (#46), and the clip patch's open that failed.
NOT_A_PHOTO = "Not a photo or a video ExifTool can read"
NOT_OPENED = "it could not be read or opened for its write"
#: Items whose write does not land today, and why: the journal's read back finds the file holding less than it was given.
NOT_LANDING = {"nul_xmp": "ExifTool writes no XMP into a segment of NUL bytes, saying Invalid XMP (#2349), until J3a"}
#: {item: tags (by name) ExifTool's rewrite lays out again, by its own rule}: where the picture lies (a TIFF's strips,
#: an MPO's second frame: as KNOWN's ThumbnailOffset). An EXIF IFD a rewrite makes is its own too (landed).
ALSO = {"tiff": ("StripOffsets",), "mpf": ("MPImageStart",)}
#: What a bake's write makes beside its fields: MWG's regions flattened (the oracle reads no structure) and, past 64 KB,
#: the extended packet's GUID.
BAKE_ALSO = ("XMP-mwg-rs", "XMP-xmpNote")


def format_of(path):
    return "clip" if images.is_clip(path) else FORMATS[os.path.splitext(path)[1].lower()]


def oracle_value(row, field):
    """The values the oracle's read `row` holds of `field`, as the journal keeps a value (fields.field_values): its first
    key whose family 0 group and name are the field's (fields.read_key); a clip's header date, every box's text, each
    once, in file order (the own walk's: in_place.headers_held)."""
    wanted = fields.read_key(field)
    found = [fields.field_values(value) for key, value in row.items()
             if "%s:%s" % (key.split(":")[0], key.split(":")[-1]) == wanted]
    if field in fields.CLIP_HEADER_DATES:
        return [text for each in found for text in each]
    return found[0] if found else []


def other_tags(row, written, also=()):
    """{tag: its values} of the oracle's read `row` but the tags of the `written` fields (by family 0 group and name),
    what no write may change by design (metadata_check.SET_ASIDE, KNOWN), what ExifTool says of the file, and `also`
    (family 1 groups or tags a write of these fields makes by TagPup's own rule)."""
    named = {fields.read_key(field) for field in written} | {
        "XMP:" + name.split(":")[-1] for field in written for kind, name in fields.EDITED_AS.get(field, ()) if kind == "xmp"}
    kept = {}
    for tag, value in row.items():
        parts = tag.split(":")
        if (parts[0] in metadata_check.SET_ASIDE or tag in metadata_check.SET_ASIDE or tag in metadata_check.KNOWN
                or parts[0] == field_values.READER_SAYS or "%s:%s" % (parts[0], parts[-1]) in named
                or (len(parts) > 2 and parts[1] in also) or parts[-1] in also):
            continue
        kept[tag] = sorted(fields.field_values(value))
    return kept


def lands(field, read, wanted):
    """Does the oracle's `read` of `field` hold `wanted`? A clip's header date, in every box the oracle reads (a write
    sets every box, the oracle reads one of each kind a track); any other field as a read gives it (fields.same_read)."""
    if field in fields.CLIP_HEADER_DATES:
        return set(read) == set(fields.field_values(wanted))
    return fields.same_read(field, read, wanted)


def changed(was, now):
    """{tag: (its values in `was`, in `now`)} of every tag the two hold otherwise."""
    return {tag: (was.get(tag), now.get(tag)) for tag in sorted(set(was) | set(now)) if was.get(tag) != now.get(tag)}


def said(row):
    """What ExifTool says of a file as it reads it: its warnings and errors (field_values.READER_SAYS), by their text."""
    return {(tag.rsplit(":", 1)[-1], str(value)) for tag, value in row.items()
            if tag.split(":")[0] == field_values.READER_SAYS and tag.rsplit(":", 1)[-1] in metadata_check.SAID}


def bytes_of(path):
    with open(path, "rb") as handle:
        return handle.read()


def segments_outside(data, kinds):
    """The bytes of each metadata segment of the JPEG `data` whose kind is not in `kinds`, in order."""
    layout = containers.read(io.BytesIO(data))
    return [data[each.offset:each.end] for each in layout.segments
            if each.kind in metadata_check.METADATA and each.kind not in kinds]


@unittest.skipIf(EXIFTOOL_PATH is None or FFMPEG.problem, "ExifTool or ffmpeg not installed")
class AFilesFieldsReadAndWritten(test_file_journal.FilesCase):
    def setUp(self):
        super().setUp()
        self.fields = exiftool_fields.opened(self, EXIFTOOL_PATH)
        self.oracle = field_values.oracle(EXIFTOOL_PATH)
        self.oracle.__enter__()
        self.addCleanup(self.oracle.__exit__, None, None, None)
        self.seen, self.away, self.made = None, set(), 0
        real_execute, real_bounded = ExifToolSession.execute, shares.bounded

        def execute(session, *params, **kwargs):
            if self.away and any(paths.key(str(each)) in self.away for each in params):
                raise exiftool_session.ExifToolTimeout("ExifTool did not answer in its deadline (the test's share away)")
            if self.seen is not None and not session._starting:
                self.seen.append(params)
            return real_execute(session, *params, **kwargs)

        def bounded(location, call, seconds, *args, **kwargs):
            if self.away and paths.key(location) in self.away:
                return "away", None
            return real_bounded(location, call, seconds, *args, **kwargs)
        for patched in (mock.patch.object(ExifToolSession, "execute", execute),
                        mock.patch.object(shares, "bounded", bounded)):
            patched.start()
            self.addCleanup(patched.stop)

    # ---- the tools of every case ----

    def copy(self, name):
        """A fresh copy of the item `name`, in a folder of its own."""
        self.made += 1
        return corpus.of(name, os.path.join(self.folder, "%d" % self.made), EXIFTOOL_PATH, FFMPEG.ffmpeg)

    def counted(self, work):
        """(what `work()` answered, the commands ExifTool was sent while it ran)."""
        self.seen = []
        try:
            return work(), len(self.seen)
        finally:
            self.seen = None

    def assert_answered(self, path, call, commands):
        who = ANSWERS[(format_of(path), call)]
        self.assertEqual(who == OWN, commands == 0, "%s %s is %s's today, and was sent %d ExifTool commands"
                         % (format_of(path), call, who, commands))

    def read(self, copies, wanted):
        found, commands = self.counted(lambda: self.fields.read(copies, wanted))
        self.assert_answered(copies[0], "read", commands)
        return found

    def whole(self, copies):
        return self.oracle.whole(copies)

    def planned(self, name, op, path, held):
        """The values `op` writes into the item `name` at `path`, holding `held`; None when it holds them already."""
        for each, planner in metadata_check.OPERATIONS:
            if each == op:
                plan = planner(self.library)(path, held)
                after = plan if isinstance(plan, dict) else plan.after
                break
        else:
            after = {"title set": lambda: fields.title_fields(TITLE),
                     "Date Taken set": lambda: fields.held_dates(
                         fields.date_taken_fields(TAKEN, clip=images.is_clip(path)), held),
                     "a mint": lambda: {"XMP-xmpMM:DocumentID": exiftool_replay.seeded_mint("conformance:" + name)()},
                     }.get(op, lambda: baked_files.values_of(baked_files.record(faces=int(op.rsplit(" ", 1)[1]))))()
        return None if not after or fields.reads_same(held, after) else after

    # ---- S1: every item read ----

    def test_every_item_reads_as_the_oracle_reads_it_alone_and_in_a_batch(self):
        names = [name for name in corpus.ITEMS if name in WRITES or name in READ_ONLY]
        copies = [self.copy(name) for name in names]
        rows = self.whole(copies)
        alone = {}
        for name, path in zip(names, copies):
            wanted = list(GROUPED)
            if images.is_clip(path):
                wanted += fields.CLIP_HEADER_DATES
            held = self.read([path], wanted)[paths.key(path)]
            alone[path] = held
            row = rows[paths.key(path)]
            with self.subTest("S1.1", item=name):
                self.assertIsInstance(held, dict)
                headers = [field for field in wanted if field in fields.CLIP_HEADER_DATES]
                self.assertEqual({field: oracle_value(row, field) for field in wanted if field not in headers},
                                 {field: held[field] for field in wanted if field not in headers})
                # ExifTool answers one box of each kind; the own walk, every box (in_place.headers_held).
                self.assertEqual({field: True for field in headers},
                                 {field: bool(held[field]) and set(oracle_value(row, field)) <= set(held[field])
                                  for field in headers}, {field: held[field] for field in headers})
            with self.subTest("S1.2", item=name):
                (record,), commands = self.counted(lambda: self.fields.whole([path], bakes=True))
                self.assert_answered(path, "read", commands)
                self.assertEqual((os.stat(path).st_mtime, os.stat(path).st_size, None),
                                 (record["mtime"], record["size"], record.get("read_error")))
                self.assertEqual(paths.key(row["SourceFile"]), paths.key(record["raw_metadata"]["SourceFile"]))
                self.assertEqual({field: held[field] for field in wanted if field in fields.METADATA_FIELDS},
                                 {field: fields.field_values(record["raw_metadata"].get(fields.read_key(field)))
                                  for field in wanted if field in fields.METADATA_FIELDS})
                if name == "extended_xmp":
                    # The bake read is the bake written (its values as a write of it sets them; bake.parse would
                    # refuse it: baked_files.area puts 31 of its 40 faces past the picture's edge).
                    self.assertEqual(baked_files.values_of(baked_files.record(faces=40)),
                                     baked_files.values_of(record["baked"]))
        for kind in sorted({format_of(path) for path in copies}):
            batch = [path for path in copies if format_of(path) == kind]
            with self.subTest("S1.3", format=kind):
                wanted = sorted({field for path in batch for field in alone[path]})
                found = self.read(batch, wanted)
                self.assertEqual([paths.key(path) for path in batch], list(found))
                self.assertEqual([{field: alone[path].get(field, found[paths.key(path)][field]) for field in wanted}
                                  for path in batch], [found[paths.key(path)] for path in batch])

    # ---- S2: each operation written, read back, written again and undone ----

    def test_each_operation_lands_as_the_oracle_reads_it_twice_alike_and_undone(self):
        skipped = set()
        for name, (ops, path_today, other) in WRITES.items():
            original = self.copy(name)
            held = self.fields.read([original], metadata_check.READ)[paths.key(original)]
            planned = {op: self.planned(name, op, original, held) for op in ops}
            skipped.update((name, op) for op, after in planned.items() if after is None)
            targets = list(dict.fromkeys(field for after in planned.values() if after for field in after))
            before = self.fields.read([original], [field for field in targets if not fields.is_struct(field)])
            before = dict(before[paths.key(original)])
            if any(fields.is_struct(field) for field in targets):
                before.update(self.fields.read([original], [fields.REGION_INFO])[paths.key(original)])
            was = self.whole([original])[paths.key(original)]
            sides = (("journaled", True),) if images.is_clip(original) else (("outside", False), ("journaled", True))
            for op, after in planned.items():
                if after is None:
                    continue
                for side, own in sides:
                    with self.subTest("S2", item=name, op=op, side=side):
                        also = ALSO.get(name, ()) + (BAKE_ALSO if op in BAKES else ())
                        self.written(original, was, before, after, other.get(op, path_today) if own else R, own, also)
        self.assertEqual(sorted(SKIPPED), sorted(skipped), "the (item, operation) pairs planned to write nothing")
        clip = self.copy("mp4")
        with self.subTest("S2.1", item="mp4", side="outside"):
            data = bytes_of(clip)
            self.assertEqual(("failed", images.CLIP_NOT_EDITED), metadata_check.written(
                self.fields, clip, fields.title_fields(TITLE), False)[::2])
            self.assertEqual(data, bytes_of(clip))

    def landed(self, copy, was, before, after, stale, also, how, start=None, rewritten=False):
        """The oracle's read of `copy` once a write of `after` left `stale` as `before` held them: each target as
        fields.as_patched says, every other tag as `was` (the original's), and, written by the own patch (`how`),
        nothing said of it that was not said of the file it patched (`start`, the oracle's read of it: `was` when
        None). ExifTool's rewrite is the oracle's own writer: what its validation says of an IFD it made is its own,
        and so are the tags it gives an IFD it made, in this write or the one before (`rewritten`)."""
        row = self.whole([copy])[paths.key(copy)]
        wanted = fields.as_patched(before, after, stale)
        plain = [field for field in after if not fields.is_struct(field)]
        if how == R or rewritten:
            # An IFD the file held none of, made by ExifTool's rewrite, holds the tags ExifTool requires of it.
            held = {tag.split(":")[1] for tag in was if tag.startswith("EXIF:")}
            also = tuple(also) + tuple({tag.split(":")[1] for tag in row if tag.startswith("EXIF:")} - held)
        self.assertEqual({}, {field: (oracle_value(row, field), wanted[field]) for field in plain
                              if not lands(field, oracle_value(row, field), wanted[field])}, "the targets")
        self.assertEqual({}, changed(other_tags(was, after, also), other_tags(row, after, also)), "a tag outside it")
        if how == P:
            self.assertEqual(set(), said(row) - said(start or was), "ExifTool says of the patched copy what it did "
                                                                     "not say of the file it patched")
        return row, wanted

    def written(self, original, was, before, after, path_today, own, also):
        name = os.path.splitext(os.path.basename(original))[0]
        copy = self.copy(name)
        data = bytes_of(copy)
        (how, stale, error), commands = self.counted(lambda: metadata_check.written(self.fields, copy, after, own))
        self.assertEqual((path_today, None), (how, error), "the path the write took")
        call = how if format_of(copy) == "jpeg" else "write"
        self.assert_answered(copy, call, commands)
        if name in NOT_LANDING:
            wanted = fields.as_patched(before, after, stale)
            self.assertFalse(fields.reads_same(self.fields.read_back(copy, list(after)), wanted), NOT_LANDING[name])
            return
        # S2.1: the targets as the oracle reads them, every other tag as before, and nothing else of the file changed.
        row, wanted = self.landed(copy, was, before, after, stale, also, how)
        regions = fields.REGION_INFO in after
        plain = [field for field in after if not fields.is_struct(field)]
        now = bytes_of(copy)
        if format_of(copy) == "jpeg":
            self.assertEqual(metadata_check.outside(data), metadata_check.outside(now), "the picture or a segment "
                             "outside EXIF, XMP and IPTC changed")
            if how == P:
                kinds = {kind for field in after if field not in stale for kind, _name in fields.EDITED_AS[field]}
                self.assertEqual(segments_outside(data, kinds), segments_outside(now, kinds),
                                 "a patch changed a segment none of its fields is held in")
        back, commands = self.counted(lambda: self.fields.read_back(copy, list(after)))
        self.assert_answered(copy, "read", commands)
        self.assertEqual({}, {field: (back.get(field), oracle_value(row, field)) for field in plain
                              if not lands(field, oracle_value(row, field), back.get(field))}, "read back")
        if regions:
            self.assertTrue(fields.reads_same(back, after), "a bake's regions read back as written")
        # S2.2: the same write again changes no byte, and a patch with nothing to write saves no record.
        again, _stale, error = metadata_check.written(self.fields, copy, after, own)
        self.assertEqual((now, None), (bytes_of(copy), error), "a second write of the same values")
        if how == P:
            self.assertEqual("unchanged", again)
        # S2.3: its undo, a write of what the file held: its targets as fields.as_patched says (a copy an XMP-only patch
        # of it leaves holds what the first write left), every other tag as the original's; an XMP-only patch of
        # properties the packet held, undone, gives the original bytes back.
        undo = {field: before.get(field, []) for field in after}
        how_back, stale_back, error = metadata_check.written(self.fields, copy, undo, own)
        self.assertIsNone(error)
        self.landed(copy, was, wanted, undo, stale_back, also, how_back, row, rewritten=how == R)
        xmp = [field for field in after if str(field).startswith("XMP")]
        if how == P == how_back and set(after) - set(stale) <= set(xmp) and all(before.get(field) for field in xmp):
            self.assertEqual(data, bytes_of(copy), "an XMP-only patch undone gives the original bytes back")

    # ---- S3: a file cut short, not a photo, in a batch with one, held open, read-only, or on a share away ----

    def refused(self, path, words, sides=(False, True)):
        """A write of a keyword into `path` on each side fails saying `words`, the file as it was and nothing beside it."""
        data, folder = bytes_of(path), os.path.dirname(path)
        for own in sides:
            how, _stale, error = metadata_check.written(self.fields, path, {"XMP:Subject": ["Trips/Dune"]}, own)
            self.assertEqual(("failed", True), (how, words in (error or "")), error)
            self.assertEqual((data, [os.path.basename(path)]), (bytes_of(path), os.listdir(folder)))

    def test_a_file_cut_short_not_a_photo_or_one_bad_in_a_batch(self):
        cut, none, zeros, text = (self.copy(name) for name in ("cut_short", "no_picture", "zeros", "text_named_jpg"))
        with self.subTest("S3.1 cut short"):
            row = self.whole([cut])[paths.key(cut)]
            held = self.read([cut], metadata_check.READ)[paths.key(cut)]
            after = self.planned("cut_short", "keyword added", cut, held)
            before = self.read([cut], list(after))[paths.key(cut)]
            for own in (False, True):
                copy = self.copy("cut_short")
                self.assertEqual((R, None), metadata_check.written(self.fields, copy, after, own)[::2])
                self.landed(copy, row, before, after, (), (), R)
                # DECISIONS 2026-10-15: a picture that stops short takes a write of its fields, its bytes as they were.
                self.assertEqual(metadata_check.outside(bytes_of(cut)), metadata_check.outside(bytes_of(copy)))
            # ExifTool refuses a JPEG with no scan in its own words before field_values' NO_PICTURE is reached.
            self.refused(none, "")
        with self.subTest("S3.2 not a photo"):
            for path in (zeros, text):
                found = self.fields.read([path], GROUPED)[paths.key(path)]
                self.assertIsInstance(found, field_values.Unreadable)
                (record,) = self.fields.whole([path])
                self.assertEqual((os.stat(path).st_mtime, os.stat(path).st_size, True, None),
                                 (record["mtime"], record["size"], bool(record.get("read_error")), record.get("unread")))
                self.refused(path, "")
            self.assertIn(NOT_A_PHOTO, str(self.fields.read([text], GROUPED)[paths.key(text)]))
        with self.subTest("S3.3 one bad file in a batch"):
            tagged, camera = self.copy("tagged"), self.copy("camera")
            alone = {path: self.read([path], GROUPED)[paths.key(path)] for path in (tagged, camera)}
            found = self.fields.read([tagged, zeros, camera], GROUPED)
            self.assertEqual([alone[tagged], True, alone[camera]],
                             [found[paths.key(tagged)], isinstance(found[paths.key(zeros)], field_values.Unreadable),
                              found[paths.key(camera)]])
            records = self.fields.whole([tagged, zeros, camera])
            self.assertEqual([(os.stat(path).st_mtime, os.stat(path).st_size, bad) for path, bad in
                              ((tagged, False), (zeros, True), (camera, False))],
                             [(each["mtime"], each["size"], "read_error" in each) for each in records])
            _found, commands = self.counted(lambda: self.fields.read([tagged, camera], GROUPED))
            self.assertEqual(0 if ANSWERS[("jpeg", "read")] == OWN else 1, commands, "a batch of good files")

    def test_a_file_held_open_or_read_only_is_refused_as_it_was(self):
        tagged, clip = self.copy("tagged"), self.copy("mp4")
        with self.subTest("S3.4 held open"):
            for path in (tagged, clip):
                data, sides = bytes_of(path), (True,) if images.is_clip(path) else (False, True)
                with lock_owners.opened(path, lock_owners.NO_SHARING):
                    (record,) = self.fields.whole([path])
                    found = self.fields.read([path], GROUPED)[paths.key(path)]
                    written = [metadata_check.written(self.fields, path, {"XMP:Subject": ["Trips/Dune"]}, own)
                               for own in sides]
                self.assertEqual((metadata.HELD, None, True), (record.get("unread"), record["mtime"],
                                                                isinstance(found, field_values.Unreadable)))
                self.assertEqual(["failed"] * len(sides), [how for how, _stale, _error in written])
                if images.is_clip(path):
                    self.assertIn(in_place.OPEN_ELSEWHERE, written[0][2])
                self.assertEqual((data, [os.path.basename(path)]), (bytes_of(path), os.listdir(os.path.dirname(path))))
        with self.subTest("S3.5 read-only"):
            for path in (tagged, clip):
                os.chmod(path, stat.S_IREAD)
                self.addCleanup(os.chmod, path, stat.S_IREAD | stat.S_IWRITE)
                wanted = GROUPED + (list(fields.CLIP_HEADER_DATES) if images.is_clip(path) else [])
                row = self.whole([path])[paths.key(path)]
                held = self.read([path], wanted)[paths.key(path)]
                self.assertEqual({}, {field: held[field] for field in wanted
                                      if not lands(field, oracle_value(row, field), held[field])})
            self.refused(tagged, field_values.NOT_MOVED.split(" (")[0])
            self.refused(clip, NOT_OPENED, sides=(True,))

    def test_a_share_that_does_not_answer_leaves_that_call_unread_and_the_next_answers(self):
        """S3.6: the stand-in's share does not answer for X, ExifTool's command naming it nor a look at it."""
        tagged, x, camera, clip = (self.copy(name) for name in ("tagged", "trailer", "camera", "mp4"))
        alone = {path: self.read([path], GROUPED)[paths.key(path)] for path in (tagged, camera)}
        data = {path: bytes_of(path) for path in (x, clip)}
        self.away = {paths.key(x), paths.key(clip)}
        found = self.fields.read([tagged, x, camera], GROUPED)
        self.assertEqual([True, True], [isinstance(found[paths.key(path)], field_values.NotAnswered) for path in (x, camera)])
        # The file before X answers as alone, or is of X's command (ExifTool reads a batch in one).
        self.assertTrue(isinstance(found[paths.key(tagged)], field_values.NotAnswered)
                        or found[paths.key(tagged)] == alone[tagged], found[paths.key(tagged)])
        self.assertEqual(alone[camera], self.fields.read([camera], GROUPED)[paths.key(camera)], "the next call answers")
        with self.fields.together():
            self.fields.read([tagged, x, camera], GROUPED)
            self.assertIsInstance(self.fields.read([camera], GROUPED)[paths.key(camera)], field_values.NotAnswered)
        self.assertEqual(alone[camera], self.fields.read([camera], GROUPED)[paths.key(camera)], "after the block")
        # A write of X on each side a write takes (a clip's is journaled alone: S2.1's outside case is Refused).
        for path, own in ((x, False), (x, True), (clip, True)):
            how, _stale, error = metadata_check.written(self.fields, path, {"XMP:Subject": ["Trips/Dune"]}, own)
            self.assertEqual(("failed", True), (how, "did not answer" in (error or "")), (own, error))
            self.assertEqual((data[path], [os.path.basename(path)]), (bytes_of(path), os.listdir(os.path.dirname(path))),
                             "a write of X leaves it as it was, nothing beside it")

    # ---- S4: a format moved to TagPup's own writer starts no ExifTool for a write it refuses ----

    def test_a_refused_clip_write_starts_no_exiftool(self):
        """S4: a Fields that has started no ExifTool yet (none read with it) is given a clip's write: nothing starts one."""
        clip = self.copy("mov")
        with mock.patch.object(exiftool_session, "ExifToolSession", side_effect=AssertionError("ExifTool started")):
            for values, own in ((fields.title_fields(TITLE), False), ({fields.ORIENTATION: ["6"]}, True)):
                with self.subTest("S4", own=own):
                    (how, _stale, error), commands = self.counted(
                        lambda: metadata_check.written(self.fields, clip, values, own))
                    self.assertEqual(("failed", images.CLIP_NOT_EDITED, 0), (how, error, commands))


if __name__ == "__main__":
    unittest.main()
