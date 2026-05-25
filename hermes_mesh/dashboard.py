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
    claim_task_assignment,
    complete_task_assignment,
    default_mesh_home,
    execute_plan_step,
    get_registered_node,
    list_node_assignments,
    list_posted_tasks,
    list_registered_nodes,
    list_task_assignments,
    list_task_results,
    plan_next_node_call,
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


def _html_list(items: list[Any], class_name: str = "chip-list") -> str:
    if not items:
        return '<span class="muted">none</span>'
    return "".join(f"<li>{html.escape(str(item))}</li>" for item in items)


def _html_kv(mapping: Mapping[str, Any]) -> str:
    if not mapping:
        return '<span class="muted">none declared</span>'
    return "".join(
        "<li>"
        f"<span>{html.escape(str(key))}</span>"
        f"<strong>{html.escape(str(value))}</strong>"
        "</li>"
        for key, value in mapping.items()
    )


def _stat_value(value: Any) -> str:
    return html.escape(str(value))


def render_dashboard(nodes: list[dict[str, Any]]) -> str:
    node_count = len(nodes)
    task_type_count = len({task for node in nodes for task in node.get("task_types", [])})
    tool_count = len({tool for node in nodes for tool in node.get("tools_available", [])})
    auto_accept_count = sum(1 for node in nodes if node.get("policies", {}).get("auto_accept_task_types"))

    cards = []
    for index, node in enumerate(nodes, start=1):
        policies = node["policies"]
        auto_accept = ", ".join(policies["auto_accept_task_types"]) or "none"
        accepts_class = "good" if policies["accepts_tasks"] else "bad"
        approval_label = "human approval" if policies["requires_human_approval"] else "auto-ready"
        approval_class = "warn" if policies["requires_human_approval"] else "good"
        privacy_flags = "".join(
            "<li>"
            f"<span>{html.escape(flag.replace('_', ' '))}</span>"
            f"<span class=\"flag flag-{html.escape(status)}\">{html.escape(status)}</span>"
            "</li>"
            for flag, status in node["privacy"].items()
        )
        cards.append(
            "<article class=\"node-card\">"
            "<div class=\"node-card__bar\"></div>"
            "<div class=\"node-card__top\">"
            "<div>"
            f"<p class=\"eyebrow\">Node {index:02d}</p>"
            f"<h2>{html.escape(str(node['display_name']))}</h2>"
            f"<p class=\"node-id\">{html.escape(str(node['node_id']))}</p>"
            "</div>"
            "<div class=\"status-stack\">"
            f"<span class=\"pill {accepts_class}\">{'accepting tasks' if policies['accepts_tasks'] else 'paused'}</span>"
            f"<span class=\"pill {approval_class}\">{html.escape(approval_label)}</span>"
            "</div>"
            "</div>"
            "<div class=\"node-grid\">"
            "<section class=\"panel span-2\">"
            "<h3>Task capability</h3>"
            f"<ul class=\"chip-list\">{_html_list(node['task_types'])}</ul>"
            "</section>"
            "<section class=\"panel\">"
            "<h3>Tools</h3>"
            f"<ul class=\"chip-list compact\">{_html_list(node['tools_available'])}</ul>"
            "</section>"
            "<section class=\"panel\">"
            "<h3>Runtime</h3>"
            f"<div class=\"transport\"><span>{html.escape(str(node['transport']['type']))}</span><small>transport type only</small></div>"
            "</section>"
            "<section class=\"panel\">"
            "<h3>Resources</h3>"
            f"<ul class=\"kv-list\">{_html_kv(node['resources'])}</ul>"
            "</section>"
            "<section class=\"panel\">"
            "<h3>Policy</h3>"
            "<ul class=\"kv-list\">"
            f"<li><span>accepts_tasks</span><strong>{html.escape(str(policies['accepts_tasks']).lower())}</strong></li>"
            f"<li><span>requires_human_approval</span><strong>{html.escape(str(policies['requires_human_approval']).lower())}</strong></li>"
            f"<li><span>auto_accept_task_types</span><strong>{html.escape(auto_accept)}</strong></li>"
            f"<li class=\"sr-only\">requires_human_approval: {html.escape(str(policies['requires_human_approval']).lower())}</li>"
            "</ul>"
            "</section>"
            "<section class=\"panel privacy-panel span-2\">"
            "<h3>Privacy boundary</h3>"
            f"<ul class=\"privacy\">{privacy_flags}</ul>"
            "</section>"
            "</div>"
            "</article>"
        )
    content = "".join(cards) or (
        '<section class="empty-state">'
        '<p class="eyebrow">No nodes registered</p>'
        '<h2>Waiting for Hermes instances to join.</h2>'
        '<p>Use the one-shot registration script or POST a capability manifest to <code>/api/nodes</code>. Private memories, skills, sessions, logs, env vars, and secrets remain local.</p>'
        '</section>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HermesMesh Dashboard</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #070a12;
      --bg-soft: #0c1220;
      --surface: rgba(15, 23, 42, 0.76);
      --surface-strong: rgba(20, 30, 50, 0.92);
      --line: rgba(148, 163, 184, 0.18);
      --line-strong: rgba(148, 163, 184, 0.32);
      --fg: #eef4ff;
      --muted: #91a0b8;
      --soft: #c6d3e8;
      --accent: #79f2c0;
      --accent-2: #8ab4ff;
      --accent-3: #d7b7ff;
      --good: #76f5b4;
      --warn: #ffd166;
      --bad: #ff8f9b;
      --shadow: 0 22px 80px rgba(0, 0, 0, 0.38);
      --radius: 24px;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      --sans: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    html {{ min-height: 100%; background: var(--bg); }}
    body {{
      margin: 0;
      min-height: 100vh;
      color: var(--fg);
      font: 15px/1.55 var(--sans);
      background:
        radial-gradient(circle at 12% 8%, rgba(121, 242, 192, 0.16), transparent 28rem),
        radial-gradient(circle at 82% 4%, rgba(138, 180, 255, 0.18), transparent 30rem),
        linear-gradient(180deg, #080b14 0%, #0a1020 48%, #070a12 100%);
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image: linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px);
      background-size: 48px 48px;
      mask-image: linear-gradient(to bottom, rgba(0,0,0,.7), transparent 72%);
    }}
    a {{ color: inherit; }}
    .shell {{ width: min(1180px, calc(100vw - 32px)); margin: 0 auto; padding: 34px 0 56px; }}
    .hero {{
      position: relative;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 32px;
      background: linear-gradient(135deg, rgba(15, 23, 42, .88), rgba(12, 18, 32, .68));
      box-shadow: var(--shadow);
      padding: clamp(24px, 5vw, 48px);
      margin-bottom: 22px;
    }}
    .hero::after {{
      content: "";
      position: absolute;
      width: 420px;
      height: 420px;
      right: -140px;
      top: -190px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(121, 242, 192, .2), transparent 64%);
      filter: blur(4px);
    }}
    .hero__content {{ position: relative; z-index: 1; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 28px; align-items: end; }}
    .eyebrow {{ margin: 0 0 10px; color: var(--accent); font: 700 12px/1 var(--mono); letter-spacing: .16em; text-transform: uppercase; }}
    h1 {{ margin: 0; max-width: 840px; font-size: clamp(42px, 8vw, 86px); line-height: .9; letter-spacing: -.07em; text-wrap: balance; }}
    .subtitle {{ margin: 22px 0 0; max-width: 760px; color: var(--soft); font-size: clamp(16px, 2vw, 19px); text-wrap: pretty; }}
    .hero-actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 24px; }}
    .button {{ display: inline-flex; align-items: center; min-height: 42px; padding: 0 15px; border: 1px solid var(--line-strong); border-radius: 999px; background: rgba(255,255,255,.06); color: var(--fg); text-decoration: none; font-weight: 700; }}
    .button.primary {{ color: #04120d; background: linear-gradient(135deg, var(--accent), #b3ffe0); border-color: transparent; }}
    .stats {{ display: grid; grid-template-columns: repeat(2, minmax(118px, 1fr)); gap: 12px; min-width: min(360px, 100%); }}
    .stat {{ border: 1px solid var(--line); border-radius: 20px; background: rgba(255,255,255,.055); padding: 16px; backdrop-filter: blur(14px); }}
    .stat strong {{ display: block; font-size: 34px; line-height: 1; letter-spacing: -.05em; }}
    .stat span {{ display: block; margin-top: 8px; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .1em; }}
    .notice {{ display: flex; gap: 12px; align-items: flex-start; border: 1px solid rgba(121,242,192,.24); border-radius: 22px; background: rgba(121,242,192,.08); padding: 14px 16px; color: var(--soft); margin-bottom: 22px; }}
    .notice strong {{ color: var(--fg); }}
    .cards {{ display: grid; gap: 18px; }}
    .node-card {{ position: relative; overflow: hidden; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); box-shadow: 0 16px 48px rgba(0,0,0,.22); }}
    .node-card__bar {{ height: 3px; background: linear-gradient(90deg, var(--accent), var(--accent-2), var(--accent-3)); }}
    .node-card__top {{ display: flex; justify-content: space-between; gap: 18px; padding: 24px 24px 18px; }}
    .node-card h2 {{ margin: 0; font-size: clamp(23px, 3vw, 34px); line-height: 1.04; letter-spacing: -.045em; }}
    .node-id {{ margin: 10px 0 0; color: var(--muted); font: 13px/1.4 var(--mono); overflow-wrap: anywhere; }}
    .status-stack {{ display: flex; align-items: flex-end; flex-direction: column; gap: 8px; flex: 0 0 auto; }}
    .pill {{ display: inline-flex; align-items: center; gap: 7px; min-height: 30px; border: 1px solid var(--line); border-radius: 999px; padding: 0 11px; background: rgba(255,255,255,.055); color: var(--soft); font: 700 12px/1 var(--mono); text-transform: uppercase; letter-spacing: .06em; white-space: nowrap; }}
    .pill::before {{ content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; box-shadow: 0 0 16px currentColor; }}
    .pill.good {{ color: var(--good); }} .pill.warn {{ color: var(--warn); }} .pill.bad {{ color: var(--bad); }}
    .node-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; border-top: 1px solid var(--line); background: var(--line); }}
    .panel {{ min-height: 150px; background: rgba(10, 16, 30, .66); padding: 18px; }}
    .panel.span-2 {{ grid-column: span 2; }}
    h3 {{ margin: 0 0 13px; color: var(--muted); font: 800 12px/1 var(--mono); letter-spacing: .14em; text-transform: uppercase; }}
    ul {{ margin: 0; padding: 0; list-style: none; }}
    .chip-list {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .chip-list li {{ border: 1px solid rgba(138,180,255,.26); border-radius: 999px; padding: 7px 10px; color: #dbe7ff; background: rgba(138,180,255,.09); font: 700 13px/1 var(--mono); }}
    .chip-list.compact li {{ color: #dffced; border-color: rgba(121,242,192,.24); background: rgba(121,242,192,.075); }}
    .kv-list {{ display: grid; gap: 9px; }}
    .kv-list li, .privacy li {{ display: flex; justify-content: space-between; gap: 14px; align-items: baseline; color: var(--muted); }}
    .kv-list strong {{ color: var(--fg); text-align: right; overflow-wrap: anywhere; }}
    .transport span {{ display: block; color: var(--fg); font: 800 26px/1 var(--mono); letter-spacing: -.06em; }}
    .transport small {{ display: block; margin-top: 8px; color: var(--muted); }}
    .privacy {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 18px; }}
    .flag {{ font: 800 12px/1 var(--mono); text-transform: uppercase; letter-spacing: .08em; }}
    .flag-safe {{ color: var(--good); }} .flag-exposed {{ color: var(--bad); }}
    .muted {{ color: var(--muted); }}
    .sr-only {{ position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }}
    .empty-state {{ border: 1px dashed var(--line-strong); border-radius: var(--radius); background: rgba(15,23,42,.54); padding: clamp(24px, 5vw, 44px); }}
    .empty-state h2 {{ margin: 0; font-size: clamp(26px, 4vw, 48px); letter-spacing: -.05em; }}
    .empty-state p:not(.eyebrow) {{ max-width: 700px; color: var(--soft); }}
    code {{ font-family: var(--mono); color: var(--accent); }}
    @media (max-width: 900px) {{
      .hero__content {{ grid-template-columns: 1fr; }}
      .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .node-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .panel.span-2 {{ grid-column: span 2; }}
    }}
    @media (max-width: 620px) {{
      .shell {{ width: min(100% - 20px, 1180px); padding-top: 10px; }}
      .hero {{ border-radius: 24px; }}
      .hero__content, .node-card__top {{ display: block; }}
      .stats {{ margin-top: 22px; }}
      .status-stack {{ align-items: flex-start; flex-direction: row; flex-wrap: wrap; margin-top: 16px; }}
      .node-grid, .privacy {{ grid-template-columns: 1fr; }}
      .panel.span-2 {{ grid-column: span 1; }}
    }}
    @media (prefers-reduced-motion: no-preference) {{
      .node-card, .button {{ transition: transform .18s ease, border-color .18s ease, background .18s ease; }}
      .node-card:hover {{ transform: translateY(-2px); border-color: rgba(121,242,192,.34); }}
      .button:hover {{ transform: translateY(-1px); background: rgba(255,255,255,.09); }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="hero__content">
        <div>
          <p class="eyebrow">Privacy-first capability network</p>
          <h1>HermesMesh Dashboard</h1>
          <p class="subtitle">Registered Hermes nodes expose task-completion capability, not private local state. Skills, memory, sessions, logs, env vars, transport commands, and secrets stay off the mesh.</p>
          <div class="hero-actions">
            <a class="button primary" href="/api/nodes">View node JSON</a>
            <a class="button" href="/health">Health check</a>
          </div>
        </div>
        <div class="stats" aria-label="Mesh summary">
          <div class="stat"><strong>{_stat_value(node_count)}</strong><span>nodes</span></div>
          <div class="stat"><strong>{_stat_value(task_type_count)}</strong><span>task types</span></div>
          <div class="stat"><strong>{_stat_value(tool_count)}</strong><span>tools</span></div>
          <div class="stat"><strong>{_stat_value(auto_accept_count)}</strong><span>auto-ready</span></div>
        </div>
      </div>
    </section>
    <section class="notice" aria-label="Privacy notice">
      <span>●</span>
      <div><strong>Public view is deliberately narrow.</strong> This page summarizes routing metadata only; private memories, local skills, session traces, raw logs, environment variables, secrets, and transport commands are never rendered here.</div>
    </section>
    <section class="cards" aria-label="Registered Hermes nodes">{content}</section>
  </main>
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
                suffix = path.removeprefix("/api/nodes/")
                if suffix.endswith("/assignments"):
                    node_id = unquote(suffix.removesuffix("/assignments"))
                    get_registered_node(node_id, mesh_home=self.mesh_home)
                    self._send_json(list_node_assignments(node_id, mesh_home=self.mesh_home))
                else:
                    node_id = unquote(suffix)
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
