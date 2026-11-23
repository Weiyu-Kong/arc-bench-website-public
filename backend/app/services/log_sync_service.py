"""Bounded, atomic log snapshots for the shared filesystem.

The executor owns the live Docker process. API processes only read completed
files, so NFS never has to expose a partially written log chunk.
"""

from __future__ import annotations

import os
from pathlib import Path


class LogSyncService:
    def __init__(self, root: Path, *, max_bytes_per_window: int, max_bytes_per_run: int) -> None:
        self.root = root
        self.max_bytes_per_window = max(1, max_bytes_per_window)
        self.max_bytes_per_run = max(1, max_bytes_per_run)
        self.published: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
        self.seen_lengths: dict[str, int] = {"stdout": 0, "stderr": 0}

    def sync(self, stream: str, content: str) -> int:
        """Atomically publish a bounded cumulative snapshot of one stream."""
        if stream not in {"stdout", "stderr"}:
            return 0
        raw = content.encode("utf-8", errors="replace")
        new_bytes = max(0, len(raw) - self.seen_lengths[stream])
        self.seen_lengths[stream] = len(raw)
        available = max(0, self.max_bytes_per_run - sum(len(value) for value in self.published.values()))
        allowed = min(self.max_bytes_per_window, available)
        start = len(raw) - new_bytes
        chunk = raw[start : start + allowed]
        self.published[stream].extend(chunk)
        snapshot = bytes(self.published[stream])
        discarded = max(0, new_bytes - len(chunk))
        self.root.mkdir(parents=True, exist_ok=True)
        final = self.root / f"{stream}.log"
        partial = final.with_suffix(".log.partial")
        partial.write_bytes(snapshot)
        with partial.open("rb") as handle:
            os.fsync(handle.fileno())
        partial.replace(final)
        return discarded
