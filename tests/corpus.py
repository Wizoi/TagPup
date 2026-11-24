"""Photos, clips and libraries made once per test run by the real writer, and copied for each test.

A test's photo is made by the code that makes the real thing (CLAUDE.md): PIL saves it and ExifTool writes its fields, a
clip is ffmpeg's, a library is schema.ensure's. Made per test, that was about half of the suite's time (a JPEG given its
fields by a fresh ExifTool is 0.4 s, a copy of it 1 ms; docs/plans/fast-trustworthy-test-suite.md, cut 2). So each item
is made once, into the run's corpus folder, under a name that is the digest of what it was made with -- the recipe the
test gives (its content: size, colour, fields, the command's arguments) and the writer's executable (its path, size and
time: a new ExifTool or ffmpeg is a new key) -- and each test is given a copy of it at the path it names.

    corpus.jpeg(path, values, exiftool)            # the JPEG FilesCase.make made, at `path`
    corpus.made(path, recipe, build, tool)         # any file: build(target) makes it with the real writer
    corpus.library(path)                           # a new library, as schema.ensure makes one

What a copy is: the bytes of the item, in a new file (shutil.copyfile: never a link), so a write to it changes neither the
corpus nor another test's copy. Its modified time is the copy's, now, and its file id a new one, as a file just made
has; nothing in tagpup reads a file id, and a test that wants an older time sets it after, as it did. Two copies of one
recipe hold the same bytes, as two files made by one recipe did: what makes the photos of a test distinct is still its
recipe (a tag, a date), never the copy.

The run's corpus folder: tools/run_tests.py makes one for the run (TAGPUP_TEST_CORPUS, inherited by every file's
process) and removes it at the end; a test file run on its own gets one for its process, removed as the process ends.
Never under the owner's home or library.

Two processes asking for one item at once: the maker takes a turn on the item's lock (tagpup.core.byte_lock), looks
again, and makes it under a name of its own (`<key>-part-<pid><ext>`) that it renames into place (os.replace), so the
item's name only ever holds a whole item. A run killed part-way leaves at most a part file, never read, and a lock the
system lets go of. A folder deleted mid-run is made again, and its items with it, by the next test that asks. An item
is read-only (its read-only attribute, set before it takes its name): a test that opens it for writing fails at once.
"""
import atexit
import contextlib
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

from tagpup.core import byte_lock  # noqa: E402

#: The run's corpus folder, named by tools/run_tests.py for every file's process.
ENV = "TAGPUP_TEST_CORPUS"
#: How long a process waits for another making the same item (an ffmpeg clip is about a second; a loaded machine, more).
WAIT = 120.0

_own = None


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


def key(recipe, tool=None):
    """The digest of what an item is made with: `recipe` (JSON: the content and the arguments) and `tool`'s executable,
    by its path, size and modified time."""
    found = None
    if tool:
        held = os.stat(tool)
        found = [os.path.normcase(os.path.abspath(tool)), held.st_size, held.st_mtime_ns]
    text = json.dumps([recipe, found], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def item(recipe, tool=None, ext=""):
    """Where the item of `recipe` is (or will be) in the run's corpus folder."""
    return os.path.join(folder(), key(recipe, tool) + ext)


def made(path, recipe, build, tool=None):
    """A copy at `path` of the item `build(target)` makes -- with the real writer, at `target`, a new file of `path`'s
    extension -- made once per run for `recipe` and `tool`. Returns `path`."""
    ext = os.path.splitext(path)[1]
    source = item(recipe, tool, ext)
    if not os.path.exists(source):
        _make(source, build)
    try:
        shutil.copyfile(source, path)
    except FileNotFoundError:
        if os.path.exists(os.path.dirname(os.path.abspath(path))) and not os.path.exists(source):
            _make(source, build)   # the corpus folder was deleted meanwhile: made again
            shutil.copyfile(source, path)
        else:
            raise
    return path


def _make(source, build):
    os.makedirs(os.path.dirname(source), exist_ok=True)
    turn = byte_lock.Lock(source + ".lock")
    if not turn.acquire(wait=WAIT):
        raise RuntimeError("another test process has been making %s for %d s" % (os.path.basename(source), WAIT))
    try:
        if os.path.exists(source):
            return   # another process made it while this one waited
        stem, ext = os.path.splitext(source)
        part = "%s-part-%d%s" % (stem, os.getpid(), ext)
        _discard(part)   # left by a process of this number killed part-way
        try:
            build(part)
            os.chmod(part, stat.S_IREAD)
            os.replace(part, source)
        except BaseException:
            _discard(part)
            raise
    finally:
        turn.release()


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
        from tagpup.files.exiftool_session import ExifToolSession
        Image.new("RGB", tuple(size), tuple(colour)).save(target, "JPEG")
        with ExifToolSession(executable=exiftool) as et:
            et.set_tags([target], tags=values, params=["-overwrite_original"])
    return made(path, ["jpeg", list(size), list(colour), values], build, exiftool)


def library(path):
    """A new library at `path`, as schema.ensure makes one: a copy of one made once per run, checkpointed into a file of
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
