"""Read-only stdlib dashboard for registered Capability Mesh nodes."""

from __future__ import annotations

import argparse
import html
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from hermes_mesh.core import (
    CapabilityMeshValidationError,
    build_task_assignment,
    default_mesh_home,
    get_registered_node,
    list_posted_tasks,
    list_registered_nodes,
    list_task_assignments,
    list_task_results,
    post_task,
    record_task_assignment,
    record_task_result,
    register_node_manifest,
    route_task,
    validate_task_contract,
)


def _resources_summary(resources: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in resources.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            summary[str(key)] = value
        elif isinstance(value, list):
            summary[str(key)] = f"{len(value)} item(s)"
        elif isinstance(value, dict):
            summary[str(key)] = f"{len(value)} field(s)"
        else:
            summary[str(key)] = type(value).__name__
    return summary


def public_node_view(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return the dashboard-safe subset of a validated manifest."""

    capabilities = manifest.get("capabilities", {})
    policies = manifest.get("policies", {})
    privacy = manifest.get("privacy", {})
    transport = manifest.get("transport", {})
    return {
        "node_id": manifest.get("node_id"),
        "display_name": manifest.get("display_name"),
        "task_types": list(capabilities.get("task_types", [])),
        "tools_available": list(capabilities.get("tools_available", [])),
        "resources": _resources_summary(capabilities.get("resources", {})),
        "policies": {
            "accepts_tasks": bool(policies.get("accepts_tasks", True)),
            "requires_human_approval": bool(policies.get("requires_human_approval", True)),
            "auto_accept_task_types": list(policies.get("auto_accept_task_types", [])),
        },
        "transport": {"type": transport.get("type", "local")},
        "privacy": {
            str(flag): "safe" if value is False else "exposed"
            for flag, value in sorted(privacy.items())
        },
    }


def public_nodes(mesh_home: str | Path | None = None) -> list[dict[str, Any]]:
    return [public_node_view(node) for node in list_registered_nodes(mesh_home=mesh_home)]


def _html_list(items: list[Any]) -> str:
    if not items:
        return '<span class="muted">none</span>'
    return "".join(f"<li>{html.escape(str(item))}</li>" for item in items)


def _html_kv(mapping: Mapping[str, Any]) -> str:
    if not mapping:
        return '<span class="muted">none declared</span>'
    return "".join(
        "<li>"
        f"<strong>{html.escape(str(key))}</strong>: {html.escape(str(value))}"
        "</li>"
        for key, value in mapping.items()
    )


def render_dashboard(nodes: list[dict[str, Any]]) -> str:
    cards = []
    for node in nodes:
        privacy_flags = "".join(
            "<li>"
            f"<span>{html.escape(flag)}</span>"
            f"<span class=\"flag flag-{html.escape(status)}\">{html.escape(status)}</span>"
            "</li>"
            for flag, status in node["privacy"].items()
        )
        cards.append(
            "<article class=\"card\">"
            f"<h2>{html.escape(str(node['display_name']))}</h2>"
            f"<p class=\"node-id\">{html.escape(str(node['node_id']))}</p>"
            "<div class=\"grid\">"
            f"<section><h3>Task Types</h3><ul>{_html_list(node['task_types'])}</ul></section>"
            f"<section><h3>Tools</h3><ul>{_html_list(node['tools_available'])}</ul></section>"
            f"<section><h3>Resources</h3><ul>{_html_kv(node['resources'])}</ul></section>"
            "<section><h3>Policies</h3><ul>"
            f"<li>accepts_tasks: {html.escape(str(node['policies']['accepts_tasks']).lower())}</li>"
            f"<li>requires_human_approval: {html.escape(str(node['policies']['requires_human_approval']).lower())}</li>"
            f"<li>auto_accept_task_types: {html.escape(', '.join(node['policies']['auto_accept_task_types']) or 'none')}</li>"
            "</ul></section>"
            f"<section><h3>Transport</h3><p>{html.escape(str(node['transport']['type']))}</p></section>"
            f"<section><h3>Privacy Flags</h3><ul class=\"privacy\">{privacy_flags}</ul></section>"
            "</div>"
            "</article>"
        )
    content = "".join(cards) or '<p class="empty">No registered Agents found.</p>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HermesMesh Dashboard</title>
  <style>
    :root {{ color-scheme: light dark; --bg: #f6f7fb; --fg: #162033; --muted: #687386; --card: #ffffff; --line: #dfe5ef; --good: #176d3b; --bad: #a12b2b; }}
    @media (prefers-color-scheme: dark) {{ :root {{ --bg: #111722; --fg: #edf2fb; --muted: #a9b4c4; --card: #182233; --line: #2b3a50; --good: #75d39b; --bad: #ff8b8b; }} }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    header {{ padding: 32px clamp(16px, 5vw, 56px) 18px; }}
    main {{ padding: 0 clamp(16px, 5vw, 56px) 48px; }}
    h1 {{ margin: 0; font-size: clamp(28px, 6vw, 52px); letter-spacing: -0.04em; }}
    .subtitle, .muted, .node-id {{ color: var(--muted); }}
    .cards {{ display: grid; gap: 18px; }}
    .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 18px; padding: 22px; box-shadow: 0 12px 30px rgba(22, 32, 51, 0.08); }}
    .card h2 {{ margin: 0; font-size: 24px; }}
    .node-id {{ margin: 0 0 18px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 16px; }}
    h3 {{ margin: 0 0 8px; font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }}
    ul {{ margin: 0; padding-left: 18px; }}
    .privacy {{ list-style: none; padding: 0; display: grid; gap: 6px; }}
    .privacy li {{ display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--line); padding-bottom: 4px; }}
    .flag {{ font-weight: 700; }}
    .flag-safe {{ color: var(--good); }}
    .flag-exposed {{ color: var(--bad); }}
    .empty {{ background: var(--card); border: 1px dashed var(--line); border-radius: 18px; padding: 22px; }}
  </style>
</head>
<body>
  <header>
    <h1>HermesMesh Dashboard</h1>
    <p class="subtitle">Read-only registered Agent capabilities. Private memory, sessions, logs, skills, env vars, and transport commands are not shown.</p>
  </header>
  <main class="cards">{content}</main>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    mesh_home: Path | None = None

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            if path == "/health":
                self._send_json({"ok": True})
            elif path == "/api/nodes":
                self._send_json(public_nodes(self.mesh_home))
            elif path.startswith("/api/nodes/"):
                node_id = unquote(path.removeprefix("/api/nodes/"))
                node = get_registered_node(node_id, mesh_home=self.mesh_home)
                self._send_json(public_node_view(node))
            elif path == "/api/tasks":
                self._send_json(list_posted_tasks(self.mesh_home))
            elif path == "/api/assignments":
                self._send_json(list_task_assignments(self.mesh_home))
            elif path == "/api/results":
                self._send_json(list_task_results(self.mesh_home))
            elif path == "/":
                self._send_html(render_dashboard(public_nodes(self.mesh_home)))
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
            elif path == "/api/assignments":
                saved = record_task_assignment(data, mesh_home=self.mesh_home)
                self._send_json({"ok": True, "path": str(saved), "assignment_id": data.get("assignment_id")})
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
            raise CapabilityMeshValidationError("JSON request body is required")
        raw = self.rfile.read(length).decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise CapabilityMeshValidationError("JSON request body must be an object")
        return data

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
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
        print(f"HermesMesh dashboard listening on http://{host}:{server.server_port}")
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the read-only HermesMesh dashboard.")
    parser.add_argument("--mesh-home", default=None, help="Mesh registry home; defaults to $HERMES_MESH_HOME or ~/.hermes-mesh")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    serve_dashboard(host=args.host, port=args.port, mesh_home=args.mesh_home)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
