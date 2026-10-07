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
from tagpup.services import identify, maintenance
from tagpup.store import db, face_tags, journal

#: The journal kind of each face named.
KIND = "face named from its photo's tag"


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
                "photos_left_for_identify_faces": found.counts["left"]},
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


def faces_from_tags(library, apply=False):
    """Plan, and with `apply` make, the naming of every face a keyword person of its photo
    names (see the module's docstring). A Result on the maintenance scaffold: `changed` is the
    face rows written; details `counts` as _plan's."""
    return maintenance.run(library, "faces_from_tags", _plan, _edits, apply=apply, remaining=_remaining)
