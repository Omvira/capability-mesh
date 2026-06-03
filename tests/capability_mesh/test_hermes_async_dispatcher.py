"""Tests for installable node dispatchers."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import yaml


def test_install_hermes_async_dispatcher_writes_script_and_manifest_snippet(tmp_path: Path) -> None:
    output_script = tmp_path / "a2a_dispatch_hermes.py"
    jobs_dir = tmp_path / "jobs"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "capability_mesh.cli",
            "node",
            "install-dispatcher",
            "hermes-async",
            "--output",
            str(output_script),
            "--jobs-dir",
            str(jobs_dir),
            "--hermes-command",
            sys.executable,
            "--manifest-snippet",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert output_script.exists()
    assert "dispatch_command:" in result.stdout
    snippet = yaml.safe_load(result.stdout)
    assert snippet["transport"]["dispatch_command"] == [sys.executable, str(output_script)]
    assert snippet["dispatch"] == {
        "type": "hermes_async",
        "jobs_dir": str(jobs_dir),
        "timeout_seconds": 3600,
    }


def test_hermes_async_dispatcher_creates_background_job_and_query_returns_result(tmp_path: Path) -> None:
    from capability_mesh.node.dispatchers.hermes_async import dispatch_payload

    jobs_dir = tmp_path / "jobs"
    command = [sys.executable, "-c", "import sys; print('worker received: ' + sys.stdin.read())"]
    created = dispatch_payload(
        {
            "node_id": "node-a",
            "message": {"role": "ROLE_USER", "parts": [{"text": "do long work"}]},
        },
        jobs_dir=jobs_dir,
        hermes_command=command,
    )

    assert "已创建 Hermes 异步后台任务" in created
    job_id = created.split("job_id: ", 1)[1].splitlines()[0]
    status = ""
    for _ in range(50):
        status = dispatch_payload(
            {
                "node_id": "node-a",
                "message": {"role": "ROLE_USER", "parts": [{"text": f"查询 {job_id}"}]},
            },
            jobs_dir=jobs_dir,
            hermes_command=command,
        )
        if "Hermes 异步任务状态：completed" in status:
            break
        time.sleep(0.1)

    assert "Hermes 异步任务状态：completed" in status
    assert "worker received: do long work" in status
    assert (jobs_dir / job_id / "stdout.txt").exists()


def test_hermes_async_dispatcher_cli_handles_stdin_payload(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    payload = {"node_id": "node-a", "message": {"role": "ROLE_USER", "parts": [{"text": "hello"}]}}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "capability_mesh.node.dispatchers.hermes_async",
            "--jobs-dir",
            str(jobs_dir),
            "--hermes-command",
            sys.executable,
            "--hermes-arg=-c",
            "--hermes-arg",
            "import sys; print(sys.stdin.read())",
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=10,
        cwd=Path(__file__).resolve().parents[2],
    )

    assert result.returncode == 0, result.stderr
    assert "已创建 Hermes 异步后台任务" in result.stdout
