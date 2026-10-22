# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-22 | wrong: TagTuner Identify, the windowed Unclustered tab: removing faces in place recounted the shown cards but not the window's total, so the banner read "first 497 of 1979, more appear as you clear these" after three of the 1979 were excluded (seen in a sandbox copy of a live library). The total now follows removals and Undo (`grid-parts.js`); `identify-faces-in-place-removal.test.mjs` "far down a long grid" failed on the old code first. | fixed: the window's total follows the faces (this branch) |
| B-2 | 2026-10-22 | low: TagTuner Identify, the banner holding the selection's actions (Assign Selected, Unmatch, Exclude, Restore, Select All) scrolled away with the grid, and the Undo bar of an assign or ignore sat at the top of the grid card: 5,000 px down a person's faces neither was in view, so every removal meant scrolling back to the top and finding the place again. The banner (heading card plus the Undo bar) is now one sticky block at the top of the panel; seen in headless Chromium on a sandbox copy. | fixed: the banner floats (this branch) |
