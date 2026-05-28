"""Architecture tests for Capability Mesh's public server/client/ui split."""

from __future__ import annotations

import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_capability_mesh_exposes_server_client_ui_packages():
    """The public architecture is server/, client/, ui/ — not a dashboard-only shim."""

    for package_name in [
        "capability_mesh.server",
        "capability_mesh.server.app",
        "capability_mesh.server.api",
        "capability_mesh.client",
        "capability_mesh.client.http",
        "capability_mesh.ui",
        "capability_mesh.ui.dashboard",
    ]:
        module = importlib.import_module(package_name)
        assert module is not None


def test_server_client_ui_source_files_are_real_modules_not_legacy_shims():
    """New modules should contain first-class code instead of only importing hermes_mesh.*."""

    expected_files = [
        ROOT / "capability_mesh" / "server" / "__init__.py",
        ROOT / "capability_mesh" / "server" / "app.py",
        ROOT / "capability_mesh" / "server" / "api.py",
        ROOT / "capability_mesh" / "client" / "__init__.py",
        ROOT / "capability_mesh" / "client" / "http.py",
        ROOT / "capability_mesh" / "ui" / "__init__.py",
        ROOT / "capability_mesh" / "ui" / "dashboard.py",
    ]

    for path in expected_files:
        assert path.exists(), f"missing architecture module: {path.relative_to(ROOT)}"
        source = path.read_text(encoding="utf-8")
        assert "from hermes_mesh" not in source
        assert "import hermes_mesh" not in source


def test_legacy_hermes_mesh_modules_delegate_to_capability_mesh_architecture():
    """Legacy names remain, but implementation should live in capability_mesh."""

    legacy_modules = [
        ROOT / "hermes_mesh" / "client.py",
        ROOT / "hermes_mesh" / "dashboard.py",
        ROOT / "hermes_mesh" / "mcp_server.py",
    ]

    for path in legacy_modules:
        source = path.read_text(encoding="utf-8")
        assert "capability_mesh" in source, f"{path.relative_to(ROOT)} should delegate to capability_mesh"
