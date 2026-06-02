"""Small file-backed Hub policy engine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from capability_mesh.core import CapabilityMeshValidationError, default_mesh_home


def _policy_path(mesh_home: str | Path | None) -> Path | None:
    home = Path(mesh_home).expanduser() if mesh_home is not None else default_mesh_home()
    for name in ("policy.yaml", "policy.yml", "policy.json"):
        path = home / name
        if path.exists():
            return path
    return None


def load_policy(mesh_home: str | Path | None = None) -> dict[str, Any]:
    path = _policy_path(mesh_home)
    if path is None:
        return {"default": "allow"}
    with path.open("r", encoding="utf-8") as f:
        if path.suffix == ".json":
            loaded = json.load(f)
        else:
            loaded = yaml.safe_load(f) or {}
    if not isinstance(loaded, dict):
        raise CapabilityMeshValidationError("policy file must contain a mapping")
    default = str(loaded.get("default", "deny")).lower()
    if default not in {"allow", "deny"}:
        raise CapabilityMeshValidationError("policy.default must be allow or deny")
    allow = loaded.get("allow", [])
    deny = loaded.get("deny", [])
    if not isinstance(allow, list) or not isinstance(deny, list):
        raise CapabilityMeshValidationError("policy allow/deny must be lists")
    return {"default": default, "allow": [str(item) for item in allow], "deny": [str(item) for item in deny]}


def _matches(pattern: str, action: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith("*"):
        return action.startswith(pattern[:-1])
    return pattern == action


def is_action_allowed(action: str, *, mesh_home: str | Path | None = None) -> bool:
    policy = load_policy(mesh_home)
    deny = list(policy.get("deny", []))
    allow = list(policy.get("allow", []))
    if any(_matches(pattern, action) for pattern in deny):
        return False
    if any(_matches(pattern, action) for pattern in allow):
        return True
    return str(policy.get("default", "deny")) == "allow"
