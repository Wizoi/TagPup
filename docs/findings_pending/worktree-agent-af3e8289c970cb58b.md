# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: Integration (decided on the queue branch): the folder-scan cache's stamp is the photos and taxonomy counters (the taxonomy one because a photo read from its file is told the tree's people, `photos._read`); every hand call of `forget_scans` and `runtime.folder_indexed` went, each followed a write that moves the photos counter. The `entries_for` patches stay: a file-only write of a loose photo moves no counter, and the time shift's reply sends the patched records; for a held photo they are redundant (its scan is dropped at the next get) but harmless. The time shift now fills the cache through `_folder_photos` (`scan_folder`, the rows when a row's stamp matches) where it read every file with `read_folder`. | open |
| B-2 | 2026-10-15 | low: Files/Data: `libraries.not_in` has no caller in the code (four tests use it as a predicate: test_folder_membership, test_index_adds_the_folder_it_is_given, test_one_answer_to_the_librarys_folders, test_rows_only_in_folders_added); its sentence "<folder> is not in <library>. Add it to <library> first." is said by nothing since `refuse_writes` went. Delete it and ask `not_held` in the tests, or give it its caller. | open |
| B-3 | 2026-10-15 | low: Integration: `launcher.wait_for` and its caller keep `started is not None` arms (launcher.py :547, :632, :645, :651) from when the always-on process's server was waited for with no Popen; whether any caller still passes None was not checked on the queue branch. | open |
| B-4 | 2026-10-15 | low: Integration: `.claude/agents/tagpup-worker.md` still says "the always-on server" (:38) and "the always-on process updating" (:56); left on the queue branch (an agent definition, not the brief's file). Say "the server". | open |
| B-5 | 2026-10-15 | low: Integration: what #1360/#1361 named and the queue branch left: the pre-pathKey localStorage key (a compatibility arm, not in the brief), and about 60 page modules plus `services/tagging.py` and `store/inspection.py` in no owner charter with `test_the_test_map` not requiring every module to be listed (tagging.py's placement is Files'). | open |
