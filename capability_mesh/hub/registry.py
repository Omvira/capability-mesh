"""Hub registry and discovery primitives for node AgentCards."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from capability_mesh.core import CapabilityMeshValidationError, default_mesh_home, validate_capability_manifest
from capability_mesh.hub.relay import build_relay_agent_url
from capability_mesh.node.a2a import build_node_agent_card


def _agent_cards_dir(mesh_home: str | Path | None = None) -> Path:
    base = Path(mesh_home).expanduser() if mesh_home is not None else default_mesh_home()
    return base / "agent-cards"


def _card_node_id(card: Mapping[str, Any]) -> str:
    for interface in card.get("supportedInterfaces", []):
        if isinstance(interface, Mapping):
            url = interface.get("url")
            if isinstance(url, str) and "/relay/nodes/" in url:
                suffix = url.split("/relay/nodes/", 1)[1]
                node_id = suffix.split("/", 1)[0]
                if node_id.strip():
                    return node_id
    raise CapabilityMeshValidationError("agent card relay supportedInterfaces[].url must include /relay/nodes/{node_id}/a2a")


def register_node_agent_card(
    manifest: Mapping[str, Any],
    *,
    hub_url: str | None = None,
    relay_base_url: str | None = None,
    mesh_home: str | Path | None = None,
) -> dict[str, Any]:
    """Validate a node manifest, generate its public AgentCard, and persist it."""

    validated = validate_capability_manifest(manifest)
    relay_url = build_relay_agent_url(relay_base_url, validated["node_id"]) if relay_base_url else None
    card = build_node_agent_card(validated, public_url=hub_url, relay_url=relay_url)
    cards_dir = _agent_cards_dir(mesh_home)
    cards_dir.mkdir(parents=True, exist_ok=True)
    path = cards_dir / f"{validated['node_id']}.yaml"
    path.write_text(yaml.safe_dump(card, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return card


def list_agent_cards(mesh_home: str | Path | None = None) -> list[dict[str, Any]]:
    """List persisted node AgentCards from the Hub registry."""

    cards_dir = _agent_cards_dir(mesh_home)
    if not cards_dir.exists():
        return []
    cards: list[dict[str, Any]] = []
    for path in sorted(cards_dir.glob("*.yaml")):
        with path.open("r", encoding="utf-8") as f:
            card = yaml.safe_load(f) or {}
        if not isinstance(card, dict):
            raise CapabilityMeshValidationError(f"{path} must contain an agent card mapping")
        _card_node_id(card)
        cards.append(card)
    return cards


def find_agent_cards_by_skill(skill_id_or_tag: str, mesh_home: str | Path | None = None) -> list[dict[str, Any]]:
    """Find node AgentCards by skill id, skill name, or skill tag."""

    if not isinstance(skill_id_or_tag, str) or not skill_id_or_tag.strip():
        raise CapabilityMeshValidationError("skill_id_or_tag is required")
    needle = skill_id_or_tag.strip().lower()
    matches: list[dict[str, Any]] = []
    for card in list_agent_cards(mesh_home=mesh_home):
        for skill in card.get("skills", []):
            if not isinstance(skill, Mapping):
                continue
            tags = [str(tag).lower() for tag in skill.get("tags", []) if isinstance(tag, str)]
            values = {str(skill.get("id", "")).lower(), str(skill.get("name", "")).lower(), *tags}
            if needle in values:
                matches.append(card)
                break
    return matches
