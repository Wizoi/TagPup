# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-17 | low: with #1599's fix (IdentityWriter.give keeps the stamp from before the read), the index after one that gave photos their identity reads each of them once more through the whole pipeline -- CLIP (its kept vector is stamped from before the identity write, so it no longer describes the file), face detection, and the clustering at the run's end -- because the CLI index has no metadata-only path for a changed file; sync has one (refresh_rows.reread: "its vector and faces are left as they are"). On the server the watcher's sync may re-read them first, cheaply, and journals it as a change of N rows; its CLI line counts each as a picture whose size changed. A CLI index of a new folder therefore costs its GPU work twice; nothing loops (a third index reads nothing, tested). Choices for the Files owner: by design (the owner's direction for #1599), or the run re-reads the photos it gave an identity through refresh_rows.reread before it ends. Worker on #1599. | open: the Files owner decides |
