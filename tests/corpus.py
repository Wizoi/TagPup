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


def _written(target, values, exiftool):
    from tagpup.files.exiftool_session import ExifToolSession
    with ExifToolSession(executable=exiftool) as et:
        et.set_tags([target], tags=values, params=["-overwrite_original"])


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
