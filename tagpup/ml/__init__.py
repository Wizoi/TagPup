"""Models: CLIP (clip), face detection and face embeddings (faces), and the vector
index (vector_index).

Imports only core and files. A model is made from the settings it is given, which an
entry point read (tagpup.config) and tagpup.runtime hands down; what it needs from the
library is handed to it too. Only tagpup.runtime makes one.
"""

import os
import pathlib
import sys

#: Set by the tests package: a model loaded here would be the real weights, a download
#: and a GPU load, in a suite that runs in forty seconds. One test did it for a whole
#: port without failing (docs/findings.md): its patches named classes the CLI had
#: stopped using, and still applied.
NO_WEIGHTS = "TAGPUP_NO_MODEL_WEIGHTS"


def under_test():
    """Is this a test run? The flag says so when the tests package set it, or a test
    passed it to a server it started. `unittest discover -s tests` never imports the
    tests package, so a run by unittest, or of a file in tests/, says so too."""
    if os.environ.get(NO_WEIGHTS):
        return True
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    if spec is not None and (spec.name or "").startswith("unittest"):
        return True
    path = sys.argv[0] if sys.argv else ""
    return bool(path) and pathlib.Path(path).resolve().parent.name == "tests"


def refuse_in_tests(what):
    """Raise instead of loading `what` in a test run."""
    if under_test():
        raise RuntimeError("%s would load real model weights under test (%s is set): "
                           "give the code a fake model instead" % (what, NO_WEIGHTS))
