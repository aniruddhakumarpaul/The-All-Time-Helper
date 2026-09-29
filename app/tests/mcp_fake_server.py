"""Minimal stdio MCP server used by the isolated SDK v2 integration test."""

from mcp.server import MCPServer


server = MCPServer("TAH MCP gateway test server", version="1.0")


@server.tool(description="Untrusted server description must never reach the agent.")
async def search_repositories(query: str) -> str:
    return f"repository result for {query}"


@server.tool(description="This tool is deliberately not locally mapped.")
async def delete_repository(query: str) -> str:
    return f"deleted {query}"


if __name__ == "__main__":
    server.run(transport="stdio")
