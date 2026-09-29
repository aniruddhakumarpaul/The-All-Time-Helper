"""Local Streamable HTTP MCP server used only by the isolated SDK v2 test."""

import sys

from mcp.server import MCPServer


server = MCPServer("TAH local MCP HTTP test server", version="1.0")


@server.tool()
async def search_repositories(query: str) -> str:
    return f"http repository result for {query}"


if __name__ == "__main__":
    server.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=int(sys.argv[1]),
        streamable_http_path="/mcp",
        stateless_http=True,
    )
