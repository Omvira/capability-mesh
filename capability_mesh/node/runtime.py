"""Standalone A2A Node runtime."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from capability_mesh.core import CapabilityMeshValidationError, build_a2a_task, record_a2a_task
from capability_mesh.node.a2a import build_node_agent_card


class NodeRuntimeHandler(BaseHTTPRequestHandler):
    manifest: dict[str, Any] = {}
    mesh_home: Path | None = None
    public_url: str | None = None

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            if path in {"/.well-known/agent-card.json", "/agent-card.json", "/api/agent-card"}:
                self._send_json(build_node_agent_card(self.manifest, public_url=self.public_url or self._server_base_url()), content_type="application/a2a+json; charset=utf-8")
            elif path == "/health":
                self._send_json({"ok": True, "node_id": self.manifest.get("node_id")})
            else:
                self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
        except CapabilityMeshValidationError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            data = self._read_json_body()
            if path in {"/message:send", "/api/a2a/messages", "/api/a2a/tasks/send"}:
                message = data.get("message", data)
                response = build_a2a_task(message)
                record_a2a_task(response, mesh_home=self.mesh_home)
                self._send_json(response, content_type="application/a2a+json; charset=utf-8")
            else:
                self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
        except (CapabilityMeshValidationError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(parsed, dict):
            raise CapabilityMeshValidationError("JSON request body must be an object")
        return parsed

    def _server_base_url(self) -> str:
        host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
        scheme = self.headers.get("X-Forwarded-Proto", "http")
        return f"{scheme}://{host}"

    def _send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK, *, content_type: str = "application/json; charset=utf-8") -> None:
        body = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def make_node_server(
    manifest: dict[str, Any],
    *,
    host: str = "127.0.0.1",
    port: int = 8766,
    mesh_home: str | Path | None = None,
    public_url: str | None = None,
) -> ThreadingHTTPServer:
    class Handler(NodeRuntimeHandler):
        pass

    Handler.manifest = dict(manifest)
    Handler.mesh_home = Path(mesh_home).expanduser() if mesh_home is not None else None
    Handler.public_url = public_url
    return ThreadingHTTPServer((host, port), Handler)


def serve_node(manifest: dict[str, Any], *, host: str = "127.0.0.1", port: int = 8766, mesh_home: str | Path | None = None, public_url: str | None = None) -> None:
    server = make_node_server(manifest, host=host, port=port, mesh_home=mesh_home, public_url=public_url)
    try:
        print(f"Capability Mesh node listening on http://{host}:{server.server_port}")
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    import yaml

    parser = argparse.ArgumentParser(description="Run a standalone Capability Mesh A2A Node server.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--mesh-home", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--public-url")
    args = parser.parse_args(argv)
    with Path(args.manifest).open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
    if not isinstance(loaded, dict):
        raise CapabilityMeshValidationError("manifest must be a mapping")
    serve_node(loaded, host=args.host, port=args.port, mesh_home=args.mesh_home, public_url=args.public_url)
    return 0


__all__ = ["NodeRuntimeHandler", "make_node_server", "serve_node", "main"]
