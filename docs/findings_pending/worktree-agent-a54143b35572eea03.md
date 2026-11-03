# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-02 | wrong: Organize's selection panel counted a photo twice for one suggested person when a Suggest run listed them in both its `people` and its `tags` (`noteSuggestion` in web/tagpup/selection.js incremented `count` on every call, though `paths` kept the photo once): "Kira Bao (2)" for one photo. Fixed: a photo counts once a person; the faces' suggestions now come through the same function. The test (selection-suggests-who-the-faces-look-like, "counts once") fails on the old code. | fixed |
| B-2 | 2026-11-02 | low: the brief for the selection's face suggestions said `faces.panel` applies "the 2% background floor, images.BACKGROUND_SHARE"; nothing of the name is on the trunk (grep of tagpup/ and web/), and the strip shows every unnamed face's suggestion at the offer cut, whatever its size. `selection_suggestions` offers the strip's people at the strip's cut, no floor, and reads no file (a photo with no size changes nothing). | ? |
| B-3 | 2026-11-02 | low: a Suggested People chip that comes from the faces alone still says "or leave it for Apply All, which writes everything here", but Apply All (`/api/folder/auto-apply`, suggestions.js) writes only what a Suggest run stored; the chip's own click writes it. Left as the brief said ("the chips' count, Apply and the rest stay as they are"): the owner decides whether Apply All takes the faces' people too or the title says it does not. | ? |
