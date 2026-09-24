"""Puts the repository root on sys.path, so code in scripts/ can import tagpup.

Everything in scripts/ is imported with scripts/ itself on sys.path, and a script run
directly (`python scripts/refresh_rows_from_files.py`) has only that. A module here
that imports tagpup does `import _root` first; tests/test_layers.py fails one that
does not, since it would work or not depending on what happened to be imported before.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)
