from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path


class DebugLogService:
    def __init__(self, workspace_path: Path, shared_log_path: Path | None = None):
        self.workspace_path = workspace_path
        self.log_path = workspace_path / "execution.debug.log"
        self.shared_log_path = shared_log_path
        if self.log_path.is_file():
            self._publish_shared_snapshot()

    def append(self, source: str, message: str) -> Path:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S +08:00")
        line = f"[{timestamp}] [{source}] {message}"
        with self.log_path.open("a", encoding="utf-8") as output:
            output.write(line + "\n")
        self._publish_shared_snapshot()
        print(line, flush=True)
        return self.log_path

    def append_block(self, source: str, title: str, content: str) -> Path:
        self.append(source, title)
        if not content:
            return self.log_path
        for line in content.splitlines():
            self.append(source, f"| {line}")
        return self.log_path

    def _publish_shared_snapshot(self) -> None:
        if self.shared_log_path is None or not self.log_path.is_file():
            return
        self.shared_log_path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.shared_log_path.with_suffix(".log.partial")
        partial.write_bytes(self.log_path.read_bytes())
        with partial.open("rb") as source:
            os.fsync(source.fileno())
        partial.replace(self.shared_log_path)
