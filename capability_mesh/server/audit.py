"""Structured audit logging for the Capability Mesh Hub."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

from capability_mesh.core import default_mesh_home
from capability_mesh.server.redaction import redact_headers, redact_value


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def record_audit_event(
    *,
    mesh_home: str | Path | None,
    action: str,
    status: str,
    path: str,
    remote_addr: str | None = None,
    node_id: str | None = None,
    headers: Mapping[str, str] | None = None,
    body: Mapping[str, Any] | None = None,
) -> None:
    home = Path(mesh_home).expanduser() if mesh_home is not None else default_mesh_home()
    home.mkdir(parents=True, exist_ok=True)
    event: dict[str, Any] = {
        "timestamp": utc_now_iso(),
        "action": action,
        "status": status,
        "path": path,
        "remote_addr": remote_addr or "",
    }
    if node_id:
        event["node_id"] = node_id
    if headers:
        event["headers"] = redact_headers(headers)
    if body:
        event["body"] = redact_value(body)
    with (home / "audit.log").open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
