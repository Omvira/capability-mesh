"""Standalone CLI for the independent Capability Mesh core.

Run with ``python -m capability_mesh.cli``.  The registry defaults to
``$CAPABILITY_MESH_HOME`` or ``~/.capability-mesh``. Legacy ``python -m
hermes_mesh.cli`` and ``$HERMES_MESH_HOME`` continue to work.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from hermes_mesh.core import (
    CapabilityMeshValidationError,
    SCHEMA_VERSION,
    build_task_assignment,
    build_default_capability_manifest,
    default_mesh_home,
    filter_task_result,
    list_contribution_records,
    list_posted_tasks,
    list_registered_nodes,
    post_task,
    record_contribution,
    record_task_assignment,
    record_task_result,
    register_node_manifest,
    route_task,
    validate_capability_manifest,
    validate_optional_skill_proposal,
    validate_task_contract,
)
from hermes_mesh.client import HermesMeshClient, HermesMeshClientError
from hermes_mesh.dashboard import serve_dashboard
from hermes_mesh.mcp_server import run_mcp_server


def _load_yaml_or_json(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise CapabilityMeshValidationError(f"{p} must contain a mapping")
    return data


def _write_yaml_or_stdout(data: dict[str, Any], output: str | None) -> None:
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    if output:
        Path(output).write_text(text, encoding="utf-8")
        print(f"Wrote {output}")
    else:
        print(text, end="")


def _write_json_or_stdout(data: dict[str, Any] | list[dict[str, Any]], output: str | None = None) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def _mesh_home(args: argparse.Namespace) -> Path:
    return Path(args.mesh_home).expanduser() if args.mesh_home else default_mesh_home()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m capability_mesh.cli",
        description="Standalone privacy-first Capability Mesh helpers.",
    )
    parser.add_argument(
        "--mesh-home",
        default=None,
        help="Mesh registry home; defaults to $CAPABILITY_MESH_HOME, legacy $HERMES_MESH_HOME, or ~/.capability-mesh",
    )
    sub = parser.add_subparsers(dest="command")

    manifest = sub.add_parser("manifest", help="Generate a node capability manifest")
    manifest.add_argument("--node-id", required=True)
    manifest.add_argument("--display-name", required=True)
    manifest.add_argument("--task-type", action="append", required=True, dest="task_types")
    manifest.add_argument("--tool", action="append", required=True, dest="tools_available")
    manifest.add_argument("--output", "-o")
    manifest.add_argument("--allow-auto-accept", action="store_true")
    manifest.set_defaults(func=cmd_manifest)

    validate = sub.add_parser("validate", help="Validate a mesh object")
    validate.add_argument("path")
    validate.add_argument("--kind", choices=["manifest", "task-contract", "skill-proposal"], required=True)
    validate.set_defaults(func=cmd_validate)

    register = sub.add_parser("register", help="Register a validated node manifest")
    register.add_argument("path")
    register.set_defaults(func=cmd_register)

    list_nodes = sub.add_parser("list", help="List registered mesh nodes")
    list_nodes.add_argument("--json", action="store_true")
    list_nodes.set_defaults(func=cmd_list)

    filter_result = sub.add_parser("filter-result", help="Filter a task result through a task contract")
    filter_result.add_argument("result_path")
    filter_result.add_argument("--contract", required=True)
    filter_result.add_argument("--output", "-o")
    filter_result.set_defaults(func=cmd_filter_result)

    post = sub.add_parser("post-task", help="Post a task contract to the local registry")
    post.add_argument("task_path")
    post.set_defaults(func=cmd_post_task)

    route = sub.add_parser("route-task", help="Route a posted task to registered nodes")
    route.add_argument("task_path")
    route.add_argument("--required-tool", action="append", dest="required_tools")
    route.add_argument("--json", action="store_true")
    route.set_defaults(func=cmd_route_task)

    record = sub.add_parser("record-result", help="Record a privacy-filtered task result")
    record.add_argument("result_path")
    record.set_defaults(func=cmd_record_result)

    contributions = sub.add_parser("contributions", help="List local contribution records")
    contributions.add_argument("--json", action="store_true")
    contributions.set_defaults(func=cmd_contributions)

    server = sub.add_parser("server", help="Run the Capability Mesh HTTP service and dashboard")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    server.set_defaults(func=cmd_server)

    dashboard = sub.add_parser("dashboard", help="Alias for server")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8765)
    dashboard.set_defaults(func=cmd_server)

    mcp_server = sub.add_parser("mcp-server", help="Run a stdio MCP server adapter for a Capability Mesh service")
    mcp_server.add_argument("--url", "--mesh-url", dest="mesh_url", required=True, help="Capability Mesh service base URL")
    mcp_server.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds")
    mcp_server.set_defaults(func=cmd_mcp_server)

    client = sub.add_parser("client", help="Call a running Capability Mesh service")
    client.add_argument("--url", required=True, help="Capability Mesh service base URL")
    client_sub = client.add_subparsers(dest="client_command")
    client_health = client_sub.add_parser("health", help="Check service health")
    client_health.set_defaults(func=cmd_client_health)
    client_card = client_sub.add_parser("agent-card", help="Fetch the service Agent Card")
    client_card.set_defaults(func=cmd_client_agent_card)
    client_nodes = client_sub.add_parser("nodes", help="List service nodes")
    client_nodes.set_defaults(func=cmd_client_nodes)
    client_register = client_sub.add_parser("register", help="Register a node manifest with the service")
    client_register.add_argument("path")
    client_register.set_defaults(func=cmd_client_register)
    client_post = client_sub.add_parser("post-task", help="Post a task to the service")
    client_post.add_argument("task_path")
    client_post.set_defaults(func=cmd_client_post_task)
    client_route = client_sub.add_parser("route-task", help="Route a task through the service")
    client_route.add_argument("task_path")
    client_route.add_argument("--required-tool", action="append", dest="required_tools")
    client_route.set_defaults(func=cmd_client_route_task)
    client_poll = client_sub.add_parser("poll", help="Poll assigned work for a node")
    client_poll.add_argument("node_id")
    client_poll.set_defaults(func=cmd_client_poll)
    client_claim = client_sub.add_parser("claim", help="Claim an assigned work item")
    client_claim.add_argument("assignment_id")
    client_claim.add_argument("--node-id", required=True)
    client_claim.set_defaults(func=cmd_client_claim)
    client_complete = client_sub.add_parser("complete", help="Complete an assigned work item with a result file")
    client_complete.add_argument("assignment_id")
    client_complete.add_argument("result_path")
    client_complete.add_argument("--node-id", required=True)
    client_complete.set_defaults(func=cmd_client_complete)
    client_wake = client_sub.add_parser("wake", help="Wake a node to poll an available assignment")
    client_wake.add_argument("assignment_id")
    client_wake.set_defaults(func=cmd_client_wake)
    client_run = client_sub.add_parser("run-next", help="Claim, execute, and complete the next local assignment")
    client_run.add_argument("manifest_path")
    client_run.set_defaults(func=cmd_client_run_next)
    client_heartbeat = client_sub.add_parser("heartbeat", help="Send one node heartbeat")
    client_heartbeat.add_argument("node_id")
    client_heartbeat.add_argument("--status", choices=["online", "idle", "busy", "offline"], default="online")
    client_heartbeat.set_defaults(func=cmd_client_heartbeat)
    client_loop = client_sub.add_parser("heartbeat-loop", help="Send node heartbeats until interrupted")
    client_loop.add_argument("node_id")
    client_loop.add_argument("--status", choices=["online", "idle", "busy", "offline"], default="online")
    client_loop.add_argument("--interval", type=float, default=30.0)
    client_loop.set_defaults(func=cmd_client_heartbeat_loop)
    client_service_loop = client_sub.add_parser("loop", help="Run an independent client loop: health, heartbeat, and optional work polling")
    client_service_loop.add_argument("manifest_path")
    client_service_loop.add_argument("--interval", type=float, default=30.0)
    client_service_loop.add_argument("--run-next", action="store_true", help="Claim and complete one assignment per loop using the manifest dispatch command")
    client_service_loop.set_defaults(func=cmd_client_loop)
    client_install = client_sub.add_parser("install", help="Interactive trial Client registration and keep-online installer")
    client_install.add_argument("--node-id", help="Unique client node id; defaults to user-host")
    client_install.add_argument("--display-name", help="Human-readable client name")
    client_install.add_argument("--task-type", action="append", help="Task type this Client can handle; repeatable")
    client_install.add_argument("--tool", action="append", help="Public capability/tool label; repeatable")
    client_install.add_argument("--transport-command", action="append", help="Transport command argv part; repeatable; default: hermes chat -q")
    client_install.add_argument("--dispatch-command", action="append", help="Dispatch command argv part for assigned work; repeatable")
    client_install.add_argument("--timeout-seconds", type=int, default=120)
    client_install.add_argument("--allow-auto-accept", action="store_true")
    client_install.add_argument("--auto-accept-task-type", action="append")
    client_install.add_argument("--no-accept-tasks", action="store_true")
    client_install.add_argument("--include-basic-resources", action="store_true")
    client_install.add_argument("--config-dir", help="Directory for generated manifest")
    client_install.add_argument("--manifest-path", help="Use an existing manifest JSON file instead of generating one")
    client_install.add_argument("--print-manifest", action="store_true")
    client_install.add_argument("--dry-run", action="store_true")
    client_install.add_argument("--keep-online", action="store_true", help="Start foreground heartbeat loop after registration")
    client_install.add_argument("--once", action="store_true", help="Send one heartbeat after registration and exit")
    client_install.add_argument("--interval", type=float, default=30.0)
    client_install.add_argument("--install-systemd", action="store_true", help="Write a user systemd service for durable keep-online loop")
    client_install.add_argument("--yes", action="store_true", help="Non-interactive mode; use flags/defaults")
    client_install.add_argument("--http-timeout", type=float, default=15.0)
    client_install.set_defaults(func=cmd_client_install)
    client_a2a = client_sub.add_parser("send-a2a", help="Send an A2A-like message envelope")
    client_a2a.add_argument("--message", help="Path to JSON/YAML message envelope; defaults to a TextPart from --text")
    client_a2a.add_argument("--text", help="TextPart content to send")
    client_a2a.add_argument("--image", help="Image file path to send as FilePart bytes/base64")
    client_a2a.add_argument("--mime-type", default="image/png", help="MIME type for --image")
    client_a2a.set_defaults(func=cmd_client_send_a2a)

    return parser


def cmd_manifest(args: argparse.Namespace) -> int:
    manifest = build_default_capability_manifest(
        node_id=args.node_id,
        display_name=args.display_name,
        task_types=args.task_types,
        tools_available=args.tools_available,
        requires_human_approval=not args.allow_auto_accept,
    )
    _write_yaml_or_stdout(manifest, args.output)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    data = _load_yaml_or_json(args.path)
    if args.kind == "manifest":
        validate_capability_manifest(data)
    elif args.kind == "task-contract":
        validate_task_contract(data)
    elif args.kind == "skill-proposal":
        validate_optional_skill_proposal(data)
    print("OK")
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    data = _load_yaml_or_json(args.path)
    path = register_node_manifest(data, mesh_home=_mesh_home(args))
    print(f"Registered {data.get('node_id')} at {path}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    nodes = list_registered_nodes(mesh_home=_mesh_home(args))
    if args.json:
        _write_json_or_stdout(nodes)
        return 0
    if not nodes:
        print("No Capability Mesh nodes registered.")
        return 0
    for node in nodes:
        task_types = ", ".join(node.get("capabilities", {}).get("task_types", []))
        print(f"{node['node_id']}\t{node['display_name']}\t{task_types}")
    return 0


def cmd_filter_result(args: argparse.Namespace) -> int:
    result = _load_yaml_or_json(args.result_path)
    contract = _load_yaml_or_json(args.contract)
    _write_json_or_stdout(filter_task_result(result, contract), args.output)
    return 0


def cmd_post_task(args: argparse.Namespace) -> int:
    task = _load_yaml_or_json(args.task_path)
    path = post_task(task, mesh_home=_mesh_home(args))
    print(f"Posted {task.get('task_id')} at {path}")
    return 0


def cmd_route_task(args: argparse.Namespace) -> int:
    task = _load_yaml_or_json(args.task_path)
    route = route_task(
        task,
        list_registered_nodes(mesh_home=_mesh_home(args)),
        required_tools=args.required_tools,
    )
    if route.get("selected_node"):
        assignment = build_task_assignment(task, route)
        record_task_assignment(assignment, mesh_home=_mesh_home(args))
    if args.json:
        _write_json_or_stdout(route)
        return 0
    print(f"{route['status']}: {route.get('selected_node') or 'no node'}")
    print(route["reason"])
    return 0


def _find_posted_task(task_id: str, mesh_home: Path) -> dict[str, Any]:
    for task in list_posted_tasks(mesh_home=mesh_home):
        if task.get("task_id") == task_id:
            return task
    raise CapabilityMeshValidationError(f"unknown posted task_id: {task_id}")


def cmd_record_result(args: argparse.Namespace) -> int:
    raw_result = _load_yaml_or_json(args.result_path)
    task_id = raw_result.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise CapabilityMeshValidationError("result task_id is required")
    mesh_home = _mesh_home(args)
    task = _find_posted_task(task_id, mesh_home)
    path = record_task_result(raw_result, task, mesh_home=mesh_home)
    records = [record for record in list_contribution_records(mesh_home=mesh_home) if record.get("task_id") == task_id]
    if not records:
        node_id = raw_result.get("node_id")
        if isinstance(node_id, str) and node_id.strip():
            record_contribution(
                {
                    "schema_version": SCHEMA_VERSION,
                    "contribution_id": f"{task_id}-{node_id}-contribution",
                    "task_id": task_id,
                    "node_id": node_id,
                    "summary": "Local task result recorded",
                    "visibility": "local_private",
                    "human_consent": False,
                },
                mesh_home=mesh_home,
            )
    print(f"Recorded result at {path}")
    return 0


def cmd_contributions(args: argparse.Namespace) -> int:
    records = list_contribution_records(mesh_home=_mesh_home(args))
    if args.json:
        _write_json_or_stdout(records)
        return 0
    if not records:
        print("No contribution records.")
        return 0
    for record in records:
        print(f"{record['contribution_id']}\t{record['task_id']}\t{record['node_id']}\t{record['visibility']}")
    return 0


def cmd_server(args: argparse.Namespace) -> int:
    serve_dashboard(host=args.host, port=args.port, mesh_home=_mesh_home(args))
    return 0


def cmd_mcp_server(args: argparse.Namespace) -> int:
    return run_mcp_server(args.mesh_url, timeout=args.timeout)


def _client(args: argparse.Namespace) -> HermesMeshClient:
    return HermesMeshClient(args.url)


def cmd_client_health(args: argparse.Namespace) -> int:
    _write_json_or_stdout(_client(args).health())
    return 0


def cmd_client_agent_card(args: argparse.Namespace) -> int:
    _write_json_or_stdout(_client(args).agent_card())
    return 0


def cmd_client_nodes(args: argparse.Namespace) -> int:
    _write_json_or_stdout(_client(args).list_nodes())
    return 0


def cmd_client_register(args: argparse.Namespace) -> int:
    manifest = _load_yaml_or_json(args.path)
    _write_json_or_stdout(_client(args).register_node(manifest))
    return 0


def cmd_client_post_task(args: argparse.Namespace) -> int:
    task = _load_yaml_or_json(args.task_path)
    _write_json_or_stdout(_client(args).post_task(task))
    return 0


def cmd_client_route_task(args: argparse.Namespace) -> int:
    task = _load_yaml_or_json(args.task_path)
    _write_json_or_stdout(_client(args).route_task(task, required_tools=args.required_tools or None))
    return 0


def cmd_client_poll(args: argparse.Namespace) -> int:
    _write_json_or_stdout(_client(args).poll_assignments(args.node_id))
    return 0


def cmd_client_claim(args: argparse.Namespace) -> int:
    _write_json_or_stdout(_client(args).claim_assignment(args.assignment_id, args.node_id))
    return 0


def cmd_client_complete(args: argparse.Namespace) -> int:
    result = _load_yaml_or_json(args.result_path)
    _write_json_or_stdout(_client(args).complete_assignment(args.assignment_id, args.node_id, result))
    return 0


def cmd_client_wake(args: argparse.Namespace) -> int:
    _write_json_or_stdout(_client(args).wake_assignment(args.assignment_id))
    return 0


def cmd_client_heartbeat(args: argparse.Namespace) -> int:
    _write_json_or_stdout(_client(args).heartbeat(args.node_id, status=args.status))
    return 0


def cmd_client_heartbeat_loop(args: argparse.Namespace) -> int:
    if args.interval <= 0:
        raise CapabilityMeshValidationError("interval must be greater than zero")
    client = _client(args)
    try:
        while True:
            _write_json_or_stdout(client.heartbeat(args.node_id, status=args.status))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def cmd_client_loop(args: argparse.Namespace) -> int:
    if args.interval <= 0:
        raise CapabilityMeshValidationError("interval must be greater than zero")
    manifest = _load_yaml_or_json(args.manifest_path)
    node_id = str(manifest.get("node_id") or "")
    if not node_id:
        raise CapabilityMeshValidationError("manifest node_id is required")
    client = _client(args)
    try:
        while True:
            health = client.health()
            if health.get("ok") is True:
                client.heartbeat(node_id)
                if args.run_next:
                    _write_json_or_stdout(client.run_next_assignment(manifest))
                else:
                    _write_json_or_stdout({"ok": True, "server": "online", "node_id": node_id})
            else:
                _write_json_or_stdout({"ok": False, "server": "unhealthy", "health": health})
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def cmd_client_install(args: argparse.Namespace) -> int:
    from scripts.install_client import main as install_client_main

    argv = ["--mesh-url", args.url]
    for name in [
        "node_id",
        "display_name",
        "timeout_seconds",
        "config_dir",
        "manifest_path",
        "interval",
        "http_timeout",
    ]:
        value = getattr(args, name, None)
        if value is not None:
            argv.extend(["--" + name.replace("_", "-"), str(value)])
    for name in ["task_type", "tool", "transport_command", "dispatch_command", "auto_accept_task_type"]:
        for value in getattr(args, name, None) or []:
            argv.extend(["--" + name.replace("_", "-"), str(value)])
    for flag in [
        "allow_auto_accept",
        "no_accept_tasks",
        "include_basic_resources",
        "print_manifest",
        "dry_run",
        "keep_online",
        "once",
        "install_systemd",
        "yes",
    ]:
        if getattr(args, flag, False):
            argv.append("--" + flag.replace("_", "-"))
    return install_client_main(argv)


def _a2a_message_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.message:
        return _load_yaml_or_json(args.message)
    parts: list[dict[str, Any]] = []
    if args.text:
        parts.append({"text": args.text})
    if args.image:
        import base64

        image_path = Path(args.image)
        parts.append(
            {
                "filename": image_path.name,
                "mediaType": args.mime_type,
                "raw": base64.b64encode(image_path.read_bytes()).decode("ascii"),
            }
        )
    if not parts:
        raise CapabilityMeshValidationError("send-a2a requires --message, --text, or --image")
    return {"role": "ROLE_USER", "parts": parts}


def cmd_client_send_a2a(args: argparse.Namespace) -> int:
    _write_json_or_stdout(_client(args).send_a2a_message(_a2a_message_from_args(args)))
    return 0


def cmd_client_run_next(args: argparse.Namespace) -> int:
    manifest = _load_yaml_or_json(args.manifest_path)
    _write_json_or_stdout(_client(args).run_next_assignment(manifest))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    if args.command == "client" and not getattr(args, "client_command", None):
        client_parser = build_parser()
        client_parser.parse_args(["client", "--url", args.url, "--help"])
        return 2
    try:
        return args.func(args)
    except (CapabilityMeshValidationError, HermesMeshClientError, OSError, yaml.YAMLError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
