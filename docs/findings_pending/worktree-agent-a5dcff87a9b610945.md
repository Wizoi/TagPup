# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-08 | low: decided in cut C, not copied: a clip over recycle_bin.MOST_CLIP_BYTES goes for good on ANY drive, a local one whose Bin would keep it too (DECISIONS 2026-11-02 item 7 read literally, and the brief), so the rule is one size test with no drive branch; photo_index holds 11 clips over 2 GiB, the largest 9,635 MB (counted read-only 2026-11-08) | decided: one rule, recycle_bin.too_large_to_copy (SQL: selection_folders.clips_over and bytes_by_folder's third column); tests/test_a_clip_deleted.py AClipOverTheLimit |
| B-2 | 2026-11-08 | low: decided in cut C: the reply of a delete for good says why per kind (`why_for_good` from recycle_bin.delete_file: 'the Recycle Bin of its drive would not keep it', or 'a video this large is not copied to a Recycle Bin'), and the page's one warning (bulk-words.forGoodQuestion) names both reasons in one sentence rather than a second warning | decided: one flag, one warning, one token; the reason is a detail of the reply |
| B-3 | 2026-11-08 | low: a panel delete for good of a big clip on a share that stops answering waits on os.remove (unbounded), as the through-this-PC copy, its looks (_judged's isfile) and its remove of the original already did before this cut; the bulk Delete takes a bounded stamp of each file first (file_changes.file_stamps) | decided: same as every delete of a file on a share since #694; os.remove is one round trip, against the copy of gigabytes it replaces |
