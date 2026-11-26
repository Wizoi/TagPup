# One root map for a run of writes: a held connection takes a moved map within a second, and undo and settle hold one map per run
Live count: 0 rows touched (no stored data changes). Counted this session before building: whether a server connection is held
more often than once a second (the worker counts it, read-only, and says so); the undo of the largest bulk change in photo_index,
1,638 files (about 40 s), is the run it matters for. Findings #3384 (low), #3395 (low).
Domains: Data (store/roots.py, the connection), Files (services/file_changes.py: undo, `_settle_files`); integration reviews the seam

| Mechanism | The ONE place it lives (module, function) | Its owner | New or existing |
| a connection takes a moved map within `RECHECK_SECONDS` however busy it is | `tagpup/store/roots.py` `_held`: keep `state.checked` when the held map is reused, stamp `now` only when it was read again | Data | existing, fixed (#3384) |
| one map for a whole run of file writes | `tagpup/services/roots.pinned` (used by `file_changes._pinned` for a forward write and rename) | Files | existing: undo's loop and `_settle_files` enter it too (#3395) |
| a connection held for the length of a run | `tagpup/store/db.connections_held` | Data | existing (#3375, 740ff9b5), unchanged |

The order matters and is the point of the plan: pin undo and settle FIRST (so a run takes one map by the pin, not by #3384's bug),
then fix #3384. Fixing #3384 alone would flip undo and settle to a map that can change between two files of one run.

What must not change: a settle that finds an unplaced root must not be refused where it settles today (`Unplaced` would newly
refuse it under `pinned`): if `pinned` cannot be entered without that refusal, undo and settle take the map once at their start
instead (one read, passed down), and say why in the code. A change of the library's roots by another process stops a run the
way a forward write stops (left as a crash leaves one, settled at the next open); an undo stopped half way says so and can be
run again.

Tests (scenarios, mapped in tests/owners.py, run against the old code first): (1) Data: a connection used every 10 ms for longer
than `RECHECK_SECONDS` takes a map moved on disk (fails today; replaces the two fixed-sleep workarounds named in #3384:
`test_a_running_server_reads_the_new_place_without_a_restart` and `test_the_generations_every_cache_of_paths_is_keyed_by_move_with_the_map`
go back to `wait_until` polling and the two `roots_location` sleeps are deleted); (2) Files: a map moved mid-undo of a 60-photo
change is not taken by that run, and the next undo or settle takes it; the same for `_settle_files`.
Steady-state writer: a root moved afterwards: `roots._held` takes it within a second on any connection, and a run of writes
holds the one it began with, by the pin; no command run.
One-offs: none.
Cuts: one worker, one branch (about 150 lines of product code and tests), reviewed by the Data and Files owners.
