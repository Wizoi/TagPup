"""The MCP server's tools: one per read of tagpup.services.inspect, `libraries`, and one
per maintenance operation (tagpup.services.maintenance's scaffold).

Each tool is a thin call: it finds the library the caller named in the home's data
folder (tagpup.config), refusing a name that is not there, calls the service, and
returns what it answered as JSON. Answers carry counts and ids; paths, names and tags
only when the caller passes reveal=true, since the library is photographs of real
people, many of them minors. An error says what went wrong without the paths or names
an exception can carry, unless the call revealed them anyway.

The write tools are the maintenance scripts' operations, the same services the scripts
call. Each is a dry run unless called with apply=true; applying backs the library up
once, which the service does -- a tool takes no backup of its own. A refused Result is
an answer, not an error: nothing was written, and it says why.

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
from tagpup.core import library as libraries
from tagpup.core.library import Library
from tagpup.core.result import NotFound, Refused
from tagpup.services import duplicate_faces, inspect, person_tags, refresh_rows

logger = logging.getLogger(__name__)

#: What a client is told the server is for, when it connects.
INSTRUCTIONS = (
    "Questions about a TagPup photo library: what it holds, photos by folder, tag or "
    "person, a photo's row against its file, the faces in a photo, the consistency checks "
    "tools/doctor.py runs, rows whose file is gone, and the plan of a query; and three "
    "maintenance operations (refresh_rows, merge_duplicate_person_tags, dedupe_faces), each "
    "a dry run unless called with apply=true, which backs the library up first. Call "
    "`libraries` first; every other tool names one of them. Answers give counts and ids. "
    "Paths, names and tags -- tags name people -- are given only with reveal=true: the "
    "library is photographs of real people, many of them minors. Ask for them only when "
    "the question needs them.")

#: Every tool reads; none writes, and asking twice answers the same.
READS = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

#: A maintenance operation: it writes when applied, and applying again finds nothing to do.
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)

#: How a tool description ends: what `reveal` gives.
REVEAL = " Names, paths and tags only with reveal=true."

#: How a write tool's description ends: what `apply` does.
APPLY = (" The default is a dry run, which changes nothing and takes no backup; apply=true "
         "backs the library up once (into its backups folder) and then writes. `changed` is "
         "what the write changed, read from the database, not what was planned.")


def written(result, library, reveal=False, limit=inspect.LIMIT):
    """A maintenance Result as a tool's answer: what it changed, attempted, skipped and
    failed, whether it was refused and why, and its details' counts, ids (each list up to
    `limit`), backup and remaining. What the details keep for a person to see -- paths,
    names, captions -- and where the backup went only with `reveal`; without it, the
    backup is named by its file alone, and the library's path in a refusal by its name."""
    details = result.details
    answer = {"ok": result.ok, "dry_run": details.get("dry_run", True), "refused": result.refused,
              "attempted": result.attempted, "changed": result.changed,
              "skipped": [list(s) for s in result.skipped], "errors": [list(e) for e in result.errors],
              "counts": details.get("counts", {}), "ids": {}, "remaining": details.get("remaining")}
    for what, ids in details.get("ids", {}).items():
        answer["ids"][what] = ids[:max(0, limit)]
        if len(ids) > limit:
            answer.setdefault("ids_not_listed", {})[what] = len(ids) - limit
    if "changed" in details:
        answer["changed_by_kind"] = details["changed"]
    backup = details.get("backup")
    answer["backup"] = backup if reveal or not backup else os.path.basename(backup)
    if reveal:
        answer["reveal"] = details.get("reveal", {})
    elif answer["refused"]:
        answer["refused"] = answer["refused"].replace(library.path, library.name)
    return answer


def library_names():
    """The libraries in the home's data folder, as the picker names them. Only libraries
    that are there: the picker offers photo_index when there are none, which this must
    not, as no tool creates one."""
    folder = config.data_dir()
    files = os.listdir(folder) if os.path.isdir(folder) else []
    return [name for name in libraries.picker_names(files, test_mode=False)
            if os.path.exists(config.library_path(name + ".db"))]


def find_library(name):
    """The Library called `name` in the home's data folder; ToolError when there is none."""
    if not name or name not in library_names():
        raise ToolError("There is no library called %r; `libraries` lists them." % (name,))
    return Library(config.library_path(name + ".db"))


def _answer(action, reveal=False):
    """Run `action`, turning what the service refuses into the tool's error. An error
    the service did not expect is logged whole and answered by its kind alone, unless
    `reveal`: its message can hold a path."""
    try:
        return action()
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
          "list, and photos without a CLIP vector for the configured model. Counts only.")
    def summary(library: str) -> dict[str, Any]:
        return _answer(lambda: inspect.summary(find_library(library), config.embedder_settings()))

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
        return _answer(lambda: inspect.photo_against_file(find_library(library), photo_id,
                                                          config.exiftool_path(), reveal), reveal)

    @tool("The faces in a photo, by photo id: each face's id, box, whether it is named and by whom "
          "(name_source 'manual' is a person's decision), detection confidence, and whether it is "
          "excluded and why; and how many people the photo lists. Never the embedding or crop."
          + REVEAL)
    def faces_in_photo(library: str, photo_id: int, reveal: bool = False) -> dict[str, Any]:
        return _answer(lambda: inspect.faces_in_photo(find_library(library), photo_id, reveal), reveal)

    @tool("One consistency check of tools/doctor.py, by name: how many rows break it, and a few "
          "of them as ids. The checks: " + ", ".join(inspect.CHECKS) + "." + REVEAL)
    def check(library: str, name: str, reveal: bool = False) -> dict[str, Any]:
        return _answer(lambda: inspect.check(find_library(library), name, reveal), reveal)

    @tool("Every consistency check tools/doctor.py runs, in its order, with how many rows break "
          "each and how many checks are broken; and how many photos have no CLIP vector for the "
          "configured model (reported, not broken). Rows whose file is gone are missing_files'."
          + REVEAL)
    def checks(library: str, reveal: bool = False) -> dict[str, Any]:
        return _answer(lambda: inspect.all_checks(find_library(library), reveal, config.embedder_settings()),
                       reveal)

    @tool("The rows whose file is not on disk, by folder: whether each folder is gone entirely, its "
          "rows as ids (up to `limit` a folder), and the faces on them -- named, named by hand, "
          "excluded, and how many different names. Answers which names sit on rows of a folder "
          "whose files are gone (findings #42). Reported, never removed." + REVEAL)
    def missing_files(library: str, reveal: bool = False, limit: int = inspect.LIMIT) -> dict[str, Any]:
        return _answer(lambda: inspect.missing_files(find_library(library), reveal, limit), reveal)

    @tool("The plan SQLite would follow for a query on a library (EXPLAIN QUERY PLAN): which index "
          "each step uses, and which steps scan a table. The query is never run. Only a single "
          "SELECT or WITH statement that only reads; anything else is refused. `params` are the "
          "placeholders' values; any not given are planned as NULL. Answers table and index names, "
          "never rows.")
    def query_plan(library: str, sql: str, params: Optional[list] = None) -> dict[str, Any]:
        return _answer(lambda: inspect.query_plan(find_library(library), sql, params))

    def write_tool(description, name=None):
        return server.tool(name=name, description=description, annotations=WRITES)

    @write_tool("Re-read the photos whose rows no longer describe their files, and record what the "
                "files hold (tags, people, captions, raw metadata, modified time and size, a missing "
                "DocumentID); rows that only list a caption twice are fixed from the row. `folder` "
                "limits it to the rows under a folder. SLOW even as a dry run: planning reads every "
                "stale photo's file with ExifTool, which on a whole library can take many minutes; "
                "ask about one folder first. Files are only read, never written. `changed_by_kind` "
                "splits rows changed from their files and captions alone." + APPLY + REVEAL,
                name="refresh_rows")
    def refresh(library: str, apply: bool = False, folder: Optional[str] = None,
                reveal: bool = False, limit: int = inspect.LIMIT) -> dict[str, Any]:
        def act():
            found = find_library(library)
            return written(refresh_rows.refresh_rows(found, config.exiftool_path(), apply=apply, folder=folder),
                           found, reveal, limit)
        return _answer(act, reveal)

    @write_tool("Remove the tag-tree nodes that are a person's bare name where a People path "
                "already names the same person (a tree left by old indexing). Only the tree changes; "
                "photos carrying the bare tag are counted, not rewritten. `changed` is tree nodes "
                "removed. Refused when the library has no tag tree." + APPLY + REVEAL)
    def merge_duplicate_person_tags(library: str, apply: bool = False, reveal: bool = False,
                                    limit: int = inspect.LIMIT) -> dict[str, Any]:
        def act():
            found = find_library(library)
            return written(person_tags.merge_duplicate_person_tags(found, apply=apply), found, reveal, limit)
        return _answer(act, reveal)

    @write_tool("Remove face rows that copy another face of the same photo (same box) and know no "
                "more than the copy kept: a name given by hand or an exclusion always wins. Faces "
                "whose copies disagree about a name, or a name and an exclusion, are counted as "
                "disputed and left for a person. `changed` is face rows deleted." + APPLY + REVEAL)
    def dedupe_faces(library: str, apply: bool = False, reveal: bool = False,
                     limit: int = inspect.LIMIT) -> dict[str, Any]:
        def act():
            found = find_library(library)
            return written(duplicate_faces.dedupe_faces(found, apply=apply), found, reveal, limit)
        return _answer(act, reveal)

    return server


def main():
    """Serve over stdio until the client goes, logging to data/logs/tagpup_mcp.log."""
    # Before FastMCP is made: it gives the root logger a console handler when it has none.
    logs.to_file("tagpup_mcp")
    logger.info("Serving the libraries in %s over stdio", config.data_dir())
    build().run("stdio")
    return 0
