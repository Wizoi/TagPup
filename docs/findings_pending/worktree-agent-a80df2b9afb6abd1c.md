# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-18 | low: the phase 3 face model's weights (`scrfd_10g_640.pt`, `scrfd_10g_1280.pt`, `adaface_ir101_webface12m.safetensors`) are not downloaded by the app, unlike facenet-pytorch's: the detector can only be made by conversion (onnx2torch, not in the project), so a missing file is a plain sentence naming each file, torch's checkpoints folder and how to make it (`scripts/make_scrfd_torchscript.py`; the AdaFace file from minchul/cvlface_adaface_ir101_webface12m), raised before anything loads (`scrfd_adaface.missing_weights`; tests/test_scrfd_adaface_faces.py). | decided: no automatic download (phase 3 merge 2a brief, from DECISIONS 2026-10-18 "PHASE 3 FACE MODELS": no onnxruntime or onnx2torch in the project); the files go where facenet-pytorch keeps its own. |
| B-2 | 2026-10-18 | low: the phase 3 detector's squares are scaled by Pillow (bilinear, reducing gap 3, the 640 square from the 1280 one), not OpenCV's area scaling as in the spike: on the sample's 10 worst photos it finds 336 faces at 640 against the spike's 369 and 2,070 at 1280 against 2,135 (2,081 merged), for half the processor's time (0.053 s for both squares against 0.102 s scaling each from the full picture). | decided: OpenCV is not in the main venv and is not added for this; 640's part is the frame-filling faces 1280 misses, which a lighter scaling does not lose; the named-face recall is phase 3's gate to measure on the hold-out (docs/AI_PIPELINE.md, section 7, "As built"). |
