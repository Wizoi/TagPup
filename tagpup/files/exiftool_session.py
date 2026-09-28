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

__all__ = ["ExifToolSession", "ExifToolTimeout", "ExifToolFailed", "DEFAULT_TIMEOUT"]

logger = logging.getLogger(__name__)

#: Seconds one ExifTool command may take. A batch of a hundred photos reads in a few
#: seconds; this is generous enough for a slow disk and still ends a stall the same
#: afternoon rather than two days later.
DEFAULT_TIMEOUT = 300

#: What ExifTool reads and writes; never the locale's code page.
ENCODING = "utf-8"

#: pyexiftool's own defaults: group names on every tag, numeric values.
DEFAULT_COMMON_ARGS = ("-G", "-n")


class ExifToolTimeout(RuntimeError):
    """ExifTool did not answer one command in time; its process has been killed. Its
    message names no photo, as ExifToolFailed's names none: it reaches the journal's
    conflict text, which History shows without revealing paths."""


#: Lines of what ExifTool said that go in ExifToolFailed's message; the rest is logged.
SAID_LINES = 2


class ExifToolFailed(ExifToolExecuteError):
    """ExifTool ran a command and failed it: pyexiftool's error, its message saying what
    ExifTool said (its first SAID_LINES error lines, each photo named "<file>")."""

    def __init__(self, exit_status, cmd_stdout, cmd_stderr, params):
        super().__init__(exit_status, cmd_stdout, cmd_stderr, params)
        said = said_briefly(cmd_stderr, params)
        if said:
            self.args = ("ExifTool: %s (exit status %s)" % (said, exit_status),) + self.args[1:]

    def __str__(self):
        return str(self.args[0])


def _text(value):
    if isinstance(value, bytes):
        return value.decode(ENCODING, "replace")
    return str(value or "")


def said_briefly(stderr, params=()):
    """ExifTool's first SAID_LINES lines of `stderr` (errors before warnings), each file
    of the command, `params`, named "<file>": ExifTool spells a path with forward
    slashes, whichever it was given."""
    lines = [line.strip() for line in _text(stderr).splitlines() if line.strip()]
    lines = [line for line in lines if not line.startswith("Warning")] + \
        [line for line in lines if line.startswith("Warning")]
    return unnamed("; ".join(lines[:SAID_LINES]), params)


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

    def execute(self, *params, raw_bytes=False):
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
                cmd.append(p.encode(self._encoding))
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

    def execute(self, *params, **kwargs):
        """ExifToolHelper's, raising ExifToolFailed, which says what ExifTool said, for a
        command ExifTool failed; logged in full."""
        try:
            return super().execute(*params, **kwargs)
        except ExifToolExecuteError as e:
            if isinstance(e, ExifToolFailed):
                raise
            logger.warning("ExifTool failed a command (exit status %s, %s): %s", e.returncode,
                           _describe(e.cmd), _text(e.stderr).strip() or "it said nothing")
            raise ExifToolFailed(e.returncode, e.stdout, e.stderr, e.cmd) from None
