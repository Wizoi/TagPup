# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-23 | low: Integration: the deadline on the folder watcher's stop is kept in two places -- `runtime.Background.stop` runs each task's stop on a daemon thread joined to the drain's deadline, and `watching.Watcher.stop` runs watchdog's `observer.stop()` on a thread of its own (FolderWatchesStopping) joined to the same length again; when the watches hang, `Watcher.stop` returns True at about the deadline, so whether the drain's log names `folder watcher (still stopping)` depends on which join ends first, and the watching log's "did not stop" line comes after the drain has answered (found writing the #3345 scenario test). Either bound alone ends the drain at its deadline. user action: an install while a watched share stops answering. | open |
