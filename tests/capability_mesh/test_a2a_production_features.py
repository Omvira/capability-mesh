"""Production A2A Protocol 1.0 feature tests."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection, HTTPResponse
from pathlib import Path
from typing import Any

from a2a import types as a2a_types
from google.protobuf.json_format import ParseDict


def _start_server(tmp_path: Path):
    from capability_mesh.server.api import make_server

    server = make_server(host="127.0.0.1", port=0, mesh_home=tmp_path)
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, port


def _json_response(response: HTTPResponse) -> dict[str, Any]:
    body = response.read().decode("utf-8")
    parsed = json.loads(body)
    assert isinstance(parsed, dict)
    return parsed


def _send_message(conn: HTTPConnection) -> dict[str, Any]:
    payload = json.dumps({"message": {"role": "ROLE_USER", "parts": [{"text": "hello production"}]}})
    conn.request("POST", "/message:send", body=payload, headers={"Content-Type": "application/a2a+json"})
    response = conn.getresponse()
    assert response.status == 200
    body = _json_response(response)
    ParseDict(body, a2a_types.SendMessageResponse())
    return body


def test_agent_card_advertises_production_a2a_capabilities(tmp_path: Path) -> None:
    server, thread, port = _start_server(tmp_path)
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/.well-known/agent-card.json")
        response = conn.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Type", "").startswith("application/a2a+json")
        assert response.getheader("X-Content-Type-Options") == "nosniff"
        assert response.getheader("Cache-Control") == "no-store"
        card = _json_response(response)
        parsed = ParseDict(card, a2a_types.AgentCard())
        assert parsed.capabilities.streaming is True
        assert parsed.capabilities.push_notifications is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_message_stream_returns_sse_stream_response_events_validated_by_sdk(tmp_path: Path) -> None:
    server, thread, port = _start_server(tmp_path)
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        payload = json.dumps({"message": {"role": "ROLE_USER", "parts": [{"text": "stream me"}]}})
        conn.request("POST", "/message:stream", body=payload, headers={"Content-Type": "application/a2a+json"})
        response = conn.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Type", "").startswith("text/event-stream")
        raw = response.read().decode("utf-8")
        data_lines = [line.removeprefix("data: ") for line in raw.splitlines() if line.startswith("data: ")]
        assert len(data_lines) >= 3
        parsed_events = [ParseDict(json.loads(line), a2a_types.StreamResponse()) for line in data_lines]
        assert parsed_events[0].HasField("task")
        assert any(event.HasField("status_update") for event in parsed_events)
        assert any(event.HasField("artifact_update") for event in parsed_events)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_push_notification_config_operations_validate_against_sdk(tmp_path: Path) -> None:
    server, thread, port = _start_server(tmp_path)
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        task_id = _send_message(conn)["task"]["id"]
        config_payload = json.dumps(
            {
                "id": "primary",
                "url": "https://hooks.example.com/a2a/tasks",
                "token": "opaque-token",
                "authentication": {"scheme": "bearer", "credentials": "opaque-token"},
            }
        )
        conn.request(
            "POST",
            f"/tasks/{task_id}/push-notification-configs",
            body=config_payload,
            headers={"Content-Type": "application/a2a+json"},
        )
        response = conn.getresponse()
        assert response.status == 200
        config = _json_response(response)
        parsed = ParseDict(config, a2a_types.TaskPushNotificationConfig())
        assert parsed.task_id == task_id
        assert parsed.id == "primary"

        conn.request("GET", f"/tasks/{task_id}/push-notification-configs")
        response = conn.getresponse()
        assert response.status == 200
        listing = _json_response(response)
        parsed_listing = ParseDict(listing, a2a_types.ListTaskPushNotificationConfigsResponse())
        assert parsed_listing.configs[0].task_id == task_id
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_jsonrpc_a2a_binding_supports_send_get_list_cancel(tmp_path: Path) -> None:
    server, thread, port = _start_server(tmp_path)
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        request = {
            "jsonrpc": "2.0",
            "id": "send-1",
            "method": "message/send",
            "params": {"message": {"role": "ROLE_USER", "parts": [{"text": "json rpc"}]}},
        }
        conn.request("POST", "/a2a/jsonrpc", body=json.dumps(request), headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        assert response.status == 200
        send_result = _json_response(response)
        assert send_result["jsonrpc"] == "2.0"
        assert send_result["id"] == "send-1"
        ParseDict(send_result["result"], a2a_types.SendMessageResponse())
        task_id = send_result["result"]["task"]["id"]

        for rpc_id, method, params, model in [
            ("get-1", "tasks/get", {"id": task_id}, a2a_types.Task()),
            ("list-1", "tasks/list", {}, a2a_types.ListTasksResponse()),
            ("cancel-1", "tasks/cancel", {"id": task_id}, a2a_types.Task()),
        ]:
            conn.request(
                "POST",
                "/a2a/jsonrpc",
                body=json.dumps({"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}),
                headers={"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            assert response.status == 200
            envelope = _json_response(response)
            assert envelope["id"] == rpc_id
            ParseDict(envelope["result"], model)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
