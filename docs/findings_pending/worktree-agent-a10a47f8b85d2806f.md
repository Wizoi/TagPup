# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-22 | low: sweep sibling of #1737: store.face_tags._decided_of was a third gallery of who a face looks like, over all years and with the face's own photo in; it now builds clustering.FaceRows of the one person and asks FaceRows.likeness (moved in the WIP commit of cut 2). | fixed: phase 4a cut 2
| B-2 | 2026-10-22 | wrong: behaviour change of #1737: the own-photo rule (a face is never compared with a face of its own photo) now applies in Identify's boxes, the strip, automatch and a person's page as in cluster-faces and Suggest; and every matcher's year is dates.era_year of Date Taken, so 1,124 named faces of photo_index (counted 2026-10-22, read-only) whose year came from a folder name are now undated (all years). kr-track and renton_parkrun: 0. | decided: by design (main session, 2026-10-22: two faces in a photo are two people)
| B-3 | 2026-10-22 | low: sweep sibling of #1737 left: Identify's photo-details max_similarity (services.identify, the face card list) is still a raw max over every named face, its own face and photo included (a named face shows 1.0); not the one gallery. | fixed: 689f7e9f |
| B-4 | 2026-10-22 | wrong: phase 4a cut 2 left store.face_tags._remember behind: a batch's decided faces (`batch.decided`) are now clustering.FaceRows (`_decided_of`), and `_remember` still reads `known.shape` and vstacks a row onto them, so naming faces from tags in an index batch raises AttributeError ('FaceRows' object has no attribute 'shape') once a person of the batch has decided faces; tests/test_faces_from_tags.py test_fifty_photos_of_one_person_read_their_decided_faces_once fails. Not fixed in the test-repair slice (not its file). | ? |
| B-5 | 2026-10-22 | low: tests/test_clustering_keeps_a_name_the_tags_confirm.py's stand-in likeness (0.75, 0.40 for crowd.jpg) is never reached, on 98e4de0c (KnownFaces.likeness: 0 calls) as now (FaceRows.likeness_of and FaceRows.likeness: 0 calls, checked): the crowd's faces are named before any likeness is asked, so the tests prove the names kept but not 'between 0.70 and 0.80' or 'below 0.70'. | ? |
| B-6 | 2026-10-22 | low: docs/DATABASE.md (photos.year) still says a face is compared across years by tagpup.core.clustering.KnownFaces, deleted in cut 2; the matchers read dates.era_year of Date Taken now, not photos.year. | ? |
