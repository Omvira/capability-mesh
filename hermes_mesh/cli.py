"""Standalone CLI for the independent Capability Mesh core.

Run with ``python -m hermes_mesh.cli``.  The registry defaults to
``$HERMES_MESH_HOME`` or ``~/.hermes-mesh`` and has no Hermes dependency.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from hermes_mesh.core import (
    CapabilityMeshValidationError,
    build_default_capability_manifest,
    default_mesh_home,
    filter_task_result,
    list_registered_nodes,
    register_node_manifest,
    validate_capability_manifest,
    validate_optional_skill_proposal,
    validate_task_contract,
)


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
        prog="python -m hermes_mesh.cli",
        description="Standalone privacy-first Capability Mesh helpers.",
    )
    parser.add_argument(
        "--mesh-home",
        default=None,
        help="Mesh registry home; defaults to $HERMES_MESH_HOME or ~/.hermes-mesh",
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except (CapabilityMeshValidationError, OSError, yaml.YAMLError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
