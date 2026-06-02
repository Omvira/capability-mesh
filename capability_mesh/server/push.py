"""Push notification delivery records and retry helper."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from capability_mesh.core import default_mesh_home

from capability_mesh.server.audit import utc_now_iso

from capability_mesh.server.outbound import validate_outbound_http_url
from capability_mesh.server.redaction import REDACTED, redact_value


def _delivery_dir(mesh_home: str | Path | None = None) -> Path:
    home = Path(mesh_home).expanduser() if mesh_home is not None else default_mesh_home()
    path = home / "push-deliveries"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _record_path(task_id: str, config_id: str, mesh_home: str | Path | None = None) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in f"{task_id}-{config_id}")
    return _delivery_dir(mesh_home) / f"{safe}.json"


def list_push_delivery_records(task_id: str | None = None, *, mesh_home: str | Path | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(_delivery_dir(mesh_home).glob("*.json")):
        with path.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict) and (task_id is None or loaded.get("task_id") == task_id):
            records.append(loaded)
    return records


def deliver_push_notification(
    *,
    task_id: str,
    task: Mapping[str, Any],
    config: Mapping[str, Any],
    mesh_home: str | Path | None = None,
    bearer_token: str | None = None,
    timeout: float = 5.0,
    max_attempts: int = 2,
    backoff_seconds: float = 0.01,
    allow_private_networks: bool = False,
) -> dict[str, Any]:
    config_id = str(config.get("id") or "default")
    url = validate_outbound_http_url(str(config.get("url") or ""), allow_private_networks=allow_private_networks)
    record: dict[str, Any] = {
        "task_id": task_id,
        "config_id": config_id,
        "url": url,
        "status": "pending",
        "attempts": 0,
        "attempt_records": [],
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "authentication": REDACTED if bearer_token else "",
    }
    body = json.dumps(task, ensure_ascii=False, sort_keys=True).encode("utf-8")
    for attempt in range(1, max(1, max_attempts) + 1):
        headers = {"Content-Type": "application/a2a+json"}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        started = utc_now_iso()
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status_code = int(resp.status)
            attempt_status = "delivered" if 200 <= status_code < 300 else "failed"
        except urllib.error.HTTPError as exc:
            status_code = int(exc.code)
            attempt_status = "failed"
        except (urllib.error.URLError, TimeoutError, OSError):
            status_code = 0
            attempt_status = "failed"
        record["attempts"] = attempt
        record["status"] = attempt_status
        record["updated_at"] = utc_now_iso()
        record["attempt_records"].append({"attempt": attempt, "status": attempt_status, "status_code": status_code, "timestamp": started})
        _record_path(task_id, config_id, mesh_home).write_text(json.dumps(redact_value(record), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if attempt_status == "delivered":
            break
        if attempt < max_attempts:
            time.sleep(backoff_seconds)
    return redact_value(record)


__all__ = ["deliver_push_notification", "list_push_delivery_records"]
