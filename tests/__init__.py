"""The tests package.

No test loads real model weights (tagpup.ml.refuse_in_tests). The flag is set here for a
run that imports the package (tools/run_tests.py, `-m unittest tests.<file>`), and
inherited by a server such a run starts. `unittest discover -s tests` never imports this
package: the loaders then know the run by its main program, and the tests that start a
server pass the flag themselves.

A run that asks (TAGPUP_TEST_EXIFTOOL, set by tools/run_tests.py --replay-exiftool or --real-exiftool) is given
ExifTool's kept answers here too (tests/exiftool_replay.py): this process only, never a server it starts.
"""
import os

os.environ.setdefault("TAGPUP_NO_MODEL_WEIGHTS", "1")
if os.environ.get("TAGPUP_TEST_EXIFTOOL") in ("replay", "record"):
    from tests import exiftool_replay
    exiftool_replay.install()
