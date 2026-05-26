"""Tests for HermesMesh HTTP service and client helpers."""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request

import pytest


def _register_dashboard_node(mesh_home):
    from hermes_mesh import build_default_capability_manifest, register_node_manifest

    manifest = build_default_capability_manifest(
        node_id="dash-node",
        display_name="Dashboard Node",
        task_types=["code_review", "test_running"],
        tools_available=["python", "pytest"],
        resources={"cpu": "shared", "models": ["small", "medium"]},
        transport_command=["/usr/bin/private-runner", "SECRET_TRANSPORT_COMMAND"],
    )
    manifest["policies"]["auto_accept_task_types"] = ["test_running"]
    manifest["policies"]["requires_human_approval"] = False
    register_node_manifest(manifest, mesh_home=mesh_home)
    return manifest


@pytest.fixture
def dashboard_url(tmp_path):
    from hermes_mesh.dashboard import make_server

    _register_dashboard_node(tmp_path)
    server = make_server(port=0, mesh_home=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _get_json(url: str):
    with urllib.request.urlopen(url, timeout=10) as response:
        assert response.headers["Content-Type"].startswith("application/json")
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.headers["Content-Type"].startswith("application/json")
        return json.loads(response.read().decode("utf-8"))


def _get_text(url: str):
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode("utf-8")


def _task(task_id="task-1", task_type="test_running"):
    return {
        "schema_version": "capability-mesh-alpha-1",
        "task_id": task_id,
        "task_type": task_type,
        "objective": "Run the unit tests",
        "inputs": {"path": "tests"},
        "allowed_result_fields": ["final_summary", "test_report"],
        "forbidden_result_fields": [
            "raw_private_logs",
            "environment_variables",
            "secrets",
            "full_session_transcript",
            "private_memory",
            "reasoning_trace",
            "local_skills",
        ],
        "required_tools": ["pytest"],
    }


def test_dashboard_api_json_lists_registered_nodes_and_capabilities(dashboard_url):
    nodes = _get_json(f"{dashboard_url}/api/nodes")

    assert nodes == [
        {
            "node_id": "dash-node",
            "display_name": "Dashboard Node",
            "task_types": ["code_review", "test_running"],
            "tools_available": ["python", "pytest"],
            "resources": {"cpu": "shared", "models": "2 item(s)"},
            "policies": {
                "accepts_tasks": True,
                "requires_human_approval": False,
                "auto_accept_task_types": ["test_running"],
            },
            "transport": {"type": "local"},
            "privacy": {
                "expose_environment": "safe",
                "expose_local_skills": "safe",
                "expose_memory": "safe",
                "expose_raw_logs": "safe",
                "expose_reasoning_trace": "safe",
                "expose_session_history": "safe",
            },
        }
    ]


def test_dashboard_api_gets_single_node(dashboard_url):
    node = _get_json(f"{dashboard_url}/api/nodes/dash-node")

    assert node["node_id"] == "dash-node"
    assert node["display_name"] == "Dashboard Node"
    assert node["task_types"] == ["code_review", "test_running"]


def test_dashboard_html_includes_registered_nodes_and_capabilities(dashboard_url):
    html = _get_text(f"{dashboard_url}/")

    assert "HermesMesh Dashboard" in html
    assert "Dashboard Node" in html
    assert "dash-node" in html
    assert "code_review" in html
    assert "pytest" in html
    assert "requires_human_approval: false" in html


def test_dashboard_html_renders_kanban_columns_and_public_cards(dashboard_url):
    _post_json(f"{dashboard_url}/api/tasks", _task("task-board"))
    _post_json(f"{dashboard_url}/api/tasks/route", {"task": _task("task-board"), "required_tools": ["pytest"]})
    _post_json(
        f"{dashboard_url}/api/results",
        {
            "task": _task("task-board"),
            "result": {
                "task_id": "task-board",
                "node_id": "dash-node",
                "status": "completed",
                "result": {
                    "final_summary": "board ok password=SECRET",
                    "test_report": "passed",
                    "raw_private_logs": "hidden",
                },
            },
        },
    )

    html = _get_text(f"{dashboard_url}/")

    assert "mesh-kanban" in html
    assert "Posted" in html
    assert "Assigned" in html
    assert "Completed" in html
    assert "task-board" in html
    assert "dash-node" in html
    assert "board ok password=[REDACTED]" in html
    assert "raw_private_logs" not in html
    assert "do not expose" not in html


def test_dashboard_board_api_returns_kanban_shape_without_private_transport(dashboard_url):
    _post_json(f"{dashboard_url}/api/tasks", _task("task-board-api"))
    _post_json(f"{dashboard_url}/api/tasks/route", {"task": _task("task-board-api"), "required_tools": ["pytest"]})

    board = _get_json(f"{dashboard_url}/api/board")

    assert [column["id"] for column in board["columns"]] == [
        "posted",
        "assigned",
        "claimed",
        "completed",
        "failed",
        "results",
    ]
    assigned = next(column for column in board["columns"] if column["id"] == "assigned")
    assert assigned["cards"][0]["id"] == "task-board-api-dash-node"
    body = json.dumps(board)
    assert "SECRET_TRANSPORT_COMMAND" not in body
    assert "/usr/bin/private-runner" not in body
    assert "dispatch_command" not in body
    assert "raw_private_logs" not in body


def test_dashboard_returns_404_for_unknown_node(dashboard_url):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get_json(f"{dashboard_url}/api/nodes/missing-node")

    assert excinfo.value.code == 404


def test_dashboard_never_exposes_transport_command(dashboard_url):
    nodes_json = json.dumps(_get_json(f"{dashboard_url}/api/nodes"))
    node_json = json.dumps(_get_json(f"{dashboard_url}/api/nodes/dash-node"))
    html = _get_text(f"{dashboard_url}/")

    assert "command" not in nodes_json
    assert "SECRET_TRANSPORT_COMMAND" not in nodes_json
    assert "/usr/bin/private-runner" not in nodes_json
    assert "command" not in node_json
    assert "SECRET_TRANSPORT_COMMAND" not in node_json
    assert "/usr/bin/private-runner" not in node_json
    assert "SECRET_TRANSPORT_COMMAND" not in html
    assert "/usr/bin/private-runner" not in html


def test_server_routes_tasks_and_records_filtered_results(dashboard_url):
    posted = _post_json(f"{dashboard_url}/api/tasks", _task())
    assert posted["ok"] is True

    route = _post_json(f"{dashboard_url}/api/tasks/route", {"task": _task(), "required_tools": ["pytest"]})
    assert route["route"]["selected_node"] == "dash-node"
    assert route["assignment"]["node_id"] == "dash-node"

    saved = _post_json(
        f"{dashboard_url}/api/results",
        {
            "task": _task(),
            "result": {
                "task_id": "task-1",
                "node_id": "dash-node",
                "status": "completed",
                "result": {
                    "final_summary": "ok token=SECRET_SHOULD_REDACT",
                    "test_report": "passed",
                    "raw_private_logs": "do not expose",
                },
            },
        },
    )
    assert saved["ok"] is True

    results = _get_json(f"{dashboard_url}/api/results")
    assert results[0]["result"] == {
        "final_summary": "ok token=[REDACTED]",
        "test_report": "passed",
    }


def test_client_can_call_standalone_server(dashboard_url):
    from hermes_mesh.client import HermesMeshClient

    client = HermesMeshClient(dashboard_url)
    assert client.health()["ok"] is True
    assert client.list_nodes()[0]["node_id"] == "dash-node"
    assert client.post_task(_task("task-2"))["ok"] is True
    routed = client.route_task(_task("task-2"), required_tools=["pytest"])
    assert routed["route"]["selected_node"] == "dash-node"


def test_server_exposes_planning_step_for_node_tool_calls(dashboard_url):
    _post_json(f"{dashboard_url}/api/tasks", _task("task-plan"))

    planned = _post_json(
        f"{dashboard_url}/api/tasks/plan",
        {
            "task": _task("task-plan"),
            "subtask": {
                "objective": "Run only the dashboard tests",
                "inputs": {"path": "tests/hermes_mesh/test_dashboard.py"},
                "required_tools": ["pytest"],
            },
        },
    )

    assert planned["plan"]["action"] == "invoke_node"
    assert planned["tool_call"]["parent_task_id"] == "task-plan"
    assert planned["tool_call"]["objective"] == "Run only the dashboard tests"
    assert planned["assignment"]["assignment_id"] == "task-plan-dash-node-call-1"
    assert planned["assignment"]["tool_call_id"] == "task-plan-dash-node-call-1"

    work = _get_json(f"{dashboard_url}/api/nodes/dash-node/assignments")
    assert work[0]["task"]["task_id"] == "task-plan-dash-node-call-1"
    assert work[0]["task"]["parent_task_id"] == "task-plan"
    assert work[0]["task"]["objective"] == "Run only the dashboard tests"
    assert "SECRET_TRANSPORT_COMMAND" not in json.dumps(planned)


def test_server_exposes_mixed_plan_step_for_server_tool_calls(dashboard_url):
    _post_json(f"{dashboard_url}/api/tasks", _task("task-server-step"))

    planned = _post_json(
        f"{dashboard_url}/api/tasks/plan-step",
        {
            "task": _task("task-server-step"),
            "requested_step": {
                "kind": "server_tool_call",
                "tool_name": "echo_sanitized",
                "arguments": {
                    "message": "server ok api_key=abc123",
                },
            },
        },
    )

    assert planned["plan"]["action"] == "invoke_server_tool"
    assert planned["tool_call"]["kind"] == "server_tool_call"
    assert planned["tool_call"]["parent_task_id"] == "task-server-step"
    assert planned["result_record"]["result"] == {"final_summary": "server ok api_key=[REDACTED]"}
    body = json.dumps(planned)
    assert "dispatch_command" not in body
    assert "SECRET_TRANSPORT_COMMAND" not in body


def test_server_plan_step_can_mix_server_then_node_without_private_leaks(dashboard_url):
    _post_json(f"{dashboard_url}/api/tasks", _task("task-mixed"))

    server_step = _post_json(
        f"{dashboard_url}/api/tasks/plan-step",
        {
            "task": _task("task-mixed"),
            "requested_step": {
                "kind": "server_tool_call",
                "tool_name": "echo_sanitized",
                "arguments": {"message": "prepare"},
            },
        },
    )
    node_step = _post_json(
        f"{dashboard_url}/api/tasks/plan-step",
        {
            "task": _task("task-mixed"),
            "requested_step": {
                "kind": "node_tool_call",
                "objective": "Run after server prepare",
                "required_tools": ["pytest"],
            },
        },
    )

    assert server_step["tool_call"]["step_id"] == "task-mixed-server-echo_sanitized-call-1"
    assert node_step["tool_call"]["tool_call_id"] == "task-mixed-dash-node-call-2"
    assert node_step["assignment"]["parent_task_id"] == "task-mixed"
    work = _get_json(f"{dashboard_url}/api/nodes/dash-node/assignments")
    assert work[0]["task"]["task_id"] == "task-mixed-dash-node-call-2"
    work_body = json.dumps(work)
    assert "server_tool_call" not in work_body
    assert "echo_sanitized" not in work_body
    assert "SECRET_TRANSPORT_COMMAND" not in work_body


def test_client_plan_step_uses_mixed_planning_endpoint(dashboard_url):
    from hermes_mesh.client import HermesMeshClient

    client = HermesMeshClient(dashboard_url)
    client.post_task(_task("task-client-step"))

    planned = client.plan_step(
        _task("task-client-step"),
        requested_step={"kind": "server_tool_call", "tool_name": "echo_sanitized", "arguments": {"message": "ok"}},
    )

    assert planned["plan"]["action"] == "invoke_server_tool"
    assert planned["result_record"]["result"] == {"final_summary": "ok"}


def test_client_plan_task_uses_planning_endpoint(dashboard_url):
    from hermes_mesh.client import HermesMeshClient

    client = HermesMeshClient(dashboard_url)
    client.post_task(_task("task-client-plan"))

    planned = client.plan_task(
        _task("task-client-plan"),
        subtask={"objective": "Run a focused subset", "required_tools": ["pytest"]},
    )

    assert planned["plan"]["action"] == "invoke_node"
    assert planned["tool_call"]["objective"] == "Run a focused subset"


def test_node_can_poll_claim_and_complete_assignment(dashboard_url):
    _post_json(f"{dashboard_url}/api/tasks", _task("task-orch"))
    _post_json(f"{dashboard_url}/api/tasks/route", {"task": _task("task-orch"), "required_tools": ["pytest"]})

    work = _get_json(f"{dashboard_url}/api/nodes/dash-node/assignments")
    assert len(work) == 1
    assert work[0]["assignment"]["assignment_id"] == "task-orch-dash-node"
    assert work[0]["assignment"]["status"] == "auto_assigned"
    assert work[0]["task"]["objective"] == "Run the unit tests"
    assert "transport" not in json.dumps(work)
    assert "SECRET_TRANSPORT_COMMAND" not in json.dumps(work)

    claimed = _post_json(
        f"{dashboard_url}/api/assignments/task-orch-dash-node/claim",
        {"node_id": "dash-node"},
    )
    assert claimed["assignment"]["status"] == "claimed"

    completed = _post_json(
        f"{dashboard_url}/api/assignments/task-orch-dash-node/complete",
        {
            "node_id": "dash-node",
            "result": {
                "status": "completed",
                "result": {
                    "final_summary": "done password=abc123",
                    "test_report": "1 passed",
                    "environment_variables": {"TOKEN": "no"},
                    "reasoning_trace": "private",
                    "raw_private_logs": "private",
                },
            },
        },
    )
    assert completed["decision"]["action"] == "completed"
    assert completed["assignment"]["status"] == "completed"
    assert completed["result_record"]["result"] == {
        "final_summary": "done password=[REDACTED]",
        "test_report": "1 passed",
    }
    assert completed["contribution"]["visibility"] == "local_private"


def test_completion_routes_to_next_candidate_when_result_fails(tmp_path):
    from hermes_mesh import build_default_capability_manifest
    from hermes_mesh.dashboard import make_server

    alpha = build_default_capability_manifest(
        node_id="alpha-node",
        display_name="Alpha",
        task_types=["test_running"],
        tools_available=["python", "pytest"],
    )
    beta = build_default_capability_manifest(
        node_id="beta-node",
        display_name="Beta",
        task_types=["test_running"],
        tools_available=["python", "pytest"],
    )
    alpha["policies"]["auto_accept_task_types"] = ["test_running"]
    alpha["policies"]["requires_human_approval"] = False
    beta["policies"]["auto_accept_task_types"] = ["test_running"]
    beta["policies"]["requires_human_approval"] = False
    from hermes_mesh import register_node_manifest

    register_node_manifest(alpha, mesh_home=tmp_path)
    register_node_manifest(beta, mesh_home=tmp_path)
    server = make_server(port=0, mesh_home=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        _post_json(f"{url}/api/tasks", _task("task-route-next"))
        routed = _post_json(f"{url}/api/tasks/route", {"task": _task("task-route-next")})
        assert routed["assignment"]["node_id"] == "alpha-node"

        completed = _post_json(
            f"{url}/api/assignments/task-route-next-alpha-node/complete",
            {"node_id": "alpha-node", "result": {"status": "failed", "result": {"final_summary": "failed"}}},
        )
        assert completed["decision"]["action"] == "route_next"
        assert completed["decision"]["next_assignment"]["node_id"] == "beta-node"
        assert _get_json(f"{url}/api/nodes/beta-node/assignments")[0]["assignment"]["assignment_id"] == "task-route-next-beta-node"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_client_run_next_assignment_dispatches_local_agent_and_completes(tmp_path):
    from hermes_mesh import build_default_capability_manifest, register_node_manifest
    from hermes_mesh.client import HermesMeshClient
    from hermes_mesh.dashboard import make_server

    manifest = build_default_capability_manifest(
        node_id="agent-node",
        display_name="Agent Node",
        task_types=["test_running"],
        tools_available=["python", "pytest"],
        dispatch_command=[
            sys.executable,
            "-c",
            "import json; print(json.dumps({'final_summary':'agent done token=abc123','test_report':'1 passed','environment_variables':{'TOKEN':'no'},'reasoning_trace':'private'}))",
        ],
    )
    manifest["policies"]["auto_accept_task_types"] = ["test_running"]
    manifest["policies"]["requires_human_approval"] = False
    register_node_manifest(manifest, mesh_home=tmp_path)

    server = make_server(port=0, mesh_home=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = HermesMeshClient(f"http://127.0.0.1:{server.server_port}")
        assert client.post_task(_task("task-agent"))["ok"] is True
        assert client.route_task(_task("task-agent"), required_tools=["pytest"])["assignment"]["node_id"] == "agent-node"

        response = client.run_next_assignment(manifest)
        assert response["decision"]["action"] == "completed"
        assert response["result_record"]["result"] == {
            "final_summary": "agent done token=[REDACTED]",
            "test_report": "1 passed",
        }
        assert client.poll_assignments("agent-node") == []
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
