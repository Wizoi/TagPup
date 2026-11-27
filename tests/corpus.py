"""Photos, clips and libraries made once by the real writer, and copied for each test.

A test's photo is made by the code that makes the real thing (CLAUDE.md): PIL saves it and ExifTool writes its fields, a
clip is ffmpeg's, a library is schema.ensure's. Made per test, that was about half of the suite's time (a JPEG given its
fields by a fresh ExifTool is 0.4 s, a copy of it 1 ms; docs/plans/fast-trustworthy-test-suite.md, cut 2). So each item
is made once, under a name that is the digest of what it was made with -- the recipe the test gives (its content: size,
colour, fields, the command's arguments) and the writer's identity (identity: a new ExifTool or ffmpeg is a new key) --
and each test is given a copy of it at the path it names.

    corpus.jpeg(path, values, exiftool)                   # the JPEG FilesCase.make made, at `path`
    corpus.noise_jpeg(path, seed, size, values, exiftool)  # damaged_photos.whole_jpeg's noise, given `values`
    corpus.clip(path, arguments, ffmpeg)                   # what `ffmpeg <arguments> <path>` makes
    corpus.library(path)                                   # a new library, as schema.ensure makes one
    corpus.made(path, recipe, build, tool)                 # any file: build(target) makes it, once per run
    corpus.segment(marker, body)                           # a JPEG segment laid by hand (and xmp_, iptc_, exif_segment)
    corpus.of(name, folder, exiftool, ffmpeg)              # the conformance corpus's item `name` (ITEMS), in `folder`

Kept across runs: what an external writer makes for this module's makers (jpeg, noise_jpeg, clip; ExifTool and ffmpeg
are not ours: given the same input and arguments they make the same bytes, owner 2026-11-24) is kept in the machine's
folder, %LOCALAPPDATA%\\TagPupTestCache\\corpus (CACHE_ENV names another), under a folder of its generation: the
digest of the code the makers run (this module, tests/damaged_photos.py, tagpup's ExifToolSession) and of Python's and
PIL's versions, so a change to any of them makes every item again. Every worktree and the checkout share it; nothing in
it is a checkout's. Its size is capped (CAP): past it, the items used least recently are deleted (a use marks an item's
modified time, at most once an hour). What our own code makes (a library: the schema and its migrations change from one
commit to the next), and what a test makes by a build of its own (made), stays in the run's folder: tools/run_tests.py
makes one for the run (ENV, inherited by every file's process) and removes it at the end; a test file run on its own
gets one for its process, removed as the process ends. Never under the owner's home or library.

What a copy is: the bytes of the item, in a new file (shutil.copyfile: never a link), so a write to it changes neither the
corpus nor another test's copy. Its modified time is the copy's, now, and its file id a new one, as a file just made
has; nothing in tagpup reads a file id, and a test that wants an older time sets it after, as it did. Two copies of one
recipe hold the same bytes, as two files made by one recipe did: what makes the photos of a test distinct is still its
recipe (a tag, a date), never the copy. What a writer stamps into a file as the time it made it (a .wmv's ASF creation
time) is when the item was made, perhaps days before: no assertion reads it (a .wmv is never dated by it).

Two processes asking for one item at once (two tests, or two runs in two worktrees): the maker takes a turn on its
folder's lock for the item (one of STRIPES, tagpup.core.byte_lock), looks again, and makes it under a name of its own
(`<key>-part-making<ext>`) that it renames into place (os.replace), so the item's name only ever holds a whole item. A
run killed part-way leaves at most a part file, never read (and deleted by the next eviction once STALE), and a lock
the system lets go of. A folder deleted mid-run, or an item evicted by another process while this one copies it, is
made again by the next test that asks. An item is read-only (its read-only attribute, set before it takes its name): a
test that opens it for writing fails at once.
"""
import atexit
import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import struct
import sys
import tempfile
import time

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

from tagpup.core import byte_lock  # noqa: E402
from tagpup.files import xmp_packet  # noqa: E402

#: The run's corpus folder, named by tools/run_tests.py for every file's process: the items not kept across runs.
ENV = "TAGPUP_TEST_CORPUS"
#: Another folder than the machine's for the items kept across runs (kept_root).
CACHE_ENV = "TAGPUP_TEST_CORPUS_CACHE"
#: How long a process waits for another making an item of its stripe (an ffmpeg clip is about a second; a loaded
#: machine, more).
WAIT = 120.0
#: The kept items' bytes, every generation together, past which the least recently used are deleted down to 80% of it.
CAP = 512 * 1024 * 1024
#: An item's use is marked (its modified time) when its mark is older than this, so a copy rarely writes.
TOUCH = 3600.0
#: A part file older than this is a killed maker's (a maker of a whole clip takes seconds).
STALE = 600.0
#: The locks the makers of a folder take turns on: one per key, folded, so their number is bounded.
STRIPES = 64
#: The code the makers of kept items run: a change to one makes every kept item again.
#: The captured layouts a recipe reads (structure and fictional texts of the same length only).
FIXTURES = os.path.join(WORKSPACE_DIR, "tests", "fixtures")
MAKERS = (os.path.abspath(__file__), os.path.join(WORKSPACE_DIR, "tests", "damaged_photos.py"),
          os.path.join(WORKSPACE_DIR, "tagpup", "files", "exiftool_session.py"),
          os.path.join(FIXTURES, "xmp_in_ifd0.json"), os.path.join(FIXTURES, "clip_headers.json"))

_own = None
_generation = None
_identities = {}


def folder():
    """The run's corpus folder: the runner's, else one of this process's own, removed as it ends."""
    global _own
    named = os.environ.get(ENV)
    if named:
        return named
    if _own is None:
        _own = tempfile.mkdtemp(prefix="tagpup_corpus_")
        atexit.register(remove, _own)
    return _own


def kept_root():
    """The machine's folder of the items kept across runs: CACHE_ENV's, else under %LOCALAPPDATA%."""
    return os.environ.get(CACHE_ENV) or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "TagPupTestCache", "corpus")


def kept():
    """The folder of this generation's kept items: under kept_root, named by the digest of the makers' code (MAKERS) and
    Python's and PIL's versions."""
    global _generation
    if _generation is None:
        import PIL
        made_with = [list(sys.version_info[:2]), PIL.__version__]
        for each in MAKERS:
            with open(each, "rb") as handle:
                made_with.append(hashlib.sha256(handle.read()).hexdigest())
        _generation = hashlib.sha256(json.dumps(made_with).encode("utf-8")).hexdigest()[:16]
    return os.path.join(kept_root(), _generation)


def identity(tool):
    """What the writer at `tool` is, as far as what it makes. The Windows ExifTool is a small launcher beside
    `exiftool_files`, the same through an upgrade: its library's version, size and time, and the launcher's size. Ffmpeg:
    its own answer to -version (its version, its build's configuration, its libraries'). Any other program: its path,
    size and modified time. Asked again whenever the files looked at change; once per process otherwise."""
    path = os.path.abspath(tool)
    held = os.stat(path)
    library = os.path.join(os.path.dirname(path), "exiftool_files", "lib", "Image", "ExifTool.pm")
    if os.path.exists(library):
        lib = os.stat(library)
        stamp = [held.st_size, lib.st_size, lib.st_mtime_ns]

        def ask():
            return ["exiftool", _version_said(library)] + stamp
    elif os.path.splitext(os.path.basename(path))[0].lower() == "ffmpeg":
        stamp = [held.st_size, held.st_mtime_ns]

        def ask():
            return ["ffmpeg", _version_answered(path)]
    else:
        return [os.path.normcase(path), held.st_size, held.st_mtime_ns]
    known = _identities.get(path)
    if known is None or known[0] != stamp:
        known = (stamp, ask())
        _identities[path] = known
    return known[1]


def _version_said(library):
    with open(library, encoding="latin-1") as handle:
        for line in handle:
            if line.startswith("$VERSION"):
                return line.split("=", 1)[1].strip().strip(";").strip("'\"")
    raise RuntimeError("%s says no $VERSION" % library)


def _version_answered(program):
    from tagpup.core import processes
    done = processes.run([program, "-version"], capture_output=True, timeout=60)
    if done.returncode != 0:
        raise RuntimeError("%s -version failed: %s" % (program, done.stderr.decode("utf-8", "replace")))
    return done.stdout.decode("utf-8", "replace").replace("\r\n", "\n")


def key(recipe, tool=None):
    """The digest of what an item is made with: `recipe` (JSON: the content and the arguments) and `tool`'s identity."""
    text = json.dumps([recipe, identity(tool) if tool else None], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def item(recipe, tool=None, ext="", keep=False):
    """Where the item of `recipe` is (or will be): in the kept folder (`keep`) or the run's."""
    return os.path.join(kept() if keep else folder(), key(recipe, tool) + ext)


def made(path, recipe, build, tool=None, keep=False):
    """A copy at `path` of the item `build(target)` makes -- with the real writer, at `target`, a new file of `path`'s
    extension -- made once for `recipe` and `tool`: once per run, or once while it is kept (`keep`: only this module's
    makers, whose recipe is all their build is given). Returns `path`."""
    ext = os.path.splitext(path)[1]
    source = item(recipe, tool, ext, keep)
    try:
        held = os.stat(source)
    except FileNotFoundError:
        _make(source, build, keep)
    else:
        if keep and time.time() - held.st_mtime > TOUCH:
            with contextlib.suppress(OSError):
                os.utime(source)   # used: the last to be evicted
    try:
        shutil.copyfile(source, path)
    except FileNotFoundError:
        if os.path.exists(os.path.dirname(os.path.abspath(path))) and not os.path.exists(source):
            _make(source, build, keep)   # its folder deleted, or the item evicted, meanwhile: made again
            shutil.copyfile(source, path)
        else:
            raise
    return path


def _make(source, build, keep):
    where, name = os.path.split(source)
    os.makedirs(where, exist_ok=True)
    stripe = int(name[:8], 16) % STRIPES
    turn = byte_lock.Lock(os.path.join(where, "stripe-%02d.lock" % stripe))
    if not turn.acquire(wait=WAIT):
        raise RuntimeError("another test process has been making an item of %s's stripe for %d s" % (name, WAIT))
    try:
        if os.path.exists(source):
            return   # another process made it while this one waited
        stem, ext = os.path.splitext(source)
        part = "%s-part-making%s" % (stem, ext)
        _discard(part)   # left by a maker killed part-way (one maker at a time holds the item's lock; the part carries no process number: a file's name is in the ExifTool replay key)
        try:
            build(part)
            os.chmod(part, stat.S_IREAD)
            os.replace(part, source)
        except BaseException:
            _discard(part)
            raise
    finally:
        turn.release()
    if keep:
        evict(kept_root())


def evict(root):
    """Delete the kept items under `root` used least recently until they hold under 80% of CAP, when they hold more;
    and every part a killed maker left (older than STALE). An item another process is copying this moment is left
    (Windows refuses its deletion); one it is about to copy is made again by it. Returns the items deleted."""
    now = time.time()
    found = []
    for generation in _listing(root):
        where = os.path.join(root, generation)
        for name in _listing(where):
            path = os.path.join(where, name)
            if name.endswith(".lock"):
                continue
            try:
                held = os.stat(path)
            except OSError:
                continue
            if "-part-" in name:
                if now - held.st_mtime > STALE:
                    with contextlib.suppress(OSError):
                        _discard(path)
                continue
            found.append((held.st_mtime, held.st_size, path))
    total = sum(size for _, size, _ in found)
    gone = []
    if total <= CAP:
        return gone
    for _, size, path in sorted(found):
        if total < CAP * 0.8:
            break
        try:
            _discard(path)
        except OSError:
            continue
        total -= size
        gone.append(path)
    return gone


def _listing(where):
    try:
        return os.listdir(where)
    except (FileNotFoundError, NotADirectoryError):
        return []


def _discard(path):
    if os.path.exists(path):
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        os.remove(path)


def remove(path):
    """Remove a corpus folder, read-only items and all; True when it is gone. A file still held is left to the temporary
    folder (said by the runner): a corpus left behind decides nothing, the next run names a folder of its own."""
    def writable(_function, failed, _error):
        with contextlib.suppress(OSError):
            os.chmod(failed, stat.S_IREAD | stat.S_IWRITE)
            os.remove(failed)
    shutil.rmtree(path, onerror=writable)
    return not os.path.exists(path)


@contextlib.contextmanager
def for_run():
    """A corpus folder for a run of tools/run_tests.py, named in this process's environment, and so in every test
    file's process it starts, until the run ends; then removed."""
    path = tempfile.mkdtemp(prefix="tagpup_corpus_")
    before = os.environ.get(ENV)
    os.environ[ENV] = path
    try:
        yield path
    finally:
        if before is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = before
        if not remove(path):
            print("note: could not delete %s" % path, flush=True)


def jpeg(path, values, exiftool, size=(16, 12), colour=(90, 110, 130)):
    """At `path`, the JPEG PIL saves of `size` in one `colour`, given `values` ({field: value}) by ExifTool as another
    program writes a file (-overwrite_original): what tests/test_file_journal.FilesCase.make made per photo."""
    def build(target):
        from PIL import Image
        Image.new("RGB", tuple(size), tuple(colour)).save(target, "JPEG")
        _written(target, values, exiftool)
    return made(path, ["jpeg", list(size), list(colour), values], build, exiftool, keep=True)


def noise_jpeg(path, seed, size, values, exiftool):
    """At `path` (its folder made), the JPEG of noise damaged_photos.whole_jpeg saves for `seed` and `size`, given
    `values` by ExifTool as another program writes a file; with no values, PIL's alone (milliseconds: not kept)."""
    import damaged_photos
    if not values:
        damaged_photos.whole_jpeg(path, seed=seed, size=tuple(size))
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def build(target):
        damaged_photos.whole_jpeg(target, seed=seed, size=tuple(size))
        _written(target, values, exiftool)
    return made(path, ["noise", seed, list(size), values], build, exiftool, keep=True)


def clip(path, arguments, ffmpeg):
    """At `path`, what `ffmpeg <arguments> <target>` makes (the format `path`'s extension names, unless the arguments
    name one). Returns `path`."""
    def build(target):
        from tagpup.core import processes
        done = processes.run([ffmpeg] + list(arguments) + [target], capture_output=True, timeout=120)
        if done.returncode != 0:
            raise RuntimeError("ffmpeg failed: %s" % done.stderr.decode("utf-8", "replace"))
    return made(path, ["ffmpeg", list(arguments)], build, ffmpeg, keep=True)


def _written(target, values, exiftool, params=()):
    """`target` given `values` ({tag: value}) and the raw arguments `params` (-ExifByteOrder=II, -ThumbnailImage<=) by
    ExifTool, as another program writes a file."""
    from tagpup.files.exiftool_session import ExifToolSession
    with ExifToolSession(executable=exiftool) as et:
        if values:
            et.set_tags([target], tags=values, params=["-overwrite_original", *params])
        else:
            et.execute("-overwrite_original", *params, target)


def library(path):
    """A new library at `path`, as schema.ensure makes one: a copy of one made once per run (not kept: the schema is ours), checkpointed into a file of
    its own by the db's owner (db.copy_database: rollback-journal mode, nothing beside it). Returns `path`."""
    from tagpup.store import db, schema

    def build(target):
        scratch = target + ".made.db"
        schema.ensure(scratch)
        db.copy_database(scratch, target)
        for leftover in (scratch, scratch + "-wal", scratch + "-shm"):
            _discard(leftover)
    with open(schema.__file__, "rb") as handle:
        version = hashlib.sha256(handle.read()).hexdigest()
    return made(path, ["library", version], build)


# Segments laid by hand, byte by byte: what a test of a layout gives the readers and editors.

def segment(marker, body):
    return b"\xff" + bytes([marker]) + struct.pack(">H", 2 + len(body)) + body


def xmp_segment(between=b"\n" + b" " * 99):
    packet = (b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/"></x:xmpmeta>'
              + between + b'<?xpacket end="w"?>')
    return segment(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + packet)


def resource(resource_id, data):
    return b"8BIM" + struct.pack(">H", resource_id) + b"\0\0" + struct.pack(">I", len(data)) + data + b"\0" * (len(data) & 1)


def iptc_segment(*resources, after=b""):
    return segment(0xED, b"Photoshop 3.0\0" + b"".join(resources) + after)


def exif_segment(zeros):
    """A little-endian TIFF: IFD0 with one ASCII entry whose value sits after the IFD, then `zeros` zero bytes."""
    artist = b"Someone\0\0\0"
    ifd = struct.pack("<H", 1) + struct.pack("<HHII", 0x013B, 2, len(artist), 8 + 2 + 12 + 4) + struct.pack("<I", 0)
    return segment(0xE1, b"Exif\0\0" + b"II*\0" + struct.pack("<I", 8) + ifd + artist + bytes(zeros))


#: Photoshop's Print Flags Information resource as 7 of 300 photo_index JPEGs hold one (a reviewer's count, 2026-11-03):
#: 8BIM 0x2710, no name, a body of 22 bytes.
PRINT_FLAGS = resource(0x2710, bytes(range(1, 23)))


def second_app13(data):
    """The JPEG `data` given a second APP13 segment, holding PRINT_FLAGS alone, after its first: Photoshop's resources
    split over two segments (0 / 2 / 3 / 0 photos in the census, docs/domains/files.md)."""
    from tagpup.files import containers
    first = containers.read(io.BytesIO(data)).of_kind("iptc")[0]
    return data[:first.end] + iptc_segment(PRINT_FLAGS) + data[first.end:]


#: The header of a photo_index JPEG whose EXIF's IFD0 holds an XMP packet (0x02BC: 39 / 0 / 0 / 0, #3108), captured,
#: its values fictional.
with open(os.path.join(FIXTURES, "xmp_in_ifd0.json"), encoding="utf-8") as _f:
    XMP_IN_IFD0 = json.load(_f)


def photo_with_xmp_in_ifd0(path, tag=0x02BC):
    """A JPEG holding the captured header's EXIF, its IFD0's packet entry under `tag`, then its APP1 packet, as the photo
    held them (its ICC profile between the two left out), before a picture PIL makes."""
    from PIL import Image
    from tagpup.files import containers

    tiff = bytearray(XMP_IN_IFD0["tiff_size"])
    for at, hexed in XMP_IN_IFD0["tiff"]:
        tiff[at:at + len(hexed) // 2] = bytes.fromhex(hexed)
    packet, at = XMP_IN_IFD0["ifd0_packet"].encode("utf-8"), XMP_IN_IFD0["ifd0_packet_at"]
    tiff[at:at + len(packet)] = packet
    walked = containers.exif_tiff(bytes(tiff))
    ifd0 = walked.named["ifd0"]
    at = ifd0.offset + 2 + 12 * [entry[0] for entry in ifd0.entries].index(0x02BC)
    tiff[at:at + 2] = struct.pack(walked.order + "H", tag)
    xmp = XMP_IN_IFD0["xmp"].replace("<?xpacket end", " " * XMP_IN_IFD0["xmp_padding"] + "<?xpacket end", 1)
    picture = io.BytesIO()
    Image.new("RGB", (16, 12), (90, 110, 130)).save(picture, "JPEG")
    picture = picture.getvalue()
    jfif = 4 + int.from_bytes(picture[4:6], "big")
    with open(path, "wb") as handle:
        handle.write(picture[:jfif] + segment(0xE1, containers.EXIF_HEAD + bytes(tiff))
                     + segment(0xE1, containers.XMP_HEAD + xmp.encode("utf-8")) + picture[jfif:])
    return path


#: Real clips' header layouts (tests/fixtures/clip_headers.json: their headers' bytes, captured read-only by container,
#: version and track count, their dates moved to a fictional day): the shape of most of the 681
#: clips the one-off dated, of the clips holding version 1 headers, of a dated three-track .mov, and of one whose track
#: and media headers disagree.
with open(os.path.join(FIXTURES, "clip_headers.json"), encoding="utf-8") as _f:
    SHAPES = json.load(_f)["shapes"]
ZERO_HEADERS = "mp4 version 0, two tracks, zero headers"
VERSION_ONE = "mp4 version 1 movie and track headers, version 0 media headers, two tracks, zero"
THREE_TRACKS = "mov version 0, three tracks, dated"
DISAGREEING = "mov version 0, four tracks whose track and media headers disagree"


def _box(kind, content=b""):
    return struct.pack(">I", 8 + len(content)) + kind.encode("latin-1") + content


def _boxes(entries, at, depth):
    made = b""
    while at < len(entries) and entries[at]["depth"] == depth:
        entry = entries[at]
        children, at = _boxes(entries, at + 1, depth + 1)
        made += bytes.fromhex(entry["bytes"]) if entry["bytes"] else _box(entry["type"], children)
    return made, at


def clip_of(shape, moov_after_mdat=False):
    """The bytes of a clip laid out as the real clips of `shape` are: its boxes under moov as the fixture lists them (a
    header's bytes as captured, any other box empty), an ftyp before them and an mdat before or after."""
    moov, _ = _boxes(SHAPES[shape]["boxes"], 0, 0)
    brand = b"qt  \0\0\0\0qt  " if shape.startswith("mov") else b"isom\0\0\2\0isomiso2mp41"
    mdat = _box("mdat", bytes(64))
    return _box("ftyp", brand) + (mdat + moov if moov_after_mdat else moov + mdat)


# An EXIF segment's body laid by hand as cameras lay one out: its IFDs, a MakerNote, a thumbnail and slack.

THUMB = b"\xff\xd8\xff\xdb" + bytes(range(40)) + b"\xff\xd9"


def text(value):
    return value.encode("utf-8") + b"\0"


def ucs2(value):
    return value.encode("utf-16-le") + b"\0\0"


def layout_tiff(order, groups, slack, tail):
    """The TIFF bytes of `groups` ((name, entries, bytes after its values), laid in that order), `slack` zero bytes and
    `tail`. An entry is (tag, type, data); data may be a function of (the groups' offsets, its own offset)."""
    from tagpup.files import containers
    pos = {}
    for _pass in range(2):  # the first measures, the second points: no size depends on a pointer
        out = bytearray(b"II*\0" if order == "<" else b"MM\0*") + struct.pack(order + "I", 8)
        for name, entries, extra in groups:
            pos[name] = len(out)
            values_at = len(out) + 2 + 12 * len(entries) + 4
            table, values = struct.pack(order + "H", len(entries)), b""
            for tag, kind, data in entries:
                if callable(data):
                    data = data(pos, values_at + len(values))
                head = struct.pack(order + "HHI", tag, kind, len(data) // containers.TIFF_TYPE_SIZES[kind])
                if len(data) <= 4:
                    table += head + data.ljust(4, b"\0")
                else:
                    table += head + struct.pack(order + "I", values_at + len(values))
                    values += data + bytes(len(data) & 1)
            out += table + struct.pack(order + "I", pos.get("ifd1", 0) if name == "ifd0" else 0) + values
            pos[name + "+"] = len(out)
            out += extra
        out += bytes(slack) + tail
    return bytes(out)


def block(order="<", ifd0=None, exif=None, slack=64, tail=b"", make=b"TestCam", sort=True, thumb=True, note=None):
    """An EXIF segment's body: IFD0, Exif (exif=False: none), GPS, IFD1 and its thumbnail, as a camera lays them out,
    then the slack. `sort=False` puts the owned IFD0 tags after the others, out of tag order."""
    u16, u32 = (lambda v: struct.pack(order + "H", v)), (lambda v: struct.pack(order + "I", v))
    if ifd0 is None:
        ifd0 = {0x010E: (2, text("A day at the lake")), 0x0112: (3, u16(1)), 0x9C9C: (1, ucs2("Kites")),
                0x9C9E: (1, ucs2("Beach;Kites"))}
    if exif is None:
        exif = {0x9003: (2, text("2019:07:14 10:22:31")), 0x9004: (2, text("2019:07:14 10:22:31")),
                0x9290: (2, text("42")), 0x9291: (2, text("042")), 0x9292: (2, text("0420"))}
    zero = [(0x010F, 2, text(make.decode())), (0x0131, 2, text("Darkroom 2.1")),
            (0x8825, 4, lambda p, _at: u32(p.get("gps", 0)))]
    if exif is not False:
        zero.append((0x8769, 4, lambda p, _at: u32(p.get("exif", 0))))
    zero = sorted(zero, key=lambda e: e[0]) + [(tag, kind, data) for tag, (kind, data) in ifd0.items()]
    groups = [("ifd0", sorted(zero, key=lambda e: e[0]) if sort else zero, b"")]
    if exif is not False:
        sub = [(0x829A, 5, u32(1) + u32(250)), (0x9000, 7, b"0232")]
        sub += [(tag, kind, data) for tag, (kind, data) in exif.items()] + ([(0x927C, 7, note)] if note else [])
        groups.append(("exif", sorted(sub, key=lambda e: e[0]), b""))
    groups.append(("gps", [(0x0000, 1, b"\2\3\0\0")], b""))
    if thumb:
        groups.append(("ifd1", [(0x0103, 3, u16(6)), (0x0201, 4, lambda p, _at: u32(p.get("ifd1+", 0))),
                                (0x0202, 4, u32(len(THUMB)))], THUMB))
    return b"Exif\0\0" + layout_tiff(order, groups, slack, tail)


def note_ifd(order, shift=0, footer=False, told=None):
    """A MakerNote with one IFD whose one value lies inside it, counted from the TIFF header as if it had been written
    `shift` bytes before where it lies (Canon's form; `footer`: ending in Canon's record of where it was written, or
    of where it was `told` it was written, `told` bytes before where it lies)."""
    def made(_pos, at):
        base = at - shift
        data = (struct.pack(order + "H", 1) + struct.pack(order + "HHII", 0x0007, 2, 8, base + 18) + bytes(4)
                + b"FW 1.0.0")
        said = base if told is None else at - told
        return data + ((b"II*\0" if order == "<" else b"MM\0*") + struct.pack(order + "I", said) if footer else b"")
    return made


def xmp_of_zeros(data):
    """The JPEG `data`, its XMP packet's bytes all zero, its segment's length as it was: 3 / 0 / 0 / 0 photos (#2349),
    which ExifTool reads as "Invalid XMP" and leaves as they were."""
    header = b"http://ns.adobe.com/xap/1.0/\x00"
    at = data.index(header)
    start, length = at + len(header), int.from_bytes(data[at - 2:at], "big") - 2 - len(header)
    return data[:start] + bytes(length) + data[start + length:]


# A clip's XMP packet as Adobe's tools write one: properties as attributes, padded.

BOM = chr(0xFEFF).encode("utf-8")
ADOBE_BEGIN = b'<?xpacket begin="' + BOM + b'" id="W5M0MpCehiHzreSzNTczkc9d"?>'
#: A clip's packet as Adobe Premiere and Media Encoder write it (40 live; the `premiere_mp4` item): its text properties attributes of the one
#: Description, element children after them, wrapped, with padding.
PREMIERE = ADOBE_BEGIN + b"""
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 5.6-c148 79.164036, 2019/08/13-01:06:57        ">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:xmpDM="http://ns.adobe.com/xmp/1.0/DynamicMedia/"
    xmlns:tiff="http://ns.adobe.com/tiff/1.0/"
    xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"
    xmlns:stEvt="http://ns.adobe.com/xap/1.0/sType/ResourceEvent#"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
   xmp:CreateDate="2018-06-02T14:03:11Z"
   xmp:MetadataDate="2018-06-02T15:00:00-07:00"
   xmpDM:videoFrameRate="29.970030"
   tiff:Orientation="1"
   xmpMM:InstanceID="xmp.iid:7c2d0e4a"
   xmpMM:DocumentID="xmp.did:7c2d0e4a"
   dc:format="H.264">
   <xmpDM:duration
    xmpDM:value="1001"
    xmpDM:scale="1/30000"/>
   <xmpMM:History>
    <rdf:Seq>
     <rdf:li
      stEvt:action="saved"
      stEvt:instanceID="xmp.iid:7c2d0e4a"
      stEvt:softwareAgent="Adobe Media Encoder 2020.0 (Windows)"
      stEvt:changed="/"/>
    </rdf:Seq>
   </xmpMM:History>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>""" + xmp_packet.padding(2400) + b'<?xpacket end="w"?>'


_EVENT = b"""
     <rdf:li
      stEvt:action="%s"
      stEvt:instanceID="xmp.iid:%08x-4b1e-4c2a-9d3f-%012x"
      stEvt:when="2018-06-%02dT14:%02d:%02d-07:00"
      stEvt:softwareAgent="Adobe Premiere Pro CC 2018.1 (Windows)"
      stEvt:changed="/"
      stEvt:parameters="from application/vnd.adobe.premiere-project to video/mp4 (H.264, Match Source - High bitrate, VBR 2 pass)"/>"""
_PANTRY = b"""
     <rdf:li>
      <rdf:Description
       xmpMM:InstanceID="xmp.iid:%08x-0d2e-4f61-8a17-000000000000"
       xmpMM:DocumentID="xmp.did:%08x-0d2e-4f61-8a17-000000000000"
       xmpMM:OriginalDocumentID="xmp.did:%08x-0d2e-4f61-8a17-000000000000"
       xmp:CreateDate="2018-06-02T14:03:11Z"
       xmpDM:videoFrameRate="29.970030"/>
     </rdf:li>"""


def premiere(events=515, pantry=15):
    """A clip's packet as Premiere writes one after many saves, PREMIERE grown as the 1 live packet over 64 KB is
    (counted 2026-11-12: 206,986 bytes, of which 204,918 XML, 16 rdf:Descriptions, 530 rdf:li, 2,068 of padding):
    `events` saves in its xmpMM:History and `pantry` Descriptions in its xmpMM:Pantry, mostly XML. lr is declared, as
    in a packet TagPup saved a keyword into once, so a keyword added and taken off gives its bytes back."""
    history = b"".join(_EVENT % ((b"saved", b"converted")[n % 2], n, n, 1 + n % 28, n % 60, n * 7 % 60)
                       for n in range(events))
    stored = b"".join(_PANTRY % (n, n, n) for n in range(pantry))
    grown = PREMIERE.replace(b"""
     <rdf:li
      stEvt:action="saved"
      stEvt:instanceID="xmp.iid:7c2d0e4a"
      stEvt:softwareAgent="Adobe Media Encoder 2020.0 (Windows)"
      stEvt:changed="/"/>""", history).replace(
        b"   </xmpMM:History>\n", b"   </xmpMM:History>\n   <xmpMM:Pantry>\n    <rdf:Bag>" + stored
        + b"\n    </rdf:Bag>\n   </xmpMM:Pantry>\n").replace(
        b'    xmlns:dc="http://purl.org/dc/elements/1.1/"\n',
        b'    xmlns:dc="http://purl.org/dc/elements/1.1/"\n    xmlns:lr="http://ns.adobe.com/lightroom/1.0/"\n')
    return grown.replace(xmp_packet.padding(2400), xmp_packet.padding(2068))


# The conformance corpus (docs/plans/own-metadata-t1.md, section 1): an item of each layout of the libraries a field's
# read or write turns on, by name (ITEMS; `of`). Kept: a noise picture PIL makes, then ExifTool's or ffmpeg's write, its
# build calling nothing of tagpup's (the values it writes are in its recipe, so its key). Laid: bytes laid by Python
# over a kept item's, per run. Every text fictional, every picture noise; each recipe names its live count (the plan's
# census, section 3, unless marked) or the user action that makes it.

#: Every item's noise picture: its size and seed (about 5 KB of real entropy, what the picture check compares).
NOISE, SEED = (64, 48), 31
#: The items' texts: `&`, `<` and letters outside ASCII, all inside cp1252 (IPTC holds them with no 1:90), but
#: UTF8_CAPTION's.
TAGS = ("Trips/Dune Départ", "People/Ellis Marlowe")
CAPTION = "Kites over the dunes & <pier> — café"
UTF8_CAPTION = "Kites over Łeba — żalno"
TITLE = "The dunes at noon"
TAKEN = "2019:06:01 10:00:00"
CAMERA = {"EXIF:Make": "Fieldcam", "EXIF:Model": "FC-1", "EXIF:DateTimeOriginal": TAKEN, "EXIF:CreateDate": TAKEN,
          "EXIF:Orientation#": 1}
#: A clip's ffmpeg arguments: 64 x 48, 1 s, no stamp of when or by which build it was made.
CLIP = ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10", "-t", "1", "-pix_fmt", "yuv420p",
        "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact"]
#: {name: (extension, maker(path, exiftool, ffmpeg))}: every item, in the order they are named below.
ITEMS = {}


def of(name, folder, exiftool, ffmpeg=None):
    """A copy of the item `name` in `folder` (made), named for it: its path."""
    ext, maker = ITEMS[name]
    os.makedirs(folder, exist_ok=True)
    return maker(os.path.join(folder, name + ext), exiftool, ffmpeg)


def _item(ext, name=None):
    def named(maker):
        ITEMS[name or maker.__name__] = (ext, maker)
        return maker
    return named


def _tagged_values():
    """Every owned field of a photo, as the product spells them (its keyword, caption and title fields)."""
    from tagpup.core import fields
    return {**fields.keyword_fields(*fields.expand_tag_fields(list(TAGS))), **fields.caption_fields(CAPTION),
            **fields.title_fields(TITLE), "XMP:DateTimeOriginal": TAKEN, "XMP-xmpMM:DocumentID": "xmp.did:corpus-tagged",
            fields.PRESERVED_NAME: "Dunes 0412.jpg", "XMP:Rating": 3, "EXIF:GPSLatitude": 51.5,
            "EXIF:GPSLatitudeRef": "N", "EXIF:GPSLongitude": 1.25, "EXIF:GPSLongitudeRef": "E"}


def _xmp_only(values):
    return {tag: value for tag, value in values.items() if tag.startswith("XMP")}


def _camera_made(path, exiftool, values, params=()):
    """The kept JPEG of noise given `values`, `params` and an IFD1 thumbnail by ExifTool (the write that makes its EXIF:
    -ExifByteOrder is taken there alone)."""
    def build(target):
        import damaged_photos
        from PIL import Image
        damaged_photos.whole_jpeg(target, seed=SEED, size=NOISE)
        thumb = target + "-thumb.jpg"
        Image.new("RGB", (16, 12), (40, 60, 80)).save(thumb, "JPEG")
        try:
            _written(target, values, exiftool, ["-ThumbnailImage<=" + thumb, *params])
        finally:
            _discard(thumb)
    return made(path, ["camera", SEED, list(NOISE), values, list(params)], build, exiftool, keep=True)


def _over(path, exiftool, values, name, camera_params=()):
    """A kept item: the camera photo (made with `camera_params`) given `values` by ExifTool."""
    def build(target):
        _camera_made(target, exiftool, CAMERA, camera_params)
        _written(target, values, exiftool)
    return made(path, [name, CAMERA, values, list(camera_params)], build, exiftool, keep=True)


def _laid(path, exiftool, base, lay, ffmpeg=None):
    """A laid item: `lay(bytes)` of the item `base`'s bytes, per run."""
    def build(target):
        ITEMS[base][1](target, exiftool, ffmpeg)
        with open(target, "rb") as handle:
            data = handle.read()
        with open(target, "wb") as handle:
            handle.write(lay(data))
    name = os.path.splitext(os.path.basename(path))[0]
    return made(path, ["laid", name, base, identity(ffmpeg) if ffmpeg else None], build, exiftool)


def _jpeg_of(data):
    from tagpup.files import containers
    return containers.read(io.BytesIO(data))


def _one(data, kind):
    return _jpeg_of(data).of_kind(kind)[0]


def _twice(kind):
    """`data` with its first segment of `kind` once more after it."""
    def lay(data):
        held = _one(data, kind)
        return data[:held.end] + data[held.offset:held.end] + data[held.end:]
    return lay


@_item(".jpg")
def camera(path, exiftool, _ffmpeg=None):
    """A camera's photo: EXIF (Make, Model, DateTimeOriginal, Orientation 1, an IFD1 thumbnail), no XMP, no IPTC. User
    action: a camera's photo added, before its bake (no JPEG row without XMP in the four libraries after it)."""
    return _camera_made(path, exiftool, CAMERA)


@_item(".jpg")
def tagged(path, exiftool, _ffmpeg=None):
    """`camera` given every owned field (XMP; IPTC with no 1:90; the XP copies; keywords with a person's path, caption,
    title, dates, DocumentID, PreservedFileName, Rating, GPS). Every JPEG holds XMP (62,161); IPTC with no 1:90
    15,444 / 4 / 0 / 0; Rating 55,695 / 172 / 4 / 817; GPS 1,552 / 1 / 0 / 0."""
    return _over(path, exiftool, _tagged_values(), "tagged")


@_item(".jpg")
def tagged_ii(path, exiftool, _ffmpeg=None):
    """`tagged` with its EXIF in Intel order (II); ExifTool makes Motorola's (MM). User action: a photo of a camera that
    writes Intel order added (no row holds its byte order, and the segment census counts none: counted 2026-11-27)."""
    return _over(path, exiftool, _tagged_values(), "tagged_ii", ["-ExifByteOrder=II"])


@_item(".jpg")
def iptc_alone(path, exiftool, _ffmpeg=None):
    """`camera` given IPTC Keywords and an XMP packet with no dc:subject: 149 / 0 / 0 / 0 (counted 2026-11-27)."""
    return _over(path, exiftool, {"IPTC:Keywords": list(TAGS), "XMP:Title": TITLE}, "iptc_alone")


@_item(".jpg")
def iptc_utf8(path, exiftool, _ffmpeg=None):
    """`tagged` written with a caption outside cp1252, its IPTC in UTF-8 (1:90): 124 / 0 / 0 / 0."""
    from tagpup.core import fields
    values = {**_tagged_values(), **fields.caption_fields(UTF8_CAPTION), "IPTC:CodedCharacterSet": "UTF8"}
    return _over(path, exiftool, values, "iptc_utf8")


@_item(".jpg")
def app13_without_iptc(path, exiftool, _ffmpeg=None):
    """`tagged`, its APP13 holding a 0x03ED resource alone (Photoshop's resolution): 18 / 2 / 3 / 0."""
    def lay(data):
        held = _one(data, "iptc")
        return data[:held.offset] + iptc_segment(resource(0x03ED, bytes(16))) + data[held.end:]
    return _laid(path, exiftool, "tagged", lay)


@_item(".jpg")
def two_app13(path, exiftool, _ffmpeg=None):
    """`tagged` given a second APP13 (second_app13): 0 / 2 / 3 / 0."""
    return _laid(path, exiftool, "tagged", second_app13)


@_item(".jpg")
def two_exif(path, exiftool, _ffmpeg=None):
    """`tagged`, its Exif APP1 once more after it: 73 / 0 / 0 / 0."""
    return _laid(path, exiftool, "tagged", _twice("exif"))


@_item(".jpg")
def two_xmp(path, exiftool, _ffmpeg=None):
    """`tagged`, its XMP APP1 once more after it: 0, the refusal arm's one item (J2, J3a)."""
    return _laid(path, exiftool, "tagged", _twice("xmp"))


@_item(".jpg")
def extended_xmp(path, exiftool, _ffmpeg=None):
    """`tagged` given a bake of 40 faces (baked_files.values_of) by the one writer, ExifTool splitting its packet into an
    extended one: 2,488 / 85 / 5 / 7. Laid: the bake's values are the product's."""
    def build(target):
        import baked_files
        from tagpup.files import field_values
        tagged(target, exiftool)
        with field_values.opened(exiftool) as fields_of:
            fields_of.write(target, baked_files.values_of(baked_files.record(faces=40)))
    return made(path, ["laid", "extended_xmp"], build, exiftool)


@_item(".jpg")
def big_xmp(path, exiftool, _ffmpeg=None):
    """`tagged` given a hierarchy of fictional keywords, its packet near 60 KB, no extension: 202 / 40 / 5 / 9."""
    from tagpup.core import fields
    many = ["Places/Region %02d/Harbour %03d" % (n % 12, n) for n in range(BIG_KEYWORDS)]
    values = _xmp_only(fields.keyword_fields(*fields.expand_tag_fields(list(TAGS) + many)))
    return _over(path, exiftool, {**_tagged_values(), **values}, "big_xmp")


#: The keywords that bring big_xmp's packet near 60 KB (under ExifTool's 65,502 of one segment).
BIG_KEYWORDS = 560


@_item(".jpg")
def mpf(path, exiftool, _ffmpeg=None):
    """PIL's MPO of two noise frames given `tagged`'s fields by ExifTool: 2,811 / 14 / 0 / 0."""
    values = {**CAMERA, **_tagged_values()}

    def build(target):
        import random
        from PIL import Image
        frames = [Image.frombytes("RGB", NOISE, random.Random(SEED + n).randbytes(NOISE[0] * NOISE[1] * 3))
                  for n in range(2)]
        frames[0].save(target, "MPO", save_all=True, append_images=frames[1:], quality=90)
        _written(target, values, exiftool)
    return made(path, ["mpf", SEED, list(NOISE), values], build, exiftool, keep=True)


@_item(".jpg")
def trailer(path, exiftool, _ffmpeg=None):
    """`tagged` with 64 bytes after its EOI: 46 / 3 / 0 / 0."""
    return _laid(path, exiftool, "tagged", lambda data: data + bytes(range(64)))


@_item(".jpg")
def maker_note(path, exiftool, _ffmpeg=None):
    """`tagged`, its EXIF a body laid by hand holding a Canon-footed MakerNote moved 64 bytes (note_ifd): moved notes,
    Canon's 10,716, Sony's 5,108 (photo_index). Synthetic: a captured note is J1's or J3a's (T1 brief, Q5)."""
    def lay(data):
        held = _one(data, "exif")
        body = block(">", make=b"Canon", note=note_ifd(">", 64, footer=True))
        return data[:held.offset] + segment(0xE1, body) + data[held.end:]
    return _laid(path, exiftool, "tagged", lay)


@_item(".jpg")
def no_taken(path, exiftool, _ffmpeg=None):
    """`camera` without DateTimeOriginal (nor CreateDate): 60 / 0 / 3 / 0."""
    held = {tag: value for tag, value in CAMERA.items() if tag not in ("EXIF:DateTimeOriginal", "EXIF:CreateDate")}
    return _camera_made(path, exiftool, held)


@_item(".jpg")
def ifd0_xmp(path, exiftool, _ffmpeg=None):
    """The captured header whose IFD0 holds an XMP packet (photo_with_xmp_in_ifd0): 39 / 0 / 0 / 0 (#3108)."""
    return made(path, ["laid", "ifd0_xmp"], photo_with_xmp_in_ifd0)


@_item(".jpg")
def nul_xmp(path, exiftool, _ffmpeg=None):
    """`tagged`, its XMP packet all zero bytes (xmp_of_zeros): 3 / 0 / 0 / 0 (#2349)."""
    return _laid(path, exiftool, "tagged", xmp_of_zeros)


@_item(".jpg")
def no_wrapper(path, exiftool, _ffmpeg=None):
    """`tagged`, its packet with no <?xpacket wrapper: 52 / 7 / 0 / 0."""
    import re

    def lay(data):
        held = _one(data, "xmp")
        body = data[held.offset + 4:held.end]
        return data[:held.offset] + segment(0xE1, re.sub(rb"<\?xpacket[^>]*\?>", b"", body)) + data[held.end:]
    return _laid(path, exiftool, "tagged", lay)


@_item(".jpg")
def other_appn(path, exiftool, _ffmpeg=None):
    """`tagged` given an APP11, an APP15 and a COM after its XMP: 1,622 / 1,049 / 2,468 (photo_index)."""
    def lay(data):
        at = _one(data, "xmp").end
        others = segment(0xEB, b"JP\0\0corpus") + segment(0xEF, bytes(12)) + segment(0xFE, b"A fictional comment")
        return data[:at] + others + data[at:]
    return _laid(path, exiftool, "tagged", lay)


@_item(".jpg")
def thumb_turn(path, exiftool, _ffmpeg=None):
    """`tagged` given an IFD1 Orientation and an XMP-tiff:Orientation (hazard c): a thumbnail, 52,306 / 2,178 / 1,304 /
    1,280."""
    return _over(path, exiftool, {**_tagged_values(), "IFD1:Orientation#": 1, "XMP-tiff:Orientation#": 1}, "thumb_turn")


def _picture_made(path, exiftool, kind, values):
    """The kept PNG or TIFF (`kind`, PIL's) of noise given `values` by ExifTool."""
    def build(target):
        import random
        from PIL import Image
        Image.frombytes("RGB", NOISE, random.Random(SEED).randbytes(NOISE[0] * NOISE[1] * 3)).save(target, kind)
        _written(target, values, exiftool)
    return made(path, [kind, SEED, list(NOISE), values], build, exiftool, keep=True)


@_item(".png")
def png(path, exiftool, _ffmpeg=None):
    """PIL's PNG given XMP (iTXt), EXIF (eXIf) and IPTC by ExifTool: 24 of the 26 PNGs."""
    return _picture_made(path, exiftool, "PNG", {**CAMERA, **_tagged_values()})


@_item(".png")
def png_xmp_only(path, exiftool, _ffmpeg=None):
    """PIL's PNG given XMP alone: 2 of the 26 PNGs."""
    return _picture_made(path, exiftool, "PNG", _xmp_only(_tagged_values()))


@_item(".tif")
def tiff(path, exiftool, _ffmpeg=None):
    """PIL's TIFF (II) given XMP (tag 700), DateTimeOriginal, XPComment, Rating and Orientation: 17 of 17."""
    values = {**_xmp_only(_tagged_values()), "EXIF:DateTimeOriginal": TAKEN, "EXIF:XPComment": CAPTION,
              "EXIF:Orientation#": 1}
    return _picture_made(path, exiftool, "TIFF", values)


def _clip_made(path, exiftool, ffmpeg, values):
    """The kept clip ffmpeg makes (CLIP, the format `path`'s extension names), given `values` by ExifTool."""
    def build(target):
        from tagpup.core import processes
        done = processes.run([ffmpeg] + CLIP + [target], capture_output=True, timeout=120)
        if done.returncode != 0:
            raise RuntimeError("ffmpeg failed: %s" % done.stderr.decode("utf-8", "replace"))
        if values:
            _written(target, values, exiftool)
    return made(path, ["clip", CLIP, values, identity(exiftool) if values else None], build, ffmpeg, keep=True)


def _clip_values():
    from tagpup.core import fields
    return _xmp_only({**fields.keyword_fields(*fields.expand_tag_fields(list(TAGS))), **fields.title_fields(TITLE),
                      "XMP:DateTimeOriginal": TAKEN})


@_item(".mp4")
def mp4(path, exiftool, ffmpeg):
    """ffmpeg's MP4 given XMP by ExifTool (a uuid box after moov): .mp4 1,745."""
    return _clip_made(path, exiftool, ffmpeg, _clip_values())


@_item(".mov")
def mov(path, exiftool, ffmpeg):
    """ffmpeg's .mov given XMP by ExifTool (inside moov): .mov 1,173."""
    return _clip_made(path, exiftool, ffmpeg, _clip_values())


@_item(".mp4")
def mp4_bare(path, exiftool, ffmpeg):
    """ffmpeg's MP4 with no XMP (the patch appends one). User action: a phone's clip added."""
    return _clip_made(path, exiftool, ffmpeg, None)


@_item(".mp4")
def premiere_mp4(path, exiftool, ffmpeg):
    """`mp4_bare` given PREMIERE in a uuid box at its end, as an MP4's XMP box lies. User action: a clip exported by
    Adobe Premiere (40 live packets of its shape, test_an_adobe_clip_packet_edited_and_read_back)."""
    def lay(data):
        from tagpup.files import containers
        return data + struct.pack(">I", 24 + len(PREMIERE)) + b"uuid" + containers.XMP_UUID + PREMIERE
    return _laid(path, exiftool, "mp4_bare", lay, ffmpeg)


def _clip_headers(n, shape):
    def maker(path, exiftool, _ffmpeg=None):
        """A clip laid out as the captured clips of one shape of tests/fixtures/clip_headers.json (clip_of)."""
        def build(target):
            with open(target, "wb") as handle:
                handle.write(clip_of(shape))
        return made(path, ["laid", "clip_headers", shape], build)
    _item("." + shape[:3], "clip_headers_%d" % n)(maker)


for _n, _shape in enumerate(SHAPES):
    _clip_headers(_n, _shape)


@_item(".jpg")
def cut_short(path, exiftool, _ffmpeg=None):
    """A JPEG cut short (damaged_photos.truncated). User action: a copy that stopped."""
    import damaged_photos
    return made(path, ["laid", "cut_short"], damaged_photos.truncated)


@_item(".jpg")
def no_picture(path, exiftool, _ffmpeg=None):
    """`tagged`'s header with no scan after it: 0. User action: a copy cut short before its picture."""
    return _laid(path, exiftool, "tagged", lambda data: data[:_jpeg_of(data).image_offset])


@_item(".jpg")
def zeros(path, exiftool, _ffmpeg=None):
    """A file of zero bytes named as a photo (damaged_photos.all_zeros). User action: a file zeroed."""
    import damaged_photos
    return made(path, ["laid", "zeros"], damaged_photos.all_zeros)


@_item(".jpg")
def text_named_jpg(path, exiftool, _ffmpeg=None):
    """A text file named .jpg (#46). User action: a file misnamed."""
    def build(target):
        with open(target, "wb") as handle:
            handle.write(b"Not a photo: a note saved under a photo's name.\r\n")
    return made(path, ["laid", "text_named_jpg"], build)
