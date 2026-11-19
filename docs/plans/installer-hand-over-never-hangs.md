# An install's hand-over always ends: a drain that cannot finish gives the server back and says why
Live count: 1 hang in about 10 installs this month (2026-11-19, 2b): the old server (184118b9) logged "Draining for an update: ... 0 request(s) in flight", then neither "Drained" nor "Could not drain"; every later drain answered "another drain" for 22 minutes until the owner had the server ended by hand. `GET /api/server` read `taking_work:false, requests:0, busy:[]` meanwhile.
Domains: Integration (tagpup/web/lifecycle.py, tagpup/launcher.py, tagpup/runtime.py `Background.stop`); Files (tagpup/jobs/watching.py `stop`); integration reviews the seams

Cause (unconfirmed, no thread dump was taken): the drain's first call sits in `Background.stop(timeout)`, and `watching.py` `stop` calls `observer.stop()` before its bounded `join(timeout)`; on Windows a watch on a share that does not answer can block in `observer.stop()` itself, which no timeout bounds. While it blocks, `_draining` stays True, so every later drain waits and says "another drain", and the install loops forever. The first job of the worker is to REPRODUCE this (a background task whose `stop` never returns) and see the drain not end; only then fix.

| Mechanism | The ONE place it lives | Its owner | New or existing |
| A drain ends by its deadline whatever a background task's stop does | `lifecycle.py` `_drain_until` (runs `Background.stop` bounded: each `task.stop` on a daemon thread joined to the one deadline; a task still running is named) | Integration | existing: change |
| A task's stop is bounded | `runtime.py` `Background.stop` (the bound), `watching.py` `stop` (observer stopped on its own thread, joined to the deadline) | Integration / Files | existing: change |
| A drain that passes its deadline logs what it waited for, resumes, and clears `_draining` | `lifecycle.py` `drain` (the "Could not drain in Ns; taking work again" line must be reached) | Integration | existing |
| An install says what it waits for and stops waiting | `launcher.py` `make_way`: "another drain" for more than a patience limit (a named constant) says what the status shows and leaves the update to the next launch, as Ctrl+C does | Integration | existing: change |

Steady-state writer: no data written. The next install or launch of any version, with no command run.
One-offs: none.
Cuts: ONE worker, ONE branch (about 300 lines): a failing test first (a stuck `stop`, the drain still ends and resumes within its deadline, a second drain then runs), then the three fixes above, one commit each. Not in scope: why the share did not answer; the folder watcher's design.
