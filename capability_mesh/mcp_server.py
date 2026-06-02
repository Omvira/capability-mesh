"""MCP stdio adapter for a running Capability Mesh HTTP service."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from capability_mesh.client import CapabilityMeshClient, CapabilityMeshClientError


PRIVATE_KEYS = {
    "command",
    "dispatch_command",
    "environment_variables",
    "env",
    "local_skills",
    "memory",
    "private_logs",
    "private_memory",
    "raw_logs",
    "raw_private_logs",
    "reasoning_trace",
    "session",
    "session_history",
    "transport_command",
    "wake_token",
    "wake_url",
}
PRIVATE_KEY_FRAGMENTS = ("api_key", "apikey", "password", "secret", "token")


def _is_private_key(key: str) -> bool:
    normalized = key.lower()
    return normalized in PRIVATE_KEYS or any(fragment in normalized for fragment in PRIVATE_KEY_FRAGMENTS)


def sanitize_for_mcp(value: Any) -> Any:
    """Return a JSON-serializable value without private transport or secret fields."""

    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _is_private_key(key_str):
                continue
            cleaned[key_str] = sanitize_for_mcp(item)
        return cleaned
    if isinstance(value, list):
        return [sanitize_for_mcp(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_for_mcp(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class CapabilityMeshMCPTools:
    """Thin, testable tool implementations over the Capability Mesh HTTP client."""

    def __init__(self, mesh_url: str, *, timeout: float = 10.0):
        self.client = CapabilityMeshClient(mesh_url, timeout=timeout)

    def list_clients(self) -> list[dict[str, Any]]:
        return sanitize_for_mcp(self.client.list_nodes())

    def get_client(self, client_id: str) -> dict[str, Any]:
        return sanitize_for_mcp(self.client.get_node(client_id))

    def call_client_async(self, task: Mapping[str, Any], required_tools: Sequence[str] | None = None) -> dict[str, Any]:
        return sanitize_for_mcp(self.client.route_task(task, required_tools=list(required_tools or []) or None))

    def create_assignment(self, task: Mapping[str, Any], required_tools: Sequence[str] | None = None) -> dict[str, Any]:
        return self.call_client_async(task, required_tools)

    def get_assignment_status(self, assignment_id: str) -> dict[str, Any]:
        for assignment in self.client.list_assignments():
            if str(assignment.get("assignment_id")) == assignment_id:
                return sanitize_for_mcp(assignment)
        raise CapabilityMeshClientError(f"unknown assignment_id: {assignment_id}")

    def send_a2a_message(self, message: Mapping[str, Any]) -> dict[str, Any]:
        return sanitize_for_mcp(self.client.send_a2a_message(message))


def _missing_sdk_error() -> str:
    return "The Python MCP SDK is required to run the stdio MCP server. Install it with `python -m pip install mcp` and retry."


def _register_fastmcp_tools(server: Any, tools: CapabilityMeshMCPTools) -> None:
    @server.tool()
    def list_clients() -> list[dict[str, Any]]:
        """List public Capability Mesh clients/nodes."""
        return tools.list_clients()

    @server.tool()
    def get_client(client_id: str) -> dict[str, Any]:
        """Get public details for one Capability Mesh client/node."""
        return tools.get_client(client_id)

    @server.tool()
    def call_client_async(task: dict[str, Any], required_tools: list[str] | None = None) -> dict[str, Any]:
        """Create an async assignment by routing a task to a matching client."""
        return tools.call_client_async(task, required_tools)

    @server.tool()
    def create_assignment(task: dict[str, Any], required_tools: list[str] | None = None) -> dict[str, Any]:
        """Alias for call_client_async with an assignment-focused name."""
        return tools.create_assignment(task, required_tools)

    @server.tool()
    def get_assignment_status(assignment_id: str) -> dict[str, Any]:
        """Return current public status for an assignment."""
        return tools.get_assignment_status(assignment_id)

    @server.tool()
    def send_a2a_message(message: dict[str, Any]) -> dict[str, Any]:
        """Send an A2A-like message envelope through Capability Mesh."""
        return tools.send_a2a_message(message)


def run_mcp_server(mesh_url: str, *, timeout: float = 10.0) -> int:
    """Run the stdio MCP server, using the Python MCP SDK when installed."""

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print(_missing_sdk_error(), file=sys.stderr)
        return 1

    server = FastMCP("Capability Mesh")
    _register_fastmcp_tools(server, CapabilityMeshMCPTools(mesh_url, timeout=timeout))
    server.run(transport="stdio")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Capability Mesh stdio MCP server adapter.")
    parser.add_argument("--url", "--mesh-url", dest="mesh_url", required=True, help="Capability Mesh service base URL")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_mcp_server(args.mesh_url, timeout=args.timeout)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
