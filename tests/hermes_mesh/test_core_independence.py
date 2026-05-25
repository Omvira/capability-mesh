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


def task_contract(task_id: str = "task-001") -> dict[str, object]:
    return {
        "schema_version": "capability-mesh-alpha-1",
        "task_id": task_id,
        "task_type": "test_running",
        "objective": "Run tests",
        "allowed_result_fields": ["final_summary", "test_report"],
        "forbidden_result_fields": ["secrets", "environment_variables"],
    }


def test_route_task_matches_tools_and_ranks_by_node_id():
    from hermes_mesh import build_default_capability_manifest, route_task

    zed = build_default_capability_manifest(
        node_id="zed-node",
        display_name="Zed",
        task_types=["test_running"],
        tools_available=["python", "pytest"],
    )
    alpha = build_default_capability_manifest(
        node_id="alpha-node",
        display_name="Alpha",
        task_types=["test_running"],
        tools_available=["python", "pytest"],
    )

    route = route_task(task_contract(), [zed, alpha], required_tools=["pytest"])

    assert route["selected_node"] == "alpha-node"
    assert route["candidates"] == ["alpha-node", "zed-node"]
    assert route["status"] == "awaiting_node_approval"


def test_route_task_rejects_nonmatches():
    from hermes_mesh import build_default_capability_manifest, route_task

    manifest = build_default_capability_manifest(
        node_id="docs-node",
        display_name="Docs",
        task_types=["docs"],
        tools_available=["python"],
    )

    route = route_task(task_contract(), [manifest], required_tools=["pytest"])

    assert route["status"] == "no_match"
    assert route["selected_node"] is None
    assert route["candidates"] == []


def test_route_task_auto_accept_status():
    from hermes_mesh import build_default_capability_manifest, route_task

    manifest = build_default_capability_manifest(
        node_id="auto-node",
        display_name="Auto",
        task_types=["test_running"],
        tools_available=["python"],
        requires_human_approval=False,
    )
    manifest["policies"]["auto_accept_task_types"] = ["test_running"]

    route = route_task(task_contract(), [manifest])

    assert route["status"] == "auto_assigned"


def test_record_task_result_filters_private_fields(tmp_path):
    from hermes_mesh import list_task_results, post_task, record_task_result


    post_task(task_contract(), mesh_home=tmp_path)
    record_task_result(
        {
            "task_id": "task-001",
            "node_id": "node-1",
            "status": "completed",
            "result": {
                "final_summary": "ok token=abc123",
                "test_report": "1 passed",
                "environment_variables": {"TOKEN": "no"},
                "raw_private_logs": "no",
            },
        },
        task_contract(),
        mesh_home=tmp_path,
    )

    records = list_task_results(mesh_home=tmp_path)
    assert records[0]["result"] == {
        "final_summary": "ok token=[REDACTED]",
        "test_report": "1 passed",
    }
    assert records[0]["verification_report"]["status"] == "passed"


def test_contribution_record_requires_consent_for_public_visibility():
    import pytest

    from hermes_mesh import CapabilityMeshValidationError, validate_contribution_record

    with pytest.raises(CapabilityMeshValidationError):
        validate_contribution_record(
            {
                "schema_version": "capability-mesh-alpha-1",
                "contribution_id": "contrib-1",
                "task_id": "task-001",
                "node_id": "node-1",
                "summary": "Useful result",
                "visibility": "public_commons",
                "human_consent": False,
            }
        )


def test_standalone_cli_post_route_record_and_contributions(tmp_path):
    mesh_home = tmp_path / "mesh-home"
    manifest_path = tmp_path / "manifest.yaml"
    task_path = tmp_path / "task.yaml"
    result_path = tmp_path / "result.yaml"
    manifest = {
        "schema_version": "capability-mesh-alpha-1",
        "node_id": "cli-node",
        "display_name": "CLI Node",
        "capabilities": {"task_types": ["test_running"], "tools_available": ["python", "pytest"]},
        "policies": {
            "accepts_tasks": True,
            "auto_accept_task_types": ["test_running"],
            "requires_human_approval": False,
        },
        "privacy": {
            "expose_local_skills": False,
            "expose_memory": False,
            "expose_session_history": False,
            "expose_reasoning_trace": False,
            "expose_raw_logs": False,
            "expose_environment": False,
        },
        "result_policy": {
            "allow": ["final_summary", "test_report"],
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
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    task_path.write_text(yaml.safe_dump(task_contract("cli-task")), encoding="utf-8")
    result_path.write_text(
        yaml.safe_dump(
            {
                "task_id": "cli-task",
                "node_id": "cli-node",
                "status": "completed",
                "result": {
                    "final_summary": "done api_key=abc123",
                    "test_report": "2 passed",
                    "secrets": "no",
                },
            }
        ),
        encoding="utf-8",
    )

    register = run_mesh_cli("--mesh-home", str(mesh_home), "register", str(manifest_path))
    posted = run_mesh_cli("--mesh-home", str(mesh_home), "post-task", str(task_path))
    routed = run_mesh_cli(
        "--mesh-home",
        str(mesh_home),
        "route-task",
        str(task_path),
        "--required-tool",
        "pytest",
        "--json",
    )
    recorded = run_mesh_cli("--mesh-home", str(mesh_home), "record-result", str(result_path))
    contributions = run_mesh_cli("--mesh-home", str(mesh_home), "contributions", "--json")

    assert register.returncode == 0, register.stderr
    assert posted.returncode == 0, posted.stderr
    assert routed.returncode == 0, routed.stderr
    assert json.loads(routed.stdout)["status"] == "auto_assigned"
    assert (mesh_home / "assignments" / "cli-task-cli-node.yaml").exists()
    assert recorded.returncode == 0, recorded.stderr
    result_record = yaml.safe_load((mesh_home / "results" / "cli-task-cli-node-result.yaml").read_text())
    assert result_record["result"]["final_summary"] == "done api_key=[REDACTED]"
    assert contributions.returncode == 0, contributions.stderr
    assert json.loads(contributions.stdout)[0]["visibility"] == "local_private"
