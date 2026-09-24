"""Moved to tagpup/files/identity.py. `import identity` keeps working until nothing uses it.

This replaces itself with the moved module, so both names are one module: a patch
through either reaches every caller.
"""
import sys

import _root  # noqa: F401
from tagpup.files import identity

sys.modules[__name__] = identity
