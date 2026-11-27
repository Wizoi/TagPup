"""ExifTool over a table of files, for tests of writes: what the journaled write reads, writes and reads back.

`Files` holds each photo's fields as ExifTool answers them (`{path: {"XMP:Subject": [...], "EXIF:DateTimeOriginal": "..."}}`,
under the key ExifTool answers a field by: tagpup.core.fields.read_key), `session` stands in for
tagpup.files.exiftool_session.ExifToolSession, and a test reaches into the middle of a write through the hooks:

* `on_read(path, n)`: called before the n-th read of `path` is answered -- another program edits the file between the plan's read
  and the write's;
* `on_write(path, n)`: called before the n-th write of any file (a test pauses a job here: set an event, wait for another);
* `unreadable`, `fails`: the keys (tagpup.core.paths.key) of paths ExifTool answers as not a photo, and whose write it refuses;
* a write of a photo whose scratch file (exiftool_session.scratch_of) is on disk is refused as ExifTool refuses it, before
  `on_write` is called;

A file is also a real one on disk when the test made it so (tests/view_library.py make_jpeg), which the lock-free parts of a write
(the stat, the tail check, the Recycle Bin) look at. A write is made as field_values.write makes it, beside the photo (`-o`):
the new file is a copy of a real photo, which the picture check (field_values._landed) compares and moves over it; a photo
that is only in the table has no new file and nothing to move, so while a session is open the check passes it by. A test
whose own stand-in writes no file at all says so with `writes_no_file(testcase)`. A table holds fields, not bytes, so it
writes no segment a patch in place could take: while a session is open every write is the rewrite (field_values.
PATCH_IN_PLACE off), whose steps (the scratch file, the picture check) these tests follow.
"""
import os
import shutil
import threading
from unittest import mock

from tagpup.core import fields, paths
from tagpup.files import exiftool_session, field_values

#: The sessions open (in every thread) and the patches of the picture check and of the patch in place they hold while any
#: is (_check_passes_by).
_open = {"sessions": 0, "patch": None, "rewrite": None}
_open_lock = threading.Lock()
_real_landed = field_values._landed


def _landed_or_only_in_the_table(photo_path, scratch):
    """The picture check of a new file the stand-in wrote (of a real photo); nothing for a photo of which none was written
    (one only in the table, or a stand-in that writes no file)."""
    if os.path.exists(scratch):
        _real_landed(photo_path, scratch)


def writes_no_file(testcase):
    """For `testcase`, whose stand-in for ExifTool keeps fields and writes no file: the picture check passes by a write
    that left no new file beside its photo (_landed_or_only_in_the_table), until the test ends."""
    patcher = mock.patch("tagpup.files.field_values._landed", _landed_or_only_in_the_table)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _check_passes_by(opening):
    """A session opened (`opening`) or closed: the first open patches the picture check, the last closed unpatches it."""
    with _open_lock:
        _open["sessions"] += 1 if opening else -1
        if opening and _open["sessions"] == 1:
            _open["patch"] = mock.patch("tagpup.files.field_values._landed", _landed_or_only_in_the_table)
            _open["patch"].start()
            _open["rewrite"] = mock.patch("tagpup.files.field_values.PATCH_IN_PLACE", False)
            _open["rewrite"].start()
        elif not opening and _open["sessions"] == 0:
            _open["patch"].stop()
            _open["rewrite"].stop()
            _open["patch"] = _open["rewrite"] = None


def _written_beside(path, params):
    """What ExifTool's `-o <file>` does: the new file of a real photo written at <file> (a copy: the table holds the fields)."""
    if "-o" in params and os.path.exists(path):
        shutil.copyfile(path, params[params.index("-o") + 1])


def standing_in(testcase):
    """ExifTool is a table of files for `testcase` (ExifToolSession patched until it ends): the Files, for a test of a route
    that writes a photo's tags as a face is named or unnamed (tagpup.services.face_people), whose photos are rows and not
    always files."""
    files = Files()
    patcher = mock.patch("tagpup.files.exiftool_session.ExifToolSession", files.session)
    patcher.start()
    testcase.addCleanup(patcher.stop)
    return files


class Files:
    def __init__(self):
        self.fields = {}
        self.reads = {}
        self.writes = 0
        self.writes_of = {}
        self.on_read = None
        self.on_write = None
        self.unreadable = set()
        self.fails = set()
        self.lock = threading.Lock()
        self.starts = 0
        self.sessions = []        # the keyword arguments each session was asked for (timeout=...)
        self.read_calls = 0       # get_tags commands answered or begun

    # ---- what the tests set up and look at -------------------------------------------------------

    def hold(self, path, **held):
        """The file at `path` holds these fields (keys with the group's ":" written "__": XMP__Subject)."""
        self.fields[paths.key(path)] = {fields.read_key(key.replace("__", ":")): value for key, value in held.items()}
        return path

    def keep_tags(self, path, tags):
        """The file holds exactly `tags` as a program writing keywords leaves every keyword field (tagpup.core.fields)."""
        flat, hierarchical = fields.expand_tag_fields(list(tags))
        held = self.fields.setdefault(paths.key(path), {})
        for field, value in fields.keyword_fields(flat, hierarchical).items():
            held[fields.read_key(field)] = value

    def tags_of(self, path):
        return list(self.fields[paths.key(path)].get("XMP:Subject") or [])

    def taken_of(self, path):
        return self.fields[paths.key(path)].get("EXIF:DateTimeOriginal")

    # ---- ExifToolSession's side ----------------------------------------------------------------------

    def session(self, *args, **kwargs):
        """A stand-in for ExifToolSession(executable=...): a context manager that is this table."""
        files = self
        files.sessions.append(dict(kwargs))

        class Session:
            def __enter__(self):
                with files.lock:
                    files.starts += 1
                _check_passes_by(True)
                return files

            def __exit__(self, *problem):
                _check_passes_by(False)
                return False
        return Session()

    def get_tags(self, photo_paths, tags=None, params=None):
        with self.lock:
            self.read_calls += 1
        rows = []
        for path in photo_paths:
            key = paths.key(path)
            with self.lock:
                self.reads[key] = self.reads.get(key, 0) + 1
                n = self.reads[key]
            if self.on_read is not None:
                self.on_read(path, n)
            if key in self.unreadable:
                rows.append({"SourceFile": path, "File:MIMEType": "text/plain"})
                continue
            held = self.fields.get(key, {})
            row = {"SourceFile": path, "File:MIMEType": "image/jpeg"}
            for field in tags or []:
                key = fields.read_key(field)
                if key in held:
                    row[key] = held[key]
            rows.append(row)
        return rows

    @staticmethod
    def refuse_in_the_way(path):
        """What ExifTool answers a write of a photo (`-o` at its scratch file) whose scratch file is there (13.59)."""
        scratch = exiftool_session.scratch_of(path)
        if os.path.exists(scratch):
            raise exiftool_session.ExifToolFailed(1, "", "Error: '%s' already exists - %s\n" % (scratch, path), [path])

    def set_tags(self, photo_paths, tags=None, params=None):
        for path in photo_paths:
            key = paths.key(path)
            with self.lock:
                self.writes += 1
                n = self.writes
            self.refuse_in_the_way(path)
            if self.on_write is not None:
                self.on_write(path, n)
            if key in self.fails:
                raise RuntimeError("the file is locked")
            with self.lock:
                self.writes_of[key] = self.writes_of.get(key, 0) + 1   # a write that was cut short by the hook is no write
            held = self.fields.setdefault(key, {})
            for field in params or []:
                if field.startswith("-") and field.endswith("="):
                    held.pop(fields.read_key(field[1:-1]), None)
            answered_as = {written: read for read, written in fields.WRITTEN_AS.items()}
            for field, value in (tags or {}).items():
                held[fields.read_key(answered_as.get(field, field))] = value
            _written_beside(path, list(params or []))

    def execute(self, *args):
        self.refuse_in_the_way(args[-1])
        held = self.fields.setdefault(paths.key(args[-1]), {})
        for field in args:
            if field.startswith("-") and field.endswith("="):
                held.pop(fields.read_key(field[1:-1]), None)
        _written_beside(args[-1], list(args))
        return ""
