"""Tests for the standalone Capability Mesh core package."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def run_mesh_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "hermes_mesh.cli", *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_hermes_mesh_imports_without_hermes_modules():
    script = """
import builtins
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'hermes_constants' or name.startswith('hermes_cli'):
        raise AssertionError(f'unexpected Hermes import: {name}')
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
import hermes_mesh
from hermes_mesh import build_default_capability_manifest
manifest = build_default_capability_manifest(
    node_id='standalone-node',
    display_name='Standalone Node',
    task_types=['test_running'],
    tools_available=['python'],
)
assert manifest['privacy']['expose_memory'] is False
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_core_registry_uses_explicit_mesh_home(tmp_path):
    from hermes_mesh import build_default_capability_manifest, list_registered_nodes, register_node_manifest

    manifest = build_default_capability_manifest(
        node_id="core-node",
        display_name="Core Node",
        task_types=["code_review"],
        tools_available=["python"],
    )

    path = register_node_manifest(manifest, mesh_home=tmp_path)

    assert path == tmp_path / "nodes" / "core-node.yaml"
    assert list_registered_nodes(mesh_home=tmp_path)[0]["node_id"] == "core-node"


def test_standalone_cli_register_and_list_with_explicit_mesh_home(tmp_path):
    mesh_home = tmp_path / "mesh-home"
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema_version": "capability-mesh-alpha-1",
                "node_id": "standalone-cli-node",
                "display_name": "Standalone CLI Node",
                "capabilities": {"task_types": ["code_review"], "tools_available": ["python"]},
                "privacy": {
                    "expose_local_skills": False,
                    "expose_memory": False,
                    "expose_session_history": False,
                    "expose_reasoning_trace": False,
                    "expose_raw_logs": False,
                    "expose_environment": False,
                },
                "result_policy": {
                    "allow": ["final_summary"],
                    "deny": [
                        "raw_private_logs",
                        "environment_variables",
                        "secrets",
                        "full_session_transcript",
                        "private_memory",
                        "reasoning_trace",
                        "local_skills",
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    registered = run_mesh_cli("--mesh-home", str(mesh_home), "register", str(manifest))
    listed = run_mesh_cli("--mesh-home", str(mesh_home), "list", "--json")

    assert registered.returncode == 0, registered.stderr
    assert (mesh_home / "nodes" / "standalone-cli-node.yaml").exists()
    assert listed.returncode == 0, listed.stderr
    assert json.loads(listed.stdout)[0]["node_id"] == "standalone-cli-node"


def test_standalone_cli_filter_result_preserves_privacy(tmp_path):
    contract = tmp_path / "contract.yaml"
    result_path = tmp_path / "result.yaml"
    contract.write_text(
        yaml.safe_dump(
            {
                "schema_version": "capability-mesh-alpha-1",
                "task_id": "task-001",
                "task_type": "test_running",
                "objective": "Run tests",
                "allowed_result_fields": ["final_summary", "test_report"],
                "forbidden_result_fields": ["secrets", "environment_variables"],
            }
        ),
        encoding="utf-8",
    )
    result_path.write_text(
        yaml.safe_dump(
            {
                "final_summary": "ok secret=abc123",
                "test_report": "1 passed",
                "environment_variables": {"TOKEN": "no"},
            }
        ),
        encoding="utf-8",
    )

    result = run_mesh_cli("filter-result", str(result_path), "--contract", str(contract))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "final_summary": "ok secret=[REDACTED]",
        "test_report": "1 passed",
    }
