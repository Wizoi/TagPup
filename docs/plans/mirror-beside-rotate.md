# Mirror (left/right): one button beside Rotate Left / Rotate Right flips the photo as shown
Live count: no photo in the sampled census holds a mirrored Orientation (2, 4, 5, 7): the "already mirrored" case is produced only by the button itself (user action). Rotate is rarely used: 1 rotate change in the live journals (photo_index), 0 in the other two. TIFF rows (the one format whose face boxes move with the file): 17, all in photo_index (counted read-only 2026-11-19).
Domains: Files (rotation, orientation table), People (the face-box signature in store/faces.py); integration reviews the seams

| Mechanism | The ONE place it lives | Its owner | New or existing |
| Orientation after a mirror | `tagpup/core/orientation.py`, a MIRRORED row beside `ROTATED_ORIENTATION`: `{1:2, 2:1, 3:4, 4:3, 5:6, 6:5, 7:8, 8:7}` (an involution: mirror twice returns) | Files | existing table, one row |
| The write | `photos.rotate_plan("mirror")` through `file_changes.write_fields(..., "rotate", ...)`: the same one journaled kind, `summary["direction"] = "mirror"`; `POST /api/photo/rotate` takes a third direction | Files | existing: one more value |
| A TIFF's face boxes follow the file | `core/orientation.py` a box mapping between two Orientation values (replaces `faces.turned_box`); `store/faces.py::turn_boxes_in` takes the mapping | Files / People | existing: one rule instead of two |
| What a settle or an undo does | `rotation.direction_between` answers "mirror", `rotation.follower` builds the turn (no size swap) and the way back | Files | existing |
| The button | `web/tagpup/index.html` +1 button in the `data-writes="rotate"` span, `main.js` +1 listener; `photo.js::rotatePhoto(direction)` reused as is | Integration | existing |

Decisions (the plan's, not reopened): NO new journal kind (it would cost a row in `journal.py`, `tests/undo_kinds.py`, `test_undo_after_every_later_kind.py`, `test_every_change_kind_has_an_undo_check.py`, and History); the write's name stays "rotate" (`PHOTO_WRITES` unchanged). A clip is refused as for rotate (`CLIP_NOT_EDITED`; the button is hidden by `stale.js`). Pixels are never re-encoded; the Orientation field only. No new route, field name or migration; nothing automatic on the owner's data.
Risk, the one real one: a TIFF mirrored, a crash after the file and before the row, settled forward (or an undo) with `direction_between` not taught "mirror" would leave that file's boxes unflipped (data-class, 17 rows at most). Its scenario file `tests/test_rotate_crash_settles_with_the_boxes.py` gains a TIFF-mirrored variant, written and run against the old code first.
Guards that change with it: `validation.ROTATE_DIRECTIONS` and message, `tests/validation_rules.json`, `tests/validation_cases.json`, `docs/SPEC_TAGPUP_GUI.md` (~315, ~345), `tests/test_tagpup_routes.py::TestPhotoRotate`, `tests/test_rotate_is_journaled.py::WhatIsRefused`, `metadata_check.OPERATIONS` +1 row ("mirrored").

Steady-state writer: no data is written by a command; a mirror is the owner pressing the button, through the same journaled write as a rotate (undoable).
One-offs: none.
Cuts: ONE worker, ONE branch (about 130 lines plus tests): the orientation-table check gains the mirror leg and fails first; "mirrored twice lands where it started, undone once lands mirrored"; the TIFF crash-settle variant; then the table, the box rule (deleting `faces.turned_box`), `direction_between`/`follower`, validation and spec, the button. Reviews: the Files owner, the People owner (the `store/faces.py` signature), the reviewer.
