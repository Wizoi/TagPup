# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-24 | low: a held connection used more often than once a second never takes a moved root map: `tagpup/store/roots.py _held` reuses the map it holds while `now - state.checked < RECHECK_SECONDS`, then writes `State(version, rows, roots, now, file)` on EVERY use, so `checked` moves forward each time and the recheck (machine.roots_of) never comes while the connection is kept busy. RECHECK_SECONDS's comment ("How long a connection trusts the map it holds before asking again") and test_roots_location's "a moved map is found within a second" hold only for a connection that rests a second. Shown (#3375 cut 3A): test_a_running_server_reads_the_new_place_without_a_restart and test_the_generations_every_cache_of_paths_is_keyed_by_move_with_the_map, their fixed sleeps replaced by wait_until polling the held connection every 10 ms, both failed in 3 of 3 runs after RECHECK_SECONDS + 1.0 s (the map never moved); with the sleeps they pass. Fix (not made, the brief allowed no product change): keep `state.checked` when the held map is reused, stamp `now` only when it was read again. user action: move a root to another place (Roots page or the CLI) while a connection the server or a run holds is asked more than once a second; whether a server connection is held that busy is not counted here. | open |
