"""The names to review: a name that faces or photos' people hold and no person's tag is (docs/ARCHITECTURE.md, "People by id, stage 2",
"Names to review"; docs/findings.md, #985).

The owner settles these by hand, one at a time. **Nothing here runs by itself and nothing is converted on open**: `entries` only reads,
a choice is a call of `resolve` the owner asked for, and each is ONE journaled change of the faces it touched (History undoes it),
counted before it is applied (`apply=False` is the rehearsal and writes nothing).

The list is a derived view of `person_ids.review_pairs` -- the names of faces and photo_people that hold no id -- read when asked and
never kept, less the names the owner set aside (`name_review_dismissals`, a name returns when it holds more rows than it did).
A group tag is not a name to settle (#986): a listed-only row naming one is the rebuild's (`tools/doctor.py --rebuild-derived`),
counted as `stale_group_rows`; a FACE named after a group is an entry ("branch").

Choices, for an entry (`why`: "none" no person tag has the name, "several" two or more do, "one" exactly one does but these rows are
not linked to them, "branch" a group has it):
- `make`: a person tag is made under the group the owner picks (`group_id`); the name's faces and listed people take its id. Only for
  "none". The tag is the group's path and the name: the tag editor's create.
- `link`: the name's rows are the person `person_id` (a person's id, labelled with their group): they take that id and that person's name.
  For any entry. When the person is called otherwise, the photos' files keep the old keyword: `keywords_kept` says how many photos.
- `unname`: the name's faces become unnamed, to be identified again (not "nobody": name_source is NULL, as a person's forced deletion
  leaves them); the photos' keywords are untouched. Only when faces hold the name.
- `dismiss` / `restore`: set the name aside until it gains rows / show it again. Not a change of a photo or face; not journaled.

The name is told to the page; the MCP and the doctor give counts, and names only on request (reveal).
"""
import difflib

from tagpup.core import validation, vocabulary
from tagpup.core.result import NotFound, Refused, Result
from tagpup.services import people as people_service
from tagpup.store import db, faces, journal, person_ids, taxonomy
from tagpup.store import name_review as store

#: The choices.
MAKE, LINK, UNNAME, DISMISS, RESTORE = "make", "link", "unname", "dismiss", "restore"
ACTIONS = (MAKE, LINK, UNNAME, DISMISS, RESTORE)

#: How many people an entry offers as "it could mean", and how close a spelling must be.
CANDIDATES, CLOSE = 5, 0.78


def _rows(review):
    return review.faces + review.listed


def _read(library):
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        reviews = person_ids.review_pairs(conn)
        aside = store.dismissed(conn)
        directory = person_ids.Directory.read(conn)
        groups = store.groups(conn)
        extra = {review.key: person_ids.samples(conn, review.key) for review in reviews}
    finally:
        conn.close()
    return reviews, aside, directory, groups, extra


def _candidates(review, directory):
    """The people this name could mean: every person called it (several, one), else those with a close spelling (none)."""
    everyone = directory.records()
    same = [person for person in everyone if vocabulary.key(person["name"]) == review.key]
    if same:
        return same
    names = {}
    for person in everyone:
        names.setdefault(vocabulary.key(person["name"]), []).append(person)
    close = difflib.get_close_matches(review.key, list(names), n=CANDIDATES, cutoff=CLOSE)
    return [person for each in close for person in names[each]][:CANDIDATES]


def _entry(review, directory, aside, extra):
    face_ids, photo_ids = extra.get(review.key, ([], []))
    rows = _rows(review)
    return {"key": review.key, "name": review.name, "why": review.why, "faces": review.faces, "faces_by_hand": review.by_hand,
            "listed": review.listed, "keyword_photos": review.from_keyword, "rows": rows, "spellings": review.spellings,
            "face_ids": face_ids, "photo_ids": photo_ids, "candidates": _candidates(review, directory),
            "dismissed": review.key in aside and rows <= aside[review.key]}


def entries(library, include_dismissed=False):
    """What the owner has to settle in `library`, read now: {"entries": [...], "count": entries shown, "dismissed": entries set
    aside, "groups": [the places a person can be made], "stale_group_rows": listed rows naming a group tag}. An entry is
    {"key", "name", "why", "faces", "faces_by_hand", "listed", "keyword_photos", "rows", "spellings", "face_ids", "photo_ids",
    "candidates": [person], "dismissed"}; the name and the faces' ids are the page's to show. A group tag's listed-only rows are not
    entries (the rebuild's). Reads only."""
    reviews, aside, directory, groups, extra = _read(library)
    shown, hidden, stale = [], 0, 0
    for review in reviews:
        if review.why == "branch" and not review.faces:
            stale += review.listed
            continue
        entry = _entry(review, directory, aside, extra)
        if entry["dismissed"]:
            hidden += 1
            if not include_dismissed:
                continue
        shown.append(entry)
    return {"entries": shown, "count": sum(1 for each in shown if not each["dismissed"]), "dismissed": hidden, "groups": groups,
            "stale_group_rows": stale}


def count(library):
    """How many names wait for the owner (the number Review People, Activity, the doctor and the MCP show): the entries not set
    aside. A library that cannot be read raises, it never says none."""
    return entries(library)["count"]


def _sentence(action, review, person=None, group=None, made=None):
    name, faces, listed = review.name, review.faces, review.listed
    rows = "%d face(s) and %d listed person(s)" % (faces, listed)
    if action == MAKE:
        return "Make %s under %s: %s would be that person." % (made, group["tag"], rows)
    if action == LINK:
        return "Link %s to %s: %s would be that person." % (name, person.tag, rows)
    if action == UNNAME:
        return ("Unname %d face(s) called %s: they go back to the faces waiting for a name. The photos' keywords stay"
                " (%d listed from a keyword)." % (faces, name, review.from_keyword))
    return "Set %s aside until it holds more than %d row(s)." % (name, faces + listed)


def resolve(library, key, action, person_id=None, group_id=None, apply=False):
    """The owner's choice for the name whose key is `key` (as `entries` gives it): `action` one of ACTIONS, with `person_id` (link) or
    `group_id` (make). A rehearsal unless `apply`: counts and the sentence that says what would change, nothing written. Applied, it
    is ONE change of the journal (the faces touched; History undoes it) in one transaction that reads the name, the tree and the
    person again under the write lock -- so a name settled in another window, a person merged meanwhile or a group removed is
    refused, never half-done. NotFound (404) for a person or group that is no longer there; Refused (400) for a choice that does not
    fit the entry. details: `sentence`, `faces`, `listed`, `applied`; applied also `change` (the journal's id; None for dismiss/restore),
    `keywords_kept` (link to a person called otherwise), `undo` (how)."""
    if action not in ACTIONS:
        raise Refused("Choose make, link, unname or dismiss.")
    key = vocabulary.key(key)
    if not key:
        raise Refused("Name the entry to settle.")
    if not apply:
        return _settle(library, key, action, person_id, group_id, None)
    return db.write_with_connection(library.path, lambda conn: _settle(library, key, action, person_id, group_id, conn),
                                    label="names to review: %s" % action)


def _settle(library, key, action, person_id, group_id, conn):
    """Decide, and with `conn` (a write connection) do, one choice. The same function rehearses (a read-only connection of its own)
    and applies, so the sentence and the counts the owner is shown are the ones that are written."""
    result, own = Result(attempted=1), None
    if conn is None:
        own = conn = db.connect(db.readonly_uri(library.path), uri=True)
    else:
        if not conn.in_transaction:
            db.begin(conn, immediate=True)
    try:
        review = next(iter(person_ids.review_pairs(conn, key)), None)
        known = person_ids.read(conn)
        if action == RESTORE:
            result.details.update(sentence="Show this name again.", faces=0, listed=0, applied=conn is not own, change=None)
            if conn is not own:
                result.changed = store.forget(conn, key)
            return result
        if review is None:
            result.refuse("Nothing is left to settle for that name: it was settled in another window. Reload the list.")
            return result
        result.details.update(faces=review.faces, listed=review.listed, applied=False, change=None, keywords_kept=0)
        writing = conn is not own
        person = group = None
        if action == LINK:
            try:
                person = known.person(person_id) if isinstance(person_id, int) and not isinstance(person_id, bool) else None
            except person_ids.PersonProblem as problem:
                people_service.translate(problem)
            if person is None:
                raise NotFound("Choose the person this name is (reload the page if they were just removed).")
            if vocabulary.key(person.name) != key:
                result.details["keywords_kept"] = review.from_keyword
        elif action == MAKE:
            if review.why != "none":
                result.refuse("%s is already a name a person tag has: link the name to that person." % review.name
                              if review.why != "branch" else "%s is a group of people, not a person to make." % review.name)
                return result
            group = store.group(conn, group_id) if isinstance(group_id, int) and not isinstance(group_id, bool) else None
            if group is None:
                raise NotFound("Choose the group the person goes under (reload the page if it was just removed).")
            problem = validation.problem("name", review.name)
            if problem:
                result.refuse(problem)
                return result
            made = vocabulary.SEPARATOR.join([group["tag"], review.name])
            if conn.execute("SELECT 1 FROM tag_taxonomy WHERE tag = ?", (made,)).fetchone():
                result.refuse("%s is already a tag in the tree." % made)
                return result
        elif action == UNNAME and not review.faces:
            result.refuse("No face holds %s: nothing to unname (the photos list it from their keywords)." % review.name)
            return result
        result.details["sentence"] = _sentence(action, review, person, group,
                                               vocabulary.SEPARATOR.join([group["tag"], review.name]) if group else None)
        if not writing:
            return result
        return _write(library, conn, result, review, action, person, group)
    finally:
        if own is not None:
            own.close()


def _write(library, conn, result, review, action, person, group):
    if action == DISMISS:
        result.changed = store.dismiss(conn, review.key, _rows(review))
        if not result.changed:
            result.details["applied"] = False
            result.refuse("This library has not been brought up to date to remember set-aside names yet: start the newest TagPup.")
            return result
        result.details.update(applied=True, undo="Show the set-aside names again from the list.")
        return result
    face_ids = person_ids.unlinked_faces(conn, review.name) if len(review.spellings) == 1 else [
        face_id for name in review.spellings for face_id in person_ids.unlinked_faces(conn, name)]
    before = journal.read_faces(conn, set(face_ids))
    if action == LINK:
        changed = person_ids.link_to(conn, review.key, person)
        operation = journal.PERSON_LINKED
    elif action == MAKE:
        tag = vocabulary.SEPARATOR.join([group["tag"], review.name])
        taxonomy.refuse_child_of_person(conn, tag)
        taxonomy.add_node(conn, tag)   # the tree edit links the rows of a name nobody had (person_ids.follow_tree)
        taxonomy.forget_people_paths(library.path)
        made = person_ids.read(conn).by_tag.get(vocabulary.normalize(tag).lower())
        changed = person_ids.faces_using(conn, [made.id]).get(made.id, 0) if made else 0
        result.details["person_tag"] = tag
        operation = journal.PERSON_MADE
    else:
        changed = faces.unname(conn, set(face_ids), source=None) if face_ids else 0
        operation = journal.NAME_UNNAMED
    result.changed = changed
    result.details["change"] = journal.record_faces(conn, operation, before)
    store.forget(conn, review.key)
    result.details.update(applied=True, undo=("Undo it in History: the faces return as unresolved names"
                                              + ("; the person's tag stays in the tree." if action == MAKE else ".")))
    return result

