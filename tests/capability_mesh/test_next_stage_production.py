"""Next-stage production readiness tests for A2A runtime, relay, auth, and deployment."""

from __future__ import annotations

import json
import threading
import time
from http.client import HTTPConnection, HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from a2a import types as a2a_types
from google.protobuf.json_format import ParseDict


def _json_response(response: HTTPResponse) -> dict[str, Any]:
    body = response.read().decode("utf-8")
    parsed = json.loads(body)
    assert isinstance(parsed, dict)
    return parsed


def _start_hub(tmp_path: Path, *, auth_token: str | None = None):
    from capability_mesh.server.api import make_server

    server = make_server(host="127.0.0.1", port=0, mesh_home=tmp_path, auth_token=auth_token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, int(server.server_address[1])


def _stop(server: ThreadingHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


class _PushHook(BaseHTTPRequestHandler):
    events: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8")
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)
        self.__class__.events.append(parsed)
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


def _start_push_hook():
    _PushHook.events = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PushHook)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, int(server.server_address[1])


def test_async_long_running_task_runtime_completes_later_and_delivers_push_webhook(tmp_path: Path) -> None:
    hub, hub_thread, hub_port = _start_hub(tmp_path)
    hook, hook_thread, hook_port = _start_push_hook()
    try:
        conn = HTTPConnection("127.0.0.1", hub_port, timeout=10)
        payload = json.dumps({"message": {"role": "ROLE_USER", "parts": [{"text": "run async"}], "metadata": {"capabilityMesh": {"async": True, "delaySeconds": 0.05}}}})
        conn.request("POST", "/message:send", body=payload, headers={"Content-Type": "application/a2a+json"})
        response = conn.getresponse()
        assert response.status == 202
        submitted = _json_response(response)
        ParseDict(submitted, a2a_types.SendMessageResponse())
        task_id = submitted["task"]["id"]
        assert submitted["task"]["status"]["state"] in {"TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"}

        config_payload = json.dumps({"id": "hook", "url": f"http://127.0.0.1:{hook_port}/push"})
        conn.request("POST", f"/tasks/{task_id}/push-notification-configs", body=config_payload, headers={"Content-Type": "application/a2a+json"})
        assert conn.getresponse().status == 200

        deadline = time.time() + 5
        completed: dict[str, Any] | None = None
        while time.time() < deadline:
            conn.request("GET", f"/tasks/{task_id}")
            poll = conn.getresponse()
            assert poll.status == 200
            task = _json_response(poll)
            if task["status"]["state"] == "TASK_STATE_COMPLETED":
                completed = task
                break
            time.sleep(0.05)
        assert completed is not None
        ParseDict(completed, a2a_types.Task())
        assert _PushHook.events
        ParseDict(_PushHook.events[-1], a2a_types.Task())
        assert _PushHook.events[-1]["id"] == task_id
    finally:
        _stop(hub, hub_thread)
        _stop(hook, hook_thread)


def test_real_node_runtime_exposes_independent_a2a_server(tmp_path: Path) -> None:
    from capability_mesh.core import build_default_capability_manifest
    from capability_mesh.node.runtime import make_node_server

    manifest = build_default_capability_manifest(node_id="node-a", display_name="Node A", task_types=["echo"], tools_available=["python"])
    server = make_node_server(manifest, host="127.0.0.1", port=0, mesh_home=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = int(server.server_address[1])
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/.well-known/agent-card.json")
        response = conn.getresponse()
        assert response.status == 200
        card = _json_response(response)
        ParseDict(card, a2a_types.AgentCard())
        assert card["name"] == "Node A Node"

        conn.request("POST", "/message:send", body=json.dumps({"message": {"role": "ROLE_USER", "parts": [{"text": "hello node"}]}}), headers={"Content-Type": "application/a2a+json"})
        response = conn.getresponse()
        assert response.status == 200
        ParseDict(_json_response(response), a2a_types.SendMessageResponse())
    finally:
        _stop(server, thread)


def test_hub_relay_forwards_a2a_http_request_to_registered_node(tmp_path: Path) -> None:
    from capability_mesh.core import build_default_capability_manifest
    from capability_mesh.node.runtime import make_node_server
    from capability_mesh.hub.registry import register_node_agent_card

    node_manifest = build_default_capability_manifest(node_id="node-a", display_name="Node A", task_types=["echo"], tools_available=["python"])
    node = make_node_server(node_manifest, host="127.0.0.1", port=0, mesh_home=tmp_path / "node")
    node_thread = threading.Thread(target=node.serve_forever, daemon=True)
    node_thread.start()
    node_url = f"http://127.0.0.1:{int(node.server_address[1])}"
    register_node_agent_card(node_manifest, hub_url=node_url, mesh_home=tmp_path / "hub")
    hub, hub_thread, hub_port = _start_hub(tmp_path / "hub")
    try:
        conn = HTTPConnection("127.0.0.1", hub_port, timeout=10)
        conn.request("POST", "/relay/nodes/node-a/a2a/message:send", body=json.dumps({"message": {"role": "ROLE_USER", "parts": [{"text": "via relay"}]}}), headers={"Content-Type": "application/a2a+json"})
        response = conn.getresponse()
        assert response.status == 200
        relayed = _json_response(response)
        ParseDict(relayed, a2a_types.SendMessageResponse())
        assert relayed["task"]["history"][0]["parts"][0]["text"] == "via relay"
    finally:
        _stop(hub, hub_thread)
        _stop(node, node_thread)


def test_auth_policy_and_audit_log_guard_production_mutating_routes(tmp_path: Path) -> None:
    hub, thread, port = _start_hub(tmp_path, auth_token="secret-token")
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        body = json.dumps({"message": {"role": "ROLE_USER", "parts": [{"text": "blocked"}]}})
        conn.request("POST", "/message:send", body=body, headers={"Content-Type": "application/a2a+json"})
        unauthorized = conn.getresponse()
        assert unauthorized.status == 401
        unauthorized.read()

        conn.request("POST", "/message:send", body=body, headers={"Content-Type": "application/a2a+json", "Authorization": "Bearer secret-token"})
        allowed = conn.getresponse()
        assert allowed.status == 200
        ParseDict(_json_response(allowed), a2a_types.SendMessageResponse())

        audit_log = tmp_path / "audit.log"
        assert audit_log.exists()
        audit_text = audit_log.read_text(encoding="utf-8")
        assert "message:send" in audit_text
        assert "secret-token" not in audit_text
        assert "[REDACTED]" in audit_text
    finally:
        _stop(hub, thread)


def test_grpc_binding_and_tls_reverse_proxy_deployment_artifacts_exist() -> None:
    root = Path(__file__).resolve().parents[2]
    assert (root / "capability_mesh" / "grpc" / "a2a.proto").exists()
    assert (root / "capability_mesh" / "grpc" / "binding.py").exists()
    assert (root / "deploy" / "nginx-capability-mesh.conf").exists()
    assert (root / "deploy" / "capability-mesh.service").exists()
    assert (root / "deploy" / "docker-compose.yml").exists()
