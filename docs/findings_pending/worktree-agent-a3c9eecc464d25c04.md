# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-12 | low: `_followed_rename(library, old, new, renamed)` (tagpup/web/tagpup_routes.py, #2852) takes the write's own `renamed` rather than deriving it from the two paths as the brief proposed: Smart Rename of one photo says renamed for a grouping changed only in case (`harbour` to `Harbour`), where paths.same(old, new) holds, and its records' path and filename must still take the new spelling; the save's and the conversion's `renamed` are paths.same's own. Derived, a case-only Smart Rename would leave the records spelled as before. A decision, not a defect. | decided: the write says whether it renamed; the follow-up does not guess it |
