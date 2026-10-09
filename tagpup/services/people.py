"""The people a library knows.

A person is told to the pages as `{"id", "name", "tag", "group", "shared"}` (tagpup.store.person_ids.Directory):
`shared` says another person has the same leaf and `group` the tail of the path that tells them apart, so a
page can label "Sam \u00b7 Thackeray" (web/common/vocabulary.js personLabel) without deciding either. Every
answer that carries a person carries that as `person`, beside the name it has always held, which is unchanged
(identity by id, stage 2, part A; the pickers read it from part C). `person` is None for a name no person tag
has, and has no id (and no tag) for a name two people are called.
"""
from tagpup.core import vocabulary
from tagpup.store import db, faces, people, person_ids, taxonomy


def names(library, keywords_too=False, include_hidden=False):
    """The people the pages offer while a name is typed (tagpup.store.people.names):
    TagPup's are the names given to faces; TagTuner's add the people keywords name."""
    return people.names(library.path, keywords_too=keywords_too, include_hidden=include_hidden)


def directory(library):
    """The library's people as the pages are told of them (person_ids.Directory), read now from its tree.
    A library with no tree has none."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return person_ids.Directory.read(conn)
    finally:
        conn.close()


def annotate(library, items, key="name", into="person"):
    """`items` (dicts) each with the `person` their `key` names, for an answer built from rows that carry only a
    name. Returns `items`. Read apart from the answer, so an answer a cache holds is not given what the tree said
    when it was made: annotate a copy, after the cache."""
    return directory(library).annotate(items, key, into)


def annotate_suggestions(library, status):
    """A Suggest run's status (tagpup.jobs.suggestions.status), each person it offered given the `person` its name is.
    The status is a copy made for this answer; what a run keeps in memory or the library holds only names, which a
    rename of a person's tag must not leave out of date. Returns `status`."""
    found = status.get("suggestions") if isinstance(status, dict) else None
    chips = [chip for entry in (found or {}).values() if isinstance(entry, dict)
             for chip in entry.get("people") or [] if isinstance(chip, dict)]
    if chips:
        annotate(library, chips)
    return status


def records(library, include_hidden=False):
    """Everyone with a person tag, as the pages are told of them, alphabetical (vocabulary.tag_sort_key) and by group
    for those that share a leaf; a person every node the tree files them under is hidden from autocomplete leaves
    out, unless `include_hidden` (names, above, does the same)."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        found = person_ids.Directory.read(conn).records()
        hidden = set() if include_hidden else taxonomy.hidden_tags(conn)
    finally:
        conn.close()
    return [each for each in found if not vocabulary.hidden_by(each["tag"], hidden)]


def with_counts(library):
    """[{"name", "count", "person"}]: everyone with a named face, and how many, for Review People.
    A person is left out when every node the tree files them under is hidden.

    Deliberately people only. An "Unmatched" pseudo-person used to be pinned at the
    top, which dropped a flat dump of every nameless face into a mode meant for
    auditing named people -- unordered and far too large to work through. Nameless
    faces belong to the Identify Faces queue (docs/findings.md, #49).
    """
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        hidden_tags = taxonomy.hidden_tags(conn)
        # Where the tree files everyone, in one read: it was a query per person (#50).
        filed = taxonomy.filed_people(conn)
        counted = faces.counts_by_name(conn)
        everyone = person_ids.Directory.read(conn)
    finally:
        conn.close()
    listed = []
    for name, count in counted:
        tag_paths = filed.get(name, [])
        if tag_paths and all(vocabulary.hidden_by(path, hidden_tags) for path in tag_paths):
            continue
        listed.append({"name": name, "count": count, "person": everyone.of_name(name)})
    # Most faces first, and the alphabet for those that tie: the order of the rows the query
    # grouped is nobody's.
    listed.sort(key=lambda each: (-each["count"], vocabulary.tag_sort_key(each["name"])))
    return listed
