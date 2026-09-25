"""The MCP server Claude works with a library through: `python -m tagpup.mcp`, over stdio.

An entry point beside the web server and the CLI (docs/ARCHITECTURE.md, phase 7). Its
tools call the same services the apps use -- the reads are tagpup.services.inspect --
rather than a one-off script per question. It runs in a process of its own and never
talks to a running app's port. Every tool but `libraries` names the library it asks
about (#100): a name in the home's data folder, which must exist; none is created.

The server is `server.build()`; `server.main()` also logs to data/logs/tagpup_mcp.log
and serves stdin and stdout, which are the protocol's and nothing else's.
"""
