"""Additional regression tests for production hardening review findings."""

from __future__ import annotations

import json
import time
from http import HTTPStatus
from http.client import HTTPConnection
from pathlib import Path

from tests.capability_mesh.test_production_maturity import _json_response, _send_async_message, _stop


def _start_public_hub(tmp_path: Path):
    from capability_mesh.server.api import make_server
    from tests.capability_mesh.test_production_maturity import _start_server

    server = make_server(host="0.0.0.0", port=0, mesh_home=tmp_path)
    thread = _start_server(server)
    return server, thread, int(server.server_address[1])


def test_relay_rejects_malformed_paths_and_unsafe_targets(tmp_path: Path) -> None:
    from capability_mesh.core import build_default_capability_manifest
    from capability_mesh.hub.registry import register_node_agent_card

    manifest = build_default_capability_manifest(node_id="node-unsafe", display_name="Unsafe", task_types=["echo"], tools_available=["python"])
    register_node_agent_card(manifest, hub_url="http://127.0.0.1:9", mesh_home=tmp_path)
    hub, thread, port = _start_public_hub(tmp_path)
    try:
        body = json.dumps({"message": {"role": "ROLE_USER", "parts": [{"text": "relay"}]}})
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("POST", "/relay/nodes/node-unsafe/not-a2a/message:send", body=body, headers={"Content-Type": "application/a2a+json"})
        malformed = conn.getresponse()
        assert malformed.status == HTTPStatus.BAD_REQUEST

        conn.request("POST", "/relay/nodes/node-unsafe/a2a/message:send", body=body, headers={"Content-Type": "application/a2a+json"})
        unsafe = conn.getresponse()
        assert unsafe.status == HTTPStatus.FORBIDDEN
        assert _json_response(unsafe)["error"] == "outbound target denied"
    finally:
        _stop(hub, thread)


def test_push_rejects_unsafe_private_webhook_url_on_public_hub(tmp_path: Path) -> None:
    hub, thread, port = _start_public_hub(tmp_path)
    try:
        submitted = _send_async_message(port, "unsafe push")
        task_id = submitted["task"]["id"]
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        config = json.dumps({"id": "unsafe", "url": "http://127.0.0.1:9/push"})
        conn.request("POST", f"/tasks/{task_id}/push-notification-configs", body=config, headers={"Content-Type": "application/a2a+json"})
        response = conn.getresponse()
        assert response.status == HTTPStatus.FORBIDDEN
        assert _json_response(response)["error"] == "outbound target denied"
    finally:
        _stop(hub, thread)


def test_push_config_registered_after_async_completion_delivers_existing_completed_task(tmp_path: Path) -> None:
    from http.server import ThreadingHTTPServer

    from tests.capability_mesh.test_production_maturity import _FlakyPushHook, _start_hub, _start_server

    _FlakyPushHook.attempts = []
    _FlakyPushHook.fail_count = 0
    hook = ThreadingHTTPServer(("127.0.0.1", 0), _FlakyPushHook)
    hook_thread = _start_server(hook)

    hub, hub_thread, hub_port = _start_hub(tmp_path)
    try:
        submitted = _send_async_message(hub_port, "late push")
        task_id = submitted["task"]["id"]
        deadline = time.time() + 5
        while time.time() < deadline:
            conn = HTTPConnection("127.0.0.1", hub_port, timeout=10)
            conn.request("GET", f"/tasks/{task_id}")
            task_response = _json_response(conn.getresponse())
            task = task_response.get("task", task_response)
            if isinstance(task, dict) and task.get("status", {}).get("state") == "TASK_STATE_COMPLETED":
                break
            time.sleep(0.05)

        conn = HTTPConnection("127.0.0.1", hub_port, timeout=10)
        config = json.dumps({"id": "late", "url": f"http://127.0.0.1:{int(hook.server_address[1])}/push"})
        conn.request("POST", f"/tasks/{task_id}/push-notification-configs", body=config, headers={"Content-Type": "application/a2a+json"})
        assert conn.getresponse().status == 200

        deadline = time.time() + 5
        while time.time() < deadline and not _FlakyPushHook.attempts:
            time.sleep(0.05)
        assert _FlakyPushHook.attempts
        assert _FlakyPushHook.attempts[-1]["task"]["id"] == task_id
    finally:
        _stop(hub, hub_thread)
        _stop(hook, hook_thread)


def test_runtime_marks_stale_running_records_after_restart(tmp_path: Path) -> None:
    from capability_mesh.node.runtime_queue import DurableTaskRuntime

    runtime = DurableTaskRuntime(tmp_path)
    task_id = runtime.submit(lambda: time.sleep(0.2))
    deadline = time.time() + 5
    while time.time() < deadline:
        if runtime.get_record(task_id)["state"] == "running":
            break
        time.sleep(0.01)
    runtime.shutdown(wait_for_tasks=False)

    recovered = DurableTaskRuntime(tmp_path, autostart=False, recover_stale=True)
    record = recovered.get_record(task_id)
    assert record["state"] == "failed"
    assert "interrupted" in record["error"]


def test_runtime_record_writes_are_atomic_for_readers(tmp_path: Path, monkeypatch) -> None:
    from capability_mesh.node import runtime_queue
    from capability_mesh.node.runtime_queue import DurableTaskRuntime

    observed_final_path_write = False
    original_write_text = Path.write_text

    def tracing_write_text(self: Path, *args, **kwargs):
        nonlocal observed_final_path_write
        if self.name == "runtime-atomic.json":
            observed_final_path_write = True
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(runtime_queue.Path, "write_text", tracing_write_text)
    runtime = DurableTaskRuntime(tmp_path, autostart=False)
    runtime._write_record("runtime-atomic", {"id": "runtime-atomic", "state": "queued"})

    assert not observed_final_path_write
    assert runtime.get_record("runtime-atomic")["state"] == "queued"
