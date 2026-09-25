"""python -m tagpup.mcp: the MCP server, over stdio (tagpup.mcp.server)."""
import sys

from tagpup.mcp import server

if __name__ == "__main__":
    sys.exit(server.main())
