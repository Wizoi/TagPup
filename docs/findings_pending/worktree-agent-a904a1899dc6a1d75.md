# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | low: `tools/code.py` (named by the brief) shares its name with the standard library's `code` module. 13 test files put `tools/` first on `sys.path` in the suite's one process (`test_patch_tool`, `test_add_findings`, `test_doctor`, ...); a later `import code` there gets the tool (`werkzeug.debug.console` imports `code` at its top, `pdb` too, whose `interact` command would break). Today nothing in the suite imports `code`, and `tests/test_code_tool.py` loads the tool from its file under a name of its own. Kept as briefed; renaming to `tools/read_code.py` changes the hook's message, the two charters and the test. | open |
| B-2 | 2026-11-13 | low: the read-budget hook counts lines, not bytes: a text file under 300 lines with very long lines (a saved tool result of one JSON line) is let through whole; the Read tool cuts each line at 2,000 characters. The brief asked for a line count; the retro's 18-26k saved tool-result reads were not measured for their line counts this task. | open |
