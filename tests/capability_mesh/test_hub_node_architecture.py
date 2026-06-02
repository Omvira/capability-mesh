"""Tests for distributed A2A Hub/Node architecture primitives."""

from __future__ import annotations

import importlib
import json

import pytest


def _manifest() -> dict:
    from capability_mesh import build_default_capability_manifest

    manifest = build_default_capability_manifest(
        node_id="alpha-node",
        display_name="Alpha",
        task_types=["test_running", "code_review"],
        tools_available=["pytest", "ruff"],
        dispatch_command=["secret-dispatch"],
    )
    manifest["transport"]["command"] = ["SECRET_TRANSPORT_COMMAND"]
    manifest["transport"]["wake_token"] = "SECRET_WAKE_TOKEN"
    manifest["transport"]["wake_url"] = "https://private.example.invalid/wake"
    manifest["transport"]["type"] = "webhook"
    del manifest["transport"]["command"]
    return manifest


def test_hub_and_node_modules_import():
    for package_name in [
        "capability_mesh.hub",
        "capability_mesh.hub.registry",
        "capability_mesh.hub.relay",
        "capability_mesh.node",
        "capability_mesh.node.a2a",
    ]:
        assert importlib.import_module(package_name) is not None


def test_build_node_agent_card_uses_public_url_and_sanitizes_private_manifest_fields():
    from capability_mesh.node.a2a import build_node_agent_card

    card = build_node_agent_card(_manifest(), public_url="https://node.example.com/a2a")

    assert card["name"] == "Alpha Node"
    assert card["supportedInterfaces"][0]["url"] == "https://node.example.com/a2a"
    assert card["supportedInterfaces"][0]["protocolBinding"] == "https://a2a-protocol.org/bindings/http-json/v1"
    assert card["capabilities"]["streaming"] is False
    assert any(skill["name"] == "test_running" for skill in card["skills"])
    assert any(skill["name"] == "pytest" for skill in card["skills"])
    body = json.dumps(card)
    assert "SECRET_TRANSPORT_COMMAND" not in body
    assert "SECRET_WAKE_TOKEN" not in body
    assert "dispatch_command" not in body
    assert "wake_token" not in body
    assert "transport" not in card.get("metadata", {})


def test_build_node_agent_card_prefers_relay_url():
    from capability_mesh.node.a2a import build_node_agent_card

    card = build_node_agent_card(
        _manifest(),
        public_url="https://node.example.com/a2a",
        relay_url="https://hub.example.com/relay/nodes/alpha-node/a2a",
    )

    assert card["supportedInterfaces"][0]["url"] == "https://hub.example.com/relay/nodes/alpha-node/a2a"


def test_relay_url_shape_and_no_protocol_mutation():
    from capability_mesh.hub.relay import build_relay_agent_url, relay_a2a_request


    assert build_relay_agent_url("https://mesh.example.com/", "alpha-node") == "https://mesh.example.com/relay/nodes/alpha-node/a2a"
    request = {"task": {"id": "task-1"}, "message": {"messageId": "msg-1"}}
    relayed = relay_a2a_request(request)
    assert relayed == request
    assert relayed is not request
    assert relayed["task"] is not request["task"]


def test_relay_url_rejects_unsafe_node_id():
    from capability_mesh import CapabilityMeshValidationError
    from capability_mesh.hub.relay import build_relay_agent_url

    with pytest.raises(CapabilityMeshValidationError):
        build_relay_agent_url("https://mesh.example.com", "../secret")


def test_hub_registry_persists_lists_and_finds_agent_cards(tmp_path):
    from capability_mesh.hub.registry import find_agent_cards_by_skill, list_agent_cards, register_node_agent_card


    card = register_node_agent_card(_manifest(), relay_base_url="https://mesh.example.com/", mesh_home=tmp_path)

    assert card["supportedInterfaces"][0]["url"] == "https://mesh.example.com/relay/nodes/alpha-node/a2a"
    assert (tmp_path / "agent-cards" / "alpha-node.yaml").exists()
    cards = list_agent_cards(mesh_home=tmp_path)
    assert len(cards) == 1
    assert cards[0]["supportedInterfaces"][0]["url"].endswith("/relay/nodes/alpha-node/a2a")
    assert find_agent_cards_by_skill("pytest", mesh_home=tmp_path)[0]["supportedInterfaces"][0]["url"].endswith("/relay/nodes/alpha-node/a2a")
    assert find_agent_cards_by_skill("test_running", mesh_home=tmp_path)[0]["supportedInterfaces"][0]["url"].endswith("/relay/nodes/alpha-node/a2a")
    assert find_agent_cards_by_skill("missing", mesh_home=tmp_path) == []
