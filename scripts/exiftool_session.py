"""Moved to tagpup/files/exiftool_session.py. `import exiftool_session` keeps working until nothing uses it.

This replaces itself with the moved module, so both names are one module: a patch
through either reaches every caller.
"""
import sys

import _root  # noqa: F401
from tagpup.files import exiftool_session

sys.modules[__name__] = exiftool_session
