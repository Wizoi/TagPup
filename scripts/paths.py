"""Moved to tagpup/core/paths.py. `import paths` keeps working until nothing uses it.

This replaces itself with the moved module, so both names are one module: a patch
through either reaches every caller.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from tagpup.core import paths  # noqa: E402

sys.modules[__name__] = paths
