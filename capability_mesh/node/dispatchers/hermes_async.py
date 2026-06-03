"""Asynchronous Hermes dispatcher for Capability Mesh A2A nodes."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

DEFAULT_TIMEOUT_SECONDS = 3600
DEFAULT_JOBS_DIR = "~/.capability-mesh-node/dispatch-jobs"
DEFAULT_HERMES_COMMAND = ["hermes", "chat", "-q"]
_QUERY_RE = re.compile(r"(?:查询|查一下|status|result)\s+([0-9a-fA-F]{8,64})")


def expand_jobs_dir(jobs_dir: str | Path | None = None) -> Path:
    return Path(jobs_dir or os.environ.get("CAPABILITY_MESH_DISPATCH_JOBS_DIR") or DEFAULT_JOBS_DIR).expanduser()


def extract_text(payload: dict[str, Any]) -> str:
    message = payload.get("message")
    if not isinstance(message, dict):
        return ""
    parts = message.get("parts")
    if not isinstance(parts, list):
        return ""
    texts: list[str] = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            texts.append(part["text"])
    return "\n".join(texts).strip()


def query_job_id(text: str) -> str | None:
    match = _QUERY_RE.search(text)
    return match.group(1).lower() if match else None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_job_dir(jobs_dir: Path, job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{8,64}", job_id):
        raise ValueError("invalid job_id")
    return jobs_dir / job_id


def _read_text(path: Path, limit: int = 12000) -> str:
    if not path.exists():
        return ""
    data = path.read_text(encoding="utf-8", errors="replace")
    if len(data) <= limit:
        return data
    return data[-limit:]


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _worker_code() -> str:
    return r'''
from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

job_dir = Path(sys.argv[1])
meta_path = job_dir / "meta.json"
meta = json.loads(meta_path.read_text(encoding="utf-8"))
command = meta["hermes_command"]
timeout_seconds = int(meta["timeout_seconds"])
prompt = (job_dir / "prompt.txt").read_text(encoding="utf-8")
meta["status"] = "running"
meta["started_at"] = datetime.now(UTC).isoformat()
meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
try:
    run_command = list(command)
    run_input = prompt
    if len(run_command) >= 2 and run_command[-2:] == ["chat", "-q"]:
        run_command.append(prompt)
        run_input = None
    completed = subprocess.run(
        run_command,
        input=run_input,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        shell=False,
    )
    (job_dir / "stdout.txt").write_text(completed.stdout, encoding="utf-8", errors="replace")
    (job_dir / "stderr.txt").write_text(completed.stderr, encoding="utf-8", errors="replace")
    meta["exit_code"] = completed.returncode
    meta["status"] = "completed" if completed.returncode == 0 else "failed"
except subprocess.TimeoutExpired as exc:
    (job_dir / "stdout.txt").write_text(str(exc.stdout or ""), encoding="utf-8", errors="replace")
    (job_dir / "stderr.txt").write_text("timeout", encoding="utf-8")
    meta["exit_code"] = None
    meta["status"] = "timeout"
except Exception as exc:  # pragma: no cover - defensive worker guard
    (job_dir / "stderr.txt").write_text(str(exc), encoding="utf-8", errors="replace")
    meta["exit_code"] = None
    meta["status"] = "failed"
finally:
    meta["finished_at"] = datetime.now(UTC).isoformat()
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
'''


def query_job(job_id: str, *, jobs_dir: str | Path | None = None) -> str:
    root = expand_jobs_dir(jobs_dir)
    job_dir = _safe_job_dir(root, job_id)
    if not job_dir.exists():
        return f"未找到 Hermes 异步任务：{job_id}"
    meta = _read_json(job_dir / "meta.json")
    stdout = _read_text(job_dir / "stdout.txt")
    stderr = _read_text(job_dir / "stderr.txt")
    lines = [
        f"Hermes 异步任务状态：{meta.get('status', 'unknown')}",
        f"job_id: {job_id}",
        f"pid: {meta.get('pid')}",
        f"exit_code: {meta.get('exit_code')}",
        f"created_at: {meta.get('created_at')}",
        f"started_at: {meta.get('started_at')}",
        f"finished_at: {meta.get('finished_at')}",
    ]
    if stdout:
        lines.extend(["", "输出：", stdout.strip()])
    if stderr:
        lines.extend(["", "错误输出：", stderr.strip()])
    return "\n".join(lines).rstrip()


def create_job(
    payload: dict[str, Any],
    *,
    jobs_dir: str | Path | None = None,
    hermes_command: Sequence[str] | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    text = extract_text(payload)
    if not text:
        text = "请处理这条 Capability Mesh A2A 消息。"
    root = expand_jobs_dir(jobs_dir)
    root.mkdir(parents=True, exist_ok=True)
    job_id = secrets.token_hex(8)
    job_dir = root / job_id
    job_dir.mkdir(mode=0o700)
    command = list(hermes_command or DEFAULT_HERMES_COMMAND)
    meta: dict[str, Any] = {
        "job_id": job_id,
        "node_id": payload.get("node_id"),
        "status": "queued",
        "pid": None,
        "exit_code": None,
        "created_at": _utc_now(),
        "started_at": None,
        "finished_at": None,
        "timeout_seconds": timeout_seconds,
        "hermes_command": command,
    }
    _write_json(job_dir / "payload.json", payload)
    (job_dir / "prompt.txt").write_text(text, encoding="utf-8")
    _write_json(job_dir / "meta.json", meta)
    process = subprocess.Popen(
        [sys.executable, "-c", _worker_code(), str(job_dir)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    meta["pid"] = process.pid
    _write_json(job_dir / "meta.json", meta)
    return textwrap.dedent(
        f"""
        已创建 Hermes 异步后台任务。
        job_id: {job_id}
        pid: {process.pid}
        任务会在本地继续执行，不受 Hub relay 超时影响。
        稍后可通过 A2A 发送：查询 {job_id}
        本地结果目录：{job_dir}
        """
    ).strip()


def dispatch_payload(
    payload: dict[str, Any],
    *,
    jobs_dir: str | Path | None = None,
    hermes_command: Sequence[str] | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    text = extract_text(payload)
    job_id = query_job_id(text)
    if job_id:
        return query_job(job_id, jobs_dir=jobs_dir)
    return create_job(payload, jobs_dir=jobs_dir, hermes_command=hermes_command, timeout_seconds=timeout_seconds)


def build_manifest_snippet(dispatch_command: Sequence[str], *, jobs_dir: str | Path, timeout_seconds: int) -> dict[str, Any]:
    return {
        "transport": {
            "timeout_seconds": timeout_seconds,
            "dispatch_command": list(dispatch_command),
        },
        "dispatch": {
            "type": "hermes_async",
            "jobs_dir": str(jobs_dir),
            "timeout_seconds": timeout_seconds,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capability Mesh Hermes async A2A dispatcher")
    parser.add_argument("--jobs-dir", default=None)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--hermes-command", default="hermes")
    parser.add_argument("--hermes-arg", action="append", default=None)
    args = parser.parse_args(argv)
    payload = json.loads(sys.stdin.read() or "{}")
    hermes_args = args.hermes_arg if args.hermes_arg is not None else ["chat", "-q"]
    command = [args.hermes_command, *hermes_args]
    print(dispatch_payload(payload, jobs_dir=args.jobs_dir, hermes_command=command, timeout_seconds=args.timeout_seconds))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
