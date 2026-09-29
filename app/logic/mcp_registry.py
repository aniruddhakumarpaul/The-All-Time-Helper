"""Application-owned MCP servers and tool authorization bindings."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

from app.logic.capability_policy import CAPABILITY_REGISTRY, CapabilityEffect


_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


@dataclass(frozen=True)
class McpServerSpec:
    server_id: str
    transport: Literal["http", "stdio"]
    endpoint_env: str | None = None
    token_env: str | None = None
    auth_profile: str = "service"
    required: bool = False
    allow_loopback_http: bool = False
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: str | None = None
    stdio_env: tuple[tuple[str, str], ...] = ()
    exposure_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _ALIAS_RE.fullmatch(self.server_id or ""):
            raise ValueError("mcp_server_id_invalid")
        if self.transport == "http":
            if not self.endpoint_env or not _ENV_RE.fullmatch(self.endpoint_env):
                raise ValueError("mcp_endpoint_env_invalid")
            if self.command or self.args or self.cwd or self.stdio_env:
                raise ValueError("mcp_http_stdio_config_conflict")
        elif self.transport == "stdio":
            if not self.command or not self.command.strip() or self.endpoint_env:
                raise ValueError("mcp_stdio_command_invalid")
            if any(not isinstance(item, str) or not item or "\x00" in item for item in self.args):
                raise ValueError("mcp_stdio_args_invalid")
            for target, source in self.stdio_env:
                if not _ENV_RE.fullmatch(target or "") or not _ENV_RE.fullmatch(source or ""):
                    raise ValueError("mcp_stdio_env_invalid")
        else:
            raise ValueError("mcp_transport_invalid")
        if self.token_env and not _ENV_RE.fullmatch(self.token_env):
            raise ValueError("mcp_token_env_invalid")
        if not _ALIAS_RE.fullmatch(self.auth_profile or ""):
            raise ValueError("mcp_auth_profile_invalid")


@dataclass(frozen=True)
class McpToolBinding:
    server_id: str
    remote_tool: str
    tool_alias: str
    capability_id: str
    description: str

    def __post_init__(self) -> None:
        if not _ALIAS_RE.fullmatch(self.server_id or ""):
            raise ValueError("mcp_binding_server_invalid")
        if not _ALIAS_RE.fullmatch(self.remote_tool or ""):
            raise ValueError("mcp_remote_tool_invalid")
        if not _ALIAS_RE.fullmatch(self.tool_alias or ""):
            raise ValueError("mcp_tool_alias_invalid")
        if len(self.description) > 240:
            raise ValueError("mcp_local_description_too_long")


class McpRegistry:
    """Immutable, code-defined authority map. Remote metadata never adds entries."""

    def __init__(self, servers: tuple[McpServerSpec, ...], bindings: tuple[McpToolBinding, ...]) -> None:
        server_map: dict[str, McpServerSpec] = {}
        for server in servers:
            if server.server_id in server_map:
                raise ValueError("mcp_duplicate_server")
            server_map[server.server_id] = server

        binding_map: dict[tuple[str, str], McpToolBinding] = {}
        alias_map: dict[str, McpToolBinding] = {}
        for binding in bindings:
            if binding.server_id not in server_map:
                raise ValueError("mcp_binding_server_unregistered")
            key = (binding.server_id, binding.remote_tool)
            if key in binding_map or binding.tool_alias in alias_map:
                raise ValueError("mcp_duplicate_binding")
            capability = CAPABILITY_REGISTRY.get(binding.capability_id)
            if (
                capability is None
                or capability.effect != CapabilityEffect.READ_ONLY
                or binding.capability_id not in {"mcp.read", "mcp.resource.read"}
            ):
                raise ValueError("mcp_binding_capability_invalid")
            binding_map[key] = binding
            alias_map[binding.tool_alias] = binding

        self._servers: Mapping[str, McpServerSpec] = MappingProxyType(server_map)
        self._bindings: Mapping[tuple[str, str], McpToolBinding] = MappingProxyType(binding_map)
        self._aliases: Mapping[str, McpToolBinding] = MappingProxyType(alias_map)

    @property
    def servers(self) -> Mapping[str, McpServerSpec]:
        return self._servers

    @property
    def bindings(self) -> Mapping[tuple[str, str], McpToolBinding]:
        return self._bindings

    @property
    def aliases(self) -> Mapping[str, McpToolBinding]:
        return self._aliases

    def server(self, server_id: str) -> McpServerSpec | None:
        return self._servers.get(str(server_id or ""))

    def binding(self, server_id: str, remote_tool: str) -> McpToolBinding | None:
        return self._bindings.get((str(server_id or ""), str(remote_tool or "")))

    def alias(self, tool_alias: str) -> McpToolBinding | None:
        return self._aliases.get(str(tool_alias or ""))


DEFAULT_MCP_REGISTRY = McpRegistry(
    servers=(
        McpServerSpec(
            server_id="github",
            transport="http",
            endpoint_env="MCP_GITHUB_URL",
            token_env="MCP_GITHUB_TOKEN",
            exposure_terms=("github", "repository", "repositories", "repo", "commit", "code search"),
        ),
    ),
    bindings=(
        McpToolBinding("github", "search_repositories", "github_search_repositories", "mcp.read", "Search GitHub repositories using the user's query."),
        McpToolBinding("github", "search_code", "github_search_code", "mcp.read", "Search code in repositories the configured GitHub identity can read."),
        McpToolBinding("github", "get_file_contents", "github_get_file_contents", "mcp.read", "Read a file from a GitHub repository."),
        McpToolBinding("github", "list_commits", "github_list_commits", "mcp.read", "List commits from a GitHub repository."),
    ),
)
