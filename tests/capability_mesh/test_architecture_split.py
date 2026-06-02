"""Architecture tests for Capability Mesh's public Hub/Node/UI split."""

from __future__ import annotations

import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_capability_mesh_exposes_hub_node_ui_boundaries():
    """The public architecture is Hub / Node / Mesh UI."""

    for package_name in [
        "capability_mesh.hub",
        "capability_mesh.hub.registry",
        "capability_mesh.hub.relay",
        "capability_mesh.node",
        "capability_mesh.node.a2a",
        "capability_mesh.server",
        "capability_mesh.server.app",
        "capability_mesh.server.api",
        "capability_mesh.server.public_projection",
        "capability_mesh.client",
        "capability_mesh.client.http",
        "capability_mesh.ui",
    ]:
        module = importlib.import_module(package_name)
        assert module is not None


def test_mesh_ui_is_static_frontend_not_python_business_backend():
    """Mesh UI should be static frontend assets, not a second Python backend/BFF."""

    ui_root = ROOT / "capability_mesh" / "ui"
    assert (ui_root / "static" / "index.html").exists()
    assert (ui_root / "static" / "app.js").exists()
    assert (ui_root / "static" / "styles.css").exists()

    python_files = sorted(p.relative_to(ui_root).as_posix() for p in ui_root.glob("**/*.py"))
    assert python_files == ["__init__.py"]

    for asset in [ui_root / "static" / "index.html", ui_root / "static" / "app.js"]:
        source = asset.read_text(encoding="utf-8")
        assert "/api/ui/dashboard" in source
        assert "transport_command" not in source
        assert "wake_token" not in source


def test_ui_projection_api_belongs_to_mesh_server_and_is_public_only():
    """UI-specific aggregation is a Server public projection API, not an independent UI backend."""

    projection = importlib.import_module("capability_mesh.server.public_projection")
    assert hasattr(projection, "build_dashboard_ui_projection")
    assert hasattr(projection, "public_board")
    assert hasattr(projection, "render_ui_shell")

    source = (ROOT / "capability_mesh" / "server" / "public_projection.py").read_text(encoding="utf-8")
    assert "from capability_mesh.core" in source
    assert "from capability_mesh.ui" not in source
    assert "transport_command" not in source
    assert "wake_token" not in source


def test_server_api_serves_static_ui_and_public_projection_without_ui_backend():
    """The Hub HTTP service owns /api/ui/* and serves static Mesh UI assets directly."""

    source = (ROOT / "capability_mesh" / "server" / "api.py").read_text(encoding="utf-8")
    assert 'path == "/api/ui/dashboard"' in source
    assert "build_dashboard_ui_projection" in source
    assert "render_ui_shell" in source
    assert "ui backend" not in source.lower()
    assert "bff" not in source.lower()
