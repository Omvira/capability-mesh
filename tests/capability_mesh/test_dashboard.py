"""Tests for Capability Mesh HTTP service and client helpers."""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
import base64
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


def _register_dashboard_node(mesh_home):
    from capability_mesh import build_default_capability_manifest, register_node_manifest

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
    from capability_mesh.dashboard import make_server

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
        assert response.headers["Content-Type"].startswith(("application/json", "application/a2a+json"))
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
        assert response.headers["Content-Type"].startswith(("application/json", "application/a2a+json"))
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



def _wake_receiver():
    received: list[dict] = []
    headers_seen: list[dict] = []

    class WakeHandler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            received.append(payload)
            headers_seen.append(dict(self.headers))
            body = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), WakeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}/wake", received, headers_seen


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
            "online_status": {
                "status": "never_seen",
                "label": "never seen",
                "last_seen_at": None,
            },
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


def test_dashboard_html_hides_registered_nodes_until_nodes_stat_clicked(dashboard_url):
    html = _get_text(f"{dashboard_url}/")

    assert "Capability Mesh" in html
    assert 'id="nodesStat"' in html
    assert 'id="nodesDrawer"' in html
    assert 'aria-label="Registered private-runtime nodes"' not in html
    assert "<h2>Registered nodes</h2>" not in html
    assert "Dashboard Node" not in html
    assert "dash-node" not in html
    assert "requires_human_approval: false" not in html


def test_dashboard_node_statuses_api_lists_names_capabilities_and_online_status(dashboard_url):
    rows = _get_json(f"{dashboard_url}/api/nodes/statuses")

    assert rows == [
        {
            "node_id": "dash-node",
            "display_name": "Dashboard Node",
            "task_types": ["code_review", "test_running"],
            "tools_available": ["python", "pytest"],
            "online_status": {
                "status": "never_seen",
                "label": "never seen",
                "last_seen_at": None,
            },
        }
    ]


def test_node_heartbeat_updates_public_status_without_private_state(dashboard_url):
    from capability_mesh.client import CapabilityMeshClient

    client = CapabilityMeshClient(dashboard_url)

    response = client.heartbeat("dash-node", status="busy")

    assert response["ok"] is True
    assert response["node"]["online_status"]["status"] == "online"
    html = _get_text(f"{dashboard_url}/")
    assert "online" in html
    assert "/api/nodes/statuses" in html
    node = _get_json(f"{dashboard_url}/api/nodes/dash-node")
    assert node["online_status"]["status"] == "online"
    assert node["online_status"]["last_seen_at"]
    statuses = _get_json(f"{dashboard_url}/api/nodes/statuses")
    assert statuses[0]["online_status"]["status"] == "online"
    body = json.dumps(response) + json.dumps(node) + json.dumps(statuses)
    assert "busy" not in body
    assert "SECRET_TRANSPORT_COMMAND" not in body
    assert "dispatch_command" not in body
    assert "wake_url" not in body
    assert "token" not in body.lower()


def test_public_presence_derives_online_stale_offline_and_never_seen(tmp_path):
    from capability_mesh import build_default_capability_manifest, record_node_heartbeat, register_node_manifest
    from capability_mesh.dashboard import make_server

    now = datetime.now(timezone.utc).replace(microsecond=0)
    for node_id in ["online-node", "stale-node", "offline-node", "never-node"]:
        register_node_manifest(
            build_default_capability_manifest(
                node_id=node_id,
                display_name=node_id,
                task_types=["test_running"],
                tools_available=["pytest"],
            ),
            mesh_home=tmp_path,
        )
    record_node_heartbeat("online-node", mesh_home=tmp_path, seen_at=(now - timedelta(seconds=10)).isoformat().replace("+00:00", "Z"))
    record_node_heartbeat("stale-node", mesh_home=tmp_path, seen_at=(now - timedelta(seconds=120)).isoformat().replace("+00:00", "Z"))
    record_node_heartbeat("offline-node", mesh_home=tmp_path, seen_at=(now - timedelta(seconds=600)).isoformat().replace("+00:00", "Z"))

    server = make_server(port=0, mesh_home=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        rows = _get_json(f"http://127.0.0.1:{server.server_port}/api/nodes/statuses")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    by_id = {row["node_id"]: row["online_status"]["status"] for row in rows}
    assert by_id == {
        "never-node": "never_seen",
        "offline-node": "offline",
        "online-node": "online",
        "stale-node": "stale",
    }


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


def test_dashboard_ui_api_returns_privacy_safe_rendering_projection(dashboard_url):
    projection = _get_json(f"{dashboard_url}/api/ui/dashboard")

    assert projection["title"] == "Capability Mesh"
    assert projection["issue_label"]
    assert "privacy" in projection["privacy_notice"].lower()
    assert projection["summary"] == {
        "node_count": 1,
        "task_type_count": 2,
        "tool_count": 2,
        "auto_accept_count": 1,
    }
    assert projection["actions"] == [
        {
            "label": "Load registered nodes",
            "href": "/api/nodes/statuses",
            "description": "Fetch public node status only when the drawer is opened.",
        },
        {
            "label": "View board JSON",
            "href": "/api/board",
            "description": "Inspect the privacy-filtered Kanban board projection.",
        },
    ]
    assert projection["nodes_drawer"] == {
        "title": "Registered nodes",
        "endpoint": "/api/nodes/statuses",
        "copy": "Node details are lazy-loaded so the dashboard shell stays privacy-light.",
    }

    body = json.dumps(projection)
    assert "Dashboard Node" not in body
    assert "dash-node" not in body
    assert "code_review" not in body
    assert "SECRET_TRANSPORT_COMMAND" not in body
    assert "/usr/bin/private-runner" not in body
    assert "dispatch_command" not in body
    assert "wake_url" not in body
    assert "token" not in body.lower()
    assert "environment_variables" not in body
    assert "raw_private_logs" not in body
    assert "private_memory" not in body
    assert "reasoning_trace" not in body
    assert "local_skills" not in body


def test_dashboard_ui_api_reuses_public_board_projection(dashboard_url):
    _post_json(f"{dashboard_url}/api/tasks", _task("task-ui-board"))
    _post_json(f"{dashboard_url}/api/tasks/route", {"task": _task("task-ui-board"), "required_tools": ["pytest"]})

    projection = _get_json(f"{dashboard_url}/api/ui/dashboard")
    board = _get_json(f"{dashboard_url}/api/board")

    assert projection["kanban"] == board
    assert [column["id"] for column in projection["kanban"]["columns"]] == [
        "posted",
        "assigned",
        "claimed",
        "completed",
        "failed",
        "results",
    ]
    for column in projection["kanban"]["columns"]:
        assert set(column) >= {
            "id",
            "title",
            "legacy_title",
            "kanban_status",
            "description",
            "count",
            "cards",
        }


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
    from capability_mesh.client import CapabilityMeshClient

    client = CapabilityMeshClient(dashboard_url)
    assert client.health()["ok"] is True
    assert client.list_nodes()[0]["node_id"] == "dash-node"
    assert client.post_task(_task("task-2"))["ok"] is True
    routed = client.route_task(_task("task-2"), required_tools=["pytest"])
    assert routed["route"]["selected_node"] == "dash-node"


def test_a2a_agent_card_exposes_safe_service_metadata(dashboard_url):
    from capability_mesh.client import CapabilityMeshClient

    card = CapabilityMeshClient(dashboard_url).agent_card()

    assert card["name"] == "Capability Mesh Server"
    assert card["url"] == dashboard_url
    assert card["protocolVersion"] == "1.0"
    assert card["protocolVersions"] == ["1.0"]
    assert card["preferredTransport"] == "HTTP+JSON"
    assert card["capabilities"]["streaming"] is False
    assert card["additionalInterfaces"][0]["url"] == f"{dashboard_url}/message:send"
    assert card["skills"][0]["id"] == "capability-mesh-message-transfer"
    body = json.dumps(card)
    assert "SECRET_TRANSPORT_COMMAND" not in body
    assert "dispatch_command" not in body
    assert "memory" not in body.lower()


def test_a2a_text_and_image_message_exchange_returns_artifacts(dashboard_url):
    from capability_mesh.client import CapabilityMeshClient

    image_bytes = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")
    response = CapabilityMeshClient(dashboard_url).send_a2a_message(
        {
            "role": "ROLE_USER",
            "parts": [
                {"text": "hello mesh"},
                {"raw": image_bytes, "filename": "pixel.png", "mediaType": "image/png"},
                {"data": {"request": "inspect"}, "mediaType": "application/json"},
            ],
        }
    )

    assert "task" in response
    task = response["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["history"][0]["parts"][1]["mediaType"] == "image/png"
    artifact_parts = task["artifacts"][0]["parts"]
    assert artifact_parts[1]["data"] == {"dataParts": 1, "fileParts": 1, "imageParts": 1, "textParts": 1}
    assert any(part.get("text") == "received 1 image file part(s)" for part in artifact_parts)


def test_server_sees_client_heartbeat_online_and_client_checks_health(dashboard_url):
    from capability_mesh.client import CapabilityMeshClient

    client = CapabilityMeshClient(dashboard_url)

    assert client.server_is_healthy() is True
    client.heartbeat("dash-node")

    assert _get_json(f"{dashboard_url}/api/nodes/dash-node")["online_status"]["status"] == "online"


def test_server_exposes_planning_step_for_node_tool_calls(dashboard_url):
    _post_json(f"{dashboard_url}/api/tasks", _task("task-plan"))

    planned = _post_json(
        f"{dashboard_url}/api/tasks/plan",
        {
            "task": _task("task-plan"),
            "subtask": {
                "objective": "Run only the dashboard tests",
                "inputs": {"path": "tests/capability_mesh/test_dashboard.py"},
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
    from capability_mesh.client import CapabilityMeshClient

    client = CapabilityMeshClient(dashboard_url)
    client.post_task(_task("task-client-step"))

    planned = client.plan_step(
        _task("task-client-step"),
        requested_step={"kind": "server_tool_call", "tool_name": "echo_sanitized", "arguments": {"message": "ok"}},
    )

    assert planned["plan"]["action"] == "invoke_server_tool"
    assert planned["result_record"]["result"] == {"final_summary": "ok"}


def test_client_plan_task_uses_planning_endpoint(dashboard_url):
    from capability_mesh.client import CapabilityMeshClient

    client = CapabilityMeshClient(dashboard_url)
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
    assert _get_json(f"{dashboard_url}/api/nodes/dash-node")["online_status"]["status"] == "online"

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
    from capability_mesh import build_default_capability_manifest
    from capability_mesh.dashboard import make_server

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
    from capability_mesh import register_node_manifest

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
    from capability_mesh import build_default_capability_manifest, register_node_manifest
    from capability_mesh.client import CapabilityMeshClient
    from capability_mesh.dashboard import make_server

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
        client = CapabilityMeshClient(f"http://127.0.0.1:{server.server_port}")
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



def test_server_can_wake_webhook_node_without_exposing_private_transport(tmp_path):
    from capability_mesh import build_default_capability_manifest, register_node_manifest
    from capability_mesh.dashboard import make_server, public_node_view

    wake_server, wake_thread, wake_url, received, headers_seen = _wake_receiver()
    try:
        manifest = build_default_capability_manifest(
            node_id="wake-node",
            display_name="Wake Node",
            task_types=["test_running"],
            tools_available=["pytest"],
        )
        manifest["policies"]["auto_accept_task_types"] = ["test_running"]
        manifest["policies"]["requires_human_approval"] = False
        manifest["transport"] = {
            "type": "webhook",
            "wake_url": wake_url,
            "wake_token": "SECRET_WAKE_TOKEN",
            "wake_timeout_seconds": 5,
            "dispatch_command": ["private-dispatch", "SECRET_TRANSPORT_COMMAND"],
        }
        register_node_manifest(manifest, mesh_home=tmp_path)

        server = make_server(port=0, mesh_home=tmp_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            _post_json(f"{url}/api/tasks", _task("task-wake"))
            routed = _post_json(f"{url}/api/tasks/route", {"task": _task("task-wake"), "required_tools": ["pytest"]})

            woken = _post_json(f"{url}/api/assignments/{routed['assignment']['assignment_id']}/wake", {})

            assert woken["ok"] is True
            assert woken["wake"]["status"] == "sent"
            assert woken["wake"]["assignment_id"] == "task-wake-wake-node"
            assert woken["wake"]["node_id"] == "wake-node"
            assert received == [
                {
                    "schema_version": "capability-mesh-alpha-1",
                    "event": "assignment_available",
                    "assignment_id": "task-wake-wake-node",
                    "node_id": "wake-node",
                    "server_url": url,
                }
            ]
            lower_headers = {key.lower(): value for key, value in headers_seen[0].items()}
            assert lower_headers["x-capabilitymesh-wake-token"] == "SECRET_WAKE_TOKEN"
            body = json.dumps(woken) + json.dumps(_get_json(f"{url}/api/nodes")) + json.dumps(public_node_view(manifest))
            assert "SECRET_WAKE_TOKEN" not in body
            assert wake_url not in body
            assert "SECRET_TRANSPORT_COMMAND" not in body
            assert "dispatch_command" not in body
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    finally:
        wake_server.shutdown()
        wake_thread.join(timeout=5)
        wake_server.server_close()


def test_client_and_cli_can_request_assignment_wake(dashboard_url):
    from capability_mesh.client import CapabilityMeshClient
    from capability_mesh.cli import build_parser

    client = CapabilityMeshClient(dashboard_url)
    client.post_task(_task("task-client-wake"))
    routed = client.route_task(_task("task-client-wake"), required_tools=["pytest"])

    response = client.wake_assignment(routed["assignment"]["assignment_id"])

    assert response["ok"] is True
    assert response["wake"]["status"] == "unsupported"
    args = build_parser().parse_args(["client", "--url", dashboard_url, "wake", routed["assignment"]["assignment_id"]])
    assert args.client_command == "wake"


def test_cli_parses_mcp_server_url_aliases(dashboard_url):
    from capability_mesh.cli import build_parser

    by_url = build_parser().parse_args(["mcp-server", "--url", dashboard_url, "--timeout", "3"])
    by_mesh_url = build_parser().parse_args(["mcp-server", "--mesh-url", dashboard_url])

    assert by_url.command == "mcp-server"
    assert by_url.mesh_url == dashboard_url
    assert by_url.timeout == 3
    assert by_mesh_url.mesh_url == dashboard_url


def test_mcp_sanitization_removes_private_transport_fields():
    from capability_mesh.mcp_server import sanitize_for_mcp

    sanitized = sanitize_for_mcp(
        {
            "node_id": "safe-node",
            "environment_variables": {"HOME": "/private"},
            "private_memory": "do not expose",
            "session_history": ["private chat"],
            "apiKey": "SECRET_API_KEY",
            "password": "SECRET_PASSWORD",
            "secrets": {"value": "SECRET_VALUE"},
            "raw_private_logs": "SECRET_LOGS",
            "transport": {
                "type": "webhook",
                "command": ["private-runner"],
                "wake_url": "http://example.invalid/private",
                "wake_token": "SECRET_WAKE_TOKEN",
                "nestedToken": "SECRET_NESTED_TOKEN",
            },
            "assignment": {
                "assignment_id": "assignment-1",
                "dispatch_command": ["private-dispatch"],
                "tool_call": {"name": "public_tool"},
            },
            "items": ({"transport_command": ["private"]},),
        }
    )

    body = json.dumps(sanitized)
    assert sanitized["node_id"] == "safe-node"
    assert sanitized["transport"] == {"type": "webhook"}
    assert sanitized["assignment"] == {"assignment_id": "assignment-1", "tool_call": {"name": "public_tool"}}
    assert sanitized["items"] == [{}]
    assert "SECRET" not in body
    assert "environment_variables" not in body
    assert "private_memory" not in body
    assert "session_history" not in body
    assert "apiKey" not in body
    assert "password" not in body
    assert "secrets" not in body
    assert "raw_private_logs" not in body
    assert "wake_url" not in body
    assert "command" not in body


def test_mcp_server_registers_expected_tools_and_runs_stdio(monkeypatch):
    import types

    from capability_mesh import mcp_server

    registered: dict[str, object] = {}
    run_calls: list[str] = []

    class FakeFastMCP:
        def __init__(self, name: str):
            self.name = name

        def tool(self):
            def decorator(func):
                registered[func.__name__] = func
                return func

            return decorator

        def run(self, *, transport: str):
            run_calls.append(transport)

    monkeypatch.setitem(sys.modules, "mcp", types.ModuleType("mcp"))
    monkeypatch.setitem(sys.modules, "mcp.server", types.ModuleType("mcp.server"))
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    setattr(fastmcp_module, "FastMCP", FakeFastMCP)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)

    rc = mcp_server.run_mcp_server("http://example.invalid", timeout=2)

    assert rc == 0
    assert run_calls == ["stdio"]
    assert set(registered) == {
        "list_clients",
        "get_client",
        "call_client_async",
        "create_assignment",
        "get_assignment_status",
        "send_a2a_message",
    }


def test_mcp_server_reports_clear_error_when_sdk_missing(monkeypatch, capsys):
    import builtins

    from capability_mesh.mcp_server import run_mcp_server

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("no mcp sdk")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    rc = run_mcp_server("http://example.invalid")

    assert rc == 1
    assert "Python MCP SDK is required" in capsys.readouterr().err


def test_client_cli_parses_heartbeat_commands(dashboard_url):
    from capability_mesh.cli import build_parser

    heartbeat = build_parser().parse_args(["client", "--url", dashboard_url, "heartbeat", "dash-node", "--status", "idle"])
    loop = build_parser().parse_args(
        ["client", "--url", dashboard_url, "heartbeat-loop", "dash-node", "--interval", "5", "--status", "online"]
    )

    assert heartbeat.client_command == "heartbeat"
    assert heartbeat.node_id == "dash-node"
    assert heartbeat.status == "idle"
    assert loop.client_command == "heartbeat-loop"
    assert loop.interval == 5


def test_client_install_cli_registers_manifest_and_sends_initial_heartbeat(dashboard_url, tmp_path):
    from capability_mesh.cli import build_parser, cmd_client_install
    from capability_mesh.client import CapabilityMeshClient

    config_dir = tmp_path / "client-home"
    args = build_parser().parse_args(
        [
            "client",
            "--url",
            dashboard_url,
            "install",
            "--yes",
            "--node-id",
            "trial-client",
            "--display-name",
            "Trial Client",
            "--task-type",
            "smoke",
            "--tool",
            "python",
            "--allow-auto-accept",
            "--once",
            "--config-dir",
            str(config_dir),
        ]
    )

    assert cmd_client_install(args) == 0

    manifest_path = config_dir / "trial-client.manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["node_id"] == "trial-client"
    assert manifest["privacy"]["expose_local_skills"] is False
    assert manifest["privacy"]["expose_memory"] is False
    assert manifest["policies"]["auto_accept_task_types"] == ["smoke"]
    node = CapabilityMeshClient(dashboard_url).get_node("trial-client")
    assert node["online_status"]["status"] == "online"


def test_install_client_script_dry_run_manifest_is_privacy_safe(capsys):
    from scripts import install_client

    rc = install_client.main(
        [
            "--yes",
            "--mesh-url",
            "http://127.0.0.1:8765",
            "--node-id",
            "dry-client",
            "--task-type",
            "smoke",
            "--tool",
            "python",
            "--dry-run",
        ]
    )

    assert rc == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["node_id"] == "dry-client"
    assert manifest["privacy"] == {
        "expose_local_skills": False,
        "expose_memory": False,
        "expose_session_history": False,
        "expose_reasoning_trace": False,
        "expose_raw_logs": False,
        "expose_environment": False,
    }
    rendered = json.dumps(manifest)
    assert "environment_variables" in rendered
    assert "private_memory" in rendered
    assert "SECRET" not in rendered


def test_install_client_config_dir_prefers_capability_env(monkeypatch, tmp_path):
    from scripts import install_client

    new_home = tmp_path / "new-client-home"
    configured_home = tmp_path / "configured-client-home"

    monkeypatch.delenv("CAPABILITY_MESH_CLIENT_HOME", raising=False)
    monkeypatch.delenv("CAPABILITY_MESH_CLIENT_HOME", raising=False)
    assert install_client._default_config_dir() == Path.home() / ".capability-mesh" / "client"

    monkeypatch.setenv("CAPABILITY_MESH_CLIENT_HOME", str(configured_home))
    assert install_client._default_config_dir() == configured_home

    monkeypatch.setenv("CAPABILITY_MESH_CLIENT_HOME", str(new_home))
    assert install_client._default_config_dir() == new_home
