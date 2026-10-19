"""Expose a guarded database to any MCP client (Claude Desktop, Cursor, agents built on MCP)."""
from __future__ import annotations

from .guard import QueryGuard


def build_server(guard: QueryGuard, name: str = "agent-query-guard"):
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
        except ImportError as e:
            raise SystemExit("MCP support needs: pip install 'agent-query-guard[mcp]'") from e

    server = Server(name)

    @server.tool()
    def run_query(sql: str) -> dict:
        """Run a read-only SQL query. Expensive or unsafe queries are refused with a reason;
        rewrite the query (filter, aggregate, or add a LIMIT) and try again."""
        return guard.run(sql, session="mcp").to_dict()

    @server.tool()
    def check_query(sql: str) -> dict:
        """Ask whether a query would be allowed, and its estimated size, without running it."""
        return guard.check(sql, session="mcp").__dict__

    @server.tool()
    def list_tables() -> list[str]:
        """List the tables this connection can see."""
        return guard.backend.tables()

    return server


def serve(guard: QueryGuard) -> None:
    build_server(guard).run()
