# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-20 | low: with `FaceModelNotReady` deleted, the warm-up's `except REFUSALS` arm (`Runtime._warm`) had no instance: neither `Runtime.faces` nor `Runtime.clip` raises `OtherModelsInUse`, the other member. Deleted with it; `jobs/suggestions.REFUSALS` (one member left) gave way to `OtherModelsInUse` named in `_log_failure`. Found removing the model-not-kept refusal. | fixed: 3a67845a |
