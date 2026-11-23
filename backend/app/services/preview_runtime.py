from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.request import urlopen

from app.services.preview_source_service import PreviewSourceService


class PreviewRuntime:
    """Node-local process lifecycle for one preview generation."""

    def __init__(self, workspace: Path, snapshot: Path, digest: str, host: str, port: int) -> None:
        self.workspace = workspace
        self.snapshot = snapshot
        self.digest = digest
        self.host = host
        self.port = port
        self.process: subprocess.Popen[bytes] | None = None
        self.log_handle = None

    def start(self, timeout: int, check_ownership: Callable[[], None]) -> None:
        deadline = time.monotonic() + timeout
        check_ownership()
        PreviewSourceService.remove_workspace(self.workspace)
        PreviewSourceService.restore(self.snapshot, self.workspace, self.digest)
        frontend = self.workspace / "frontend"
        backend = self.workspace / "backend"
        if not frontend.is_dir() or not backend.is_dir():
            raise RuntimeError("Preview source must contain frontend and backend directories")
        self._run(["npm", "install"], frontend, deadline, check_ownership)
        self._run(["npm", "run", "build"], frontend, deadline, check_ownership)
        self._run(["npm", "install", "--omit=dev"], backend, deadline, check_ownership)
        check_ownership()
        self.log_handle = (self.workspace / "preview.log").open("wb")
        env = {**os.environ, "HOST": self.host, "PORT": str(self.port)}
        self.process = subprocess.Popen(
            ["npm", "run", "start"], cwd=backend, env=env,
            stdout=self.log_handle, stderr=subprocess.STDOUT, start_new_session=True,
        )
        while time.monotonic() < deadline:
            check_ownership()
            if self.process.poll() is not None:
                raise RuntimeError(f"Preview backend exited during startup with code {self.process.returncode}")
            if self.healthy():
                return
            time.sleep(1)
        raise TimeoutError("Preview did not become healthy before startup timeout")

    def healthy(self) -> bool:
        try:
            with urlopen(f"http://127.0.0.1:{self.port}/api/health", timeout=2) as response:
                return response.status == 200
        except (URLError, TimeoutError, OSError):
            return False

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        process = self.process
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        if self.log_handle:
            self.log_handle.close()
        self.process = None

    def cleanup(self) -> None:
        self.stop()
        shutil.rmtree(self.workspace, ignore_errors=True)

    @staticmethod
    def _run(command: list[str], cwd: Path, deadline: float, check_ownership: Callable[[], None]) -> None:
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT)
            while process.poll() is None:
                try:
                    check_ownership()
                except Exception:
                    process.kill()
                    process.wait()
                    raise
                if time.monotonic() >= deadline:
                    process.kill()
                    process.wait()
                    raise TimeoutError(f"{' '.join(command)} exceeded the preview startup timeout")
                time.sleep(1)
            if process.returncode:
                output.seek(0)
                detail = output.read().decode("utf-8", errors="replace").strip()[-2000:]
                raise RuntimeError(f"{' '.join(command)} failed ({process.returncode}): {detail}")
