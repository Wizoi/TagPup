# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-18 | low: `face-pass` without `--apply` asks the new model about every photo not done, holding the graphics card, so a dry run of a large folder takes as long as the run less its writes (about 0.3 s a photo by the phase 3 spike). Worker on slice B. | decided: by design (docs/AI_PIPELINE.md, slice B as built: the dry run counts what --apply would write, slice A2's rule) |
| B-2 | 2026-10-18 | low: the brief's "`undo` names the first ten reasons a change was refused" was already the journal's (`tagpup.store.journal.Refusal`: the first ten and "and N more"); the CLI prints it as it is. A rehearsal that does not round-trip lists up to `journal.SHOWN` (20) differences instead, a second number for one idea; a face pass's undo is refused before that point. Worker on slice B; scenario P8 from the command line shows the ten. | decided: by design (docs/AI_PIPELINE.md, slice B as built) |
