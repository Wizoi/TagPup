"""The people a library knows.

A person is told to the pages as `{"id", "name", "tag"}` (tagpup.store.person_ids.Directory): the id stands for the
whole tag path and the name is its leaf, shown and never the identity. Every answer that carries a person carries that
as `person`, beside the name it has always held, which is unchanged. `person` is None for a name no person tag has and
for a name two people are called (a name alone cannot say which; a typed one is asked "which one?" by path).
"""
from tagpup.core import vocabulary
from tagpup.core.result import NotFound, Refused
from tagpup.store import db, faces, person_ids, taxonomy


def translate(problem):
    """Raise what a web route answers for a person_ids.PersonProblem: NotFound (404) for an id that is no person -- merged or
    deleted in another window, never made again -- and Refused (400) for a name two people have or a group, whose sentence
    names the candidates. Called from an `except` block."""
    if isinstance(problem, person_ids.StalePerson):
        raise NotFound(str(problem)) from None
    raise Refused(str(problem)) from None


def resolve(library, ref):
    """The Person `ref` is -- an id, a tag path or a bare name (tagpup.store.person_ids.resolve) -- read now, or None for a name
    no person is filed under. NotFound for a stale id, Refused for a name two people have (the sentence names them) or a group."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return person_ids.resolve(conn, ref)
    except person_ids.PersonProblem as problem:
        translate(problem)
    finally:
        conn.close()


def for_reading(library, person):
    """`person` as a READ may ask for them: the id of their node when it is a name one person has (or a path); a bucket as it is.
    None for a name no person is called (a name is never a person by itself: the answer is nobody's, and empty). NotFound for an
    id that is nobody's, Refused for a group and for a name two people have (the sentence names their paths: the page asks which
    one). Nothing is created."""
    if isinstance(person, str) and person in vocabulary.BUCKETS.values():
        return person
    found = resolve(library, person)
    return found.id if found else None


def tags_by_id(library):
    """{id: tag} of everyone the tree files as a person, read now: the tag a person is written as."""
    return {record["id"]: record["tag"] for record in records(library, include_hidden=True)}


def directory(library):
    """The library's people as the pages are told of them (person_ids.Directory), read now from its tree.
    A library with no tree has none."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return person_ids.Directory.read(conn)
    finally:
        conn.close()


def annotate(library, items, into="person"):
    """`items` (dicts) each with the `person` their `person_id` is. Returns `items`. Read apart from the answer, so an answer a
    cache holds is not given what the tree said when it was made: annotate a copy, after the cache."""
    return directory(library).annotate(items, into)


def annotate_suggestions(library, status):
    """A Suggest run's status (tagpup.jobs.suggestions.status), each person it offered given the `person` its name is.
    The status is a copy made for this answer; what a run keeps in memory or the library holds only names, which a
    rename of a person's tag must not leave out of date. Returns `status`."""
    found = status.get("suggestions") if isinstance(status, dict) else None
    chips = [chip for entry in (found or {}).values() if isinstance(entry, dict)
             for chip in entry.get("people") or [] if isinstance(chip, dict)]
    if chips:
        # The suggester writes the person's TAG PATH in `name` (suggester.py: item["tag"]); a bare name too.
        everyone = directory(library)
        for chip in chips:
            chip["person"] = everyone.of_reference(chip.get("name"))
    return status


def records(library, include_hidden=False):
    """Everyone with a person tag, as the pages are told of them (/api/people, both apps), alphabetical
    (vocabulary.tag_sort_key) and by group for those that share a leaf; a person whose node is hidden from autocomplete, or
    sits in a hidden branch, is left out unless `include_hidden`: that answers "does this person exist?", which a hidden
    person does."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        found = person_ids.Directory.read(conn).records()
        hidden = set() if include_hidden else taxonomy.hidden_tags(conn)
    finally:
        conn.close()
    return [each for each in found if not vocabulary.hidden_by(each["tag"], hidden)]


def with_counts(library):
    """[{"name", "count", "person", "person_id"}]: everyone with a named face, and how many, for Review People: a person
    is the node, so two people called alike are two rows (their `person` says which).
    A person is left out when every node the tree files them under is hidden.

    Deliberately people only. An "Unmatched" pseudo-person used to be pinned at the
    top, which dropped a flat dump of every nameless face into a mode meant for
    auditing named people -- unordered and far too large to work through. Nameless
    faces belong to the Identify Faces queue (docs/findings.md, #49).
    """
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        hidden_tags = taxonomy.hidden_tags(conn)
        counted = faces.counts_by_person(conn)
        everyone = person_ids.Directory.read(conn)
    finally:
        conn.close()
    listed = []
    for ref, count in counted:
        name = ref.name
        person = everyone.of_id(ref.id)
        if person and vocabulary.hidden_by(person["tag"], hidden_tags):
            continue
        listed.append({"name": name, "count": count, "person": person, "person_id": person["id"] if person else None})
    # Most faces first, and the alphabet for those that tie: the order of the rows the query
    # grouped is nobody's.
    listed.sort(key=lambda each: (-each["count"], vocabulary.tag_sort_key(each["name"])))
    return listed
