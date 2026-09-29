"""Isolated entry point for the MCP Python SDK 2.x client."""

from __future__ import annotations

import asyncio
import http
import json
import os
import sys
from typing import Any

from app.logic.mcp_network import PinnedMcpNetworkBackend, validate_mcp_endpoint


MAX_TOOLS = 64
MAX_SCHEMA_BYTES = 16 * 1024
MAX_RESULT_BYTES = 32 * 1024
MAX_TEXT_BYTES = 4096
MAX_BLOCKS = 20


def _dump(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _bounded_http_transport(httpx2: Any, endpoint: str, allow_loopback_http: bool) -> Any:
    hostname, port, _ = validate_mcp_endpoint(
        endpoint,
        allow_loopback=allow_loopback_http,
        resolve=False,
    )

    class LimitedStream(httpx2.AsyncByteStream):
        def __init__(self, inner: Any) -> None:
            self._inner = inner
            self._bytes = 0

        async def __aiter__(self):
            async for chunk in self._inner:
                self._bytes += len(chunk)
                if self._bytes > 1024 * 1024:
                    await self._inner.aclose()
                    raise ValueError("result_too_large")
                yield chunk

        async def aclose(self) -> None:
            await self._inner.aclose()

    class LimitedTransport(httpx2.AsyncBaseTransport):
        def __init__(self) -> None:
            self._inner = httpx2.AsyncHTTPTransport(
                verify=True,
                trust_env=False,
                limits=httpx2.Limits(max_connections=2, max_keepalive_connections=1),
            )
            pool = getattr(self._inner, "_pool", None)
            network_backend = getattr(pool, "_network_backend", None)
            if network_backend is None:
                raise RuntimeError("mcp_transport_pin_unavailable")
            pool._network_backend = PinnedMcpNetworkBackend(
                network_backend,
                hostname=hostname,
                port=port,
                allow_loopback=allow_loopback_http,
            )

        async def handle_async_request(self, request: Any) -> Any:
            response = await self._inner.handle_async_request(request)
            return httpx2.Response(
                response.status_code,
                headers=response.headers,
                stream=LimitedStream(response.stream),
                request=request,
                extensions=response.extensions,
            )

        async def aclose(self) -> None:
            await self._inner.aclose()

    return LimitedTransport()


def _tool_record(item: Any) -> dict[str, Any]:
    schema = getattr(item, "input_schema", None)
    try:
        schema_size = len(_dump(schema))
    except (TypeError, ValueError, RecursionError):
        schema_size = MAX_SCHEMA_BYTES + 1
    if not isinstance(schema, dict) or schema_size > MAX_SCHEMA_BYTES:
        schema = None
    return {
        "name": str(getattr(item, "name", "") or "")[:80],
        "description": str(getattr(item, "description", "") or "")[:1024],
        "input_schema": schema,
    }


def _tool_result(result: Any) -> dict[str, Any]:
    content = []
    total = 0
    for block in list(getattr(result, "content", ()) or ())[:MAX_BLOCKS]:
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_TEXT_BYTES:
            raise ValueError("result_too_large")
        total += len(encoded)
        if total > MAX_RESULT_BYTES:
            raise ValueError("result_too_large")
        content.append({"type": "text", "text": text.replace("\x00", "")})
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        encoded = _dump(structured)
        if len(encoded) > MAX_RESULT_BYTES:
            raise ValueError("result_too_large")
        structured = json.loads(encoded)
    return {
        "is_error": bool(getattr(result, "is_error", False)),
        "content": content,
        "structured_content": structured,
    }


async def _request(payload: dict[str, Any]) -> dict[str, Any]:
    from mcp import Client

    operation = payload.get("operation")
    transport = payload.get("transport")
    if operation not in {"list_tools", "call_tool"} or transport not in {"http", "stdio"}:
        raise ValueError("request_invalid")

    if transport == "http":
        endpoint = payload.get("endpoint")
        allow_loopback_http = payload.get("allow_loopback_http") is True
        if not isinstance(endpoint, str):
            raise ValueError("endpoint_invalid")
        validate_mcp_endpoint(endpoint, allow_loopback=allow_loopback_http, resolve=False)
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        async def check_content_length(response: Any) -> None:
            raw_size = response.headers.get("content-length")
            if raw_size and raw_size.isdigit() and int(raw_size) > 1024 * 1024:
                raise ValueError("result_too_large")

        token = os.environ.get("MCP_BEARER_TOKEN", "")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        http_client = httpx2.AsyncClient(
            headers=headers,
            timeout=httpx2.Timeout(connect=4.0, read=12.0, write=4.0, pool=2.0),
            follow_redirects=False,
            trust_env=False,
            limits=httpx2.Limits(max_connections=2, max_keepalive_connections=1),
            transport=_bounded_http_transport(httpx2, endpoint, allow_loopback_http),
            event_hooks={"response": [check_content_length]},
        )
        async with http_client:
            async with Client(streamable_http_client(endpoint, http_client=http_client)) as client:
                return await _execute(client, operation, payload)

    stdio = payload.get("stdio")
    if not isinstance(stdio, dict):
        raise ValueError("stdio_config_invalid")
    command = stdio.get("command")
    args = stdio.get("args")
    environment = stdio.get("environment", {})
    if (
        not isinstance(command, str) or not command or "\x00" in command
        or not isinstance(args, list) or any(not isinstance(arg, str) or "\x00" in arg for arg in args)
        or not isinstance(environment, dict)
        or any(not isinstance(k, str) or not isinstance(v, str) for k, v in environment.items())
    ):
        raise ValueError("stdio_config_invalid")
    from mcp.client.stdio import StdioServerParameters

    parameters = StdioServerParameters(
        command=command,
        args=args,
        env=environment,
        cwd=stdio.get("cwd"),
    )
    async with Client(parameters) as client:
        return await _execute(client, operation, payload)


async def _execute(client: Any, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    if operation == "list_tools":
        tools = []
        cursor = None
        for _ in range(MAX_TOOLS):
            page = await client.list_tools(cursor=cursor)
            tools.extend(_tool_record(item) for item in page.tools[: MAX_TOOLS - len(tools)])
            cursor = getattr(page, "next_cursor", None)
            if not cursor or len(tools) >= MAX_TOOLS:
                break
        response = {"tools": tools}
        if len(_dump(response)) > 1024 * 1024:
            raise ValueError("result_too_large")
        return response

    name = payload.get("tool_name")
    arguments = payload.get("arguments")
    if not isinstance(name, str) or not name or not isinstance(arguments, dict):
        raise ValueError("request_invalid")
    return _tool_result(await client.call_tool(name, arguments))


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(64 * 1024)
        if not raw or len(raw) >= 64 * 1024:
            raise ValueError("request_invalid")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("request_invalid")
        result = asyncio.run(_request(payload))
        response = {"ok": True, "result": result}
    except ValueError as exc:
        code = str(exc) if str(exc) in {"result_too_large", "tool_not_available", "arguments_invalid"} else "server_unavailable"
        response = {"ok": False, "error": code}
    except BaseException as exc:
        status = getattr(exc, "status_code", None)
        remote_response = getattr(exc, "response", None)
        status = status or getattr(remote_response, "status_code", None)
        if status in {http.HTTPStatus.UNAUTHORIZED, http.HTTPStatus.FORBIDDEN}:
            code = "authentication_failed"
        elif isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            code = "timed_out"
        elif isinstance(exc, ValueError) and str(exc) == "result_too_large":
            code = "result_too_large"
        else:
            code = "server_unavailable"
        response = {"ok": False, "error": code}
    try:
        encoded = _dump(response)
    except (TypeError, ValueError, RecursionError):
        encoded = b'{"ok":false,"error":"server_unavailable"}'
    if len(encoded) > 1024 * 1024:
        encoded = b'{"ok":false,"error":"result_too_large"}'
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
