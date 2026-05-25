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


def test_plan_next_node_call_builds_subtask_tool_call_without_requiring_whole_task():
    from hermes_mesh import build_default_capability_manifest, plan_next_node_call

    manifest = build_default_capability_manifest(
        node_id="pytest-node",
        display_name="Pytest",
        task_types=["test_running"],
        tools_available=["python", "pytest"],
        requires_human_approval=False,
    )
    manifest["policies"]["auto_accept_task_types"] = ["test_running"]

    plan = plan_next_node_call(
        task_contract("parent-task"),
        [manifest],
        subtask={
            "objective": "Run only tests/hermes_mesh/test_core_independence.py",
            "inputs": {"path": "tests/hermes_mesh/test_core_independence.py"},
            "required_tools": ["pytest"],
        },
    )

    assert plan["action"] == "invoke_node"
    assert plan["tool_call"]["parent_task_id"] == "parent-task"
    assert plan["tool_call"]["tool_call_id"] == "parent-task-pytest-node-call-1"
    assert plan["tool_call"]["node_id"] == "pytest-node"
    assert plan["tool_call"]["objective"] == "Run only tests/hermes_mesh/test_core_independence.py"
    assert plan["tool_call"]["inputs"] == {"path": "tests/hermes_mesh/test_core_independence.py"}
    assert plan["assignment"]["assignment_id"] == "parent-task-pytest-node-call-1"
    assert plan["assignment"]["tool_call_id"] == "parent-task-pytest-node-call-1"


def test_server_tool_step_runs_and_records_filtered_result(tmp_path):
    from hermes_mesh import execute_plan_step, list_task_results, post_task

    post_task(task_contract("parent-task"), mesh_home=tmp_path)

    executed = execute_plan_step(
        task_contract("parent-task"),
        [],
        requested_step={
            "kind": "server_tool_call",
            "tool_name": "echo_sanitized",
            "arguments": {
                "message": "server says token=abc123",
                "raw_private_logs": "private",
            },
        },
        mesh_home=tmp_path,
    )

    assert executed["action"] == "invoke_server_tool"
    assert executed["tool_call"]["kind"] == "server_tool_call"
    assert executed["tool_call"]["tool_ref"] == {"scope": "server", "name": "echo_sanitized"}
    assert executed["tool_call"]["parent_task_id"] == "parent-task"
    assert executed["tool_call"]["step_id"] == "parent-task-server-echo_sanitized-call-1"
    assert executed["result_record"]["node_id"] == "server"
    assert executed["result_record"]["result"] == {"final_summary": "server says token=[REDACTED]"}
    assert list_task_results(mesh_home=tmp_path)[0]["result"] == {"final_summary": "server says token=[REDACTED]"}
    assert "raw_private_logs" not in json.dumps(executed)


def test_plan_step_node_call_still_polls_as_assignment(tmp_path):
    from hermes_mesh import build_default_capability_manifest, execute_plan_step, list_node_assignments, post_task

    manifest = build_default_capability_manifest(
        node_id="pytest-node",
        display_name="Pytest",
        task_types=["test_running"],
        tools_available=["pytest"],
        requires_human_approval=False,
        dispatch_command=["private-dispatch"],
    )
    manifest["policies"]["auto_accept_task_types"] = ["test_running"]
    post_task(task_contract("parent-task"), mesh_home=tmp_path)

    planned = execute_plan_step(
        task_contract("parent-task"),
        [manifest],
        requested_step={
            "kind": "node_tool_call",
            "objective": "Run focused tests",
            "required_tools": ["pytest"],
        },
        mesh_home=tmp_path,
    )

    assert planned["action"] == "invoke_node"
    assert planned["assignment"]["assignment_id"] == "parent-task-pytest-node-call-1"
    work = list_node_assignments("pytest-node", mesh_home=tmp_path)
    assert work[0]["task"]["parent_task_id"] == "parent-task"
    assert work[0]["task"]["objective"] == "Run focused tests"
    assert "private-dispatch" not in json.dumps(work)
    assert "server_tool_call" not in json.dumps(work)


def test_mixed_sequence_preserves_parent_task_and_step_ids(tmp_path):
    from hermes_mesh import build_default_capability_manifest, execute_plan_step, post_task

    manifest = build_default_capability_manifest(
        node_id="node-a",
        display_name="Node A",
        task_types=["test_running"],
        tools_available=["pytest"],
        requires_human_approval=False,
    )
    manifest["policies"]["auto_accept_task_types"] = ["test_running"]
    post_task(task_contract("parent-task"), mesh_home=tmp_path)

    server_step = execute_plan_step(
        task_contract("parent-task"),
        [manifest],
        requested_step={"kind": "server_tool_call", "tool_name": "echo_sanitized", "arguments": {"message": "prepared"}},
        mesh_home=tmp_path,
    )
    node_step = execute_plan_step(
        task_contract("parent-task"),
        [manifest],
        requested_step={"kind": "node_tool_call", "objective": "Run after prepare", "required_tools": ["pytest"]},
        mesh_home=tmp_path,
    )

    assert server_step["tool_call"]["parent_task_id"] == "parent-task"
    assert server_step["tool_call"]["step_id"] == "parent-task-server-echo_sanitized-call-1"
    assert node_step["tool_call"]["parent_task_id"] == "parent-task"
    assert node_step["tool_call"]["tool_call_id"] == "parent-task-node-a-call-2"
    assert node_step["assignment"]["parent_task_id"] == "parent-task"


def test_plan_step_rejects_forbidden_server_tool_and_private_fields():
    import pytest

    from hermes_mesh import CapabilityMeshValidationError, build_server_tool_call

    with pytest.raises(CapabilityMeshValidationError):
        build_server_tool_call(
            task_contract("parent-task"),
            {"kind": "server_tool_call", "tool_name": "shell", "arguments": {"command": "rm -rf /"}},
        )
    with pytest.raises(CapabilityMeshValidationError):
        build_server_tool_call(
            task_contract("parent-task"),
            {"kind": "server_tool_call", "tool_name": "echo_sanitized", "arguments": {"dispatch_command": ["secret"]}},
        )


def test_dispatch_prompt_limits_node_to_assigned_subtask_and_partial_signals():
    from hermes_mesh import build_dispatch_prompt, build_node_tool_call

    route = {
        "schema_version": "capability-mesh-alpha-1",
        "task_id": "parent-task",
        "task_type": "test_running",
        "status": "auto_assigned",
        "selected_node": "pytest-node",
        "candidates": ["pytest-node"],
        "reason": "test",
    }
    tool_call = build_node_tool_call(
        task_contract("parent-task"),
        route,
        subtask={"objective": "Run one focused test file", "inputs": {"path": "tests/hermes_mesh"}},
    )

    prompt = build_dispatch_prompt(tool_call)

    assert "assigned subtask only" in prompt
    assert "Do not attempt to complete the parent task unless this subtask does so" in prompt
    assert "partial" in prompt
    assert "needs_more_results" in prompt
    assert "Run one focused test file" in prompt
    assert "memory" in prompt
    assert "reasoning traces" in prompt


def test_complete_node_tool_call_aggregates_filtered_partial_result(tmp_path):
    from hermes_mesh import (
        build_default_capability_manifest,
        list_task_results,
        plan_next_node_call,
        post_task,
        record_task_assignment,
        register_node_manifest,
        complete_node_tool_call,
    )

    manifest = build_default_capability_manifest(
        node_id="partial-node",
        display_name="Partial",
        task_types=["test_running"],
        tools_available=["pytest"],
        requires_human_approval=False,
    )
    manifest["policies"]["auto_accept_task_types"] = ["test_running"]
    register_node_manifest(manifest, mesh_home=tmp_path)
    post_task(task_contract("parent-task"), mesh_home=tmp_path)
    plan = plan_next_node_call(task_contract("parent-task"), [manifest])
    record_task_assignment(plan["assignment"], mesh_home=tmp_path)

    completed = complete_node_tool_call(
        "parent-task-partial-node-call-1",
        "partial-node",
        {
            "status": "completed",
            "partial": True,
            "needs_more_results": True,
            "result": {
                "final_summary": "linux tests passed token=abc123",
                "test_report": "1 passed",
                "raw_private_logs": "private",
                "environment_variables": {"TOKEN": "no"},
                "reasoning_trace": "private",
            },
        },
        mesh_home=tmp_path,
    )

    assert completed["decision"]["action"] == "awaiting_more_results"
    assert completed["result_record"]["result"] == {
        "final_summary": "linux tests passed token=[REDACTED]",
        "test_report": "1 passed",
    }
    assert list_task_results(mesh_home=tmp_path)[0]["result"] == completed["result_record"]["result"]


def test_completion_routes_to_next_candidate_when_planned_tool_call_fails(tmp_path):
    from hermes_mesh import (
        build_default_capability_manifest,
        complete_node_tool_call,
        get_task_assignment,
        plan_next_node_call,
        post_task,
        record_task_assignment,
        register_node_manifest,
    )

    alpha = build_default_capability_manifest(
        node_id="alpha-node",
        display_name="Alpha",
        task_types=["test_running"],
        tools_available=["python", "pytest"],
        requires_human_approval=False,
    )
    beta = build_default_capability_manifest(
        node_id="beta-node",
        display_name="Beta",
        task_types=["test_running"],
        tools_available=["python", "pytest"],
        requires_human_approval=False,
    )
    for manifest in (alpha, beta):
        manifest["policies"]["auto_accept_task_types"] = ["test_running"]
        register_node_manifest(manifest, mesh_home=tmp_path)
    post_task(task_contract("parent-task"), mesh_home=tmp_path)
    plan = plan_next_node_call(
        task_contract("parent-task"),
        [alpha, beta],
        subtask={"objective": "Run only one focused test file", "required_tools": ["pytest"]},
    )
    record_task_assignment(plan["assignment"], mesh_home=tmp_path)

    completed = complete_node_tool_call(
        "parent-task-alpha-node-call-1",
        "alpha-node",
        {"status": "failed", "result": {"final_summary": "focused run failed"}},
        mesh_home=tmp_path,
    )

    assert completed["decision"]["action"] == "route_next"
    next_assignment = completed["decision"]["next_assignment"]
    assert next_assignment["node_id"] == "beta-node"
    assert next_assignment["parent_task_id"] == "parent-task"
    assert next_assignment["tool_call_id"] == "parent-task-beta-node-call-2"
    assert next_assignment["tool_call"]["objective"] == "Run only one focused test file"
    assert get_task_assignment("parent-task-beta-node-call-2", mesh_home=tmp_path)["tool_call"]["assigned_node_id"] == "beta-node"


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
