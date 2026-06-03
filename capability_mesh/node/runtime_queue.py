"""Durable bounded worker runtime for local node and Hub background work."""

from __future__ import annotations

import json
import os
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, wait
from pathlib import Path
from threading import Lock
from typing import Any, Callable


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _redact_text(text: str) -> str:
    words = []
    for word in text.split():
        if any(marker in word.lower() for marker in ("secret", "token", "password", "credential")):
            words.append("[REDACTED]")
        else:
            words.append(word)
    return " ".join(words)


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if any(marker in str(key).lower() for marker in ("secret", "token", "password", "credential")):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_value(item)
        return redacted
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


class DurableTaskRuntime:
    """Small JSON-backed task runtime with bounded thread workers."""

    def __init__(self, mesh_home: str | Path, *, max_workers: int = 2, autostart: bool = True, recover_stale: bool = True) -> None:
        self.mesh_home = Path(mesh_home).expanduser()
        self.tasks_dir = self.mesh_home / "runtime" / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        if recover_stale:
            self._mark_stale_records_failed()
        self._executor: ThreadPoolExecutor | None = ThreadPoolExecutor(max_workers=max(1, max_workers)) if autostart else None
        self._futures: set[Future[Any]] = set()
        self._lock = Lock()

    def submit(self, fn: Callable[[], Any], *, task_id: str | None = None) -> str:
        if self._executor is None:
            raise RuntimeError("runtime is not started")
        runtime_id = task_id or f"runtime-{uuid.uuid4().hex}"
        self._write_record(
            runtime_id,
            {
                "id": runtime_id,
                "state": "queued",
                "attempts": 0,
                "created_at": _utc_now_iso(),
                "updated_at": _utc_now_iso(),
                "started_at": "",
                "completed_at": "",
                "error": "",
                "transitions": [{"state": "queued", "timestamp": _utc_now_iso()}],
            },
        )
        future = self._executor.submit(self._run, runtime_id, fn)
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(self._forget_future)
        return runtime_id

    def _forget_future(self, future: Future[Any]) -> None:
        with self._lock:
            self._futures.discard(future)

    def _run(self, task_id: str, fn: Callable[[], Any]) -> None:
        record = self.get_record(task_id)
        record["state"] = "running"
        record["attempts"] = int(record.get("attempts", 0)) + 1
        record["started_at"] = record.get("started_at") or _utc_now_iso()
        record["updated_at"] = _utc_now_iso()
        record.setdefault("transitions", []).append({"state": "running", "timestamp": _utc_now_iso()})
        self._write_record(task_id, record)
        try:
            result = fn()
        except Exception as exc:
            record = self.get_record(task_id)
            record["state"] = "failed"
            record["error"] = _redact_text(str(exc))
            record["updated_at"] = _utc_now_iso()
            record.setdefault("transitions", []).append({"state": "failed", "timestamp": _utc_now_iso()})
            self._write_record(task_id, record)
            return
        record = self.get_record(task_id)
        record["state"] = "completed"
        record["result"] = _redact_value(result)
        record["completed_at"] = _utc_now_iso()
        record["updated_at"] = _utc_now_iso()
        record.setdefault("transitions", []).append({"state": "completed", "timestamp": _utc_now_iso()})
        self._write_record(task_id, record)

    def drain(self, *, timeout: float | None = None) -> None:
        with self._lock:
            futures = set(self._futures)
        if futures:
            wait(futures, timeout=timeout)

    def shutdown(self, *, wait_for_tasks: bool = True) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=wait_for_tasks, cancel_futures=not wait_for_tasks)
            self._executor = None

    def _mark_stale_records_failed(self) -> None:
        for path in sorted(self.tasks_dir.glob("*.json")):
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(loaded, dict) or loaded.get("state") not in {"queued", "running"}:
                continue
            loaded["state"] = "failed"
            loaded["error"] = "task interrupted before runtime recovery"
            loaded["updated_at"] = _utc_now_iso()
            loaded.setdefault("transitions", []).append({"state": "failed", "timestamp": _utc_now_iso(), "reason": "runtime recovery"})
            self._write_record(str(path.with_suffix("").name), loaded)

    def get_record(self, task_id: str) -> dict[str, Any]:
        path = self.tasks_dir / f"{task_id}.json"
        with path.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise RuntimeError(f"invalid runtime task record: {task_id}")
        return loaded

    def _write_record(self, task_id: str, record: dict[str, Any]) -> None:
        path = self.tasks_dir / f"{task_id}.json"
        tmp_path = self.tasks_dir / f".{task_id}.{uuid.uuid4().hex}.tmp"
        payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        with tmp_path.open("w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)


__all__ = ["DurableTaskRuntime"]
