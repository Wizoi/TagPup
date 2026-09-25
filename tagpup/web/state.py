"""The library a request names, for the routes.

What a server keeps for each library it serves is a `PerLibrary`
(tagpup.core.per_library), imported here so a route module needs one import.
"""
from flask import current_app, g

from tagpup.core.per_library import PerLibrary  # noqa: F401  (the routes' import)


def current():
    """The Library the request names (tagpup.web.libraries), or None when the URL names
    none -- the picker, the page before a library is chosen."""
    return getattr(g, "library", None)


def require():
    """The request's Library, or a 404 for a request that names none but needs one."""
    from flask import abort
    library = current()
    if library is None:
        abort(404, description="No library: choose one first")
    return library


def runtime():
    """The process's models (tagpup.runtime.Runtime) the app was made with, or None."""
    return current_app.config.get("RUNTIME")


def settings(library):
    """The library's settings (tagpup.runtime.library_settings): stamped the first time a
    library holding none is opened."""
    from tagpup import runtime as runtimes
    return runtimes.library_settings(library)


def exiftool(library):
    """The ExifTool program the library's reads and writes of photo files go through:
    the one it names, else the machine's."""
    from tagpup import runtime as runtimes
    return runtimes.exiftool(library)


def rename_format(library):
    """The pattern Smart Rename names the library's photos with."""
    return settings(library).rename_format
