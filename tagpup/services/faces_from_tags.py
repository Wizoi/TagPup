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
"""
from tagpup.core.result import Result
from tagpup.services import identify, maintenance
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


def _decided(library):
    """The decided faces as (ids, names, matrix), built when a photo needs them."""
    return identify.decided_faces(library)[1]


def _plan(library):
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        found = face_tags.plan(conn, references=lambda: _decided(library))
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
        work=named)


def _edits(planned):
    """Each face by id, while it is still what the plan read: unnamed, not excluded, and not
    marked nobody by hand since."""
    return [journal.update("faces", (choice.face_id,),
                           {"name": None, "excluded": 0, "name_source": choice.source},
                           {"name": choice.name}, kind=KIND)
            for choice in planned.work]


def _remaining(library):
    return {"faces": len(_plan(library).work)}


def faces_from_tags(library, apply=False, again=False):
    """Plan, and with `apply` make, the naming of every face a keyword person of its photo
    names (see the module's docstring). A Result on the maintenance scaffold: `changed` is the
    face rows written; details `counts` as _plan's, and `earlier_apply`. A second apply is refused
    without `again` (#840), with the counts it would have named in the details."""
    earlier = earlier_apply(library)
    if apply and earlier and not again:
        planned = _plan(library)
        result = Result(attempted=planned.size, details={"dry_run": True, "change": None, "counts": dict(planned.counts),
                                                         "ids": dict(planned.ids), "reveal": {}, "earlier_apply": True})
        result.refuse(AGAIN)
        return result
    result = maintenance.run(library, "faces_from_tags", _plan, _edits, apply=apply, remaining=_remaining)
    result.details["earlier_apply"] = earlier
    return result
