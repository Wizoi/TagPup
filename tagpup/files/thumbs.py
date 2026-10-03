"""The thumbnail cache's files: a folder of small JPEGs, one for each photo and the state of its file.

Derived, never the source of anything: every entry can be made again from its photo, and a missing,
unreadable or wrong one only costs a thumbnail made again. Nothing here knows a library or a database;
tagpup.services.thumbnails decides what an entry is called and when one is made or dropped
(docs/ARCHITECTURE.md, phase 9a-2).

An entry is `<shard>/<id>_<path hash>_<mtime ms>_<size>.jpg`:

* `id`, the photo's id; the shard is `id // 1000`, so no folder holds more than a thousand files.
* `path hash`, eight hex digits of the photo's own path as its row holds it. Ids are reassigned when a
  library is restored from a snapshot or its photos are indexed again from nothing, so an id alone could
  serve another photo's picture; the id and the path together do not. A library moved to another place
  (every row's path unchanged, a root's map changed) keeps its entries; a photo renamed gets a new hash
  and so a new entry, made once, and the old one is dropped as it is made.
* `mtime ms` and `size`, the stamp of the file the picture was made from: a file changed is another entry,
  and the one it replaces is deleted.

An entry is written to a temporary file in its shard and renamed over its name (`os.replace`), so a reader
never sees half of one and two writers of one entry leave one whole file. A crash between the two leaves a
`.tmp-` file, which `sweep_temporaries` removes once it is old. Nothing is bounded: the owner's decision
(about 20-40 KB a photo).
"""
import hashlib
import itertools
import os
import re
import threading
import time

from tagpup.files import images

#: The long side of a thumbnail, in pixels.
SIZE = 300

#: Photos to a shard folder.
SHARD = 1000

#: A temporary file is only the work of a writer that crashed once it is this old, in seconds.
TEMPORARY_AGE = 3600

_ENTRY = re.compile(r"^(\d+)_([0-9a-f]{8})_(-?\d+)_(\d+)\.jpg$")
_TEMPORARY = ".tmp-"
_counter = itertools.count()


def short_hash(text):
    """Eight hex digits of `text`: a photo's own path, as the caller spells it."""
    return hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=4).hexdigest()


def stamp_key(mtime):
    """`mtime`, the file's, as whole milliseconds: what an entry's name holds."""
    return int(round(mtime * 1000))


def name_of(photo_id, path_hash, mtime, size):
    return "%d_%s_%d_%d.jpg" % (photo_id, path_hash, stamp_key(mtime), size)


def etag_of(photo_id, path_hash, mtime, size):
    """What names an entry's picture to a browser: the entry's name without its extension."""
    return name_of(photo_id, path_hash, mtime, size)[:-len(".jpg")]


def _shard(root, photo_id):
    return os.path.join(root, "%03d" % (photo_id // SHARD))


def entry_path(root, photo_id, path_hash, mtime, size):
    return os.path.join(_shard(root, photo_id), name_of(photo_id, path_hash, mtime, size))


def _read(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except (FileNotFoundError, NotADirectoryError):
        return None


def read(root, photo_id, path_hash, mtime, size):
    """The bytes of the entry for this photo in this state, or None when there is none. Reads it whole
    and closes it, so nothing holds an entry open while another request replaces it."""
    return _read(entry_path(root, photo_id, path_hash, mtime, size))


def _entries_of(root, photo_id):
    """[(name, mtime ms, size)] of every entry of `photo_id` in its shard, whatever its hash."""
    try:
        listing = os.listdir(_shard(root, photo_id))
    except (FileNotFoundError, NotADirectoryError):
        return []
    found = []
    prefix = "%d_" % photo_id
    for name in listing:
        if name.startswith(prefix):
            match = _ENTRY.match(name)
            if match and int(match.group(1)) == photo_id:
                found.append((name, match.group(2), int(match.group(3)), int(match.group(4))))
    return found


def newest(root, photo_id, path_hash):
    """The bytes of the newest entry of this photo made from a file at this path, or None: what a photo
    whose file is not there now is shown, the last it looked like."""
    held = [each for each in _entries_of(root, photo_id) if each[1] == path_hash]
    for name, _hash, _mtime, _size in sorted(held, key=lambda each: each[2], reverse=True):
        data = _read(os.path.join(_shard(root, photo_id), name))
        if data is not None:
            return data
    return None


def write(root, photo_id, path_hash, mtime, size, data):
    """Keep `data` as the entry for this photo in this state, whole or not at all, and delete the entries
    it replaces (those of the photo under another stamp or path). Raises OSError when the folder cannot be
    made or written -- a folder that is read-only, a disk that is full -- having left no half entry."""
    shard = _shard(root, photo_id)
    os.makedirs(shard, exist_ok=True)
    final = os.path.join(shard, name_of(photo_id, path_hash, mtime, size))
    temporary = os.path.join(shard, "%s%d-%d-%d" % (_TEMPORARY, os.getpid(), threading.get_ident(), next(_counter)))
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
        try:
            os.replace(temporary, final)
        except PermissionError:
            # Windows will not replace a file another process holds open at this moment. If the entry is
            # there, whoever holds it put it there: the same picture, made the same way.
            if not os.path.isfile(final):
                raise
    finally:
        try:
            os.remove(temporary)
        except OSError:
            pass
    for name, _hash, _mtime, _size in _entries_of(root, photo_id):
        if name != os.path.basename(final):
            try:
                os.remove(os.path.join(shard, name))
            except OSError:
                pass   # one held open now is dropped by the next write


def remove(root, photo_id):
    """Delete every entry of `photo_id`. Returns (entries, bytes) deleted."""
    count = size = 0
    for name, _hash, _mtime, _size in _entries_of(root, photo_id):
        path = os.path.join(_shard(root, photo_id), name)
        try:
            length = os.path.getsize(path)
            os.remove(path)
        except FileNotFoundError:
            continue
        count, size = count + 1, size + length
    return count, size


def entries(root):
    """(photo id, path hash, mtime ms, size, path, bytes) of every entry, shard by shard."""
    try:
        shards = sorted(os.listdir(root))
    except (FileNotFoundError, NotADirectoryError):
        return
    for shard in shards:
        folder = os.path.join(root, shard)
        try:
            listing = os.scandir(folder)
        except (FileNotFoundError, NotADirectoryError):
            continue
        with listing:
            for entry in listing:
                match = _ENTRY.match(entry.name)
                if match:
                    try:
                        length = entry.stat().st_size
                    except OSError:
                        continue
                    yield (int(match.group(1)), match.group(2), int(match.group(3)), int(match.group(4)),
                           entry.path, length)


def usage(root):
    """(entries, bytes) the cache holds."""
    count = size = 0
    for each in entries(root):
        count, size = count + 1, size + each[5]
    return count, size


def sweep_temporaries(root, older_than=TEMPORARY_AGE):
    """Delete the temporary files a writer that crashed left, those older than `older_than` seconds. Returns
    how many."""
    removed, cutoff = 0, time.time() - older_than
    try:
        shards = os.listdir(root)
    except (FileNotFoundError, NotADirectoryError):
        return 0
    for shard in shards:
        folder = os.path.join(root, shard)
        try:
            listing = os.scandir(folder)
        except (FileNotFoundError, NotADirectoryError):
            continue
        with listing:
            for entry in listing:
                if entry.name.startswith(_TEMPORARY):
                    try:
                        if entry.stat().st_mtime < cutoff:
                            os.remove(entry.path)
                            removed += 1
                    except OSError:
                        continue
    return removed


# ---- Making a picture --------------------------------------------------------------------------

def render(photo_path):
    """The thumbnail of the photo at `photo_path`: a JPEG no larger than SIZE on a side, turned upright by
    its Orientation. Raises images.Unreadable when the picture does not decode (the file is damaged) and the
    OSError the system raised when it cannot be read at all: a share gone for a moment is not a damaged
    photo."""
    try:
        return images.smaller_copy(photo_path, SIZE, upright=True)
    except Exception as error:
        if not images.is_damage(error):
            raise
        with open(photo_path, "rb") as handle:
            data = handle.read()
        raise images.damage(data, error) from error


_placeholder = None
_placeholder_guard = threading.Lock()


def placeholder():
    """A small grey picture with a cross, for a photo that cannot be shown: the same bytes every time."""
    global _placeholder
    with _placeholder_guard:
        if _placeholder is None:
            _placeholder = images.placeholder_jpeg()
        return _placeholder
