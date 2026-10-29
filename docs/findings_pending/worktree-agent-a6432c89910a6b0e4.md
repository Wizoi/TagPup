# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-28 | low: a library with no named roots never removes the rows of a held folder deleted whole: `sync._removable` takes the topmost folder walked above a row as its root's first place, and a folder that is gone is not walked, so nothing confirms it. Live count (this session, read-only): 0; all three live libraries' paths are under roots (`store.roots.roots_for` not identity), so none takes the no-roots arm, though one of them names no root folder in its settings (`library.roots` empty) and walks the 8 folders it holds. | decided: by design; no instance (CLAUDE.md, a special case needs a real instance), and the rows stay reported as missing, which is the safe side |
| B-2 | 2026-10-28 | low: the Files owner's charter (contract item 4 and the invariant row) still said sync's missing files are "reported, never removed" after DECISIONS 2026-10-28 corrected it. | fixed: docs: sync removes the rows of files it looked for and did not find, its root confirmed there; the specs, invariant, charter and scenario say so |
| B-3 | 2026-10-28 | low: the sync CLI's line on the kept missing rows said "under a root not confirmed there", which put the word "root" in the report of a library with no roots, the wording #917 took out; the test of #917 failed on it. | fixed: cli: sync's kept missing rows said without 'root', which a library with no roots does not have (#917) |
