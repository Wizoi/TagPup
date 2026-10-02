"""Where this machine keeps each root of a library: asked at the database boundary.

The store converts a photo's path to and from the library's root-relative form
(tagpup.core.paths: to_row, from_row), and needs, for a library that has roots, the
machine's map of them (machine_roots.json in the TagPup home, read by tagpup.config). The
store may import only core, and config is read where a program starts, so the one reader
is given here, once: tagpup.config registers its `roots_of` when it is imported, and the
store asks. A library with no roots never asks (every path function then behaves as it
always did), so a process that never imported config cannot open a library with roots by
mistake and get a map of nothing: it is told, and does not guess.

Nobody has to remember to import config first: asked with no reader registered, `roots_of`
imports tagpup.config itself, which registers. (A dynamic import, since core may import nothing
above it and the layer test reads the imports a module writes: config's own import of core is the
declared direction; this is the one place core asks upward, to be answered by the layer whose
whole job is the machine.)
"""
import importlib

from tagpup.core import paths

__all__ = ["provide", "roots_of", "IDENTITY"]

#: The Roots of a library with none: every path function is then the old one.
IDENTITY = paths.Roots()

_provider = None


def provide(reader):
    """Register `reader(logical)` -> paths.Roots, where `logical` is the library's {root
    name: the share's address}. tagpup.config does, at import."""
    global _provider
    _provider = reader


def roots_of(logical):
    """The paths.Roots of a library whose roots are `logical`, on this machine. IDENTITY for
    none. paths.RootsError, never a guess, when there are roots and nobody says where the
    machine keeps them."""
    if not logical:
        return IDENTITY
    if _provider is None:
        try:
            importlib.import_module("tagpup.config")
        except Exception as problem:
            raise paths.RootsError(
                "this library has roots (%s) and the machine's map reader could not be loaded: importing "
                "tagpup.config failed with %s: %s" % (", ".join(sorted(logical)), type(problem).__name__,
                                                      problem)) from problem
    if _provider is None:
        raise paths.RootsError(
            "this library has roots (%s) and no machine map is available: tagpup.config was not "
            "imported, so there is no machine_roots.json to say where this machine keeps them"
            % ", ".join(sorted(logical)))
    return _provider(dict(logical))
