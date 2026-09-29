"""Destination validation and DNS-pinned networking for MCP HTTP clients."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Callable, Sequence
from urllib.parse import urlsplit


McpIpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
McpResolver = Callable[..., Sequence[tuple]]
MAX_MCP_DNS_ADDRESSES = 32


class McpDestinationError(ValueError):
    """A configured MCP destination does not satisfy the network policy."""


class McpResolutionError(OSError):
    """The MCP destination could not be resolved to a bounded address set."""


def _canonical_ip(value: str | McpIpAddress) -> McpIpAddress:
    raw = str(value)
    if "%" in raw:
        raise McpDestinationError("mcp_destination_invalid")
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        raise McpDestinationError("mcp_destination_invalid") from None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def canonicalize_mcp_hostname(host: str) -> str:
    """Canonicalize an HTTP origin host without accepting scoped or malformed names."""
    if not isinstance(host, str) or not host or host != host.strip() or "%" in host or "\x00" in host:
        raise McpDestinationError("mcp_destination_invalid")
    try:
        return str(_canonical_ip(host))
    except McpDestinationError:
        pass

    try:
        ascii_host = host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise McpDestinationError("mcp_destination_invalid") from None
    if not ascii_host or len(ascii_host) > 253:
        raise McpDestinationError("mcp_destination_invalid")
    labels = ascii_host.split(".")
    if any(
        not label or len(label) > 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        for label in labels
    ):
        raise McpDestinationError("mcp_destination_invalid")
    return ascii_host


def validate_mcp_destination_ip(
    value: str | McpIpAddress,
    *,
    allow_loopback: bool = False,
) -> McpIpAddress:
    """Validate one numeric destination address; never treats mapped IPv6 as a bypass."""
    address = _canonical_ip(value)
    if allow_loopback:
        if not address.is_loopback:
            raise McpDestinationError("mcp_destination_forbidden")
    elif (
        not address.is_global
        or address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        raise McpDestinationError("mcp_destination_forbidden")
    return address


def resolve_mcp_destination_ips(
    host: str,
    port: int,
    *,
    allow_loopback: bool = False,
    resolver: McpResolver | None = None,
) -> tuple[McpIpAddress, ...]:
    """Resolve once, validate every answer, and fail closed on mixed answer sets."""
    canonical_host = canonicalize_mcp_hostname(host)
    try:
        literal = ipaddress.ip_address(canonical_host)
    except ValueError:
        literal = None
    if literal is not None:
        return (validate_mcp_destination_ip(literal, allow_loopback=allow_loopback),)

    lookup = resolver or socket.getaddrinfo
    try:
        answers = lookup(canonical_host, int(port), type=socket.SOCK_STREAM)
    except OSError:
        raise McpResolutionError("mcp_destination_resolution_failed") from None
    if not answers or len(answers) > MAX_MCP_DNS_ADDRESSES:
        raise McpResolutionError("mcp_destination_resolution_failed")

    validated: dict[str, McpIpAddress] = {}
    for answer in answers:
        try:
            address_text = answer[4][0]
            address = validate_mcp_destination_ip(address_text, allow_loopback=allow_loopback)
        except (IndexError, TypeError):
            raise McpDestinationError("mcp_destination_invalid") from None
        validated[str(address)] = address
    if not validated:
        raise McpResolutionError("mcp_destination_resolution_failed")
    return tuple(validated.values())


def validate_mcp_endpoint(
    endpoint: str,
    *,
    allow_loopback: bool = False,
    resolve: bool = False,
    resolver: McpResolver | None = None,
) -> tuple[str, int, str]:
    """Validate the configured endpoint shape and optionally its complete DNS set."""
    if (
        not isinstance(endpoint, str) or not endpoint or endpoint != endpoint.strip()
        or "\x00" in endpoint
    ):
        raise McpDestinationError("mcp_endpoint_invalid")
    try:
        parsed = urlsplit(endpoint)
        hostname = parsed.hostname
        parsed_port = parsed.port
        port = parsed_port if parsed_port is not None else (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise McpDestinationError("mcp_endpoint_invalid") from None
    if (
        not hostname or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or "?" in endpoint or "#" in endpoint
        or parsed.scheme not in ({"https", "http"} if allow_loopback else {"https"})
        or not 1 <= port <= 65535
    ):
        raise McpDestinationError("mcp_endpoint_invalid")

    canonical_host = canonicalize_mcp_hostname(hostname)
    try:
        literal = ipaddress.ip_address(canonical_host)
    except ValueError:
        literal = None
    local_name = canonical_host == "localhost" or canonical_host.endswith(".localhost")

    if literal is not None:
        validate_mcp_destination_ip(literal, allow_loopback=allow_loopback)
    elif local_name and not allow_loopback:
        raise McpDestinationError("mcp_destination_forbidden")

    if parsed.scheme == "http" and not (
        allow_loopback and (local_name or (literal is not None and literal.is_loopback))
    ):
        raise McpDestinationError("mcp_endpoint_invalid")

    if resolve:
        resolve_mcp_destination_ips(
            canonical_host,
            port,
            allow_loopback=allow_loopback,
            resolver=resolver,
        )
    return canonical_host, port, parsed.scheme


class PinnedMcpNetworkBackend:
    """Validate DNS in the connection operation and dial only its numeric address."""

    def __init__(
        self,
        backend: object,
        *,
        hostname: str,
        port: int,
        allow_loopback: bool = False,
        resolver: McpResolver | None = None,
    ) -> None:
        self._backend = backend
        self._hostname = canonicalize_mcp_hostname(hostname)
        self._port = int(port)
        self._allow_loopback = allow_loopback
        self._resolver = resolver

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Sequence[tuple] | None = None,
    ) -> object:
        if canonicalize_mcp_hostname(host) != self._hostname or int(port) != self._port:
            raise McpDestinationError("mcp_destination_mismatch")
        addresses = await asyncio.to_thread(
            resolve_mcp_destination_ips,
            self._hostname,
            self._port,
            allow_loopback=self._allow_loopback,
            resolver=self._resolver,
        )
        return await self._backend.connect_tcp(
            str(addresses[0]),
            self._port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, path: str, **kwargs: object) -> object:
        del path, kwargs
        raise McpDestinationError("mcp_destination_invalid")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)
