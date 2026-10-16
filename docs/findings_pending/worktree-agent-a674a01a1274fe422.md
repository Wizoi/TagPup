# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-16 | low: `docs/DEVELOPMENT.md` "Tests" table still gives the Python suite as ~1,300 tests and the frontend as ~500, while CLAUDE.md (and the tiers paragraph moved beside it on this branch) say about 4,300; one of the two is a stale number of the kind the retro's instruction diff removes. Count both suites once and correct the table. Found moving CLAUDE.md's running-things text, retro 2026-10-16 action 3. | fixed: 2dda637c (3,603 Python test cases and 1,586 node tests, counted 2026-10-16, in both places) |
| B-2 | 2026-10-16 | low: `docs/portable/hooks.example/no-heredoc-escapes.mjs` is a snapshot of `.claude/hooks/no-heredoc-escapes.mjs` (asked for by the brief of the portable kit), so the hook is now written twice and the copy can drift; its header says the source copy is the one kept up to date. Either keep it (a kit must be copyable without the repository) or replace it by INSTALL.md's copy line alone. | fixed: a34b067e (the snapshot deleted; INSTALL copies from .claude/hooks/) |
