"""ExifTool over a table of files, for tests of writes: what the journaled write reads, writes and reads back.

`Files` holds each photo's fields as ExifTool answers them (`{path: {"XMP:Subject": [...], "EXIF:DateTimeOriginal": "..."}}`,
under the key ExifTool answers a field by: tagpup.core.fields.read_key), `session` stands in for
tagpup.files.exiftool_session.ExifToolSession, and a test reaches into the middle of a write through the hooks:

* `on_read(path, n)`: called before the n-th read of `path` is answered -- another program edits the file between the plan's read
  and the write's;
* `on_write(path, n)`: called before the n-th write of any file (a test pauses a job here: set an event, wait for another);
* `unreadable`, `fails`: the keys (tagpup.core.paths.key) of paths ExifTool answers as not a photo, and whose write it refuses.

A file is also a real one on disk when the test made it so (tests/view_library.py make_jpeg), which the lock-free parts of a write
(the stat, the tail check, the Recycle Bin) look at.
"""
import threading
from unittest import mock

from tagpup.core import fields, paths


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
        self.cannot_start = None
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
                if files.cannot_start is not None:
                    raise files.cannot_start
                return files

            def __exit__(self, *problem):
                return False
        return Session()

    def get_tags(self, photo_paths, tags=None):
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

    def set_tags(self, photo_paths, tags=None, params=None):
        for path in photo_paths:
            key = paths.key(path)
            with self.lock:
                self.writes += 1
                n = self.writes
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
            for field, value in (tags or {}).items():
                held[fields.read_key(field)] = value

    def execute(self, *args):
        held = self.fields.setdefault(paths.key(args[-1]), {})
        for field in args:
            if field.startswith("-") and field.endswith("="):
                held.pop(fields.read_key(field[1:-1]), None)
        return ""
