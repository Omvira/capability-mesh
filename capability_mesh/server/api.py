"""HTTP API handler for the Capability Mesh Server."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from capability_mesh.core import (
    CapabilityMeshValidationError,
    build_a2a_list_tasks_response,
    build_a2a_stream_responses,
    build_a2a_task,
    build_agent_card,
    build_task_assignment,
    cancel_a2a_task,
    claim_task_assignment,
    complete_task_assignment,
    default_mesh_home,
    execute_plan_step,
    get_a2a_task,
    get_registered_node,
    handle_a2a_jsonrpc,
    list_node_assignments,
    list_a2a_tasks,
    list_posted_tasks,
    list_registered_nodes,
    list_task_assignments,
    list_task_results,
    list_task_push_notification_configs,
    plan_next_node_call,
    post_task,
    record_node_heartbeat,
    record_a2a_task,
    record_task_assignment,
    record_task_push_notification_config,
    record_task_result,
    register_node_manifest,
    route_task,
    validate_task_contract,
    wake_assignment,
)
from capability_mesh.server.public_projection import (
    build_dashboard_ui_projection,
    public_board,
    public_node_statuses,
    public_node_view,
    public_nodes,
    read_static_asset,
    render_ui_shell,
)
from capability_mesh.node.runtime_queue import DurableTaskRuntime
from capability_mesh.server.audit import record_audit_event
from capability_mesh.server.outbound import private_networks_allowed_for_server, validate_outbound_http_url
from capability_mesh.server.policy import is_action_allowed
from capability_mesh.server.push import deliver_push_notification




class DashboardHandler(BaseHTTPRequestHandler):
    mesh_home: Path | None = None
    auth_token: str | None = None
    runtime: DurableTaskRuntime | None = None
    relay_timeout_seconds: float = 10.0

    def _audit(self, action: str, status: str, body: Mapping[str, Any] | None = None) -> None:
        node_id = None
        if action.startswith("relay/nodes/"):
            node_id = action.removeprefix("relay/nodes/").split("/", 1)[0]
        record_audit_event(
            mesh_home=self.mesh_home,
            action=action,
            status=status,
            path=urlsplit(self.path).path,
            remote_addr=self.client_address[0] if self.client_address else None,
            node_id=node_id,
            headers={key: value for key, value in self.headers.items()},
            body=body,
        )

    def _authorize_mutation(self, action: str, body: Mapping[str, Any]) -> bool:
        if not is_action_allowed(action, mesh_home=self.mesh_home):
            self._audit(action, "policy_denied", body)
            self._send_json({"error": "policy denied"}, status=HTTPStatus.FORBIDDEN)
            return False
        if not self.auth_token:
            self._audit(action, "allowed", body)
            return True
        expected = f"Bearer {self.auth_token}"
        if hmac.compare_digest(self.headers.get("Authorization", ""), expected):
            self._audit(action, "allowed", body)
            return True
        self._audit(action, "denied", body)
        self._send_json({"error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
        return False

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            if path == "/health":
                self._send_json({"ok": True})
            elif path in {"/.well-known/agent-card.json", "/agent-card.json", "/api/agent-card"}:
                self._send_json(build_agent_card(server_url=self._server_base_url()), content_type="application/a2a+json; charset=utf-8")
            elif path == "/api/nodes":
                self._send_json(public_nodes(self.mesh_home))
            elif path == "/api/nodes/statuses":
                self._send_json(public_node_statuses(self.mesh_home))
            elif path == "/api/board":
                self._send_json(public_board(self.mesh_home))
            elif path == "/api/ui/dashboard":
                self._send_json(build_dashboard_ui_projection(self.mesh_home))
            elif path.startswith("/api/nodes/"):
                suffix = path.removeprefix("/api/nodes/")
                if suffix.endswith("/assignments"):
                    node_id = unquote(suffix.removesuffix("/assignments"))
                    get_registered_node(node_id, mesh_home=self.mesh_home)
                    self._send_json(list_node_assignments(node_id, mesh_home=self.mesh_home))
                else:
                    node_id = unquote(suffix)
                    node = get_registered_node(node_id, mesh_home=self.mesh_home)
                    self._send_json(public_node_view(node, mesh_home=self.mesh_home))
            elif path == "/api/tasks":
                self._send_json(list_posted_tasks(self.mesh_home))
            elif path == "/api/assignments":
                self._send_json(list_task_assignments(self.mesh_home))
            elif path == "/api/results":
                self._send_json(list_task_results(self.mesh_home))
            elif path == "/api/a2a/tasks":
                self._send_json(list_a2a_tasks(self.mesh_home))
            elif path == "/tasks":
                self._send_json(build_a2a_list_tasks_response(self.mesh_home), content_type="application/a2a+json; charset=utf-8")
            elif path.startswith("/relay/pull/nodes/"):
                node_id = unquote(path.removeprefix("/relay/pull/nodes/").strip("/"))
                self._send_json({"node_id": node_id, "messages": [], "binding": "custom-long-poll-placeholder"})
            elif path.startswith("/tasks/") and path.endswith("/push-notification-configs"):
                suffix = path.removeprefix("/tasks/").removesuffix("/push-notification-configs")
                task_id = unquote(suffix.rstrip("/"))
                self._send_json(list_task_push_notification_configs(task_id, mesh_home=self.mesh_home), content_type="application/a2a+json; charset=utf-8")
            elif path.startswith("/tasks/"):
                task_id = unquote(path.removeprefix("/tasks/"))
                self._send_json(get_a2a_task(task_id, mesh_home=self.mesh_home), content_type="application/a2a+json; charset=utf-8")
            elif path == "/":
                self._send_html(render_ui_shell(self.mesh_home))
            elif path.startswith("/static/"):
                self._send_static(path.removeprefix("/static/"))
            else:
                self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
        except CapabilityMeshValidationError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            data = self._read_json_body()
            action = path.strip("/") or "root"
            if not self._authorize_mutation(action, data):
                return
            if path.startswith("/relay/nodes/"):
                self._proxy_relay(path, data)
            elif path == "/api/nodes":
                saved = register_node_manifest(data, mesh_home=self.mesh_home)
                self._send_json({"ok": True, "path": str(saved), "node_id": data.get("node_id")})
            elif path == "/a2a/jsonrpc":
                self._send_json(handle_a2a_jsonrpc(data, mesh_home=self.mesh_home), content_type="application/json; charset=utf-8")
            elif path in {"/message:send", "/api/a2a/messages", "/api/a2a/tasks/send"}:
                message = data.get("message", data)
                if self._is_async_message(message):
                    task = self._submit_async_message(message)
                    self._send_json(task, status=HTTPStatus.ACCEPTED, content_type="application/a2a+json; charset=utf-8")
                else:
                    task = build_a2a_task(message)
                    record_a2a_task(task, mesh_home=self.mesh_home)
                    self._send_json(task, content_type="application/a2a+json; charset=utf-8")
            elif path.startswith("/tasks/") and path.endswith(":cancel"):
                task_id = unquote(path.removeprefix("/tasks/").removesuffix(":cancel"))
                task = cancel_a2a_task(task_id, mesh_home=self.mesh_home)
                self._send_json(task, content_type="application/a2a+json; charset=utf-8")
            elif path.startswith("/tasks/") and path.endswith("/push-notification-configs"):
                suffix = path.removeprefix("/tasks/").removesuffix("/push-notification-configs")
                task_id = unquote(suffix.rstrip("/"))
                self._validate_outbound_url(str(data.get("url") or ""))
                record_task_push_notification_config(task_id, data, mesh_home=self.mesh_home)
                config = list_task_push_notification_configs(task_id, mesh_home=self.mesh_home)["configs"][-1]
                self._send_json(config, content_type="application/a2a+json; charset=utf-8")
                self._deliver_existing_completed_task_push(task_id, config)
            elif path == "/message:stream":
                message = data.get("message", data)
                events = build_a2a_stream_responses(message)
                self._send_sse(events)
            elif path.startswith("/api/nodes/") and path.endswith("/heartbeat"):
                node_id = unquote(path.removeprefix("/api/nodes/").removesuffix("/heartbeat"))
                status = data.get("status", "online")
                if not isinstance(status, str):
                    raise CapabilityMeshValidationError("status must be a string")
                record_node_heartbeat(node_id, status, mesh_home=self.mesh_home)
                node = get_registered_node(node_id, mesh_home=self.mesh_home)
                self._send_json({"ok": True, "node": public_node_view(node, mesh_home=self.mesh_home)})
            elif path == "/api/tasks":
                saved = post_task(data, mesh_home=self.mesh_home)
                self._send_json({"ok": True, "path": str(saved), "task_id": data.get("task_id")})
            elif path == "/api/tasks/route":
                task = data.get("task", data)
                required_tool = data.get("required_tool")
                required_tools = data.get("required_tools")
                if required_tool and not required_tools:
                    required_tools = [required_tool]
                validated_task = validate_task_contract(task)
                route = route_task(validated_task, list_registered_nodes(self.mesh_home), required_tools=required_tools)
                response: dict[str, Any] = {"route": route}
                if route.get("selected_node"):
                    assignment = build_task_assignment(validated_task, route)
                    saved = record_task_assignment(assignment, mesh_home=self.mesh_home)
                    response["assignment"] = assignment
                    response["path"] = str(saved)
                self._send_json(response)
            elif path == "/api/tasks/plan":
                task = data.get("task", data)
                subtask = data.get("subtask")
                validated_task = validate_task_contract(task)
                plan = plan_next_node_call(
                    validated_task,
                    list_registered_nodes(self.mesh_home),
                    subtask=subtask,
                )
                response = {"plan": plan}
                if plan.get("action") == "invoke_node":
                    assignment = plan["assignment"]
                    saved = record_task_assignment(assignment, mesh_home=self.mesh_home)
                    response["tool_call"] = plan["tool_call"]
                    response["assignment"] = assignment
                    response["path"] = str(saved)
                self._send_json(response)
            elif path == "/api/tasks/plan-step":
                task = data.get("task", data)
                requested_step = data.get("requested_step") or data.get("step")
                validated_task = validate_task_contract(task)
                plan = execute_plan_step(
                    validated_task,
                    list_registered_nodes(self.mesh_home),
                    requested_step=requested_step,
                    mesh_home=self.mesh_home,
                )
                response = {"plan": plan}
                if plan.get("action") in {"invoke_server_tool", "invoke_node"}:
                    response["tool_call"] = plan.get("tool_call")
                if plan.get("assignment") is not None:
                    response["assignment"] = plan["assignment"]
                if plan.get("result_record") is not None:
                    response["result_record"] = plan["result_record"]
                self._send_json(response)
            elif path == "/api/assignments":
                saved = record_task_assignment(data, mesh_home=self.mesh_home)
                self._send_json({"ok": True, "path": str(saved), "assignment_id": data.get("assignment_id")})
            elif path.startswith("/api/assignments/") and path.endswith("/claim"):
                assignment_id = unquote(path.removeprefix("/api/assignments/").removesuffix("/claim"))
                node_id = data.get("node_id")
                if not isinstance(node_id, str) or not node_id.strip():
                    raise CapabilityMeshValidationError("node_id is required")
                assignment = claim_task_assignment(assignment_id, node_id, mesh_home=self.mesh_home)
                self._send_json({"ok": True, "assignment": assignment})
            elif path.startswith("/api/assignments/") and path.endswith("/complete"):
                assignment_id = unquote(path.removeprefix("/api/assignments/").removesuffix("/complete"))
                node_id = data.get("node_id")
                if not isinstance(node_id, str) or not node_id.strip():
                    raise CapabilityMeshValidationError("node_id is required")
                completion = complete_task_assignment(
                    assignment_id,
                    node_id,
                    data.get("result", data),
                    mesh_home=self.mesh_home,
                )
                self._send_json({"ok": True, **completion})
            elif path.startswith("/api/assignments/") and path.endswith("/wake"):
                assignment_id = unquote(path.removeprefix("/api/assignments/").removesuffix("/wake"))
                wake = wake_assignment(
                    assignment_id,
                    server_url=self._server_base_url(),
                    mesh_home=self.mesh_home,
                )
                self._send_json({"ok": wake.get("status") in {"sent", "unsupported"}, "wake": wake})
            elif path == "/api/results":
                task = data.get("task") or data.get("contract")
                result = data.get("result", data)
                if task is None:
                    raise CapabilityMeshValidationError("task or contract is required")
                saved = record_task_result(result, task, mesh_home=self.mesh_home)
                self._send_json({"ok": True, "path": str(saved)})
            else:
                self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
        except (CapabilityMeshValidationError, json.JSONDecodeError) as exc:
            if not getattr(self, "_headers_started", False):
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)


    def _is_async_message(self, message: Any) -> bool:
        if not isinstance(message, Mapping):
            return False
        metadata = message.get("metadata")
        if not isinstance(metadata, Mapping):
            return False
        mesh_meta = metadata.get("capabilityMesh")
        return isinstance(mesh_meta, Mapping) and bool(mesh_meta.get("async"))

    def _async_delay(self, message: Mapping[str, Any]) -> float:
        metadata = message.get("metadata")
        if isinstance(metadata, Mapping):
            mesh_meta = metadata.get("capabilityMesh")
            if isinstance(mesh_meta, Mapping):
                try:
                    return max(0.0, min(float(mesh_meta.get("delaySeconds", 0.1)), 60.0))
                except (TypeError, ValueError):
                    return 0.1
        return 0.1

    def _submit_async_message(self, message: Mapping[str, Any]) -> dict[str, Any]:
        envelope = build_a2a_task(message)
        task = dict(envelope["task"])
        task["status"] = {
            "state": "TASK_STATE_WORKING",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "message": {
                "messageId": f"{task['id']}-working",
                "role": "ROLE_AGENT",
                "contextId": task.get("contextId", task["id"]),
                "taskId": task["id"],
                "parts": [{"text": "task accepted for asynchronous execution"}],
            },
        }
        record_a2a_task({"task": task}, mesh_home=self.mesh_home)
        delay = self._async_delay(message)
        if self.runtime is not None:
            self.runtime.submit(lambda: self._complete_async_task(task["id"], envelope["task"], delay), task_id=f"a2a-{task['id']}")
        else:
            self._complete_async_task(task["id"], envelope["task"], delay)
        return {"task": task}

    def _complete_async_task(self, task_id: str, completed_task: Mapping[str, Any], delay: float) -> None:
        time.sleep(delay)
        task = dict(completed_task)
        record_a2a_task({"task": task}, mesh_home=self.mesh_home)
        self._deliver_push_notifications(task_id, task)

    def _deliver_push_notifications(self, task_id: str, task: Mapping[str, Any]) -> None:
        try:
            configs = list_task_push_notification_configs(task_id, mesh_home=self.mesh_home).get("configs", [])
        except CapabilityMeshValidationError:
            return
        for config in configs:
            if not isinstance(config, Mapping) or not isinstance(config.get("url"), str):
                continue
            bearer_token = None
            auth = config.get("authentication")
            if isinstance(auth, Mapping) and auth.get("scheme") == "bearer" and auth.get("credentials"):
                bearer_token = str(auth["credentials"])
            deliver_push_notification(task_id=task_id, task=task, config=config, mesh_home=self.mesh_home, bearer_token=bearer_token, timeout=5, max_attempts=2, allow_private_networks=self._private_outbound_allowed())

    def _deliver_existing_completed_task_push(self, task_id: str, config: Mapping[str, Any]) -> None:
        try:
            response = get_a2a_task(task_id, mesh_home=self.mesh_home)
        except CapabilityMeshValidationError:
            return
        task: Mapping[str, Any] | None = None
        if isinstance(response, Mapping):
            task_candidate = response.get("task", response)
            if isinstance(task_candidate, Mapping):
                task = task_candidate
        if task is not None and isinstance(task.get("status"), Mapping) and task["status"].get("state") == "TASK_STATE_COMPLETED":
            self._deliver_push_notifications(task_id, task)

    def _private_outbound_allowed(self) -> bool:
        host = str(self.server.server_address[0])
        return private_networks_allowed_for_server(host)

    def _validate_outbound_url(self, url: str) -> str:
        try:
            return validate_outbound_http_url(url, allow_private_networks=self._private_outbound_allowed())
        except CapabilityMeshValidationError as exc:
            self._send_json({"error": "outbound target denied", "detail": str(exc)}, status=HTTPStatus.FORBIDDEN)
            raise

    def _proxy_relay(self, path: str, data: Mapping[str, Any]) -> None:
        suffix = path.removeprefix("/relay/nodes/")
        node_id, separator, tail = suffix.partition("/a2a")
        if not node_id or not separator or not re.fullmatch(r"[A-Za-z0-9_.-]+", node_id):
            raise CapabilityMeshValidationError("relay path must be /relay/nodes/{node_id}/a2a/{operation}")
        if tail not in {"/message:send", "/message:stream"} and not (tail.startswith("/tasks/") and (tail.endswith(":cancel") or tail == "")):
            raise CapabilityMeshValidationError("relay operation is not allowed")
        from capability_mesh.hub.registry import list_agent_cards

        cards = list_agent_cards(mesh_home=self.mesh_home)
        target_url = ""
        marker = f"/relay/nodes/{node_id}/a2a"
        for card in cards:
            card_matches_node = False
            for skill in card.get("skills", []):
                if isinstance(skill, Mapping) and str(skill.get("id", "")).startswith(f"{node_id}-"):
                    card_matches_node = True
                    break
            for interface in card.get("supportedInterfaces", []):
                if isinstance(interface, Mapping):
                    url = str(interface.get("url", "")).rstrip("/")
                    if marker in url:
                        target_url = url.split(marker, 1)[0].rstrip("/")
                        break
                    if card_matches_node and url:
                        target_url = url
                        break
            if target_url:
                break
        if not target_url:
            raise CapabilityMeshValidationError(f"unknown relay node: {node_id}")
        target_url = self._validate_outbound_url(target_url)
        endpoint = target_url + (tail or "/")
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/a2a+json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.relay_timeout_seconds) as resp:
                payload = resp.read()
                content_type = resp.headers.get("Content-Type", "application/json; charset=utf-8")
                self.send_response(resp.status)
                self._send_common_headers(content_type, len(payload))
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            if exc.code >= 500 and not payload:
                self._send_json({"error": "relay target unavailable"}, status=HTTPStatus.BAD_GATEWAY)
                return
            self.send_response(exc.code)
            self._send_common_headers(exc.headers.get("Content-Type", "application/json; charset=utf-8"), len(payload))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError):
                raise
            self._send_json({"error": "relay target unavailable"}, status=HTTPStatus.BAD_GATEWAY)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise CapabilityMeshValidationError("JSON request body must be an object")
        return data

    def _server_base_url(self) -> str:
        host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
        scheme = self.headers.get("X-Forwarded-Proto", "http")
        return f"{scheme}://{host}"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK, *, content_type: str = "application/json; charset=utf-8") -> None:
        self._headers_started = True
        body = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self._send_common_headers(content_type, len(body))
        self.end_headers()
        self.wfile.write(body)

    def _send_common_headers(self, content_type: str, content_length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")

    def _send_sse(self, events: list[dict[str, Any]]) -> None:
        payload = "".join(f"event: message\ndata: {json.dumps(event, ensure_ascii=False, sort_keys=True)}\n\n" for event in events)
        body = payload.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self._send_common_headers("text/event-stream; charset=utf-8", len(body))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self._send_common_headers("text/html; charset=utf-8", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, name: str) -> None:
        try:
            body, content_type = read_static_asset(name)
        except FileNotFoundError:
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self._send_common_headers(content_type, len(body))
        self.end_headers()
        self.wfile.write(body)


def make_server(host: str = "127.0.0.1", port: int = 8765, mesh_home: str | Path | None = None, auth_token: str | None = None) -> ThreadingHTTPServer:
    class Handler(DashboardHandler):
        pass

    Handler.mesh_home = Path(mesh_home).expanduser() if mesh_home is not None else default_mesh_home()
    Handler.auth_token = auth_token or os.environ.get("CAPABILITY_MESH_AUTH_TOKEN")
    Handler.runtime = DurableTaskRuntime(Handler.mesh_home, max_workers=2)

    class CapabilityMeshHTTPServer(ThreadingHTTPServer):
        def server_close(self) -> None:
            runtime = Handler.runtime
            if runtime is not None:
                runtime.shutdown(wait_for_tasks=True)
                Handler.runtime = None
            super().server_close()

    return CapabilityMeshHTTPServer((host, port), Handler)


def serve_dashboard(host: str = "127.0.0.1", port: int = 8765, mesh_home: str | Path | None = None, auth_token: str | None = None) -> None:
    server = make_server(host=host, port=port, mesh_home=mesh_home, auth_token=auth_token)
    try:
        print(f"Capability Mesh dashboard listening on http://{host}:{server.server_port}")
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the read-only Capability Mesh dashboard.")
    parser.add_argument("--mesh-home", default=None, help="Mesh registry home; defaults to $CAPABILITY_MESH_HOME, ~/.capability-mesh")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--auth-token", default=None)
    args = parser.parse_args(argv)
    serve_dashboard(host=args.host, port=args.port, mesh_home=args.mesh_home, auth_token=args.auth_token)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
