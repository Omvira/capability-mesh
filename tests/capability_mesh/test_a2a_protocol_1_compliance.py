"""A2A Protocol 1.0 compliance tests using the official a2a-sdk models."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

from google.protobuf.json_format import ParseDict
from a2a import types as a2a_types


def _manifest() -> dict[str, Any]:
    from capability_mesh import build_default_capability_manifest

    return build_default_capability_manifest(
        node_id="alpha-node",
        display_name="Alpha",
        task_types=["code_review"],
        tools_available=["python"],
    )


def test_hub_agent_card_validates_against_official_a2a_sdk_model() -> None:
    from capability_mesh.core import build_hub_agent_card

    card = build_hub_agent_card(hub_url="https://mesh.example.com/a2a")
    parsed = ParseDict(card, a2a_types.AgentCard())

    assert parsed.name == "Capability Mesh Hub"
    assert parsed.supported_interfaces[0].url == "https://mesh.example.com/a2a"
    assert parsed.supported_interfaces[0].protocol_binding == "https://a2a-protocol.org/bindings/http-json/v1"
    assert parsed.supported_interfaces[0].protocol_version == "1.0"
    assert parsed.default_input_modes
    assert parsed.default_output_modes
    assert parsed.skills


def test_node_agent_card_validates_against_official_a2a_sdk_model_and_uses_relay_base_url() -> None:
    from capability_mesh.node.a2a import build_node_agent_card

    card = build_node_agent_card(_manifest(), relay_url="https://mesh.example.com/relay/nodes/alpha-node/a2a")
    parsed = ParseDict(card, a2a_types.AgentCard())

    assert parsed.name == "Alpha Node"
    assert parsed.supported_interfaces[0].url == "https://mesh.example.com/relay/nodes/alpha-node/a2a"
    assert parsed.supported_interfaces[0].protocol_binding == "https://a2a-protocol.org/bindings/http-json/v1"
    assert parsed.capabilities.streaming is False
    assert any(skill.name == "code_review" for skill in parsed.skills)
    assert "preferredTransport" not in card
    assert "additionalInterfaces" not in card


def test_a2a_message_and_task_validate_against_official_a2a_sdk_models() -> None:
    from capability_mesh.core import build_a2a_task, validate_a2a_message

    message = validate_a2a_message({"role": "user", "parts": [{"text": "hello"}]})
    parsed_message = ParseDict(message, a2a_types.Message())
    assert parsed_message.role == a2a_types.Role.ROLE_USER
    assert parsed_message.message_id

    envelope = build_a2a_task(message)
    parsed_response = ParseDict(envelope, a2a_types.SendMessageResponse())
    assert parsed_response.HasField("task")
    assert parsed_response.task.status.state == a2a_types.TaskState.TASK_STATE_COMPLETED
    assert parsed_response.task.artifacts[0].parts[0].text


def test_standard_http_json_a2a_task_endpoints(tmp_path: Path) -> None:
    from capability_mesh.server.api import make_server

    server = make_server(host="127.0.0.1", port=0, mesh_home=tmp_path)
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        payload = json.dumps({"message": {"role": "ROLE_USER", "parts": [{"text": "hello"}]}})
        conn.request("POST", "/message:send", body=payload, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        ParseDict(body, a2a_types.SendMessageResponse())
        task_id = body["task"]["id"]

        conn.request("GET", f"/tasks/{task_id}")
        response = conn.getresponse()
        task_body = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        ParseDict(task_body, a2a_types.Task())
        assert task_body["id"] == task_id

        conn.request("GET", "/tasks")
        response = conn.getresponse()
        tasks_body = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        parsed_list = ParseDict(tasks_body, a2a_types.ListTasksResponse())
        assert parsed_list.tasks

        conn.request("POST", f"/tasks/{task_id}:cancel", body="{}", headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        cancel_body = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        parsed_cancel = ParseDict(cancel_body, a2a_types.Task())
        assert parsed_cancel.status.state == a2a_types.TaskState.TASK_STATE_CANCELED
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
