"""Tests for HermesMesh HTTP service and client helpers."""

from __future__ import annotations

import json
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
