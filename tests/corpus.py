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
MAKERS = (os.path.abspath(__file__), os.path.join(WORKSPACE_DIR, "tests", "damaged_photos.py"),
          os.path.join(WORKSPACE_DIR, "tagpup", "files", "exiftool_session.py"))

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
