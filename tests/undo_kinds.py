"""Where each kind of change the journal names (tagpup.services.journal.UNDO_CHECKS) is held by the undo matrix
(tests/test_undo_after_every_later_kind.py, scenario D2): a row of it (OPERATIONS) or left out with its reason (LEFT).
The tables alone, so the guard reads them in the fast tier (tests/test_every_change_kind_has_an_undo_check.py: a kind the
code makes that neither names fails it, #2748) and the matrix asserts each row's change is of its kind.
"""
from tagpup.services import baking, bulk_edit, convert, face_batch, face_people, retire_photo_vectors, sync, tagging
from tagpup.services import journal as journal_service
from tagpup.services import settings as library_settings
from tagpup.services import tags_from_faces
from tagpup.store import file_journal, journal

#: Each kind of change a row of the matrix makes, by its UNDO_CHECKS key, and the rows (the matrix's BUILT and WRITERS)
#: making it. A kind no code makes any more (journal_service.RETIRED) is here where a row makes it as its deleted writer
#: did: the guard compares the kinds the code makes with OPERATIONS less RETIRED.
OPERATIONS = {
    library_settings.WITH_DEFAULTS: ("stamp",),
    library_settings.CHANGE: ("settings", "switch"),
    "migration %d: %s": ("migration",),
    sync.OPERATION: ("sync", "sync removal"),
    "save photo": ("save photo",),
    "smart rename": ("smart rename",),
    "smart rename: original names": ("original names",),
    tagging.CLEAR_DUPLICATES: ("clear duplicates",),
    "rotate": ("rotate",),
    journal_service.FACE_PASS: ("pass", "later pass"),
    "refresh_rows": ("refresh",),
    baking.OPERATION: ("bake",),
}

#: A write of photo files' fields through file_changes.write_fields (tagging.change_each and face_people.write_tags
#: included), undone file by file as Save's is, the faces of a person taken off following (face_people.follow_change).
AS_SAVE = "a write of photo files' fields whose way back is the journal's alone, as Save's: Save's row holds the kind"
#: A bulk decision on faces carried by a write of photo files with its faces alongside (tagpup.services.face_batch), the
#: faces put back in the transaction that undoes each file.
AS_FACE_BATCH = ("a write of photo files with its faces alongside, undone with them: Save's row holds the files' way back; "
                 "tests/test_bulk_faces_and_tags.py undoes an assignment, an ignore, "
                 "a cancelled batch and the taking of names off faces (unname bulk) from History, and its ReExamine "
                 "undoes a re-examine")
#: A rename of photo files through file_changes.rename.
AS_RENAME = "a rename of a photo file through file_changes.rename: Smart Rename's row holds the kind"
#: A change of faces or people rows alone, no file: refused by a newer change of the same rows (journal.refusal), as the
#: sync removal's of the named face it deleted is.
AS_ROWS = "a change of faces or people rows alone, no file, whose way back is the journal's: the sync removal's row holds it"

#: The reasons that say the kind writes photo files, and the one that says it writes none: the guard holds each kind to
#: what its writer does.
WRITES_FILES = {AS_SAVE, AS_FACE_BATCH, AS_RENAME}
ROWS_ALONE = {AS_ROWS}

#: Every other kind UNDO_CHECKS names that the code makes, and why it is not a row of the matrix.
LEFT = {
    tagging.ADD_TO_ALL: AS_SAVE,
    "apply all suggestions": AS_SAVE,
    "rename tag": AS_SAVE,
    "remove tag": AS_SAVE,
    "time shift": AS_SAVE,
    **{operation: AS_SAVE for operation in bulk_edit.OPERATIONS.values()},
    face_people.ADDED: AS_SAVE,
    face_people.REMOVED: AS_SAVE,
    tags_from_faces.OPERATION: AS_SAVE,
    **{operation: AS_FACE_BATCH for operation in face_batch.DESCRIPTIONS.values()},
    "rename after caption": AS_RENAME,
    face_people.UNNAMED: AS_ROWS,
    journal.PERSON_DELETED: AS_ROWS,
    journal.PERSON_MERGED: AS_ROWS,
    "faces_from_tags": AS_ROWS,
    library_settings.FROM_REPLACED: "a stamp of the settings, never undone with the same sentence as the stamp's row",
    baking.INDEX_OPERATION: ("never listed nor undone (its sentence); the save it writes after while unfinished is "
                             "test_a_save_is_undone_once_the_index_bake_writing_its_photo_is_finished"),
    retire_photo_vectors.VECTORS_OPERATION: ("its own check (retire_photo_vectors.vectors_check): "
                                             "tests/test_retiring_the_old_photo_models_vectors.py"),
    convert.CARRIED: AS_SAVE,
    convert.OPERATION: ("a clip's conversion, planned by file_journal.plan and never undone (its sentence: the original "
                        "is deleted)"),
    file_journal.DELETE: ("a Delete's crash record (file_journal.INTERNAL), never listed nor undone (its sentence): "
                          "tests/test_a_delete_cut_short.py"),
}
