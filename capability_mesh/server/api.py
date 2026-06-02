"""HTTP API handler for the Capability Mesh Server."""

from __future__ import annotations

import argparse
import json
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




class DashboardHandler(BaseHTTPRequestHandler):
    mesh_home: Path | None = None

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
            if path == "/api/nodes":
                saved = register_node_manifest(data, mesh_home=self.mesh_home)
                self._send_json({"ok": True, "path": str(saved), "node_id": data.get("node_id")})
            elif path == "/a2a/jsonrpc":
                self._send_json(handle_a2a_jsonrpc(data, mesh_home=self.mesh_home), content_type="application/json; charset=utf-8")
            elif path in {"/message:send", "/api/a2a/messages", "/api/a2a/tasks/send"}:
                message = data.get("message", data)
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
                record_task_push_notification_config(task_id, data, mesh_home=self.mesh_home)
                config = list_task_push_notification_configs(task_id, mesh_home=self.mesh_home)["configs"][-1]
                self._send_json(config, content_type="application/a2a+json; charset=utf-8")
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
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

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


def make_server(host: str = "127.0.0.1", port: int = 8765, mesh_home: str | Path | None = None) -> ThreadingHTTPServer:
    class Handler(DashboardHandler):
        pass

    Handler.mesh_home = Path(mesh_home).expanduser() if mesh_home is not None else default_mesh_home()
    return ThreadingHTTPServer((host, port), Handler)


def serve_dashboard(host: str = "127.0.0.1", port: int = 8765, mesh_home: str | Path | None = None) -> None:
    server = make_server(host=host, port=port, mesh_home=mesh_home)
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
    args = parser.parse_args(argv)
    serve_dashboard(host=args.host, port=args.port, mesh_home=args.mesh_home)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
