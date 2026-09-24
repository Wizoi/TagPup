"""Moved to tagpup/store/taxonomy.py. `import taxonomy` keeps working until nothing uses it.

This replaces itself with the moved module, so both names are one module: a patch
through either reaches every caller.
"""
import sys

import _root  # noqa: F401
from tagpup.store import taxonomy

sys.modules[__name__] = taxonomy
