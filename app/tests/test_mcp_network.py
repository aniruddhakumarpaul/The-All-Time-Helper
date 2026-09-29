import asyncio
import os
import socket
import ssl
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from app.logic.mcp_gateway import McpGateway, McpGatewayError
from app.logic.mcp_network import (
    McpDestinationError,
    PinnedMcpNetworkBackend,
    resolve_mcp_destination_ips,
    validate_mcp_destination_ip,
    validate_mcp_endpoint,
)
from app.logic.mcp_registry import McpServerSpec


ROOT = Path(__file__).resolve().parents[2]
SDK_PATH = Path(os.getenv("MCP_SDK_V2_PACKAGE_DIR", ".runtime/mcp-sdk")).resolve()
SDK_AVAILABLE = (
    (SDK_PATH / "mcp-2.2.0.dist-info" / "METADATA").is_file()
    and (SDK_PATH / "httpx2" / "__init__.py").is_file()
    and (SDK_PATH / "httpcore2" / "__init__.py").is_file()
)


def dns_answer(address, port=443):
    if ":" in address:
        return socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port, 0, 0)
    return socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port)


class FakeNetworkBackend:
    def __init__(self):
        self.attempted_hosts = []

    async def connect_tcp(self, host, port, **kwargs):
        self.attempted_hosts.append((host, port))
        return object()

    async def connect_unix_socket(self, path, **kwargs):
        raise AssertionError("HTTP MCP must not use a Unix socket")

    async def sleep(self, seconds):
        return None


class McpDestinationValidationTests(unittest.TestCase):
    def test_private_reserved_loopback_and_link_local_addresses_are_rejected(self):
        addresses = (
            "127.0.0.1",
            "::1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "fe80::1",
            "0.0.0.0",
            "::",
            "224.0.0.1",
            "240.0.0.1",
            "::ffff:127.0.0.1",
            "::ffff:10.0.0.1",
            "fe80::1%3",
        )
        for address in addresses:
            with self.subTest(address=address), self.assertRaises(McpDestinationError):
                validate_mcp_destination_ip(address)

    def test_ipv4_mapped_global_address_uses_ipv4_policy(self):
        result = validate_mcp_destination_ip("::ffff:93.184.216.34")
        self.assertEqual(str(result), "93.184.216.34")

    def test_loopback_mode_accepts_only_loopback(self):
        self.assertEqual(str(validate_mcp_destination_ip("127.0.0.1", allow_loopback=True)), "127.0.0.1")
        self.assertEqual(str(validate_mcp_destination_ip("::1", allow_loopback=True)), "::1")
        with self.assertRaises(McpDestinationError):
            validate_mcp_destination_ip("93.184.216.34", allow_loopback=True)

    def test_mixed_public_private_dns_answers_fail_closed(self):
        answers = [dns_answer("93.184.216.34"), dns_answer("10.0.0.1")]
        with self.assertRaises(McpDestinationError):
            resolve_mcp_destination_ips(
                "mcp.example.test", 443, resolver=lambda *args, **kwargs: answers,
            )

    def test_dns_answer_count_is_bounded(self):
        answers = [dns_answer("93.184.216.34") for _ in range(33)]
        with self.assertRaises(OSError):
            resolve_mcp_destination_ips(
                "mcp.example.test", 443, resolver=lambda *args, **kwargs: answers,
            )

    def test_endpoint_rejects_credentials_queries_fragments_unsupported_schemes_and_zones(self):
        invalid = (
            "https://user:pass@mcp.example.test/mcp",
            "https://mcp.example.test/mcp?token=x",
            "https://mcp.example.test/mcp#fragment",
            "http://mcp.example.test/mcp",
            "ftp://mcp.example.test/mcp",
            "https://[fe80::1%253]/mcp",
        )
        for endpoint in invalid:
            with self.subTest(endpoint=endpoint), self.assertRaises(McpDestinationError):
                validate_mcp_endpoint(endpoint)

    def test_gateway_rejects_private_ip_literals_for_normal_servers(self):
        server = McpServerSpec("remote", "http", endpoint_env="MCP_REMOTE_URL")
        addresses = ("127.0.0.1", "::1", "10.0.0.1", "172.16.0.1", "192.168.1.1",
                     "169.254.169.254", "fe80::1", "0.0.0.0", "::")
        for address in addresses:
            host = f"[{address}]" if ":" in address else address
            with self.subTest(address=address), self.assertRaises(McpGatewayError):
                McpGateway._validate_endpoint(f"https://{host}/mcp", server, resolve=False)

    def test_localhost_alias_requires_loopback_opt_in_and_loopback_answers(self):
        with self.assertRaises(McpDestinationError):
            validate_mcp_endpoint("https://api.localhost/mcp")
        accepted = validate_mcp_endpoint(
            "http://api.localhost/mcp",
            allow_loopback=True,
            resolve=True,
            resolver=lambda *args, **kwargs: [dns_answer("127.0.0.1", 80)],
        )
        self.assertEqual(accepted, ("api.localhost", 80, "http"))
        with self.assertRaises(McpDestinationError):
            validate_mcp_endpoint(
                "http://api.localhost/mcp",
                allow_loopback=True,
                resolve=True,
                resolver=lambda *args, **kwargs: [
                    dns_answer("127.0.0.1", 80), dns_answer("93.184.216.34", 80),
                ],
            )


class McpPinnedNetworkTests(unittest.TestCase):
    def test_backend_dials_validated_numeric_address(self):
        inner = FakeNetworkBackend()
        backend = PinnedMcpNetworkBackend(
            inner,
            hostname="mcp.example.test",
            port=443,
            resolver=lambda *args, **kwargs: [
                dns_answer("93.184.216.34"), dns_answer("8.8.8.8"),
            ],
        )
        asyncio.run(backend.connect_tcp("mcp.example.test", 443))
        self.assertEqual(inner.attempted_hosts, [("93.184.216.34", 443)])

    def test_public_preflight_then_private_worker_lookup_never_attempts_connection(self):
        server = McpServerSpec("remote", "http", endpoint_env="MCP_REMOTE_URL")
        lookups = iter(([dns_answer("93.184.216.34")], [dns_answer("127.0.0.1")]))

        def resolve(*args, **kwargs):
            return next(lookups)

        inner = FakeNetworkBackend()
        with patch("app.logic.mcp_network.socket.getaddrinfo", side_effect=resolve):
            McpGateway._validate_endpoint(
                "https://rebind.example.test/mcp", server, resolve=True,
            )
            backend = PinnedMcpNetworkBackend(
                inner, hostname="rebind.example.test", port=443,
            )
            with self.assertRaises(McpDestinationError):
                asyncio.run(backend.connect_tcp("rebind.example.test", 443))
        self.assertEqual(inner.attempted_hosts, [])

    def test_loopback_preflight_then_non_loopback_lookup_never_attempts_connection(self):
        server = McpServerSpec(
            "local", "http", endpoint_env="MCP_LOCAL_URL", allow_loopback_http=True,
        )
        lookups = iter(([dns_answer("127.0.0.1", 80)], [dns_answer("10.0.0.1", 80)]))

        def resolve(*args, **kwargs):
            return next(lookups)

        inner = FakeNetworkBackend()
        with patch("app.logic.mcp_network.socket.getaddrinfo", side_effect=resolve):
            McpGateway._validate_endpoint("http://localhost/mcp", server, resolve=True)
            backend = PinnedMcpNetworkBackend(
                inner, hostname="localhost", port=80, allow_loopback=True,
            )
            with self.assertRaises(McpDestinationError):
                asyncio.run(backend.connect_tcp("localhost", 80))
        self.assertEqual(inner.attempted_hosts, [])

    def test_backend_rejects_unexpected_origin_without_resolution(self):
        inner = FakeNetworkBackend()
        backend = PinnedMcpNetworkBackend(
            inner,
            hostname="mcp.example.test",
            port=443,
            resolver=lambda *args, **kwargs: self.fail("unexpected origin must not resolve"),
        )
        with self.assertRaises(McpDestinationError):
            asyncio.run(backend.connect_tcp("attacker.example.test", 443))
        self.assertEqual(inner.attempted_hosts, [])


@unittest.skipUnless(SDK_AVAILABLE, "isolated MCP SDK 2.2.0 runtime not installed")
class McpIsolatedRuntimeNetworkTests(unittest.TestCase):
    def test_httpcore_preserves_tls_sni_and_verification_for_hostname_origin(self):
        sys.path.insert(0, str(SDK_PATH))
        import httpcore2
        import httpx2

        connected_hosts = []
        tls_observations = []

        class FakeStream:
            async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
                tls_observations.append((server_hostname, ssl_context.verify_mode, ssl_context.check_hostname))
                return self

            def get_extra_info(self, name):
                return None

        class FakeBackend:
            async def connect_tcp(self, host, port, **kwargs):
                connected_hosts.append((host, port))
                return FakeStream()

            async def sleep(self, seconds):
                return None

        http_transport = httpx2.AsyncHTTPTransport(verify=True, trust_env=False)
        pinned_backend = PinnedMcpNetworkBackend(
            FakeBackend(),
            hostname="mcp.example.test",
            port=443,
            resolver=lambda *args, **kwargs: [dns_answer("93.184.216.34")],
        )
        connection = httpcore2.AsyncHTTPConnection(
            origin=httpcore2.Origin(b"https", b"mcp.example.test", 443),
            ssl_context=http_transport._pool._ssl_context,
            network_backend=pinned_backend,
        )
        request = httpcore2.Request(
            "GET",
            "https://mcp.example.test/mcp",
            extensions={"timeout": {"connect": 1.0}},
        )
        asyncio.run(connection._connect(request))

        self.assertEqual(connected_hosts, [("93.184.216.34", 443)])
        self.assertEqual(tls_observations, [("mcp.example.test", ssl.CERT_REQUIRED, True)])
        asyncio.run(http_transport.aclose())

    def test_service_bearer_is_not_inherited_by_stdio_child_environment(self):
        code = "from mcp.client.stdio import get_default_environment; assert 'MCP_BEARER_TOKEN' not in get_default_environment()"
        child_env = os.environ.copy()
        child_env["PYTHONPATH"] = os.pathsep.join((str(SDK_PATH), str(ROOT)))
        child_env["MCP_BEARER_TOKEN"] = "stdio-token-isolation-test"
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
