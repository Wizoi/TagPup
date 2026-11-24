"""ExifTool's answers kept for the tests, and given back without running it: record and replay at ExifToolSession.

ExifTool is not TagPup's: once its answer to given arguments and given file bytes is known, running it again tells the
suite nothing (owner, 2026-11-24; docs/work/3375.md). tools/run_tests.py asks for it by default (TAGPUP_TEST_EXIFTOOL=replay
for each file's process; set it by hand for a file run alone) and the tests package then sets a Keeper as
tagpup.files.exiftool_session.REPLAY, through which every command goes:

* a hit -- a command kept before -- is answered from what was kept: its stdout, stderr and exit status, and the bytes of
  each file it wrote put where it wrote them (the new file of `-o`, a file rewritten in place, an `_original` backup).
  No ExifTool process starts for it: a session starts its process with the first command it cannot answer.
* a miss runs the real ExifTool, and keeps its answer unless the answer depends on something the key leaves out.
tools/run_tests.py --real-exiftool (TAGPUP_TEST_EXIFTOOL=record) runs every command for real and keeps each answer: the
gate run before a release, which refreshes what is kept. A file run alone with plain `python -m unittest` sets nothing,
and every command is ExifTool's. A file in STAYS_REAL is never given a keeper by the runner.

The key: the command's arguments, each file named in them by its name, the sha256 of its bytes, whether it may be
written and whether ExifTool's scratch or backup of it is there (SCRATCHES: a scratch in the way fails a write), the
target of `-o` by its name and whether it is there, the session's common arguments, the content of its
`-config` file, its encoding, the ExifTool installed (its version, its executable's and library's size and time) and
pyexiftool's version. The `-execute N` framing is added after (_DrainingExifTool._sent), so it is not in it. Paths are
not either: a test's folder is new each run, so each path ExifTool spells in its answer is kept as a mark and given back
as the command's own path. A file's name is kept (an answer may spell it alone, as FileName): a file named by the
process that made it (a pid, a time) is a miss in every run, as tests/corpus.py's part file was.

Never kept, so a miss each time (`why` in the counts): raw bytes asked; a folder, an argument file (`-@`) or a file
read or written by name in an option (-w, -srcfile, -csv=, `<=`); a file that cannot be read; an answer that names a
folder or carries a file's dates or attributes (the file's times, not its bytes); a run during which a file other than
its outputs appeared or one went, in a folder it names; a failed run (a timeout). Hidden inputs, checked and not in the
key: a file open in another program (ExifTool reads it, as the key does: only tagpup's move over it fails); the time
zone (TagPup reads with -n: dates as stored); an ExifTool config in the user's profile (there is none; one would
change answers no key holds).

Where: one folder per ExifTool installed, `<version>-<digest>`, under %LOCALAPPDATA%\\TagPupTestCache\\exiftool (the
machine's: every worktree and the checkout share it, and nothing in it is a checkout's; TAGPUP_TEST_EXIFTOOL_CACHE
names another). An entry is one file named by its key, written under a part name and os.replace'd while its stripe's
lock is held (tagpup.core.byte_lock): two processes asking for one key both run it, and the later's entry replaces the
earlier's whole; a reader never sees half of one, and a part a kill left is never read; a folder deleted mid-run is
made again by the next entry. A hit marks its entry used (its time), and the runner trims the folder to CAP after a
run, the entries used longest ago first (trim).

The DocumentID a photo is given (tagpup.files.identity.mint_document_id) is a uuid4 in the arguments of its write, so a
photo given one would miss at that write and at every later read of it. Under a keeper it is minted from a generator
seeded per process: a file's run mints the same ids in the same order each run.
"""
import atexit
import collections
import hashlib
import json
import os
import random
import re
import shutil
import sys
import threading
import time
import uuid

import exiftool

from tagpup.core import byte_lock
from tagpup.files import exiftool_session, identity

#: What a test process does with ExifTool's answers: REPLAY or RECORD; anything else, nothing.
ENV = "TAGPUP_TEST_EXIFTOOL"
REPLAY, RECORD = "replay", "record"
#: Another folder for the kept answers than the machine's (cache_root).
CACHE_ENV = "TAGPUP_TEST_EXIFTOOL_CACHE"
#: A file each test process adds a line of its counts to as it ends (tools/run_tests.py sums them).
COUNTS_ENV = "TAGPUP_TEST_EXIFTOOL_COUNTS"

#: Test files the runner never gives a keeper, so every command is ExifTool's: their claim is ExifTool's own behaviour
#: or its process's, which a kept answer would stand in for. Judged replayable (their claim is TagPup's, ExifTool's
#: answer an input keyed by its version): test_a_file_holds_what_exiftool_kept (the journal records what the file
#: holds), test_bulk_edits_real_exiftool (a bulk edit end to end; "real" against test_bulk_edits' stand-in).
STAYS_REAL = {
    "test_exiftool_session": "the session's process: its framing, both pipes drained, a deadline, a kill, a restart",
    "test_exiftool_encoding": "the bytes a command is sent and answered in (CSTR, the file-name charset), under the key",
    "test_exiftool_says_why": "what ExifTool says when a write fails: its own sentences, its scratch in the way",
    "test_bulk_edits_stalled_exiftool": "a command that does not answer: the deadline and what the batch does after",
    "test_minor_errors_in_a_journaled_write": "what ExifTool does with XMP it objects to: -m takes it, a packet cut short not",
    "test_the_two_writers_compared_on_copies": "ExifTool's writes compared with TagPup's own writer: ExifTool's half is the claim",
    "test_a_tag_added_touches_its_file_no_more_than_it_must": "it counts each os.stat of the file and each start of ExifTool "
    "(a call of ExifToolSession.run while not running): a keeper looks at the file to key it and starts ExifTool lazily",
    "test_exiftool_answers_kept_for_tests": "the keeper itself: it gives each test its own keeper, cache and real runs",
}

#: Locks the writers of entries take turns on: one of these per key's first two hex digits, folded.
STRIPES = 64
#: Seconds a writer waits for its stripe before it leaves the entry unkept.
WAIT = 30.0
#: How a path is marked in a kept answer: the argument's place, then how ExifTool spelled it (SPELLINGS).
MARK = "\x00F%d%s\x00"
MARKED = re.compile("\x00F(\\d+)([afjk])\x00")
#: A path as given (a), with forward slashes (f), and either as JSON writes it inside a string (j, k).
SPELLINGS = {
    "a": lambda p: p,
    "f": lambda p: p.replace("\\", "/"),
    "j": lambda p: json.dumps(p, ensure_ascii=False)[1:-1],
    "k": lambda p: json.dumps(p.replace("\\", "/"), ensure_ascii=False)[1:-1],
}
#: An answer that carries a file's own dates or attributes, which its bytes do not decide.
STAT_TAGS = re.compile(r"File(?:Modify|Access|Create|InodeChange)Date|FilePermissions|FileAttributes")
#: Options naming a file whose bytes the key does not hold, or one ExifTool writes beside its outputs.
NOT_KEPT_OPTIONS = frozenset({"-@", "-srcfile", "-w", "-w!", "-w+", "-W", "-W!", "-W+", "-textout", "-tagout"})
#: What a seeded process's ids are minted from.
SEED = "tagpup-tests"
#: What ExifTool leaves beside a file it writes, each a hidden input of a write: its scratch (a write fails while one is
#: there) and its backup.
SCRATCHES = ("_exiftool_tmp", "_original")
#: Bytes the kept answers may take on this machine before trim removes the entries used longest ago.
CAP = 1024 * 1024 * 1024
#: Seconds after which a part file is a writer's that was killed, removed by trim.
STALE_PART = 3600


def cache_root():
    """The folder the kept answers are in: CACHE_ENV's, else the machine's."""
    return os.environ.get(CACHE_ENV) or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "TagPupTestCache", "exiftool")


_versions = {}


def tool_of(executable):
    """(version, digest) of the ExifTool at `executable`: its version as its library says it, and the digest of its
    executable's, script's, library's and any config's size and time, and pyexiftool's version. The Windows ExifTool is a
    small launcher beside `exiftool_files`: an upgrade may leave the launcher as it was, so its library is looked at."""
    path = shutil.which(executable) or executable
    beside = os.path.join(os.path.dirname(os.path.abspath(path)), "exiftool_files")
    looked = [path, os.path.join(beside, "exiftool.pl"), os.path.join(beside, "lib", "Image", "ExifTool.pm"),
              os.path.join(beside, ".ExifTool_config"), os.path.join(os.path.dirname(os.path.abspath(path)),
                                                                     ".ExifTool_config")]
    stamps = []
    for each in looked:
        try:
            held = os.stat(each)
            stamps.append([os.path.normcase(os.path.abspath(each)), held.st_size, held.st_mtime_ns])
        except OSError:
            stamps.append([os.path.normcase(os.path.abspath(each)), None])
    known = _versions.get(path)
    if known is None or known[0] != stamps:
        known = (stamps, _version_said(looked[2]))
        _versions[path] = known
    digest = hashlib.sha256(json.dumps([stamps, exiftool.__version__]).encode("utf-8")).hexdigest()[:16]
    return known[1], digest


def _version_said(library):
    try:
        with open(library, encoding="latin-1") as handle:
            for line in handle:
                found = re.match(r"\$VERSION\s*=\s*'([^']+)'", line)
                if found:
                    return found.group(1)
    except OSError:
        pass
    return "unknown"


def _digest(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def arguments(params):
    """(the key's arguments, [(place, path, kind, digest)]) of a command, kind "file", "target" (of -o) or "missing";
    or (None, why) when its answer cannot be kept."""
    keyed, named, target_next = [], [], False
    for place, param in enumerate(params):
        if isinstance(param, bytes):
            keyed.append(["bytes", param.hex()])
            continue
        if target_next:
            target_next = False
            if os.path.isdir(param):
                return None, "-o names a folder"
            digest = _digest(param) if os.path.exists(param) else None
            keyed.append(["target", os.path.basename(param), digest])
            named.append((place, param, "target", digest))
            continue
        if param.lower() in ("-o", "-out"):
            target_next = True
            keyed.append(param)
            continue
        if param in NOT_KEPT_OPTIONS or param.lower().startswith(("-csv=", "-json=")) or "<=" in param:
            return None, "an option reading or writing a file by name"
        if not param or param.startswith("-"):
            keyed.append(param)
            continue
        if os.path.isdir(param):
            return None, "a folder named"
        if os.path.isfile(param):
            digest = _digest(param)
            if digest is None:
                return None, "a file that could not be read"
            keyed.append(["file", os.path.basename(param), digest, os.access(param, os.W_OK),
                          [os.path.exists(param + suffix) for suffix in SCRATCHES]])
            named.append((place, param, "file", digest))
            continue
        if ("\\" in param or "/" in param) and os.path.isdir(os.path.dirname(os.path.abspath(param))):
            keyed.append(["missing", os.path.basename(param)])
            named.append((place, param, "missing", None))
            continue
        keyed.append(param)
    return keyed, named


def key_of(session, params):
    """(the folder of the session's ExifTool, the command's key, its named files), or (None, why, None)."""
    keyed, named = arguments(params)
    if keyed is None:
        return None, named, None
    version, tool = tool_of(session._executable)
    config = session._config_file
    text = json.dumps({"args": keyed, "common": [str(a) for a in session._common_args or ()],
                       "config": (_digest(config) or config) if config else config, "encoding": session._encoding,
                       "tool": tool}, sort_keys=True, separators=(",", ":"))
    folder = os.path.join(cache_root(), "%s-%s" % (version, tool))
    return folder, hashlib.sha256(text.encode("utf-8")).hexdigest(), named


def _marked(text, named):
    """`text` with each named path marked (MARK), longest spelling first; None when a folder of one is left in it."""
    if not text:
        return text
    if "\x00" in text:
        return None
    spelled = {}
    for place, path, _kind, _digest in named:
        for form, spell in SPELLINGS.items():
            spelled.setdefault(spell(path), MARK % (place, form))
    for path in sorted(spelled, key=len, reverse=True):
        text = text.replace(path, spelled[path])
    folded = text.lower()
    for _place, path, _kind, _digest in named:
        folder = os.path.dirname(os.path.abspath(path))
        if len(folder) > 3 and any(spell(folder).lower() in folded for spell in SPELLINGS.values()):
            return None
    return text


def _unmarked(text, paths):
    return MARKED.sub(lambda m: SPELLINGS[m.group(2)](paths[int(m.group(1))]), text) if text else text


def _listings(named):
    """{folder: its names} of each folder a command names."""
    folders = {os.path.dirname(os.path.abspath(path)) for _place, path, _kind, _digest in named}
    found = {}
    for folder in folders:
        try:
            found[folder] = set(os.listdir(folder))
        except OSError:
            found[folder] = None
    return found


def _outputs(named, before):
    """[(place, suffix, bytes)] a run wrote, from its named files and their folders before it; or a str: why not."""
    after = _listings(named)
    expected = {}
    outputs = []
    for place, path, kind, digest in named:
        folder, name = os.path.split(os.path.abspath(path))
        if kind == "file":
            now = _digest(path)
            if now is None:
                return "a file it was given went or could not be read after"
            if now != digest:
                outputs.append((place, "", path))
            expected[(folder, name + "_original")] = (place, "_original")
        elif kind == "target" and digest is None and os.path.isfile(path):
            outputs.append((place, "", path))
            expected[(folder, name)] = None
    for folder, names in before.items():
        if names is None or after.get(folder) is None:
            return "a folder it names could not be listed"
        if names - after[folder]:
            return "a file went from a folder it names"
        for name in after[folder] - names:
            if (folder, name) not in expected:
                return "a file it did not name appeared beside its files"
            if expected[(folder, name)] is not None:
                place, suffix = expected[(folder, name)]
                outputs.append((place, suffix, os.path.join(folder, name)))
    written = []
    for place, suffix, path in outputs:
        try:
            with open(path, "rb") as handle:
                written.append((place, suffix, handle.read()))
        except OSError:
            return "an output could not be read"
    return written


def read_entry(path):
    """(header, [(place, suffix, bytes)]) of a whole entry, or None (none there, or not whole)."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    head, newline, rest = data.partition(b"\n")
    try:
        header = json.loads(head) if newline else None
    except ValueError:
        return None
    if not isinstance(header, dict) or len(rest) != sum(length for _p, _s, length in header.get("outputs", [])):
        return None
    blobs, at = [], 0
    for place, suffix, length in header["outputs"]:
        blobs.append((place, suffix, rest[at:at + length]))
        at += length
    return header, blobs


def write_entry(folder, key, header, blobs):
    """Keep an entry: a part file os.replace'd into place, its stripe's lock held. Returns why not, or None."""
    entry = os.path.join(folder, key + ".entry")
    turn = byte_lock.Lock(os.path.join(folder, "locks", "%02d.lock" % (int(key[:2], 16) % STRIPES)))
    try:
        if not turn.acquire(wait=WAIT):
            return "its stripe stayed taken"
    except OSError:
        return "the folder could not be made"
    part = "%s.part-%d-%d" % (entry, os.getpid(), threading.get_ident())
    try:
        header = dict(header, outputs=[[place, suffix, len(data)] for place, suffix, data in blobs])
        with open(part, "wb") as handle:
            handle.write(json.dumps(header).encode("utf-8") + b"\n")
            for _place, _suffix, data in blobs:
                handle.write(data)
        os.replace(part, entry)
        return None
    except OSError:
        if os.path.exists(part):
            os.remove(part)
        return "the entry could not be written"
    finally:
        turn.release()


def _placed(blobs, named):
    """Put a hit's outputs where the run put them: new files first, files rewritten in place last (as ExifTool, by a
    rename over them). False, and the new files removed, when one cannot be."""
    paths = {place: (path, kind) for place, path, kind, _digest in named}
    made = []
    try:
        for place, suffix, data in sorted(blobs, key=lambda b: paths[b[0]][1] == "file" and not b[1]):
            path, kind = paths[place]
            if kind == "file" and not suffix:
                part = path + ".replay-part"
                with open(part, "wb") as handle:
                    handle.write(data)
                os.replace(part, path)
            else:
                with open(path + suffix, "xb") as handle:
                    made.append(path + suffix)
                    handle.write(data)
        return True
    except (OSError, KeyError):
        for path in made:
            if os.path.exists(path):
                os.remove(path)
        return False


def _used(entry):
    """Mark an entry used now, for trim: its modified time."""
    try:
        os.utime(entry, None)
    except OSError:
        pass   # replaced or trimmed by another process since it was read: its answer was given all the same


def trim(cap=CAP):
    """Keep the kept answers within `cap` bytes, removing the entries used longest ago (every ExifTool's: an old
    version's are never used again) and any part file older than STALE_PART. Returns (entries, bytes) left and removed.
    An entry another process is reading cannot be removed on Windows and is left; one removed while another process
    was about to read it is a miss there."""
    now, entries, removed = time.time(), [], [0, 0]
    for folder, _dirs, names in os.walk(cache_root()):
        for name in names:
            path = os.path.join(folder, name)
            try:
                held = os.stat(path)
            except OSError:
                continue
            if name.endswith(".entry"):
                entries.append((held.st_mtime, held.st_size, path))
            elif ".entry.part-" in name and now - held.st_mtime > STALE_PART and _removed(path):
                removed[0] += 1
                removed[1] += held.st_size
    total, left = sum(size for _when, size, _path in entries), len(entries)
    for _when, size, path in sorted(entries):
        if total <= cap:
            break
        if _removed(path):
            total, left = total - size, left - 1
            removed[0] += 1
            removed[1] += size
    return (left, total), tuple(removed)


def _removed(path):
    try:
        os.remove(path)
        return True
    except OSError:
        return False


class Keeper:
    """ExifTool's answers for one test process: answered from the kept ones (REPLAY) or always run and kept (RECORD)."""

    def __init__(self, mode):
        self.mode = mode
        self.counts = collections.Counter()
        self.why = collections.Counter()
        self._lock = threading.Lock()

    def _count(self, *names, why=None):
        with self._lock:
            self.counts.update(names)
            if why:
                self.why[why] += 1

    def execute(self, session, params, raw_bytes, sent):
        """The answer to `params`: kept, or `sent()`'s (the real ExifTool's), kept when it can be."""
        self._count("commands")
        if raw_bytes:
            return self._ran(session, sent, "raw bytes asked")
        folder, key, named = key_of(session, params)
        if folder is None:
            return self._ran(session, sent, key)
        if self.mode == REPLAY:
            entry = os.path.join(folder, key + ".entry")
            kept = read_entry(entry)
            if kept is not None:
                header, blobs = kept
                if _placed(blobs, named):
                    _used(entry)
                    paths = {place: path for place, path, _kind, _digest in named}
                    session._last_stdout = _unmarked(header["stdout"], paths)
                    session._last_stderr = _unmarked(header["stderr"], paths)
                    session._last_status = header["status"]
                    self._count("hits")
                    return session._last_stdout
                self._count(why="a kept output could not be placed")
        before = _listings(named)
        answer = self._ran(session, sent, "not kept before" if self.mode == REPLAY else None)
        stdout, stderr = _marked(session._last_stdout, named), _marked(session._last_stderr, named)
        outputs = _outputs(named, before)
        if stdout is None or stderr is None:
            why = "its answer names a folder"
        elif STAT_TAGS.search(stdout):
            why = "its answer holds a file's dates"
        elif isinstance(outputs, str):
            why = outputs
        else:
            why = write_entry(folder, key, {"stdout": stdout, "stderr": stderr, "status": session._last_status,
                                            "version": folder}, outputs)
        self._count("kept" if why is None else "not kept", why=why and "not kept: " + why)
        return answer

    def _ran(self, session, sent, why):
        starts = not session.running
        self._count("misses", why=why)
        answer = sent()
        if starts and session.running:
            self._count("starts")
        return answer

    def report(self):
        """Add this process's counts to the file COUNTS_ENV names, a line of JSON."""
        where = os.environ.get(COUNTS_ENV)
        if where and self.counts["commands"]:
            line = {"argv": sys.argv[1:], "counts": dict(self.counts), "why": dict(self.why)}
            with open(where, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(line) + "\n")


def seeded_mint(seed=SEED):
    """identity.mint_document_id, from a generator seeded with `seed`: the same ids in the same order each run."""
    generator, lock = random.Random(seed), threading.Lock()

    def mint_document_id():
        with lock:
            bits = generator.getrandbits(128)
        return "xmp.did:%s" % uuid.UUID(int=bits, version=4)
    return mint_document_id


def install(mode=None):
    """Give this test process a Keeper when `mode` (else ENV's) is REPLAY or RECORD; returns it, or None."""
    mode = mode or os.environ.get(ENV)
    if mode not in (REPLAY, RECORD):
        return None
    keeper = Keeper(mode)
    exiftool_session.REPLAY = keeper
    identity.mint_document_id = seeded_mint()
    atexit.register(keeper.report)
    return keeper
