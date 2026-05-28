"""HTTP client helpers for Capability Mesh services."""

from __future__ import annotations

import json
from typing import Any, Mapping
from urllib import error, request
from urllib.parse import quote, urljoin

from capability_mesh.core import (
    CapabilityMeshValidationError,
    build_dispatch_result,
    filter_task_result,
    validate_capability_manifest,
    validate_task_contract,
    validate_task_result_record,
)


class HermesMeshClientError(RuntimeError):
    """Raised when a Capability Mesh service request fails."""


def _json_request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any] | list[dict[str, Any]]:
    normalized_path = path if path.startswith("/") else f"/{path}"
    url = base_url.rstrip("/") + normalized_path
    body = None
    headers = {"Accept": "application/a2a+json, application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/a2a+json; charset=utf-8"
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
    """Small stdlib HTTP client for the standalone Capability Mesh service."""

    def __init__(self, base_url: str, *, timeout: float = 10.0):
        self.base_url = base_url
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        data = _json_request(self.base_url, "/health", timeout=self.timeout)
        if not isinstance(data, dict):
            raise HermesMeshClientError("health endpoint returned a list")
        return data

    def server_is_healthy(self) -> bool:
        try:
            return self.health().get("ok") is True
        except HermesMeshClientError:
            return False

    def agent_card(self) -> dict[str, Any]:
        data = _json_request(self.base_url, "/.well-known/agent-card.json", timeout=self.timeout)
        if not isinstance(data, dict):
            raise HermesMeshClientError("agent card endpoint returned a list")
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

    def heartbeat(self, node_id: str, *, status: str = "online") -> dict[str, Any]:
        data = _json_request(
            self.base_url,
            f"/api/nodes/{quote(node_id)}/heartbeat",
            method="POST",
            payload={"status": status},
            timeout=self.timeout,
        )
        if not isinstance(data, dict):
            raise HermesMeshClientError("heartbeat endpoint returned a list")
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

    def plan_task(
        self,
        task: Mapping[str, Any],
        *,
        subtask: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"task": validate_task_contract(task)}
        if subtask is not None:
            payload["subtask"] = dict(subtask)
        data = _json_request(
            self.base_url,
            "/api/tasks/plan",
            method="POST",
            payload=payload,
            timeout=self.timeout,
        )
        if not isinstance(data, dict):
            raise HermesMeshClientError("plan endpoint returned a list")
        return data

    def plan_step(
        self,
        task: Mapping[str, Any],
        *,
        requested_step: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"task": validate_task_contract(task)}
        if requested_step is not None:
            payload["requested_step"] = dict(requested_step)
        data = _json_request(
            self.base_url,
            "/api/tasks/plan-step",
            method="POST",
            payload=payload,
            timeout=self.timeout,
        )
        if not isinstance(data, dict):
            raise HermesMeshClientError("plan-step endpoint returned a list")
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

    def poll_assignments(self, node_id: str) -> list[dict[str, Any]]:
        data = _json_request(
            self.base_url,
            f"/api/nodes/{quote(node_id)}/assignments",
            timeout=self.timeout,
        )
        if not isinstance(data, list):
            raise HermesMeshClientError("node assignments endpoint returned a non-list")
        return [dict(item) for item in data]

    def list_assignments(self) -> list[dict[str, Any]]:
        data = _json_request(self.base_url, "/api/assignments", timeout=self.timeout)
        if not isinstance(data, list):
            raise HermesMeshClientError("assignments endpoint returned a non-list")
        return [dict(item) for item in data]

    def claim_assignment(self, assignment_id: str, node_id: str) -> dict[str, Any]:
        data = _json_request(
            self.base_url,
            f"/api/assignments/{quote(assignment_id)}/claim",
            method="POST",
            payload={"node_id": node_id},
            timeout=self.timeout,
        )
        if not isinstance(data, dict):
            raise HermesMeshClientError("claim endpoint returned a list")
        return data

    def complete_assignment(
        self,
        assignment_id: str,
        node_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        data = _json_request(
            self.base_url,
            f"/api/assignments/{quote(assignment_id)}/complete",
            method="POST",
            payload={"node_id": node_id, "result": dict(result)},
            timeout=self.timeout,
        )
        if not isinstance(data, dict):
            raise HermesMeshClientError("complete endpoint returned a list")
        return data

    def wake_assignment(self, assignment_id: str) -> dict[str, Any]:
        data = _json_request(
            self.base_url,
            f"/api/assignments/{quote(assignment_id)}/wake",
            method="POST",
            payload={},
            timeout=self.timeout,
        )
        if not isinstance(data, dict):
            raise HermesMeshClientError("wake endpoint returned a list")
        return data

    def send_a2a_message(self, message: Mapping[str, Any]) -> dict[str, Any]:
        data = _json_request(
            self.base_url,
            "/message:send",
            method="POST",
            payload={"message": dict(message)},
            timeout=self.timeout,
        )
        if not isinstance(data, dict):
            raise HermesMeshClientError("A2A message endpoint returned a list")
        return data

    def run_next_assignment(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        node = validate_capability_manifest(manifest)
        work = self.poll_assignments(str(node["node_id"]))
        if not work:
            return {"ok": True, "status": "idle", "node_id": node["node_id"]}
        item = work[0]
        assignment = item.get("assignment")
        task = item.get("task")
        if not isinstance(assignment, dict) or not isinstance(task, dict):
            raise HermesMeshClientError("assignment work item is malformed")
        assignment_id = str(assignment.get("assignment_id"))
        self.claim_assignment(assignment_id, str(node["node_id"]))
        result = build_dispatch_result(node, validate_task_contract(task))
        return self.complete_assignment(assignment_id, str(node["node_id"]), result)


CapabilityMeshClientError = HermesMeshClientError
CapabilityMeshClient = HermesMeshClient
