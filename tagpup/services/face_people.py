"""A face's name and the person on the photo's keywords, kept in step (#861; docs/ARCHITECTURE.md,
"Faces on the photo").

They are one decision. A face named and the person absent from the photo's keywords is the drift the
owner saw ("a green box and No people tags"); a person left on the photo after the face was said not to
be them is the same drift the other way. So every write that names, unnames or rules out a face of an
open photo goes through here, and this is the one place that says how the keyword follows:

* **Naming** a face adds the person to the photo, the TAG FIRST: a tag that cannot be written (a file
  that cannot be read, a network share that is away, a person the tree files in two places) names no
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
read is a conflict and never overwritten), under the one lock of changes of photo files, with a deadline
on each ExifTool command so a share that has gone costs a minute, not five. TagPup's own page writes the
tag itself for a box it draws (its queue, its undo, its placement question), so its calls say
`page_writes_tags` and are told what to take off instead (`untag` in the details).

Undo of the tag change in History takes the person off and unnames the face with it (#908: unname_after_undo); a tag
taken off a photo any other way -- the pill, a selection -- does the same (unname_for_removed_tags), and the face is then a
decision, "nobody", which is the record that nothing is to name it or write its tag again. Many photos at once are
tagpup.jobs.face_assignments' job, whose steps are the functions here (name_faces, unname_faces, exclude, name_guesses).
"""
import logging

from tagpup.core import fields, paths, vocabulary
from tagpup.core.result import Conflict, Result
from tagpup.files import exiftool_session
from tagpup.services import faces as faces_service
from tagpup.services import file_changes, tagging
from tagpup.store import db, faces, file_journal, journal, person_ids, photos, removals, taxonomy

logger = logging.getLogger(__name__)

#: What the journal calls the changes made here (History's `operation`).
ADDED = "person added for a named face"
REMOVED = "person taken off for an unnamed face"

#: The change that unnamed the faces of a person taken off a photo (History's `operation`).
UNNAMED = removals.OPERATION

#: How long one ExifTool command of these writes may take, in seconds (the bulk edits' chunk's is the same).
TIMEOUT = 60

#: How many photos one change of the repair writes: a chunk is one journaled change (History lists each).
CHUNK = 25


# ---- Which tag ----------------------------------------------------------------------------------------------

class Filer:
    """The tag a person is written as, by the one rule a chip and Apply All follow (`vocabulary.person_tag`): the tree
    read once, and only read -- no node is made. None for a person the tree files in two places, or a tree with several
    people roots: someone must choose (the page's placement question)."""

    def __init__(self, library):
        self.filed, self.roots = taxonomy.people_filing(library.path)

    def tag(self, name):
        name = (name or "").strip()
        return vocabulary.person_tag(name, self.filed.get(vocabulary.key(name), []), self.roots)


def not_filed(name):
    return ("%s is filed in more than one place in the tag tree (or the tree has several people roots), so it is "
            "not known where to file them: add them to the photo from TagPup's Organize, which asks, or name the "
            "path." % name)


def already_names(tags, tag):
    """Do the photo's `tags` name the person `tag` is, by their leaf under any root? THE rule for "this photo already names
    them" -- the keyword writer's own (tagging._change_each: vocabulary.same_person) -- so that what is planned, what is
    skipped and what is written agree."""
    return any(vocabulary.same_person(tag, there) for there in tags)


def _person_tags(tags, name, known):
    """The tags among `tags` that name the person `name`: under a people root, or a person's node, by the leaf."""
    return [tag for tag in tags
            if any(vocabulary.same_person(person, name) for person in vocabulary.extract_people({}, [tag], known))]


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
    """Where a photo's tags are written: the ExifTool program, and `told(result)`, heard after each chunk written with the
    Result of its change (details["written"] is what the pages' records are told, in the order the files were written), and
    `on_chunk(done, total)`, the progress. A caller whose page writes the photo's tags itself passes no Writer."""

    def __init__(self, exiftool_path, told=None, on_chunk=None):
        self.exiftool_path = exiftool_path
        self.told = told
        self.on_chunk = on_chunk


def write_tags(library, changes, writer, operation, persons=None, stop_at_first_error=True):
    """Give each photo of `changes` ({path: (tags to add, tags to take off)}) its own, CHUNK photos at a time, each chunk one
    journaled change under the one lock of changes of photo files and with a deadline on ExifTool; the lock is let go
    between chunks, so another change of photo files waits for a chunk and not for all of them. Stops at the first chunk
    that fails, unless `stop_at_first_error` is False (the repair: a photo that cannot be written is an error, and the
    others are written). A Result: `changed` the files written, details["written"] and `changes`, the journal's ids."""
    result = Result(attempted=len(changes))
    items = list(changes.items())
    for start in range(0, len(items), CHUNK):
        chunk = dict(items[start:start + CHUNK])
        with exiftool_session.ExifToolSession(executable=writer.exiftool_path, timeout=TIMEOUT) as session:
            with file_changes.exclusively():
                done = tagging.change_each(library, chunk, writer.exiftool_path, operation, persons=persons, et=session,
                                           stop_at_first_error=stop_at_first_error)
                if writer.told:
                    writer.told(done)
        _merge(result, done)
        if not done.ok and stop_at_first_error:
            break
        if writer.on_chunk:
            writer.on_chunk(min(start + CHUNK, len(items)), len(items))
    result.changed = result.details.get("tags_written", 0)
    return result


def add_people(library, wanted, writer, filer=None, operation=ADDED, stop_at_first_error=True):
    """Add the person of each (photo path, name) of `wanted` to the photo's keywords, a photo given all its people in
    one write. A Result: `changed` the files written, details `written`. A photo whose keywords name the person already
    (by their leaf under any root: already_names) is not read or written again.
    Refused, nothing written, for a name the tree files in two places."""
    filer = filer or Filer(library)
    result = Result(attempted=len(wanted))
    held = {paths.key(path): tags for path, tags, _raw in photos.read_tags(library.path, [path for path, _name in wanted])}
    changes, persons = {}, {}
    for photo_path, name in wanted:
        tag = filer.tag(name)
        if tag is None:
            result.refuse(not_filed(name))
            return result
        if already_names(held.get(paths.key(photo_path), []), tag):
            continue
        add = changes.setdefault(photo_path, ([], ()))[0]
        if tag not in add:
            add.append(tag)
        persons.setdefault(paths.key(photo_path), set()).add(tag)
    return write_tags(library, changes, writer, operation, persons, stop_at_first_error) if changes else result


def untag_plan(library, items):
    """{photo path: [the tags to take off]} for the (photo path, person name) of `items` whose person is on no face of the
    photo any more -- read from the library NOW, after the faces were written -- and on whose keywords the person still
    is, in every spelling the photo holds (a tag at the person's path and a bare one are one person)."""
    wanted = {}
    for photo_path, name in items:
        wanted.setdefault(photo_path, []).append(name)
    if not wanted:
        return {}
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        carried = {photo_path: {vocabulary.key(name) for name in faces.names_in_photo(conn, photo_path)}
                   for photo_path in wanted}
        known = taxonomy.read_people_vocabulary(conn)
    finally:
        conn.close()
    held = {paths.key(path): tags for path, tags, _raw in photos.read_tags(library.path, list(wanted))}
    plan = {}
    for photo_path, names in wanted.items():
        tags = held.get(paths.key(photo_path), [])
        for name in dict.fromkeys(names):
            if vocabulary.key(name) in carried[photo_path]:
                continue
            for tag in _person_tags(tags, name, known):
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

def name_face(library, face_id, person_name, writer):
    """Name one face AND put the person on its photo, the tag first (see the module's docstring). Everything
    faces_service.name_face refuses is refused before a tag is written. `changed` is the faces named; details
    `tags_written` (0 when the photo named the person already) and `written`. A face that carried another person's name
    is renamed: that person's tags go from the photo unless another face is them, as unname_face's do. With no `writer`
    the page wrote the person's tag itself (TagPup's box and strip) and writes the old person's off itself too:
    details["untag"] says which."""
    person_name = (person_name or "").strip()
    refused = Result(attempted=1)
    # The page wrote the tag before it asked (no writer): its save ran the one-face rule (#788) and may have given the
    # person to ANOTHER face as a guess. A guess yields to a person's choice (and is given back below); a name somebody
    # decided on another face still refuses.
    found = faces_service.check_nameable(library, face_id, person_name, refused, guesses_yield=writer is None)
    if found is None:
        return refused
    photo_path, was = found
    if writer is None:
        _give_back_guesses(library, photo_path, face_id, person_name)
    before = _names_on(library, photo_path)
    # No writer: the page wrote the tag itself (TagPup's box and strip, first), and says so.
    tagged = add_people(library, [(photo_path, person_name)], writer) if writer is not None else Result()
    if tagged.ok and tagged.details.get("tags_written"):
        _give_back_to(library, photo_path, face_id, person_name, before)
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
        result = faces_service.name_face(library, face_id, person_name)
    except Conflict as late:
        raise Conflict("%s The person was added to the photo." % late) from None
    result.details.update(tags_written=tagged.details.get("tags_written", 0), written=tagged.details.get("written", {}))
    if tagged.details.get("changes"):
        result.details["changes"] = tagged.details["changes"]
    if result.refused and tagged.details.get("tags_written"):
        result.refused += " The person was added to the photo."
    # A face renamed is a name taken off one person and given to another: the old person's tag goes unless another face is them.
    if result.changed and was and vocabulary.key(was) != vocabulary.key(person_name):
        _finish_untag(library, result, [(photo_path, was)], writer)
    return result


def name_faces(library, face_ids, person_name, writer):
    """Name many faces as one person AND put the person on each of their photos, the tag first (name_face, for a chunk of
    a selection: the job's, tagpup.jobs.face_assignments). Everything faces_service.name_faces refuses is refused before a tag is
    written. A photo whose tag cannot be written (a file that cannot be read, a share that is away, a file changed outside since
    it was read) names no face and is an error entry; the others are named. Faces whose naming is refused afterwards (somebody
    named the person on another face meanwhile) keep the tags, the state the app has always allowed, and the same choice names
    them. `changed` the faces named; details `matched_ids` (the faces named), `skipped_excluded`, `tags_written`, `written`,
    `changes`, and for the page's caches `face_ids` and `fingerprints`."""
    person_name = (person_name or "").strip()
    check = faces_service.nameable(library, face_ids, person_name)
    result = Result(attempted=len(face_ids))
    result.details.update(matched=0, matched_ids=[], skipped_excluded=check.details.get("skipped_excluded", []),
                          tags_written=0, written={})
    if check.refused:
        result.refuse(check.refused)
        return result
    photo_of = check.details["photos"]
    wanted = check.details["matched_ids"]
    if not wanted:
        return result
    before = {photo: _names_on(library, photo) for photo in dict.fromkeys(photo_of.values())}
    tagged = add_people(library, [(photo_of[face_id], person_name) for face_id in wanted], writer, stop_at_first_error=False)
    if tagged.refused:
        result.refuse(tagged.refused)
        return result
    result.details.update(tags_written=tagged.details.get("tags_written", 0), written=tagged.details.get("written", {}))
    if tagged.details.get("changes"):
        result.details["changes"] = tagged.details["changes"]
    left_out = {paths.key(what) for what, _why in tagged.errors + tagged.skipped}
    for what, why in tagged.errors + tagged.skipped:
        result.fail(what, "the person was not added to the photo, so the face is not named: %s" % why)
    ready = [face_id for face_id in wanted if paths.key(photo_of[face_id]) not in left_out]
    # The tag just written can name ANOTHER face by the one-face rule (name_face's _give_back_to): the choice stands.
    for face_id in ready:
        if paths.key(photo_of[face_id]) in {paths.key(path) for path in tagged.details.get("written", {})}:
            _give_back_to(library, photo_of[face_id], face_id, person_name, before[photo_of[face_id]])
    if not ready:
        return result
    # The tag just written can have NAMED the chosen face itself, as a guess (the one-face rule of a photo with one face to be
    # named): a person chose it, so the guess becomes their decision, and the face is one this call named.
    now = _names_now(library, ready)
    by_rule = [face_id for face_id in ready if face_id in now and vocabulary.key(now[face_id]) == vocabulary.key(person_name)
               and face_id not in before[photo_of[face_id]]]
    confirmed = []
    if by_rule:
        confirmed = db.write_with_connection(
            library.path, lambda conn: [face_id for face_id in by_rule if faces.confirm(conn, face_id)],
            label="confirm names a person chose")
        ready = [face_id for face_id in ready if face_id not in by_rule]
        result.changed += len(confirmed)
        result.details["matched_ids"] = list(confirmed)
        result.details["matched"] = len(confirmed)
        if not ready:
            return result
    try:
        named = faces_service.name_faces(library, ready, person_name)
    except Conflict as late:
        result.fail("the faces", "%s The person was added to the photos." % late)
        return result
    if named.refused:
        result.fail("the faces", "%s The person was added to the photos." % named.refused)
        return result
    result.changed += named.changed
    result.details.update(matched=result.details["matched"] + named.details["matched"],
                          matched_ids=result.details["matched_ids"] + named.details["matched_ids"],
                          skipped_excluded=result.details["skipped_excluded"])
    for key in ("face_ids", "fingerprints"):
        if key in named.details:
            result.details[key] = named.details[key]
    return result


def _names_on(library, photo_path):
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return faces.names_by_face(conn, photo_path)
    finally:
        conn.close()


def _give_back_guesses(library, photo_path, face_id, person_name):
    """Take back the guesses that carry `person_name` on the photo's other faces (only a face still carrying it as a guess)."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        taken = faces.guesses_named(conn, photo_path, person_name, face_id)
    finally:
        conn.close()
    if taken:
        db.write_with_connection(library.path, lambda conn: faces.revert_automatic(conn, taken),
                                 label="give a name back to the face chosen")


def _give_back_to(library, photo_path, face_id, person_name, before):
    """The tag just written can name ANOTHER face by the one-face rule (tagpup.store.face_tags, #788): a photo whose only face
    to be named is not the one chosen -- renaming a named face, with one face left nameless -- gives the new person to that
    face as a guess. A person chose this face: the guess goes back (only a face that was nameless before the tag and still
    carries the name as a guess), and the choice is named below."""
    taken = {other: name for other, name in _names_on(library, photo_path).items()
             if other != face_id and other not in before and vocabulary.key(name) == vocabulary.key(person_name)}
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
        named = [(photo_path, name) for name in faces.names_in_photo(conn, photo_path)]
    finally:
        conn.close()
    result = faces_service.unname_photo(library, photo_path)
    if result.changed:
        _finish_untag(library, result, named, writer)
    return result


def exclude(library, face_ids, reason=None, writer=None, names=None):
    """Rule faces out ("not important"), then take the tags of the people they were named off their photos, as unnaming
    does. `names` ({face id: name}): the names the faces carried when a job began, for a chunk that may be run again after a
    stop -- faces already ruled out carry none by then, but their people are still to be taken off (untag_plan reads what the
    photo holds now, so a second run takes off nothing twice)."""
    named = _items_of(library, names) if names is not None else _names_of(library, face_ids)
    result = faces_service.exclude(library, face_ids, reason)
    if (result.changed or names is not None) and named:
        _finish_untag(library, result, named, writer)
    return result


def _items_of(library, names):
    """[(photo path, name)] for {face id: name}: the photo each face is in now."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        where = faces.rows(conn, list(names))
    finally:
        conn.close()
    return [(where[face_id][0], name) for face_id, name in names.items() if face_id in where]


def unname_faces(library, face_ids, writer, undo=False, names=None):
    """Take the names off many faces -- a chunk of a selection (Unmatch selected; `undo`, an assignment taken back) -- then the
    tags of the people they named off their photos unless another face of the photo still carries them, the faces first. `names`
    ({face id: name}): as exclude's."""
    named = _items_of(library, names) if names is not None else _names_of(library, face_ids)
    result = faces_service.unname_faces(library, face_ids, undo=undo)
    if named:
        _finish_untag(library, result, named, writer)
    return result


def _names_of(library, face_ids):
    """[(photo path, name)] of the faces among `face_ids` that carry a name and are not excluded."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return faces.named_among(conn, face_ids)
    finally:
        conn.close()


def name_guesses(library, proposed, writer):
    """Re-examine's chunk: write the guesses proposed for some photos ({photo path: [(face id, name)]}, faces_service.propose_guesses)
    and put each person on their photo (automatch_photo, for many photos). The faces first, decided under the write lock, then
    the tag of EVERY face of the chunk that carries its planned name now -- also one named by an earlier run of the same job that
    stopped before its tag was written, so a run again completes it. A photo whose tag cannot be written has the names it was
    just given taken back (faces.revert_automatic: a face a person named meanwhile is theirs). `changed` the faces named and kept;
    details `named_ids`, `tags_written`, `written`, `changes`, `face_ids` ... as faces_service.name_guesses'."""
    result = faces_service.name_guesses(library, proposed)
    wanted = {face_id: (photo, name) for photo, found in proposed.items() for face_id, name in found}
    now = _names_now(library, list(wanted))
    carrying = [(photo, name) for face_id, (photo, name) in wanted.items()
                if face_id in now and vocabulary.key(now[face_id]) == vocabulary.key(name)]
    result.details.update(tags_written=0, written={})
    if not carrying:
        return result
    tagged = add_people(library, carrying, writer, stop_at_first_error=False)
    if tagged.refused:
        # A person the tree files in two places: the names just given are taken back, none is left without its tag.
        failed_photos = {paths.key(photo) for photo, _name in carrying}
        problem = tagged.refused
    else:
        failed_photos = {paths.key(what) for what, _why in tagged.errors + tagged.skipped}
        problem = None
    result.details.update(tags_written=tagged.details.get("tags_written", 0), written=tagged.details.get("written", {}))
    if tagged.details.get("changes"):
        result.details["changes"] = tagged.details["changes"]
    named_ids = result.details.get("named_ids") or {}
    back = {face_id: name for face_id, name in named_ids.items() if paths.key(wanted[face_id][0]) in failed_photos}
    if back:
        reverted = set(db.write_with_connection(library.path, lambda conn: faces.revert_automatic(conn, back),
                                                label="take back automatch names"))
        result.details["named_ids"] = {face_id: name for face_id, name in named_ids.items() if face_id not in reverted}
        result.changed = len(result.details["named_ids"])
    if problem:
        result.fail("the people", "%s The names were taken back." % problem)
    for what, why in tagged.errors + tagged.skipped:
        result.fail(what, "the person was not added to the photo, so the name was taken back: %s" % why)
    return result


# ---- A person's tag taken off a photo --------------------------------------------------------------------------

def _faces_to_unname(library, removed):
    """[(face id, name, name_source)] of the faces whose person was taken off their photo: for each photo of `removed`
    ({photo path: [the tags a write took off it]}), a face that carries a person one of those tags names, when the photo's
    keywords -- as the library records them NOW, after the write -- no longer name them under any spelling. A branch of
    the tree is never a person (person_ids.People.why_not): a tag that names one takes no face's name off. Reads only."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        known = taxonomy.read_people_vocabulary(conn)
        tree = person_ids.read(conn)
        named = {photo_path: faces.named_in_photo(conn, photo_path) for photo_path in removed}
    finally:
        conn.close()
    now = {paths.key(path): tags for path, tags, _raw in photos.read_tags(library.path, list(removed))}
    found = []
    for photo_path, taken_off in removed.items():
        tags_now = now.get(paths.key(photo_path), [])
        for face_id, name, source in named[photo_path]:
            if tree.why_not(name) == "branch":
                continue
            if _person_tags(taken_off, name, known) and not _person_tags(tags_now, name, known):
                found.append((face_id, name, source))
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
    edits = [journal.update("faces", (face_id,), {"name": name, "name_source": source, "excluded": 0},
                            {"name": None, "name_source": "manual"}, kind="face unnamed", skippable=True)
             for face_id, name, source in found]
    try:
        # `files_change`: the change of photo files this follows, so that undoing THAT change can undo this one (undo_unnaming).
        applied = journal.apply(library.path, UNNAMED, edits, summary={"faces": len(edits), "files_change": files_change})
    except journal.Refusal as why:
        result.fail("the faces of the photos a tag was taken off", why)
        return result
    still_named = set(_names_now(library, [face_id for face_id, _n, _s in found]))
    result.details["unnamed"] = [{"id": face_id, "name": name} for face_id, name, _source in found
                                 if face_id not in still_named]
    result.details["change"] = applied.change_id
    result.changed = len(result.details["unnamed"])
    return result


def _tags_of(held):
    """The keyword tags of a journaled file's fields ({field: texts}, the journal's own keys)."""
    return vocabulary.extract_tags({fields.read_key(field): texts for field, texts in held.items()})


def follow_change(library, change_id, undone=False, moved=None):
    """THE place that decides what a change of photo files did to the people on its photos (#908; the one owner of "this
    photo's tag was really taken off / put on by this change"): it reads the journaled change's own files -- only those the
    change really wrote and left done (or, `undone`, that its Undo really put back), and from what each held before to what it
    holds after. For the photos where a person's tag really went, the faces that carried them are unnamed
    (unname_for_removed_tags). A person's tag put back is NOT followed by naming a face -- not by the pill, Ctrl+Z or a
    selection: a decision made since is not in the journal, so a face is never named from an old record. The one exception is
    exact: History's Undo of a removal undoes the unnaming that removal made (undo_unnaming), the journaled inverse, refused if
    anything wrote those faces since. `moved` ({old path: new path}) says where a photo went when the same save renamed it. A
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
            undo_unnaming(library, change_id, result)
    return result


def undo_unnaming(library, files_change, result):
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
        journal.undo(library.path, change)
    except journal.Refusal as why:
        logger.warning("Could not name the faces of change %s again: %s", files_change, why)
        result.details["faces_problem"] = ("The faces of this removal were not named again because they changed since: %s" % why)
        return
    named = _names_now(library, [face_id for face_id, _name in was])
    result.details["renamed"] = [{"id": face_id, "name": name} for face_id, name in was if face_id in named]
    result.details["faces_changes"].append(change)
    result.changed += len(result.details["renamed"])


def _names_now(library, face_ids):
    """{face id: name} of the faces among `face_ids` that carry a name now."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return {face_id: name for face_id, (_path, name, _ex) in faces.rows(conn, face_ids).items() if name}
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
    tagged = add_people(library, [(photo_path, name) for name in dict.fromkeys(named_ids.values())], writer)
    if tagged.ok:
        result.details.update(tags_written=tagged.details.get("tags_written", 0), written=tagged.details.get("written", {}))
        return result
    reverted = db.write_with_connection(library.path, lambda conn: faces.revert_automatic(conn, named_ids),
                                        label="take back automatch names")
    undone = Result(attempted=result.attempted)
    undone.details.update(result.details)
    undone.details["named_ids"] = {face_id: name for face_id, name in named_ids.items() if face_id not in reverted}
    undone.changed = len(undone.details["named_ids"])
    undone.fail(photo_path, "The people it named could not be added to the photo, so the names were taken back: %s"
                % (tagged.refused or tagged.message()))
    return undone
