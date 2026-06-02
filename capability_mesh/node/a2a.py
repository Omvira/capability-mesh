"""A2A-facing primitives for Capability Mesh nodes."""

from __future__ import annotations

import re
from typing import Any, Mapping

from capability_mesh.core import validate_capability_manifest


_SKILL_ID_SAFE = re.compile(r"[^a-z0-9._-]+")


def _skill_id(prefix: str, value: str) -> str:
    text = _SKILL_ID_SAFE.sub("-", value.strip().lower()).strip("-._")
    return f"{prefix}-{text or 'capability'}"


def build_node_agent_card(
    manifest: Mapping[str, Any],
    *,
    public_url: str | None = None,
    relay_url: str | None = None,
) -> dict[str, Any]:
    """Build a public, privacy-safe AgentCard for one A2A-capable mesh node."""

    validated = validate_capability_manifest(manifest)
    node_id = str(validated["node_id"])
    display_name = str(validated["display_name"])
    url = str(relay_url or public_url or "").rstrip("/")
    capabilities = validated["capabilities"]
    task_types = list(capabilities.get("task_types", []))
    tools = list(capabilities.get("tools_available", []))

    skills: list[dict[str, Any]] = []
    for task_type in task_types:
        skills.append(
            {
                "id": _skill_id(f"{node_id}-task", str(task_type)),
                "name": str(task_type),
                "description": f"Node task capability: {task_type}",
                "tags": ["a2a", "node", "task", str(task_type)],
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["text/plain", "application/json"],
            }
        )
    for tool in tools:
        skills.append(
            {
                "id": _skill_id(f"{node_id}-tool", str(tool)),
                "name": str(tool),
                "description": f"Node tool capability: {tool}",
                "tags": ["a2a", "node", "tool", str(tool)],
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["text/plain", "application/json"],
            }
        )

    return {
        "name": f"{display_name} Node",
        "description": f"Capability Mesh node {node_id} exposing A2A-compatible task and tool operations.",
        "url": url,
        "version": "0.1.0",
        "protocolVersion": "1.0",
        "protocolVersions": ["1.0"],
        "preferredTransport": "HTTP+JSON",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "additionalInterfaces": [
            {
                "protocolBinding": "A2A-HTTP+JSON",
                "transport": "HTTP+JSON",
                "url": f"{url}/message:send" if url else "/message:send",
            }
        ],
        "skills": skills,
        "metadata": {
            "node_id": node_id,
            "architecture": "capability-mesh-node",
        },
    }
