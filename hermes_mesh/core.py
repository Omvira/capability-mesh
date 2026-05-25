"""Privacy-first primitives for Capability Mesh Alpha.

Capability Mesh nodes expose task-completion capability, not private
experience.  These helpers intentionally default to local/private behaviour:
no skills, memory, sessions, traces, raw logs, environment variables, or secrets
are shareable unless a future layer adds explicit human-approved workflows.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml

SCHEMA_VERSION = "capability-mesh-alpha-1"

DEFAULT_ALLOWED_RESULT_FIELDS = [
    "final_summary",
    "patch",
    "test_report",
    "generated_file",
    "web_form_verification",
]

DEFAULT_FORBIDDEN_RESULT_FIELDS = [
    "raw_private_logs",
    "environment_variables",
    "secrets",
    "full_session_transcript",
    "private_memory",
    "reasoning_trace",
    "local_skills",
]

TASK_ASSIGNMENT_STATUSES = {"auto_assigned", "awaiting_node_approval", "claimed", "declined", "completed", "failed"}
TASK_RESULT_STATUSES = {"completed", "failed"}
CONTRIBUTION_VISIBILITIES = {"none", "local_private", "team_registry", "public_commons"}
PLAN_STEP_KINDS = {"server_tool_call", "node_tool_call", "orchestration_tool_call"}
PLAN_STEP_ACTIONS = {"invoke_server_tool", "invoke_node", "orchestration_action", "completed", "no_match"}
SERVER_LOCAL_TOOLS = {"aggregate_results", "verify_result", "echo_sanitized"}
SERVER_TOOL_NODE_PRIVATE_FIELDS = {
    "transport",
    "transport_command",
    "dispatch_command",
    "command",
    "node_private_transport",
    "node_dispatch_command",
}

PRIVATE_PRIVACY_FLAGS = {
    "expose_local_skills": False,
    "expose_memory": False,
    "expose_session_history": False,
    "expose_reasoning_trace": False,
    "expose_raw_logs": False,
    "expose_environment": False,
}

PUBLIC_SKILL_VISIBILITIES = {"team_registry", "public_commons"}
LOCAL_SKILL_VISIBILITIES = {"none", "local_private"}
_ALLOWED_SKILL_VISIBILITIES = LOCAL_SKILL_VISIBILITIES | PUBLIC_SKILL_VISIBILITIES

_SECRET_PATTERNS = [
    # OpenAI-like keys and similarly long bearer tokens.
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+", re.I),
]

_SAFE_NODE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_SAFE_SSH_HOST = re.compile(r"^[A-Za-z0-9_.:-]+$")
_SAFE_SSH_USER = re.compile(r"^[A-Za-z0-9_.-]+$")
DEFAULT_TRANSPORT_COMMAND = [sys.executable, "-c", "print('capability-mesh')"]
DEFAULT_TRANSPORT_TIMEOUT = 10


class CapabilityMeshValidationError(ValueError):
    """Raised when a Capability Mesh Alpha object violates privacy rules."""


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CapabilityMeshValidationError(f"{name} must be a mapping")
    return value


def _require_non_empty_string(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise CapabilityMeshValidationError(f"{field} must be a non-empty string")


def _require_non_empty_string_list(value: Any, field: str) -> None:
    if not isinstance(value, list) or not value:
        raise CapabilityMeshValidationError(f"{field} must be a non-empty list")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise CapabilityMeshValidationError(f"{field} must contain only non-empty strings")


def _require_schema_version(obj: Mapping[str, Any]) -> None:
    if obj.get("schema_version") != SCHEMA_VERSION:
        raise CapabilityMeshValidationError(
            f"schema_version must be {SCHEMA_VERSION!r}"
        )


def build_default_capability_manifest(
    *,
    node_id: str,
    display_name: str,
    task_types: list[str],
    tools_available: list[str],
    resources: Mapping[str, Any] | None = None,
    requires_human_approval: bool = True,
    transport_command: list[str] | None = None,
    dispatch_command: list[str] | None = None,
) -> dict[str, Any]:
    """Build a valid privacy-first node capability manifest."""

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "node_id": node_id,
        "display_name": display_name,
        "capabilities": {
            "task_types": list(task_types),
            "tools_available": list(tools_available),
            "resources": dict(resources or {}),
        },
        "policies": {
            "accepts_tasks": True,
            "auto_accept_task_types": [],
            "requires_human_approval": bool(requires_human_approval),
        },
        "transport": {
            "type": "local",
            "command": list(transport_command or DEFAULT_TRANSPORT_COMMAND),
            "timeout_seconds": DEFAULT_TRANSPORT_TIMEOUT,
        },
        "privacy": dict(PRIVATE_PRIVACY_FLAGS),
        "result_policy": {
            "allow": list(DEFAULT_ALLOWED_RESULT_FIELDS),
            "deny": list(DEFAULT_FORBIDDEN_RESULT_FIELDS),
        },
    }
    if dispatch_command is not None:
        manifest["transport"]["dispatch_command"] = list(dispatch_command)
    return validate_capability_manifest(manifest)


def validate_capability_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a node Capability Manifest.

    Alpha rejects any manifest that claims to expose private local state.  This
    makes the protocol safe-by-default while later versions can add explicit,
    reviewed exceptions if needed.
    """

    manifest = _require_mapping(manifest, "manifest")
    _require_schema_version(manifest)
    _require_non_empty_string(manifest.get("node_id"), "node_id")
    if not _SAFE_NODE_ID.fullmatch(str(manifest.get("node_id"))):
        raise CapabilityMeshValidationError(
            "node_id may only contain letters, numbers, dots, underscores, and hyphens"
        )
    _require_non_empty_string(manifest.get("display_name"), "display_name")

    capabilities = _require_mapping(manifest.get("capabilities"), "capabilities")
    _require_non_empty_string_list(capabilities.get("task_types"), "capabilities.task_types")
    _require_non_empty_string_list(
        capabilities.get("tools_available"), "capabilities.tools_available"
    )

    policies = _require_mapping(manifest.get("policies", {}), "policies")
    accepts_tasks = policies.get("accepts_tasks", True)
    if accepts_tasks not in {True, False}:
        raise CapabilityMeshValidationError("policies.accepts_tasks must be boolean")
    requires_human_approval = policies.get("requires_human_approval", True)
    if requires_human_approval not in {True, False}:
        raise CapabilityMeshValidationError("policies.requires_human_approval must be boolean")
    auto_accept = policies.get("auto_accept_task_types", [])
    if auto_accept != []:
        _require_non_empty_string_list(auto_accept, "policies.auto_accept_task_types")
    unknown_auto_accept = set(auto_accept) - set(capabilities.get("task_types", []))
    if unknown_auto_accept:
        raise CapabilityMeshValidationError(
            "policies.auto_accept_task_types must be declared in capabilities.task_types: "
            + ", ".join(sorted(unknown_auto_accept))
        )

    privacy = _require_mapping(manifest.get("privacy"), "privacy")
    for flag, expected in PRIVATE_PRIVACY_FLAGS.items():
        if privacy.get(flag) is not expected:
            raise CapabilityMeshValidationError(
                f"privacy.{flag} must be {expected}; private state is not shared by default"
            )

    result_policy = _require_mapping(manifest.get("result_policy"), "result_policy")
    _require_non_empty_string_list(result_policy.get("allow"), "result_policy.allow")
    _require_non_empty_string_list(result_policy.get("deny"), "result_policy.deny")
    missing_denies = set(DEFAULT_FORBIDDEN_RESULT_FIELDS) - set(result_policy.get("deny", []))
    if missing_denies:
        raise CapabilityMeshValidationError(
            "result_policy.deny must include privacy-forbidden fields: "
            + ", ".join(sorted(missing_denies))
        )
    validated = copy.deepcopy(dict(manifest))
    validated["policies"] = {
        "accepts_tasks": bool(accepts_tasks),
        "auto_accept_task_types": list(auto_accept),
        "requires_human_approval": bool(requires_human_approval),
    }
    validated["transport"] = validate_transport_metadata(
        validated.get(
            "transport",
            {
                "type": "local",
                "command": list(DEFAULT_TRANSPORT_COMMAND),
                "timeout_seconds": DEFAULT_TRANSPORT_TIMEOUT,
            },
        )
    )
    return validated


def validate_transport_metadata(transport: Mapping[str, Any]) -> dict[str, Any]:
    """Validate local/ssh transport metadata without accepting shell strings."""

    transport = _require_mapping(transport, "transport")
    transport_type = transport.get("type", "local")
    if transport_type not in {"local", "ssh"}:
        raise CapabilityMeshValidationError("transport.type must be 'local' or 'ssh'")

    command = transport.get("command", DEFAULT_TRANSPORT_COMMAND)
    if not isinstance(command, list) or not command:
        raise CapabilityMeshValidationError("transport.command must be a non-empty list")
    if not all(isinstance(part, str) and part.strip() for part in command):
        raise CapabilityMeshValidationError("transport.command must contain only non-empty strings")

    timeout = transport.get("timeout_seconds", DEFAULT_TRANSPORT_TIMEOUT)
    if not isinstance(timeout, int) or timeout < 1 or timeout > 300:
        raise CapabilityMeshValidationError("transport.timeout_seconds must be an integer from 1 to 300")

    validated: dict[str, Any] = {
        "type": transport_type,
        "command": list(command),
        "timeout_seconds": timeout,
    }
    dispatch_command = transport.get("dispatch_command")
    if dispatch_command is not None:
        if not isinstance(dispatch_command, list) or not dispatch_command:
            raise CapabilityMeshValidationError("transport.dispatch_command must be a non-empty list")
        if not all(isinstance(part, str) and part.strip() for part in dispatch_command):
            raise CapabilityMeshValidationError("transport.dispatch_command must contain only non-empty strings")
        validated["dispatch_command"] = list(dispatch_command)
    if transport_type == "ssh":
        _require_non_empty_string(transport.get("host"), "transport.host")
        host = str(transport["host"])
        if not _SAFE_SSH_HOST.fullmatch(host):
            raise CapabilityMeshValidationError("transport.host contains unsafe characters")
        validated["host"] = host
        if transport.get("user") is not None:
            user = str(transport["user"])
            if not _SAFE_SSH_USER.fullmatch(user):
                raise CapabilityMeshValidationError("transport.user contains unsafe characters")
            validated["user"] = user
        port = transport.get("port")
        if port is not None:
            if not isinstance(port, int) or port < 1 or port > 65535:
                raise CapabilityMeshValidationError("transport.port must be an integer from 1 to 65535")
            validated["port"] = port
    return validated


def default_mesh_home() -> Path:
    """Return the standalone mesh home directory.

    The core package is intentionally independent from Hermes.  Standalone users
    can set HERMES_MESH_HOME; otherwise mesh state lives under ~/.hermes-mesh.
    Hermes-specific adapters may pass an explicit mesh_home to registry helpers.
    """

    import os

    configured = os.environ.get("HERMES_MESH_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".hermes-mesh"


def capability_mesh_nodes_dir(mesh_home: str | Path | None = None) -> Path:
    """Return the node manifest registry directory."""

    base = Path(mesh_home).expanduser() if mesh_home is not None else default_mesh_home()
    return base / "nodes"


def _mesh_registry_dir(name: str, mesh_home: str | Path | None = None) -> Path:
    base = Path(mesh_home).expanduser() if mesh_home is not None else default_mesh_home()
    return base / name


def _safe_record_id(value: Any, field: str) -> str:
    _require_non_empty_string(value, field)
    text = str(value)
    if not _SAFE_NODE_ID.fullmatch(text):
        raise CapabilityMeshValidationError(
            f"{field} may only contain letters, numbers, dots, underscores, and hyphens"
        )
    return text


def _safe_tool_name(value: Any, field: str) -> str:
    return _safe_record_id(value, field)


def _contains_private_command_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in SERVER_TOOL_NODE_PRIVATE_FIELDS:
                return True
            if _contains_private_command_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_private_command_key(item) for item in value)
    return False


def _write_registry_record(
    dirname: str,
    record_id: str,
    data: Mapping[str, Any],
    mesh_home: str | Path | None = None,
) -> Path:
    path_id = _safe_record_id(record_id, "record_id")
    registry_dir = _mesh_registry_dir(dirname, mesh_home)
    registry_dir.mkdir(parents=True, exist_ok=True)
    path = registry_dir / f"{path_id}.yaml"
    path.write_text(
        yaml.safe_dump(dict(data), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _list_registry_records(
    dirname: str,
    validator: Any,
    mesh_home: str | Path | None = None,
) -> list[dict[str, Any]]:
    registry_dir = _mesh_registry_dir(dirname, mesh_home)
    if not registry_dir.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(registry_dir.glob("*.yaml")):
        with path.open("r", encoding="utf-8") as f:
            records.append(validator(yaml.safe_load(f) or {}))
    return records


def register_node_manifest(manifest: Mapping[str, Any], mesh_home: str | Path | None = None) -> Path:
    """Validate and persist a node manifest in the local file registry."""

    validated = validate_capability_manifest(manifest)
    nodes_dir = capability_mesh_nodes_dir(mesh_home)
    nodes_dir.mkdir(parents=True, exist_ok=True)
    path = nodes_dir / f"{validated['node_id']}.yaml"
    path.write_text(
        yaml.safe_dump(validated, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def list_registered_nodes(mesh_home: str | Path | None = None) -> list[dict[str, Any]]:
    """Return validated node manifests from the local file registry."""

    nodes_dir = capability_mesh_nodes_dir(mesh_home)
    if not nodes_dir.exists():
        return []

    nodes: list[dict[str, Any]] = []
    for path in sorted(nodes_dir.glob("*.yaml")):
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        nodes.append(validate_capability_manifest(data))
    return nodes


def get_registered_node(node_id: str, mesh_home: str | Path | None = None) -> dict[str, Any]:
    """Return one registered node by id."""

    for node in list_registered_nodes(mesh_home):
        if node.get("node_id") == node_id:
            return node
    raise CapabilityMeshValidationError(f"unknown node_id: {node_id}")


def post_task(task: Mapping[str, Any], mesh_home: str | Path | None = None) -> Path:
    """Validate and persist a task post in the local registry."""

    validated = validate_task_post(task)
    return _write_registry_record("tasks", validated["task_id"], validated, mesh_home)


def list_posted_tasks(mesh_home: str | Path | None = None) -> list[dict[str, Any]]:
    """Return validated task posts from the local registry."""

    return _list_registry_records("tasks", validate_task_post, mesh_home)


def record_task_assignment(assignment: Mapping[str, Any], mesh_home: str | Path | None = None) -> Path:
    """Persist a validated local task assignment."""

    validated = validate_task_assignment(assignment)
    return _write_registry_record("assignments", validated["assignment_id"], validated, mesh_home)


def list_task_assignments(mesh_home: str | Path | None = None) -> list[dict[str, Any]]:
    """Return validated local task assignments."""

    return _list_registry_records("assignments", validate_task_assignment, mesh_home)


def get_task_assignment(assignment_id: str, mesh_home: str | Path | None = None) -> dict[str, Any]:
    """Return one assignment by id."""

    for assignment in list_task_assignments(mesh_home=mesh_home):
        if assignment.get("assignment_id") == assignment_id:
            return assignment
    raise CapabilityMeshValidationError(f"unknown assignment_id: {assignment_id}")


def get_posted_task(task_id: str, mesh_home: str | Path | None = None) -> dict[str, Any]:
    """Return one posted task by id."""

    for task in list_posted_tasks(mesh_home=mesh_home):
        if task.get("task_id") == task_id:
            return task
    raise CapabilityMeshValidationError(f"unknown task_id: {task_id}")


def list_node_assignments(node_id: str, mesh_home: str | Path | None = None) -> list[dict[str, Any]]:
    """Return pending work items assigned to a node, including the task contract."""

    _safe_record_id(node_id, "node_id")
    work: list[dict[str, Any]] = []
    for assignment in list_task_assignments(mesh_home=mesh_home):
        if assignment.get("node_id") != node_id:
            continue
        if assignment.get("status") not in {"auto_assigned", "awaiting_node_approval", "claimed"}:
            continue
        assigned_task = assignment.get("tool_call")
        if assigned_task is not None:
            assigned_task = validate_task_contract(_require_mapping(assigned_task, "tool_call"))
        else:
            assigned_task = get_posted_task(str(assignment["task_id"]), mesh_home=mesh_home)
        work.append(
            {
                "assignment": assignment,
                "task": assigned_task,
            }
        )
    return work


def claim_task_assignment(
    assignment_id: str,
    node_id: str,
    mesh_home: str | Path | None = None,
) -> dict[str, Any]:
    """Mark an assigned work item as claimed by its assigned node."""

    assignment = get_task_assignment(assignment_id, mesh_home=mesh_home)
    if assignment.get("node_id") != node_id:
        raise CapabilityMeshValidationError("assignment is not assigned to node_id")
    if assignment.get("status") not in {"auto_assigned", "awaiting_node_approval", "claimed"}:
        raise CapabilityMeshValidationError("assignment is not claimable")
    claimed = dict(assignment)
    claimed["status"] = "claimed"
    record_task_assignment(claimed, mesh_home=mesh_home)
    return validate_task_assignment(claimed)


def record_task_result(
    result: Mapping[str, Any],
    contract: Mapping[str, Any],
    mesh_home: str | Path | None = None,
) -> Path:
    """Build, privacy-filter, validate, and persist a task result record."""

    record = build_task_result_record(result, contract)
    return _write_registry_record("results", record["result_id"], record, mesh_home)


def list_task_results(mesh_home: str | Path | None = None) -> list[dict[str, Any]]:
    """Return validated task result records."""

    return _list_registry_records("results", validate_task_result_record, mesh_home)


def complete_task_assignment(
    assignment_id: str,
    node_id: str,
    result: Mapping[str, Any],
    mesh_home: str | Path | None = None,
) -> dict[str, Any]:
    """Record node output, update contribution/assignment state, and choose next action."""

    assignment = get_task_assignment(assignment_id, mesh_home=mesh_home)
    if assignment.get("node_id") != node_id:
        raise CapabilityMeshValidationError("assignment is not assigned to node_id")
    assigned_task = assignment.get("tool_call")
    if assigned_task is not None:
        task = validate_task_contract(_require_mapping(assigned_task, "tool_call"))
    else:
        task = get_posted_task(str(assignment["task_id"]), mesh_home=mesh_home)
    raw = dict(_require_mapping(result, "result"))
    raw["task_id"] = task["task_id"]
    raw["node_id"] = node_id
    result_record = build_task_result_record(raw, task)
    record_task_result(raw, task, mesh_home=mesh_home)

    finished = dict(assignment)
    finished["status"] = "completed" if result_record["status"] == "completed" else "failed"
    record_task_assignment(finished, mesh_home=mesh_home)

    verification = result_record.get("verification_report", {})
    contribution = {
        "schema_version": SCHEMA_VERSION,
        "contribution_id": f"{task['task_id']}-{node_id}-contribution",
        "task_id": task["task_id"],
        "node_id": node_id,
        "summary": str(result_record.get("result", {}).get("final_summary") or "Task result recorded"),
        "visibility": "local_private",
        "human_consent": False,
        "verification_report": verification,
    }
    record_contribution(contribution, mesh_home=mesh_home)

    if raw.get("needs_more_results") is True or raw.get("partial") is True:
        decision: dict[str, Any] = {"action": "awaiting_more_results", "reason": "node reported partial result"}
    elif result_record["status"] == "completed" and verification.get("status") == "passed":
        decision = {"action": "completed", "reason": "result completed and verification passed"}
    else:
        next_assignment = _build_route_next_tool_call_assignment(task, node_id, mesh_home=mesh_home)
        if next_assignment is not None:
            record_task_assignment(next_assignment, mesh_home=mesh_home)
            decision = {
                "action": "route_next",
                "reason": "current result failed or did not verify",
                "next_assignment": next_assignment,
            }
        else:
            remaining_nodes = [
                manifest
                for manifest in list_registered_nodes(mesh_home=mesh_home)
                if manifest.get("node_id") != node_id
            ]
            next_route = route_task(task, remaining_nodes, required_tools=list(task.get("required_tools", [])))
            if next_route.get("selected_node"):
                next_assignment = build_task_assignment(task, next_route)
                record_task_assignment(next_assignment, mesh_home=mesh_home)
                decision = {
                    "action": "route_next",
                    "reason": "current result failed or did not verify",
                    "route": next_route,
                    "next_assignment": next_assignment,
                }
            else:
                decision = {
                    "action": "no_match",
                    "reason": next_route.get("reason", "no remaining matching nodes"),
                    "route": next_route,
                }
    return {
        "assignment": validate_task_assignment(finished),
        "result_record": result_record,
        "contribution": validate_contribution_record(contribution),
        "decision": decision,
    }


def complete_node_tool_call(
    tool_call_id: str,
    node_id: str,
    result: Mapping[str, Any],
    mesh_home: str | Path | None = None,
) -> dict[str, Any]:
    """Complete one server-planned node tool call/subtask assignment."""

    return complete_task_assignment(tool_call_id, node_id, result, mesh_home=mesh_home)


def _next_tool_call_index(parent_task_id: str, mesh_home: str | Path | None = None) -> int:
    max_index = 0
    prefix = f"{parent_task_id}-"
    marker = "-call-"
    for assignment in list_task_assignments(mesh_home=mesh_home):
        if assignment.get("parent_task_id") != parent_task_id:
            continue
        tool_call_id = str(assignment.get("tool_call_id") or assignment.get("assignment_id", ""))
        if not tool_call_id.startswith(prefix) or marker not in tool_call_id:
            continue
        try:
            max_index = max(max_index, int(tool_call_id.rsplit(marker, 1)[1]))
        except ValueError:
            continue
    for result in list_task_results(mesh_home=mesh_home):
        for value in (str(result.get("task_id", "")), str(result.get("result_id", ""))):
            if not value.startswith(prefix) or marker not in value:
                continue
            try:
                max_index = max(max_index, int(value.rsplit(marker, 1)[1].split("-", 1)[0]))
            except ValueError:
                continue
    return max_index + 1


def _build_route_next_tool_call_assignment(
    task: Mapping[str, Any],
    node_id: str,
    mesh_home: str | Path | None = None,
) -> dict[str, Any] | None:
    if not task.get("parent_task_id"):
        return None
    parent_task_id = str(task["parent_task_id"])
    try:
        parent_task = get_posted_task(parent_task_id, mesh_home=mesh_home)
    except CapabilityMeshValidationError:
        return None
    remaining_nodes = [
        manifest
        for manifest in list_registered_nodes(mesh_home=mesh_home)
        if manifest.get("node_id") != node_id
    ]
    route_contract = dict(parent_task)
    route_contract["task_type"] = task["task_type"]
    route = route_task(route_contract, remaining_nodes, required_tools=list(task.get("required_tools", [])))
    if not route.get("selected_node"):
        return None
    subtask = {
        "objective": task["objective"],
        "inputs": copy.deepcopy(task.get("inputs", {})),
        "task_type": task["task_type"],
        "required_tools": list(task.get("required_tools", [])),
    }
    tool_call = build_node_tool_call(
        parent_task,
        route,
        subtask=subtask,
        call_index=_next_tool_call_index(parent_task_id, mesh_home=mesh_home),
    )
    return build_node_tool_call_assignment(parent_task, route, tool_call)


def record_contribution(contribution: Mapping[str, Any], mesh_home: str | Path | None = None) -> Path:
    """Persist an explicit contribution record in the local registry."""

    validated = validate_contribution_record(contribution)
    return _write_registry_record("contributions", validated["contribution_id"], validated, mesh_home)


def list_contribution_records(mesh_home: str | Path | None = None) -> list[dict[str, Any]]:
    """Return validated contribution records."""

    return _list_registry_records("contributions", validate_contribution_record, mesh_home)


def _transport_command(transport: Mapping[str, Any]) -> list[str]:
    validated = validate_transport_metadata(transport)
    command = list(validated["command"])
    if validated["type"] == "local":
        return command

    target = validated["host"]
    if validated.get("user"):
        target = f"{validated['user']}@{target}"
    ssh_command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={validated['timeout_seconds']}",
    ]
    if validated.get("port"):
        ssh_command.extend(["-p", str(validated["port"])])
    ssh_command.append(target)
    ssh_command.extend(command)
    return ssh_command


def _run_transport(manifest: Mapping[str, Any], command: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    validated = validate_capability_manifest(manifest)
    transport = dict(validated["transport"])
    if command is not None:
        transport["command"] = command
    timeout = int(transport.get("timeout_seconds", DEFAULT_TRANSPORT_TIMEOUT))
    return subprocess.run(
        _transport_command(transport),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def ping_node(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Run a harmless transport command and return node health metadata."""

    validated = validate_capability_manifest(manifest)
    try:
        result = _run_transport(validated)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "node_id": validated["node_id"],
            "transport": validated["transport"]["type"],
            "ok": False,
            "status": "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "error",
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
        }
    return {
        "node_id": validated["node_id"],
        "transport": validated["transport"]["type"],
        "ok": result.returncode == 0,
        "status": "online" if result.returncode == 0 else "error",
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def validate_task_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a task contract submitted to the mesh."""

    contract = _require_mapping(contract, "contract")
    _require_schema_version(contract)
    for field in ("task_id", "task_type", "objective"):
        _require_non_empty_string(contract.get(field), field)
    _require_non_empty_string_list(
        contract.get("allowed_result_fields"), "allowed_result_fields"
    )
    forbidden = contract.get("forbidden_result_fields")
    if forbidden is None:
        raise CapabilityMeshValidationError("forbidden_result_fields is required")
    _require_non_empty_string_list(forbidden, "forbidden_result_fields")
    expected_fields = contract.get("expected_fields")
    if expected_fields is not None:
        _require_non_empty_string_list(expected_fields, "expected_fields")
    parent_task_id = contract.get("parent_task_id")
    if parent_task_id is not None:
        _require_non_empty_string(parent_task_id, "parent_task_id")
    tool_call_id = contract.get("tool_call_id")
    if tool_call_id is not None:
        _safe_record_id(tool_call_id, "tool_call_id")
    assigned_node_id = contract.get("assigned_node_id")
    if assigned_node_id is not None:
        _safe_record_id(assigned_node_id, "assigned_node_id")
    return copy.deepcopy(dict(contract))


def validate_task_post(post: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a posted task contract with privacy-preserving defaults."""

    validated = validate_task_contract(post)
    submitter = validated.get("submitter")
    if submitter is not None:
        _require_non_empty_string(submitter, "submitter")
    if validated.get("required_tools") is not None:
        _require_non_empty_string_list(validated["required_tools"], "required_tools")
    return validated


def validate_task_assignment(assignment: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a local task assignment routing decision."""

    assignment = _require_mapping(assignment, "assignment")
    _require_schema_version(assignment)
    for field in ("assignment_id", "task_id", "task_type", "node_id", "status", "reason"):
        _require_non_empty_string(assignment.get(field), field)
    _safe_record_id(assignment["assignment_id"], "assignment_id")
    if assignment["status"] not in TASK_ASSIGNMENT_STATUSES:
        raise CapabilityMeshValidationError(
            "status must be one of: " + ", ".join(sorted(TASK_ASSIGNMENT_STATUSES))
        )
    candidates = assignment.get("candidates", [])
    if candidates != []:
        _require_non_empty_string_list(candidates, "candidates")
    if assignment.get("parent_task_id") is not None:
        _require_non_empty_string(assignment["parent_task_id"], "parent_task_id")
    if assignment.get("tool_call_id") is not None:
        _safe_record_id(assignment["tool_call_id"], "tool_call_id")
    if assignment.get("tool_call") is not None:
        validate_task_contract(_require_mapping(assignment["tool_call"], "tool_call"))
    return copy.deepcopy(dict(assignment))


def validate_task_result_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a stored, privacy-filtered task result record."""

    record = _require_mapping(record, "task_result")
    _require_schema_version(record)
    for field in ("result_id", "task_id", "node_id", "status"):
        _require_non_empty_string(record.get(field), field)
    _safe_record_id(record["result_id"], "result_id")
    if record["status"] not in TASK_RESULT_STATUSES:
        raise CapabilityMeshValidationError(
            "status must be one of: " + ", ".join(sorted(TASK_RESULT_STATUSES))
        )
    _require_mapping(record.get("result"), "result")
    if record.get("verification_report") is not None:
        validate_verification_report(record["verification_report"])
    return copy.deepcopy(dict(record))


def validate_contribution_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an explicit contribution record without adding reward semantics."""

    record = _require_mapping(record, "contribution_record")
    _require_schema_version(record)
    for field in ("contribution_id", "task_id", "node_id", "summary", "visibility"):
        _require_non_empty_string(record.get(field), field)
    _safe_record_id(record["contribution_id"], "contribution_id")
    if record["visibility"] not in CONTRIBUTION_VISIBILITIES:
        raise CapabilityMeshValidationError(
            "visibility must be one of: " + ", ".join(sorted(CONTRIBUTION_VISIBILITIES))
        )
    if record["visibility"] in PUBLIC_SKILL_VISIBILITIES:
        if record.get("human_consent") is not True:
            raise CapabilityMeshValidationError(
                "human_consent is required for team/public contribution records"
            )
        _require_non_empty_string(record.get("human_review_note"), "human_review_note")
    else:
        human_consent = record.get("human_consent", False)
        if human_consent not in {True, False}:
            raise CapabilityMeshValidationError("human_consent must be boolean")
    verification = record.get("verification_report")
    if verification is not None:
        validate_verification_report(verification)
    return copy.deepcopy(dict(record))


def route_task(
    contract: Mapping[str, Any],
    manifests: list[Mapping[str, Any]],
    required_tools: list[str] | None = None,
) -> dict[str, Any]:
    """Deterministically route a task to a capable manifest without dispatching it."""

    validated_contract = validate_task_post(contract)
    if required_tools is None:
        required_tools = list(validated_contract.get("required_tools", []))
    elif required_tools:
        _require_non_empty_string_list(required_tools, "required_tools")
    required_tool_set = set(required_tools or [])

    candidates: list[dict[str, Any]] = []
    reasons: list[str] = []
    for manifest in manifests:
        node = validate_capability_manifest(manifest)
        node_id = node["node_id"]
        capabilities = node["capabilities"]
        policies = node["policies"]
        if policies.get("accepts_tasks") is not True:
            reasons.append(f"{node_id}: does not accept tasks")
            continue
        if validated_contract["task_type"] not in capabilities.get("task_types", []):
            reasons.append(f"{node_id}: task_type not declared")
            continue
        tools_available = set(capabilities.get("tools_available", []))
        missing_tools = sorted(required_tool_set - tools_available)
        if missing_tools:
            reasons.append(f"{node_id}: missing tools {', '.join(missing_tools)}")
            continue
        candidates.append(node)

    candidates.sort(key=lambda item: item["node_id"])
    candidate_ids = [node["node_id"] for node in candidates]
    if not candidates:
        return {
            "schema_version": SCHEMA_VERSION,
            "task_id": validated_contract["task_id"],
            "task_type": validated_contract["task_type"],
            "status": "no_match",
            "selected_node": None,
            "candidates": [],
            "reason": "; ".join(reasons) or "no manifests available",
        }

    selected = candidates[0]
    auto_accept = validated_contract["task_type"] in selected["policies"].get(
        "auto_accept_task_types", []
    )
    status = "auto_assigned" if auto_accept else "awaiting_node_approval"
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": validated_contract["task_id"],
        "task_type": validated_contract["task_type"],
        "status": status,
        "selected_node": selected["node_id"],
        "candidates": candidate_ids,
        "reason": "selected lowest node_id among matching candidates",
    }


def build_task_assignment(contract: Mapping[str, Any], route: Mapping[str, Any]) -> dict[str, Any]:
    """Build a persisted assignment from a route_task decision."""

    validated_contract = validate_task_post(contract)
    route_data = _require_mapping(route, "route")
    selected_node = route_data.get("selected_node")
    _require_non_empty_string(selected_node, "selected_node")
    assignment = {
        "schema_version": SCHEMA_VERSION,
        "assignment_id": f"{validated_contract['task_id']}-{selected_node}",
        "task_id": validated_contract["task_id"],
        "task_type": validated_contract["task_type"],
        "node_id": selected_node,
        "status": route_data.get("status"),
        "candidates": list(route_data.get("candidates", [])),
        "reason": route_data.get("reason", "routed locally"),
    }
    return validate_task_assignment(assignment)


def build_node_tool_call(
    parent_contract: Mapping[str, Any],
    route: Mapping[str, Any],
    *,
    subtask: Mapping[str, Any] | None = None,
    call_index: int = 1,
) -> dict[str, Any]:
    """Build the task contract for one node capability call planned by the server."""

    parent = validate_task_post(parent_contract)
    route_data = _require_mapping(route, "route")
    selected_node = route_data.get("selected_node")
    _require_non_empty_string(selected_node, "selected_node")
    if call_index < 1:
        raise CapabilityMeshValidationError("call_index must be greater than zero")
    subtask_data = dict(_require_mapping(subtask or {}, "subtask"))
    required_tools = subtask_data.get("required_tools", parent.get("required_tools", []))
    if required_tools:
        _require_non_empty_string_list(required_tools, "subtask.required_tools")
    tool_call_id = str(subtask_data.get("tool_call_id") or f"{parent['task_id']}-{selected_node}-call-{call_index}")
    _safe_record_id(tool_call_id, "tool_call_id")
    tool_call = {
        "schema_version": SCHEMA_VERSION,
        "task_id": tool_call_id,
        "parent_task_id": parent["task_id"],
        "tool_call_id": tool_call_id,
        "node_id": selected_node,
        "assigned_node_id": selected_node,
        "task_type": str(subtask_data.get("task_type") or parent["task_type"]),
        "objective": str(subtask_data.get("objective") or parent["objective"]),
        "inputs": copy.deepcopy(subtask_data.get("inputs", parent.get("inputs", {}))),
        "allowed_result_fields": list(parent["allowed_result_fields"]),
        "forbidden_result_fields": list(parent["forbidden_result_fields"]),
    }
    if required_tools:
        tool_call["required_tools"] = list(required_tools)
    if parent.get("expected_fields") is not None:
        tool_call["expected_fields"] = list(parent["expected_fields"])
    return validate_task_contract(tool_call)


def build_node_tool_call_assignment(
    parent_contract: Mapping[str, Any],
    route: Mapping[str, Any],
    tool_call: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a persisted assignment for one server-planned node tool call."""

    parent = validate_task_post(parent_contract)
    call = validate_task_contract(tool_call)
    route_data = _require_mapping(route, "route")
    selected_node = route_data.get("selected_node")
    _require_non_empty_string(selected_node, "selected_node")
    assignment = {
        "schema_version": SCHEMA_VERSION,
        "assignment_id": call["task_id"],
        "task_id": parent["task_id"],
        "parent_task_id": parent["task_id"],
        "tool_call_id": call["task_id"],
        "task_type": call["task_type"],
        "node_id": selected_node,
        "status": route_data.get("status"),
        "candidates": list(route_data.get("candidates", [])),
        "reason": route_data.get("reason", "server-planned node tool call"),
        "tool_call": call,
    }
    return validate_task_assignment(assignment)


def validate_tool_ref(ref: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a server or node tool reference without private transport data."""

    ref_data = _require_mapping(ref, "tool_ref")
    scope = ref_data.get("scope")
    if scope not in {"server", "node", "orchestration"}:
        raise CapabilityMeshValidationError("tool_ref.scope must be server, node, or orchestration")
    name = _safe_tool_name(ref_data.get("name"), "tool_ref.name")
    if scope == "server" and name not in SERVER_LOCAL_TOOLS:
        raise CapabilityMeshValidationError("server tool is not allowlisted")
    validated = {"scope": scope, "name": name}
    if ref_data.get("node_id") is not None:
        if scope != "node":
            raise CapabilityMeshValidationError("tool_ref.node_id is only valid for node tool refs")
        validated["node_id"] = _safe_record_id(ref_data["node_id"], "tool_ref.node_id")
    if _contains_private_command_key(ref_data):
        raise CapabilityMeshValidationError("tool_ref must not include private transport or dispatch commands")
    return validated


def validate_plan_step(step: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a mixed orchestration step request."""

    step_data = dict(_require_mapping(step, "requested_step"))
    kind = step_data.get("kind")
    if kind not in PLAN_STEP_KINDS:
        raise CapabilityMeshValidationError("requested_step.kind must be server_tool_call, node_tool_call, or orchestration_tool_call")
    if _contains_private_command_key(step_data):
        raise CapabilityMeshValidationError("requested_step must not include private transport or dispatch commands")
    if kind == "server_tool_call":
        tool_name = _safe_tool_name(step_data.get("tool_name") or step_data.get("name"), "tool_name")
        if tool_name not in SERVER_LOCAL_TOOLS:
            raise CapabilityMeshValidationError("server tool is not allowlisted")
        arguments = step_data.get("arguments", {})
        _require_mapping(arguments, "arguments")
        sanitized_arguments = {
            str(key): copy.deepcopy(value)
            for key, value in dict(arguments).items()
            if str(key) not in set(DEFAULT_FORBIDDEN_RESULT_FIELDS)
        }
        return {"kind": kind, "tool_name": tool_name, "arguments": sanitized_arguments}
    if kind == "node_tool_call":
        validated = {"kind": kind}
        for key in ("objective", "task_type"):
            if step_data.get(key) is not None:
                _require_non_empty_string(step_data[key], key)
                validated[key] = str(step_data[key])
        if step_data.get("inputs") is not None:
            validated["inputs"] = copy.deepcopy(dict(_require_mapping(step_data["inputs"], "inputs")))
        if step_data.get("required_tools") is not None:
            _require_non_empty_string_list(step_data["required_tools"], "required_tools")
            validated["required_tools"] = list(step_data["required_tools"])
        return validated
    action = step_data.get("action", "completed")
    if action not in {"completed", "no_match"}:
        raise CapabilityMeshValidationError("orchestration action must be completed or no_match")
    return {"kind": kind, "action": action, "reason": str(step_data.get("reason") or "orchestration action")}


def build_server_tool_call(
    parent_contract: Mapping[str, Any],
    requested_step: Mapping[str, Any],
    *,
    call_index: int = 1,
) -> dict[str, Any]:
    """Build one allowlisted server-local tool call for a parent task."""

    parent = validate_task_post(parent_contract)
    step = validate_plan_step(requested_step)
    if step["kind"] != "server_tool_call":
        raise CapabilityMeshValidationError("requested_step.kind must be server_tool_call")
    if call_index < 1:
        raise CapabilityMeshValidationError("call_index must be greater than zero")
    step_id = str(step.get("step_id") or f"{parent['task_id']}-server-{step['tool_name']}-call-{call_index}")
    _safe_record_id(step_id, "step_id")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "server_tool_call",
        "step_id": step_id,
        "parent_task_id": parent["task_id"],
        "tool_ref": validate_tool_ref({"scope": "server", "name": step["tool_name"]}),
        "arguments": copy.deepcopy(step["arguments"]),
        "allowed_result_fields": list(parent["allowed_result_fields"]),
        "forbidden_result_fields": list(parent["forbidden_result_fields"]),
    }


def _execute_echo_sanitized(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {"final_summary": str(arguments.get("message") or arguments.get("final_summary") or "")}


def _execute_aggregate_results(arguments: Mapping[str, Any]) -> dict[str, Any]:
    results = arguments.get("results", [])
    if not isinstance(results, list):
        raise CapabilityMeshValidationError("aggregate_results.results must be a list")
    summaries: list[str] = []
    reports: list[str] = []
    for item in results:
        data = _require_mapping(item, "aggregate_results result")
        if data.get("final_summary") is not None:
            summaries.append(str(data["final_summary"]))
        if data.get("test_report") is not None:
            reports.append(str(data["test_report"]))
    output: dict[str, Any] = {"final_summary": "\n".join(summaries)}
    if reports:
        output["test_report"] = "\n".join(reports)
    return output


def _execute_verify_result(arguments: Mapping[str, Any]) -> dict[str, Any]:
    result = _require_mapping(arguments.get("result", {}), "verify_result.result")
    expected_fields = arguments.get("expected_fields", [])
    if expected_fields:
        _require_non_empty_string_list(expected_fields, "verify_result.expected_fields")
    missing = [field for field in expected_fields if result.get(field) in {None, ""}]
    status = "failed" if missing else "passed"
    return {
        "final_summary": "verification " + status,
        "test_report": "missing: " + ", ".join(missing) if missing else "all expected fields present",
    }


def execute_server_tool_call(tool_call: Mapping[str, Any], parent_contract: Mapping[str, Any]) -> dict[str, Any]:
    """Run one deterministic server-local tool and privacy-filter its result."""

    call = _require_mapping(tool_call, "tool_call")
    if call.get("kind") != "server_tool_call":
        raise CapabilityMeshValidationError("tool_call.kind must be server_tool_call")
    tool_ref = validate_tool_ref(_require_mapping(call.get("tool_ref"), "tool_ref"))
    arguments = _require_mapping(call.get("arguments", {}), "arguments")
    if tool_ref["name"] == "echo_sanitized":
        raw_result = _execute_echo_sanitized(arguments)
    elif tool_ref["name"] == "aggregate_results":
        raw_result = _execute_aggregate_results(arguments)
    elif tool_ref["name"] == "verify_result":
        raw_result = _execute_verify_result(arguments)
    else:
        raise CapabilityMeshValidationError("server tool is not allowlisted")
    parent = validate_task_post(parent_contract)
    contract = dict(parent)
    contract["task_id"] = str(call["step_id"])
    contract["parent_task_id"] = parent["task_id"]
    return build_task_result_record(
        {
            "result_id": f"{call['step_id']}-server-result",
            "task_id": call["step_id"],
            "node_id": "server",
            "status": "completed",
            "result": raw_result,
        },
        contract,
    )


def plan_task_step(
    parent_contract: Mapping[str, Any],
    manifests: list[Mapping[str, Any]],
    *,
    requested_step: Mapping[str, Any] | None = None,
    call_index: int | None = None,
    mesh_home: str | Path | None = None,
) -> dict[str, Any]:
    """Plan one mixed parent-task step: server tool, node tool, orchestration action, or no_match."""

    parent = validate_task_post(parent_contract)
    if requested_step is None:
        return plan_next_node_call(parent, manifests, call_index=call_index or _next_tool_call_index(parent["task_id"], mesh_home=mesh_home))
    step = validate_plan_step(requested_step)
    if step["kind"] == "server_tool_call":
        tool_call = build_server_tool_call(parent, step, call_index=call_index or _next_tool_call_index(parent["task_id"], mesh_home=mesh_home))
        return {"schema_version": SCHEMA_VERSION, "action": "invoke_server_tool", "task_id": parent["task_id"], "tool_call": tool_call}
    if step["kind"] == "node_tool_call":
        subtask = {key: value for key, value in step.items() if key != "kind"}
        return plan_next_node_call(parent, manifests, subtask=subtask, call_index=call_index or _next_tool_call_index(parent["task_id"], mesh_home=mesh_home))
    return {"schema_version": SCHEMA_VERSION, "action": step["action"], "task_id": parent["task_id"], "reason": step["reason"]}


def execute_plan_step(
    parent_contract: Mapping[str, Any],
    manifests: list[Mapping[str, Any]],
    *,
    requested_step: Mapping[str, Any] | None = None,
    mesh_home: str | Path | None = None,
) -> dict[str, Any]:
    """Plan and persist the side effect for one mixed orchestration step."""

    parent = validate_task_post(parent_contract)
    plan = plan_task_step(parent, manifests, requested_step=requested_step, mesh_home=mesh_home)
    if plan.get("action") == "invoke_server_tool":
        result_record = execute_server_tool_call(plan["tool_call"], parent)
        _write_registry_record("results", result_record["result_id"], result_record, mesh_home)
        return {**plan, "result_record": result_record}
    if plan.get("action") == "invoke_node":
        record_task_assignment(plan["assignment"], mesh_home=mesh_home)
    return plan


def plan_next_node_call(
    parent_contract: Mapping[str, Any],
    manifests: list[Mapping[str, Any]],
    *,
    subtask: Mapping[str, Any] | None = None,
    call_index: int = 1,
) -> dict[str, Any]:
    """Plan the next server-controlled node tool call without requiring whole-task completion."""

    parent = validate_task_post(parent_contract)
    subtask_data = dict(_require_mapping(subtask or {}, "subtask"))
    required_tools = subtask_data.get("required_tools", parent.get("required_tools", []))
    route_contract = dict(parent)
    if subtask_data.get("task_type") is not None:
        route_contract["task_type"] = subtask_data["task_type"]
    route = route_task(route_contract, manifests, required_tools=list(required_tools or []))
    if not route.get("selected_node"):
        return {"schema_version": SCHEMA_VERSION, "action": "no_match", "route": route, "reason": route.get("reason", "no matching node")}
    tool_call = build_node_tool_call(parent, route, subtask=subtask_data, call_index=call_index)
    assignment = build_node_tool_call_assignment(parent, route, tool_call)
    return {
        "schema_version": SCHEMA_VERSION,
        "action": "invoke_node",
        "task_id": parent["task_id"],
        "route": route,
        "tool_call": tool_call,
        "assignment": assignment,
    }


def build_task_result_record(result: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, Any]:
    """Build a privacy-filtered result record from raw node output."""

    raw = _require_mapping(result, "result")
    validated_contract = validate_task_post(contract)
    _require_non_empty_string(raw.get("node_id"), "node_id")
    status = raw.get("status", "completed")
    if status not in TASK_RESULT_STATUSES:
        raise CapabilityMeshValidationError(
            "status must be one of: " + ", ".join(sorted(TASK_RESULT_STATUSES))
        )
    payload = raw.get("result", raw)
    filtered = filter_task_result(_require_mapping(payload, "result"), validated_contract)
    report = build_verification_report(filtered, validated_contract)
    return validate_task_result_record(
        {
            "schema_version": SCHEMA_VERSION,
            "result_id": str(raw.get("result_id") or f"{validated_contract['task_id']}-{raw['node_id']}-result"),
            "task_id": validated_contract["task_id"],
            "node_id": raw["node_id"],
            "status": status,
            "result": filtered,
            "verification_report": report,
        }
    )


def build_dispatch_prompt(contract: Mapping[str, Any]) -> str:
    """Build a self-contained, privacy-preserving one-shot node tool-call prompt."""

    validated = validate_task_contract(contract)
    parent = validated.get("parent_task_id", validated["task_id"])
    return (
        "Capability Mesh node tool call. You are responsible for the assigned subtask only. "
        "Do not attempt to complete the parent task unless this subtask does so. "
        "Use only the objective and inputs below. "
        "Do not load or expose private memory, local skills, session history, "
        "reasoning traces, raw logs, environment variables, or secrets. Execute without private memory. Return "
        "JSON containing only these allowed fields plus optional boolean partial and needs_more_results signals: "
        f"{', '.join(validated['allowed_result_fields'])}.\n\n"
        f"Parent task ID: {parent}\n"
        f"Tool call ID: {validated['task_id']}\n"
        f"Task type: {validated['task_type']}\n"
        f"Objective: {validated['objective']}\n"
        f"Inputs: {json.dumps(validated.get('inputs', {}), ensure_ascii=False, sort_keys=True)}"
    )


def _dispatch_command(manifest: Mapping[str, Any], contract: Mapping[str, Any]) -> list[str]:
    transport = validate_capability_manifest(manifest)["transport"]
    command = list(transport.get("dispatch_command") or transport["command"])
    prompt = build_dispatch_prompt(contract)
    return [*command, prompt]


def _parse_stdout_result(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        return {"final_summary": ""}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"final_summary": text}
    if isinstance(data, dict):
        return data
    return {"final_summary": str(data)}


def build_dispatch_result(manifest: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, Any]:
    """Dispatch a contract to a node and filter stdout through the contract."""

    validated_manifest = validate_capability_manifest(manifest)
    validated_contract = validate_task_contract(contract)
    try:
        completed = _run_transport(
            validated_manifest,
            command=_dispatch_command(validated_manifest, validated_contract),
        )
        raw_result = _parse_stdout_result(completed.stdout)
        filtered = filter_task_result(raw_result, validated_contract)
        status = "completed" if completed.returncode == 0 else "failed"
        stderr = _redact_secret_like_text(completed.stderr)
        return {
            "schema_version": SCHEMA_VERSION,
            "task_id": validated_contract["task_id"],
            "node_id": validated_manifest["node_id"],
            "status": status,
            "returncode": completed.returncode,
            "result": filtered,
            "stderr": stderr,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "task_id": validated_contract["task_id"],
            "node_id": validated_manifest["node_id"],
            "status": "failed",
            "returncode": None,
            "result": {},
            "stderr": _redact_secret_like_text(str(exc)),
        }


def _redact_secret_like_text(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: _redact_assignment(match.group(0)), redacted)
    return redacted


def _redact_assignment(value: str) -> str:
    if re.search(r"[:=]", value):
        return re.sub(r"([:=]\s*)[^\s,;]+", r"\1[REDACTED]", value, count=1)
    return "[REDACTED]"


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_secret_like_text(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    return value


def filter_task_result(
    result: Mapping[str, Any], contract: Mapping[str, Any]
) -> dict[str, Any]:
    """Return only allowed result fields, with secret-like strings redacted."""

    result = _require_mapping(result, "result")
    validated_contract = validate_task_contract(contract)
    allowed = set(validated_contract["allowed_result_fields"])
    forbidden = set(validated_contract.get("forbidden_result_fields", [])) | set(
        DEFAULT_FORBIDDEN_RESULT_FIELDS
    )
    output: dict[str, Any] = {}
    for key, value in result.items():
        if key in forbidden or key not in allowed:
            continue
        output[key] = _redact_value(value)
    return output


_FIELD_STATUSES = {"filled", "missing", "mismatch", "unknown"}
_MISSING = object()


def build_web_form_verification(
    *,
    task_id: str,
    expected_fields: list[str],
    observed_fields: Mapping[str, Any],
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a privacy-filterable verification primitive for web form fills."""

    _require_non_empty_string(task_id, "task_id")
    _require_non_empty_string_list(expected_fields, "expected_fields")
    observed = _require_mapping(observed_fields, "observed_fields")
    safe_evidence = _redact_value(dict(evidence or {}))
    per_field: dict[str, dict[str, Any]] = {}
    filled = 0
    for field in expected_fields:
        observed_value = observed.get(field, _MISSING)
        status = "filled" if observed_value not in {_MISSING, None, ""} else "missing"
        if status == "filled":
            filled += 1
        item: dict[str, Any] = {"status": status}
        if field in observed:
            item["observed"] = _redact_value(observed_value)
        field_evidence = safe_evidence.get(field) if isinstance(safe_evidence, dict) else None
        if field_evidence is not None:
            item["evidence"] = field_evidence
        per_field[field] = item
    score = round(filled / len(expected_fields), 2)
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "expected_fields": list(expected_fields),
        "observed_fields": _redact_value(dict(observed)),
        "per_field_status": per_field,
        "evidence": safe_evidence,
        "overall_score": score,
        "status": "passed" if score == 1.0 else "failed",
    }


def validate_web_form_verification(report: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the web form verification result primitive."""

    report = _require_mapping(report, "web_form_verification")
    _require_schema_version(report)
    _require_non_empty_string(report.get("task_id"), "task_id")
    _require_non_empty_string_list(report.get("expected_fields"), "expected_fields")
    _require_mapping(report.get("observed_fields"), "observed_fields")
    per_field = _require_mapping(report.get("per_field_status"), "per_field_status")
    for field in report["expected_fields"]:
        item = _require_mapping(per_field.get(field), f"per_field_status.{field}")
        if item.get("status") not in _FIELD_STATUSES:
            raise CapabilityMeshValidationError(
                f"per_field_status.{field}.status must be one of: "
                + ", ".join(sorted(_FIELD_STATUSES))
            )
    _require_mapping(report.get("evidence", {}), "evidence")
    score = report.get("overall_score")
    if not isinstance(score, (int, float)) or score < 0 or score > 1:
        raise CapabilityMeshValidationError("overall_score must be between 0 and 1")
    if report.get("status") not in {"passed", "failed"}:
        raise CapabilityMeshValidationError("status must be passed or failed")
    return copy.deepcopy(dict(report))


def build_verification_report(
    filtered_result: Mapping[str, Any], contract: Mapping[str, Any]
) -> dict[str, Any]:
    """Build a verification report for a filtered task result."""

    result = _require_mapping(filtered_result, "filtered_result")
    validated_contract = validate_task_contract(contract)
    allowed = set(validated_contract["allowed_result_fields"])
    forbidden = set(validated_contract["forbidden_result_fields"]) | set(DEFAULT_FORBIDDEN_RESULT_FIELDS)
    present = set(result.keys())
    redacted_again = filter_task_result(result, validated_contract)
    checks = {
        "allowed_fields_present": bool(present & allowed),
        "no_forbidden_fields": not bool(present & forbidden),
        "privacy_filter_applied": redacted_again == dict(result),
    }
    if "web_form_verification" in result:
        try:
            validate_web_form_verification(result["web_form_verification"])
            checks["web_form_verification_valid"] = True
        except CapabilityMeshValidationError:
            checks["web_form_verification_valid"] = False
    passed = all(checks.values())
    quality_score = round(sum(1 for value in checks.values() if value) / len(checks), 2)
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": validated_contract["task_id"],
        "status": "passed" if passed else "failed",
        "checks": checks,
        "quality_score": quality_score,
    }


def validate_verification_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a verification report primitive."""

    report = _require_mapping(report, "verification_report")
    _require_schema_version(report)
    _require_non_empty_string(report.get("task_id"), "task_id")
    if report.get("status") not in {"passed", "failed"}:
        raise CapabilityMeshValidationError("status must be passed or failed")
    checks = _require_mapping(report.get("checks"), "checks")
    for key in ("allowed_fields_present", "no_forbidden_fields", "privacy_filter_applied"):
        if checks.get(key) not in {True, False}:
            raise CapabilityMeshValidationError(f"checks.{key} must be boolean")
    score = report.get("quality_score")
    if not isinstance(score, (int, float)) or score < 0 or score > 1:
        raise CapabilityMeshValidationError("quality_score must be between 0 and 1")
    return copy.deepcopy(dict(report))


def validate_optional_skill_proposal(proposal: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a skill proposal without making contribution implicit.

    Public/team contribution requires explicit human consent plus a review note.
    Local-private proposals may exist without consent because they do not leave
    the user's machine.  The default safe choice should be ``none`` or
    ``local_private``.
    """

    proposal = _require_mapping(proposal, "proposal")
    _require_schema_version(proposal)
    for field in ("proposal_id", "source_task_id", "title", "summary"):
        _require_non_empty_string(proposal.get(field), field)

    visibility = proposal.get("proposed_visibility", "none")
    if visibility not in _ALLOWED_SKILL_VISIBILITIES:
        raise CapabilityMeshValidationError(
            "proposed_visibility must be one of: "
            + ", ".join(sorted(_ALLOWED_SKILL_VISIBILITIES))
        )

    if visibility in PUBLIC_SKILL_VISIBILITIES:
        if proposal.get("human_consent") is not True:
            raise CapabilityMeshValidationError(
                "human_consent is required for team/public skill contribution"
            )
        _require_non_empty_string(proposal.get("human_review_note"), "human_review_note")
    return copy.deepcopy(dict(proposal))
