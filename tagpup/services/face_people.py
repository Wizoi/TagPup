"""A face's name and the person on the photo's keywords, kept in step (#861; docs/ARCHITECTURE.md,
"Faces on the photo").

They are one decision. A face named and the person absent from the photo's keywords is the drift the
owner saw ("a green box and No people tags"); a person left on the photo after the face was said not to
be them is the same drift the other way. So every write that names, unnames or rules out a face of an
open photo goes through here, and this is the one place that says how the keyword follows:

* **Naming** a face adds the person to the photo, the TAG FIRST: a tag that cannot be written (a file
  that cannot be read, a network share that is away, a new person with no one people root to be filed under) names no
  face, and says why. A face that cannot be named afterwards (somebody named the person on another face
  meanwhile) keeps the tag, the state the app always allowed, and the same choice names it.
* **Taking a name off** a face, ruling it out ("not important") or taking every name off a photo removes
  the person's keywords from the photo, THE FACE FIRST, unless another face of the photo still carries the
  person. The order is the other way round on purpose: the tag removed first and the face left named
  would be the drift again, where a tag left behind is a state the app has always allowed (the person's
  pill takes it off). A tag that cannot be removed leaves the face unnamed and says so.
* **Automatch** decides the names under the write lock of the faces, so the tag it writes is the one it
  decided: the faces first, and if the tag cannot be written the names it just gave are taken back
  (`faces.revert_automatic`: a face a person named meanwhile is theirs).

The tag is written by the machinery of every other keyword write (tagpup.services.tagging.change_each:
one journaled change of photo files, `undo` and History take it back, a file changed outside since it was
read is a conflict and never overwritten; more than batches.CHUNK photos are a batch, written a chunk at a time, Cancel
and a hand-over stopping it at a chunk), under the one lock of changes of photo files. TagPup's own page writes the
tag itself for a box it draws (its queue, its undo, its placement question), so its calls say
`page_writes_tags` and are told what to take off instead (`untag` in the details).

Undo of the tag change in History takes the person off and unnames the face with it (#908: unname_after_undo); a tag
taken off a photo any other way -- the pill, a selection -- does the same (unname_for_removed_tags), and the face is then a
decision, "nobody", which is the record that nothing is to name it or write its tag again. Many photos at once are
not written here: they are one batch of photo files whose photos' faces are written with each file (tagpup.services.face_assignment).
"""
import logging

from tagpup.core import fields, paths, vocabulary
from tagpup.core.result import Conflict, Refused, Result
from tagpup.services import faces as faces_service
from tagpup.services import file_changes, tagging
from tagpup.services import people as people_service
from tagpup.store import db, faces, file_journal, journal, person_ids, photos, removals, taxonomy

logger = logging.getLogger(__name__)

#: What the journal calls the changes made here (History's `operation`).
ADDED = "person added for a named face"
REMOVED = "person taken off for an unnamed face"

#: The change that unnamed the faces of a person taken off a photo (History's `operation`).
UNNAMED = removals.OPERATION

# ---- Which tag ----------------------------------------------------------------------------------------------

class Filer:
    """The tag a person is written as, by the one rule a chip and Apply All follow (`vocabulary.person_tag`): the tree
    read once, and only read -- no node is made. A person given by the id of their node is the tag of that node (None for a
    node gone). A NAME is a person the tree does not have yet (`made` and bulk_edit resolve a name a person has first, and
    refuse the one two people have), filed under the one people root; None when the tree has several: someone must choose
    (the page's placement question)."""

    def __init__(self, library):
        self.roots = taxonomy.people_filing(library.path)[1]
        self.tags_by_id = people_service.tags_by_id(library)

    def tag(self, person):
        if isinstance(person, int):
            return self.tags_by_id.get(person)
        return vocabulary.person_tag((person or "").strip(), [], self.roots)


def made(library, ref):
    """THE one maker of a new person for a face (the store's face writers make no node): `ref` (the id of a person, or a typed
    name, as faces.who_is gives it) as the id of a person that has a node: a name no person is called is given its node at the
    path the filer chooses -- the tag first, as the New person dialog makes it (every tag has an id, owner 2026-10-10) -- and
    the node's id is returned. Refused for a name two people have (the sentence names their paths), one the filer cannot place,
    or one that would go under a person that faces or photos use; nothing is made then."""
    if isinstance(ref, int):
        return ref
    found = people_service.resolve(library, ref)
    if found is not None:
        return found.id
    tag = Filer(library).tag(ref)
    if tag is None:
        raise Refused(not_filed(ref))

    def make(conn):
        taxonomy.refuse_child_of_person(conn, tag)
        return taxonomy.add_node(conn, tag, root_has_face=1)

    try:
        return db.write_with_connection(library.path, make, label="tag tree: add %s" % tag)
    except person_ids.PersonProblem as why:
        raise Refused(str(why)) from None


def not_filed(name):
    return ("The tag tree has several people roots, so it is not known where to file %s: add them to the photo from "
            "TagPup's Organize, which asks, or name the path." % name)


def already_names(tags, tag, known):
    """Do the photo's `tags` name the person `tag` is, by the id of their node (two people called alike are two)? THE rule
    for "this photo already names them" -- the keyword writer's own (tagging._change_each: vocabulary.names_same_person) --
    so that what is planned, what is skipped and what is written agree. `known` is the tree's PeopleVocabulary."""
    return any(vocabulary.names_same_person(known, tag, there) for there in tags)


def person_tags(tags, person, known):
    """The tags among `tags` that name the person whose node is `person` (an id): their node's own path, nothing else (a bare
    name is a path of its own, owner 2026-10-11). None for no id: a name alone is nobody."""
    if not isinstance(person, int) or person not in known.names:
        return []
    return [tag for tag in tags if (ref := vocabulary.person_ref_of_tag(tag, known)) is not None and ref.id == person]


# ---- The tag, written and taken off ----------------------------------------------------------------------------

def _merge(into, tag_result):
    """What a tag write did, in `into` (a Result): the files written, the records of them for the pages, the
    journal's change ids and what failed."""
    into.details["tags_written"] = into.details.get("tags_written", 0) + tag_result.changed
    into.details.setdefault("written", {}).update(tag_result.details.get("written", {}))
    change = tag_result.details.get("change")
    if change is not None:
        into.details.setdefault("changes", []).append(change)
    for what, why in tag_result.errors:
        into.fail(what, why)
    for what, why in tag_result.skipped:
        into.skip(what, why)
    if tag_result.refused and not into.refused:
        into.refuse(tag_result.refused)


class Writer:
    """Where a photo's tags are written: the ExifTool program, and `told(result)`, heard once the files are written with the
    Result of their change (details["written"] is what the pages' records are told, in the order the files were written). A
    caller whose page writes the photo's tags itself passes no Writer."""

    def __init__(self, exiftool_path, told=None):
        self.exiftool_path = exiftool_path
        self.told = told


def write_tags(library, changes, writer, operation, persons=None, stop_at_first_error=True):
    """Give each photo of `changes` ({path: (tags to add, tags to take off)}) its own, as ONE journaled change of photo files
    (tagging.change_each, through file_changes.write_fields): more than batches.CHUNK photos are a batch -- its turn taken
    before the lock (file_changes.batch_exclusively), written a chunk at a time, Cancel and a hand-over stopping it at a chunk --
    and more than batches.MAX_BATCH are refused, nothing written (the most the repair has to write in the three libraries is
    662 photos, counted 2026-10-15). The pages' records are told under the lock, in the order the files were written. Stops at
    the first photo that fails, unless `stop_at_first_error` is False (the repair: a photo that cannot be written is an error,
    and the others are written). A Result: `changed` the files written, details["written"] and `changes`, the journal's ids;
    refused, nothing written, when too many batches wait or it was cancelled before its turn."""
    result = Result(attempted=len(changes))
    try:
        with file_changes.batch_exclusively(library, operation, len(changes)):
            done = tagging.change_each(library, changes, writer.exiftool_path, operation, persons=persons,
                                       stop_at_first_error=stop_at_first_error)
            if writer.told:
                writer.told(done)
    except (Conflict, Refused) as why:
        result.refuse(str(why))
        return result
    _merge(result, done)
    result.changed = result.details.get("tags_written", 0)
    return result


def add_people(library, wanted, writer, filer=None, operation=ADDED, stop_at_first_error=True):
    """Add the person of each (photo path, person) of `wanted` to the photo's keywords, a photo given all its people in
    one write; a person is the id of their node. A Result: `changed` the files written, details `written`. A photo whose
    keywords name the person already (by the id of their node: already_names) is not read or written again.
    Refused, nothing written, for a person whose node is gone (merged or removed meanwhile)."""
    filer = filer or Filer(library)
    result = Result(attempted=len(wanted))
    held = {paths.key(path): tags for path, tags, _raw in photos.read_tags(library.path, [path for path, _name in wanted])}
    known = taxonomy.people_vocabulary(library.path)
    changes, persons = {}, {}
    for photo_path, person in wanted:
        tag = filer.tag(person)
        if tag is None:
            result.refuse(str(person_ids.StalePerson(person)))
            return result
        if already_names(held.get(paths.key(photo_path), []), tag, known):
            continue
        add = changes.setdefault(photo_path, ([], ()))[0]
        if tag not in add:
            add.append(tag)
        persons.setdefault(paths.key(photo_path), set()).add(tag)
    return write_tags(library, changes, writer, operation, persons, stop_at_first_error) if changes else result


def untag_plan(library, items):
    """{photo path: [the tags to take off]} for the (photo path, person) of `items` -- a person is the Ref a face carried --
    whose person is on no face of the photo any more -- read from the library NOW, after the faces were written -- and on whose
    keywords the person still is, in every spelling the photo holds (a tag at the person's path and a bare one are one person;
    another person called alike is not)."""
    wanted = {}
    for photo_path, person in items:
        wanted.setdefault(photo_path, []).append(person.id)
    if not wanted:
        return {}
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        carried = {photo_path: {each.id for each in faces.people_in_photo(conn, photo_path)}
                   for photo_path in wanted}
        known = taxonomy.read_people_vocabulary(conn)
    finally:
        conn.close()
    held = {paths.key(path): tags for path, tags, _raw in photos.read_tags(library.path, list(wanted))}
    plan = {}
    for photo_path, people in wanted.items():
        tags = held.get(paths.key(photo_path), [])
        for person in dict.fromkeys(people):
            if person in carried[photo_path]:
                continue
            for tag in person_tags(tags, person, known):
                if tag not in plan.setdefault(photo_path, []):
                    plan[photo_path].append(tag)
    return {photo_path: tags for photo_path, tags in plan.items() if tags}


def remove_people(library, plan, writer):
    """Take the tags of `plan` ({photo path: [tags]}, untag_plan's) off their photos, as one journaled change."""
    if not plan:
        return Result()
    return write_tags(library, {path: ((), tags) for path, tags in plan.items()}, writer, REMOVED)


def _finish_untag(library, result, items, writer):
    """After the faces were written: the tags that go with them, taken off now, or, when `writer` is None because
    the page writes the photo's tags itself, told to the page as details["untag"]. A tag that could not be taken off leaves
    the face unnamed all the same: details["tag_problem"] says why."""
    plan = untag_plan(library, items)
    result.details["untag"] = plan
    if writer is None or not plan:
        return
    removed = remove_people(library, plan, writer)
    result.details["tags_removed"] = removed.details.get("tags_written", 0)
    result.details.setdefault("written", {}).update(removed.details.get("written", {}))
    if removed.details.get("changes"):
        result.details.setdefault("changes", []).extend(removed.details["changes"])
    # The faces are written and reported as they are: a tag that stayed is said, not made an error of the faces.
    if not removed.ok:
        result.details["tag_problem"] = removed.refused or removed.message()


# ---- Naming ---------------------------------------------------------------------------------------------------

def name_face(library, face_id, person, writer):
    """Name one face AND put the person on its photo, the tag first (see the module's docstring). Everything
    faces_service.name_face refuses is refused before a tag is written. `person` is the id of the person's node, or a typed
    name (faces_service.name_face; a tag path is no name). `changed` is the faces named; details
    `tags_written` (0 when the photo named the person already) and `written`. A face that carried another person is renamed:
    that person's tags go from the photo unless another face is them, as unname_face's do. With no `writer`
    the page wrote the person's tag itself (TagPup's box and strip) and writes the old person's off itself too:
    details["untag"] says which."""
    refused = Result(attempted=1)
    found = faces_service.check_nameable(library, face_id, person, refused, guesses_yield=writer is None)
    if found is None:
        return refused
    ref, person_name = faces_service.who_is(library, person, refused)
    ref = made(library, ref)
    photo_path, was = found
    if writer is None:
        _give_back_guesses(library, photo_path, face_id, ref)
    before = _names_on(library, photo_path)
    # No writer: the page wrote the tag itself (TagPup's box and strip, first), and says so.
    tagged = add_people(library, [(photo_path, ref)], writer) if writer is not None else Result()
    if tagged.ok and tagged.details.get("tags_written"):
        _give_back_to(library, photo_path, face_id, ref, before)
    if not tagged.ok:
        # Nothing was named: the face stays as it was.
        failed = Result(attempted=1)
        failed.details.update(tagged.details)
        failed.refused = tagged.refused
        failed.errors = list(tagged.errors)
        if not failed.refused:
            failed.refused = ("%s was not added to the photo, so the face is not named: %s"
                              % (person_name, tagged.message()))
        return failed
    try:
        result = faces_service.name_face(library, face_id, ref)
    except Conflict as late:
        raise Conflict("%s The person was added to the photo." % late) from None
    result.details.update(tags_written=tagged.details.get("tags_written", 0), written=tagged.details.get("written", {}))
    if tagged.details.get("changes"):
        result.details["changes"] = tagged.details["changes"]
    if result.refused and tagged.details.get("tags_written"):
        result.refused += " The person was added to the photo."
    # A face renamed is a name taken off one person and given to another: the old person's tag goes unless another face is them.
    if result.changed and was and not is_person(was, ref):
        _finish_untag(library, result, [(photo_path, was)], writer)
    return result


def is_person(face_ref, person):
    """Is the person a face carries, `face_ref` (a Ref), the person with the id `person`?"""
    return face_ref is not None and face_ref.id == person


def _names_on(library, photo_path):
    """{face id: Ref} of the photo's faces that carry a person."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return faces.people_by_face(conn, photo_path)
    finally:
        conn.close()


def _give_back_guesses(library, photo_path, face_id, person):
    """Take back the guesses that carry `person` on the photo's other faces (only a face still carrying it as a guess)."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        taken = faces.guesses_named(conn, photo_path, person, face_id)
    finally:
        conn.close()
    if taken:
        db.write_with_connection(library.path, lambda conn: faces.revert_automatic(conn, taken),
                                 label="give a name back to the face chosen")


def _give_back_to(library, photo_path, face_id, person, before):
    """The tag just written can name ANOTHER face by the one-face rule (tagpup.store.face_tags, #788): a photo whose only face
    to be named is not the one chosen -- renaming a named face, with one face left nameless -- gives the new person to that
    face as a guess. A person chose this face: the guess goes back (only a face that was nameless before the tag and still
    carries the name as a guess), and the choice is named below."""
    taken = {other: person for other, found in _names_on(library, photo_path).items()
             if other != face_id and other not in before and is_person(found, person)}
    if taken:
        db.write_with_connection(library.path, lambda conn: faces.revert_automatic(conn, taken),
                                 label="give a name back to the face chosen")


# ---- Taking names off ---------------------------------------------------------------------------------------------

def unname_face(library, face_id, writer=None):
    """Take a face's name off, then the person's tag off the photo unless another face still carries them. With no
    `writer` the page writes the photo's tags itself: details["untag"] says which to take off."""
    named = _names_of(library, [face_id])
    result = faces_service.unname_face(library, face_id)
    if result.changed:
        _finish_untag(library, result, named, writer)
    return result


def unname_photo(library, photo_path, writer=None):
    """Take the names off every face of a photo (Unmatch All), then the tags of the people they named."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        named = [(photo_path, each) for each in faces.people_in_photo(conn, photo_path)]
    finally:
        conn.close()
    result = faces_service.unname_photo(library, photo_path)
    if result.changed:
        _finish_untag(library, result, named, writer)
    return result


def exclude(library, face_ids, reason=None, writer=None):
    """Rule faces out ("not important"), then take the tags of the people they were named off their photos, as unnaming
    does."""
    named = _names_of(library, face_ids)
    result = faces_service.exclude(library, face_ids, reason)
    if result.changed and named:
        _finish_untag(library, result, named, writer)
    return result


def _names_of(library, face_ids):
    """[(photo path, Ref)] of the faces among `face_ids` that carry a person and are not excluded."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return faces.named_among(conn, face_ids)
    finally:
        conn.close()


# ---- A person's tag taken off a photo --------------------------------------------------------------------------

def _faces_to_unname(library, removed):
    """[(face id, Ref, name_source)] of the faces whose person was taken off their photo: for each photo of `removed`
    ({photo path: [the tags a write took off it]}), a face that carries a person one of those tags names, when the photo's
    keywords -- as the library records them NOW, after the write -- no longer name them under any spelling. A branch of
    the tree is never a person (person_ids.People.why_not): a tag that names one takes no face's name off. Reads only."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        known = taxonomy.read_people_vocabulary(conn)
        named = {photo_path: faces.named_in_photo(conn, photo_path) for photo_path in removed}
    finally:
        conn.close()
    now = {paths.key(path): tags for path, tags, _raw in photos.read_tags(library.path, list(removed))}
    found = []
    for photo_path, taken_off in removed.items():
        tags_now = now.get(paths.key(photo_path), [])
        for face_id, person, source in named[photo_path]:
            if person.id is None:
                continue   # a name from before ids is no person
            if person_tags(taken_off, person.id, known) and not person_tags(tags_now, person.id, known):
                found.append((face_id, person, source))
    return found


def unname_for_removed_tags(library, removed, files_change=None):
    """A person's tag taken off a photo takes the person's name off the photo's faces (owner, 2026-10-08, #908): the pill in the
    pages, a tag taken off a selection, History's Undo of an add. `removed` is {photo path: [the tags that were taken off it]},
    of the photos whose file a change REALLY changed from holding the tag to not holding it (follow_change reads that from the
    change's own files; a photo that never held the tag, or whose file could not be written, is not here).

    The face is left as a decision, "this is nobody" (name_source 'manual', name NULL) -- the record that the owner took the
    person off ON PURPOSE: automatch, Re-examine, the one-face rule of a save (tagpup.store.face_tags) and clustering never name
    a face so decided, and `tags-from-faces` writes a tag only for a NAMED face, so none of them puts the person back. It is
    one journaled change of the faces (UNNAMED), which History's Undo takes back, name and who decided it as they were. A face
    renamed meanwhile is left out of it (skippable). Returns a Result: `changed` the faces unnamed; details `unnamed`
    ([{"id", "name"}] -- never reported with the library's counts, the page keeps them for its Undo) and `change`. A change that
    cannot be written is an error in the Result; the tag is off all the same."""
    result = Result(attempted=len(removed))
    result.details.update(unnamed=[], change=None)
    if not removed:
        return result
    found = _faces_to_unname(library, removed)
    if not found:
        return result
    edits = [journal.update("faces", (face_id,),
                            {"name": person.name, "tag_id": person.id, "name_source": source, "excluded": 0},
                            {"name": None, "tag_id": None, "name_source": "manual"}, kind="face unnamed", skippable=True)
             for face_id, person, source in found]
    try:
        # `files_change`: the change of photo files this follows, so that undoing THAT change can undo this one (undo_unnaming).
        applied = journal.apply(library.path, UNNAMED, edits, summary={"faces": len(edits), "files_change": files_change})
    except journal.Refusal as why:
        result.fail("the faces of the photos a tag was taken off", why)
        return result
    still_named = set(_names_now(library, [face_id for face_id, _n, _s in found]))
    result.details["unnamed"] = [{"id": face_id, "name": person.name} for face_id, person, _source in found
                                 if face_id not in still_named]
    result.details["change"] = applied.change_id
    result.changed = len(result.details["unnamed"])
    return result


def _tags_of(held):
    """The keyword tags of a journaled file's fields ({field: texts}, the journal's own keys)."""
    return vocabulary.extract_tags({fields.read_key(field): texts for field, texts in held.items()})


def follow_change(library, change_id, undone=False, moved=None, undo=None):
    """THE place that decides what a change of photo files did to the people on its photos (#908; the one owner of "this
    photo's tag was really taken off / put on by this change"): it reads the journaled change's own files -- only those the
    change really wrote and left done (or, `undone`, that its Undo really put back), and from what each held before to what it
    holds after. For the photos where a person's tag really went, the faces that carried them are unnamed
    (unname_for_removed_tags). A person's tag put back is NOT followed by naming a face -- not by the pill, Ctrl+Z or a
    selection: a decision made since is not in the journal, so a face is never named from an old record. The one exception is
    exact: History's Undo of a removal undoes the unnaming that removal made (undo_unnaming), the journaled inverse, refused if
    anything wrote those faces since: `undo(library, change_id)`, the undo History's own (tagpup.services.journal.undo_rows,
    which asks the one account, why_not, before it writes); given only with `undone`. `moved` ({old path: new path}) says where a photo went when the same save renamed it. A
    Result: details `unnamed` and `renamed` ([{"id", "name"}]), `faces_changes` (the journal's ids), `faces_problem`."""
    moved = {paths.key(old): new for old, new in (moved or {}).items()}
    removed, put_back, left = {}, False, False
    for row in file_journal.files_of(library.path, change_id):
        if row.is_rename:
            continue
        if row.state != ("undone" if undone else "done"):
            left = left or (undone and row.state == "done")
            continue
        was, now = (row.after, row.before) if undone else (row.before, row.after)
        was_tags, now_tags = _tags_of(was), _tags_of(now)
        taken = [tag for tag in was_tags if tag not in set(now_tags)]
        put_back = put_back or any(tag not in set(was_tags) for tag in now_tags)
        if taken:
            removed[moved.get(paths.key(row.path), row.path)] = taken
    result = unname_for_removed_tags(library, removed, files_change=None if undone else change_id)
    result.details["renamed"] = []
    result.details["faces_changes"] = [each for each in [result.details.get("change")] if each]
    if undone and put_back:
        if left:
            result.details["faces_problem"] = ("Some files were not put back, so the faces of this removal were not named again: "
                                               "undo it again in History when they can be.")
        else:
            undo_unnaming(library, change_id, result, undo)
    return result


def undo_unnaming(library, files_change, result, undo):
    """History's Undo of a removal (`files_change`, now undone) names the faces it unnamed again by undoing that unnaming: the
    journal's own inverse, which refuses (and the faces stay as they are, said in `result.details["faces_problem"]`) if anything
    wrote those faces since -- a name given by hand included, since the journal compares the rows with what the change left."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        change = removals.unnaming_of(conn, files_change)
        was = removals.faces_of(conn, change) if change is not None else []
    finally:
        conn.close()
    if change is None:
        return
    try:
        undo(library, change)
    except journal.Refusal as why:
        logger.warning("Could not name the faces of change %s again: %s", files_change, why)
        result.details["faces_problem"] = ("The faces of this removal were not named again because they changed since: %s" % why)
        return
    named = _names_now(library, [face_id for face_id, _name in was])
    result.details["renamed"] = [{"id": face_id, "name": name} for face_id, name in was if face_id in named]
    result.details["faces_changes"].append(change)
    result.changed += len(result.details["renamed"])


def _names_now(library, face_ids):
    """{face id: Ref} of the faces among `face_ids` that carry a person now."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return {face_id: person for face_id, (_path, person, _ex) in faces.rows(conn, face_ids).items() if person}
    finally:
        conn.close()


# ---- Automatch ------------------------------------------------------------------------------------------------------

def automatch_photo(library, photo_path, named, writer):
    """Automatch a photo's unnamed faces (faces_service.automatch_photo), then add the people it named to the photo. A
    tag that cannot be written takes the names back: nothing is left named that the photo does not carry."""
    result = faces_service.automatch_photo(library, photo_path, named)
    named_ids = result.details.get("named_ids") or {}
    if not named_ids:
        return result
    tagged = add_people(library, [(photo_path, person) for person in dict.fromkeys(named_ids.values())], writer)
    if tagged.ok:
        result.details.update(tags_written=tagged.details.get("tags_written", 0), written=tagged.details.get("written", {}))
        return result
    reverted = db.write_with_connection(library.path, lambda conn: faces.revert_automatic(conn, named_ids),
                                        label="take back automatch names")
    undone = Result(attempted=result.attempted)
    undone.details.update(result.details)
    undone.details["named_ids"] = {face_id: person for face_id, person in named_ids.items() if face_id not in reverted}
    undone.changed = len(undone.details["named_ids"])
    undone.fail(photo_path, "The people it named could not be added to the photo, so the names were taken back: %s"
                % (tagged.refused or tagged.message()))
    return undone
