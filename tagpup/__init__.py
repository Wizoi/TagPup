"""TagPup: the photo library, organized by layer. See docs/ARCHITECTURE.md.

Imports only go down: core <- store, files <- ml <- services <- jobs <- entry points.
tests/test_layers.py fails the build on an import that goes the other way.
"""
