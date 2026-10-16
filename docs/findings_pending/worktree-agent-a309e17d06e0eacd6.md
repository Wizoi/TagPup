# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-16 | low: docs/AI_PIPELINE.md plans on 68,460 photos and 226k faces, and on "the resolution question (224 or 512)" as if the library ran at 224: counted read-only this session, photo_index holds 59,519 photos and 187,784 faces (35,425 named, 27 excluded), and every photo vector of the three libraries is under the key `ViT-H-14|laion2b_s32b_b79k|full-frame|1.4|512`, so today's size is 512 and phase 0's check (b) compares 224 against it. Update the document's counts when section 7 is filled. | open |
| B-2 | 2026-10-16 | low: the brief for phase 0 asks for scripts under scripts/ that time `images.opened`, `ml/clip.py` and `ml/faces.py` and count with SQL, but a program may not import store, files or ml (tests/test_layers.py, PROGRAMS_TODAY) nor hold SQL; measure_pipeline_stages times the real objects through hooks on what the runtime and services hand it (embed_image's `seen`, forward hooks, wrappers on MTCNN's detect, PhotoIndex.build_or_update, services.faces.record_batch, Pillow's save) and the census reads through services (inspect, identify, photos). By design: the guards hold; a future brief that needs a stage the hooks cannot reach needs a store read or an ml helper of its own. | open |
| B-3 | 2026-10-16 | low: the strata count a photo's faces in play (services.identify.photos_waiting: named, or unnamed and not excluded), not every face found: on photo_index the difference is 27 excluded faces, on kr-track 5,788 of 9,850 faces are excluded, so kr-track's strata read 69 photos at 10 or more faces where the faces table holds more. By design for this measurement (the services expose no all-faces count without the vectors); said in the census output. | open |
