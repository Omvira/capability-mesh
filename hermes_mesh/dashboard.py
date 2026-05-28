"""Legacy compatibility shim for Capability Mesh dashboard helpers."""

from capability_mesh.server.app import main, serve_dashboard
from capability_mesh.server.api import DashboardHandler, make_server
from capability_mesh.server.public_projection import *  # noqa: F401,F403

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
