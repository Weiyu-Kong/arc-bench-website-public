from __future__ import annotations

import subprocess
import sys
import threading
import os
import signal

from app.core.config import ROOT_DIR


class HostDemoExecutionService:
    """Process registry for host-side deterministic demo replays."""

    _lock = threading.Lock()
    _processes: dict[str, subprocess.Popen] = {}

    @classmethod
    def start(cls, submission_id: str, *, reuse_workspace: bool = False) -> subprocess.Popen:
        """Start a demo supervisor outside the API request and Celery queue."""
        with cls._lock:
            existing = cls._processes.get(submission_id)
            if existing is not None and existing.poll() is None:
                return existing
            command = [sys.executable, "-m", "app.worker.host_demo_runner", submission_id]
            if reuse_workspace:
                command.append("--reuse-workspace")
            log_path = cls._log_file(submission_id)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("ab") as output:
                process = subprocess.Popen(
                    command,
                    cwd=str(ROOT_DIR / "backend"),
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            cls._processes[submission_id] = process
            cls._write_pid_file(submission_id, process.pid)
        threading.Thread(
            target=cls._reap,
            args=(submission_id, process),
            name=f"host-demo-{submission_id}",
            daemon=True,
        ).start()
        return process

    @classmethod
    def _reap(cls, submission_id: str, process: subprocess.Popen) -> None:
        process.wait()
        with cls._lock:
            if cls._processes.get(submission_id) is process:
                cls._processes.pop(submission_id, None)
        cls._remove_pid_file(submission_id)

    @classmethod
    def _pid_file(cls, submission_id: str):
        return ROOT_DIR / "runtime" / "host-demo-pids" / f"{submission_id}.pid"

    @classmethod
    def _log_file(cls, submission_id: str):
        return ROOT_DIR / "runtime" / "host-demo-pids" / f"{submission_id}.log"

    @classmethod
    def _write_pid_file(cls, submission_id: str, pid: int) -> None:
        path = cls._pid_file(submission_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(pid), encoding="utf-8")

    @classmethod
    def _remove_pid_file(cls, submission_id: str) -> None:
        try:
            cls._pid_file(submission_id).unlink()
        except FileNotFoundError:
            pass

    @classmethod
    def register(cls, submission_id: str, process: subprocess.Popen) -> None:
        with cls._lock:
            cls._processes[submission_id] = process

    @classmethod
    def unregister(cls, submission_id: str) -> None:
        with cls._lock:
            cls._processes.pop(submission_id, None)

    @classmethod
    def stop(cls, submission_id: str) -> None:
        with cls._lock:
            process = cls._processes.get(submission_id)
        if process is None or process.poll() is not None:
            try:
                pid = int(cls._pid_file(submission_id).read_text(encoding="utf-8").strip())
                os.killpg(pid, signal.SIGTERM)
                cls._remove_pid_file(submission_id)
                return
            except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
                cls.unregister(submission_id)
                return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        cls.unregister(submission_id)
