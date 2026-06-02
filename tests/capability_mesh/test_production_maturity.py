"""Focused production maturity tests for durable runtime, policy, audit, relay, and client helpers."""

from __future__ import annotations

import json
import threading
import time
from http import HTTPStatus
from http.client import HTTPConnection, HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml
from a2a import types as a2a_types
from google.protobuf.json_format import ParseDict


def _json_response(response: HTTPResponse) -> dict[str, Any]:
    body = response.read().decode("utf-8")
    parsed = json.loads(body)
    assert isinstance(parsed, dict)
    return parsed


def _start_server(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _stop(server: ThreadingHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _start_hub(tmp_path: Path, *, auth_token: str | None = None) -> tuple[ThreadingHTTPServer, threading.Thread, int]:
    from capability_mesh.server.api import make_server

    server = make_server(host="127.0.0.1", port=0, mesh_home=tmp_path, auth_token=auth_token)
    thread = _start_server(server)
    return server, thread, int(server.server_address[1])


def _send_async_message(port: int, text: str = "async") -> dict[str, Any]:
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    payload = json.dumps(
        {
            "message": {
                "role": "ROLE_USER",
                "parts": [{"text": text}],
                "metadata": {"capabilityMesh": {"async": True, "delaySeconds": 0.01}},
            }
        }
    )
    conn.request("POST", "/message:send", body=payload, headers={"Content-Type": "application/a2a+json"})
    response = conn.getresponse()
    assert response.status == HTTPStatus.ACCEPTED
    return _json_response(response)


class _FlakyPushHook(BaseHTTPRequestHandler):
    attempts: list[dict[str, Any]] = []
    fail_count = 1

    def do_POST(self) -> None:  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8")
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)
        self.__class__.attempts.append({"authorization": self.headers.get("Authorization"), "task": parsed})
        if len(self.__class__.attempts) <= self.__class__.fail_count:
            self.send_response(500)
        else:
            self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


def test_durable_async_runtime_records_lifecycle_and_failure(tmp_path: Path) -> None:
    from capability_mesh.node.runtime_queue import DurableTaskRuntime

    runtime = DurableTaskRuntime(tmp_path, max_workers=1)
    task_id = runtime.submit(lambda: {"ok": True})
    runtime.drain(timeout=5)
    record = runtime.get_record(task_id)
    assert record["state"] == "completed"
    assert record["attempts"] == 1
    assert record["completed_at"]
    assert record["transitions"][0]["state"] == "queued"

    failed_id = runtime.submit(lambda: (_ for _ in ()).throw(RuntimeError("boom secret-token")))
    runtime.drain(timeout=5)
    failed = runtime.get_record(failed_id)
    assert failed["state"] == "failed"
    assert failed["attempts"] == 1
    assert "boom" in failed["error"]
    assert "secret-token" not in failed["error"]
    assert (tmp_path / "runtime" / "tasks" / f"{failed_id}.json").exists()

    recovered = DurableTaskRuntime(tmp_path, autostart=False)
    assert recovered.get_record(failed_id)["state"] == "failed"


def test_push_delivery_retries_persists_status_and_redacts_bearer(tmp_path: Path) -> None:
    from capability_mesh.server.push import list_push_delivery_records

    _FlakyPushHook.attempts = []
    _FlakyPushHook.fail_count = 1
    hook = ThreadingHTTPServer(("127.0.0.1", 0), _FlakyPushHook)
    hook_thread = _start_server(hook)
    hub, hub_thread, hub_port = _start_hub(tmp_path)
    try:
        submitted = _send_async_message(hub_port, "push retry")
        task_id = submitted["task"]["id"]
        conn = HTTPConnection("127.0.0.1", hub_port, timeout=10)
        config = json.dumps(
            {
                "id": "flaky",
                "url": f"http://127.0.0.1:{int(hook.server_address[1])}/push",
                "authentication": {"scheme": "bearer", "credentials": "secret-webhook-token"},
            }
        )
        conn.request("POST", f"/tasks/{task_id}/push-notification-configs", body=config, headers={"Content-Type": "application/a2a+json"})
        assert conn.getresponse().status == 200

        deadline = time.time() + 5
        records: list[dict[str, Any]] = []
        while time.time() < deadline:
            records = list_push_delivery_records(task_id, mesh_home=tmp_path)
            if records and records[-1]["status"] == "delivered" and records[-1]["attempts"] == 2:
                break
            time.sleep(0.05)
        assert records[-1]["status"] == "delivered"
        assert records[-1]["attempt_records"][0]["status"] == "failed"
        assert records[-1]["attempt_records"][1]["status"] == "delivered"
        persisted = (tmp_path / "push-deliveries" / f"{task_id}-flaky.json").read_text(encoding="utf-8")
        assert "secret-webhook-token" not in persisted
        assert "[REDACTED]" in persisted
        assert _FlakyPushHook.attempts[-1]["authorization"] == "Bearer secret-webhook-token"
    finally:
        _stop(hub, hub_thread)
        _stop(hook, hook_thread)


def test_policy_file_denies_and_allows_mutating_actions(tmp_path: Path) -> None:
    (tmp_path / "policy.yaml").write_text(yaml.safe_dump({"default": "deny", "allow": ["message:send"]}), encoding="utf-8")
    hub, thread, port = _start_hub(tmp_path)
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        body = json.dumps({"message": {"role": "ROLE_USER", "parts": [{"text": "allowed"}]}})
        conn.request("POST", "/message:send", body=body, headers={"Content-Type": "application/a2a+json"})
        assert conn.getresponse().status == 200
        conn.request("POST", "/relay/nodes/missing/a2a/message:send", body=body, headers={"Content-Type": "application/a2a+json"})
        denied = conn.getresponse()
        assert denied.status == HTTPStatus.FORBIDDEN
        assert _json_response(denied)["error"] == "policy denied"
    finally:
        _stop(hub, thread)


def test_structured_audit_redacts_secret_headers_and_body(tmp_path: Path) -> None:
    hub, thread, port = _start_hub(tmp_path, auth_token="hub-secret")
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        body = json.dumps({"message": {"role": "ROLE_USER", "parts": [{"text": "hello"}], "metadata": {"api_token": "body-secret"}}})
        conn.request("POST", "/message:send", body=body, headers={"Content-Type": "application/a2a+json", "Authorization": "Bearer hub-secret"})
        assert conn.getresponse().status == 200
        audit_lines = (tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()
        event = json.loads(audit_lines[-1])
        assert {"timestamp", "action", "status", "path", "remote_addr"}.issubset(event)
        audit_text = json.dumps(event)
        assert "hub-secret" not in audit_text
        assert "body-secret" not in audit_text
        assert "[REDACTED]" in audit_text
    finally:
        _stop(hub, thread)


def test_relay_target_unavailable_maps_to_gateway_error(tmp_path: Path) -> None:
    from capability_mesh.core import build_default_capability_manifest
    from capability_mesh.hub.registry import register_node_agent_card

    manifest = build_default_capability_manifest(node_id="node-down", display_name="Down", task_types=["echo"], tools_available=["python"])
    register_node_agent_card(manifest, hub_url="http://127.0.0.1:9", mesh_home=tmp_path)
    hub, thread, port = _start_hub(tmp_path)
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        body = json.dumps({"message": {"role": "ROLE_USER", "parts": [{"text": "relay"}]}})
        conn.request("POST", "/relay/nodes/node-down/a2a/message:send", body=body, headers={"Content-Type": "application/a2a+json"})
        response = conn.getresponse()
        assert response.status == HTTPStatus.BAD_GATEWAY
        assert _json_response(response)["error"] == "relay target unavailable"
    finally:
        _stop(hub, thread)


def test_node_a2a_client_calls_supported_interface_url(tmp_path: Path) -> None:
    from capability_mesh.core import build_default_capability_manifest
    from capability_mesh.node.client import NodeA2AClient
    from capability_mesh.node.runtime import make_node_server

    manifest = build_default_capability_manifest(node_id="node-b", display_name="Node B", task_types=["echo"], tools_available=["python"])
    node = make_node_server(manifest, host="127.0.0.1", port=0, mesh_home=tmp_path)
    thread = _start_server(node)
    try:
        base_url = f"http://127.0.0.1:{int(node.server_address[1])}"
        card = json.loads(json.dumps(NodeA2AClient.fetch_agent_card(base_url)))
        response = NodeA2AClient(card).send_message({"role": "ROLE_USER", "parts": [{"text": "client call"}]})
        ParseDict(response, a2a_types.SendMessageResponse())
        assert response["task"]["history"][0]["parts"][0]["text"] == "client call"
    finally:
        _stop(node, thread)


def test_grpc_binding_helpers_emit_sdk_validated_json(tmp_path: Path) -> None:
    from capability_mesh.grpc.binding import send_message_json

    parsed = json.loads(send_message_json({"role": "ROLE_USER", "parts": [{"text": "grpc"}]}, mesh_home=str(tmp_path)))
    ParseDict(parsed, a2a_types.SendMessageResponse())
    assert parsed["task"]["history"][0]["parts"][0]["text"] == "grpc"
