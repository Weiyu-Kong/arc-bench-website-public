from __future__ import annotations

import hashlib
import json
import subprocess
import difflib
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
import re

from app.models.submission import Submission
from app.services.runtime_path_service import RuntimePathService


LANGUAGE_BY_SUFFIX = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".css": "css",
    ".go": "go",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "jsx",
    ".json": "json",
    ".md": "markdown",
    ".mjs": "javascript",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "shell",
    ".sql": "sql",
    ".svg": "svg",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".txt": "text",
    ".vue": "vue",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}

COMMIT_MESSAGE_PATTERN = re.compile(r"^(?P<node_id>[A-Z0-9.-]+)\s+\((?P<phase>design|implement)\):\s*(?P<summary>.+)$")


@dataclass(frozen=True)
class GitCommitRecord:
    oid: str
    short_oid: str
    committed_at: str
    message: str


@dataclass(frozen=True)
class GitChangedFileRecord:
    file_path: str
    change_type: str
    old_file_path: str | None = None


class SubmissionArtifactService:
    def __init__(self) -> None:
        self.runtime_paths = RuntimePathService()

    def read_node_visual_states(self, submission: Submission) -> dict[str, str]:
        snapshot = self._read_traceability_snapshot(submission)
        if snapshot is None:
            return {}
        node_states = snapshot.get("node_states")
        if not isinstance(node_states, list):
            return {}
        visual_states: dict[str, str] = {}
        for row in node_states:
            if not isinstance(row, dict):
                continue
            req_id = str(row.get("req_id") or "").strip()
            visual_state = self._normalize_node_visual_state(row.get("state"))
            if req_id and visual_state:
                visual_states[req_id] = visual_state
        return visual_states

    def read_traceability(
        self,
        submission: Submission,
        node_id: str,
        *,
        include_tests: bool = True,
    ) -> dict[str, list[dict]]:
        snapshot = self._read_traceability_snapshot(submission)
        if snapshot is None:
            return {"interfaces": [], "tests": []}

        interfaces: list[dict] = []
        tests: list[dict] = []

        for row in snapshot.get("interfaces", []):
            if not isinstance(row, dict):
                continue
            req_ids = self._parse_json_list(row.get("req_ids"))
            if node_id and node_id != "__all__" and node_id not in req_ids:
                continue
            interfaces.append(
                {
                    "interface_id": str(row.get("interface_id") or ""),
                    "req_ids": req_ids,
                    "type": str(row.get("type") or ""),
                    "content": str(row.get("content") or ""),
                    "file_path": str(row.get("file_path") or ""),
                    "first_line": self._normalize_first_line_reference(row.get("first_line")),
                    "implemented": bool(row.get("implemented")),
                    "callers": self._parse_json_list(row.get("callers")),
                    "callees": self._parse_json_list(row.get("callees")),
                }
            )

        if not include_tests:
            return {"interfaces": interfaces, "tests": []}

        e2e_seen: dict[str, int] = {}
        for row in snapshot.get("tests", []):
            if not isinstance(row, dict):
                continue
            req_id = str(row.get("req_id") or "")
            if node_id and node_id != "__all__" and node_id != req_id:
                continue
            test_id = str(row.get("test_id") or "")
            file_path = str(row.get("file_path") or "")
            content = self._read_test_content(submission, file_path=file_path, req_id=req_id)
            resolved_file_path = content[0] if content else file_path
            scenario_id = str(row.get("scenario_id") or "").strip() or None
            if scenario_id is None and str(row.get("type") or "").upper() == "E2E":
                # Assign ordinals from the snapshot's per-requirement order.
                # Generated test IDs may reuse ordinals across requirements.
                ordinal = e2e_seen.get(req_id, 0) + 1
                e2e_seen[req_id] = ordinal
                scenario_id = f"{req_id}::scenario-{ordinal}"
            tests.append(
                {
                    "test_id": test_id,
                    "req_id": req_id,
                    "scenario_id": scenario_id,
                    "type": str(row.get("type") or ""),
                    "file_path": resolved_file_path,
                    "first_line": self._normalize_first_line_reference(row.get("first_line")),
                    "status": self._resolve_test_status(row.get("passed")),
                    "content": content[1] if content else None,
                }
            )

        return {"interfaces": interfaces, "tests": tests}

    def _read_test_content(self, submission: Submission, *, file_path: str, req_id: str) -> tuple[str, str] | None:
        """Resolve replay metadata to the task's real test file and expose its source."""
        normalized = self._normalize_relative_path(file_path) if file_path else None
        # Demo source archives contain the complete expanded project (including
        # backend/frontend internal tests). Read directly from the immutable
        # archive first so a just-moved replay pointer never yields a missing
        # file while the live workspace is being swapped.
        if normalized:
            archived = self.read_snapshot_text(submission, normalized.as_posix())
            if archived is not None:
                return normalized.as_posix(), archived
        workspace_path = self.runtime_paths.resolve_workspace_path(submission)
        if workspace_path is None:
            if normalized:
                content = self.read_snapshot_text(submission, normalized.as_posix())
                if content is not None:
                    return normalized.as_posix(), content
            return None
        project_root = workspace_path / "template"
        candidates: list[Path] = []
        normalized = self._normalize_relative_path(file_path) if file_path else None
        if normalized:
            candidates.append(project_root / normalized)
        tests_root = workspace_path / "tests"
        if tests_root.is_dir() and req_id:
            candidates.extend(sorted(tests_root.glob(f"{req_id}-*.spec.ts")))
        for candidate in candidates:
            try:
                candidate = candidate.resolve()
                candidate.relative_to(workspace_path.resolve())
            except (OSError, ValueError):
                continue
            if candidate.is_file():
                try:
                    return candidate.relative_to(workspace_path).as_posix(), candidate.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    return None
        # Some replay generations are assembled from a project snapshot while
        # the API still points at the previous workspace. Fall back to a safe
        # recursive lookup of the exact relative suffix so internal test source
        # remains available during the atomic workspace swap.
        if normalized:
            suffix = normalized.as_posix()
            try:
                for candidate in workspace_path.rglob(normalized.name):
                    if not candidate.is_file():
                        continue
                    candidate.resolve().relative_to(workspace_path.resolve())
                    relative = candidate.relative_to(workspace_path).as_posix()
                    if relative == suffix or relative.endswith(f"/{suffix}"):
                        return relative, candidate.read_text(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
        return None

    def read_source(
        self,
        submission: Submission,
        *,
        file_path: str,
        first_line: str | int | None,
        kind: str,
        commit_oid: str | None = None,
    ) -> dict[str, str | int]:
        if kind not in {"file", "diff"}:
            raise ValueError("Only kind=file and kind=diff are supported")

        if kind == "diff":
            return self._read_diff_source(submission, file_path=file_path, commit_oid=commit_oid)

        project_root = self._get_project_root(submission)
        if project_root is None:
            normalized_relative_path = self._normalize_relative_path(file_path)
            content = self.read_snapshot_text(submission, normalized_relative_path.as_posix())
            if content is None:
                raise FileNotFoundError(f"Source file not found: {normalized_relative_path.as_posix()}")
            return {
                "kind": "file",
                "file_path": normalized_relative_path.as_posix(),
                "language": LANGUAGE_BY_SUFFIX.get(normalized_relative_path.suffix.lower(), "text"),
                "content": content,
                "first_line": self._resolve_source_highlight_line(content, first_line),
            }

        normalized_relative_path = self._normalize_relative_path(file_path)
        # Demo snapshots store the complete project tree in immutable archives.
        # Prefer the archive member when the live replay workspace has not yet
        # materialized a test file (for example immediately after a pointer move).
        if submission.agent_source == "demo_replay":
            for member_name in (f"project/{normalized_relative_path.as_posix()}", f"template/{normalized_relative_path.as_posix()}"):
                archived = self.read_snapshot_text(submission, member_name)
                if archived is not None:
                    return {
                        "kind": "file",
                        "file_path": normalized_relative_path.as_posix(),
                        "language": LANGUAGE_BY_SUFFIX.get(normalized_relative_path.suffix.lower(), "text"),
                        "content": archived,
                        "first_line": self._resolve_source_highlight_line(archived, first_line),
                    }
        target_path = (project_root / normalized_relative_path).resolve()
        if not target_path.is_file() and normalized_relative_path.parts and normalized_relative_path.parts[0] == "tests":
            target_path = (project_root.parent / normalized_relative_path).resolve()
        project_root_resolved = project_root.resolve()

        try:
            target_path.relative_to(project_root_resolved)
        except ValueError as exc:
            # Test files may be mounted beside template/ in demo workspaces.
            try:
                target_path.relative_to(self.runtime_paths.resolve_workspace_path(submission).resolve())
            except (AttributeError, OSError, ValueError):
                raise FileNotFoundError(f"Source path is outside the submission workspace: {file_path}") from exc

        if not target_path.is_file():
            raise FileNotFoundError(f"Source file not found: {normalized_relative_path.as_posix()}")

        content = target_path.read_text(encoding="utf-8", errors="replace")
        language = LANGUAGE_BY_SUFFIX.get(target_path.suffix.lower(), "text")
        highlight_line = self._resolve_source_highlight_line(content, first_line)

        return {
            "kind": "file",
            "file_path": normalized_relative_path.as_posix(),
            "language": language,
            "content": content,
            "first_line": highlight_line,
        }

    def read_commit_history(self, submission: Submission) -> dict[str, str | list[dict[str, object]]]:
        workspace_path = self.runtime_paths.resolve_workspace_path(submission)
        if workspace_path is None and submission.agent_source == "demo_replay":
            workspace_path = self._find_demo_workspace(submission)
        if workspace_path is None or not workspace_path.is_dir():
            return {"availability": "workspace_unavailable", "commits": []}

        project_root = workspace_path / "template"
        if not project_root.is_dir():
            return {"availability": "workspace_unavailable", "commits": []}

        # Deterministic demos use immutable commit-history JSON rather than a
        # Git repository in the per-run workspace.
        snapshot_root = workspace_path / "submission" / "snapshots" / "ticketbooking"
        checkpoint = project_root / ".arc" / "checkpoint.json"
        if snapshot_root.is_dir():
            index = 0
            try:
                payload = json.loads(checkpoint.read_text(encoding="utf-8"))
                index = int(payload.get("last_completed_index", 0) or 0)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass
            # The initial pointer has no step-0000 history package. Expose the
            # first virtual commit so the demo panel is useful before replay
            # starts as well.
            if index <= 0:
                index = 1
            history_path = snapshot_root / "steps" / f"{index:04d}" / "commit-history.json"
            if history_path.is_file():
                try:
                    payload = json.loads(history_path.read_text(encoding="utf-8"))
                    if not isinstance(payload, dict):
                        return {"availability": "available", "commits": []}
                    raw_commits = payload.get("commits", [])
                    commits: list[dict[str, object]] = []
                    if isinstance(raw_commits, list):
                        for position, raw_commit in enumerate(raw_commits, start=1):
                            if not isinstance(raw_commit, dict):
                                continue
                            commit = dict(raw_commit)
                            oid = str(commit.get("oid") or "").strip()
                            commit["oid"] = oid
                            commit["short_oid"] = str(commit.get("short_oid") or oid[:7])
                            commit["committed_at"] = str(commit.get("committed_at") or f"demo-step-{position:04d}")
                            commit["message"] = str(commit.get("message") or commit.get("summary") or "Demo replay step")
                            changed_files = commit.get("changed_files")
                            normalized_changed_files: list[dict[str, object]] = []
                            if isinstance(changed_files, list):
                                for changed_file in changed_files:
                                    if isinstance(changed_file, str) and changed_file.strip():
                                        normalized_changed_files.append({"file_path": changed_file.strip(), "change_type": "modified"})
                                    elif isinstance(changed_file, dict) and str(changed_file.get("file_path") or "").strip():
                                        normalized_changed_files.append({
                                            "file_path": str(changed_file.get("file_path") or "").strip(),
                                            "change_type": str(changed_file.get("change_type") or "modified"),
                                            "old_file_path": changed_file.get("old_file_path"),
                                        })
                            commit["changed_files"] = normalized_changed_files
                            commits.append(commit)
                    return {"availability": str(payload.get("availability") or "available"), "commits": commits}
                except (OSError, json.JSONDecodeError):
                    return {"availability": "available", "commits": []}

        git_dir = project_root / ".git"
        if not git_dir.exists():
            return {"availability": "git_unavailable", "commits": []}

        commits: list[dict[str, object]] = []
        for commit in self._iter_git_commits(project_root):
            parsed = self._parse_commit_message(commit.message)
            changed_files = self._read_commit_changed_files(project_root, commit.oid)
            commits.append(
                {
                    "oid": commit.oid,
                    "short_oid": commit.short_oid,
                    "committed_at": commit.committed_at,
                    "message": commit.message,
                    "node_id": parsed["node_id"],
                    "phase": parsed["phase"],
                    "summary": parsed["summary"],
                    "changed_files": [
                        {
                            "file_path": changed_file.file_path,
                            "change_type": changed_file.change_type,
                            "old_file_path": changed_file.old_file_path,
                        }
                        for changed_file in changed_files
                    ],
                }
            )
        return {"availability": "available", "commits": commits}

    def _find_demo_workspace(self, submission: Submission) -> Path | None:
        """Recover a demo workspace when its persisted path predates a path change."""
        candidates: list[Path] = []
        if submission.user_id:
            candidates.append(
                self.runtime_paths.get_shared_run_root(submission.user_id, submission.id) / "demo-workspace"
            )

        # Read-only compatibility for demos created before workspaces moved to
        # ARCBENCH_SHARED_STORAGE_ROOT.
        root = self.runtime_paths.settings.user_submissions_root
        if root.is_dir():
            candidates.extend(sorted(root.glob(f"*/demo/{submission.id}/workspace")))
            candidates.extend(sorted(root.glob(f"*/{submission.id}/workspace")))
        for candidate in candidates:
            if (candidate / "submission" / "snapshots" / "ticketbooking" / "manifest.json").is_file():
                return candidate
        return None

    def _get_traceability_dir(self, submission: Submission) -> Path | None:
        workspace_path = self.runtime_paths.resolve_workspace_path(submission)
        if workspace_path is None:
            return None
        traceability_dir = self.runtime_paths.get_arc_dir_from_workspace(workspace_path) / "traceability"
        return traceability_dir if traceability_dir.is_dir() else None

    def refresh_traceability_snapshot_for_workspace(self, workspace_path: Path, *, force: bool = False) -> bool:
        traceability_dir = self.runtime_paths.get_arc_dir_from_workspace(workspace_path) / "traceability"
        return traceability_dir.is_dir() and any(traceability_dir.glob("*.json"))

    def _get_project_root(self, submission: Submission) -> Path | None:
        workspace_path = self.runtime_paths.resolve_workspace_path(submission)
        if workspace_path is None:
            return None
        project_root = workspace_path / "template"
        return project_root if project_root.is_dir() else None

    def _read_traceability_snapshot(self, submission: Submission) -> dict[str, object] | None:
        traceability_dir = self._get_traceability_dir(submission)
        table_names = (
            "requirements",
            "scenarios",
            "interfaces",
            "tests",
            "call_edges",
            "node_states",
            "node_contracts",
        )
        if traceability_dir is not None:
            payload: dict[str, object] = {}
            for table_name in table_names:
                table_payload = self._read_traceability_table(traceability_dir / f"{table_name}.json")
                payload[table_name] = list(table_payload.values())
            return payload
        return self._read_shared_preview_traceability_snapshot(submission, table_names)

    def _read_shared_preview_traceability_snapshot(
        self,
        submission: Submission,
        table_names: tuple[str, ...],
    ) -> dict[str, object] | None:
        snapshot_path = self._get_current_snapshot_path(submission)
        if snapshot_path is None:
            return None

        payload: dict[str, object] = {}
        try:
            with tarfile.open(snapshot_path, "r:gz") as archive:
                for table_name in table_names:
                    table_payload = self._read_traceability_archive_table(
                        archive,
                        f".arc/traceability/{table_name}.json",
                    )
                    payload[table_name] = list(table_payload.values())
        except (OSError, tarfile.TarError):
            return None
        return payload

    def read_snapshot_text(self, submission: Submission, member_name: str) -> str | None:
        content = self.read_snapshot_bytes(submission, member_name)
        return content.decode("utf-8", errors="replace") if content is not None else None

    def read_snapshot_json(self, submission: Submission, member_name: str) -> object | None:
        content = self.read_snapshot_text(submission, member_name)
        if content is None:
            return None
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None

    def read_snapshot_bytes(self, submission: Submission, member_name: str) -> bytes | None:
        normalized = self._normalize_archive_member_name(member_name)
        snapshot_path = self._get_current_snapshot_path(submission)
        if snapshot_path is None:
            return None
        try:
            with tarfile.open(snapshot_path, "r:gz") as archive:
                member = archive.getmember(normalized)
                if not member.isfile() or member.issym() or member.islnk():
                    return None
                extracted = archive.extractfile(member)
                return extracted.read() if extracted is not None else None
        except (KeyError, OSError, tarfile.TarError):
            return None

    def list_snapshot_files(self, submission: Submission) -> list[str] | None:
        snapshot_path = self._get_current_snapshot_path(submission)
        if snapshot_path is None:
            return None
        try:
            with tarfile.open(snapshot_path, "r:gz") as archive:
                return sorted(
                    member.name
                    for member in archive.getmembers()
                    if member.isfile()
                    and not member.issym()
                    and not member.islnk()
                    and not PurePosixPath(member.name).is_absolute()
                    and ".." not in PurePosixPath(member.name).parts
                )
        except (OSError, tarfile.TarError):
            return None

    def write_snapshot_zip(
        self,
        submission: Submission,
        destination: Path,
        *,
        prefix: str = "template",
        include: Callable[[PurePosixPath], bool] | None = None,
    ) -> bool:
        snapshot_path = self._get_current_snapshot_path(submission)
        if snapshot_path is None:
            return False
        try:
            with tarfile.open(snapshot_path, "r:gz") as source, zipfile.ZipFile(
                destination,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                allowZip64=True,
            ) as archive:
                for member in source.getmembers():
                    try:
                        member_name = self._normalize_archive_member_name(member.name)
                    except FileNotFoundError:
                        continue
                    relative = PurePosixPath(member_name)
                    # Only the project-root report is produced by the private
                    # evaluator. Preserve identically named reports in nested
                    # application test projects.
                    if relative.parts == (".arc", "playwright-report.json"):
                        continue
                    if include is not None and not include(relative):
                        continue
                    archive_name = (PurePosixPath(prefix) / relative).as_posix()
                    if member.isdir():
                        info = zipfile.ZipInfo(archive_name.rstrip("/") + "/")
                        info.create_system = 3
                        info.external_attr = (stat.S_IFDIR | stat.S_IMODE(member.mode)) << 16
                        archive.writestr(info, b"")
                        continue
                    if member.issym():
                        info = zipfile.ZipInfo(archive_name)
                        info.create_system = 3
                        info.external_attr = (stat.S_IFLNK | stat.S_IMODE(member.mode)) << 16
                        archive.writestr(info, str(member.linkname).encode("utf-8"))
                        continue
                    if member.islnk():
                        link_path = PurePosixPath(member.linkname)
                        if link_path.is_absolute() or ".." in link_path.parts:
                            continue
                    elif not member.isfile():
                        continue
                    extracted = source.extractfile(member)
                    if extracted is None:
                        continue
                    info = zipfile.ZipInfo(archive_name)
                    info.create_system = 3
                    info.external_attr = (stat.S_IFREG | stat.S_IMODE(member.mode)) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, extracted.read())
            return True
        except (FileNotFoundError, OSError, tarfile.TarError, zipfile.BadZipFile):
            destination.unlink(missing_ok=True)
            return False

    def _get_current_snapshot_path(self, submission: Submission) -> Path | None:
        if not submission.user_id:
            return None
        snapshots_root = self.runtime_paths.get_shared_run_root(submission.user_id, submission.id) / "preview-sources"
        try:
            manifest = json.loads((snapshots_root / "current.json").read_text(encoding="utf-8"))
            snapshot_path = Path(str(manifest["path"])).resolve()
            expected_sha256 = str(manifest["sha256"]).strip().lower()
            snapshot_path.relative_to(snapshots_root.resolve())
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if not snapshot_path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            return None
        return snapshot_path if self._sha256(snapshot_path) == expected_sha256 else None

    @staticmethod
    def _normalize_archive_member_name(member_name: str) -> str:
        normalized = PurePosixPath(str(member_name).strip())
        if str(normalized) in {"", "."} or normalized.is_absolute() or ".." in normalized.parts:
            raise FileNotFoundError(f"Invalid snapshot member path: {member_name}")
        return normalized.as_posix()

    @staticmethod
    def _read_traceability_archive_table(archive: tarfile.TarFile, member_name: str) -> dict[str, dict]:
        try:
            member = archive.getmember(member_name)
        except KeyError:
            return {}
        if not member.isfile():
            return {}
        extracted = archive.extractfile(member)
        if extracted is None:
            return {}
        try:
            payload = json.load(extracted)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): value for key, value in payload.items() if isinstance(value, dict)}

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _read_traceability_table(path: Path) -> dict[str, dict]:
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): value for key, value in payload.items() if isinstance(value, dict)}

    def _read_diff_source(self, submission: Submission, *, file_path: str, commit_oid: str | None) -> dict[str, str | int]:
        if not commit_oid or not commit_oid.strip():
            raise ValueError("commit_oid is required for kind=diff")

        project_root = self._get_project_root(submission)
        if project_root is None:
            raise FileNotFoundError("Submission source workspace is not available")

        git_dir = project_root / ".git"
        if not git_dir.exists() and submission.agent_source == "demo_replay":
            return self._read_demo_diff_source(submission, file_path=file_path, commit_oid=commit_oid.strip())
        if not git_dir.exists():
            raise FileNotFoundError("Git history is not available for this submission")

        safe_commit_oid = commit_oid.strip()
        commit_record = self._get_git_commit(project_root, safe_commit_oid)
        normalized_relative_path = self._normalize_relative_path(file_path) if file_path.strip() else None

        if normalized_relative_path is None:
            diff_output = self._run_git(project_root, ["show", "--format=medium", "--patch", safe_commit_oid])
            output_file_path = f"commit-{commit_record.short_oid}.diff"
        else:
            changed_files = self._read_commit_changed_files(project_root, safe_commit_oid)
            matching_change = self._match_changed_file(normalized_relative_path.as_posix(), changed_files)
            if matching_change is None:
                raise FileNotFoundError(
                    f"Diff file not found in commit {commit_record.short_oid}: {normalized_relative_path.as_posix()}"
                )
            diff_output = self._run_git(
                project_root,
                ["show", "--format=medium", "--patch", safe_commit_oid, "--", matching_change.file_path],
            )
            output_file_path = matching_change.file_path

        return {
            "kind": "diff",
            "file_path": output_file_path,
            "language": "diff",
            "content": diff_output,
            "first_line": 1,
        }

    def _read_demo_diff_source(self, submission: Submission, *, file_path: str, commit_oid: str) -> dict[str, str | int]:
        workspace_path = self.runtime_paths.resolve_workspace_path(submission)
        if workspace_path is None:
            raise FileNotFoundError("Submission source workspace is not available")
        snapshot_root = workspace_path / "submission" / "snapshots" / "ticketbooking"
        manifest_path = snapshot_root / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise FileNotFoundError("Demo snapshot history is not available") from None
        steps = manifest.get("steps", []) if isinstance(manifest, dict) else []
        target_index = None
        for raw in steps:
            if not isinstance(raw, dict):
                continue
            index = int(raw.get("index", 0) or 0)
            try:
                history = json.loads((snapshot_root / "steps" / f"{index:04d}" / "commit-history.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if any(isinstance(item, dict) and str(item.get("oid") or "").strip() == commit_oid for item in history.get("commits", [])):
                target_index = index
                break
        if target_index is None:
            raise FileNotFoundError(f"Commit not found: {commit_oid}")
        target_project = snapshot_root / "steps" / f"{target_index:04d}" / "project"
        previous_project = snapshot_root / "steps" / f"{max(1, target_index - 1):04d}" / "project"
        relative = file_path.strip()
        if relative.startswith("template/"):
            relative = relative[len("template/"):]
        if relative:
            old_path = previous_project / relative
            new_path = target_project / relative
            old_text = old_path.read_text(encoding="utf-8", errors="replace") if old_path.is_file() else ""
            new_text = new_path.read_text(encoding="utf-8", errors="replace") if new_path.is_file() else ""
            diff_output = "".join(difflib.unified_diff(old_text.splitlines(True), new_text.splitlines(True), fromfile=f"a/{relative}", tofile=f"b/{relative}"))
            output_path = relative
        else:
            diff_output = f"Demo snapshot commit {commit_oid[:7]}\n\nChanges are represented by the immutable replay snapshot at step {target_index:04d}."
            output_path = f"demo-step-{target_index:04d}.diff"
        return {"kind": "diff", "file_path": output_path, "language": "diff", "content": diff_output, "first_line": 1}

    def _iter_git_commits(self, project_root: Path) -> list[GitCommitRecord]:
        output = self._run_git(
            project_root,
            [
                "log",
                "--reverse",
                "--date=iso-strict",
                "--pretty=format:%H%x1f%h%x1f%cI%x1f%s",
            ],
        )
        commits: list[GitCommitRecord] = []
        for line in output.splitlines():
            parts = line.split("\x1f")
            if len(parts) != 4:
                continue
            oid, short_oid, committed_at, message = parts
            if not message.strip():
                continue
            commits.append(
                GitCommitRecord(
                    oid=oid.strip(),
                    short_oid=short_oid.strip(),
                    committed_at=committed_at.strip(),
                    message=message.strip(),
                )
            )
        return commits

    def _get_git_commit(self, project_root: Path, commit_oid: str) -> GitCommitRecord:
        output = self._run_git(
            project_root,
            [
                "show",
                "--no-patch",
                "--date=iso-strict",
                "--pretty=format:%H%x1f%h%x1f%cI%x1f%s",
                commit_oid,
            ],
        ).strip()
        parts = output.split("\x1f")
        if len(parts) != 4:
            raise FileNotFoundError(f"Commit not found: {commit_oid}")
        return GitCommitRecord(
            oid=parts[0].strip(),
            short_oid=parts[1].strip(),
            committed_at=parts[2].strip(),
            message=parts[3].strip(),
        )

    def _read_commit_changed_files(self, project_root: Path, commit_oid: str) -> list[GitChangedFileRecord]:
        output = self._run_git(project_root, ["show", "--format=", "--name-status", "--find-renames", commit_oid])
        changed_files: list[GitChangedFileRecord] = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status = parts[0].strip()
            change_type = status[:1] if status else ""
            if not change_type:
                continue
            if change_type == "R" and len(parts) >= 3:
                changed_files.append(
                    GitChangedFileRecord(
                        file_path=parts[2].strip(),
                        change_type="R",
                        old_file_path=parts[1].strip(),
                    )
                )
                continue
            changed_files.append(
                GitChangedFileRecord(
                    file_path=parts[1].strip(),
                    change_type=change_type,
                )
            )
        return changed_files

    @staticmethod
    def _match_changed_file(
        normalized_file_path: str, changed_files: list[GitChangedFileRecord]
    ) -> GitChangedFileRecord | None:
        for changed_file in changed_files:
            if changed_file.file_path == normalized_file_path or changed_file.old_file_path == normalized_file_path:
                return changed_file
        return None

    @staticmethod
    def _parse_commit_message(message: str) -> dict[str, str | None]:
        match = COMMIT_MESSAGE_PATTERN.match(message.strip())
        if not match:
            return {"node_id": None, "phase": None, "summary": None}
        return {
            "node_id": match.group("node_id"),
            "phase": match.group("phase"),
            "summary": match.group("summary").strip(),
        }

    @staticmethod
    def _run_git(project_root: Path, args: list[str]) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(project_root),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
            raise FileNotFoundError(stderr)
        return completed.stdout

    @staticmethod
    def _normalize_relative_path(file_path: str) -> Path:
        normalized = PurePosixPath(file_path.strip())
        if str(normalized) in {"", "."}:
            raise FileNotFoundError("Source file path is required")
        if normalized.is_absolute() or ".." in normalized.parts:
            raise FileNotFoundError(f"Invalid source file path: {file_path}")
        return Path(*normalized.parts)

    @staticmethod
    def _parse_json_list(raw_value: object) -> list[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, list):
            return [str(item) for item in raw_value]
        try:
            parsed = json.loads(str(raw_value))
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed if str(item).strip()]

    @staticmethod
    def _parse_positive_int(raw_value: object) -> int | None:
        if raw_value is None:
            return None
        try:
            parsed = int(str(raw_value).strip())
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @classmethod
    def _normalize_first_line_reference(cls, raw_value: object) -> str | None:
        if raw_value is None:
            return None
        normalized = str(raw_value).strip()
        if not normalized:
            return None
        parsed_int = cls._parse_positive_int(normalized)
        return str(parsed_int) if parsed_int is not None else normalized

    @classmethod
    def _resolve_source_highlight_line(cls, content: str, first_line: str | int | None) -> int:
        lines = content.replace("\r\n", "\n").split("\n")
        parsed_line_number = cls._parse_positive_int(first_line)
        if parsed_line_number is not None:
            return min(parsed_line_number, max(len(lines), 1))

        normalized_matcher = cls._normalize_first_line_matcher(first_line)
        if normalized_matcher:
            for index, line in enumerate(lines, start=1):
                if line.strip() == normalized_matcher:
                    return index
        return 1

    @staticmethod
    def _normalize_first_line_matcher(raw_value: object) -> str | None:
        if raw_value is None:
            return None
        normalized = str(raw_value).replace("\r\n", "\n").strip()
        if not normalized:
            return None
        first_non_empty_line = next((line.strip() for line in normalized.split("\n") if line.strip()), "")
        return first_non_empty_line or None

    @classmethod
    def _resolve_test_status(cls, db_passed: object) -> str | None:
        if db_passed is None:
            return None
        normalized_db_passed = str(db_passed).strip().lower()
        if normalized_db_passed in {"1", "true"}:
            return "passed"
        if normalized_db_passed in {"0", "false"}:
            return "failed"
        return None

    @staticmethod
    def _normalize_node_visual_state(raw_value: object) -> str | None:
        normalized = str(raw_value or "").strip().upper()
        if not normalized or normalized == "UNSEEN":
            return "default"
        if normalized == "DESIGNED":
            return "design"
        if normalized in {"PASSED", "CONVERGED", "CONVERGED_WITH_FAILED_CHILDREN"}:
            return "test-passed"
        if normalized == "FAILED":
            return "test-failed"
        return "default"
