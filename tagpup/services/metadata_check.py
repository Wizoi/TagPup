"""The shadow comparison of TagPup's own JPEG writer with ExifTool's, on copies (docs/PLAN_OWN_METADATA.md, M4 step 4c,
the verification bar's points 1, 2 and 6): `metadata-check` of tagpup_cli.py. It never writes the library or a photo.

Of a library's JPEGs, the census's seeded draw (segment_census.drawn) is read, each file through shares (read_whole: a
file gone, changed while it was read, that cannot be read, or on a share that does not answer is counted by
shares.why_not_read's word and passed over), into a sandbox of this PC: SANDBOX in the temporary folder, one run on the PC at a time (byte_lock),
left by a run that was killed removed by the next, and removed at the run's end however it ends (Ctrl+C too; a file of
it another program holds a moment, as an antivirus scans a copy just written, tried once more: _removed). Each photo's
copies are in a folder of their own, numbered, so one whose removal failed is never in the next photo's way. Nothing the
run calls reads a home (the planners are pure, the writers write the copy they are given), so the sandbox is a folder,
not a home. Free space is checked before each photo (MARGIN, and COPIES of its size).

Each of OPERATIONS is planned as the app plans it (tagging's, photos' and fields' planners) from the fields the copy holds,
skipped when the copy holds the plan already, and written twice, each time on a fresh copy in a folder of its own,
through the one writer (field_values.write): once by ExifTool's rewrite (given no `saving`, as a write the own writer
refuses falls to it), once as a journaled write is (given a `saving` that saves nothing: the own writer's patch when
it fits, else the same rewrite). Then compared, a difference each:
- every segment but the EXIF, XMP and IPTC ones, in order, and the picture (its first scan to its end): the copy's own;
- the fields written, as each side reads back (fields.reads_same of fields.as_patched: a copy the own writer left stale
  holds what it held), and ExifTool's whole read of the two, tag for tag (field_values.oracle: every copy of a tag, each by its
  family 0 and 1 groups, what SET_ASIDE names aside), a tag held empty as one not held (fields.field_values: an empty
  caption ExifTool's rewrite drops and the own writer's patch leaves): a copy the own writer left stale holds the
  original's value, and KNOWN tags may differ;
- what ExifTool says of each copy as it reads it (each warning and error of its validation, by its text: field_values.READER_SAYS),
  where the own writer's copy says a thing neither ExifTool's copy nor the original says (one ExifTool heals as it reads
  still reads the same tags, and another reader may refuse it; a warning the original held already is not the writer's,
  and a copy saying fewer of the original's is no worse: its validation's counts are not compared);
- a side failing where the other wrote; a copy that could not be compared;
- a side whose second write of the same values changes a byte of its copy (each writer's write is idempotent).
How each side wrote (patch, rewrite, unchanged, failed) is counted, and whether both agree; a disagreement is counted by
its direction and is no difference (both are a correct write; the bar is what the file holds; main session, 2026-11-05).

Counts only, by default: no path, name, tag or value (the library is photographs of real people, many of them minors);
`reveal` lists each difference's photo, operation and values.
"""
import collections
import io
import json
import os
import shutil
import tempfile
import time
from contextlib import closing, contextmanager

from tagpup.core import byte_lock, dates, fields, paths, vocabulary
from tagpup.core.result import Refused
from tagpup.files import containers, field_values, shares
from tagpup.services import photos as photo_actions, roots as roots_service, segment_census, tagging
from tagpup.store import db, inspection

#: Photos compared by default; the bar is 3,000 a library (the owner's run).
SAMPLE = 100
#: The sandbox's folder in this PC's temporary folder; its lock is beside it, SANDBOX + ".lock".
SANDBOX = "tagpup-metadata-check"
#: Bytes left free on the sandbox's disk beyond COPIES of a photo's size, else the run stops.
MARGIN = 1 << 30
#: Copies of a photo the sandbox holds at once at most: the original, the two sides', and ExifTool's new file beside
#: a side's before it is moved over it.
COPIES = 4
#: Segments of a JPEG the writers edit (containers' kinds); every other is the photo's own.
METADATA = frozenset({"exif", "xmp", "xmp-extension", "iptc"})
#: The tags of what ExifTool says of a file (field_values.READER_SAYS) compared, by their name: its validation's counts
#: ("0 14 0") are not.
SAID = frozenset({"Warning", "Error"})
#: Groups and tags of ExifTool's read that are no field of the file: the file system's, those derived from others, the
#: version of the ExifTool that read it.
SET_ASIDE = frozenset({"SourceFile", "File", "Composite", "ExifTool:ExifToolVersion"})
#: Tags the two writers leave differently by design, as ExifTool 13.59 reads them (each seen in its output,
#: tests/test_the_two_writers_compared_on_copies.py): the XMP toolkit names the program that wrote the packet last, which
#: ExifTool's rewrite makes itself and the own writer's patch leaves; where the thumbnail lies in the EXIF block, which a
#: rewrite of the block lays out again and a patch leaves (the thumbnail's length is compared). Neither writer updates an
#: IPTCDigest held (DECISIONS 2026-11-03), so it is compared too. The IPTC version datasets (1:00, 2:00): ExifTool adds
#: them to every IPTC block it writes, the own writer to a record that holds a dataset (iptc_block), and leaves a block
#: an XMP-only save does not write as it was (DECISIONS 2026-11-03, 2026-11-05); no reader reads them.
KNOWN = frozenset({"XMP:XMP-x:XMPToolkit", "EXIF:IFD1:ThumbnailOffset", "IPTC:EnvelopeRecordVersion",
                   "IPTC:ApplicationRecordVersion"})
#: What every operation's planner reads of a copy.
READ = tuple(dict.fromkeys(tagging.SAVE_READ + tuple(fields.caption_fields("")) + dates.SHIFTED_FIELDS
                           + (fields.ORIENTATION, fields.PRESERVED_NAME)))
#: A keyword and a person no photo holds (fictional), a caption and a name kept; each but the person holds letters
#: outside ASCII (IPTC's character set, XMP's escaping, EXIF's UCS-2 XPComment and ASCII ImageDescription).
KEYWORD, PERSON = "Shadow Check/Lantern Walk \u00e0 Br\u00e9z\u00e9", "Wren Halloway"
CAPTION = "Lanterns along the harbour wall \u2014 caf\u00e9 & <quay>"
PRESERVED = "F\u00eate des lanternes \u2014 0412.jpg"
SHIFT_MINUTES = 90
#: (name, whether its write is given a `saving`: tried as a patch in place by the own writer).
SIDES = (("ExifTool", False), ("own", True))
TWO_AT_ONCE = "Another metadata-check is running on this PC; wait for it to end."
#: Seconds the sandbox's removal waits before its one retry, when a file of it was held by another program.
REMOVAL_RETRY = 1.0
#: Why a run ended early.
INTERRUPTED = "interrupted"
NO_ROOM = "the sandbox's disk has less than %d MB free"


def _keywords(add, remove):
    return lambda library: tagging.keywords_planner(library, lambda _path, held: (add(held), remove(held)))


def _fixed(values):
    return lambda _library: lambda _path, _held: values


def _preserved(_library):
    """Smart Rename's planner (photos.preserve_names): the name kept, unless the photo keeps one already."""
    return lambda _path, held: {} if held.get(fields.PRESERVED_NAME) else {fields.PRESERVED_NAME: PRESERVED}


#: (name, planner(library) -> plan_one(path, held) answering a file_changes.Plan or a dict of values).
OPERATIONS = (
    ("keyword added", _keywords(lambda held: [KEYWORD], lambda held: [])),
    ("last keyword taken off", _keywords(lambda held: [], lambda held: held[-1:])),
    ("person added", _keywords(lambda held: [vocabulary.person_tag(PERSON, [], [])], lambda held: [])),
    ("caption set", _fixed(fields.caption_fields(CAPTION))),
    ("caption cleared", _fixed(fields.caption_fields(""))),
    ("Date Taken shifted", lambda _library: photo_actions.date_shift_plan(SHIFT_MINUTES)),
    ("turned right", lambda _library: photo_actions.rotate_plan("right")),
    ("mirrored", lambda _library: photo_actions.rotate_plan("mirror")),
    ("preserved name set", _preserved),
)


def sandbox_root():
    return os.path.join(tempfile.gettempdir(), SANDBOX)


def _removed(folder):
    """Remove `folder` and all it holds; when a file of it is held (another program's handle, WinError 32), once more
    after REMOVAL_RETRY. What is left after that is removed by the next run's start."""
    for wait in (REMOVAL_RETRY, None):
        try:
            shutil.rmtree(folder)
            return
        except FileNotFoundError:
            return
        except OSError:
            if wait is None:
                return
            time.sleep(wait)


@contextmanager
def sandbox():
    """The run's sandbox folder; Refused while another run holds it. Removed at the end."""
    root = sandbox_root()
    held = byte_lock.Lock(root + ".lock")
    if not held.acquire():
        raise Refused(TWO_AT_ONCE)
    try:
        _removed(root)                                   # a run killed before its cleanup
        os.makedirs(root, exist_ok=True)
        yield root
    finally:
        _removed(root)
        held.release()


def _outside(data):
    """(every segment of the JPEG `data` but its metadata ones, its picture), or None when it is no JPEG with a scan."""
    layout = containers.read(io.BytesIO(data))
    if not isinstance(layout, containers.Jpeg) or layout.image_offset is None:
        return None
    return [data[each.offset:each.end] for each in layout.segments if each.kind not in METADATA], data[layout.image_offset:]


def _written(et, path, values, own):
    """(how the write went: patch, rewrite, unchanged or failed; the fields it left stale; why it failed). `own`: the
    write is a journaled one's, the own writer's patch tried first; else ExifTool's rewrite."""
    how = []
    saving = (lambda _record, _before: how.append("patch")) if own else None
    try:
        stale = field_values.write(et, path, values, saving=saving, scratch_written=lambda: how.append("rewrite"))
    except Exception as error:
        return "failed", (), str(error)
    return (how[0] if how else "unchanged"), tuple(stale), None


def _same(a, b):
    """Do two values of ExifTool's read hold the same, in any order, one held empty as one not held?"""
    return sorted(fields.field_values(a)) == sorted(fields.field_values(b))


def _said(row):
    """{(name, text)} of what ExifTool says of a file as it reads it (field_values.READER_SAYS's SAID tags, every
    copy)."""
    return {(tag.rsplit(":", 1)[-1], str(value)) for tag, value in row.items()
            if tag.split(":")[0] == field_values.READER_SAYS and tag.rsplit(":", 1)[-1] in SAID}


def _tags_differing(original, theirs, own, stale):
    """The tags of ExifTool's whole read on which the two sides differ (a field the own writer left stale: own's holds
    other than the original's; what ExifTool says of the copy, field_values.READER_SAYS, named "ExifTool:Warning" or
    "ExifTool:Error": own's says a thing neither ExifTool's copy nor the original says). A tag's field is its family 0
    group and its name (field_values.WHOLE_READ's keys)."""
    found = ["%s:%s" % (field_values.READER_SAYS, name) for name in
             sorted({name for name, _text in _said(own) - _said(theirs) - _said(original)})]
    for tag in sorted(set(theirs) | set(own)):
        group = tag.split(":")[0]
        if group in SET_ASIDE or tag in SET_ASIDE or tag in KNOWN or group == field_values.READER_SAYS:
            continue
        if group == "MakerNotes" or "%s:%s" % (group, tag.rsplit(":", 1)[-1]) in stale:
            # A maker note ExifTool cannot parse (bad offsets) is dropped by its rewrite and kept byte for byte by the
            # own writer: the own writer's reads as the original's, or it is a difference (#3109).
            # A maker note tag that reads as ExifTool's copy reads is no difference either: ExifTool's rewrite of a note
            # it cannot parse changes what it reads, and the own copy agrees with it (#3112).
            if not _same(own.get(tag), original.get(tag)) and (
                    group != "MakerNotes" or not _same(own.get(tag), theirs.get(tag))):
                found.append(tag)
        elif not _same(theirs.get(tag), own.get(tag)):
            found.append(tag)
    return found


class _Run:
    def __init__(self, library, et, oracle, reveal):
        self.library, self.et, self.oracle, self.reveal = library, et, oracle, reveal
        self.looked, self.skipped = collections.Counter(), collections.Counter()
        self.how, self.differences = collections.defaultdict(collections.Counter), collections.Counter()
        self.revealed = []

    def differ(self, path, operation, what, detail=None):
        self.differences[what] += 1
        if self.reveal:
            self.revealed.append({"photo": path, "operation": operation, "difference": what, "detail": detail})

    def photo(self, path, folder):
        """Compare the writers on the photo at `path`, in `folder`; None, or why the run is to stop."""
        state, data = shares.read_whole(path)
        if state == "read" and _outside(data) is None:
            state = "not a JPEG with a picture"
        if state != "read":
            self.looked[state] += 1
            return None
        need = MARGIN + COPIES * len(data)
        if shutil.disk_usage(folder).free < need:
            return NO_ROOM % (need >> 20)
        original = os.path.join(folder, "original.jpg")
        with open(original, "wb") as handle:
            handle.write(data)
        before = field_values.read(self.et, [original], READ).get(paths.key(original))
        if not isinstance(before, dict):
            self.looked["read, not by ExifTool"] += 1
            return None
        self.looked["read"] += 1
        try:
            whole = self.oracle.whole([original]).get(paths.key(original), {})
        except OSError as error:
            self.differ(path, "the original", "a copy could not be compared", str(error))
            return None
        for number, (operation, planner) in enumerate(OPERATIONS):
            plan = planner(self.library)(original, before)
            after = plan if isinstance(plan, dict) else plan.after
            if not after or fields.reads_same(before, after):
                self.skipped[operation] += 1
                continue
            each = os.path.join(folder, str(number))
            os.makedirs(each)
            try:
                self.operation(path, operation, data, before, after, whole, each)
            except Exception as error:      # ExifTool refused a read of a copy, or stopped answering
                self.differ(path, operation, "a copy could not be compared", str(error))
            finally:
                shutil.rmtree(each, ignore_errors=True)
        return None

    def operation(self, path, operation, data, before, after, whole, folder):
        copies, sides = [], []
        for name, own in SIDES:
            copy = os.path.join(folder, name + ".jpg")
            with open(copy, "wb") as handle:
                handle.write(data)
            copies.append(copy)
            sides.append(_written(self.et, copy, after, own))
        (theirs, _, their_error), (own, stale, own_error) = sides
        self.how[operation]["%s, own %s" % (theirs, own)] += 1
        if (theirs == "failed") != (own == "failed"):
            self.differ(path, operation, "one side failed", their_error or own_error)
        if "failed" in (theirs, own):
            return
        kept = _outside(data)
        for (name, _own), copy in zip(SIDES, copies):
            with open(copy, "rb") as handle:
                now = _outside(handle.read())
            if now is None or now[1] != kept[1]:
                self.differ(path, operation, "%s changed the picture" % name)
            elif now[0] != kept[0]:
                self.differ(path, operation, "%s changed a segment outside EXIF, XMP and IPTC" % name)
        read_back = field_values.read(self.et, copies, list(after))
        was = {field: before.get(field, []) for field in after}
        for (name, _own), copy, left in zip(SIDES, copies, ((), stale)):
            held = read_back.get(paths.key(copy))
            if not isinstance(held, dict) or not fields.reads_same(held, fields.as_patched(was, after, left)):
                self.differ(path, operation, "%s's fields read back other than written" % name, held)
        found = self.oracle.whole(copies)
        for tag in _tags_differing(whole, found.get(paths.key(copies[0]), {}), found.get(paths.key(copies[1]), {}),
                                   set(stale)):
            self.differ(path, operation, "the sides differ in %s" % tag,
                        [found.get(paths.key(copy), {}).get(tag) for copy in copies])
        for (name, own), copy in zip(SIDES, copies):
            with open(copy, "rb") as handle:
                first = handle.read()
            how, _stale, error = _written(self.et, copy, after, own)
            with open(copy, "rb") as handle:
                if handle.read() != first:
                    self.differ(path, operation, "%s's second write of the same values changed the file" % name,
                                error or how)


def check(library, exiftool_path, sample=SAMPLE, seed=segment_census.SEED, reveal=False, say=None):
    """The shadow comparison of `sample` of `library`'s JPEGs (the module): {"jpegs", "sampled", "seed", "looked",
    "skipped", "how", "differences", "stopped" (None, or why the run ended early), "revealed"}. Refused: a sample under
    one, a library whose root this machine does not place, a run already going on this PC. `say(line)` is told the
    progress."""
    if sample < 1:
        raise Refused("A metadata-check compares one photo or more.")
    sentence = roots_service.problem(library)
    if sentence:
        raise Refused(sentence)
    with closing(db.connect(db.readonly_uri(library.path), uri=True)) as conn:
        jpegs = [path for _photo_id, path in inspection.photo_files(conn) if segment_census.is_jpeg(path)]
    chosen = segment_census.drawn(jpegs, sample, seed)
    stopped = None
    with sandbox() as root, field_values.session(exiftool_path) as et, field_values.oracle(exiftool_path) as oracle:
        run = _Run(library, et, oracle, reveal)
        try:
            for done, path in enumerate(chosen, 1):
                folder = tempfile.mkdtemp(prefix="%d-" % done, dir=root)
                try:
                    stopped = run.photo(path, folder)
                finally:
                    shutil.rmtree(folder, ignore_errors=True)
                if stopped:
                    break
                if say and done % 100 == 0:
                    say("%d of %d photos compared, %d differences so far." % (done, len(chosen),
                                                                               sum(run.differences.values())))
        except KeyboardInterrupt:
            stopped = INTERRUPTED
    return {"jpegs": len(jpegs), "sampled": len(chosen), "seed": seed, "looked": dict(run.looked),
            "skipped": dict(run.skipped), "how": {name: dict(found) for name, found in run.how.items()},
            "differences": dict(run.differences), "stopped": stopped, "revealed": run.revealed}


def lines(found):
    """The check's answer as the lines printed: counts only (and, revealed, each difference)."""
    said = ["%d JPEGs, %d drawn (seed %d)%s." % (found["jpegs"], found["sampled"], found["seed"],
                                                 "; stopped: " + found["stopped"] if found["stopped"] else ""),
            "  looked: " + (", ".join("%s %d" % item for item in sorted(found["looked"].items())) or "none")]
    for operation, _planner in OPERATIONS:
        how = found["how"].get(operation, {})
        agreed = sum(n for key, n in how.items() if len(set(key.split(", own "))) == 1)
        said.append("  %s: %d written, %d agreed on patch or rewrite, %d held it already (%s)" % (
            operation, sum(how.values()), agreed, found["skipped"].get(operation, 0),
            ", ".join("ExifTool %s %d" % item for item in sorted(how.items())) or "none"))
    said.append("  differences: %d" % sum(found["differences"].values()))
    said.extend("    %s: %d" % item for item in sorted(found["differences"].items()))
    said.extend("    %s" % json.dumps(each, ensure_ascii=False) for each in found["revealed"])
    return said
