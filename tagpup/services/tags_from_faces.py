"""Put on a photo the people its faces name and its keywords do not (#861): `tagpup_cli.py tags-from-faces`.

A face named and the photo's person tag are one decision (tagpup.services.face_people). Faces named before that rule
-- by TagTuner's strip, Match, AutoMatch All, Identify Faces, clustering -- left photos listing a person from a face
alone (`photo_people` source 'face'): the owner's "a green box and No people tags". This is the same rule applied once
to what is there already, as `faces-from-tags` (tagpup.services.faces_from_tags) is the other way round.

**It changes PHOTO FILES**, not only the library: each photo gets the person's keyword written into its file by
ExifTool, as every other keyword write does (tagpup.services.tagging.change_each). A dry run (the default) only counts.
`apply` writes CHUNK photos at a time, each chunk one journaled change of photo files (operation `OPERATION`), which
records every file's fields before and after: `undo` takes a chunk back, and a file that was changed outside since is
a conflict and is never overwritten. That journal is the way back; no copy of the library is taken (a photo's keywords
are not in the library alone, and the journal holds what a copy of the library could not). A person the tree files in
two places, or a tree with several people roots, is left (counted): which tag they are is the owner's to choose.

Interrupted part-way (a crash, a closed window): the chunks written are written, journaled and recorded in their rows;
the chunk under way is settled at the next start by what its files hold (tagpup.services.file_changes), and running it
again does the rest -- a photo that lists the person from a keyword is no longer in the plan. Two at once (the CLI and
the always-on server): the lock of changes of photo files is the process's, so a CLI run is not held by it; each file is
read again just before it is written, and a file another process changed meanwhile is a conflict, reported, not
overwritten -- run it again. A photo whose file cannot be read (a share that is away, a file gone) is an error counted
and the others are written.
"""
from tagpup.core.result import Result
from tagpup.services import face_people
from tagpup.store import db, people
from tagpup.store import roots as store_roots

#: What the journal calls the changes made here (History's `operation`).
OPERATION = "person tags from faces"


def plan(library):
    """What `apply` would write, counted: a Result whose details are `counts` (safe to show anyone) and `work`, the
    (photo path, name) of each person to put on a photo (never reported). Reads only."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        found = store_roots.natives(conn, people.on_faces_alone(conn), 0)
    finally:
        conn.close()
    filer = face_people.Filer(library)
    work, left, photos = [], 0, set()
    for photo_path, name in found:
        photos.add(photo_path)
        if filer.tag(name) is None:
            left += 1
        else:
            work.append((photo_path, name))
    result = Result(attempted=len(photos))
    result.details.update(
        dry_run=True, work=work,
        counts={"photos_with_a_person_on_a_face_alone": len(photos), "people": len(found),
                "photos_to_write": len({photo_path for photo_path, _name in work}), "people_to_write": len(work),
                "people_the_tree_files_in_two_places": left})
    return result


def apply(library, planned, exiftool_path, on_chunk=None):
    """Write what `plan` counted, CHUNK photos at a time (face_people.write_tags): a Result as add_people's -- `changed`
    the photo files written, `errors` the photos that could not be (counted by the caller, never named), details `changes`
    the journal's ids. `on_chunk(done, total)` hears the progress."""
    writer = face_people.Writer(exiftool_path, on_chunk=on_chunk)
    result = face_people.add_people(library, planned.details["work"], writer, operation=OPERATION, stop_at_first_error=False)
    result.details["dry_run"] = False
    return result
