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
    build_a2a_task,
    build_agent_card,
    build_task_assignment,
    claim_task_assignment,
    complete_task_assignment,
    default_mesh_home,
    execute_plan_step,
    get_registered_node,
    list_node_assignments,
    list_a2a_tasks,
    list_posted_tasks,
    list_registered_nodes,
    list_task_assignments,
    list_task_results,
    plan_next_node_call,
    post_task,
    public_node_presence,
    record_node_heartbeat,
    record_a2a_task,
    record_task_assignment,
    record_task_result,
    register_node_manifest,
    route_task,
    validate_task_contract,
    wake_assignment,
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


def _public_node_status(manifest: Mapping[str, Any], mesh_home: str | Path | None = None) -> dict[str, Any]:
    """Return dashboard-safe status derived only from heartbeat timestamps."""

    return public_node_presence(str(manifest.get("node_id")), mesh_home=mesh_home)


def public_node_statuses(mesh_home: str | Path | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for manifest in list_registered_nodes(mesh_home=mesh_home):
        view = public_node_view(manifest, mesh_home=mesh_home)
        rows.append(
            {
                "node_id": view.get("node_id"),
                "display_name": view.get("display_name"),
                "task_types": view.get("task_types", []),
                "tools_available": view.get("tools_available", []),
                "online_status": public_node_presence(str(manifest.get("node_id")), mesh_home=mesh_home),
            }
        )
    return rows


def public_node_view(manifest: Mapping[str, Any], mesh_home: str | Path | None = None) -> dict[str, Any]:
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
        "online_status": _public_node_status(manifest, mesh_home=mesh_home),
        "privacy": {
            str(flag): "safe" if value is False else "exposed"
            for flag, value in sorted(privacy.items())
        },
    }


def public_nodes(mesh_home: str | Path | None = None) -> list[dict[str, Any]]:
    return [public_node_view(node, mesh_home=mesh_home) for node in list_registered_nodes(mesh_home=mesh_home)]


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



def _count_by_status(records: list[Mapping[str, Any]], key: str = "status") -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        status = str(record.get(key) or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _safe_text(value: Any, max_len: int = 180) -> str:
    text = "" if value is None else str(value)
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def _card_html(card: Mapping[str, Any]) -> str:
    chips = "".join(
        f'<span class="mini-chip">{html.escape(str(chip))}</span>'
        for chip in card.get("chips", [])
        if chip is not None and str(chip) != ""
    )
    meta = "".join(
        "<li>"
        f"<span>{html.escape(str(key))}</span>"
        f"<strong>{html.escape(_safe_text(value, 80))}</strong>"
        "</li>"
        for key, value in card.get("meta", {}).items()
        if value is not None and value != ""
    )
    summary = card.get("summary")
    summary_html = f'<p class="kanban-summary">{html.escape(_safe_text(summary))}</p>' if summary else ""
    return (
        '<article class="kanban-card">'
        '<div class="mesh-thumb mesh-thumb--small" aria-hidden="true"><i></i><i></i><i></i></div>'
        '<div class="kanban-card__top">'
        f'<span class="kanban-card__type">{html.escape(str(card.get("type", "card")))}</span>'
        f'<span class="kanban-card__status">{html.escape(str(card.get("status", "unknown")))}</span>'
        '</div>'
        f'<h3>{html.escape(_safe_text(card.get("title") or card.get("id"), 120))}</h3>'
        f'<p class="kanban-id">{html.escape(str(card.get("id", "")))}</p>'
        f'{summary_html}'
        f'<div class="mini-chip-row">{chips}</div>'
        f'<ul class="kanban-meta">{meta}</ul>'
        '</article>'
    )


def _board_column_for_assignment_status(status: str) -> str:
    """Map assignment status to the public board's compatibility column id."""

    if status == "claimed":
        return "claimed"
    if status == "completed":
        return "completed"
    if status == "failed":
        return "failed"
    return "assigned"


def public_board(mesh_home: str | Path | None = None) -> dict[str, Any]:
    """Return a dashboard-safe Capability Mesh Kanban board projection."""

    tasks = list_posted_tasks(mesh_home)
    assignments = list_task_assignments(mesh_home)
    results = list_task_results(mesh_home)
    columns: list[dict[str, Any]] = [
        {
            "id": "posted",
            "title": "Triage",
            "legacy_title": "Posted",
            "kanban_status": "triage",
            "description": "New mesh tasks waiting for routing or decomposition.",
            "cards": [],
        },
        {
            "id": "assigned",
            "title": "Ready",
            "legacy_title": "Assigned",
            "kanban_status": "ready",
            "description": "Assignments selected for a node and ready to claim.",
            "cards": [],
        },
        {
            "id": "claimed",
            "title": "Running",
            "legacy_title": "Claimed",
            "kanban_status": "running",
            "description": "Claimed work currently being executed by a node lane.",
            "cards": [],
        },
        {
            "id": "completed",
            "title": "Done",
            "legacy_title": "Completed",
            "kanban_status": "done",
            "description": "Completed assignments with structured handoff metadata.",
            "cards": [],
        },
        {
            "id": "failed",
            "title": "Blocked",
            "legacy_title": "Failed",
            "kanban_status": "blocked",
            "description": "Failed or blocked work that needs human/operator attention.",
            "cards": [],
        },
        {
            "id": "results",
            "title": "Results",
            "legacy_title": "Results",
            "kanban_status": "done",
            "description": "Privacy-filtered result records and summaries.",
            "cards": [],
        },
    ]
    by_id = {column["id"]: column for column in columns}
    assigned_task_ids = {str(item.get("task_id")) for item in assignments}
    result_task_ids = {str(item.get("task_id")) for item in results}

    for task in tasks:
        task_id = str(task.get("task_id", ""))
        if task_id in assigned_task_ids or task_id in result_task_ids:
            continue
        by_id["posted"]["cards"].append(
            {
                "id": task_id,
                "type": "task",
                "status": "posted",
                "title": task.get("objective") or task_id,
                "chips": [task.get("task_type"), *(task.get("required_tools") or [])],
                "meta": {"task_type": task.get("task_type"), "tools": ", ".join(task.get("required_tools") or [])},
            }
        )

    for assignment in assignments:
        status = str(assignment.get("status") or "assigned")
        column_id = _board_column_for_assignment_status(status)
        tool_call = assignment.get("tool_call") if isinstance(assignment.get("tool_call"), Mapping) else {}
        by_id[column_id]["cards"].append(
            {
                "id": assignment.get("assignment_id"),
                "type": "assignment",
                "status": status,
                "title": tool_call.get("objective") or assignment.get("task_id"),
                "chips": [assignment.get("node_id"), assignment.get("tool_call_id")],
                "meta": {
                    "node": assignment.get("node_id"),
                    "task": assignment.get("task_id"),
                    "tool_call": assignment.get("tool_call_id"),
                },
            }
        )

    for result in results:
        payload = result.get("result") if isinstance(result.get("result"), Mapping) else {}
        summary = payload.get("final_summary") or payload.get("test_report") or payload.get("summary")
        by_id["results"]["cards"].append(
            {
                "id": result.get("result_id"),
                "type": "handoff",
                "status": result.get("status") or "done",
                "title": result.get("task_id"),
                "summary": summary,
                "chips": [result.get("node_id"), result.get("status")],
                "meta": {"task": result.get("task_id"), "node": result.get("node_id"), "record": "result"},
            }
        )

    for column in columns:
        column["count"] = len(column["cards"])
    return {
        "columns": columns,
        "summary": {
            "tasks": len(tasks),
            "assignments": len(assignments),
            "results": len(results),
            "assignment_status": _count_by_status(assignments),
            "result_status": _count_by_status(results),
        },
    }


def _dashboard_summary(nodes: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "node_count": len(nodes),
        "task_type_count": len({task for node in nodes for task in node.get("task_types", [])}),
        "tool_count": len({tool for node in nodes for tool in node.get("tools_available", [])}),
        "auto_accept_count": sum(
            1 for node in nodes if node.get("policies", {}).get("auto_accept_task_types")
        ),
    }


def _dashboard_actions() -> list[dict[str, str]]:
    return [
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


def _dashboard_nodes_drawer() -> dict[str, str]:
    return {
        "title": "Registered nodes",
        "endpoint": "/api/nodes/statuses",
        "copy": "Node details are lazy-loaded so the dashboard shell stays privacy-light.",
    }


def build_dashboard_ui_projection(mesh_home: str | Path | None = None) -> dict[str, Any]:
    """Return a privacy-safe JSON projection for standalone UI rendering."""

    nodes = public_nodes(mesh_home)
    return {
        "title": "Capability Mesh",
        "issue_label": "Open Design dashboard projection",
        "privacy_notice": "Privacy-first view: public counts, lazy node status, filtered board data.",
        "summary": _dashboard_summary(nodes),
        "actions": _dashboard_actions(),
        "nodes_drawer": _dashboard_nodes_drawer(),
        "kanban": public_board(mesh_home),
    }


def _render_kanban_board(board: Mapping[str, Any]) -> str:
    rendered_columns = []
    for column in board.get("columns", []):
        cards = "".join(_card_html(card) for card in column.get("cards", [])) or '<p class="kanban-empty">No cards</p>'
        column_title = str(column.get("title", column.get("id")))
        status = str(column.get("kanban_status") or column.get("id") or "")
        description = column.get("description")
        legacy_title = column.get("legacy_title")
        legacy_html = f'<span class="sr-only">{html.escape(str(legacy_title))}</span>' if legacy_title else ""
        if description:
            description_html = (
                '<p class="kanban-column__hint">'
                f'{html.escape(str(description))}{legacy_html}'
                '</p>'
            )
        else:
            description_html = legacy_html
        rendered_columns.append(
            f'<section class="kanban-column kanban-column--{html.escape(status)}" data-status="{html.escape(status)}">'
            '<div class="kanban-column__header">'
            '<div>'
            f'<h2><span class="status-dot" aria-hidden="true"></span>{html.escape(column_title)}</h2>'
            f'<small>{html.escape(status)}</small>'
            '</div>'
            f'<span>{html.escape(str(column.get("count", 0)))}</span>'
            '</div>'
            f'{description_html}'
            f'<div class="kanban-column__cards">{cards}</div>'
            '</section>'
        )
    return "".join(rendered_columns)

def render_dashboard(nodes: list[dict[str, Any]], board: Mapping[str, Any] | None = None) -> str:
    board = board or {"columns": [], "summary": {}}
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
        presence_raw = node.get("online_status")
        presence: Mapping[str, Any] = presence_raw if isinstance(presence_raw, Mapping) else {}
        presence_status = str(presence.get("status") or "unknown")
        presence_label = str(presence.get("label") or presence_status).replace("_", " ")
        if presence_status == "online":
            presence_class = "good"
        elif presence_status in {"offline"}:
            presence_class = "bad"
        else:
            presence_class = "warn"
        last_seen = presence.get("last_seen_at") or "no heartbeat yet"
        cards.append(
            "<article class=\"node-card\">"
            "<div class=\"node-card__top\">"
            "<div>"
            f"<p class=\"eyebrow\">Node {index:02d}</p>"
            f"<h2>{html.escape(str(node['display_name']))}</h2>"
            f"<p class=\"node-id\">{html.escape(str(node['node_id']))}</p>"
            "</div>"
            "<div class=\"mesh-thumb\" aria-hidden=\"true\"><i></i><i></i><i></i></div>"
            "<div class=\"status-stack\">"
            f"<span class=\"pill {accepts_class}\">{'accepting tasks' if policies['accepts_tasks'] else 'paused'}</span>"
            f"<span class=\"pill {approval_class}\">{html.escape(approval_label)}</span>"
            f"<span class=\"pill {presence_class}\">{html.escape(presence_label)}</span>"
            f"<span class=\"pill warn\">last seen: {html.escape(str(last_seen))}</span>"
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
        '<h2>Waiting for Capability Mesh nodes to join.</h2>'
        '<p>Use the one-shot registration script or POST a capability manifest to <code>/api/nodes</code>. Private memories, skills, sessions, logs, env vars, and secrets remain local.</p>'
        '</section>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Capability Mesh</title>
  <style>
    :root {{
      color-scheme: light;
      --paper: #efe7d2;
      --paper-deep: #e2d5b7;
      --surface: #f8f1df;
      --surface-alt: #eadfca;
      --ink: #211a14;
      --muted: #756a5b;
      --soft: #4d4439;
      --line: #2d251d;
      --line-soft: rgba(45, 37, 29, .28);
      --accent: #9f3f27;
      --accent-2: #235d50;
      --accent-3: #b98a2f;
      --good: #235d50;
      --warn: #9f6d1b;
      --bad: #9f3f27;
      --shadow: 8px 8px 0 rgba(45, 37, 29, .16);
      --radius: 0;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      --sans: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --serif: "Playfair Display", Didot, "Bodoni 72", Georgia, "Times New Roman", serif;
    }}
    * {{ box-sizing: border-box; }}
    html {{ min-height: 100%; background: var(--paper); }}
    body {{
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      font: 15px/1.55 var(--sans);
      background:
        linear-gradient(90deg, rgba(45,37,29,.045) 1px, transparent 1px),
        linear-gradient(rgba(45,37,29,.035) 1px, transparent 1px),
        radial-gradient(circle at 14% 10%, rgba(185, 138, 47, .16), transparent 24rem),
        radial-gradient(circle at 86% 3%, rgba(35, 93, 80, .12), transparent 26rem),
        var(--paper);
      background-size: 42px 42px, 42px 42px, auto, auto, auto;
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image: radial-gradient(rgba(45,37,29,.11) .6px, transparent .8px);
      background-size: 7px 7px;
      opacity: .28;
    }}
    a {{ color: inherit; }}
    .shell {{ width: min(1240px, calc(100vw - 32px)); margin: 0 auto; padding: 24px 0 56px; }}
    .hero {{
      position: relative;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: linear-gradient(135deg, rgba(248, 241, 223, .96), rgba(226, 213, 183, .86));
      box-shadow: var(--shadow);
      padding: 0;
      margin-bottom: 22px;
    }}
    .hero__content {{ position: relative; z-index: 1; display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 0; align-items: stretch; }}
    .masthead {{ padding: clamp(24px, 5vw, 54px); border-right: 1px solid var(--line); }}
    .issue-meta {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border-bottom: 1px solid var(--line); margin: 0 0 24px; color: var(--muted); font: 700 11px/1.3 var(--mono); letter-spacing: .08em; text-transform: uppercase; }}
    .issue-meta span {{ display: block; padding: 0 10px 12px 0; }}
    .eyebrow {{ margin: 0 0 10px; color: var(--accent); font: 800 11px/1 var(--mono); letter-spacing: .16em; text-transform: uppercase; }}
    h1 {{ margin: 0; max-width: 840px; font-family: var(--serif); font-size: clamp(48px, 9vw, 112px); font-weight: 700; line-height: .82; letter-spacing: -.06em; text-wrap: balance; }}
    .subtitle {{ margin: 22px 0 0; max-width: 780px; color: var(--soft); font-size: clamp(16px, 2vw, 20px); text-wrap: pretty; }}
    .hero-actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 24px; }}
    .button {{ display: inline-flex; align-items: center; min-height: 40px; padding: 0 14px; border: 1px solid var(--line); border-radius: 0; background: var(--surface); color: var(--ink); text-decoration: none; font: 800 12px/1 var(--mono); letter-spacing: .06em; text-transform: uppercase; box-shadow: 3px 3px 0 rgba(45,37,29,.14); }}
    .button.primary {{ color: var(--surface); background: var(--ink); }}
    .stats {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border-left: 0; background: var(--line); gap: 1px; }}
    .stat {{ min-height: 150px; border: 0; border-radius: 0; background: rgba(248, 241, 223, .9); color: var(--ink); padding: 18px; display: flex; flex-direction: column; justify-content: space-between; text-align: left; font: inherit; }}
    button.stat {{ cursor: pointer; }}
    .stat strong {{ display: block; font-family: var(--serif); font-size: 52px; line-height: .86; letter-spacing: -.05em; }}
    .stat span {{ display: block; color: var(--muted); font: 800 11px/1.2 var(--mono); text-transform: uppercase; letter-spacing: .1em; }}
    .stat small {{ display: inline-flex; align-items: center; margin-top: 9px; color: var(--accent); font: 800 10px/1 var(--mono); text-transform: uppercase; letter-spacing: .08em; }}
    .notice {{ display: flex; gap: 12px; align-items: flex-start; border: 1px solid var(--line); border-radius: 0; background: rgba(35,93,80,.08); padding: 14px 16px; color: var(--soft); margin-bottom: 22px; box-shadow: 4px 4px 0 rgba(35,93,80,.12); }}
    .notice strong {{ color: var(--ink); }}
    .cards {{ display: grid; gap: 18px; }}
    .node-card {{ position: relative; overflow: hidden; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); box-shadow: var(--shadow); }}
    .node-card__top {{ display: grid; grid-template-columns: minmax(0, 1fr) 180px auto; gap: 18px; padding: 22px; align-items: start; border-bottom: 1px solid var(--line); }}
    .node-card h2 {{ margin: 0; font-family: var(--serif); font-size: clamp(28px, 4vw, 48px); line-height: .96; letter-spacing: -.045em; }}
    .node-id {{ margin: 10px 0 0; color: var(--muted); font: 13px/1.4 var(--mono); overflow-wrap: anywhere; }}
    .status-stack {{ display: flex; align-items: flex-end; flex-direction: column; gap: 8px; flex: 0 0 auto; }}
    .pill {{ display: inline-flex; align-items: center; gap: 7px; min-height: 29px; border: 1px solid var(--line); border-radius: 0; padding: 0 10px; background: var(--surface-alt); color: var(--soft); font: 800 11px/1 var(--mono); text-transform: uppercase; letter-spacing: .06em; white-space: nowrap; }}
    .pill::before {{ content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }}
    .pill.good {{ color: var(--good); }} .pill.warn {{ color: var(--warn); }} .pill.bad {{ color: var(--bad); }}
    .node-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; background: var(--line); }}
    .panel {{ min-height: 150px; background: rgba(248, 241, 223, .92); padding: 18px; }}
    .panel.span-2 {{ grid-column: span 2; }}
    h3 {{ margin: 0 0 13px; color: var(--muted); font: 800 11px/1 var(--mono); letter-spacing: .14em; text-transform: uppercase; }}
    ul {{ margin: 0; padding: 0; list-style: none; }}
    .chip-list {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .chip-list li {{ border: 1px solid var(--line); border-radius: 0; padding: 7px 10px; color: var(--ink); background: rgba(185,138,47,.12); font: 700 13px/1 var(--mono); }}
    .chip-list.compact li {{ color: var(--accent-2); background: rgba(35,93,80,.08); }}
    .kv-list {{ display: grid; gap: 9px; }}
    .kv-list li, .privacy li {{ display: flex; justify-content: space-between; gap: 14px; align-items: baseline; color: var(--muted); }}
    .kv-list strong {{ color: var(--ink); text-align: right; overflow-wrap: anywhere; }}
    .transport span {{ display: block; color: var(--ink); font: 800 26px/1 var(--mono); letter-spacing: -.06em; }}
    .transport small {{ display: block; margin-top: 8px; color: var(--muted); }}
    .privacy {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 18px; }}
    .flag {{ font: 800 12px/1 var(--mono); text-transform: uppercase; letter-spacing: .08em; }}
    .flag-safe {{ color: var(--good); }} .flag-exposed {{ color: var(--bad); }}
    .muted {{ color: var(--muted); }}
    .sr-only {{ position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }}
    .empty-state {{ border: 1px dashed var(--line); border-radius: var(--radius); background: var(--surface); padding: clamp(24px, 5vw, 44px); }}
    .empty-state h2 {{ margin: 0; font-family: var(--serif); font-size: clamp(30px, 5vw, 56px); letter-spacing: -.05em; }}
    .empty-state p:not(.eyebrow) {{ max-width: 700px; color: var(--soft); }}
    code {{ font-family: var(--mono); color: var(--accent); }}

    .mesh-thumb {{ position: relative; min-height: 128px; border: 1px solid var(--line); background: linear-gradient(135deg, rgba(35,93,80,.12), transparent 42%), linear-gradient(45deg, transparent 48%, var(--line-soft) 49%, var(--line-soft) 51%, transparent 52%), var(--paper-deep); overflow: hidden; }}
    .mesh-thumb::before, .mesh-thumb::after, .mesh-thumb i {{ content: ""; position: absolute; width: 11px; height: 11px; border: 1px solid var(--line); background: var(--surface); }}
    .mesh-thumb::before {{ left: 18%; top: 20%; }}
    .mesh-thumb::after {{ right: 17%; bottom: 18%; background: var(--accent-3); }}
    .mesh-thumb i:nth-child(1) {{ left: 58%; top: 26%; background: var(--accent-2); }}
    .mesh-thumb i:nth-child(2) {{ left: 34%; bottom: 21%; background: var(--accent); }}
    .mesh-thumb i:nth-child(3) {{ right: 34%; top: 58%; }}
    .mesh-thumb--small {{ min-height: 78px; margin-bottom: 12px; }}

    .mesh-kanban {{ margin: 22px 0; }}
    .section-heading {{ display: flex; justify-content: space-between; gap: 16px; align-items: end; margin: 28px 0 14px; }}
    .section-heading h2 {{ margin: 0; font-family: var(--serif); font-size: clamp(32px, 5vw, 58px); line-height: .9; letter-spacing: -.05em; }}
    .section-heading p {{ margin: 6px 0 0; color: var(--muted); max-width: 760px; }}
    .kanban-board {{ display: grid; grid-template-columns: repeat(6, minmax(220px, 1fr)); gap: 14px; overflow-x: auto; padding: 0 8px 8px 0; }}
    .kanban-column {{ min-width: 220px; border: 1px solid var(--line); border-radius: 0; background: var(--surface); box-shadow: 5px 5px 0 rgba(45,37,29,.12); }}
    .kanban-column__header {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; padding: 14px 15px; border-bottom: 1px solid var(--line); }}
    .kanban-column__header h2 {{ display: flex; align-items: center; gap: 8px; margin: 0; font: 800 13px/1 var(--mono); letter-spacing: .08em; text-transform: uppercase; }}
    .kanban-column__header small {{ display: block; margin-top: 7px; color: var(--muted); font: 700 10px/1 var(--mono); letter-spacing: .12em; text-transform: uppercase; }}
    .kanban-column__header > span {{ display: inline-flex; min-width: 26px; height: 26px; align-items: center; justify-content: center; border: 1px solid var(--line); border-radius: 0; background: var(--surface-alt); color: var(--soft); font: 800 12px/1 var(--mono); }}
    .status-dot {{ width: 9px; height: 9px; border: 1px solid var(--line); background: var(--accent-3); box-shadow: 2px 2px 0 rgba(45,37,29,.14); }}
    .kanban-column--triage .status-dot {{ background: var(--accent-3); }}
    .kanban-column--todo .status-dot {{ background: var(--muted); }}
    .kanban-column--ready .status-dot {{ background: var(--accent-2); }}
    .kanban-column--running .status-dot {{ background: #2f6fbb; }}
    .kanban-column--blocked .status-dot {{ background: var(--bad); }}
    .kanban-column--done .status-dot {{ background: var(--good); }}
    .kanban-column__hint {{ margin: 0; padding: 10px 15px 0; min-height: 45px; color: var(--muted); font-size: 12px; line-height: 1.35; }}
    .kanban-column__cards {{ display: grid; gap: 10px; padding: 12px; }}
    .kanban-card {{ border: 1px solid var(--line); border-radius: 0; background: rgba(239,231,210,.72); padding: 12px; }}
    .kanban-card__top {{ display: flex; justify-content: space-between; gap: 8px; color: var(--muted); font: 800 10px/1 var(--mono); text-transform: uppercase; letter-spacing: .08em; }}
    .kanban-card h3 {{ margin: 11px 0 6px; color: var(--ink); font: 700 18px/1.08 var(--serif); letter-spacing: -.02em; text-transform: none; }}
    .kanban-id {{ margin: 0 0 8px; color: var(--muted); font: 11px/1.35 var(--mono); overflow-wrap: anywhere; }}
    .kanban-summary {{ margin: 8px 0; color: var(--soft); font-size: 13px; }}
    .mini-chip-row {{ display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }}
    .mini-chip {{ border: 1px solid var(--line); border-radius: 0; padding: 4px 7px; color: var(--accent-2); background: rgba(35,93,80,.07); font: 700 10px/1 var(--mono); }}
    .kanban-meta {{ display: grid; gap: 5px; margin-top: 10px; }}
    .kanban-meta li {{ display: flex; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 12px; }}
    .kanban-meta strong {{ color: var(--ink); text-align: right; overflow-wrap: anywhere; }}
    .kanban-empty {{ margin: 0; color: var(--muted); font-size: 13px; }}
    .nodes-drawer[hidden] {{ display: none; }}
    .nodes-drawer {{ position: fixed; inset: 0; z-index: 20; display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, 520px); background: rgba(33,26,20,.32); }}
    .nodes-drawer__shade {{ border: 0; background: transparent; }}
    .nodes-drawer__panel {{ min-height: 100vh; border-left: 1px solid var(--line); background: var(--surface); box-shadow: -8px 0 0 rgba(45,37,29,.14); padding: 22px; overflow: auto; }}
    .nodes-drawer__top {{ display: flex; justify-content: space-between; gap: 14px; align-items: flex-start; padding-bottom: 16px; border-bottom: 1px solid var(--line); }}
    .nodes-drawer__top h2 {{ margin: 0; font-family: var(--serif); font-size: 42px; line-height: .9; letter-spacing: -.05em; }}
    .nodes-close {{ width: 38px; height: 38px; border: 1px solid var(--line); border-radius: 0; background: var(--ink); color: var(--surface); font: 900 20px/1 var(--mono); cursor: pointer; }}
    .nodes-list {{ display: grid; gap: 12px; margin-top: 18px; }}
    .node-row {{ border: 1px solid var(--line); border-radius: 0; background: rgba(239,231,210,.7); padding: 14px; }}
    .node-row__top {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }}
    .node-row h3 {{ margin: 0 0 5px; color: var(--ink); font: 700 22px/1.05 var(--serif); letter-spacing: -.02em; text-transform: none; }}
    .node-row__id {{ margin: 0; color: var(--muted); font: 11px/1.35 var(--mono); overflow-wrap: anywhere; }}
    .node-row__caps {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }}
    .status-pill {{ display: inline-flex; align-items: center; gap: 7px; border: 1px solid var(--line); border-radius: 0; padding: 7px 9px; background: var(--surface-alt); font: 900 10px/1 var(--mono); letter-spacing: .08em; text-transform: uppercase; white-space: nowrap; }}
    .status-pill::before {{ content: ""; width: 7px; height: 7px; border: 1px solid currentColor; border-radius: 50%; background: currentColor; }}
    .status-pill--online {{ color: var(--good); }} .status-pill--offline, .status-pill--error, .status-pill--timeout {{ color: var(--bad); }} .status-pill--stale, .status-pill--never_seen, .status-pill--unknown {{ color: var(--warn); }}
    .nodes-loading, .nodes-error {{ margin-top: 18px; color: var(--muted); }}
    .nodes-error {{ color: var(--bad); }}
    .nodes-template-source {{ display: none; }}
    @media (max-width: 900px) {{
      .hero__content {{ grid-template-columns: 1fr; }}
      .masthead {{ border-right: 0; border-bottom: 1px solid var(--line); }}
      .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .node-card__top {{ grid-template-columns: minmax(0, 1fr) 160px; }}
      .status-stack {{ grid-column: 1 / -1; align-items: flex-start; flex-direction: row; flex-wrap: wrap; }}
      .node-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .panel.span-2 {{ grid-column: span 2; }}
    }}
    @media (max-width: 620px) {{
      .shell {{ width: min(100% - 20px, 1180px); padding-top: 10px; }}
      .hero__content, .node-card__top {{ display: block; }}
      .issue-meta {{ grid-template-columns: 1fr; gap: 6px; }}
      .issue-meta span {{ padding-bottom: 4px; }}
      h1 {{ font-size: clamp(44px, 17vw, 72px); }}
      .mesh-thumb {{ margin-top: 16px; }}
      .stats {{ margin-top: 22px; }}
      .status-stack {{ align-items: flex-start; flex-direction: row; flex-wrap: wrap; margin-top: 16px; }}
      .node-grid, .privacy {{ grid-template-columns: 1fr; }}
      .panel.span-2 {{ grid-column: span 1; }}
      .nodes-drawer {{ grid-template-columns: 1fr; }}
      .nodes-drawer__shade {{ display: none; }}
      .nodes-drawer__panel {{ border-left: 0; }}
    }}
    @media (prefers-reduced-motion: no-preference) {{
      .node-card, .button {{ transition: transform .16s ease, box-shadow .16s ease, background .16s ease; }}
      .node-card:hover {{ transform: translate(-2px, -2px); box-shadow: 10px 10px 0 rgba(45,37,29,.18); }}
      .button:hover {{ transform: translate(-1px, -1px); box-shadow: 5px 5px 0 rgba(45,37,29,.18); }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="hero__content">
        <div class="masthead">
          <div class="issue-meta" aria-label="Dashboard issue metadata">
            <span>Issue 01</span>
            <span>Local Mesh Catalog</span>
            <span>Privacy First</span>
          </div>
          <p class="eyebrow">Privacy-first capability network</p>
          <h1>Capability Mesh</h1>
          <p class="subtitle">Registered Capability Mesh nodes expose task-completion capability, not private local state. Skills, memory, sessions, logs, env vars, transport commands, and secrets stay off the mesh.</p>
          <div class="hero-actions">
            <a class="button primary" href="/api/nodes">View node JSON</a>
            <a class="button" href="/health">Health check</a>
          </div>
        </div>
        <div class="stats" aria-label="Mesh summary">
          <button class="stat" type="button" id="nodesStat" aria-haspopup="dialog" aria-controls="nodesDrawer"><strong>{_stat_value(node_count)}</strong><span>nodes</span><small>Open node list</small></button>
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
    <section class="mesh-kanban" aria-label="Capability Mesh task board">
      <div class="section-heading">
        <div>
          <p class="eyebrow">Capability Mesh Kanban</p>
          <h2>KANBAN</h2>
          <p>Mirror the Capability Mesh task lifecycle: triage, todo, ready, running, blocked, and done. Cards stay privacy-filtered while surfacing task ids, node lanes, assignment status, handoff summaries, and result counts.</p>
        </div>
        <a class="button" href="/api/board">View board JSON</a>
      </div>
      <div class="kanban-board">{_render_kanban_board(board)}</div>
    </section>
    <div class="nodes-drawer" id="nodesDrawer" role="dialog" aria-modal="true" aria-labelledby="nodesDrawerTitle" hidden>
      <button class="nodes-drawer__shade" type="button" data-close-nodes aria-label="Close registered nodes list"></button>
      <aside class="nodes-drawer__panel">
        <div class="nodes-drawer__top">
          <div>
            <p class="eyebrow">Registered nodes</p>
            <h2 id="nodesDrawerTitle">Nodes</h2>
            <p class="subtitle">Names, declared capabilities, and current online status only. Private transports and commands stay hidden.</p>
          </div>
          <button class="nodes-close" type="button" data-close-nodes aria-label="Close">×</button>
        </div>
        <p class="nodes-loading" id="nodesLoading">Loading node status…</p>
        <p class="nodes-error" id="nodesError" hidden></p>
        <div class="nodes-list" id="nodesList"></div>
      </aside>
    </div>
    <script>
      (() => {{
        const trigger = document.getElementById('nodesStat');
        const drawer = document.getElementById('nodesDrawer');
        const list = document.getElementById('nodesList');
        const loading = document.getElementById('nodesLoading');
        const error = document.getElementById('nodesError');
        const closeButtons = document.querySelectorAll('[data-close-nodes]');
        const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[char]));
        const chips = (items) => (items || []).map((item) => `<span class="mini-chip">${{esc(item)}}</span>`).join('') || '<span class="muted">none</span>';
        const renderRows = (nodes) => {{
          if (!nodes.length) {{
            list.innerHTML = '<section class="empty-state"><p class="eyebrow">No nodes registered</p><h2>Waiting for Capability Mesh nodes to join.</h2></section>';
            return;
          }}
          list.innerHTML = nodes.map((node) => {{
            const health = node.online_status || {{ label: 'unknown', status: 'unknown' }};
            const label = String(health.label || health.status || 'unknown');
            const statusClass = String(health.status || label || 'unknown').toLowerCase().replace(/[^a-z0-9_-]/g, '-');
            return `<article class="node-row">
              <div class="node-row__top">
                <div><h3>${{esc(node.display_name || node.node_id)}}</h3><p class="node-row__id">${{esc(node.node_id)}}</p></div>
                <span class="status-pill status-pill--${{esc(statusClass)}}">${{esc(label)}}</span>
              </div>
              <div class="node-row__caps" aria-label="Node capabilities">${{chips(node.task_types)}}${{chips(node.tools_available)}}</div>
            </article>`;
          }}).join('');
        }};
        const openDrawer = async () => {{
          drawer.hidden = false;
          loading.hidden = false;
          error.hidden = true;
          list.innerHTML = '';
          try {{
            const response = await fetch('/api/nodes/statuses', {{ headers: {{ 'Accept': 'application/json' }} }});
            if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
            renderRows(await response.json());
          }} catch (err) {{
            error.textContent = `Unable to load node status: ${{err.message}}`;
            error.hidden = false;
          }} finally {{
            loading.hidden = true;
          }}
        }};
        const closeDrawer = () => {{ drawer.hidden = true; }};
        trigger?.addEventListener('click', openDrawer);
        closeButtons.forEach((button) => button.addEventListener('click', closeDrawer));
        document.addEventListener('keydown', (event) => {{ if (event.key === 'Escape' && !drawer.hidden) closeDrawer(); }});
      }})();
    </script>
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
            elif path == "/":
                self._send_html(render_dashboard(public_nodes(self.mesh_home), public_board(self.mesh_home)))
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
            elif path in {"/message:send", "/api/a2a/messages", "/api/a2a/tasks/send"}:
                message = data.get("message", data)
                task = build_a2a_task(message)
                record_a2a_task(task, mesh_home=self.mesh_home)
                self._send_json(task, content_type="application/a2a+json; charset=utf-8")
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
        self.send_header("Content-Type", content_type)
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
        print(f"Capability Mesh dashboard listening on http://{host}:{server.server_port}")
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the read-only Capability Mesh dashboard.")
    parser.add_argument("--mesh-home", default=None, help="Mesh registry home; defaults to $CAPABILITY_MESH_HOME, legacy $HERMES_MESH_HOME, or ~/.capability-mesh")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    serve_dashboard(host=args.host, port=args.port, mesh_home=args.mesh_home)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
