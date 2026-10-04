"""A SELECTION of photos, by id, and what is made of one: the photos it holds, in order (docs/ARCHITECTURE.md, phase
9d-1). The one reading and the one resolver of the bulk edits (tagpup.jobs.bulk_edits) and of the selection panel's tally.

A selection is one of two shapes, sent by a page that never holds 68,000 ids to send:

* `{"ids": [photo id, ...]}`: the photos picked, at most MAX_LISTED. Ids the library has no photo of -- deleted,
  another library's, never anyone's -- are simply not in it, and an id named twice is one.
* `{"source": {"kind", "value", "recursive"}, "excluded": [photo id, ...]}`: every photo of a source (the library views'
  own: the whole library, a folder and its subfolders, a keyword, a person, a year, a month) but the excluded ones
  (at most MAX_LISTED; ids not in the source are no matter). The ids are those of `/api/library/ids` for the same
  source, in the same order, read here, on the server, in one transaction: the page sends the source, not its list.

A selection holds at most MAX_SELECTED photos; a larger one is Refused, with how many it was. Reads only, on a read-only
connection; a root this machine does not place raises paths.RootsError (the web layer's gate answers that first).
"""
import collections
import hashlib
import os

from tagpup.core import paths, vocabulary
from tagpup.core.result import Refused
from tagpup.files import recycle_bin
from tagpup.services import library_view
from tagpup.store import library_view as store
from tagpup.store import selection_folders as store_folders

#: The most ids a selection lists (`ids`, or `excluded`).
MAX_LISTED = 20_000

#: The most photos a selection holds, however it is named.
MAX_SELECTED = 200_000

#: The most tags, and the most people, a tally lists; the rest are counted in `more`.
MAX_TALLIED = 500

#: A selection read: exactly one of `ids` (a tuple, or None) and `source` (a store.Source, or None); `excluded` (a tuple) goes
#: with a source.
Selection = collections.namedtuple("Selection", "ids source excluded")

#: The photos a selection holds: `ids`, in order, each once; `requested` how many ids or how big a source it was asked
#: for; `missing` the ids asked for that have no photo (an ids selection); `excluded` those of the source it left out.
Resolved = collections.namedtuple("Resolved", "ids requested missing excluded")


def _id_list(found, what):
    """`found`, a list of photo ids, as a tuple of ints each once and in order; Refused for anything else or too many."""
    if not isinstance(found, list):
        raise Refused("%s must be a list of photo ids." % what)
    if len(found) > MAX_LISTED:
        raise Refused("%s lists %d photos; at most %d may be named. Select by source instead."
                      % (what, len(found), MAX_LISTED))
    for each in found:
        if type(each) is not int or not 0 < each < 2 ** 62:
            raise Refused("%s must hold whole photo ids, as [1, 2, 3]." % what)
    return tuple(dict.fromkeys(found))


def read(library, body):
    """The Selection a request's `selection` says, or Refused with why it means nothing: neither shape, both, ids that are
    not whole numbers, too many of them, a source that is none (library_view.source_of's own sentences)."""
    if not isinstance(body, dict):
        raise Refused("A selection is {\"ids\": [...]} or {\"source\": {...}, \"excluded\": [...]}.")
    has_ids, has_source = "ids" in body, "source" in body
    if has_ids == has_source:
        raise Refused("A selection names photos either by \"ids\" or by a \"source\", not both and not neither.")
    if has_ids:
        if "excluded" in body:
            raise Refused("\"excluded\" goes with a source, not with a list of ids.")
        return Selection(_id_list(body["ids"], "ids"), None, ())
    named = body["source"]
    if not isinstance(named, dict):
        raise Refused("A source is {\"kind\": ..., \"value\": ..., \"recursive\": ...}.")
    source = library_view.source_of(library, named.get("kind"), named.get("value"), named.get("recursive"))
    return Selection(None, source, _id_list(body.get("excluded", []), "excluded"))


#: The most folders the panel lists under "Folders to Organize" (#675); more are counted and the owner asked to narrow.
MAX_FOLDERS_LISTED = 10


def tally(library, selection):
    """What the photos of `selection` hold: {"total" (the photos that exist), "tags": [{"tag", "count"}], "people":
    [{"name", "count"}], "more_tags", "more_people", "folders": {"count", "listed": [{"path", "name", "photos"}]}} -- the
    folders the selection is in, counted, and named (native path, the folder's own name, its photos selected) only when
    there are MAX_FOLDERS_LISTED or fewer, by name (#675: what the panel offers to open in Organize; 68,000 photos are
    one grouped read of photo_folder, never 68,000 paths); and the tags and people the selection carries, each with the number of
    its photos, tags alphabetically by the shared order (vocabulary.tag_sort_key) and people by it too, at most MAX_TALLIED
    of each (the rest are counted in `more_*`, the most used kept). From photo_tags and photo_people, one grouped read over
    the selection: a source is joined in SQL and its excluded ids taken out there, so 68,000 photos are no list in
    Python and no request of that size. A tag no node of the tree holds is not in photo_tags and so not in it (the
    navigator's counts leave it out too). Refused as `resolve` refuses a selection, but NOT for being larger than a job
    takes: the panel may tally a whole library."""
    conn = library_view.opened(library)
    try:
        found = store.tally(conn, selection.ids, selection.source, selection.excluded)
        # In the tally's read transaction: the folders are of the photos just counted.
        folders = _folders(conn, selection)
    finally:
        conn.close()
    tags = _kept([(tag, count) for tag, count in found["tags"]])
    people = _kept(found["people"])
    return {"total": found["total"],
            "tags": [{"tag": tag, "count": count} for tag, count in tags[0]], "more_tags": tags[1],
            "people": [{"name": name, "count": count} for name, count in people[0]], "more_people": people[1],
            "folders": folders}


def _folders(conn, selection):
    """{"count": folders the selection is in, "listed": [{"path", "name", "photos"}]}: named only when MAX_FOLDERS_LISTED or
    fewer, in the shared order of their names (then their paths)."""
    counted = store_folders.counts(conn, selection.ids, selection.source, selection.excluded)
    if not counted or len(counted) > MAX_FOLDERS_LISTED:
        return {"count": len(counted), "listed": []}
    named = store_folders.described(conn, counted)
    listed = [{"path": path, "name": name, "photos": counted[folder_id]} for folder_id, (path, name) in named.items()]
    listed.sort(key=lambda each: (vocabulary.tag_sort_key(each["name"]), each["path"]))
    return {"count": len(counted), "listed": listed}


def _kept(counted):
    """([(name, count)] at most MAX_TALLIED of them -- the most used, then in the shared alphabetical order --, how many
    were left out)."""
    if len(counted) > MAX_TALLIED:
        counted = sorted(counted, key=lambda each: (-each[1], vocabulary.tag_sort_key(each[0])))
        left = len(counted) - MAX_TALLIED
        counted = counted[:MAX_TALLIED]
    else:
        left = 0
    return sorted(counted, key=lambda each: vocabulary.tag_sort_key(each[0])), left


def token_of(photo_ids):
    """The token of a set of photos: a hash of their ids, sorted (the order they were named in is no matter). What a Delete's
    question was about, and what its start must still resolve to (#691). 68,000 ids are about 20 ms."""
    return hashlib.sha256(",".join(str(each) for each in sorted(set(photo_ids))).encode("ascii")).hexdigest()


def where_deleted(library, selection):
    """Where a Delete of `selection` would send its files, for the question asked before it (#674): {"total" (photos), "token"
    (token_of the photos: the start of the delete must carry it, and is refused when the selection no longer resolves to
    them, #691), "folders", "through_this_pc" (photos in folders with no Recycle Bin -- a network share, a mapped or SUBST drive,
    a removable one -- which are copied to this PC and the copies recycled there, #694), "reasons": [{"reason", "photos"}],
    "copy_bytes" (what those copies take, by the index's sizes), "restores_to" (the folder the copies are put in, and so where
    Windows restores them: <Downloads>\\TagPup deleted from shares), "too_long" (how many of them would have a copy's path of
    260 characters or more, and are left, #704), "no_room" (None, or the sentence when they cannot be copied and kept here:
    Downloads synced to OneDrive, no room on its drive with 1 GB to spare, or this PC's Recycle Bin unable to keep them -- asked of
    Windows now, #703, #706: nothing is to be asked then)}; the reasons as recycle_bin says them ("on a network share").
    Asked once a folder, never once a photo (a Select all of photo_index is 2,672 folders, about 2 s); a UNC path and a mapped
    drive are told by their spelling and the drive's type, without reading the share. Refused, as `resolve` refuses it, over
    MAX_SELECTED."""
    resolved = resolve(library, selection)
    conn = library_view.opened(library)
    try:
        counted = store_folders.counts(conn, resolved.ids, None, ())
        sizes = store_folders.bytes_by_folder(conn, resolved.ids, None, ())
        named = store_folders.described(conn, counted)
    finally:
        conn.close()
    reasons = collections.Counter()
    copy_bytes = largest = 0
    binless = set()
    for folder_id, (path, _name) in named.items():
        reason = recycle_bin.no_bin_reason(path)
        if reason:
            reasons[reason] += counted[folder_id]
            total, biggest = sizes.get(folder_id) or (0, 0)
            copy_bytes += total
            largest = max(largest, biggest)
            binless.add(folder_id)
    through = sum(reasons.values())
    too_long = _too_long(library, resolved.ids, binless, named) if through else 0
    return {"total": len(resolved.ids), "token": token_of(resolved.ids), "folders": len(counted), "through_this_pc": through,
            "reasons": [{"reason": reason, "photos": photos} for reason, photos in reasons.most_common()],
            "copy_bytes": copy_bytes, "restores_to": recycle_bin.mirror_root(), "too_long": too_long,
            "no_room": recycle_bin.can_copy_here(copy_bytes, largest, fresh=True) if through else None}


def _too_long(library, photo_ids, binless, named):
    """How many photos of `photo_ids` in the folders `binless` would have a copy's path too long for the Recycle Bin (#704): their
    paths read (ids and paths only), only when some go through this PC."""
    folders = {paths.key(named[folder_id][0]) for folder_id in binless}
    conn = library_view.opened(library)
    try:
        found = store.paths_of(conn, photo_ids)
    finally:
        conn.close()
    root = recycle_bin.mirror_root()
    return sum(1 for path in found.values()
               if paths.key(os.path.dirname(path)) in folders and recycle_bin.too_long(recycle_bin.mirror_of(path, root)))


def _refuse_if_large(count):
    if count > MAX_SELECTED:
        raise Refused("That selects %s photos; a bulk edit takes at most %s. Narrow the selection."
                      % ("{:,}".format(count), "{:,}".format(MAX_SELECTED)))


def resolve(library, selection):
    """The photos `selection` holds, as Resolved: those that exist now, in order -- an ids selection in the order named, a
    source in `/api/library/ids`'s order -- each once, at most MAX_SELECTED (else Refused). The photo that exists is decided
    here, by the library's own rows; a source is read in one transaction, so a library changing meanwhile gives a list that
    was true at one moment."""
    conn = library_view.opened(library)
    try:
        if selection.source is None:
            _refuse_if_large(len(selection.ids))
            there = store.existing_ids(conn, selection.ids)
            ids = [photo_id for photo_id in selection.ids if photo_id in there]
            return Resolved(ids, len(selection.ids), len(selection.ids) - len(ids), 0)
        found, total = store.source_ids(conn, selection.source, MAX_SELECTED)
        _refuse_if_large(total)
    finally:
        conn.close()
    left_out = set(selection.excluded)
    ids = [photo_id for photo_id in dict.fromkeys(found) if photo_id not in left_out]
    return Resolved(ids, total, 0, len(found) - len(ids))
