"""Compatibility wrapper for Capability Mesh MCP server helpers."""

from hermes_mesh.mcp_server import *  # noqa: F401,F403

if __name__ == "__main__":  # pragma: no cover
    from hermes_mesh.mcp_server import main

    raise SystemExit(main())
