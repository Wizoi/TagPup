"""Name the faces a photo's person tag names, for the photos already in a library (#788).

From now on a photo's save, its index and the recording of its faces do this for the photo they
touch (tagpup.store.face_tags). This is the same rule applied once to what is there already --
`tagpup_cli.py faces-from-tags` -- on the maintenance scaffold (tagpup.services.maintenance): a
dry run unless applied, counts only, and the apply one change of the library's journal, so
`undo` takes it back. It also names, by comparison, the faces of photos with several faces or
several people, which a save does not (it would build the decided faces' matrix inside a write):
a face is named only when exactly one person's decided faces are as alike as naming unasked allows
and the photo's keywords name that person (tagpup.store.face_tags).

Names are written as automatic (name_source stays NULL): clustering may revise them. A face a
person unmatched by hand, or excluded, is never read as a candidate.

With `folder` (tagpup.services.folder_scope) the plan reads only the photos under that folder, at any depth. Nothing else changes:
the people their tags name, the decided faces a face is compared with and the gate of a person with no decided face (#839) stay
the whole library's, so the folder's plan is the whole-library plan restricted to the folder's photos, and the write is as
guarded. A folder with no photo of the library under it is refused, not read as "nothing to do".
"""
import collections

from tagpup.core.result import Result
from tagpup.services import folder_scope, identify, maintenance
from tagpup.store import db, face_tags, journal

#: The journal kind of each face named.
KIND = "face named from its photo's tag"

#: Why a second apply asks for --again (#840).
AGAIN = ("faces-from-tags was applied to this library before, and not undone. The faces it named then are references for "
         "the next run, which names the faces that look like them from 0.70, so each apply can name more and a person's "
         "references can drift one step at a time (the rule on a save does the same). Run it again with --again if that "
         "is wanted.")


def earlier_apply(library):
    """Was faces-from-tags applied to this library, and not undone since? Read from the journal."""
    return any(entry["operation"] == "faces_from_tags" and entry["status"] != "undone"
               for entry in journal.history(library.path, limit=1000))


def earlier_applies(library):
    """The most recent apply of faces-from-tags not undone since, as its history entry (its summary has `scope`: "whole library" or
    "folder", never the folder), or None."""
    return next((entry for entry in journal.history(library.path, limit=1000)
                 if entry["operation"] == "faces_from_tags" and entry["status"] != "undone"), None)


def earlier_sentence(entry, how="cli"):
    """Where the earlier apply `entry` was (earlier_applies): the whole library or one folder, and its change. The folder's path is
    behind History's reveal, as a change's values are: the CLI's sentence says how to ask for it."""
    if entry is None:
        return ""
    change = entry["id"]
    if entry["summary"].get("scope") == "folder":
        named = "; `history --change %d --reveal` names it" % change if how == "cli" else ""
        return "It was applied to one folder (change %d%s)." % (change, named)
    return "It was applied to the whole library (change %d)." % change


def _decided(library):
    """The decided faces as (ids, names, matrix), built when a photo needs them."""
    return identify.decided_faces(library)[1]


#: What a plan carries for the write: the faces to name, and what it read of their photos (face_tags.guards).
Named = collections.namedtuple("Named", "choices photo_tags siblings")


def _plan(library, on_step=None, folder=None):
    photo_ids, summary = None, {"scope": "whole library"}
    if folder is not None:
        try:
            scope = folder_scope.resolve(library, folder)
        except folder_scope.NoPhotosThere as why:
            return maintenance.Plan(refused=str(why))
        photo_ids, summary = scope.photo_ids, {"scope": "folder", "folder": scope.folder}
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        # One read transaction: the guards are the state the plan read, not a later one.
        db.begin(conn)
        found = face_tags.plan(conn, photo_ids=photo_ids, references=lambda: _decided(library), on_step=on_step)
        photo_tags, siblings = face_tags.guards(conn, found.named)
    finally:
        conn.close()
    named = found.named
    return maintenance.Plan(
        size=len(named),
        counts={"photos_with_a_face_and_a_person_to_place": found.counts["photos"],
                "named_by_the_tag_alone": found.counts["one_face_one_person"],
                "photos_named_by_comparison": found.counts["matched"],
                "faces": len(named),
                "photos_left_for_identify_faces": found.counts["left"],
                # Of the photos with one face and one person (#833), by how the face looks like the person.
                "one_face_one_person": found.counts["tag_alone"],
                "person_has_no_decided_face": found.counts["no_decided_face"],
                "like_them_from_0.80": found.counts["like_them"],
                "like_them_from_0.70_to_0.80": found.counts["like_them_somewhat"],
                "not_like_them": found.counts["not_like_them"],
                "not_decidable_yet": found.counts["not_decidable_yet"],
                "face_unreadable": found.counts["unreadable_face"],
                "background_sized_faces_passed_over": found.counts["background_sized"]},
        ids={"faces": [choice.face_id for choice in named]},
        reveal={"named": [(choice.face_id, choice.name) for choice in named]},
        work=Named(named, photo_tags, siblings), summary=summary)


def plan(library, on_step=None, folder=None):
    """The plan alone, for a caller that shows it before anything is written (the job behind the apps' button, which asks
    first): a maintenance.Plan -- `size` the faces it would name, `counts` as faces_from_tags' details, `work` what
    `faces_from_tags(..., planned=)` applies. `on_step(stage, done, total)` hears the plan's progress and may raise to stop
    it (tagpup.store.face_tags.plan). With `folder`, the photos under it only; the plan is `refused` (with a sentence) when the
    library holds none there. Reads only."""
    return _plan(library, on_step, folder)


def _edits(planned):
    """Each face by id, while it is still what the plan read: unnamed, not excluded, and not
    marked nobody by hand since. And, writing nothing, what made each a candidate (#869): its
    photo's tags, and its photo's other faces' names, as the plan read them -- a person's tag
    taken off, or another face of the photo named, while the question was open, refuses the
    whole change (journal.update with no values writes nothing; its `expect` is the guard)."""
    work = planned.work
    edits = [journal.update("faces", (choice.face_id,),
                            {"name": None, "excluded": 0, "name_source": choice.source},
                            {"name": choice.name}, kind=KIND)
             for choice in work.choices]
    edits += [journal.update("faces", (face_id,), {"name": name}, {}) for face_id, name in work.siblings.items()]
    edits += [journal.update("photos", (photo_id,), {"tags": tags}, {}) for photo_id, tags in work.photo_tags.items()]
    return edits


def _remaining(library, folder=None):
    return {"faces": len(_plan(library, folder=folder).work.choices)}


def faces_from_tags(library, apply=False, again=False, planned=None, folder=None):
    """Plan, and with `apply` make, the naming of every face a keyword person of its photo
    names (see the module's docstring). A Result on the maintenance scaffold: `changed` is the
    face rows written; details `counts` as _plan's, and `earlier_apply`. A second apply is refused
    without `again` (#840), with the counts it would have named in the details.

    With `planned`, a plan read earlier (`plan`), that plan is what is applied -- the faces a person was
    asked about -- instead of one read again, which took 13.6 s on photo_index and could name other faces
    than the question said; the counts after the write are not read again either. The write is as
    guarded as ever: each face must still be what the plan read, or nothing is written.

    With `folder`, the plan reads (and, read again after the write, counts what is still to be named in) the photos under that
    folder only; a folder the library holds no photo under is refused. A second apply needs `again` whichever folder the
    first was for (#995): the faces it named are references for the next."""
    earlier = earlier_apply(library)
    plan_of = (lambda _library: _plan(_library, folder=folder)) if planned is None else (lambda _library: planned)
    if apply and earlier and not again:
        planned = plan_of(library)
        result = Result(attempted=planned.size, details={"dry_run": True, "change": None, "counts": dict(planned.counts),
                                                         "ids": dict(planned.ids), "reveal": {}, "earlier_apply": not planned.refused})
        # A folder with no photo is the first thing to say; else the sentence, with where the earlier apply was.
        result.refuse(planned.refused or (AGAIN + " " + earlier_sentence(earlier_applies(library))).strip())
        return result
    result = maintenance.run(library, "faces_from_tags", plan_of, _edits, apply=apply,
                             remaining=(lambda _library: _remaining(_library, folder)) if planned is None else None)
    result.details["earlier_apply"] = earlier
    return result
