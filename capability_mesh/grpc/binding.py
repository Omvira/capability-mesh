"""Optional gRPC binding adapter for A2A deployments.

The package ships the proto contract as a stable deployment artifact. Projects that
need a concrete gRPC server can generate Python stubs from ``a2a.proto`` and adapt
these pure helpers to their runtime.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from capability_mesh.core import build_a2a_list_tasks_response, build_a2a_task, cancel_a2a_task, get_a2a_task, record_a2a_task


PROTO_PATH = "capability_mesh/grpc/a2a.proto"


def send_message_json(message: Mapping[str, Any], *, mesh_home: str | None = None) -> str:
    response = build_a2a_task(message)
    record_a2a_task(response, mesh_home=mesh_home)
    return json.dumps(response, ensure_ascii=False, sort_keys=True)


def get_task_json(task_id: str, *, mesh_home: str | None = None) -> str:
    return json.dumps(get_a2a_task(task_id, mesh_home=mesh_home), ensure_ascii=False, sort_keys=True)


def list_tasks_json(*, mesh_home: str | None = None) -> str:
    return json.dumps(build_a2a_list_tasks_response(mesh_home), ensure_ascii=False, sort_keys=True)


def cancel_task_json(task_id: str, *, mesh_home: str | None = None) -> str:
    return json.dumps(cancel_a2a_task(task_id, mesh_home=mesh_home), ensure_ascii=False, sort_keys=True)
