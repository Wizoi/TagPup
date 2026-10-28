# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-28 | low: `runtime._build_faces` now builds ScrfdAdaFace whatever `faces.model` reads (the FaceModel arm deleted, Cut B build 1), while a library can still read `mtcnn-facenet`: `validation.FACE_MODELS` keeps it as a choice and `validation.BEFORE_FACE_MODELS` is what a library holding settings but no faces.model row reads. Such a library would file AdaFace vectors under `scrfd-adaface` and read faces under `mtcnn-facenet`. No live instance (the 3 libraries hold the scrfd-adaface row and 0 FaceNet vectors, docs/OLD_MODEL_VESTIGES.md's counts, not recounted this task: a sync was running in the owner's home), and a switch to it is refused (`settings.NOT_FACENET`); so no branch was written (CLAUDE.md, "A special case needs a real instance"). Closed by the later Cut B build that deletes MTCNN_FACENET from validation and BEFORE_FACE_MODELS (rows 3, 4). | open |
| B-2 | 2026-10-28 | low: `scripts/measure_pipeline_stages.py` times MTCNN (`_watch_faces` hooks `model.mtcnn` / `model.resnet`, `stages_of`'s "MTCNN" span), which no runtime builds since `ml/faces.py` went (Cut B build 1): run on any library it fails at the first photo's faces. Its `import facenet_pytorch` was deleted with the package, and its FaceModel test (`test_pipeline_measurements.test_faces_are_timed_from_their_decode_to_the_detector`). docs/OLD_MODEL_VESTIGES.md row 22, CONVERT or DELETE in a later Cut B build. | open |
