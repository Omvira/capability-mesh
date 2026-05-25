#!/usr/bin/env python3
"""One-shot HermesMesh node registration without cloning the repo.

This script intentionally uses only the Python standard library so a Hermes node can
register with a running HermesMesh service via:

  curl -fsSL https://raw.githubusercontent.com/Omvira/HermesMesh/main/scripts/register_node.py | \
    python3 - --mesh-url http://10.0.16.11:8765 --node-id my-node --task-type code_review --tool hermes

It submits only a privacy-first capability manifest. It does not read or upload
local skills, memory, session history, raw logs, env vars, or secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
import urllib.error
import urllib.request
from typing import Any

SCHEMA_VERSION = "capability-mesh-alpha-1"
DEFAULT_FORBIDDEN_RESULT_FIELDS = [
    "raw_private_logs",
    "environment_variables",
    "secrets",
    "full_session_transcript",
    "private_memory",
    "reasoning_trace",
    "local_skills",
]
DEFAULT_ALLOWED_RESULT_FIELDS = [
    "final_summary",
    "patch",
    "test_report",
    "generated_file",
    "web_form_verification",
]
DEFAULT_PRIVACY = {
    "expose_local_skills": False,
    "expose_memory": False,
    "expose_session_history": False,
    "expose_reasoning_trace": False,
    "expose_raw_logs": False,
    "expose_environment": False,
}


def _default_node_id() -> str:
    host = socket.gethostname() or "hermes-node"
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "user"
    raw = f"{user}-{host}".lower()
    return "".join(ch if ch.isalnum() or ch in "_.-" else "-" for ch in raw).strip("-._") or "hermes-node"


def _default_display_name(node_id: str) -> str:
    return f"Hermes node {node_id}"


def _build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    command = list(args.transport_command or ["hermes", "chat", "-q"])
    resources: dict[str, Any] = {}
    if args.include_basic_resources:
        resources = {
            "os": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "node_id": args.node_id,
        "display_name": args.display_name or _default_display_name(args.node_id),
        "capabilities": {
            "task_types": args.task_type,
            "tools_available": args.tool,
            "resources": resources,
        },
        "policies": {
            "accepts_tasks": not args.no_accept_tasks,
            "auto_accept_task_types": list(args.auto_accept_task_type or []),
            "requires_human_approval": not args.allow_auto_accept,
        },
        "transport": {
            "type": "local",
            "command": command,
            "timeout_seconds": args.timeout_seconds,
        },
        "privacy": dict(DEFAULT_PRIVACY),
        "result_policy": {
            "allow": list(DEFAULT_ALLOWED_RESULT_FIELDS),
            "deny": list(DEFAULT_FORBIDDEN_RESULT_FIELDS),
        },
    }
    if args.dispatch_command:
        manifest["transport"]["dispatch_command"] = list(args.dispatch_command)
    return manifest


def _post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/api/nodes",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data) if data else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HermesMesh registration failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"HermesMesh registration failed: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Register this Hermes node with a HermesMesh service without cloning HermesMesh.")
    parser.add_argument("--mesh-url", required=True, help="HermesMesh service URL, e.g. http://10.0.16.11:8765")
    parser.add_argument("--node-id", default=_default_node_id(), help="Unique node id; defaults to user-host")
    parser.add_argument("--display-name", default=None, help="Human-readable display name")
    parser.add_argument("--task-type", action="append", required=True, help="Task type this node can handle; repeatable")
    parser.add_argument("--tool", action="append", required=True, help="Public capability/tool label; repeatable")
    parser.add_argument("--transport-command", action="append", help="Command argv part for future local dispatch; repeatable; default: hermes chat -q")
    parser.add_argument("--dispatch-command", action="append", help="Optional dispatch command argv part; repeatable")
    parser.add_argument("--timeout-seconds", type=int, default=120, help="Transport timeout metadata, 1..300")
    parser.add_argument("--allow-auto-accept", action="store_true", help="Mark node as not requiring human approval")
    parser.add_argument("--auto-accept-task-type", action="append", help="Task type this node auto-accepts; repeatable")
    parser.add_argument("--no-accept-tasks", action="store_true", help="Register as not currently accepting tasks")
    parser.add_argument("--include-basic-resources", action="store_true", help="Include basic OS/Python resource metadata; never includes env vars")
    parser.add_argument("--print-manifest", action="store_true", help="Print manifest JSON before registering")
    parser.add_argument("--dry-run", action="store_true", help="Print manifest JSON and do not register")
    parser.add_argument("--http-timeout", type=int, default=15, help="HTTP timeout seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout_seconds < 1 or args.timeout_seconds > 300:
        raise SystemExit("--timeout-seconds must be between 1 and 300")
    auto_accept = set(args.auto_accept_task_type or [])
    task_types = set(args.task_type or [])
    unknown = auto_accept - task_types
    if unknown:
        raise SystemExit("--auto-accept-task-type must also be declared with --task-type: " + ", ".join(sorted(unknown)))
    manifest = _build_manifest(args)
    if args.print_manifest or args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    if args.dry_run:
        return 0
    result = _post_json(args.mesh_url, manifest, args.http_timeout)
    print("Registered HermesMesh node:")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
