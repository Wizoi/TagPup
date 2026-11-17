"""A SELECTION of photos, by id, and what is made of one: the photos it holds, in order (docs/ARCHITECTURE.md, phase
9d-1). The one reading and the one resolver of the bulk edits (tagpup.services.bulk_edit) and of the selection panel's tally.

A selection is one of three shapes, sent by a page that never holds 68,000 ids to send:

* `{"ids": [photo id, ...]}`: the photos picked, at most MAX_LISTED. Ids the library has no photo of -- deleted,
  another library's, never anyone's -- are simply not in it, and an id named twice is one.
* `{"source": {"kind", "value", "recursive", "media"}, "excluded": [photo id, ...]}`: every photo of a source (the library
  views' own: the whole library, a folder and its subfolders, a keyword, a person, a year, a month; of the one kind `media`
  names, as the view's Photos / Videos filter shows it, library_view.media_of) but the excluded ones
  (at most MAX_LISTED; ids not in the source are no matter). The ids are those of `/api/library/ids` for the same
  source, in the same order, read here, on the server, in one transaction: the page sends the source, not its list.
* `{"paths": [path, ...]}`: the photos at these paths, at most MAX_LISTED, each named once (paths.key): what a folder opened in
  Organize holds, whose scan carries no ids. Each is the photo whose row is at it, however it is spelled
  (store.roots.photo_ids_at), looked up when the selection is resolved; a path no row has (a file not indexed yet) is
  missing, as an id nobody has.

A selection holds at most batches.MAX_BATCH photos; a larger one is Refused, with how many it was. Reads only, on a read-only
connection; a root this machine does not place raises paths.RootsError (the web layer's gate answers that first).
"""
import collections
import hashlib
import os

from tagpup.core import paths, vocabulary
from tagpup.core.result import Refused
from tagpup.files import recycle_bin
from tagpup.services import batches, file_changes, library_view
from tagpup.store import library_view as store
from tagpup.store import person_ids
from tagpup.store import roots as store_roots
from tagpup.store import selection_folders as store_folders

#: The most ids or paths a selection lists (`ids`, `excluded` or `paths`).
MAX_LISTED = 20_000

#: The most tags, and the most people, a tally lists; the rest are counted in `more`.
MAX_TALLIED = 500

#: A selection read: exactly one of `ids` (a tuple), `source` (a store.Source) and `paths` (a tuple of paths as named), the others
#: None; `excluded` (a tuple) goes with a source.
Selection = collections.namedtuple("Selection", "ids source excluded paths")

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


def _path_list(found):
    """`found`, a list of photos' paths, as a tuple in the order named; Refused for anything but text, too many, or one photo
    named twice (paths.key: a page sends each once, so a second spelling of one is a page's mistake, said, not guessed at)."""
    if not isinstance(found, list):
        raise Refused("paths must be a list of photos' paths.")
    if len(found) > MAX_LISTED:
        raise Refused("paths lists %d photos; at most %d may be named." % (len(found), MAX_LISTED))
    if not all(isinstance(each, str) and each for each in found):
        raise Refused("paths must hold photos' paths, as text.")
    if len({paths.key(each) for each in found}) != len(found):
        raise Refused("paths names a photo twice: name each once.")
    return tuple(found)


def read(library, body):
    """The Selection a request's `selection` says, or Refused with why it means nothing: no shape, two, ids that are not whole
    numbers, paths that are not text or name a photo twice, too many of either, a source that is none (library_view.source_of's
    own sentences)."""
    if not isinstance(body, dict):
        raise Refused("A selection is {\"ids\": [...]}, {\"source\": {...}, \"excluded\": [...]} or {\"paths\": [...]}.")
    shapes = [shape for shape in ("ids", "source", "paths") if shape in body]
    if len(shapes) != 1:
        raise Refused("A selection names photos by \"ids\", by a \"source\" or by \"paths\": one of them, not two and not none.")
    if shapes[0] != "source" and "excluded" in body:
        raise Refused("\"excluded\" goes with a source, not with a list of ids or paths.")
    if shapes[0] == "ids":
        return Selection(_id_list(body["ids"], "ids"), None, (), None)
    if shapes[0] == "paths":
        return Selection(None, None, (), _path_list(body["paths"]))
    named = body["source"]
    if not isinstance(named, dict):
        raise Refused("A source is {\"kind\": ..., \"value\": ..., \"recursive\": ...}.")
    source = library_view.source_of(library, named.get("kind"), named.get("value"), named.get("recursive"), named.get("media"))
    return Selection(None, source, _id_list(body.get("excluded", []), "excluded"), None)


#: The most folders the panel lists under "Folders to Organize" (#675); more are counted and the owner asked to narrow.
MAX_FOLDERS_LISTED = 10


def tally(library, selection):
    """What the photos of `selection` hold: {"total" (the photos that exist), "tags": [{"tag", "count"}], "people":
    [{"name", "count", "has_node", "person"}], "more_tags", "more_people", "folders": {"count", "listed": [{"path", "name", "photos"}]}} -- the
    folders the selection is in, counted, and named (native path, the folder's own name, its photos selected) only when
    there are MAX_FOLDERS_LISTED or fewer, by name (#675: what the panel offers to open in Organize; 68,000 photos are
    one grouped read of photo_folder, never 68,000 paths); and the tags and people the selection carries, each with the number of
    its photos, tags alphabetically by the shared order (vocabulary.tag_sort_key) and people by it too, at most MAX_TALLIED
    of each (the rest are counted in `more_*`, the most used kept). From photo_tags and photo_people, one grouped read over
    the selection: a source is joined in SQL and its excluded ids taken out there, so 68,000 photos are no list in
    Python and no request of that size. A tag that is a person's node is a person and is left out of "tags" before they are cut
    (#865); a person's `has_node` says whether the tree has one node of that name (no: a branch, two nodes, none; #866). A tag no node of the tree holds is not in photo_tags and so not in it (the
    navigator's counts leave it out too). Refused as `resolve` refuses a selection, but NOT for being larger than a job
    takes: the panel may tally a whole library."""
    conn = library_view.opened(library)
    try:
        selection = _by_id(conn, selection)
        found = store.tally(conn, selection.ids, selection.source, selection.excluded)
        # In the tally's read transaction: the folders are of the photos just counted.
        folders = _folders(conn, selection)
        everyone = person_ids.Directory.read(conn)
    finally:
        conn.close()
    tags = _kept([(tag, count) for tag, count in found["tags"]], lambda each: each[0])
    people = _kept(found["people"], lambda each: each[0].name)
    nameless = set(found["nameless"])
    return {"total": found["total"],
            "tags": [{"tag": tag, "count": count} for tag, count in tags[0]], "more_tags": tags[1],
            "people": [{"name": person.name, "count": count, "has_node": person not in nameless,
                        "person": everyone.of_id(person.id), "person_id": person.id}
                       for person, count in people[0]],
            "more_people": people[1],
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


def _kept(counted, name_of):
    """([(thing, count)] at most MAX_TALLIED of them -- the most used, then in the shared alphabetical order of `name_of(each)`
    --, how many were left out)."""
    if len(counted) > MAX_TALLIED:
        counted = sorted(counted, key=lambda each: (-each[1], vocabulary.tag_sort_key(name_of(each))))
        left = len(counted) - MAX_TALLIED
        counted = counted[:MAX_TALLIED]
    else:
        left = 0
    return sorted(counted, key=lambda each: vocabulary.tag_sort_key(name_of(each))), left


def token_of(photo_ids, for_good_ids=()):
    """The token of a set of photos: a hash of their ids, sorted (the order they were named in is no matter), and, after a
    ".", a hash of the ids of those the question said would be deleted for good, every one of them (deleted_for_good), not
    only those it named. What a Delete's question was about, and what its start must still resolve to (#691): a start
    confirmed for good carries the token of a question that warned of exactly the files it would now delete for good. A token
    begins with token_of its photos alone, so a start can tell photos that changed from files for good that did
    (bulk_edit.refuse_if_changed). 68,000 ids are about 20 ms."""
    photos = hashlib.sha256(",".join(str(each) for each in sorted(set(photo_ids))).encode("ascii")).hexdigest()
    if not for_good_ids:
        return photos
    for_good = ",".join(str(each) for each in sorted(set(for_good_ids)))
    return photos + "." + hashlib.sha256(("for good:" + for_good).encode("ascii")).hexdigest()


#: How many of the files a Delete would delete for good its question names; the rest are counted.
MOST_FOR_GOOD_NAMED = 5


def where_deleted(library, selection):
    """Where a Delete of `selection` would send its files, for the question asked before it (#674): {"total" (photos), "token"
    (token_of the photos: the start of the delete must carry it, and is refused when the selection no longer resolves to
    them, #691), "folders", "through_this_pc" (photos in folders with no Recycle Bin -- a network share, a mapped or SUBST drive,
    a removable one -- which are copied to this PC and the copies recycled there, #694), "reasons": [{"reason", "photos"}],
    "copy_bytes" (what those copies take, by the index's sizes; a clip over recycle_bin.MOST_CLIP_BYTES makes none and is
    counted in neither: it goes for good), "restores_to" (the folder the copies are put in, and so where
    Windows restores them: <Downloads>\\TagPup deleted from shares), "too_long" (how many of them would have a copy's path of
    260 characters or more, and are left, #704), "no_room" (None, or the sentence when they cannot be copied here at all:
    Downloads synced to OneDrive, no room on its drive with 1 GB to spare, or a Recycle Bin Windows cannot tell of, #703, #706:
    nothing is to be asked then), "for_good": {"count", "names"} (the clips over recycle_bin.MOST_CLIP_BYTES, on any drive, the
    files on a drive whose Recycle Bin would not keep them -- larger than it keeps, or a Bin set to delete at once,
    recycle_bin.keeps_up_to --, and the files, or the copies of the files with no Bin of their own, a Recycle Bin would not
    keep when their turn comes (_not_binned: full, set to delete at once, or smaller than one), which are then not counted
    through this PC; all deleted for good only when the start says `for_good`, its token then of that; the file names of the
    first MOST_FOR_GOOD_NAMED; the token covers every one of them), "bin_full" (how many of them are for good only because a
    Recycle Bin is full: the page asks "The Recycle Bin is full: delete permanently instead?"; owner, 2026-11-12)}; the reasons
    as recycle_bin says them ("on a network share").
    Asked once a folder, never once a photo (a Select all of photo_index is 2,672 folders, about 2 s); a UNC path and a mapped
    drive are told by their spelling and the drive's type, without reading the share. Refused, as `resolve` refuses it, over
    batches.MAX_BATCH."""
    resolved = resolve(library, selection)
    conn = library_view.opened(library)
    try:
        counted, sizes, named = _weighed(conn, resolved.ids)
        why_none = {folder_id: recycle_bin.no_bin_reason(path) for folder_id, (path, _name) in named.items()}
        binless = {folder_id for folder_id, reason in why_none.items() if reason}
        for_good, stored = _for_good(conn, resolved.ids, sizes, named, binless)
        not_binned = _not_binned(conn, resolved.ids, counted, sizes, named, binless, for_good)
    finally:
        conn.close()
    through, copy_bytes, _largest = _through(counted, sizes, binless)
    reasons = collections.Counter()
    for folder_id in binless:
        # Those whose copy a Recycle Bin would not keep go for good, once confirmed, not through this PC.
        gone, gone_bytes = not_binned.not_copied.get(folder_id, (0, 0))
        left = counted[folder_id] - (sizes.get(folder_id) or (0, 0, 0))[2] - gone
        if left > 0:
            reasons[why_none[folder_id]] += left
        through, copy_bytes = through - gone, copy_bytes - gone_bytes
    for_good, stored = for_good | not_binned.ids, stored + not_binned.stored
    copied = [photo_id for photo_id in resolved.ids if photo_id not in for_good]
    too_long = _too_long(library, copied, binless, named) if through else 0
    names = sorted(paths.row_name(each) for each in stored)[:MOST_FOR_GOOD_NAMED]
    return {"total": len(resolved.ids), "token": token_of(resolved.ids, for_good), "folders": len(counted),
            "through_this_pc": through, "for_good": {"count": len(for_good), "names": names},
            "reasons": [{"reason": reason, "photos": photos} for reason, photos in reasons.most_common()],
            "copy_bytes": copy_bytes, "restores_to": recycle_bin.mirror_root(), "too_long": too_long,
            "no_room": not_binned.no_room, "bin_full": not_binned.full}


def _through(counted, sizes, binless):
    """(how many photos of the folders `binless` go through this PC, the bytes of their copies, the largest), by the index's
    sizes (`counted` and `sizes` _weighed's): a clip over recycle_bin.MOST_CLIP_BYTES makes no copy and is counted in none."""
    through = copy_bytes = largest = 0
    for folder_id in binless:
        total, biggest, clips_over = sizes.get(folder_id) or (0, 0, 0)
        through += max(counted[folder_id] - clips_over, 0)
        copy_bytes += total
        largest = max(largest, biggest)
    return through, copy_bytes, largest


#: What the Recycle Bins do with a Delete's files that would go to one (_not_binned): `ids`, those a Bin would not keep when
#: their turn comes (deleted for good once confirmed); `stored`, the stored paths of the first MOST_FOR_GOOD_NAMED of them by
#: path; `full`, how many only because a Bin is full (recycle_bin.BIN_FULL); `no_room`, the sentence when no copy can be made
#: here at all, or None; `not_copied`, {folder id of `binless`: (its files among `ids`, their bytes)}.
NotBinned = collections.namedtuple("NotBinned", "ids stored full no_room not_copied")


def _not_binned(conn, photo_ids, counted, sizes, named, binless, for_good):
    """NotBinned of a Delete of `photo_ids`, in the order its start deletes them, each Recycle Bin asked of Windows now. A file
    of a folder with a Bin of its own goes to the Bin of its volume; one of `binless` through this PC, its copy to the Bin of
    the copies' volume, which may be the same Bin (a clip over recycle_bin.MOST_CLIP_BYTES makes none; `for_good`, _for_good's,
    go to no Bin). Each Bin's files are weighed all at once first (recycle_bin.copies_not_kept: one query, and no more when they
    all fit); when they do not, one after another in the start's order by the index's sizes (recycle_bin.kept_in_turn), as the
    start's delete of each weighs it: the Bin keeps each while it has room, and the others are these. So the question tells,
    and its token covers, what the start does. A copies' Bin Windows cannot tell of is `no_room`; a drive's own is left out
    (the delete of each of its files asks again, and refuses it untouched)."""
    bins = {}       # {volume key: [a folder to ask its Bin by, [folder ids]]}
    no_room = None
    through, copy_bytes, _largest = _through(counted, sizes, binless)
    if through:
        no_room = recycle_bin.cannot_make_copies(copy_bytes)
        if not no_room:
            root = recycle_bin.mirror_root()
            try:
                bins.setdefault(recycle_bin.bin_volume(root), [root, []])[1].extend(binless)
            except (OSError, AttributeError, ValueError) as e:
                no_room = recycle_bin.cannot_tell(e)
    for folder_id, (path, _name) in named.items():
        if folder_id in binless:
            continue
        try:
            bins.setdefault(recycle_bin.bin_volume(path), [path, []])[1].append(folder_id)
        except (OSError, AttributeError, ValueError):
            continue
    ids, not_copied, stored, full = set(), {}, [], 0
    for folder, folder_ids in bins.values():
        weighed = [sizes.get(folder_id) or (0, 0, 0) for folder_id in folder_ids]
        try:
            if recycle_bin.copies_not_kept(sum(each[0] for each in weighed), max(each[1] for each in weighed), folder,
                                           fresh=True) is None:
                continue
            files = store_folders.sizes_in(conn, folder_ids, recycle_bin.MOST_CLIP_BYTES, photo_ids)
            order = [photo_id for photo_id in photo_ids if photo_id in files and photo_id not in for_good]
            turns = recycle_bin.kept_in_turn([files[photo_id][0] for photo_id in order], folder)
        except (OSError, AttributeError, ValueError) as e:
            if binless.intersection(folder_ids):
                no_room = recycle_bin.cannot_tell(e)
            continue
        for photo_id, refused in zip(order, turns):
            if refused is None:
                continue
            size, path, folder_id = files[photo_id]
            ids.add(photo_id)
            stored.append(path)
            full += refused == recycle_bin.BIN_FULL
            if folder_id in binless:
                count, byte_count = not_copied.get(folder_id, (0, 0))
                not_copied[folder_id] = (count + 1, byte_count + size)
    return NotBinned(ids, sorted(stored)[:MOST_FOR_GOOD_NAMED], full, no_room, not_copied)


def _weighed(conn, photo_ids):
    """({folder id: photos of `photo_ids` in it}, {folder id: (their bytes, the largest)}, {folder id: (path, name)}): what
    where_deleted and deleted_for_good ask of each folder."""
    counted = store_folders.counts(conn, photo_ids, None, ())
    sizes = store_folders.bytes_by_folder(conn, recycle_bin.MOST_CLIP_BYTES, photo_ids, None, ())
    return counted, sizes, store_folders.described(conn, counted)


def _for_good(conn, photo_ids, sizes, named, binless):
    """({the ids of `photo_ids` a Delete deletes for good once confirmed: the clips over recycle_bin.MOST_CLIP_BYTES, on any
    drive, and the files on a drive whose Recycle Bin would not keep them -- larger than it keeps, or a Bin set to delete at
    once, recycle_bin.keeps_up_to --, by the index's sizes}, the stored paths of the first MOST_FOR_GOOD_NAMED of each kind and
    Bin). A folder of `binless` goes through this PC instead of a Bin of its own; one whose Bin Windows cannot tell of is left
    out of the second (the delete of each of its files asks again, and refuses it untouched). Each drive's Bin is asked once
    (recycle_bin.keeps_up_to_by_folder)."""
    not_kept = collections.defaultdict(list)        # {what a Bin keeps: [folder ids with a file over it]}
    looked = {folder_id: path for folder_id, (path, _name) in named.items() if folder_id not in binless}
    keeps = recycle_bin.keeps_up_to_by_folder(looked.values())
    for folder_id, path in looked.items():
        kept = keeps[path]
        if kept is None:
            continue
        if not recycle_bin.would_keep((sizes.get(folder_id) or (0, 0, 0))[1], kept):
            not_kept[kept].append(folder_id)
    found, stored = set(), []
    holding = [folder_id for folder_id, (_total, _largest, clips) in sizes.items() if clips]
    if holding:
        found, stored = store_folders.clips_over(conn, holding, recycle_bin.MOST_CLIP_BYTES, MOST_FOR_GOOD_NAMED, photo_ids,
                                                 None, ())
        found = set(found)
    for kept, folder_ids in not_kept.items():
        ids, first = store_folders.larger_than(conn, folder_ids, kept, recycle_bin.MOST_CLIP_BYTES, MOST_FOR_GOOD_NAMED,
                                               photo_ids, None, ())
        found.update(ids)
        stored.extend(first)
    return found, stored


def deleted_for_good(library, photo_ids):
    """The ids of `photo_ids` a Delete would delete for good now (_for_good, _not_binned), asked again at its start: a start
    confirmed for good goes on only when these are the files its question named (token_of), and deletes for good none but these.
    The Bins' sizes are asked of Windows again, as the question asked them: a Bin emptied since the question is seen (and the
    start refused, to be asked again), never deleted into for good from a size remembered; each file of the run is then weighed
    by this reading and what the run sent since (recycle_bin.delete_file's `fresh`)."""
    conn = library_view.opened(library)
    try:
        counted, sizes, named = _weighed(conn, photo_ids)
        binless = {folder_id for folder_id, (path, _name) in named.items() if recycle_bin.no_bin_reason(path)}
        found = _for_good(conn, photo_ids, sizes, named, binless)[0]
        return found | _not_binned(conn, photo_ids, counted, sizes, named, binless, found).ids
    finally:
        conn.close()


def paths_of(library, photo_ids):
    """{photo id: its path, as this machine places it} of the photos of `photo_ids` the library has (ids and paths only): what a
    resolved selection's photos are written as (tagpup.services.bulk_edit)."""
    conn = library_view.opened(library)
    try:
        return store.paths_of(conn, photo_ids)
    finally:
        conn.close()


def _too_long(library, photo_ids, binless, named):
    """How many photos of `photo_ids` in the folders `binless` would have a copy's path too long for the Recycle Bin (#704): their
    paths read (ids and paths only), only when some go through this PC."""
    folders = {paths.key(named[folder_id][0]) for folder_id in binless}
    found = paths_of(library, photo_ids)
    root = recycle_bin.mirror_root()
    return sum(1 for path in found.values()
               if paths.key(os.path.dirname(path)) in folders and recycle_bin.too_long(recycle_bin.mirror_of(path, root)))


def _refuse_if_large(count):
    why = file_changes.too_many(count, "are selected")     # the one cap and its sentence
    if why:
        raise Refused(why)


def _by_id(conn, selection):
    """`selection`, when it names photos by path, as the ids of the photos whose rows are at those paths now, in the order
    named, each once (store.roots.photo_ids_at, one seek of the path index a path: the one lookup of a row by its path, any
    spelling); a path no row has is in none. Any other selection as it is."""
    if selection.paths is None:
        return selection
    return Selection(tuple(dict.fromkeys(store_roots.photo_ids_at(conn, selection.paths))), None, (), None)


def resolve(library, selection):
    """The photos `selection` holds, as Resolved: those that exist now, in order -- an ids or paths selection in the order named,
    a source in `/api/library/ids`'s order -- each once, at most batches.MAX_BATCH once the excluded are out (else Refused). The
    photo that exists is decided here, by the library's own rows: a path no row has is `missing`, as an id nobody has, and a
    photo re-indexed since (a new id at its path) is another photo, so a delete's start refuses it (bulk_edit.refuse_if_changed).
    A source is read in one transaction, so a library changing meanwhile gives a list that was true at one moment."""
    conn = library_view.opened(library)
    try:
        if selection.paths is not None:
            _refuse_if_large(len(selection.paths))
            ids = list(_by_id(conn, selection).ids)
            return Resolved(ids, len(selection.paths), len(selection.paths) - len(ids), 0)
        if selection.source is None:
            _refuse_if_large(len(selection.ids))
            there = store.existing_ids(conn, selection.ids)
            ids = [photo_id for photo_id in selection.ids if photo_id in there]
            return Resolved(ids, len(selection.ids), len(selection.ids) - len(ids), 0)
        # The limit is of the photos left once the excluded are out ("everything but these 63,500" is 4,500 photos).
        found, total = store.source_ids(conn, selection.source, batches.MAX_BATCH + len(selection.excluded))
    finally:
        conn.close()
    left_out = set(selection.excluded)
    ids = [photo_id for photo_id in dict.fromkeys(found) if photo_id not in left_out]
    _refuse_if_large(total - len(found) + len(ids))
    return Resolved(ids, total, 0, len(found) - len(ids))
