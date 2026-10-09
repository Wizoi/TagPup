"""The people a library knows.

A person is told to the pages as `{"id", "name", "tag", "group", "shared"}` (tagpup.store.person_ids.Directory):
`shared` says another person has the same leaf and `group` the tail of the path that tells them apart, so a
page can label "Sam \u00b7 Thackeray" (web/common/vocabulary.js personLabel) without deciding either. Every
answer that carries a person carries that as `person`, beside the name it has always held, which is unchanged
(identity by id, stage 2, part A; the pickers read it from part C). `person` is None for a name no person tag
has, and has no id (and no tag) for a name two people are called.
"""
from tagpup.core import vocabulary
from tagpup.core.result import NotFound, Refused
from tagpup.store import db, faces, people, person_ids, taxonomy


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
    """`person` as a READ may ask for them: the id of their node when it is a name one person has (or a path), the name as it
    is for one no node is, a `SharedName` for a name two or more people have -- the union, which is what the name always
    showed; nothing is created or linked --; a bucket or an id is as it is. NotFound for an id that is nobody's, Refused for a
    group. Writes never come through here: they refuse a shared name (resolve)."""
    if isinstance(person, str) and person in vocabulary.BUCKETS.values():
        return person
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        found = person_ids.resolve(conn, person)
    except person_ids.AmbiguousPerson:
        return person_ids.SharedName(str(person).strip())
    except person_ids.PersonProblem as problem:
        translate(problem)
    finally:
        conn.close()
    return found.id if found else person


def link_name(library, name, apply=False):
    """Link every face and listed person called `name` that is linked to nobody to the one person that name is: the OWNER's action
    for a name that is one person's but whose rows were never linked (tagpup.store.person_ids.unresolved, `one`), since nothing
    links one by itself. A dry run unless `apply`; counts only. Applied: one journaled change of the faces, in the transaction that
    links them (History's undo returns them to unresolved names). Refused, naming the candidates, for a name two people have;
    refused for a name nobody is called or a group. details: `faces_by_hand`, `faces_by_guess`, `listed`, `applied`; applied:
    `change`."""
    from tagpup.core.result import Result
    from tagpup.store import journal
    result = Result(attempted=1)
    name = str(name or "").strip()
    if not name:
        result.refuse("Name the person to link.")
        return result
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        try:
            person = person_ids.read(conn).person(name)
        except person_ids.PersonProblem as problem:
            translate(problem)
        if person is None:
            result.refuse("No person is called %s: there is no one to link the name to." % name)
            return result
        counts = person_ids.unlinked_counts(conn, name)
    finally:
        conn.close()
    result.details.update(faces_by_hand=counts[0], faces_by_guess=counts[1], listed=counts[2], applied=False)
    if not apply or not any(counts):
        return result

    def link(connection):
        known = person_ids.read(connection)
        if known.person(name) is None:
            return None
        before = journal.read_faces(connection, person_ids.unlinked_faces(connection, name))
        changed = person_ids.link_added(connection, {vocabulary.key(name)}, known)
        return changed, journal.record_faces(connection, journal.PERSON_LINKED, before)

    try:
        done = db.write_with_connection(library.path, link, label="link a name to its person")
    except person_ids.PersonProblem as problem:
        translate(problem)
    if done is None:
        result.refuse("No person is called %s now." % name)
        return result
    result.changed = done[0]
    result.details.update(applied=True, change=done[1])
    return result


def tags_by_id(library):
    """{id: tag} of everyone the tree files as a person, read now: the tag a person is written as."""
    return {record["id"]: record["tag"] for record in records(library, include_hidden=True)}


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
        # The suggester writes the person's TAG PATH in `name` (suggester.py: item["tag"]); a bare name too.
        everyone = directory(library)
        for chip in chips:
            chip["person"] = everyone.of_reference(chip.get("name"))
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
        # Where the tree files everyone, in one read: it was a query per person (#50).
        filed = taxonomy.filed_people(conn)
        counted = faces.counts_by_person(conn)
        everyone = person_ids.Directory.read(conn)
    finally:
        conn.close()
    listed = []
    for ref, count in counted:
        name = ref.name
        person = everyone.of_row(ref.id, name)
        tag_paths = [person["tag"]] if person and person["id"] is not None else filed.get(name, [])
        if tag_paths and all(vocabulary.hidden_by(path, hidden_tags) for path in tag_paths):
            continue
        listed.append({"name": name, "count": count, "person": person, "person_id": person["id"] if person else None})
    # Most faces first, and the alphabet for those that tie: the order of the rows the query
    # grouped is nobody's.
    listed.sort(key=lambda each: (-each["count"], vocabulary.tag_sort_key(each["name"])))
    return listed
