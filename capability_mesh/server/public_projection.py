"""Public display projections owned by the Capability Mesh Server."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from capability_mesh.core import (
    list_posted_tasks,
    list_registered_nodes,
    list_task_assignments,
    list_task_results,
    public_node_presence,
)


STATIC_UI_ROOT = Path(__file__).resolve().parents[1] / "ui" / "static"


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


def public_node_view(manifest: Mapping[str, Any], mesh_home: str | Path | None = None) -> dict[str, Any]:
    """Return the display-safe subset of a validated node manifest."""

    capabilities = manifest.get("capabilities", {})
    policies = manifest.get("policies", {})
    privacy = manifest.get("privacy", {})
    node_transport = manifest.get("transport", {})
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
        "transport": {"type": node_transport.get("type", "local")},
        "online_status": public_node_presence(str(manifest.get("node_id")), mesh_home=mesh_home),
        "privacy": {str(flag): "safe" if value is False else "exposed" for flag, value in sorted(privacy.items())},
    }


def public_nodes(mesh_home: str | Path | None = None) -> list[dict[str, Any]]:
    return [public_node_view(node, mesh_home=mesh_home) for node in list_registered_nodes(mesh_home=mesh_home)]


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


def _count_by_status(records: list[Mapping[str, Any]], key: str = "status") -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        status = str(record.get(key) or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _board_column_for_assignment_status(status: str) -> str:
    if status == "claimed":
        return "claimed"
    if status == "completed":
        return "completed"
    if status == "failed":
        return "failed"
    return "assigned"


def public_board(mesh_home: str | Path | None = None) -> dict[str, Any]:
    """Return a display-safe Capability Mesh Kanban board projection."""

    tasks = list_posted_tasks(mesh_home)
    assignments = list_task_assignments(mesh_home)
    results = list_task_results(mesh_home)
    columns: list[dict[str, Any]] = [
        {"id": "posted", "title": "Triage", "legacy_title": "Posted", "kanban_status": "triage", "description": "New mesh tasks waiting for routing or decomposition.", "cards": []},
        {"id": "assigned", "title": "Ready", "legacy_title": "Assigned", "kanban_status": "ready", "description": "Assignments selected for a node and ready to claim.", "cards": []},
        {"id": "claimed", "title": "Running", "legacy_title": "Claimed", "kanban_status": "running", "description": "Claimed work currently being executed by a node lane.", "cards": []},
        {"id": "completed", "title": "Done", "legacy_title": "Completed", "kanban_status": "done", "description": "Completed assignments with structured handoff metadata.", "cards": []},
        {"id": "failed", "title": "Blocked", "legacy_title": "Failed", "kanban_status": "blocked", "description": "Failed or blocked work that needs human/operator attention.", "cards": []},
        {"id": "results", "title": "Results", "legacy_title": "Results", "kanban_status": "done", "description": "Privacy-filtered result records and summaries.", "cards": []},
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
                "meta": {"node": assignment.get("node_id"), "task": assignment.get("task_id"), "tool_call": assignment.get("tool_call_id")},
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
        "auto_accept_count": sum(1 for node in nodes if node.get("policies", {}).get("auto_accept_task_types")),
    }


def build_dashboard_ui_projection(mesh_home: str | Path | None = None) -> dict[str, Any]:
    """Return a privacy-safe JSON projection for the static frontend."""

    nodes = public_nodes(mesh_home)
    return {
        "title": "Capability Mesh",
        "issue_label": "Open Design dashboard projection",
        "privacy_notice": "Privacy-first view: public counts, lazy node status, filtered board data.",
        "summary": _dashboard_summary(nodes),
        "actions": [
            {"label": "Load registered nodes", "href": "/api/nodes/statuses", "description": "Fetch public node status only when the drawer is opened."},
            {"label": "View board JSON", "href": "/api/board", "description": "Inspect the privacy-filtered Kanban board projection."},
        ],
        "nodes_drawer": {
            "title": "Registered nodes",
            "endpoint": "/api/nodes/statuses",
            "copy": "Node details are lazy-loaded so the dashboard shell stays privacy-light.",
        },
        "kanban": public_board(mesh_home),
    }


def read_static_asset(name: str) -> tuple[bytes, str]:
    if "/" in name or "\\" in name:
        raise FileNotFoundError(name)
    path = STATIC_UI_ROOT / name
    content_type = {
        ".html": "text/html; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
    }.get(path.suffix, "application/octet-stream")
    return path.read_bytes(), content_type


def render_ui_shell(mesh_home: str | Path | None = None) -> str:
    body, _ = read_static_asset("index.html")
    html = body.decode("utf-8")
    projection_json = json.dumps(build_dashboard_ui_projection(mesh_home), ensure_ascii=False).replace("</", "<\\/")
    initial_data = f'<script id="initialProjection" type="application/json">{projection_json}</script>'
    return html.replace('<script src="/static/app.js" defer></script>', f'{initial_data}\n  <script src="/static/app.js" defer></script>')


def render_dashboard(*_: Any, **__: Any) -> str:
    """Compatibility wrapper for legacy dashboard imports."""

    return render_ui_shell()


render_dashboard_html = render_dashboard


__all__ = [
    "build_dashboard_ui_projection",
    "public_board",
    "public_node_statuses",
    "public_node_view",
    "public_nodes",
    "read_static_asset",
    "render_dashboard",
    "render_dashboard_html",
    "render_ui_shell",
]
