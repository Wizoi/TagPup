# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-28 | low: `scripts/measure_pipeline_holdouts.py` compared every candidate's word path with the baseline's precision at `WORD_CUT = 0.23`, ViT-H-14's cut, while the baseline (`CANDIDATES[0]`) is SigLIP 2 SO400M, whose table cut is 0.11 (`tagpup/ml/photo_models.py`): the Word cut column of `table` answered a question at a point no library uses. Fixed: `WORD_CUT` is read from the table through a new `services.settings.photo_word_cut` (a program may not import tagpup.ml); `test_the_baselines_word_cut_is_the_tables_for_its_model` fails on the old code (0.11 != 0.23). Decision: the cut is the baseline candidate's, taken to be the library's model, not read from the `--source` library's settings (`table` runs without a library). | fixed: this branch |
| B-2 | 2026-10-28 | low: `scripts/prepare_test_environment.py` seeded its two faces under `MTCNN_FACENET` (a model no library holds since Cut B) and its photo vectors at 1024 dimensions (ViT-H-14's), not the default model's 1152. Fixed with docs/OLD_MODEL_VESTIGES.md row 22: `SCRFD_ADAFACE` and 1152. No test drives the script's database (it seeds the screenshots' sandbox), so none failed first. | fixed: this branch |
