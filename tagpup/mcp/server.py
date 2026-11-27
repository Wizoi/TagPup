"""The MCP server's tools: one per read of tagpup.services.inspect, `libraries`, the last
sync (`sync_state`) and the journal (`history`). Every tool reads; none writes: a write
(sync --apply, undo) is the CLI's (tagpup_cli.py) or the pages'.

Each tool is a thin call: it finds the library the caller named in the home's data
folder (tagpup.config), refusing a name that is not there, calls the service, and
returns what it answered as JSON. Answers carry counts and ids; paths, names and tags
only when the caller passes reveal=true, since the library is photographs of real
people, many of them minors. An error says what went wrong without the paths or names
an exception can carry, unless the call revealed them anyway.

Logging goes to data/logs/tagpup_mcp.log (tagpup.logs), never stdout: over stdio,
stdout carries the protocol and nothing else.
"""
import logging
import os
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from tagpup import config, logs
from tagpup import runtime as runtimes
from tagpup.core import library as libraries
from tagpup.core.library import Library
from tagpup.core.result import NotFound, Refused
from tagpup.services import inspect
from tagpup.services import libraries as library_service
from tagpup.services import roots as roots_service
from tagpup.services import sync as sync_service
from tagpup.services import journal as library_journal

logger = logging.getLogger(__name__)

#: What a client is told the server is for, when it connects.
INSTRUCTIONS = (
    "Questions about a TagPup photo library: what it holds, photos by folder, tag or "
    "person, a photo's row against its file, the faces in a photo, the consistency checks "
    "tools/doctor.py runs, rows whose file is gone, when the library was last in step with its "
    "folders, the plan of a query, and the library's journal (`history`). Every tool reads; "
    "none writes (a sync --apply or an undo is the CLI's, tagpup_cli.py). Call "
    "`libraries` first; every other tool names one of them. Answers give counts and ids. "
    "Paths, names and tags -- tags name people -- are given only with reveal=true: the "
    "library is photographs of real people, many of them minors. Ask for them only when "
    "the question needs them.")

#: Every tool reads; none writes, and asking twice answers the same.
READS = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

#: How a tool description ends: what `reveal` gives.
REVEAL = " Names, paths and tags only with reveal=true."


def library_names():
    """The libraries in the home's data folder, as the picker names them. Only libraries
    that are there: the picker offers photo_index when there are none, which this must
    not, as no tool creates one."""
    folder = config.data_dir()
    files = os.listdir(folder) if os.path.isdir(folder) else []
    return [name for name in libraries.picker_names(files, test_mode=False)
            if os.path.exists(config.library_path(name + ".db"))]


#: What a read tool says when the library is newer than this version: set by find_library, added to the answer by _answer.
_notes = []


def find_library(name, photos=True):
    """The Library called `name` in the home's data folder; ToolError when there is none. And, for a
    tool that reads photos' paths (`photos`), when the library holds a root this machine does not
    place: the message names machine_roots.json and the line to add (tagpup.services.roots.problem),
    where the store would refuse the first path with a traceback's last line."""
    if not name or name not in library_names():
        raise ToolError("There is no library called %r; `libraries` lists them." % (name,))
    found = Library(config.library_path(name + ".db"))
    if library_service.newer_problem(found.path):
        _notes.append(library_service.newer_note(found.path))   # a library of a newer TagPup is read as it is
    if photos:
        unplaced = roots_service.problem(found)
        if unplaced:
            raise ToolError(unplaced)
    return found


def _embedder(library):
    """The CLIP settings the library's vectors are counted against, or None where the library's settings
    cannot be read -- its folder settings hold paths, which a root this machine does not place cannot
    name: the checks then say so (tagpup.services.inspect), and count nothing against a model."""
    try:
        return runtimes.peek_settings(library).embedder
    except ValueError:
        return None


def _answer(action, reveal=False):
    """Run `action`, turning what the service refuses into the tool's error. An error
    the service did not expect is logged whole and answered by its kind alone, unless
    `reveal`: its message can hold a path. Every tool reads, inside `reading_newer()`: a newer library is read
    as it is."""
    del _notes[:]
    try:
        with library_service.reading_newer():
            answer = action()
        if _notes and isinstance(answer, dict):
            answer = dict(answer, note=_notes[0])
        return answer
    except ToolError:
        raise
    except (NotFound, Refused) as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.exception("A tool failed")
        raise ToolError("%s (%s); the log, data/logs/tagpup_mcp.log, has the detail."
                        % (type(e).__name__, e) if reveal else
                        "%s; the log, data/logs/tagpup_mcp.log, has the detail." % type(e).__name__) from e


def build():
    """The server and its tools, without starting it or logging anywhere: what main()
    serves over stdio, and what the tests talk to in process."""
    server = FastMCP("tagpup", instructions=INSTRUCTIONS, log_level="WARNING")

    def tool(description, name=None):
        return server.tool(name=name, description=description, annotations=READS)

    # Named here: FastMCP names a tool after its function, and `libraries` is a module.
    @tool("The libraries in this TagPup home's data folder, by name. Every other tool takes "
          "one of these as `library`.", name="libraries")
    def list_libraries() -> dict[str, Any]:
        return {"libraries": library_names()}

    @tool("What a library holds, as tools/doctor.py prints it: photos, faces, named, named by hand, "
          "excluded, untagged; the schema's version, the tag tree's nodes, the people the photos "
          "list, and photos without a vector for the library's photo model. Counts only.")
    def summary(library: str) -> dict[str, Any]:
        def read():
            found = find_library(library, photos=False)
            return inspect.summary(found, _embedder(found))
        return _answer(read)

    @tool("The folders a library's photos are directly in, the fullest first: each one's number, "
          "photo count and whether it is on disk. `limit` caps the folders listed." + REVEAL)
    def folders(library: str, reveal: bool = False, limit: int = inspect.LIMIT) -> dict[str, Any]:
        return _answer(lambda: inspect.folders(find_library(library), reveal, limit), reveal)

    @tool("The photos under a folder (any depth), carrying a tag or a tag under it, or listing a "
          "person among their people; each one given narrows the others. Answers their count and "
          "ids (up to `limit`). A person is a name or their tag, spelled as the photos spell it."
          + REVEAL)
    def photos(library: str, folder: Optional[str] = None, tag: Optional[str] = None,
               person: Optional[str] = None, reveal: bool = False, limit: int = inspect.LIMIT) -> dict[str, Any]:
        return _answer(lambda: inspect.photos(find_library(library), folder, tag, person, reveal, limit), reveal)

    @tool("A photo's row, by photo id, against what its file holds now, read with ExifTool as the "
          "indexer reads it: whether the file is there, its modified time and size, tags, captions, "
          "the people its keywords name, its DocumentID, and which raw metadata fields differ. "
          "`stale` says whether anything does. The file is only read." + REVEAL)
    def photo_against_file(library: str, photo_id: int, reveal: bool = False) -> dict[str, Any]:
        def read():
            found = find_library(library)
            with runtimes.fields(found, runtimes.peek_settings(found)) as fields:
                return inspect.photo_against_file(found, photo_id, fields, reveal)
        return _answer(read, reveal)

    @tool("The faces in a photo, by photo id: each face's id, box, whether it is named and by whom "
          "(name_source 'manual' is a person's decision), detection confidence, and whether it is "
          "excluded and why; and how many people the photo lists. Never the embedding or crop."
          + REVEAL)
    def faces_in_photo(library: str, photo_id: int, reveal: bool = False) -> dict[str, Any]:
        return _answer(lambda: inspect.faces_in_photo(find_library(library), photo_id, reveal), reveal)

    @tool("One consistency check of tools/doctor.py, by name: how many rows break it, and a few "
          "of them as ids. The checks: " + ", ".join(inspect.CHECKS) + "." + REVEAL)
    def check(library: str, name: str, reveal: bool = False) -> dict[str, Any]:
        return _answer(lambda: inspect.check(find_library(library, photos=False), name, reveal), reveal)

    @tool("Every consistency check tools/doctor.py runs, in its order, with how many rows break "
          "each and how many checks are broken; and how many photos have no CLIP vector for the "
          "library's photo model, and `people_at_the_top` -- the people directly under the tag tree's root, "
          "not yet filed in a branch (reported, not broken). Rows whose file is gone are missing_files'."
          + REVEAL)
    def checks(library: str, reveal: bool = False) -> dict[str, Any]:
        def read():
            found = find_library(library, photos=False)
            return inspect.all_checks(found, reveal, _embedder(found))
        return _answer(read, reveal)

    @tool("The rows whose file is not on disk, by folder: whether each folder is gone entirely, its "
          "rows as ids (up to `limit` a folder), and the faces on them -- named, named by hand, "
          "excluded, and how many different names. Answers which names sit on rows of a folder "
          "whose files are gone (findings #42). Read only; a whole sync removes the rows of files it "
          "confirms gone. A photo on a network share "
          "that does not answer is counted `not_looked_at`, never gone." + REVEAL)
    def missing_files(library: str, reveal: bool = False, limit: int = inspect.LIMIT) -> dict[str, Any]:
        return _answer(lambda: inspect.missing_files(find_library(library), reveal, limit), reveal)

    @tool("The plan SQLite would follow for a query on a library (EXPLAIN QUERY PLAN): which index "
          "each step uses, and which steps scan a table. The query is never run. Only a single "
          "SELECT or WITH statement that only reads; anything else is refused. `params` are the "
          "placeholders' values; any not given are planned as NULL. Answers table and index names, "
          "never rows.")
    def query_plan(library: str, sql: str, params: Optional[list] = None) -> dict[str, Any]:
        return _answer(lambda: inspect.query_plan(find_library(library, photos=False), sql, params))

    @tool("When the library was last in step with its folders, and its last applied sync: when it "
          "started and finished, whether it looked at the whole library, whether it left it in step, "
          "and what it found and changed, as counts. Both null for a library never synced.")
    def sync_state(library: str) -> dict[str, Any]:
        return _answer(lambda: sync_service.last(find_library(library, photos=False)))

    @tool("The library's journal: every change a maintenance operation applied, newest first (up to "
          "`limit`), each with its id, operation, status (applied, derived_pending, undone), "
          "the schema version it was made at, when it was made, applied and undone, its summary "
          "(counts), how many rows of each table it inserted, updated and deleted, and `why_not`: why "
          "the CLI's `undo` would refuse it now (a sentence), or null when it may be tried. With `change`, "
          "that change alone and the keys (ids) of every row it wrote; with reveal=true too, each "
          "column's value before and after (a BLOB by its size), which can name people.")
    def history(library: str, change: Optional[int] = None, reveal: bool = False,
                limit: int = 20) -> dict[str, Any]:
        return _answer(lambda: library_journal.history(find_library(library, photos=False), change, reveal, limit), reveal)

    return server


def main():
    """Serve over stdio until the client goes, logging to data/logs/tagpup_mcp.log."""
    # Before FastMCP is made: it gives the root logger a console handler when it has none.
    logs.to_file("tagpup_mcp")
    logger.info("Serving the libraries in %s over stdio", config.data_dir())
    build().run("stdio")
    return 0
