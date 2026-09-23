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

Everything else -- get_tags, set_tags, check_execute, auto-start -- is ExifToolHelper's.
"""
import os
import random
import threading
import time

import exiftool
from exiftool.exceptions import ExifToolNotRunning, ExifToolVersionError

__all__ = ["ExifToolSession", "ExifToolTimeout", "DEFAULT_TIMEOUT"]

#: Seconds one ExifTool command may take. A batch of a hundred photos reads in a few
#: seconds; this is generous enough for a slow disk and still ends a stall the same
#: afternoon rather than two days later.
DEFAULT_TIMEOUT = 300


class ExifToolTimeout(RuntimeError):
    """ExifTool did not answer one command in time; its process has been killed."""


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
            message = "ExifTool %s (%s); its process was killed." % (reason, _describe(params))
            if partial:
                message += " Last stderr: %s" % partial[-500:]
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

    def __init__(self, *args, timeout=DEFAULT_TIMEOUT, **kwargs):
        self.timeout = timeout
        super().__init__(*args, **kwargs)
