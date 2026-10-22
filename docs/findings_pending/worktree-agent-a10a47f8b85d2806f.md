# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-22 | low: sweep sibling of #1737: store.face_tags._decided_of was a third gallery of who a face looks like, over all years and with the face's own photo in; it now builds clustering.FaceRows of the one person and asks FaceRows.likeness (moved in the WIP commit of cut 2). | fixed: phase 4a cut 2
| B-2 | 2026-10-22 | wrong: behaviour change of #1737: the own-photo rule (a face is never compared with a face of its own photo) now applies in Identify's boxes, the strip, automatch and a person's page as in cluster-faces and Suggest; and every matcher's year is dates.era_year of Date Taken, so 1,124 named faces of photo_index (counted 2026-10-22, read-only) whose year came from a folder name are now undated (all years). kr-track and renton_parkrun: 0. | decided: by design (main session, 2026-10-22: two faces in a photo are two people)
| B-3 | 2026-10-22 | low: sweep sibling of #1737 left: Identify's photo-details max_similarity (services.identify, the face card list) is still a raw max over every named face, its own face and photo included (a named face shows 1.0); not the one gallery. | ?
