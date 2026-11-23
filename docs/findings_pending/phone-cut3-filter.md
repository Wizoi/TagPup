# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-23 | low: the phone's Filter tab (or the Filter chip) over a Grid view that is not one row's source opens a new Grid step on the whole library with no mark, not over that view: a people group's Show these photos (an any_of of its people; mobile-filter.js filterOf takes a single member only) and an address naming a folder without its subfolders (`?view=folder&value=...`, recursive false, no row's source). Probed in jsdom on branch phone-cut3-filter: People, Family, Show these photos, then Filter gives kind all. user action: People, a group, Show these photos, then Filter. A union of one tab's rows could open with each marked `+`; a folder alone has no mark that means it. | open |
