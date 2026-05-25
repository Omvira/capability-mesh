"""HTTP client helpers for HermesMesh services."""

from __future__ import annotations

import json
from typing import Any, Mapping
from urllib import error, request
from urllib.parse import quote, urljoin

from hermes_mesh.core import (
    CapabilityMeshValidationError,
    filter_task_result,
    validate_capability_manifest,
    validate_task_contract,
    validate_task_result_record,
)


class HermesMeshClientError(RuntimeError):
    """Raised when a HermesMesh service request fails."""


def _json_request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any] | list[dict[str, Any]]:
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - caller chooses endpoint
            raw = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HermesMeshClientError(f"{method} {url} failed with HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise HermesMeshClientError(f"{method} {url} failed: {exc.reason}") from exc
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, (dict, list)):
        raise HermesMeshClientError(f"{method} {url} returned non-object JSON")
    return parsed


class HermesMeshClient:
    """Small stdlib HTTP client for the standalone HermesMesh service."""

    def __init__(self, base_url: str, *, timeout: float = 10.0):
        self.base_url = base_url
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        data = _json_request(self.base_url, "/health", timeout=self.timeout)
        if not isinstance(data, dict):
            raise HermesMeshClientError("health endpoint returned a list")
        return data

    def list_nodes(self) -> list[dict[str, Any]]:
        data = _json_request(self.base_url, "/api/nodes", timeout=self.timeout)
        if not isinstance(data, list):
            raise HermesMeshClientError("nodes endpoint returned a non-list")
        return [dict(node) for node in data]

    def get_node(self, node_id: str) -> dict[str, Any]:
        data = _json_request(self.base_url, f"/api/nodes/{quote(node_id)}", timeout=self.timeout)
        if not isinstance(data, dict):
            raise HermesMeshClientError("node endpoint returned a list")
        return data

    def register_node(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        data = _json_request(
            self.base_url,
            "/api/nodes",
            method="POST",
            payload=validate_capability_manifest(manifest),
            timeout=self.timeout,
        )
        if not isinstance(data, dict):
            raise HermesMeshClientError("register endpoint returned a list")
        return data

    def post_task(self, task: Mapping[str, Any]) -> dict[str, Any]:
        data = _json_request(
            self.base_url,
            "/api/tasks",
            method="POST",
            payload=validate_task_contract(task),
            timeout=self.timeout,
        )
        if not isinstance(data, dict):
            raise HermesMeshClientError("post task endpoint returned a list")
        return data

    def route_task(
        self,
        task: Mapping[str, Any],
        *,
        required_tool: str | None = None,
        required_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = {"task": validate_task_contract(task)}
        if required_tools:
            payload["required_tools"] = list(required_tools)
        elif required_tool:
            payload["required_tool"] = required_tool
        data = _json_request(
            self.base_url,
            "/api/tasks/route",
            method="POST",
            payload=payload,
            timeout=self.timeout,
        )
        if not isinstance(data, dict):
            raise HermesMeshClientError("route endpoint returned a list")
        return data

    def record_result(self, result: Mapping[str, Any], task: Mapping[str, Any]) -> dict[str, Any]:
        contract = validate_task_contract(task)
        payload = {"result": filter_task_result(result, contract), "task": contract}
        data = _json_request(
            self.base_url,
            "/api/results",
            method="POST",
            payload=payload,
            timeout=self.timeout,
        )
        if not isinstance(data, dict):
            raise HermesMeshClientError("record result endpoint returned a list")
        if "record" in data and isinstance(data["record"], dict):
            validate_task_result_record(data["record"])
        return data
