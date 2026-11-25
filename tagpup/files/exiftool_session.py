"""An ExifTool session that cannot hang on its own error output.

pyexiftool (0.5.x) talks to one long-running `exiftool -stay_open` process over three
pipes, and after sending a command it reads **stdout to the end first, and only then
stderr**. ExifTool writes one warning or error line per file to stderr as it goes.
On Windows an anonymous pipe holds about 4 KB, so once a single command has produced
that much -- around 35 lines of "Warning: IPTCDigest is not current ... - <path>" or
"Error: File not found - <path>" -- ExifTool blocks writing stderr, never reaches
the end of its stdout, and pyexiftool waits for that end forever. At 0% CPU, for as
long as nobody notices: two maintenance scripts sat there for two days.

ExifToolSession is pyexiftool's ExifToolHelper with that one method replaced:

* stdout and stderr are read at the same time, so neither can fill up;
* every command has a deadline (`timeout`, seconds). When it passes, the ExifTool
  process is killed and ExifToolTimeout is raised naming the files the command was
  about, instead of the caller waiting for days. The next call starts a fresh
  process, as ExifToolHelper always does when it is not running.

It also speaks UTF-8 both ways. pyexiftool otherwise encodes arguments and decodes
answers with the locale's code page (cp1252 here) while ExifTool reads and writes
UTF-8: a keyword "Zoë" reached the file as "Zo?", a caption the file held as "ü" came
back as "Ã¼", and a photo named with a character outside cp1252 could not be passed
at all. `-charset filename=utf8` tells ExifTool on Windows that file names arrive,
and are to be reported, in UTF-8 too.

A command ExifTool fails raises ExifToolFailed, which says what ExifTool said. pyexiftool's
error says only "execute returned a non-zero exit status: 1", and that was all a page and
the log had of "Error creating file: <file>_exiftool_tmp" when two writes met. The
message names no photo -- a file's name can hold a caption, and so a name -- and the
whole of what ExifTool said is logged.

pyexiftool sends ExifTool one argument a line, so a value holding a line break -- a caption another program wrote over
two lines -- was written as its first line, and its second read as a file name ("File not found"). Such an argument is
sent as a C string (`#[CSTR]`, ExifTool 11.08 or later: see CSTR); every other goes as it is.

Its ExifTool ends with the TagPup process that started it, however that ends (processes.end_with_this_process): a
writer killed hard left its ExifTool running for weeks (#2660).

A test run may give every command to a keeper of ExifTool's answers (REPLAY, set only by the tests package): it answers
a command it has seen for the same bytes and arguments from what it kept, and the process starts only for the first one
it has not.

Everything else -- get_tags, set_tags, check_execute, auto-start -- is ExifToolHelper's.
"""
import logging
import os
import random
import re
import threading
import time

import exiftool
from exiftool.exceptions import ExifToolExecuteError, ExifToolNotRunning, ExifToolVersionError

from tagpup.core import processes

__all__ = ["ExifToolSession", "ExifToolTimeout", "ExifToolFailed", "StaysDead", "DEFAULT_TIMEOUT"]

logger = logging.getLogger(__name__)

#: Tests only: the keeper of ExifTool's answers (tests/exiftool_replay.py) the tests package sets when a run asks for it;
#: None in every product start, and nothing in tagpup, scripts or a launcher sets it
#: (tests/test_exiftool_answers_kept_for_tests.py). When set, every command goes through it (_DrainingExifTool.execute),
#: and when it is `lazy` a session starts its process for the first command it cannot answer, not at `with`
#: (ExifToolSession.run).
REPLAY = None

#: Seconds one ExifTool command may take. A batch of a hundred photos reads in a few
#: seconds; this is generous enough for a slow disk and still ends a stall the same
#: afternoon rather than two days later.
DEFAULT_TIMEOUT = 300

#: What ExifTool reads and writes; never the locale's code page.
ENCODING = "utf-8"

#: pyexiftool's own defaults: group names on every tag, numeric values.
DEFAULT_COMMON_ARGS = ("-G", "-n")

#: The prefix that has ExifTool read the rest of an argument's line as a C string, in which a line break is an escape:
#: ExifTool 11.08 or later (added in 11.07, made to work with -stay_open in 11.08, its escapes handled as now since 12.26;
#: the suite runs it on 13.59). An older one takes the line for a comment and leaves the argument out, which the
#: journal's read back finds; none of the live libraries names an ExifTool of its own (`paths.exiftool`, 2026-10-25). It
#: goes before the argument's dash: `#[CSTR]-XMP:Subject=x`.
CSTR = "#[CSTR]"

_BACKSLASH, _CR, _LF = chr(92), chr(13), chr(10)

#: What ExifTool's C string gives a backslash of its own, however it is sent: "$" comes back "\$", "@" "\@" (13.59).
NOT_IN_CSTR = ("$", "@")


def _argument(text):
    """One argument as a line of ExifTool's -stay_open input. As it is, unless it holds a line break: then a C string
    (CSTR), each backslash doubled and each CR and LF written as an escape. Only then, since a C string adds a backslash
    to a "$" or "@" (NOT_IN_CSTR), and 7 captions of the live libraries hold a "$" (2026-10-25). A line break beside
    either cannot be sent either way, and is refused before anything is: no live caption holds both."""
    if _CR not in text and _LF not in text:
        return text
    if any(c in text for c in NOT_IN_CSTR):
        raise ValueError("A value holding a line break and a $ or @ cannot be given to ExifTool; nothing was written")
    return CSTR + text.replace(_BACKSLASH, _BACKSLASH * 2).replace(_CR, _BACKSLASH + "r").replace(_LF, _BACKSLASH + "n")


class ExifToolTimeout(RuntimeError):
    """ExifTool did not answer one command in time; its process has been killed. Its
    message names no photo, as ExifToolFailed's names none: it reaches the journal's
    conflict text, which History shows without revealing paths."""


#: Lines of what ExifTool said that go in ExifToolFailed's message; the rest is logged.
SAID_LINES = 2


#: What a write names the new file ExifTool writes of a photo, its scratch file: the photo's path with this after it.
#: TagPup names it so (`-o`, tagpup.files.field_values.write), as ExifTool named its own temporary file before the
#: picture check (exiftool.pl: `"${file}_exiftool_tmp"`, 13.59), so one left by an older write is the same file. A write
#: that is interrupted (a share that drops, a crash) leaves it beside the photo. scratch_of is the one place that names it.
SCRATCH_SUFFIX = "_exiftool_tmp"

#: What ExifTool says when the scratch file of a photo is there already (`-o` to a file that exists: "Error: '<file>'
#: already exists - <file>", 13.59), and what the owner is told to do. A failed write leaves one (a photo open in another
#: program or read-only when the new file is moved over it, an interrupted write); file_changes removes only an
#: interrupted write's, stale, beside the photo as the write saw it (#2180). One an older ExifTool left after a failure
#: that deleted the original is the photo's only copy (#1410, #1411): hence the second half of TEMPORARY_SENTENCE.
TEMPORARY_IN_THE_WAY = "' already exists"
TEMPORARY_SENTENCE = (". A file named as the photo with _exiftool_tmp after it, left beside it by a write that failed, "
                      "is in the way; the server's log names both. If the photo is there and opens, delete that file; "
                      "if the photo is gone, that file is its only copy: rename it back. Then try again.")
#: What the owner is told instead when file_changes leaves the scratch file because it was written within the deadline of
#: a command: it may be another write's of the photo, under way, and deleting it could lose that write (#2180).
LATELY_SENTENCE = (". A file named as the photo with _exiftool_tmp after it, written in the last %d minutes, is in the "
                   "way; another write of the photo may be under way. Leave it, and try again after %d minutes."
                   % (DEFAULT_TIMEOUT // 60, DEFAULT_TIMEOUT // 60))


def scratch_of(photo_path):
    """The scratch file ExifTool writes `photo_path` through (SCRATCH_SUFFIX)."""
    return photo_path + SCRATCH_SUFFIX


def scratch_in_the_way(error):
    """Did ExifTool refuse a write because the photo's scratch file is there already (TEMPORARY_IN_THE_WAY)?"""
    return isinstance(error, ExifToolFailed) and TEMPORARY_IN_THE_WAY in str(error)


def written_lately(error):
    """`error`, a refusal for a scratch file in the way (scratch_in_the_way), telling the owner to leave the file and try
    again later (LATELY_SENTENCE) rather than what to do with a failed write's (TEMPORARY_SENTENCE)."""
    error.args = (str(error).replace(TEMPORARY_SENTENCE, LATELY_SENTENCE),) + error.args[1:]
    return error


class ExifToolFailed(ExifToolExecuteError):
    """ExifTool ran a command and failed it: pyexiftool's error, its message saying what
    ExifTool said (its first SAID_LINES error lines, each photo named "<file>"), and, when
    its temporary file is in the way, what to do (TEMPORARY_SENTENCE)."""

    def __init__(self, exit_status, cmd_stdout, cmd_stderr, params):
        super().__init__(exit_status, cmd_stdout, cmd_stderr, params)
        said = said_briefly(cmd_stderr, params)
        if said:
            more = TEMPORARY_SENTENCE if TEMPORARY_IN_THE_WAY in said else ""
            self.args = ("ExifTool: %s (exit status %s)%s" % (said, exit_status, more),) + self.args[1:]

    def __str__(self):
        return str(self.args[0])


def _text(value):
    if isinstance(value, bytes):
        return value.decode(ENCODING, "replace")
    return str(value or "")


def _said(stderr):
    """The lines of ExifTool's `stderr`, errors before warnings, blank ones dropped."""
    lines = [line.strip() for line in _text(stderr).splitlines() if line.strip()]
    return [line for line in lines if not line.startswith("Warning")] + \
        [line for line in lines if line.startswith("Warning")]


def said_briefly(stderr, params=()):
    """ExifTool's first SAID_LINES lines of `stderr` (errors before warnings), each file
    of the command, `params`, named "<file>": ExifTool spells a path with forward
    slashes, whichever it was given."""
    return unnamed("; ".join(_said(stderr)[:SAID_LINES]), params)


def _files(params):
    return [_text(p) for p in params if _text(p) and not _text(p).startswith("-")]


def unnamed(text, params=()):
    """`text` with each file of the command, `params`, named "<file>": as given or with
    forward slashes, as ExifTool spells a path whichever it was given, and then by its
    name alone."""
    for name in _files(params):
        if len(name) < 3:
            continue
        pattern = "".join("[\\\\/]" if c in "\\/" else re.escape(c) for c in name)
        text = re.sub(pattern, "<file>", text, flags=re.IGNORECASE)
        leaf = re.split(r"[\\/]", name)[-1]
        if len(leaf) >= 3:
            text = re.sub(re.escape(leaf), "<file>", text, flags=re.IGNORECASE)
    return text


def _counted(params):
    """How many files a command was about, for a message that names none."""
    n = len(_files(params))
    return "no files" if not n else "1 file" if n == 1 else "%d files" % n


def _read_until(fd, sentinel, sink):
    """Read `fd` until its output ends with `sentinel` (or the pipe closes).

    Unlike pyexiftool's reader this stops at end-of-file: a process that died
    mid-command would otherwise have it spin forever appending empty reads.
    """
    chunks = []
    tail = b""
    keep = len(sentinel) + 8
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            chunk = b""
        if not chunk:
            sink["eof"] = True
            break
        chunks.append(chunk)
        tail = (tail + chunk)[-keep:]
        if tail.strip().endswith(sentinel):
            break
    sink["data"] = b"".join(chunks)


def _describe(params):
    """The files a command was about, briefly, for an error message."""
    names = []
    for p in params:
        text = p.decode("utf-8", "replace") if isinstance(p, bytes) else str(p)
        if text and not text.startswith("-"):
            names.append(text)
    if not names:
        return "no files"
    if len(names) == 1:
        return names[0]
    return "%d files, %s ... %s" % (len(names), names[0], names[-1])


class _DrainingExifTool(exiftool.ExifTool):
    """ExifTool.execute, reading both output pipes at once and under a deadline."""

    timeout = DEFAULT_TIMEOUT
    #: Under REPLAY: a start under way, whose own -ver is ExifTool's (never the keeper's); under a lazy one, the start
    #: a command asked for (_sent), the one start run() makes then.
    _starting = False

    def execute(self, *params, raw_bytes=False):
        if REPLAY is not None and not self._starting:   # a start's own -ver is ExifTool's, never kept
            return REPLAY.execute(self, params, raw_bytes, lambda: self._sent(params, raw_bytes))
        return self._sent(params, raw_bytes)

    def _sent(self, params, raw_bytes):
        """The command sent to the ExifTool process, and its answer read."""
        if not self.running and REPLAY is not None and REPLAY.lazy:
            self._starting = True
            try:
                self.run()
            finally:
                self._starting = False
        if not self.running:
            raise ExifToolNotRunning("Cannot execute()")

        # The same framing pyexiftool uses: a numbered -execute so its {ready}
        # marker cannot be mistaken for file content, and the exit status echoed to
        # stderr between delimiters.
        signal_num = random.randint(100000, 999999)
        seq_execute = "-execute%d\n" % signal_num
        seq_ready = "{ready%d}" % signal_num
        seq_err_post = "post%d" % signal_num
        delim = "="

        cmd = []
        for p in params:
            if isinstance(p, bytes):
                cmd.append(p)
            elif isinstance(p, str):
                cmd.append(_argument(p).encode(self._encoding))
            else:
                raise TypeError("ERROR: Parameter was not bytes/str: %s => %s" % (type(p), p))
        cmd.extend((b"-echo4",
                    ("%s${status}%s%s" % (delim, delim, seq_err_post)).encode(self._encoding),
                    seq_execute.encode(self._encoding)))
        cmd_bytes = b"\n".join(cmd)

        out, err, wrote = {}, {}, {}
        process = self._process

        def write():
            try:
                process.stdin.write(cmd_bytes)
                process.stdin.flush()
                wrote["ok"] = True
            except (OSError, ValueError) as e:
                wrote["error"] = e

        threads = [
            threading.Thread(target=_read_until, daemon=True, name="exiftool-stdout",
                             args=(process.stdout.fileno(), seq_ready.encode(self._encoding), out)),
            threading.Thread(target=_read_until, daemon=True, name="exiftool-stderr",
                             args=(process.stderr.fileno(), seq_err_post.encode(self._encoding), err)),
            threading.Thread(target=write, daemon=True, name="exiftool-stdin"),
        ]
        for t in threads:
            t.start()

        deadline = time.monotonic() + self.timeout
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))

        stalled = any(t.is_alive() for t in threads)
        if stalled or out.get("eof") or err.get("eof") or "error" in wrote:
            partial = err.get("data", b"").decode(self._encoding, "replace").strip()
            self._abandon(threads)
            if stalled:
                reason = "did not answer within %gs" % self.timeout
            else:
                reason = "exited in the middle of a command"
            logger.warning("ExifTool %s (%s); its process was killed.%s", reason, _describe(params),
                           " Last stderr: %s" % partial if partial else "")
            message = "ExifTool %s (%s); its process was killed." % (reason, _counted(params))
            if partial:
                message += " Last stderr: %s" % unnamed(partial, params)[-500:]
            raise ExifToolTimeout(message)

        raw_stdout, raw_stderr = out["data"], err["data"]
        if not raw_bytes:
            raw_stdout = raw_stdout.decode(self._encoding)
            raw_stderr = raw_stderr.decode(self._encoding)
            err_delim = delim
        else:
            err_delim = delim.encode(self._encoding)

        cmd_stdout = raw_stdout.strip()[:-len(seq_ready)]
        cmd_stderr = raw_stderr.strip()[:-len(seq_err_post)]
        n = len(err_delim)
        if cmd_stderr[-n:] != err_delim:
            raise ExifToolVersionError(
                "Exiftool expected to return status on stderr, but got unexpected "
                "character: %r != %r" % (cmd_stderr[-n:], err_delim))
        first = cmd_stderr.rfind(err_delim, 0, -n)

        self._last_stderr = cmd_stderr[:first]
        self._last_stdout = cmd_stdout
        self._last_status = int(cmd_stderr[first + n:-n])
        return self._last_stdout

    def _abandon(self, threads):
        """Kill the process so every pipe closes and every helper thread ends."""
        process = self._process
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=10)
        except Exception:
            pass
        for t in threads:
            t.join(5)
        for pipe in (process.stdin, process.stdout, process.stderr):
            try:
                pipe.close()
            except Exception:
                pass
        self._flag_running_false()


class ExifToolSession(exiftool.ExifToolHelper, _DrainingExifTool):
    """ExifToolHelper that drains stderr as it goes and gives up after `timeout`.

        with ExifToolSession(executable=path) as et:
            rows = et.get_tags(batch, tags=["XMP:Subject"])
    """

    def __init__(self, *args, timeout=DEFAULT_TIMEOUT, encoding=ENCODING,
                 common_args=DEFAULT_COMMON_ARGS, **kwargs):
        self.timeout = timeout
        common_args = list(common_args or [])
        if not any(str(a).lower().startswith("filename=") for a in common_args):
            common_args += ["-charset", "filename=utf8"]
        super().__init__(*args, encoding=encoding, common_args=common_args, **kwargs)

    def run(self):
        """ExifToolHelper's, its ExifTool then ending with this process (processes.end_with_this_process); every
        start goes through here, the one after a timeout too (ExifToolHelper.execute's auto-start). Under a lazy REPLAY
        only the start a command asks for (_DrainingExifTool._sent)."""
        if self.running or (REPLAY is not None and REPLAY.lazy and not self._starting):
            return
        starting, self._starting = self._starting, True
        try:
            super().run()
        finally:
            self._starting = starting
        processes.end_with_this_process(self._process.pid)

    def execute(self, *params, **kwargs):
        """ExifToolHelper's, raising ExifToolFailed, which says what ExifTool said, for a
        command ExifTool failed; logged in full, on one line, its errors before its warnings (#2015: the error that
        failed a command was logged lines below the warning before it)."""
        try:
            return super().execute(*params, **kwargs)
        except ExifToolExecuteError as e:
            if isinstance(e, ExifToolFailed):
                raise
            logger.warning("ExifTool failed a command (exit status %s, %s): %s", e.returncode,
                           _describe(e.cmd), "; ".join(_said(e.stderr)) or "it said nothing")
            raise ExifToolFailed(e.returncode, e.stdout, e.stderr, e.cmd) from None


class StaysDead:
    """A session of one batch of writes, which stays dead once a command has timed out: pyexiftool starts its process again on the
    next command, and that command would wait its own deadline for a share that is not coming back. Every command after a timeout
    answers the same ExifToolTimeout at once, so the files after it are errors in the time it takes to say so."""

    def __init__(self, session):
        self._session = session
        self._dead = None

    @property
    def dead(self):
        """Has a command of it timed out? Every command after answers that ExifToolTimeout at once."""
        return self._dead is not None

    def __getattr__(self, name):
        wanted = getattr(self._session, name)
        if not callable(wanted):
            return wanted

        def command(*args, **kwargs):
            if self._dead is not None:
                raise self._dead
            try:
                return wanted(*args, **kwargs)
            except ExifToolTimeout as stalled:
                self._dead = stalled
                raise
        return command
