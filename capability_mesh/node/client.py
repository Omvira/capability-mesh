"""Node-side A2A HTTP client helper."""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Mapping

from capability_mesh.a2a_compat import validate_agent_card_dict, validate_send_message_response_dict
from capability_mesh.core import CapabilityMeshValidationError, validate_a2a_message


class NodeA2AClient:
    def __init__(self, agent_card: Mapping[str, Any], *, timeout: float = 10.0) -> None:
        self.agent_card = validate_agent_card_dict(agent_card)
        self.timeout = timeout

    @staticmethod
    def fetch_agent_card(base_url: str, *, timeout: float = 10.0) -> dict[str, Any]:
        url = base_url.rstrip("/") + "/.well-known/agent-card.json"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            loaded = json.loads(resp.read().decode("utf-8"))
        if not isinstance(loaded, dict):
            raise CapabilityMeshValidationError("AgentCard response must be an object")
        return validate_agent_card_dict(loaded)

    def send_message(self, message: Mapping[str, Any]) -> dict[str, Any]:
        validated = validate_a2a_message(message)
        endpoint = self._message_send_url()
        body = json.dumps({"message": validated}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/a2a+json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            loaded = json.loads(resp.read().decode("utf-8"))
        if not isinstance(loaded, dict):
            raise CapabilityMeshValidationError("SendMessageResponse must be an object")
        return validate_send_message_response_dict(loaded)

    def _message_send_url(self) -> str:
        for interface in self.agent_card.get("supportedInterfaces", []):
            if isinstance(interface, Mapping):
                url = str(interface.get("url") or "").rstrip("/")
                if url:
                    return url + "/message:send"
        raise CapabilityMeshValidationError("AgentCard has no supportedInterface URL")


__all__ = ["NodeA2AClient"]
