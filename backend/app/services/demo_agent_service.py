from __future__ import annotations

import os
from pathlib import Path
import zipfile

from app.core.config import get_settings


class DemoAgentService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.bundle_path = self.settings.demo_agent_zip
        self.bundle_root = self.settings.demo_agent_zip.with_suffix("")

    def build_bundle(self) -> tuple[bytes, str]:
        if self.bundle_root.is_dir():
            return self._build_bundle_from_directory(self.bundle_root), self.bundle_path.name
        if not self.bundle_path.is_file():
            raise FileNotFoundError(f"Demo agent bundle not found: {self.bundle_path}")
        return self._build_bundle_from_archive(self.bundle_path), self.bundle_path.name

    def _build_bundle_from_archive(self, source_path: Path) -> bytes:
        """Repack an existing bundle while excluding install-generated files."""
        from io import BytesIO

        output = BytesIO()
        with zipfile.ZipFile(source_path, "r") as source, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for entry in source.infolist():
                relative = Path(entry.filename)
                if entry.is_dir() or self._should_skip_path(relative):
                    continue
                archive.writestr(entry, source.read(entry))
        return output.getvalue()

    def _build_bundle_from_directory(self, source_root: Path) -> bytes:
        from io import BytesIO

        memory_file = BytesIO()
        with zipfile.ZipFile(memory_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            # Prune large immutable/generated directories before descending
            # into them. Filtering only after Path.rglob() still walks every
            # snapshot and node_modules entry even though none enters the ZIP.
            for current_root, directory_names, file_names in os.walk(source_root, followlinks=False):
                current_path = Path(current_root)
                relative_root = current_path.relative_to(source_root)
                directory_names[:] = sorted(
                    name
                    for name in directory_names
                    if not self._should_skip_path(relative_root / name)
                )
                for filename in sorted(file_names):
                    path = current_path / filename
                    relative_path = path.relative_to(source_root)
                    if self._should_skip_path(relative_path):
                        continue
                    archive.write(path, arcname=relative_path.as_posix())
        return memory_file.getvalue()

    @staticmethod
    def _should_skip_path(relative_path: Path) -> bool:
        parts = relative_path.parts
        if not parts:
            return False
        # Snapshots are large immutable server assets. They are injected into
        # each execution workspace by WorkspaceAssembler instead of being
        # duplicated in every user's demo-agent archive.
        if parts[0] == "snapshots":
            return True
        if parts[0] == ".tmp":
            return True
        if "node_modules" in parts:
            return True
        if parts[0] in {"dist", "build", "coverage", ".vite", ".cache"}:
            return True
        if "__pycache__" in parts:
            return True
        if relative_path.suffix == ".pyc":
            return True
        return False
