"""Server application entry points for Capability Mesh."""

from __future__ import annotations

import argparse
from pathlib import Path

from capability_mesh.server.api import DashboardHandler, make_server


def serve_dashboard(host: str = "127.0.0.1", port: int = 8765, mesh_home: str | Path | None = None, auth_token: str | None = None) -> None:
    server = make_server(host=host, port=port, mesh_home=mesh_home, auth_token=auth_token)
    try:
        print(f"Capability Mesh dashboard listening on http://{host}:{server.server_port}")
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the read-only Capability Mesh dashboard.")
    parser.add_argument(
        "--mesh-home",
        default=None,
        help="Mesh registry home; defaults to $CAPABILITY_MESH_HOME, ~/.capability-mesh",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--auth-token", default=None)
    args = parser.parse_args(argv)
    serve_dashboard(host=args.host, port=args.port, mesh_home=args.mesh_home, auth_token=args.auth_token)
    return 0


__all__ = ["DashboardHandler", "make_server", "serve_dashboard", "main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
