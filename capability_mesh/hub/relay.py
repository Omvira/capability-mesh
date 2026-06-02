"""Hub relay primitives for distributed A2A node communication."""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from capability_mesh.core import CapabilityMeshValidationError


_SAFE_NODE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_node_id(node_id: str) -> str:
    if not isinstance(node_id, str) or not node_id.strip():
        raise CapabilityMeshValidationError("node_id is required")
    if not _SAFE_NODE_ID.fullmatch(node_id):
        raise CapabilityMeshValidationError("node_id may only contain letters, numbers, dots, underscores, and hyphens")
    return node_id


def build_relay_agent_url(relay_base_url: str, node_id: str) -> str:
    """Build the public Hub relay URL for a node's A2A endpoint."""

    if not isinstance(relay_base_url, str) or not relay_base_url.strip():
        raise CapabilityMeshValidationError("relay_base_url is required")
    base = relay_base_url.rstrip("/")
    if not (base.startswith("http://") or base.startswith("https://")):
        raise CapabilityMeshValidationError("relay_base_url must be an http(s) URL")
    return f"{base}/relay/nodes/{_validate_node_id(node_id)}/a2a"


def relay_a2a_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Return an unchanged relay payload.

    The Hub relay forwards A2A requests without changing Task IDs, message IDs,
    or protocol semantics. Network forwarding is intentionally left to the HTTP
    integration layer; this pure primitive documents and tests the no-mutation
    contract.
    """

    return copy.deepcopy(dict(request))
