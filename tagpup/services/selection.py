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

from tagpup.core import vocabulary
from tagpup.core.result import Refused
from tagpup.services import library_view
from tagpup.store import library_view as store

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


def tally(library, selection):
    """What the photos of `selection` hold: {"total" (the photos that exist), "tags": [{"tag", "count"}], "people":
    [{"name", "count"}], "more_tags", "more_people"} -- the tags and people the selection carries, each with the number of
    its photos, tags alphabetically by the shared order (vocabulary.tag_sort_key) and people by it too, at most MAX_TALLIED
    of each (the rest are counted in `more_*`, the most used kept). From photo_tags and photo_people, one grouped read over
    the selection: a source is joined in SQL and its excluded ids taken out there, so 68,000 photos are no list in
    Python and no request of that size. A tag no node of the tree holds is not in photo_tags and so not in it (the
    navigator's counts leave it out too). Refused as `resolve` refuses a selection, but NOT for being larger than a job
    takes: the panel may tally a whole library."""
    conn = library_view.opened(library)
    try:
        found = store.tally(conn, selection.ids, selection.source, selection.excluded)
    finally:
        conn.close()
    tags = _kept([(tag, count) for tag, count in found["tags"]])
    people = _kept(found["people"])
    return {"total": found["total"],
            "tags": [{"tag": tag, "count": count} for tag, count in tags[0]], "more_tags": tags[1],
            "people": [{"name": name, "count": count} for name, count in people[0]], "more_people": people[1]}


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
