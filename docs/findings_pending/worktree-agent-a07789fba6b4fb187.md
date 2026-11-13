# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-13 | low: `.claude/hooks/plan-first.mjs` reads a brief naming the integration owner (its charter, or `Domain: Integration`) as a feature across two domains, as its brief asked; so a worker on the integration owner's own shell (a hook, the launcher, a route), one domain, is refused until it cites a plan. The brief that built the hook is one such launch. Decide: keep (such a brief cites a plan or names the work's other domain), or match only two `Domain:` lines. | open |
| B-2 | 2026-11-13 | low: `.claude/hooks/plan-first.mjs` counts `Domain:` LINES, as its brief asked; one line naming two owners (`Domain: Files and Media`) passes without a plan. Not matched because no such brief was counted (the briefs are not stored); if one is seen, the hook splits a Domain line's value on `and` and commas. | open |
| B-3 | 2026-11-13 | low: `.claude/hooks/plan-first.mjs` checks that a cited plan exists in the project folder (CLAUDE_PROJECT_DIR) or the session's working folder, not that it is committed; a worker's worktree is made from HEAD, so an uncommitted plan passes the hook and is absent where the worker reads. docs/plans/README.md says to commit it before the first launch; a `git ls-files` check in the hook would make the rule a guard at the cost of a git call per launch. | open |
