#!/usr/bin/env python3
"""Interactive HermesMesh client registration and keep-online CLI.

Stdlib-only on purpose so a trial client can be installed with a one-shot
curl/python command. The CLI builds a privacy-first public capability manifest,
registers it with a HermesMesh Server, writes a local manifest copy, and can keep
the Client online by sending heartbeat/presence updates.

It never reads or uploads local skills, memory, sessions, raw logs, environment
variables, credentials, or secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "capability-mesh-alpha-1"
DEFAULT_HEARTBEAT_INTERVAL = 30.0
DEFAULT_ALLOWED_RESULT_FIELDS = ["final_summary", "patch", "test_report", "generated_file", "web_form_verification"]
DEFAULT_FORBIDDEN_RESULT_FIELDS = [
    "raw_private_logs",
    "environment_variables",
    "secrets",
    "full_session_transcript",
    "private_memory",
    "reasoning_trace",
    "local_skills",
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
    host = socket.gethostname() or "hermes-client"
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "user"
    raw = f"{user}-{host}".lower()
    return "".join(ch if ch.isalnum() or ch in "_.-" else "-" for ch in raw).strip("-._") or "hermes-client"


def _default_config_dir() -> Path:
    return Path(os.environ.get("HERMES_MESH_CLIENT_HOME", Path.home() / ".hermes-mesh" / "client")).expanduser()


def _split_csv(value: str | None, default: list[str]) -> list[str]:
    if value is None or not value.strip():
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


def _prompt(prompt: str, default: str | None = None, *, required: bool = False) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        if not required:
            return ""
        print("This value is required.", file=sys.stderr)


def _prompt_bool(prompt: str, default: bool) -> bool:
    marker = "Y/n" if default else "y/N"
    while True:
        value = input(f"{prompt} [{marker}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes", "true", "1"}:
            return True
        if value in {"n", "no", "false", "0"}:
            return False
        print("Please answer yes or no.", file=sys.stderr)


def _load_or_prompt_args(args: argparse.Namespace) -> argparse.Namespace:
    if args.yes:
        return args
    print("HermesMesh Client setup")
    print("This publishes only safe capability metadata. Do not enter secrets, tokens, private skill names, memory, or log paths.")
    args.mesh_url = _prompt("HermesMesh Server URL", args.mesh_url or "http://127.0.0.1:8765", required=True)
    args.node_id = _prompt("Client node id", args.node_id or _default_node_id(), required=True)
    args.display_name = _prompt("Display name", args.display_name or f"HermesMesh Client {args.node_id}")
    args.task_type = _split_csv(_prompt("Task types, comma-separated", ",".join(args.task_type or ["smoke"])), ["smoke"])
    args.tool = _split_csv(_prompt("Public tool/capability labels, comma-separated", ",".join(args.tool or ["hermes"])), ["hermes"])
    dispatch_default = " ".join(args.dispatch_command or ["hermes", "chat", "-q"])
    dispatch = _prompt("Local dispatch command for assigned tasks", dispatch_default)
    args.dispatch_command = shlex.split(dispatch) if dispatch else []
    args.allow_auto_accept = _prompt_bool("Allow this trial client to auto-accept declared task types?", args.allow_auto_accept)
    if args.allow_auto_accept and not args.auto_accept_task_type:
        args.auto_accept_task_type = list(args.task_type)
    args.include_basic_resources = _prompt_bool("Include basic OS/Python resource labels? No env vars are uploaded", args.include_basic_resources)
    args.keep_online = _prompt_bool("Start heartbeat loop now to keep this Client online?", args.keep_online)
    return args


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    task_types = list(args.task_type or ["smoke"])
    tools = list(args.tool or ["hermes"])
    auto_accept = list(args.auto_accept_task_type or (task_types if args.allow_auto_accept else []))
    unknown = set(auto_accept) - set(task_types)
    if unknown:
        raise SystemExit("auto-accept task types must also be declared task types: " + ", ".join(sorted(unknown)))
    resources: dict[str, Any] = {}
    if args.include_basic_resources:
        resources = {"os": platform.system(), "machine": platform.machine(), "python": platform.python_version()}
    command = list(args.transport_command or ["hermes", "chat", "-q"])
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "node_id": args.node_id,
        "display_name": args.display_name or f"HermesMesh Client {args.node_id}",
        "description": "Trial HermesMesh Client registered by install_client.py",
        "capabilities": {"task_types": task_types, "tools_available": tools, "resources": resources},
        "policies": {
            "accepts_tasks": not args.no_accept_tasks,
            "auto_accept_task_types": auto_accept,
            "requires_human_approval": not args.allow_auto_accept,
        },
        "transport": {"type": "local", "command": command, "timeout_seconds": args.timeout_seconds},
        "privacy": dict(DEFAULT_PRIVACY),
        "result_policy": {"allow": list(DEFAULT_ALLOWED_RESULT_FIELDS), "deny": list(DEFAULT_FORBIDDEN_RESULT_FIELDS)},
    }
    if args.dispatch_command:
        manifest["transport"]["dispatch_command"] = list(args.dispatch_command)
    return manifest


def _request_json(base_url: str, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 15.0) -> dict[str, Any] | list[Any]:
    url = base_url.rstrip("/") + (path if path.startswith("/") else f"/{path}")
    body = None
    headers = {"Accept": "application/json, application/a2a+json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user-provided server URL
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Could not reach {url}: {exc.reason}") from exc
    return json.loads(raw) if raw else {}


def check_server(mesh_url: str, timeout: float) -> dict[str, Any]:
    data = _request_json(mesh_url, "/health", timeout=timeout)
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise SystemExit(f"Server health check failed: {data}")
    return data


def register_client(mesh_url: str, manifest: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = _request_json(mesh_url, "/api/nodes", method="POST", payload=manifest, timeout=timeout)
    if not isinstance(data, dict):
        raise SystemExit(f"Registration returned unexpected response: {data}")
    return data


def send_heartbeat(mesh_url: str, node_id: str, status: str, timeout: float) -> dict[str, Any]:
    quoted = urllib.parse.quote(node_id, safe="")
    data = _request_json(mesh_url, f"/api/nodes/{quoted}/heartbeat", method="POST", payload={"status": status}, timeout=timeout)
    if not isinstance(data, dict):
        raise SystemExit(f"Heartbeat returned unexpected response: {data}")
    return data


def write_manifest(manifest: dict[str, Any], config_dir: Path) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    node_id = str(manifest["node_id"])
    path = config_dir / f"{node_id}.manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_systemd_service(args: argparse.Namespace, manifest_path: Path) -> Path:
    script_path = Path(__file__).resolve()
    if not script_path.exists() or str(script_path) == "<stdin>":
        raise SystemExit("--install-systemd requires running install_client.py from a saved file, not stdin")
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    node_id = args.node_id
    service_name = f"hermes-mesh-client-{node_id}.service"
    service_path = service_dir / service_name
    exec_args = [
        sys.executable,
        str(script_path),
        "--yes",
        "--mesh-url",
        args.mesh_url,
        "--node-id",
        node_id,
        "--manifest-path",
        str(manifest_path),
        "--keep-online",
        "--interval",
        str(args.interval),
        "--http-timeout",
        str(args.http_timeout),
    ]
    content = "\n".join(
        [
            "[Unit]",
            "Description=HermesMesh trial client keep-online loop",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            "ExecStart=" + " ".join(shlex.quote(part) for part in exec_args),
            "Restart=always",
            "RestartSec=5",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )
    service_path.write_text(content, encoding="utf-8")
    return service_path


def heartbeat_loop(mesh_url: str, node_id: str, interval: float, timeout: float) -> int:
    print(f"Client {node_id} is online. Sending heartbeat every {interval:g}s. Press Ctrl+C to stop.")
    try:
        while True:
            data = send_heartbeat(mesh_url, node_id, "online", timeout)
            online = data.get("node", {}).get("online_status", {}) if isinstance(data.get("node"), dict) else {}
            label = online.get("label") or online.get("status") or "online"
            print(json.dumps({"ok": True, "node_id": node_id, "status": label}, ensure_ascii=False), flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        try:
            send_heartbeat(mesh_url, node_id, "offline", timeout)
            print(f"Client {node_id} marked offline.")
        except SystemExit as exc:
            print(f"Could not mark client offline: {exc}", file=sys.stderr)
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Register and start a trial HermesMesh Client.")
    parser.add_argument("--mesh-url", help="HermesMesh Server URL, e.g. http://10.0.16.11:8765")
    parser.add_argument("--node-id", default=_default_node_id(), help="Unique client node id")
    parser.add_argument("--display-name", help="Human-readable client name")
    parser.add_argument("--task-type", action="append", help="Task type this Client can handle; repeatable")
    parser.add_argument("--tool", action="append", help="Public capability/tool label; repeatable")
    parser.add_argument("--transport-command", action="append", help="Transport command argv part; repeatable; default: hermes chat -q")
    parser.add_argument("--dispatch-command", action="append", help="Dispatch command argv part for assigned work; repeatable")
    parser.add_argument("--timeout-seconds", type=int, default=120, help="Transport timeout metadata, 1..300")
    parser.add_argument("--allow-auto-accept", action="store_true", help="Mark declared task types as auto-accepted")
    parser.add_argument("--auto-accept-task-type", action="append", help="Task type this client auto-accepts; repeatable")
    parser.add_argument("--no-accept-tasks", action="store_true", help="Register as not currently accepting tasks")
    parser.add_argument("--include-basic-resources", action="store_true", help="Include OS/Python resource labels; never env vars")
    parser.add_argument("--config-dir", default=str(_default_config_dir()), help="Directory for generated manifest")
    parser.add_argument("--manifest-path", help="Use an existing manifest JSON file instead of generating one")
    parser.add_argument("--print-manifest", action="store_true", help="Print manifest before registration")
    parser.add_argument("--dry-run", action="store_true", help="Print manifest and do not register or heartbeat")
    parser.add_argument("--keep-online", action="store_true", help="Start foreground heartbeat loop after registration")
    parser.add_argument("--once", action="store_true", help="Send one heartbeat after registration and exit")
    parser.add_argument("--interval", type=float, default=DEFAULT_HEARTBEAT_INTERVAL, help="Heartbeat loop interval seconds")
    parser.add_argument("--install-systemd", action="store_true", help="Write a user systemd service for durable keep-online loop")
    parser.add_argument("--yes", action="store_true", help="Non-interactive mode; use flags/defaults")
    parser.add_argument("--http-timeout", type=float, default=15.0, help="HTTP timeout seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args = _load_or_prompt_args(args)
    if not args.mesh_url:
        raise SystemExit("--mesh-url is required in --yes mode")
    if args.timeout_seconds < 1 or args.timeout_seconds > 300:
        raise SystemExit("--timeout-seconds must be between 1 and 300")
    if args.interval <= 0:
        raise SystemExit("--interval must be greater than zero")
    if args.manifest_path:
        manifest = json.loads(Path(args.manifest_path).expanduser().read_text(encoding="utf-8"))
        args.node_id = str(manifest.get("node_id") or args.node_id)
    else:
        manifest = build_manifest(args)
    if args.print_manifest or args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    if args.dry_run:
        return 0
    print(f"Checking Server {args.mesh_url} ...")
    check_server(args.mesh_url, args.http_timeout)
    manifest_path = Path(args.manifest_path).expanduser() if args.manifest_path else write_manifest(manifest, Path(args.config_dir).expanduser())
    print(f"Saved manifest: {manifest_path}")
    result = register_client(args.mesh_url, manifest, args.http_timeout)
    print("Registered Client:")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    hb = send_heartbeat(args.mesh_url, str(manifest["node_id"]), "online", args.http_timeout)
    print("Initial heartbeat:")
    print(json.dumps(hb, ensure_ascii=False, indent=2, sort_keys=True))
    if args.install_systemd:
        service_path = write_systemd_service(args, manifest_path)
        print(f"Wrote user systemd service: {service_path}")
        print("Enable it with:")
        print("  systemctl --user daemon-reload")
        print(f"  systemctl --user enable --now {service_path.name}")
    if args.keep_online:
        return heartbeat_loop(args.mesh_url, str(manifest["node_id"]), args.interval, args.http_timeout)
    if args.once:
        return 0
    print("Client registered and heartbeat sent once. Re-run with --keep-online to keep it online in the foreground.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
