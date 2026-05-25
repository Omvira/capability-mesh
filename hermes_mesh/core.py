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
    return copy.deepcopy(dict(contract))


def build_dispatch_prompt(contract: Mapping[str, Any]) -> str:
    """Build a self-contained, privacy-preserving one-shot task prompt."""

    validated = validate_task_contract(contract)
    return (
        "Capability Mesh task. Use only the objective and inputs below. "
        "Do not load or expose private memory, local skills, session history, "
        "reasoning traces, raw logs, environment variables, or secrets. Execute without private memory. Return "
        "JSON containing only these allowed fields: "
        f"{', '.join(validated['allowed_result_fields'])}.\n\n"
        f"Task ID: {validated['task_id']}\n"
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
