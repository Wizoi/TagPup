# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-12 | low: a pick from the tag or person box's list is saved by the pick (#2834), whether it was picked by Enter or by a click: the page sees only the browser's replacement of the text (Chrome's `input` event with no inputType, Firefox's `insertReplacementText`) and cannot tell the two apart. A typed or pasted text equal to a list entry is not a pick and waits for Enter. The event shape is the browsers' documented one, tested in jsdom only (tests/frontend/tagging-by-keyboard.test.mjs). | decided: both are "picked from the list", and the owner asked for fewer clicks; the owner's first pick in Chrome after the install is the check |
| B-2 | 2026-11-12 | low: Left at the start of the tag or person box, or Right at its end (either in an empty box), steps to the previous or next photo (#2835); with text left in the box the step asks Save/Discard/Cancel, as every way off a photo does. A selection, Shift, Ctrl or Alt, and Up and Down (they walk the list) stay the box's. | decided: text editing is never broken, and no typed text is left behind unasked |
| B-3 | 2026-11-12 | low: the tag and person text is taken, and its box emptied, when a save is asked for (#2836), so keys typed during the save start the next tag; a save that fails gives its text back only to an empty box of the photo still open, and otherwise the failed entry's Retry holds it. The check that refused a queued save whose photo was no longer open is gone: the text is taken with its photo, so it is written to that photo. | decided: what was typed during the save is the owner's newer text, and is never overwritten |
