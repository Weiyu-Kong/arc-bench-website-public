from __future__ import annotations

from pathlib import Path
import hashlib

from app.core.config import get_settings
from app.models.submission import Submission
from app.models.user import User


class RuntimePathService:
    def __init__(self) -> None:
        self.settings = get_settings()

    @staticmethod
    def _sanitize_segment(value: str) -> str:
        normalized = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value.strip())
        normalized = normalized.strip("-")
        return normalized or "unknown"

    def get_user_root_by_identity(self, username: str, user_id: str) -> Path:
        user_segment = self._sanitize_segment(username)
        return self.settings.user_submissions_root / f"{user_segment}-{user_id}"

    def bucket_for_user(self, user_id: str) -> int:
        """Return a stable bucket without relying on Python's random hash seed."""
        digest = hashlib.sha256(user_id.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % self.settings.storage_bucket_count

    def get_shared_user_root(self, user_id: str) -> Path:
        return self.settings.shared_storage_root / f"bucket-{self.bucket_for_user(user_id):02d}" / "users" / user_id

    def get_shared_submission_root(self, user_id: str, submission_id: str) -> Path:
        return self.get_shared_user_root(user_id) / "submissions" / submission_id

    def get_shared_run_root(self, user_id: str, run_id: str) -> Path:
        return self.get_shared_user_root(user_id) / "runs" / run_id

    def get_shared_run_requirements_root(self, user_id: str, run_id: str) -> Path:
        return self.get_shared_run_root(user_id, run_id) / "requirements"

    def get_shared_log_root(self, user_id: str, run_id: str, generation: int) -> Path:
        return self.get_shared_run_root(user_id, run_id) / "logs" / f"generation-{generation}"

    def get_shared_log_path(self, user_id: str, run_id: str, generation: int, stream: str = "stdout") -> Path:
        if stream not in {"stdout", "stderr", "execution.debug"}:
            raise ValueError("Unsupported log stream")
        return self.get_shared_log_root(user_id, run_id, generation) / f"{stream}.log"

    def get_shared_control_path(self, user_id: str, run_id: str, generation: int, name: str) -> Path:
        if name not in {"pause", "resume", "continue"}:
            raise ValueError("Unsupported run control")
        return self.get_shared_run_root(user_id, run_id) / "control" / f"generation-{generation}" / f"{name}.request.json"

    def get_user_root(self, user: User) -> Path:
        return self.get_user_root_by_identity(user.username, user.id)

    def get_submission_root(self, submission: Submission, username: str | None = None) -> Path:
        user_name = username or "user"
        if submission.user_id:
            return self.get_user_root_by_identity(user_name, submission.user_id) / submission.id
        return self.settings.user_submissions_root / "anonymous" / submission.id

    def get_submission_root_by_identity(self, username: str, user_id: str, submission_id: str) -> Path:
        return self.get_user_root_by_identity(username, user_id) / submission_id

    def get_workspace_root(
        self,
        submission: Submission,
        generation: int,
        username: str | None = None,
    ) -> Path:
        user_id = str(submission.user_id or "anonymous")
        generation_segment = f"generation-{generation}"
        return self.settings.executor_workspace_root / self._sanitize_segment(user_id) / str(submission.id) / generation_segment / "workspace"

    def get_host_demo_workspace_root(self, submission: Submission, user: User) -> Path:
        """Return the stable shared workspace used by host demo replay.

        Demo state is read directly by API replicas, so it must live beside
        the run's shared logs and controls rather than on one host's disk.
        """
        user_id = str(submission.user_id or user.id)
        return self.get_shared_run_root(user_id, str(submission.id)) / "demo-workspace"

    @staticmethod
    def get_template_root_from_workspace(workspace_path: Path) -> Path:
        return workspace_path / "template"

    @staticmethod
    def get_arc_dir_from_workspace(workspace_path: Path) -> Path:
        return workspace_path / "template" / ".arc"

    def get_archive_path(self, submission: Submission, username: str | None = None) -> Path:
        return self.get_submission_root(submission, username=username) / "agent.zip"

    @staticmethod
    def resolve_existing_path(path_value: str | None) -> Path | None:
        if not path_value:
            return None
        path = Path(path_value)
        return path if path.exists() else None

    def resolve_workspace_path(self, submission: Submission) -> Path | None:
        """Resolve a workspace locally, remapping shared Demo paths if needed."""
        persisted = self.resolve_existing_path(submission.workspace_path)
        if persisted is not None:
            return persisted
        # Absolute paths persisted by another API host can have a different
        # mount prefix. Demo runs have a stable logical shared-storage path.
        if submission.agent_source == "demo_replay" and submission.user_id:
            shared = self.get_shared_run_root(str(submission.user_id), str(submission.id)) / "demo-workspace"
            return shared if shared.exists() else None
        return None
