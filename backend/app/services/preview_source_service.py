from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

from app.services.runtime_path_service import RuntimePathService


class PreviewSourceService:
    """Publishes immutable preview inputs to storage shared by all nodes."""

    # A published source snapshot is also the durable fallback used when the
    # live workspace is no longer available for download. Preserve the whole
    # template tree, including hidden files, generated output and Git metadata;
    # dependency trees are the only entries too large and machine-specific to
    # carry between runs.
    IGNORE_NAMES = {"node_modules"}
    PRUNE_GRACE_SECONDS = 60

    def __init__(self) -> None:
        self.paths = RuntimePathService()

    def publish(self, user_id: str, run_id: str, template_dir: Path) -> tuple[Path, str, str | None]:
        if not template_dir.is_dir():
            raise FileNotFoundError(f"Preview template directory not found: {template_dir}")
        snapshots = self.paths.get_shared_run_root(user_id, run_id) / "preview-sources"
        snapshots.mkdir(parents=True, exist_ok=True)
        source_digest = self._source_digest(template_dir)
        try:
            current_snapshot, current_digest, current_revision, current_source_digest = self.current_details(user_id, run_id)
            if current_source_digest == source_digest and current_snapshot.is_file():
                return current_snapshot, current_digest, current_revision
        except (FileNotFoundError, RuntimeError):
            pass
        fd, temporary_name = tempfile.mkstemp(prefix=".snapshot-", suffix=".tar.gz", dir=snapshots)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            with tarfile.open(temporary, "w:gz", dereference=False) as archive:
                for path in self._iter_snapshot_paths(template_dir):
                    relative = path.relative_to(template_dir)
                    try:
                        archive.add(path, arcname=relative, recursive=False)
                    except FileNotFoundError:
                        # Test servers and agent runtimes use atomic temporary
                        # files. A path can disappear after os.walk sees it;
                        # omitting that transient path is safe for previews.
                        continue
            digest = self._sha256(temporary)
            destination = snapshots / f"{digest}.tar.gz"
            if destination.exists():
                temporary.unlink()
            else:
                os.replace(temporary, destination)
            revision = self._git_revision(template_dir)
            manifest = snapshots / "current.json"
            manifest_fd, manifest_temporary_name = tempfile.mkstemp(prefix=".current-", suffix=".tmp", dir=snapshots)
            os.close(manifest_fd)
            manifest_temporary = Path(manifest_temporary_name)
            manifest_temporary.write_text(
                json.dumps({"path": str(destination), "sha256": digest, "revision": revision, "source_sha256": source_digest}),
                encoding="utf-8",
            )
            os.replace(manifest_temporary, manifest)
            return destination, digest, revision
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def restore(snapshot: Path, destination: Path, expected_sha256: str) -> None:
        if not snapshot.is_file() or PreviewSourceService._sha256(snapshot) != expected_sha256:
            raise RuntimeError("Preview source snapshot is missing or failed integrity validation")
        destination.mkdir(parents=True, exist_ok=False)
        root = destination.resolve()
        links: list[tuple[Path, str]] = []
        directories: list[tuple[Path, int]] = []
        with tarfile.open(snapshot, "r:gz") as archive:
            for member in archive.getmembers():
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts or str(relative) in {"", "."}:
                    raise RuntimeError("Preview source snapshot contains an unsafe path")
                target = destination.joinpath(*relative.parts)
                if member.issym():
                    link = str(member.linkname)
                    if Path(link).is_absolute():
                        raise RuntimeError("Preview source snapshot contains an absolute symbolic link")
                    try:
                        (target.parent / link).resolve().relative_to(root)
                    except ValueError as exc:
                        raise RuntimeError("Preview source snapshot contains an unsafe symbolic link") from exc
                    links.append((target, link))
                    continue
                try:
                    target.resolve().relative_to(root)
                except ValueError as exc:
                    raise RuntimeError("Preview source snapshot contains an unsafe path") from exc
                mode = stat.S_IMODE(member.mode)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    directories.append((target, mode))
                    continue
                if member.islnk():
                    link_path = PurePosixPath(member.linkname)
                    if link_path.is_absolute() or ".." in link_path.parts:
                        raise RuntimeError("Preview source snapshot contains an unsafe hard link")
                elif not member.isfile():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                with target.open("wb") as output:
                    shutil.copyfileobj(extracted, output)
                target.chmod(mode)
        for target, link in links:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(link)
        for target, mode in reversed(directories):
            target.chmod(mode)

    def current(self, user_id: str, run_id: str) -> tuple[Path, str, str | None]:
        snapshot, digest, revision, _source_digest = self.current_details(user_id, run_id)
        return snapshot, digest, revision

    def current_details(self, user_id: str, run_id: str) -> tuple[Path, str, str | None, str | None]:
        manifest = self.paths.get_shared_run_root(user_id, run_id) / "preview-sources" / "current.json"
        if not manifest.is_file():
            raise FileNotFoundError("No shared preview source has been published for this run")
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            snapshot = Path(str(payload["path"]))
            digest = str(payload["sha256"])
            revision = str(payload["revision"]) if payload.get("revision") else None
            source_digest = str(payload["source_sha256"]) if payload.get("source_sha256") else None
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Shared preview source manifest is invalid") from exc
        return snapshot, digest, revision, source_digest

    def prune(self, user_id: str, run_id: str, *, protected_paths: set[Path], keep_newest: int = 2) -> int:
        snapshots = self.paths.get_shared_run_root(user_id, run_id) / "preview-sources"
        if not snapshots.is_dir():
            return 0
        try:
            current_snapshot, _digest, _revision = self.current(user_id, run_id)
            protected_paths.add(current_snapshot.resolve())
        except (FileNotFoundError, RuntimeError):
            pass
        archives = sorted(
            snapshots.glob("*.tar.gz"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        protected_paths.update(path.resolve() for path in archives[:keep_newest])
        removed = 0
        prune_before = time.time() - self.PRUNE_GRACE_SECONDS
        for archive in archives[keep_newest:]:
            if archive.resolve() in protected_paths or archive.stat().st_mtime > prune_before:
                continue
            archive.unlink(missing_ok=True)
            removed += 1
        return removed

    @staticmethod
    def _git_revision(template_dir: Path) -> str | None:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=template_dir, capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    @staticmethod
    def remove_workspace(path: Path) -> None:
        shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _source_digest(self, template_dir: Path) -> str:
        digest = hashlib.sha256()
        digest.update(b"complete-workspace-v1\0")
        for path in self._iter_snapshot_paths(template_dir):
            relative = path.relative_to(template_dir)
            try:
                digest.update(relative.as_posix().encode("utf-8"))
                digest.update(b"\0")
                digest.update(str(path.lstat().st_mode).encode("ascii"))
                digest.update(b"\0")
                if path.is_symlink():
                    digest.update(b"symlink\0")
                    digest.update(os.readlink(path).encode("utf-8"))
                elif path.is_dir():
                    digest.update(b"directory\0")
                elif path.is_file():
                    with path.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
            except FileNotFoundError:
                # See publish(): disappearing runtime files must not prevent a
                # stable source snapshot from being published.
                continue
            digest.update(b"\0")
        return digest.hexdigest()

    def _iter_snapshot_paths(self, template_dir: Path, *, files_only: bool = False):
        paths: list[Path] = []
        for root, dir_names, file_names in os.walk(template_dir):
            root_path = Path(root)
            dir_names[:] = sorted(
                name
                for name in dir_names
                if not self._is_ignored_name(name)
            )
            if not files_only:
                paths.extend(root_path / name for name in dir_names)
            else:
                paths.extend(root_path / name for name in dir_names if (root_path / name).is_symlink())
            paths.extend(
                root_path / name
                for name in sorted(file_names)
                if not self._is_ignored_name(name)
            )
        return sorted(paths, key=lambda path: path.relative_to(template_dir).as_posix())

    @classmethod
    def _is_ignored_name(cls, name: str) -> bool:
        return name in cls.IGNORE_NAMES
