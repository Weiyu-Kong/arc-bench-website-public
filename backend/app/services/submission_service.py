import json
import logging
import os
import errno
import stat
import subprocess
import shutil
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Literal, Optional, List
from threading import Event

from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.agent_archive_layout import required_agent_root_files, resolve_agent_root
from app.services.arc_reference_service import ARC_ENTRYPOINT, add_arc_reference
from app.core.enums import AgentSourceType, RuntimeType, SubmissionStatus
from app.models.requirement import Requirement
from app.models.competition_account import CompetitionEntry, Team, TeamMembership
from app.models.run import Run as Submission
from app.models.run_attempt import RunAttempt
from app.models.submission import Submission as AgentSubmission
from app.models.user import User
from app.schemas.submission import (
    StepState,
    SubmissionDetail,
    RunSummary,
    SubmissionRunnerEvent,
    SubmissionSummary,
    SubmissionVisualEvent,
    WorkspaceFileEntry,
    WorkspaceFileListPayload,
    FileUpdatePayload,
    TestCreatePayload,
    TestCreateResponse,
    TestType,
)
from app.services.docker_manager import DockerManager
from app.services.demo_replay_loader import (
    DemoReplayPaths,
    DemoReplayStep,
    find_replay_step_index,
    load_demo_agent_module,
    load_demo_replay_steps,
    resolve_demo_replay_paths,
)
from app.services.preview_service import PreviewService
from app.services.lock_service import distributed_locked
from app.services.notification_service import NotificationService
from app.services.result_parser import ResultParser
from app.services.runtime_path_service import RuntimePathService
from app.services.submission_artifact_service import SubmissionArtifactService
from app.services.submission_event_stream import SubmissionEventStream
from app.services.workspace_assembler import WorkspaceAssembler
from app.services.requirement_catalog import RequirementCatalogService


logger = logging.getLogger(__name__)


DEFAULT_STEPS = [
    StepState(
        key="deploy_agent",
        title="Preparing environment",
        status="pending",
        description="Pull runner container, prepare workspace, and initialize the selected agent runtime.",
    ),
    StepState(
        key="start_agent",
        title="Running agent",
        status="pending",
        description="Execute the selected agent until it finishes the task and exits cleanly.",
    ),
    StepState(
        key="run_tests",
        title="Evaluating result",
        status="pending",
        description="Execute the benchmark test suite against the finished task output.",
    ),
]

DEMO_BASE_TREE_SENTINEL = "__arcbench_base_tree__"


class ExecutionOwnershipLost(RuntimeError):
    pass


class RunService:
    def __init__(
        self,
        db: Session,
        *,
        run_id: str | None = None,
        attempt_id: str | None = None,
        generation: int | None = None,
        ownership_lost: Event | None = None,
    ):
        self.db = db
        self.settings = get_settings()
        self.runtime_paths = RuntimePathService()
        self.artifact_service = SubmissionArtifactService()
        self.execution_owner = (run_id, attempt_id, generation) if run_id and attempt_id and generation is not None else None
        self.ownership_lost = ownership_lost

    def _assert_execution_owner(self, *, lock: bool = False) -> None:
        if self.execution_owner is None:
            return
        if self.ownership_lost is not None and self.ownership_lost.is_set():
            raise ExecutionOwnershipLost("Run attempt lease was lost")
        run_id, attempt_id, generation = self.execution_owner
        query = select(Submission.id).where(
            Submission.id == run_id,
            Submission.active_attempt_id == attempt_id,
            Submission.active_generation == generation,
        )
        if lock:
            query = query.with_for_update()
        with self.db.no_autoflush:
            owned = self.db.execute(query).scalar_one_or_none()
        if owned is None:
            if self.ownership_lost is not None:
                self.ownership_lost.set()
            raise ExecutionOwnershipLost("Run attempt is no longer active")

    def _commit(self) -> None:
        self._assert_execution_owner(lock=True)
        self.db.commit()

    def _get_submission_user(self, user_id: str) -> User:
        user = self.db.get(User, user_id)
        if not user:
            raise LookupError(f"User '{user_id}' not found")
        return user

    def get_submission_checkpoint_path(self, submission: Submission) -> Path | None:
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is None:
            return None
        return self.runtime_paths.get_arc_dir_from_workspace(workspace_path) / "checkpoint.json"

    def get_pause_request_path(self, submission: Submission) -> Path | None:
        if not submission.user_id:
            return None
        return self.runtime_paths.get_shared_control_path(submission.user_id, submission.id, submission.active_generation, "pause")

    def get_resume_request_path(self, submission: Submission) -> Path | None:
        if not submission.user_id:
            return None
        return self.runtime_paths.get_shared_control_path(submission.user_id, submission.id, submission.active_generation, "resume")

    def get_continue_request_path(self, submission: Submission) -> Path | None:
        if not submission.user_id:
            return None
        return self.runtime_paths.get_shared_control_path(submission.user_id, submission.id, submission.active_generation, "continue")

    def read_checkpoint(self, submission: Submission) -> dict:
        checkpoint_path = self.get_submission_checkpoint_path(submission)
        if checkpoint_path is None or not checkpoint_path.exists():
            return {}
        try:
            payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def write_checkpoint(self, submission: Submission, payload: dict) -> Path:
        checkpoint_path = self.get_submission_checkpoint_path(submission)
        if checkpoint_path is None:
            raise FileNotFoundError("Submission workspace is not available")
        self._write_json_atomic(checkpoint_path, payload)
        return checkpoint_path

    def get_template_repo_path(self, submission: Submission) -> Path | None:
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is None:
            return None
        template_path = workspace_path / "template"
        return template_path if template_path.is_dir() else None

    def _get_demo_replay_paths(self, submission: Submission) -> DemoReplayPaths | None:
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is None:
            return None
        return resolve_demo_replay_paths(workspace_path)

    def _load_demo_replay_steps(self, submission: Submission) -> tuple[Path, DemoReplayPaths, list[DemoReplayStep]]:
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is None:
            raise FileNotFoundError("Submission workspace is not available")
        replay_paths = resolve_demo_replay_paths(workspace_path)
        if replay_paths is None:
            raise ValueError("Submission does not contain ARC demo replay metadata")
        steps = load_demo_replay_steps(workspace_path, replay_paths)
        if not steps:
            raise ValueError("ARC demo replay metadata is empty")
        return workspace_path, replay_paths, steps

    def _is_demo_replay_submission(self, submission: Submission) -> bool:
        # Snapshot demos no longer require the legacy ``arc-replay`` metadata
        # tree. The persisted agent source is the authoritative discriminator;
        # retain the metadata check for older demo workspaces.
        return (
            submission.agent_source == AgentSourceType.DEMO_REPLAY.value
            or self._get_demo_replay_paths(submission) is not None
        )

    def _is_arc_workspace(self, submission: Submission) -> bool:
        """Return true for normal ARC runs as well as the deterministic demo."""
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is None:
            return False
        project_dir = workspace_path / "template"
        if submission.agent_source == AgentSourceType.DEMO_REPLAY.value:
            return (
                (project_dir / ".arc" / "processing_queue.json").is_file()
                and (workspace_path / "submission" / "snapshots" / "ticketbooking" / "manifest.json").is_file()
            )
        return (project_dir / ".git").is_dir() and (
            (project_dir / ".arc" / "processing_queue.json").is_file()
            or submission.agent_source == AgentSourceType.BUILTIN_ARC_AGENT.value
        )

    def _resolve_manual_edit_context(
        self,
        submission: Submission,
        *,
        checkpoint: dict | None = None,
    ) -> dict[str, object]:
        normalized_checkpoint = dict(checkpoint or self.read_checkpoint(submission))
        if not self._is_demo_replay_submission(submission):
            last_completed_index = int(normalized_checkpoint.get("last_completed_index", 0) or 0)
            completed = normalized_checkpoint.get("completed")
            last_entry = completed[-1] if isinstance(completed, list) and completed else {}
            workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
            queue_path = workspace_path / "template" / ".arc" / "processing_queue.json" if workspace_path else None
            queue_task = {}
            if queue_path and queue_path.is_file():
                try:
                    queue = json.loads(queue_path.read_text(encoding="utf-8"))
                    tasks = queue.get("tasks", []) if isinstance(queue, dict) else []
                    queue_task = next((task for task in tasks if isinstance(task, dict) and task.get("status") != "COMPLETED"), {})
                except (OSError, json.JSONDecodeError):
                    queue_task = {}
            return {
                "checkpoint": normalized_checkpoint,
                "steps": [],
                "last_completed_index": max(0, last_completed_index),
                "current_node_id": str(normalized_checkpoint.get("current_node_id") or queue_task.get("node_id") or last_entry.get("node_id") or "").strip() or None,
                "current_phase": str(normalized_checkpoint.get("current_phase") or queue_task.get("phase") or last_entry.get("phase") or "").strip().lower() or None,
                "base_source_commit": str(last_entry.get("source_commit") or DEMO_BASE_TREE_SENTINEL),
            }

        _workspace_path, _replay_paths, steps = self._load_demo_replay_steps(submission)
        last_completed_index = int(normalized_checkpoint.get("last_completed_index", 0) or 0)
        last_completed_index = max(0, min(last_completed_index, len(steps)))
        next_step = steps[last_completed_index] if last_completed_index < len(steps) else None
        if last_completed_index > 0:
            base_source_commit = steps[last_completed_index - 1].source_commit
        else:
            base_source_commit = DEMO_BASE_TREE_SENTINEL
        return {
            "checkpoint": normalized_checkpoint,
            "steps": steps,
            "last_completed_index": last_completed_index,
            "current_node_id": next_step.node_id if next_step else None,
            "current_phase": next_step.phase if next_step else None,
            "base_source_commit": base_source_commit,
        }

    @staticmethod
    def _normalize_pending_test_creations(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                normalized.append(text)
        return normalized

    def _build_manual_edit_commit_message(self, node_id: str | None, phase: str | None) -> str:
        normalized_node_id = str(node_id or "").strip() or "ROOT"
        normalized_phase = str(phase or "").strip().lower()
        if normalized_phase not in {"design", "implement"}:
            normalized_phase = "implement"
        return f"{normalized_node_id} ({normalized_phase}): user edit"

    def can_manual_edit(self, submission: Submission) -> bool:
        if submission.status != SubmissionStatus.PAUSED.value:
            return False
        return self._is_arc_workspace(submission)

    def get_manual_edit_state(self, submission: Submission) -> dict[str, object]:
        if not self._is_arc_workspace(submission):
            return {
                "can_manual_edit": False,
                "manual_edit_node_id": None,
                "manual_edit_phase": None,
                "manual_edit_dirty": False,
            }

        context = self._resolve_manual_edit_context(submission)
        checkpoint = context["checkpoint"] if isinstance(context.get("checkpoint"), dict) else {}
        manual_edit_session = checkpoint.get("manual_edit_session")
        session = manual_edit_session if isinstance(manual_edit_session, dict) else {}
        node_id = str(session.get("current_node_id") or context.get("current_node_id") or "").strip() or None
        phase = str(session.get("current_phase") or context.get("current_phase") or "").strip().lower() or None
        dirty = bool(session.get("has_workspace_changes")) or bool(session.get("has_traceability_changes"))
        return {
            "can_manual_edit": self.can_manual_edit(submission),
            "manual_edit_node_id": node_id,
            "manual_edit_phase": phase if phase in {"design", "implement"} else None,
            "manual_edit_dirty": dirty,
        }

    def prepare_manual_edit_session(self, submission: Submission) -> dict:
        context = self._resolve_manual_edit_context(submission)
        checkpoint = dict(context["checkpoint"])
        existing_session = checkpoint.get("manual_edit_session")
        existing = existing_session if isinstance(existing_session, dict) else {}
        node_id = str(existing.get("current_node_id") or context.get("current_node_id") or "").strip() or None
        phase = str(existing.get("current_phase") or context.get("current_phase") or "").strip().lower() or None
        commit_message = self._build_manual_edit_commit_message(node_id, phase)
        checkpoint["pause_mode"] = "manual_edit"
        checkpoint["current_node_id"] = node_id
        checkpoint["current_phase"] = phase
        checkpoint["runtime_state_restored"] = True
        checkpoint["resume_requires_restart"] = True
        checkpoint["resume_patch_conflict"] = False
        checkpoint["manual_edit_session"] = {
            "base_completed_index": int(existing.get("base_completed_index", context.get("last_completed_index", 0)) or 0),
            "current_node_id": node_id,
            "current_phase": phase,
            "base_source_commit": str(existing.get("base_source_commit") or context.get("base_source_commit") or DEMO_BASE_TREE_SENTINEL),
            "has_workspace_changes": bool(existing.get("has_workspace_changes")),
            "has_traceability_changes": bool(existing.get("has_traceability_changes")),
            "pending_test_creations": self._normalize_pending_test_creations(existing.get("pending_test_creations")),
            "pending_commit_message": commit_message,
        }
        self.write_checkpoint(submission, checkpoint)
        return checkpoint

    def _update_manual_edit_session(
        self,
        submission: Submission,
        *,
        has_workspace_changes: bool | None = None,
        has_traceability_changes: bool | None = None,
        append_test_creation: str | None = None,
    ) -> dict:
        if has_workspace_changes or has_traceability_changes or append_test_creation:
            submission.manual_edit_used = True
            self.db.add(submission)
            self.db.commit()
            self.db.refresh(submission)
        checkpoint = self.prepare_manual_edit_session(submission)
        manual_edit_session = checkpoint.get("manual_edit_session")
        if not isinstance(manual_edit_session, dict):
            raise RuntimeError("Manual edit session is not available")
        if has_workspace_changes is not None:
            manual_edit_session["has_workspace_changes"] = bool(has_workspace_changes or manual_edit_session.get("has_workspace_changes"))
        if has_traceability_changes is not None:
            manual_edit_session["has_traceability_changes"] = bool(has_traceability_changes or manual_edit_session.get("has_traceability_changes"))
        if append_test_creation:
            pending = self._normalize_pending_test_creations(manual_edit_session.get("pending_test_creations"))
            pending.append(append_test_creation)
            manual_edit_session["pending_test_creations"] = pending
        checkpoint["manual_edit_session"] = manual_edit_session
        self.write_checkpoint(submission, checkpoint)
        return checkpoint

    def _reset_arc_queue_for_node(self, submission: Submission, node_id: str) -> None:
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is None:
            raise FileNotFoundError("Submission workspace is not available")
        queue_path = workspace_path / "template" / ".arc" / "processing_queue.json"
        if not queue_path.is_file():
            return
        try:
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("ARC processing queue is unreadable") from exc
        tasks = queue.get("tasks") if isinstance(queue, dict) else None
        if not isinstance(tasks, list):
            return
        start = next((index for index, task in enumerate(tasks) if isinstance(task, dict) and task.get("node_id") == node_id and str(task.get("phase", "")).upper() == "DESIGN"), None)
        if start is None:
            raise ValueError(f"Requirement node {node_id} is not present in the ARC processing queue")
        node_states = queue.setdefault("node_states", {})
        affected_node_ids: set[str] = set()
        for task in tasks[start:]:
            if not isinstance(task, dict):
                continue
            task["status"] = "PENDING"
            task_node = str(task.get("node_id") or "").strip()
            if task_node:
                node_states[task_node] = "UNSEEN"
                affected_node_ids.add(task_node)
        queue["last_task_id"] = None
        queue_path.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        traceability_path = workspace_path / "template" / ".arc" / "traceability" / "node_states.json"
        states = self._read_json_table(traceability_path)
        for key, row in states.items():
            req_id = str(row.get("req_id") or key).strip()
            if req_id in affected_node_ids:
                row["state"] = "UNSEEN"
        self._write_json_table(traceability_path, states)

    def read_submission_task_documents(self, submission: Submission) -> dict[str, str]:
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is not None:
            task_dir = self.runtime_paths.get_template_root_from_workspace(workspace_path) / "requirements"
            requirements_md = self._read_text(task_dir / "requirements.md")
            requirements_yaml = self._read_text(task_dir / "requirements.yaml")
            prerequisites_md = self._read_text(task_dir / "prerequisites.md")
        else:
            requirements_md = self.artifact_service.read_snapshot_text(submission, "requirements/requirements.md") or ""
            requirements_yaml = self.artifact_service.read_snapshot_text(submission, "requirements/requirements.yaml") or ""
            prerequisites_md = self.artifact_service.read_snapshot_text(submission, "requirements/prerequisites.md") or ""
        return {
            "requirements_md": requirements_md,
            "requirements_yaml": requirements_yaml,
            "prerequisites_md": prerequisites_md,
        }

    def write_submission_task_documents(
        self,
        submission: Submission,
        *,
        requirements_md: str,
        requirements_yaml: str,
        prerequisites_md: str,
    ) -> None:
        if not self.can_manual_edit(submission):
            raise ValueError("Requirement documents can only be edited while a real ARC run is paused")
        # Validate the complete tree before touching any workspace file. This
        # prevents a malformed YAML edit from leaving markdown and YAML out of sync.
        from app.services.traceability_seed_builder import TraceabilitySeedBuilder
        TraceabilitySeedBuilder().build_from_yaml_text(requirements_yaml)
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is None:
            raise FileNotFoundError("Submission workspace is not available")
        task_dir = self.runtime_paths.get_template_root_from_workspace(workspace_path) / "requirements"
        task_dir.mkdir(parents=True, exist_ok=True)
        self._write_text_atomic(task_dir / "requirements.md", requirements_md)
        self._write_text_atomic(task_dir / "requirements.yaml", requirements_yaml)
        self._write_text_atomic(task_dir / "prerequisites.md", prerequisites_md)
        self._write_arc_resume_context(
            submission,
            {
                "mode": "requirements_changed",
                "node_id": str(self.read_checkpoint(submission).get("current_node_id") or "").strip() or None,
                "phase": "design",
            },
        )

    def write_submission_traceability_store(self, submission: Submission, requirements_yaml: str) -> None:
        self.write_submission_task_runtime_artifacts(submission, requirements_yaml)

    def reset_progress_for_edited_node(self, submission: Submission, node_id: str) -> None:
        normalized_node_id = node_id.strip()
        if not normalized_node_id:
            return
        if not self._is_demo_replay_submission(submission):
            queue_reset = True
            try:
                self._reset_arc_queue_for_node(submission, normalized_node_id)
            except ValueError:
                # Newly added requirement: ARC will rebuild an incompatible queue.
                queue_reset = False
            self._stop_runner_container(submission.id, submission.active_generation)
            self._stop_preview_container(submission.id)
            self._clear_execution_artifacts(submission)
            checkpoint = self.read_checkpoint(submission)
            manual_session = checkpoint.get("manual_edit_session")
            pending_tests = manual_session.get("pending_test_creations", []) if isinstance(manual_session, dict) else []
            checkpoint.update({
                "paused": False,
                "pause_mode": "checkpoint",
                "resume_requires_restart": True,
                "runtime_state_restored": True,
                "current_node_id": normalized_node_id,
                "current_phase": "design",
                "manual_edit_session": None,
                "requirements_changed": not queue_reset,
            })
            self._write_arc_resume_context(
                submission,
                {
                    "mode": "manual_test_edit" if pending_tests else "requirements_changed",
                    "node_id": normalized_node_id,
                    "phase": "design",
                    "pending_test_creations": pending_tests,
                },
            )
            self.write_checkpoint(submission, checkpoint)
            submission.status = SubmissionStatus.PAUSED.value
            submission.started_at = None
            submission.finished_at = None
            submission.score = None
            submission.test_pass_rate = None
            submission.passed_count = 0
            submission.failed_count = 0
            submission.failure_reason = None
            self.db.add(submission)
            self._commit()
            self.db.refresh(submission)
            self.update_steps(submission, self.build_step_states(active_key="start_agent", completed={"deploy_agent"}, description=f"Paused at edited checkpoint {normalized_node_id} (design)"))
            self.append_step_event(submission.id, step_key="start_agent", message=f"Edited node {normalized_node_id}; next resume will restart from its design phase", status="info")
            return
        workspace_path, replay_paths, steps = self._load_demo_replay_steps(submission)
        project_root = self.get_template_repo_path(submission)
        if project_root is None:
            raise FileNotFoundError("Submission workspace is not available")
        git_dir = project_root / ".git"
        if not git_dir.exists():
            raise FileNotFoundError("Git history is not available for this submission")

        history_payload = self.artifact_service.read_commit_history(submission)
        if history_payload.get("availability") != "available":
            raise FileNotFoundError("Git history is not available for this submission")

        commits = history_payload.get("commits", [])
        if not isinstance(commits, list):
            raise ValueError("Commit history is not available")

        target_design_index = find_replay_step_index(steps, node_id=normalized_node_id, phase="design")
        if target_design_index <= 0:
            raise ValueError(f"Design commit not found for node {normalized_node_id}")

        restart_index = target_design_index - 1
        if restart_index > 0:
            prior_commit = commits[restart_index - 1]
            prior_commit_oid = str(prior_commit.get("oid") or "").strip()
            if not prior_commit_oid:
                raise RuntimeError("Failed to resolve prior commit for edited node restart")
            self._run_git(project_root, ["reset", "--hard", prior_commit_oid])
        else:
            self._restore_demo_target_tree_to_base(workspace_path, replay_paths)
        self._run_git(project_root, ["clean", "-fd"])

        checkpoint = self.read_checkpoint(submission)
        checkpoint["last_completed_index"] = restart_index
        checkpoint["completed"] = self._build_checkpoint_completed_entries_from_steps(steps[:restart_index])
        checkpoint["paused"] = False
        checkpoint["runtime_state_restored"] = False
        checkpoint["resume_requires_restart"] = True
        checkpoint["pause_mode"] = "checkpoint"
        checkpoint["manual_edit_session"] = None
        checkpoint["resume_patch_conflict"] = False
        checkpoint["edited_node_restart"] = {
            "node_id": normalized_node_id,
            "restart_from_index": restart_index,
        }
        self.write_checkpoint(submission, checkpoint)

        self._stop_runner_container(submission.id, submission.active_generation)
        self._stop_preview_container(submission.id)
        self._clear_execution_artifacts(submission)
        self._restore_demo_runtime_state_from_checkpoint(
            submission,
            workspace_path=workspace_path,
            completed_index=restart_index,
            paused_message=f"Execution paused at edited checkpoint {normalized_node_id} (design)",
        )
        checkpoint["runtime_state_restored"] = True
        self.write_checkpoint(submission, checkpoint)

        submission.status = SubmissionStatus.PAUSED.value
        submission.started_at = None
        submission.finished_at = None
        submission.score = None
        submission.test_pass_rate = None
        submission.passed_count = 0
        submission.failed_count = 0
        submission.run_duration_seconds = None
        submission.token_count = None
        submission.token_cost_usd = None
        submission.token_cost_currency = None
        submission.feature_implemented_count = 0
        submission.feature_total_count = 0
        submission.feature_implementation_rate = None
        submission.stdout_path = None
        submission.stderr_path = None
        submission.result_path = None
        submission.failure_reason = None
        self.db.add(submission)
        self._commit()
        self.db.refresh(submission)
        self.update_steps(
            submission,
            self.build_step_states(
                active_key="start_agent",
                completed={"deploy_agent"},
                description=f"Paused at edited checkpoint {normalized_node_id} (design)",
            ),
        )
        self.append_step_event(
            submission.id,
            step_key="start_agent",
            message=f"Edited node {normalized_node_id}; next resume will restart from its design phase",
            status="info",
        )
        SubmissionEventStream.publish(
            submission.id,
            reason="edited_node_rewind_completed",
            submission=True,
            logs=True,
            commit_history=True,
            traceability_selected=True,
            traceability_all=True,
            preview=True,
        )

    def write_submission_task_runtime_artifacts(self, submission: Submission, requirements_yaml: str) -> None:
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is None:
            raise FileNotFoundError("Submission workspace is not available")
        arc_dir = self.runtime_paths.get_arc_dir_from_workspace(workspace_path)
        traceability_dir = arc_dir / "traceability"
        traceability_seed_path = arc_dir / "traceability-seed.json"
        from app.services.traceability_seed_builder import TraceabilitySeedBuilder

        seed_builder = TraceabilitySeedBuilder()
        seed = seed_builder.build_from_yaml_text(requirements_yaml)
        self._write_json_atomic(traceability_seed_path, seed)
        self._merge_traceability_tables(traceability_dir, seed)

    def _merge_traceability_tables(self, traceability_dir: Path, seed: dict[str, list[dict]]) -> None:
        """Update requirement metadata without destroying completed ARC artifacts."""
        traceability_dir.mkdir(parents=True, exist_ok=True)
        new_requirements = {
            str(row.get("req_id") or "").strip(): row
            for row in seed.get("requirements", [])
            if isinstance(row, dict) and str(row.get("req_id") or "").strip()
        }
        new_scenarios = {
            str(row.get("scenario_id") or "").strip(): row
            for row in seed.get("scenarios", [])
            if isinstance(row, dict) and str(row.get("scenario_id") or "").strip()
        }
        existing = {name: self._read_json_table(traceability_dir / f"{name}.json") for name in (
            "interfaces", "tests", "call_edges", "node_states", "node_contracts"
        )}
        existing["interfaces"] = {
            key: row for key, row in existing["interfaces"].items()
            if set(self._parse_json_list(row.get("req_ids"))) & set(new_requirements)
        }
        existing["tests"] = {
            key: row for key, row in existing["tests"].items()
            if str(row.get("req_id") or "").strip() in new_requirements
        }
        existing["node_states"] = {
            key: row for key, row in existing["node_states"].items()
            if str(row.get("req_id") or key).strip() in new_requirements
        }
        existing["node_contracts"] = {
            key: row for key, row in existing["node_contracts"].items()
            if str(row.get("req_id") or key).strip() in new_requirements
        }
        existing["call_edges"] = {
            key: row for key, row in existing["call_edges"].items()
            if isinstance(row, dict)
            and str(row.get("source_req_id") or "").strip() in new_requirements
            and str(row.get("target_req_id") or "").strip() in new_requirements
        }
        self._write_json_table(traceability_dir / "requirements.json", new_requirements)
        self._write_json_table(traceability_dir / "scenarios.json", new_scenarios)
        for name, rows in existing.items():
            self._write_json_table(traceability_dir / f"{name}.json", rows)

    @staticmethod
    def _parse_json_list(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return [value.strip()] if value.strip() else []
            return RunService._parse_json_list(parsed)
        return []

    def _sync_traceability_node_states(self, project_root: Path, queue: dict) -> None:
        path = project_root / ".arc" / "traceability" / "node_states.json"
        rows = self._read_json_table(path)
        queue_states = queue.get("node_states", {}) if isinstance(queue, dict) else {}
        if not isinstance(queue_states, dict):
            return
        for key, state in queue_states.items():
            row = rows.setdefault(str(key), {"req_id": str(key)})
            row["state"] = str(state)
        self._write_json_table(path, rows)

    def get_submission_task_asset_path(self, submission: Submission, asset_kind: str, asset_path: str) -> Path:
        normalized_kind = "reference" if asset_kind == "references" else asset_kind
        if normalized_kind not in {"assets", "reference"}:
            raise FileNotFoundError(f"Unknown submission task asset kind: {asset_kind}")

        normalized_relative_path = self._normalize_relative_path(asset_path)
        asset_roots: list[Path] = []
        if submission.user_id:
            asset_roots.append(
                self.runtime_paths.get_shared_run_requirements_root(submission.user_id, submission.id) / normalized_kind
            )
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is not None:
            asset_roots.append(
                self.runtime_paths.get_template_root_from_workspace(workspace_path) / "requirements" / normalized_kind
            )
        for asset_root in asset_roots:
            if not asset_root.is_dir():
                continue
            resolved_root = asset_root.resolve()
            resolved_target = (asset_root / normalized_relative_path).resolve()
            try:
                resolved_target.relative_to(resolved_root)
            except ValueError as exc:
                raise FileNotFoundError(f"Submission task asset path is invalid: {asset_path}") from exc
            if resolved_target.is_file():
                return resolved_target
        if submission.catalog in {"playground", "competition", "benchmark"}:
            try:
                catalog_asset = RequirementCatalogService.for_catalog(self.db, submission.catalog).get_asset_path(
                    submission.requirement_id,
                    "assets" if normalized_kind == "assets" else "references",
                    normalized_relative_path.as_posix(),
                )
            except (LookupError, FileNotFoundError, ValueError):
                pass
            else:
                return catalog_asset
        raise FileNotFoundError(f"Submission task asset not found: {normalized_relative_path.as_posix()}")

    @distributed_locked(lambda self, submission: f"submission:{submission.id}")
    def request_pause(self, submission: Submission) -> None:
        self.db.refresh(submission)
        if not self.can_pause(submission):
            raise ValueError("Submission is not running")
        request_path = self.get_pause_request_path(submission)
        if request_path is None:
            raise FileNotFoundError("Submission workspace is not available")
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(
            json.dumps({"requested_at": datetime.utcnow().isoformat(), "submission_id": submission.id}, indent=2) + "\n",
            encoding="utf-8",
        )
        self.update_status(submission, SubmissionStatus.PAUSE_REQUESTED)

    @distributed_locked(lambda self, submission: f"submission:{submission.id}")
    def cancel_submission(self, submission: Submission) -> None:
        submission = self.db.scalar(select(Submission).where(Submission.id == submission.id).with_for_update())
        if submission is None:
            raise LookupError("Run not found")
        if not self.can_cancel(submission):
            raise ValueError("Submission is not running")
        current_steps = [StepState.model_validate(step) for step in json.loads(submission.steps_json or "[]")]
        active_step = next((step.key for step in current_steps if step.status == "running"), "start_agent")
        completed = {step.key for step in current_steps if step.status == "completed"}
        self.update_steps(submission, self.build_cancelled_step_states(active_step, completed))
        submission.status = SubmissionStatus.CANCELLED.value
        submission.finished_at = datetime.utcnow()
        # This column represents agent-process time, not wall-clock time. A
        # forced cancellation may terminate the runner before it writes its
        # execution metric, so do not substitute elapsed wall time here.
        submission.run_duration_seconds = None
        submission.token_count = None
        submission.token_cost_usd = None
        submission.token_cost_currency = None
        submission.failure_reason = "Run cancelled by user"
        if submission.active_attempt_id:
            attempt = self.db.get(RunAttempt, submission.active_attempt_id)
            if attempt and attempt.generation == submission.active_generation:
                attempt.status = "CANCELLED"
                attempt.finished_at = datetime.utcnow()
                attempt.lease_until = None
                attempt.failure_reason = submission.failure_reason
        submission.active_attempt_id = None
        submission.worker_id = None
        submission.lease_until = None
        submission.heartbeat_at = None
        self.db.add(submission)
        self._commit()
        self.db.refresh(submission)
        # Remove durable messages that have not yet been handed to Celery.
        # This prevents a queued cancellation from starting later.
        from app.models.task_outbox import TaskOutbox
        self.db.query(TaskOutbox).filter(
            TaskOutbox.run_id == submission.id,
            TaskOutbox.sent_at.is_(None),
        ).delete(synchronize_session=False)
        self.db.commit()
        self.append_step_event(submission.id, step_key=active_step, message="Run cancelled by user", status="error")
        self._create_run_notification(submission, "cancelled", "Run cancelled", "Your run was cancelled. Logs and generated artifacts are still available.")
        SubmissionEventStream.publish(submission.id, reason="cancelled", submission=True, logs=True)

    @distributed_locked(lambda self, submission: f"submission:{submission.id}")
    def request_resume(self, submission: Submission) -> None:
        self.db.refresh(submission)
        if not self.can_resume(submission):
            raise ValueError("Submission is not paused")
        request_path = self.get_resume_request_path(submission)
        if request_path is None:
            raise FileNotFoundError("Submission workspace is not available")
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(
            json.dumps({"requested_at": datetime.utcnow().isoformat(), "submission_id": submission.id}, indent=2) + "\n",
            encoding="utf-8",
        )
        self.update_status(submission, SubmissionStatus.RUNNING)

    @distributed_locked(lambda self, submission: f"submission:{submission.id}")
    def clear_runtime_request_files(self, submission: Submission) -> None:
        for path in (self.get_pause_request_path(submission), self.get_resume_request_path(submission)):
            if path is None:
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                continue

    @distributed_locked(lambda self, submission: f"submission:{submission.id}")
    def request_continue(self, submission: Submission) -> None:
        self.db.refresh(submission)
        if not self.can_continue(submission):
            raise ValueError("Only a completed built-in ARC run with an available workspace can continue")
        request_path = self.get_continue_request_path(submission)
        if request_path is None:
            raise FileNotFoundError("Submission workspace is not available")
        if submission.agent_source != AgentSourceType.DEMO_REPLAY.value:
            self._refresh_builtin_arc_agent_workspace(submission)
        self.clear_runtime_request_files(submission)
        request_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json_atomic(
            request_path,
            {"requested_at": datetime.utcnow().isoformat(), "submission_id": submission.id},
        )
        checkpoint = self.read_checkpoint(submission)
        checkpoint["continue_existing_workspace"] = True
        checkpoint["resume_requires_restart"] = True
        self.write_checkpoint(submission, checkpoint)
        submission.status = SubmissionStatus.RUNNING.value
        submission.started_at = datetime.utcnow()
        submission.finished_at = None
        submission.failure_reason = None
        next_steps = self.build_step_states(
            active_key="start_agent" if submission.agent_source == AgentSourceType.DEMO_REPLAY.value else "deploy_agent",
            completed={"deploy_agent"} if submission.agent_source == AgentSourceType.DEMO_REPLAY.value else set(),
            description="Continuing incremental demo from its current snapshot" if submission.agent_source == AgentSourceType.DEMO_REPLAY.value else "Continuing built-in ARC in the existing workspace",
        )
        if submission.agent_source == AgentSourceType.DEMO_REPLAY.value:
            # The host runner starts immediately after this method returns, so
            # persist its RUNNING state before spawning the detached process.
            self.update_steps(submission, next_steps)
        else:
            # Keep the normal queue transition and outbox insert atomic in the
            # route's transaction.
            submission.steps_json = json.dumps([step.model_dump() for step in next_steps])
            self.db.add(submission)
        self.append_step_event(
            submission.id,
            step_key="start_agent" if submission.agent_source == AgentSourceType.DEMO_REPLAY.value else "deploy_agent",
            message="Incremental demo continuation requested" if submission.agent_source == AgentSourceType.DEMO_REPLAY.value else "Continuation requested; generated code and ARC state are preserved",
            status="info",
        )
        SubmissionEventStream.publish(
            submission.id,
            reason="continue_requested",
            submission=True,
            logs=True,
            commit_history=True,
            traceability_selected=True,
            traceability_all=True,
            preview=True,
        )

    def _refresh_builtin_arc_agent_workspace(self, submission: Submission) -> None:
        """Apply platform fixes to the managed ARC source without touching generated code."""
        if submission.agent_source != AgentSourceType.BUILTIN_ARC_AGENT.value:
            return
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is None:
            raise FileNotFoundError("Submission workspace is not available")
        submission_dir = workspace_path / "submission"
        if not submission_dir.is_dir():
            raise FileNotFoundError("Built-in ARC source workspace is not available")

        archive_path = Path(submission.agent_archive_path)
        self._write_builtin_arc_agent_archive(archive_path)
        shutil.rmtree(submission_dir)
        submission_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "r") as archive:
            archive.extractall(submission_dir)

    @distributed_locked(lambda self, submission, commit_oid: f"submission:{submission.id}")
    def rewind_to_commit(self, submission: Submission, commit_oid: str) -> dict[str, str | int | None]:
        self.db.refresh(submission)
        normalized_commit_oid = commit_oid.strip()
        if not normalized_commit_oid:
            raise ValueError("commit_oid is required")
        if not self.can_rewind(submission):
            raise ValueError("Submission must be paused or completed before rewinding")

        if not self._is_demo_replay_submission(submission):
            project_root = self.get_template_repo_path(submission)
            if project_root is None or not (project_root / ".git").exists():
                raise FileNotFoundError("Git history is not available for this submission")
            history_payload = self.artifact_service.read_commit_history(submission)
            commits = history_payload.get("commits", [])
            target_commit = next((item for item in commits if str(item.get("oid", "")).strip() == normalized_commit_oid), None) if isinstance(commits, list) else None
            if target_commit is None:
                raise FileNotFoundError(f"Commit not found: {normalized_commit_oid}")
            node_id = str(target_commit.get("node_id") or "").strip() or None
            phase = str(target_commit.get("phase") or "").strip().lower() or None
            self._run_git(project_root, ["reset", "--hard", normalized_commit_oid])
            self._run_git(project_root, ["clean", "-fd"])
            queue_path = project_root / ".arc" / "processing_queue.json"
            if queue_path.is_file() and node_id and phase:
                queue = json.loads(queue_path.read_text(encoding="utf-8"))
                tasks = queue.get("tasks", [])
                target_index = next((index for index, task in enumerate(tasks) if isinstance(task, dict) and task.get("node_id") == node_id and str(task.get("phase", "")).lower() == phase), None)
                if target_index is not None:
                    target_task = tasks[target_index]
                    target_state = "DESIGNED" if phase == "design" else "PASSED"
                    queue.setdefault("node_states", {})[node_id] = target_state
                    for task in tasks[target_index + 1:]:
                        if isinstance(task, dict):
                            task["status"] = "PENDING"
                            task_node_id = str(task.get("node_id") or "").strip()
                            if task_node_id:
                                queue.setdefault("node_states", {})[task_node_id] = "UNSEEN"
                    queue["last_task_id"] = target_task.get("task_id")
                    queue_path.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                    self._sync_traceability_node_states(project_root, queue)
            checkpoint = self.read_checkpoint(submission)
            checkpoint.update({"pause_mode": "checkpoint", "resume_requires_restart": True, "runtime_state_restored": True, "manual_edit_session": None, "rewind_target": {"commit_oid": normalized_commit_oid, "node_id": node_id, "phase": phase}})
            self.write_checkpoint(submission, checkpoint)
            self._write_arc_resume_context(
                submission,
                {
                    "mode": "rewind",
                    "node_id": node_id,
                    "phase": phase,
                    "commit_oid": normalized_commit_oid,
                    "resume_from_task": f"{node_id}:{str(phase).upper()}" if node_id and phase else None,
                },
            )
            self._stop_runner_container(submission.id, submission.active_generation)
            self._stop_preview_container(submission.id)
            self._clear_execution_artifacts(submission)
            self._mark_rewound_paused(submission, node_id=node_id or "ROOT", phase=phase or "implement", commit_oid=normalized_commit_oid)
            SubmissionEventStream.publish(submission.id, reason="rewind_completed", submission=True, logs=True, commit_history=True, traceability_selected=True, traceability_all=True, preview=True)
            return {"commit_oid": normalized_commit_oid, "node_id": node_id, "phase": phase, "commit_index": None}

        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is None:
            raise FileNotFoundError("Submission workspace is not available")
        snapshot_manifest = workspace_path / "submission" / "snapshots" / "ticketbooking" / "manifest.json"
        if self._is_demo_replay_submission(submission) and snapshot_manifest.is_file():
            try:
                manifest_payload = json.loads(snapshot_manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest_payload = {}
            raw_steps = manifest_payload.get("steps", []) if isinstance(manifest_payload, dict) else []
            steps = [
                DemoReplayStep(
                    index=int(item.get("index", 0) or 0),
                    node_id=str(item.get("node_id") or ""),
                    phase=str(item.get("phase") or "").lower(),
                    commit_message=str(item.get("commit_message") or ""),
                    source_commit=str(item.get("source_commit") or ""),
                    interfaces=[], tests=[], implemented_interface_ids=[], passed_test_ids=[],
                )
                for item in raw_steps if isinstance(item, dict)
            ]
        else:
            _workspace_path, _replay_paths, steps = self._load_demo_replay_steps(submission)
            workspace_path = _workspace_path
        project_root = self.get_template_repo_path(submission)
        if project_root is None:
            raise FileNotFoundError("Submission workspace is not available")

        # Snapshot demos have no runtime Git repository. Rewind by resolving
        # the selected virtual commit to its snapshot index and materializing
        # that immutable state into the per-run workspace.
        if self._is_demo_replay_submission(submission) and snapshot_manifest.is_file():
            history_payload = self.artifact_service.read_commit_history(submission)
            commits = history_payload.get("commits", [])
            snapshot_root = workspace_path / "submission" / "snapshots" / "ticketbooking"
            target_commit = next(
                (item for item in commits if str(item.get("oid", "")).strip() == normalized_commit_oid),
                None,
            ) if isinstance(commits, list) else None
            # Demo repair commits live in later immutable snapshots and are
            # intentionally hidden from the baseline commit-history panel.
            if target_commit is None:
                for repair_index in (25, 26, 27, 28, 29, 30):
                    repair_history = snapshot_root / "steps" / f"{repair_index:04d}" / "commit-history.json"
                    try:
                        payload = json.loads(repair_history.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    candidates = payload.get("commits", []) if isinstance(payload, dict) else []
                    target_commit = next((item for item in candidates if str(item.get("oid", "")).strip() == normalized_commit_oid), None)
                    if target_commit is not None:
                        break
            if target_commit is None:
                raise FileNotFoundError(f"Commit not found: {normalized_commit_oid}")
            node_id = str(target_commit.get("node_id") or "").strip() or None
            phase = str(target_commit.get("phase") or "").strip().lower() or None
            # Snapshot indices are one-based and commit history is ordered in
            # the same way. Prefer the ordinal so virtual commit IDs remain
            # stable even if a step's metadata is incomplete.
            target_position = next(
                (position for position, item in enumerate(commits, start=1) if item is target_commit),
                0,
            )
            rewound_index = target_position or next(
                (
                    step.index
                    for step in steps
                    if step.source_commit == normalized_commit_oid
                    or (step.node_id == node_id and step.phase == phase)
                ),
                0,
            )
            if rewound_index <= 0:
                raise RuntimeError("Failed to map selected demo commit to a snapshot step")
            step_dir = snapshot_root / "steps" / f"{rewound_index:04d}"
            snapshot_project = step_dir / "project"
            if not snapshot_project.is_dir():
                raise FileNotFoundError(f"Demo snapshot step is unavailable: {step_dir}")
            for child in list(project_root.iterdir()):
                if child.name not in {"requirements", ".arc"}:
                    if child.name in {"frontend", "backend"} and child.is_dir():
                        for nested in list(child.iterdir()):
                            shutil.rmtree(nested) if nested.is_dir() else nested.unlink()
                    else:
                        shutil.rmtree(child) if child.is_dir() else child.unlink()
            for child in snapshot_project.iterdir():
                if child.name in {"requirements", ".arc"}:
                    continue
                destination = project_root / child.name
                # A user's demo workspace is only a source/traceability view.
                # Dependencies and build outputs belong to the shared,
                # pre-deployed per-step previews and are never materialized
                # during rewind or TDD replay.
                if child.is_dir() and child.name in {"frontend", "backend"} and destination.is_dir():
                    for source_path in child.iterdir():
                        if source_path.name in {
                            "node_modules", "dist", "build", "coverage", ".cache", ".vite",
                            "database.db", "database.db-wal", "database.db-shm", "database.db-journal",
                        }:
                            continue
                        target_path = destination / source_path.name
                        if target_path.exists():
                            shutil.rmtree(target_path) if target_path.is_dir() else target_path.unlink()
                        shutil.copytree(source_path, target_path) if source_path.is_dir() else shutil.copy2(source_path, target_path)
                else:
                    if child.is_dir():
                        shutil.copytree(
                            child,
                            destination,
                            ignore=shutil.ignore_patterns(
                                "node_modules", "dist", "build", "coverage", ".cache", ".vite",
                                "database.db", "database.db-wal", "database.db-shm", "database.db-journal",
                            ),
                        )
                    else:
                        shutil.copy2(child, destination)
            arc_dir = project_root / ".arc"
            for source_name, target_name in (("traceability", "traceability"), ("node-sessions", "node_sessions")):
                source = step_dir / source_name
                target = arc_dir / target_name
                if target.exists():
                    shutil.rmtree(target)
                if source.is_dir():
                    shutil.copytree(source, target)
            queue_source = step_dir / "queue.json"
            if queue_source.is_file():
                shutil.copy2(queue_source, arc_dir / "processing_queue.json")
            events_source = step_dir / "runner-events.jsonl"
            if events_source.is_file():
                shutil.copy2(events_source, arc_dir / "runner-events.jsonl")
            checkpoint = self.read_checkpoint(submission)
            checkpoint.update({
                "last_completed_index": rewound_index,
                "completed": self._build_checkpoint_completed_entries_from_steps(steps[:rewound_index]),
                "paused": True,
                "pause_mode": "checkpoint",
                "runtime_state_restored": True,
                "resume_requires_restart": False,
                "manual_edit_session": None,
                "resume_patch_conflict": False,
                "rewind_target": {"commit_oid": normalized_commit_oid, "node_id": node_id, "phase": phase, "commit_index": rewound_index},
            })
            self.write_checkpoint(submission, checkpoint)
            self._stop_preview_container(submission.id)
            self._mark_rewound_paused(submission, node_id=node_id or "ROOT", phase=phase or "implement", commit_oid=normalized_commit_oid)
            SubmissionEventStream.publish(submission.id, reason="rewind_completed", submission=True, logs=True, commit_history=True, traceability_selected=True, traceability_all=True, preview=True)
            return {"commit_oid": normalized_commit_oid, "node_id": node_id, "phase": phase, "commit_index": rewound_index}

        git_dir = project_root / ".git"
        if not git_dir.exists():
            raise FileNotFoundError("Git history is not available for this submission")

        history_payload = self.artifact_service.read_commit_history(submission)
        if history_payload.get("availability") != "available":
            raise FileNotFoundError("Git history is not available for this submission")

        commits = history_payload.get("commits", [])
        target_commit = next((commit for commit in commits if str(commit.get("oid", "")).strip() == normalized_commit_oid), None)
        if target_commit is None:
            raise FileNotFoundError(f"Commit not found: {normalized_commit_oid}")

        node_id = str(target_commit.get("node_id") or "").strip() or None
        phase = str(target_commit.get("phase") or "").strip() or None
        if not node_id or phase not in {"design", "implement"}:
            raise ValueError("Selected commit does not map to a requirement node and resumable phase")

        rewound_index = find_replay_step_index(steps, node_id=node_id, phase=phase)
        if rewound_index <= 0:
            raise RuntimeError("Failed to map selected commit to ARC demo replay step")

        self._run_git(project_root, ["reset", "--hard", normalized_commit_oid])
        self._run_git(project_root, ["clean", "-fd"])

        checkpoint = self.read_checkpoint(submission)
        checkpoint["last_completed_index"] = rewound_index
        checkpoint["completed"] = self._build_checkpoint_completed_entries_from_steps(steps[:rewound_index])
        checkpoint["paused"] = False
        checkpoint["runtime_state_restored"] = False
        checkpoint["pause_mode"] = "checkpoint"
        checkpoint["manual_edit_session"] = None
        checkpoint["resume_patch_conflict"] = False
        checkpoint["rewind_target"] = {
            "commit_oid": normalized_commit_oid,
            "node_id": node_id,
            "phase": phase,
            "commit_index": rewound_index,
        }
        checkpoint["resume_requires_restart"] = True
        self.write_checkpoint(submission, checkpoint)

        self._stop_runner_container(submission.id, submission.active_generation)
        self._stop_preview_container(submission.id)
        self._clear_execution_artifacts(submission)
        self._restore_demo_runtime_state_from_checkpoint(
            submission,
            workspace_path=workspace_path,
            completed_index=rewound_index,
            paused_message=f"Execution paused at rewound checkpoint {node_id} ({phase})",
        )
        checkpoint["runtime_state_restored"] = True
        self.write_checkpoint(submission, checkpoint)
        self._mark_rewound_paused(submission, node_id=node_id, phase=phase, commit_oid=normalized_commit_oid)
        SubmissionEventStream.publish(
            submission.id,
            reason="rewind_completed",
            submission=True,
            logs=True,
            commit_history=True,
            traceability_selected=True,
            traceability_all=True,
            preview=True,
        )

        return {
            "commit_oid": normalized_commit_oid,
            "node_id": node_id,
            "phase": phase,
            "commit_index": rewound_index,
        }

    def checkpoint_requires_restart(self, submission: Submission) -> bool:
        checkpoint = self.read_checkpoint(submission)
        return bool(checkpoint.get("resume_requires_restart"))

    @distributed_locked(lambda self, submission: f"submission:{submission.id}")
    def clear_checkpoint_restart_flag(self, submission: Submission) -> None:
        checkpoint = self.read_checkpoint(submission)
        if not checkpoint.get("resume_requires_restart"):
            return
        checkpoint["resume_requires_restart"] = False
        self.write_checkpoint(submission, checkpoint)

    @distributed_locked(lambda self, submission: f"submission:{submission.id}")
    def set_checkpoint_restart_flag(self, submission: Submission) -> None:
        checkpoint = self.read_checkpoint(submission)
        checkpoint["resume_requires_restart"] = True
        self.write_checkpoint(submission, checkpoint)

    def ensure_demo_checkpoint_runtime_restored(self, submission: Submission) -> None:
        checkpoint = self.read_checkpoint(submission)
        completed_index = int(checkpoint.get("last_completed_index", 0) or 0)
        if completed_index <= 0:
            return
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is None:
            raise FileNotFoundError("Submission workspace is not available")
        if resolve_demo_replay_paths(workspace_path) is None:
            return
        if bool(checkpoint.get("runtime_state_restored")) and self._is_demo_runtime_restore_complete(workspace_path):
            return
        self.rebuild_demo_submission_to_checkpoint(submission)

    def _is_demo_runtime_restore_complete(self, workspace_path: Path) -> bool:
        arc_dir = self.runtime_paths.get_arc_dir_from_workspace(workspace_path)
        required_paths = (
            arc_dir / "traceability",
            arc_dir / "runner-events.jsonl",
        )
        return all(path.exists() for path in required_paths)

    def append_step_event(
        self,
        submission_id: str,
        step_key: Literal["deploy_agent", "start_agent", "run_tests"],
        message: str,
        status: Literal["info", "success", "error"] = "info",
    ) -> Path | None:
        submission = self.get_submission(submission_id)
        payload = {
            "event_id": uuid.uuid4().hex,
            "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "step_key": step_key,
            "stage": self._stage_for_step_key(step_key),
            "status": status,
            "message": message,
            "summary": message,
            "heartbeat": False,
            "artifact_reference": None,
        }
        log_path: Path | None = None
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is None and submission.user_id:
            username = self._get_submission_user(submission.user_id).username
            generation = self.execution_owner[2] if self.execution_owner is not None else submission.active_generation
            candidate_workspace_path = self.runtime_paths.get_workspace_root(
                submission,
                username=username,
                generation=generation,
            )
            if candidate_workspace_path.exists():
                workspace_path = candidate_workspace_path
        if workspace_path is not None:
            log_path = self.runtime_paths.get_arc_dir_from_workspace(workspace_path) / "runner-events.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(payload, ensure_ascii=True) + "\n")
        SubmissionEventStream.publish(
            submission_id,
            reason=f"step:{step_key}",
            logs=True,
        )
        return log_path

    @staticmethod
    def _validate_agent_archive(archive_path: Path, runtime: RuntimeType) -> None:
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                members = [Path(info.filename) for info in archive.infolist() if not info.is_dir()]
        except zipfile.BadZipFile as exc:
            raise ValueError("Uploaded file is not a valid zip archive") from exc

        if not members:
            raise ValueError("Uploaded zip archive is empty")

        resolve_agent_root(members, runtime)

    @staticmethod
    def _required_agent_root_files(runtime: RuntimeType) -> tuple[str, ...]:
        return required_agent_root_files(runtime)

    @staticmethod
    def _write_builtin_arc_agent_archive(archive_path: Path) -> None:
        source_archive = Path(get_settings().agent_reference_arc_archive).resolve()
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(
                archive_path,
                "w",
                zipfile.ZIP_DEFLATED,
            ) as archive:
                add_arc_reference(archive, source_archive, templates_root=get_settings().arc_template_root)
        except zipfile.BadZipFile as exc:
            archive_path.unlink(missing_ok=True)
            raise ValueError(f"Built-in ARC archive is invalid: {source_archive}") from exc
        except (ValueError, OSError):
            archive_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _build_builtin_arc_entrypoint() -> str:
        """Adapt ARC's source CLI to the platform's common agent contract."""
        return ARC_ENTRYPOINT

    def list_submissions(
        self,
        user_id: str,
        requirement_id: str | None = None,
        *,
        submission_id: str | None = None,
        active_only: bool = False,
    ) -> list[RunSummary]:
        # Official competition capacity is owned by the team.  The active-run
        # control page must therefore expose runs attached to the user's team,
        # even when another team member originally created the run.  Skipping
        # membership lookup here made such runs consume team capacity while
        # remaining invisible in /running.
        membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user_id))
        access_filter = Submission.user_id == user_id
        if membership:
            team_entry_ids = select(CompetitionEntry.id).where(CompetitionEntry.team_id == membership.team_id)
            access_filter = or_(access_filter, Submission.competition_entry_id.in_(team_entry_ids))
        query = select(Submission).where(access_filter).order_by(desc(Submission.created_at))
        if requirement_id:
            query = query.where(Submission.requirement_id == requirement_id)
        if submission_id:
            query = query.where(Submission.submission_id == submission_id)
        if active_only:
            query = query.where(Submission.status.in_((
                SubmissionStatus.QUEUED.value,
                SubmissionStatus.STARTING.value,
                SubmissionStatus.RUNNING.value,
                SubmissionStatus.PAUSED.value,
                SubmissionStatus.PAUSE_REQUESTED.value,
                SubmissionStatus.RESUME_REQUESTED.value,
            )))
        rows = self.db.scalars(query).all()
        source_ids = {row.submission_id for row in rows if row.submission_id}
        sources = (
            {
                source.id: source
                for source in self.db.scalars(
                    select(AgentSubmission).where(AgentSubmission.id.in_(source_ids))
                ).all()
            }
            if source_ids
            else {}
        )
        return [self.to_summary(row, sources.get(row.submission_id)) for row in rows]

    def to_summary(self, run: Submission, source: AgentSubmission | None = None) -> RunSummary:
        if source is None and run.submission_id:
            source = self.db.get(AgentSubmission, run.submission_id)
        return RunSummary(
            id=run.id,
            submission_id=run.submission_id,
            display_name=source.display_name if source else run.submission_display_name,
            model_name=source.model_name if source else run.model_name,
            original_filename=source.original_filename if source else (run.original_filename or "agent.zip"),
            catalog=source.catalog if source else run.catalog,
            competition_id=source.competition_id if source else run.competition_id,
            billing_mode=run.billing_mode,
            evaluation_started_at=run.evaluation_started_at,
            settled_at=run.settled_at,
            requirement_id=run.requirement_id,
            runtime=run.runtime,
            agent_source=run.agent_source,
            status=run.status,
            score=run.score,
            test_pass_rate=run.test_pass_rate,
            passed_count=run.passed_count,
            failed_count=run.failed_count,
            run_duration_seconds=run.run_duration_seconds,
            token_count=run.token_count,
            token_cost_usd=run.token_cost_usd,
            token_cost_currency=run.token_cost_currency,
            cost_source=run.cost_source,
            cost_complete=run.cost_complete,
            feature_implemented_count=run.feature_implemented_count,
            feature_total_count=run.feature_total_count,
            feature_implementation_rate=run.feature_implementation_rate,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            failure_reason=run.failure_reason,
        )

    def get_submission(self, submission_id: str, user_id: str | None = None) -> Submission:
        self._assert_execution_owner()
        submission = self.db.get(Submission, submission_id)
        if not submission:
            raise LookupError(f"Submission '{submission_id}' not found")
        if user_id is not None and submission.user_id != user_id:
            membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user_id))
            entry = self.db.get(CompetitionEntry, submission.competition_entry_id) if submission.competition_entry_id else None
            if not (membership and entry and entry.owner_kind == "team" and entry.team_id == membership.team_id):
                raise LookupError(f"Submission '{submission_id}' not found")
        return submission

    def delete_submission(self, submission_id: str, user_id: str) -> None:
        """Delete one owned run and its canonical shared runtime directory.

        This method operates on the ``runs`` table (the historical name is kept
        for API compatibility). The uploaded agent snapshot in ``submissions``
        is deliberately retained, so deleting a run never removes the user's
        reusable upload. Run attempts and pending outbox deliveries are removed
        together to prevent a deleted run from being dispatched later.
        """
        submission = self.get_submission(submission_id, user_id)
        # A paused run has no executor/container left to protect.  Allow any
        # member of the owning official team to discard it, while retaining
        # the leader-only rule for active, resumable, or other lifecycle
        # operations.
        if submission.status == SubmissionStatus.PAUSED.value:
            self._require_official_run_member(user_id, submission)
        else:
            self._require_official_run_leader(user_id, submission)
        self._cancel_for_deletion(submission)

        if not submission.user_id:
            raise RuntimeError("Run has no owner user")
        # New deployments keep runs under shared storage. Existing deployments
        # may still have the legacy per-host user-submissions layout, so select
        # the first existing canonical location and validate it against its own
        # configured root.
        submission_root = self.runtime_paths.get_shared_run_root(submission.user_id, submission.id).resolve()
        storage_root = self.settings.shared_storage_root.resolve()
        if submission_root.exists():
            try:
                relative_submission_root = submission_root.relative_to(storage_root)
            except ValueError as exc:
                raise RuntimeError("Resolved run directory is outside shared storage") from exc
            if relative_submission_root.parts[-2:] != ("runs", submission.id):
                raise RuntimeError("Resolved run directory does not match the requested run")
        else:
            owner = self._get_submission_user(str(submission.user_id))
            submission_root = self.runtime_paths.get_submission_root_by_identity(
                owner.username,
                owner.id,
                submission.id,
            ).resolve()
            legacy_root = self.settings.user_submissions_root.resolve()
            try:
                relative_submission_root = submission_root.relative_to(legacy_root)
            except ValueError as exc:
                raise RuntimeError("Resolved legacy run directory is outside user submissions") from exc
            if relative_submission_root.name != submission.id:
                raise RuntimeError("Resolved legacy run directory does not match the requested run")

        # Docker Desktop keeps Windows bind-mount file handles open until the
        # container is removed. Release it before deleting .git/workspace files.
        self._remove_runner_containers_for_deletion(submission.id)
        self._stop_preview_container(submission.id)
        if submission_root.exists():
            self._remove_submission_runtime_directory(submission_root)

        # RunAttempt and TaskOutbox intentionally do not have database foreign
        # keys, so clean them explicitly before deleting the run row.
        self.db.query(RunAttempt).filter(RunAttempt.run_id == submission.id).delete(synchronize_session=False)
        from app.models.task_outbox import TaskOutbox

        self.db.query(TaskOutbox).filter(TaskOutbox.run_id == submission.id).delete(synchronize_session=False)
        self.db.delete(submission)
        self._commit()

    def _require_official_run_leader(self, user_id: str, run: Submission) -> None:
        if run.catalog == "competition" and run.competition_id:
            from app.services.classroom_service import ClassroomService
            team = ClassroomService(self.db).require_leader(user_id, run.competition_id)
            if team is not None:
                class_entry = self.db.get(CompetitionEntry, run.competition_entry_id) if run.competition_entry_id else None
                if class_entry is None or class_entry.team_id != team.id:
                    raise PermissionError("Only the owning class team leader can manage this run")
                return
        if run.billing_mode != "official_evaluation":
            return
        entry = self.db.get(CompetitionEntry, run.budget_competition_entry_id or run.competition_entry_id)
        if entry and entry.owner_kind == "user" and entry.user_id == user_id and entry.competition_id == run.competition_id:
            return
        membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user_id))
        team = self.db.get(Team, membership.team_id) if membership else None
        if (
            membership is None
            or team is None
            or team.leader_user_id != user_id
            or entry is None
            or entry.competition_id != run.competition_id
            or entry.team_id != team.id
        ):
            raise PermissionError("Only the team leader can manage official competition runs")

    def _require_official_run_member(self, user_id: str, run: Submission) -> None:
        """Require membership in the owning team; class runs remain leader-only."""
        if run.catalog == "competition" and run.competition_id:
            from app.services.classroom_service import ClassroomService
            team = ClassroomService(self.db).require_leader(user_id, run.competition_id)
            if team is not None:
                class_entry = self.db.get(CompetitionEntry, run.competition_entry_id) if run.competition_entry_id else None
                if class_entry is None or class_entry.team_id != team.id:
                    raise PermissionError("Only the owning class team leader can manage this run")
                return
        if run.billing_mode != "official_evaluation":
            return
        entry = self.db.get(CompetitionEntry, run.budget_competition_entry_id or run.competition_entry_id)
        if entry and entry.owner_kind == "user" and entry.user_id == user_id and entry.competition_id == run.competition_id:
            return
        membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user_id))
        if (
            membership is None
            or entry is None
            or entry.owner_kind != "team"
            or entry.competition_id != run.competition_id
            or entry.team_id != membership.team_id
        ):
            raise PermissionError("Only members of the owning official competition team can manage this run")

    def delete_submissions(self, submission_ids: list[str], user_id: str) -> dict[str, list]:
        """Best-effort batch deletion: retain only records that could not be removed."""
        unique_ids = list(dict.fromkeys(item.strip() for item in submission_ids if item and item.strip()))
        if not unique_ids:
            raise ValueError("Choose at least one run to delete")

        deleted_ids: list[str] = []
        skipped: list[dict[str, str]] = []
        for submission_id in unique_ids:
            try:
                self.delete_submission(submission_id, user_id)
                deleted_ids.append(submission_id)
            except Exception as exc:  # noqa: BLE001 - batch deletion is intentionally best-effort
                # A failed item must retain its database record so it stays
                # visible and can be retried after a filesystem lock clears.
                self.db.rollback()
                skipped.append({"id": submission_id, "reason": str(exc)})
        return {"deleted_ids": deleted_ids, "skipped": skipped}

    def _cancel_for_deletion(self, submission: Submission) -> None:
        """Make deletion a terminal action: stop an active run before removing it."""
        if submission.status in {
            SubmissionStatus.PENDING.value,
            SubmissionStatus.RUNNING.value,
            SubmissionStatus.PAUSE_REQUESTED.value,
            SubmissionStatus.PAUSED.value,
            SubmissionStatus.RESUME_REQUESTED.value,
        }:
            submission.status = SubmissionStatus.CANCELLED.value
            submission.finished_at = datetime.utcnow()
            submission.run_duration_seconds = None
            submission.token_count = None
            submission.token_cost_usd = None
            submission.token_cost_currency = None
            submission.failure_reason = "Run cancelled because it was deleted"
            self.db.add(submission)
            self.db.flush()

    @staticmethod
    def _remove_submission_runtime_directory(submission_root: Path) -> None:
        # Docker Desktop can briefly keep a bind-mounted directory open, or
        # finish a delayed filesystem write just after the container is removed.
        # Retry those Windows-specific transient errors before giving up.
        for attempt in range(6):
            try:
                # ``onexc`` was added in Python 3.12. Production hosts may
                # still run 3.11, where ``onerror`` is the compatible hook.
                shutil.rmtree(submission_root, onerror=RunService._clear_readonly_for_removal_legacy)
                return
            except FileNotFoundError:
                return
            except OSError as exc:
                if not RunService._is_retryable_runtime_removal_error(exc):
                    raise
                if attempt == 5:
                    raise
                time.sleep(0.25 * (attempt + 1))

    @staticmethod
    def _is_retryable_runtime_removal_error(error: OSError) -> bool:
        return error.errno in {errno.EACCES, errno.EPERM, errno.ENOTEMPTY} or getattr(error, "winerror", None) in {
            5,   # Access is denied.
            32,  # The process cannot access the file because it is being used.
            145, # The directory is not empty.
        }

    @staticmethod
    def _clear_readonly_for_removal(remove_func, path: str, error: OSError) -> None:
        if not isinstance(error, OSError) or error.errno not in {errno.EACCES, errno.EPERM}:
            raise error
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        remove_func(path)

    @staticmethod
    def _clear_readonly_for_removal_legacy(remove_func, path: str, exc_info) -> None:
        """Adapt shutil.rmtree's pre-3.12 ``onerror`` callback signature."""
        error = exc_info[1]
        if not isinstance(error, OSError):
            raise error
        RunService._clear_readonly_for_removal(remove_func, path, error)

    def to_detail(self, submission: Submission, *, include_step_logs: bool = True, include_tests: bool = True) -> SubmissionDetail:
        source = self.db.get(AgentSubmission, submission.submission_id)
        steps = [StepState.model_validate(step) for step in json.loads(submission.steps_json or "[]")]
        if include_step_logs:
            steps = self.attach_step_logs(steps, self.read_events(submission))
        else:
            steps = [step.model_copy(update={"logs": []}) for step in steps]
        tests_are_private = self.tests_are_private(submission, source=source)
        if tests_are_private:
            # Official competition participants may see aggregate counters, but
            # never test titles, paths, scenarios, output, or runner test logs.
            # Keep this redaction in the API service rather than relying on the
            # frontend so direct requests receive the same protected payload.
            tests = []
            private_step_descriptions = {
                "pending": "Evaluation pending",
                "running": "Evaluation in progress",
                "completed": "Evaluation completed",
                "failed": "Evaluation finished",
                "cancelled": "Evaluation cancelled",
            }
            steps = [
                step.model_copy(
                    update={
                        "logs": [],
                        "description": private_step_descriptions.get(step.status, "Evaluation"),
                    }
                )
                if step.key == "run_tests"
                else step
                for step in steps
            ]
        else:
            tests = self._read_submission_tests(submission) if include_tests else []
        node_states = self.artifact_service.read_node_visual_states(submission)
        visual_events = self.read_visual_events(submission)
        node_states = self._overlay_visual_event_states(node_states, visual_events)
        manual_edit_state = self.get_manual_edit_state(submission)
        return SubmissionDetail(
            id=submission.id,
            submission_id=submission.submission_id,
            display_name=source.display_name if source else submission.submission_display_name,
            model_name=source.model_name if source else submission.model_name,
            original_filename=source.original_filename if source else (submission.original_filename or "agent.zip"),
            catalog=source.catalog if source else submission.catalog,
            competition_id=source.competition_id if source else submission.competition_id,
            requirement_id=submission.requirement_id,
            runtime=submission.runtime,
            agent_source=submission.agent_source,
            status=submission.status,
            score=submission.score,
            test_pass_rate=submission.test_pass_rate,
            passed_count=submission.passed_count,
            failed_count=submission.failed_count,
            run_duration_seconds=submission.run_duration_seconds,
            token_count=submission.token_count,
            token_cost_usd=submission.token_cost_usd,
            token_cost_currency=submission.token_cost_currency,
            cost_source=submission.cost_source,
            cost_complete=submission.cost_complete,
            feature_implemented_count=submission.feature_implemented_count,
            feature_total_count=submission.feature_total_count,
            feature_implementation_rate=submission.feature_implementation_rate,
            created_at=submission.created_at,
            started_at=submission.started_at,
            finished_at=submission.finished_at,
            failure_reason=submission.failure_reason,
            steps=steps,
            stdout_path=submission.stdout_path,
            stderr_path=submission.stderr_path,
            result_path=submission.result_path,
            workspace_path=submission.workspace_path,
            logs_available=bool(submission.stdout_path or submission.stderr_path),
            tests=tests,
            node_states=node_states,
            can_pause=self.can_pause(submission),
            can_resume=self.can_resume(submission),
            can_rewind=self.can_rewind(submission),
            can_manual_edit=bool(manual_edit_state["can_manual_edit"]),
            manual_edit_node_id=manual_edit_state["manual_edit_node_id"],
            manual_edit_phase=manual_edit_state["manual_edit_phase"],
            manual_edit_dirty=bool(manual_edit_state["manual_edit_dirty"]),
            pause_available=bool(self.get_pause_request_path(submission)),
            can_cancel=self.can_cancel(submission),
            can_continue=self.can_continue(submission),
        )

    def tests_are_private(
        self,
        submission: Submission,
        *,
        source: AgentSubmission | None = None,
    ) -> bool:
        """Return whether per-test artifacts must be hidden for this run."""
        source = source or self.db.get(AgentSubmission, submission.submission_id)
        catalog = str(source.catalog if source else submission.catalog or "").strip().lower()
        if catalog != "competition":
            return False

        competition_id = str(
            (source.competition_id if source else submission.competition_id)
            or str(submission.requirement_id or "").split("--", 1)[0]
        ).strip().lower()
        if not competition_id:
            return False
        return any(
            str(competition.id or "").strip().lower() == competition_id
            and str(competition.type or "").strip().lower() == "official"
            for competition in RequirementCatalogService.for_catalog(self.db, "competition").list_competitions()
        )

    def _read_submission_tests(self, submission: Submission) -> list[dict]:
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is not None:
            report_path = self.runtime_paths.get_arc_dir_from_workspace(workspace_path) / "playwright-report.json"
            if report_path.is_file():
                return ResultParser().parse_playwright_report(report_path).get("tests", [])
        if submission.result_path and Path(submission.result_path).exists():
            return json.loads(Path(submission.result_path).read_text(encoding="utf-8")).get("tests", [])
        report = self.artifact_service.read_snapshot_json(submission, ".arc/playwright-report.json")
        if isinstance(report, dict):
            return ResultParser().parse_playwright_report_payload(report).get("tests", [])
        return []

    def _read_runner_event_payloads(self, submission: Submission) -> list[dict]:
        content = self._read_runner_event_content(submission)
        events: list[dict] = []
        for raw_line in content.splitlines():
            try:
                parsed = json.loads(raw_line.strip())
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                events.append(parsed)
        return events

    def _read_runner_event_content(self, submission: Submission) -> str:
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path:
            runner_events_path = self.runtime_paths.get_arc_dir_from_workspace(workspace_path) / "runner-events.jsonl"
            return runner_events_path.read_text(encoding="utf-8", errors="replace") if runner_events_path.is_file() else ""
        return self.artifact_service.read_snapshot_text(submission, ".arc/runner-events.jsonl") or ""

    @staticmethod
    def _overlay_visual_event_states(
        node_states: dict[str, str],
        visual_events: list[SubmissionVisualEvent],
    ) -> dict[str, str]:
        resolved = dict(node_states)
        for event in visual_events:
            node_id = str(event.node_id or "").strip()
            if not node_id:
                continue
            visual_state: str | None = None
            if event.phase == "design" and event.status == "completed":
                visual_state = "design"
            elif event.phase == "implement" and event.status == "completed":
                visual_state = "implement"
            elif event.phase == "test" and event.status == "passed":
                visual_state = "test-passed"
            elif event.phase == "test" and event.status == "failed":
                visual_state = "test-failed"
            if visual_state is not None:
                resolved[node_id] = visual_state
        return resolved

    def read_events(self, submission: Submission) -> list[dict]:
        events: list[dict] = []
        for parsed in self._read_runner_event_payloads(submission):
            timestamp = str(parsed.get("timestamp", "")).strip()
            step_key = str(parsed.get("step_key", "")).strip()
            status = str(parsed.get("status", "info")).strip() or "info"
            message = str(parsed.get("message", "")).strip()
            if not timestamp or not step_key or not message:
                continue
            events.append(
                {
                    "timestamp": timestamp,
                    "step_key": step_key,
                    "status": status,
                    "message": message,
                }
            )
        return events

    def read_visual_events(self, submission: Submission) -> list[SubmissionVisualEvent]:
        visual_events: list[SubmissionVisualEvent] = []
        for parsed in self._read_runner_event_payloads(submission):
            if str(parsed.get("type", "")).strip() == "runner_state":
                continue
            if str(parsed.get("type", "")).strip() != "requirement_state":
                continue

            node_id = str(parsed.get("node_id", "")).strip()
            phase = str(parsed.get("phase", "")).strip()
            status = str(parsed.get("status", "")).strip()
            timestamp = str(parsed.get("timestamp", "")).strip()
            message = str(parsed.get("message", "")).strip() or None

            if not node_id or phase not in {"design", "implement", "test"} or status not in {"completed", "passed", "failed"} or not timestamp:
                continue

            visual_events.append(
                SubmissionVisualEvent(
                    type="requirement_state",
                    node_id=node_id,
                    phase=phase,
                    status=status,
                    timestamp=timestamp,
                    message=message,
                )
            )
        return visual_events

    def read_runner_events(self, submission: Submission) -> list[SubmissionRunnerEvent]:
        runner_events: list[SubmissionRunnerEvent] = []
        for index, parsed in enumerate(self._read_runner_event_payloads(submission)):
            timestamp = str(parsed.get("timestamp", "")).strip()
            summary = str(parsed.get("summary") or parsed.get("message") or "").strip()
            if not timestamp or not summary:
                continue
            runner_events.append(
                SubmissionRunnerEvent(
                    event_id=str(parsed.get("event_id") or f"legacy-{index}"),
                    timestamp=timestamp,
                    stage=str(parsed.get("stage") or self._stage_for_step_key(str(parsed.get("step_key", "")))),
                    status=str(parsed.get("status") or parsed.get("state") or "info"),
                    summary=summary,
                    heartbeat=bool(parsed.get("heartbeat")),
                    artifact_reference=(str(parsed.get("artifact_reference")).strip() or None)
                    if parsed.get("artifact_reference") is not None
                    else None,
                )
            )
        return runner_events

    @staticmethod
    def _stage_for_step_key(step_key: str) -> str:
        return {
            "deploy_agent": "Preparing environment",
            "start_agent": "Running agent",
            "run_tests": "Evaluating result",
        }.get(step_key, "Running agent")

    def read_runner_event_lines(self, submission: Submission) -> list[str]:
        lines: list[str] = []
        for raw_line in self._read_runner_event_content(submission).splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            lines.append(self.format_runner_event_log_line(parsed) if isinstance(parsed, dict) else line)
        return lines

    def read_event_lines(self, submission: Submission) -> list[str]:
        return [
            self.format_runner_event_log_line(event)
            for event in self.read_events(submission)
        ]

    @staticmethod
    def format_runner_event_log_line(payload: dict) -> str:
        event_type = str(payload.get("type", "")).strip()
        message = str(payload.get("message", "")).strip()
        if not event_type:
            return message
        if event_type == "requirement_state":
            node_id = str(payload.get("node_id", "")).strip() or "node"
            phase = str(payload.get("phase", "")).strip() or "phase"
            status = str(payload.get("status", "")).strip() or "status"
            summary = f"node_id={node_id} phase={phase} status={status}"
            if message:
                summary = f"{summary} message={message}"
            return summary
        if event_type == "runner_state":
            state = str(payload.get("state", "")).strip() or "state"
            summary = f"state={state}"
            if message:
                summary = f"{summary} message={message}"
            return summary
        if event_type == "signal":
            reason = str(payload.get("reason", "")).strip() or "refresh"
            return f"reason={reason}"
        if event_type == "interface_upsert":
            interface_id = str(payload.get("interface_id", "")).strip() or "interface"
            req_ids = payload.get("req_ids")
            req_summary = ""
            if isinstance(req_ids, list) and req_ids:
                req_summary = f" req_ids={','.join(str(item) for item in req_ids)}"
            return f"interface_id={interface_id}{req_summary}"
        if event_type == "interface_status":
            interface_id = str(payload.get("interface_id", "")).strip() or "interface"
            implemented = "implemented" if bool(payload.get("implemented")) else "planned"
            return f"interface_id={interface_id} status={implemented}"
        if event_type == "test_upsert":
            test_id = str(payload.get("test_id", "")).strip() or "test"
            req_id = str(payload.get("req_id", "")).strip()
            suffix = f" req_id={req_id}" if req_id else ""
            return f"test_id={test_id}{suffix}"
        if event_type in {
            "manual_edit_started",
            "workspace_file_updated",
            "manual_test_created",
            "manual_edit_committed",
            "resume_patch_conflict",
        }:
            context_bits = [
                str(payload.get("node_id", "")).strip(),
                str(payload.get("phase", "")).strip(),
                str(payload.get("file_path", "")).strip(),
            ]
            context = " ".join(bit for bit in context_bits if bit)
            if context and message:
                return f"{context} message={message}"
            if context:
                return context
        if message:
            return f"message={message}"
        return event_type

    def update_steps(self, submission: Submission, steps: list[StepState]) -> None:
        submission.steps_json = json.dumps([step.model_dump() for step in steps])
        self.db.add(submission)
        self._commit()
        self.db.refresh(submission)
        SubmissionEventStream.publish(submission.id, reason="steps_updated", submission=True)

    def mark_running(self, submission: Submission, workspace_path: Path) -> None:
        self._assert_execution_owner()
        submission.status = SubmissionStatus.RUNNING.value
        submission.started_at = datetime.utcnow()
        submission.workspace_path = str(workspace_path)
        submission.stdout_path = str(self.runtime_paths.get_arc_dir_from_workspace(workspace_path) / "stdout.log")
        submission.stderr_path = None
        submission.failure_reason = None
        if self.execution_owner is not None:
            _, attempt_id, generation = self.execution_owner
            self.db.query(RunAttempt).filter(
                RunAttempt.id == attempt_id,
                RunAttempt.run_id == submission.id,
                RunAttempt.generation == generation,
                RunAttempt.status == "STARTING",
            ).update({RunAttempt.status: "RUNNING"})
        self.db.add(submission)
        self._commit()
        self.db.refresh(submission)
        SubmissionEventStream.publish(
            submission.id,
            reason="running",
            submission=True,
            traceability_selected=True,
            traceability_all=True,
        )

    def finalize(
        self,
        submission: Submission,
        status: SubmissionStatus,
        passed_count: int,
        failed_count: int,
        score: float,
        stdout_path: Path | None,
        stderr_path: Path | None,
        result_path: Path | None,
        failure_reason: str | None = None,
        test_pass_rate: float | None = None,
        feature_implemented_count: int = 0,
        feature_total_count: int = 0,
        feature_implementation_rate: float | None = None,
        run_duration_seconds: float | None = None,
        token_count: int | None = None,
        token_cost: float | None = None,
        token_cost_currency: str | None = None,
    ) -> None:
        # Execution completion and test correctness are separate. An evaluation
        # that produced test results completed successfully even at zero passes.
        if status == SubmissionStatus.FAILED and passed_count + failed_count > 0:
            status = SubmissionStatus.PASSED
            failure_reason = None
        submission.status = status.value
        submission.finished_at = datetime.utcnow()
        submission.passed_count = passed_count
        submission.failed_count = failed_count
        submission.score = score
        submission.test_pass_rate = test_pass_rate if test_pass_rate is not None else score
        # The runner measures this around the generation-agent subprocess and
        # excludes image preparation, dependency installation, deployment,
        # tests, and paused waiting. Never derive it from the run wall clock.
        submission.run_duration_seconds = (
            max(0, int(round(run_duration_seconds)))
            if isinstance(run_duration_seconds, (int, float))
            else None
        )
        if submission.cost_source != "model_proxy":
            submission.token_count = max(0, int(token_count)) if isinstance(token_count, int) else None
            # Historical physical column name; token_cost_currency is authoritative.
            submission.token_cost_usd = max(0.0, float(token_cost)) if isinstance(token_cost, (int, float)) else None
            submission.token_cost_currency = (
                str(token_cost_currency).strip().upper()[:8]
                if token_cost_currency and submission.token_cost_usd is not None
                else None
            )
        # score remains the backwards-compatible test pass-rate field.
        submission.feature_implemented_count = max(0, feature_implemented_count)
        submission.feature_total_count = max(0, feature_total_count)
        submission.feature_implementation_rate = (
            feature_implementation_rate if feature_implementation_rate is not None else 0.0
        )
        submission.stdout_path = str(stdout_path) if stdout_path else None
        submission.stderr_path = str(stderr_path) if stderr_path else None
        submission.result_path = str(result_path) if result_path else None
        submission.failure_reason = failure_reason
        self.db.add(submission)
        self._commit()
        self.db.refresh(submission)
        if status in {SubmissionStatus.PASSED, SubmissionStatus.FAILED}:
            try:
                from app.services.competition_template_service import CompetitionTemplateService

                CompetitionTemplateService(self.db).freeze_completed_run(submission)
            except Exception:  # noqa: BLE001 - freezing is optional to the completed run
                # A run result remains valid if optional template freezing
                # fails; it simply will not become an eligible template.
                self.db.rollback()
        notification_kind = "completed" if status == SubmissionStatus.PASSED else "failed"
        notification_title = "Run completed" if status == SubmissionStatus.PASSED else "Run failed"
        notification_body = (
            f"Your run completed with score {score:.1f}."
            if status == SubmissionStatus.PASSED
            else f"Your run failed: {failure_reason or 'See complete logs for details.'}"
        )
        self._create_run_notification(submission, notification_kind, notification_title, notification_body)
        SubmissionEventStream.publish(
            submission.id,
            reason=f"finalized:{status.value.lower()}",
            submission=True,
            commit_history=True,
            traceability_selected=True,
            traceability_all=True,
            preview=True,
        )

    def _create_run_notification(self, submission: Submission, kind: str, title: str, body: str) -> None:
        NotificationService(self.db).create_once(
            user_id=submission.user_id,
            run_id=submission.id,
            kind=kind,
            title=title,
            body=body,
        )

    def update_status(self, submission: Submission, status: SubmissionStatus, failure_reason: str | None = None) -> None:
        submission.status = status.value
        submission.failure_reason = failure_reason
        self.db.add(submission)
        self._commit()
        self.db.refresh(submission)
        SubmissionEventStream.publish(submission.id, reason=f"status:{status.value.lower()}", submission=True)

    def reject_pending_run(self, submission: Submission, reason: str) -> None:
        """Make an admission-rejected Run terminal instead of leaving it PENDING."""
        self.db.refresh(submission)
        if submission.status != SubmissionStatus.PENDING.value:
            return
        submission.status = SubmissionStatus.FAILED.value
        submission.finished_at = datetime.utcnow()
        submission.failure_reason = reason
        submission.steps_json = json.dumps([
            step.model_dump()
            for step in self.build_failed_step_states(
                failed_key="deploy_agent",
                reason=reason,
                completed=set(),
            )
        ])
        self.db.add(submission)
        self.db.commit()
        self.db.refresh(submission)
        SubmissionEventStream.publish(submission.id, reason="admission_rejected", submission=True)

    @staticmethod
    def can_pause(submission: Submission) -> bool:
        return submission.status == SubmissionStatus.RUNNING.value

    @staticmethod
    def can_cancel(submission: Submission) -> bool:
        return submission.status in {
            SubmissionStatus.PENDING.value,
            SubmissionStatus.QUEUED.value,
            SubmissionStatus.STARTING.value,
            SubmissionStatus.RUNNING.value,
            SubmissionStatus.PAUSED.value,
            SubmissionStatus.PAUSE_REQUESTED.value,
            SubmissionStatus.RESUME_REQUESTED.value,
        }

    @staticmethod
    def can_resume(submission: Submission) -> bool:
        return submission.status == SubmissionStatus.PAUSED.value

    def can_continue(self, submission: Submission) -> bool:
        if submission.agent_source not in {
            AgentSourceType.BUILTIN_ARC_AGENT.value,
            AgentSourceType.DEMO_REPLAY.value,
        }:
            return False
        if submission.status not in {
            SubmissionStatus.PASSED.value,
            SubmissionStatus.FAILED.value,
            SubmissionStatus.CANCELLED.value,
        }:
            return False
        if submission.agent_source == AgentSourceType.DEMO_REPLAY.value:
            checkpoint = self.read_checkpoint(submission)
            current_index = int(checkpoint.get("last_completed_index", 0) or 0)
            # Requirement edits themselves materialize 31 and 34. Continue is
            # available only for the prepared processing transitions; step 35
            # is terminal and must not expose a dead Continue button.
            if current_index not in {31, 32, 34}:
                return False
        return self.get_template_repo_path(submission) is not None

    def can_rewind(self, submission: Submission) -> bool:
        if submission.status not in {
            SubmissionStatus.PAUSED.value,
            SubmissionStatus.PASSED.value,
            SubmissionStatus.FAILED.value,
        }:
            return False
        return self._is_arc_workspace(submission)

    @staticmethod
    def _read_text(path: Path) -> str:
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def _clear_execution_artifacts(self, submission: Submission) -> None:
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is None:
            raise FileNotFoundError("Submission workspace is not available")
        arc_dir = self.runtime_paths.get_arc_dir_from_workspace(workspace_path)
        for filename in [
            "stdout.log",
            "playwright-report.json",
            "runner-events.jsonl",
            "preview-ready.json",
            "pause.request.json",
            "resume.request.json",
            "continue.request.json",
        ]:
            target = arc_dir / filename
            if target.exists():
                target.unlink()
        traceability_dir = arc_dir / "traceability"
        if traceability_dir.is_dir():
            shutil.rmtree(traceability_dir)

    def _get_demo_state_checkpoint_dir(self, workspace_path: Path, completed_index: int) -> Path:
        normalized_index = max(int(completed_index or 0), 0)
        return self.runtime_paths.get_arc_dir_from_workspace(workspace_path) / "state-checkpoints" / f"step-{normalized_index:04d}"

    @staticmethod
    def _append_runner_state_event(runner_events_path: Path, *, state: str, message: str) -> None:
        runner_events_path.parent.mkdir(parents=True, exist_ok=True)
        with runner_events_path.open("a", encoding="utf-8") as output:
            output.write(
                json.dumps(
                    {
                        "type": "runner_state",
                        "state": state,
                        "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                        "message": message,
                    },
                    ensure_ascii=True,
                )
                + "\n"
            )

    def _append_runner_event(self, submission: Submission, payload: dict[str, object]) -> None:
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is None:
            return
        runner_events_path = self.runtime_paths.get_arc_dir_from_workspace(workspace_path) / "runner-events.jsonl"
        runner_events_path.parent.mkdir(parents=True, exist_ok=True)
        enriched_payload = {
            "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            **payload,
        }
        with runner_events_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(enriched_payload, ensure_ascii=True) + "\n")

    def _append_runner_refresh_signal(
        self,
        submission: Submission,
        *,
        reason: str,
        submission_refresh: bool = False,
        logs: bool = False,
        commit_history: bool = False,
        traceability_selected: bool = False,
        traceability_all: bool = False,
        preview: bool = False,
        message: str | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "type": "signal",
            "reason": reason,
            "refresh": {
                "submission": submission_refresh,
                "logs": logs,
                "commit_history": commit_history,
                "traceability_selected": traceability_selected,
                "traceability_all": traceability_all,
                "preview": preview,
            },
        }
        if message:
            payload["message"] = message
        self._append_runner_event(submission, payload)

    def _notify_traceability_tables_changed(self, workspace_path: Path) -> None:
        traceability_dir = self.runtime_paths.get_arc_dir_from_workspace(workspace_path) / "traceability"
        if traceability_dir.is_dir():
            self.artifact_service.refresh_traceability_snapshot_for_workspace(workspace_path, force=True)

    def _parse_git_status_paths(self, status_output: str) -> list[str]:
        dirty_files: list[str] = []
        for raw_line in status_output.splitlines():
            line = raw_line.rstrip()
            if len(line) < 4:
                continue
            candidate = line[3:].strip()
            if " -> " in candidate:
                candidate = candidate.split(" -> ", 1)[1].strip()
            if candidate:
                dirty_files.append(candidate.replace("\\", "/"))
        return dirty_files

    def build_manual_edit_commit_preview(self, submission: Submission) -> dict[str, object]:
        if not self.can_manual_edit(submission):
            raise ValueError("Submission is not in paused manual edit mode")
        manual_edit_state = self.get_manual_edit_state(submission)
        project_dir = self.get_template_repo_path(submission)
        dirty_files: list[str] = []
        if project_dir is not None and (project_dir / ".git").exists():
            dirty_files = self._parse_git_status_paths(self._run_git(project_dir, ["status", "--porcelain"],))
        return {
            "message": self._build_manual_edit_commit_message(
                manual_edit_state["manual_edit_node_id"],
                manual_edit_state["manual_edit_phase"],
            ),
            "node_id": manual_edit_state["manual_edit_node_id"],
            "phase": manual_edit_state["manual_edit_phase"],
            "dirty": bool(dirty_files) or bool(manual_edit_state["manual_edit_dirty"]),
            "dirty_files": dirty_files,
        }

    def commit_manual_edit_session(self, submission: Submission) -> dict[str, object]:
        preview = self.build_manual_edit_commit_preview(submission)
        project_dir = self.get_template_repo_path(submission)
        if project_dir is None:
            raise FileNotFoundError("Submission workspace is not available")
        dirty_files = list(preview["dirty_files"]) if isinstance(preview.get("dirty_files"), list) else []
        manual_session = self.read_checkpoint(submission).get("manual_edit_session")
        pending_tests = manual_session.get("pending_test_creations", []) if isinstance(manual_session, dict) else []
        node_id = preview.get("node_id")
        test_edit = bool(pending_tests) or any(str(path).replace("\\", "/").startswith(("tests/", "test/")) for path in dirty_files)
        if test_edit and node_id:
            # A completed node must be made runnable again before ARC resumes;
            # otherwise a test-only edit would be invisible to the queue.
            try:
                self._reset_arc_queue_for_node(submission, str(node_id))
            except (FileNotFoundError, ValueError):
                # The ARC workflow will reconcile a missing/incompatible queue
                # on resume (for example after a requirement-tree edit).
                pass
        self._write_arc_resume_context(
            submission,
            {
                "mode": "manual_test_edit" if test_edit else "pause",
                "node_id": node_id,
                "phase": preview.get("phase"),
                "dirty_files": dirty_files,
                "pending_test_creations": pending_tests,
            },
        )
        committed = False
        if dirty_files:
            self._run_git(project_dir, ["add", "."])
            commit_output = self._run_git(project_dir, ["commit", "-m", str(preview["message"])])
            committed = bool(commit_output is not None)
            self._append_runner_event(
                submission,
                {
                    "type": "manual_edit_committed",
                    "node_id": preview["node_id"],
                    "phase": preview["phase"],
                    "message": str(preview["message"]),
                },
            )
            self._append_runner_refresh_signal(
                submission,
                reason="commit_history_changed",
                commit_history=True,
                preview=True,
                message=str(preview["message"]),
            )

        checkpoint = self.prepare_manual_edit_session(submission)
        manual_edit_session = checkpoint.get("manual_edit_session")
        if isinstance(manual_edit_session, dict):
            manual_edit_session["has_workspace_changes"] = False
            manual_edit_session["has_traceability_changes"] = False
            manual_edit_session["pending_test_creations"] = []
            manual_edit_session["pending_commit_message"] = str(preview["message"])
            checkpoint["manual_edit_session"] = manual_edit_session
            self.write_checkpoint(submission, checkpoint)
        return {
            "committed": committed,
            **preview,
        }

    def _write_arc_resume_context(self, submission: Submission, payload: dict[str, object]) -> None:
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is None:
            return
        arc_dir = workspace_path / "template" / ".arc"
        arc_dir.mkdir(parents=True, exist_ok=True)
        self._write_json_atomic(arc_dir / "resume-context.json", payload)

    @distributed_locked(lambda self, submission: f"submission:{submission.id}")
    def prepare_resume_from_pause(self, submission: Submission) -> dict[str, object]:
        commit_result = {"committed": False}
        if self.can_manual_edit(submission):
            commit_result = self.commit_manual_edit_session(submission)
            checkpoint = self.read_checkpoint(submission)
            checkpoint["pause_mode"] = "checkpoint"
            checkpoint["manual_edit_session"] = None
            checkpoint["resume_requires_restart"] = True
            checkpoint["runtime_state_restored"] = True
            checkpoint["resume_patch_conflict"] = False
            self.write_checkpoint(submission, checkpoint)
            return commit_result

        checkpoint = self.read_checkpoint(submission)
        checkpoint["pause_mode"] = "checkpoint"
        checkpoint["resume_requires_restart"] = True
        self.write_checkpoint(submission, checkpoint)
        return commit_result

    @distributed_locked(lambda self, submission, **kwargs: f"submission:{submission.id}")
    def mark_paused_for_manual_edit(self, submission: Submission, *, reason: str) -> None:
        if not self._is_arc_workspace(submission):
            return
        checkpoint = self.prepare_manual_edit_session(submission)
        self.update_steps(
            submission,
            self.build_step_states(
                active_key="start_agent",
                completed={"deploy_agent"},
                description="Paused editing session",
            ),
        )
        manual_edit_session = checkpoint.get("manual_edit_session")
        if isinstance(manual_edit_session, dict):
            self._append_runner_event(
                submission,
                {
                    "type": "manual_edit_started",
                    "node_id": manual_edit_session.get("current_node_id"),
                    "phase": manual_edit_session.get("current_phase"),
                    "message": reason,
                },
            )

    @distributed_locked(lambda self, submission, **kwargs: f"submission:{submission.id}")
    def mark_resume_patch_conflict(self, submission: Submission, *, message: str) -> None:
        if not self._is_arc_workspace(submission):
            return
        checkpoint = self.prepare_manual_edit_session(submission)
        checkpoint["resume_patch_conflict"] = True
        self.write_checkpoint(submission, checkpoint)
        self.update_steps(
            submission,
            self.build_step_states(
                active_key="start_agent",
                completed={"deploy_agent"},
                description="Paused after replay merge conflict",
            ),
        )
        self.append_step_event(
            submission.id,
            step_key="start_agent",
            message=message,
            status="error",
        )

    def _restore_demo_runtime_state_from_checkpoint(
        self,
        submission: Submission,
        *,
        workspace_path: Path,
        completed_index: int,
        paused_message: str,
    ) -> None:
        arc_dir = self.runtime_paths.get_arc_dir_from_workspace(workspace_path)
        state_checkpoint_dir = self._get_demo_state_checkpoint_dir(workspace_path, completed_index)
        runner_events_path = arc_dir / "runner-events.jsonl"

        restored_from_saved_checkpoint = False
        required_files = ("runner-events.jsonl",)
        required_dirs = ("traceability",)
        if state_checkpoint_dir.is_dir():
            missing_files: list[str] = []
            for filename in required_files:
                if not (state_checkpoint_dir / filename).is_file():
                    missing_files.append(filename)
            for dirname in required_dirs:
                if not (state_checkpoint_dir / dirname).is_dir():
                    missing_files.append(dirname)
            if missing_files:
                raise FileNotFoundError(
                    f"Checkpoint state is incomplete for step {completed_index}: {', '.join(missing_files)}"
                )
            for dirname in required_dirs:
                source_dir = state_checkpoint_dir / dirname
                target_dir = arc_dir / dirname
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                shutil.copytree(source_dir, target_dir)
            for filename in required_files:
                source_path = state_checkpoint_dir / filename
                if source_path.is_file():
                    self._write_atomic_bytes(
                        arc_dir / filename,
                        source_path.read_bytes(),
                    )
                    continue
            restored_from_saved_checkpoint = True

        if not restored_from_saved_checkpoint:
            if completed_index > 0:
                raise FileNotFoundError(
                    f"Checkpoint state package is missing for completed step {completed_index}: {state_checkpoint_dir}"
                )
            payload = self.read_submission_task_documents(submission)
            requirements_yaml = payload.get("requirements_yaml", "").strip()
            if not requirements_yaml:
                raise ValueError("Submission requirements.yaml is required to restore demo runtime state")
            self.write_submission_task_runtime_artifacts(submission, requirements_yaml)
            runner_events_path.write_text("", encoding="utf-8")

        self._append_runner_state_event(
            runner_events_path,
            state="paused",
            message=paused_message,
        )

    @staticmethod
    def _restore_demo_target_tree_to_base(workspace_path: Path, replay_paths: DemoReplayPaths) -> None:
        module = load_demo_agent_module()
        module.reset_target_worktree(workspace_path / "template")
        for source_path in replay_paths.source_template_dir.iterdir():
            if source_path.name == "arc-replay":
                continue
            target_path = workspace_path / "template" / source_path.name
            if source_path.is_dir():
                shutil.copytree(source_path, target_path, dirs_exist_ok=True)
            elif source_path.is_file():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target_path)

    def rebuild_demo_submission_to_checkpoint(self, submission: Submission) -> None:
        workspace_path, replay_paths, steps = self._load_demo_replay_steps(submission)
        checkpoint = self.read_checkpoint(submission)
        completed_index = int(checkpoint.get("last_completed_index", 0) or 0)
        if completed_index < 0:
            completed_index = 0
        if completed_index > len(steps):
            completed_index = len(steps)
        checkpoint["runtime_state_restored"] = False
        checkpoint["pause_mode"] = "checkpoint"
        checkpoint["manual_edit_session"] = None
        checkpoint["resume_patch_conflict"] = False
        self.write_checkpoint(submission, checkpoint)
        project_root = self.get_template_repo_path(submission)
        if project_root is None:
            raise FileNotFoundError("Submission workspace is not available")

        if completed_index > 0:
            history_payload = self.artifact_service.read_commit_history(submission)
            commits = history_payload.get("commits", [])
            if not isinstance(commits, list) or completed_index > len(commits):
                raise RuntimeError("Checkpoint commit history is not available for pause restore")
            target_commit_oid = str(commits[completed_index - 1].get("oid") or "").strip()
            if not target_commit_oid:
                raise RuntimeError("Checkpoint target commit is missing")
            self._run_git(project_root, ["reset", "--hard", target_commit_oid])
        else:
            self._restore_demo_target_tree_to_base(workspace_path, replay_paths)
        self._run_git(project_root, ["clean", "-fd"])
        self._clear_execution_artifacts(submission)
        self._restore_demo_runtime_state_from_checkpoint(
            submission,
            workspace_path=workspace_path,
            completed_index=completed_index,
            paused_message="Execution paused at restored checkpoint",
        )
        checkpoint["runtime_state_restored"] = True
        self.write_checkpoint(submission, checkpoint)

    @staticmethod
    def _build_checkpoint_completed_entries_from_steps(
        steps: list[DemoReplayStep],
    ) -> list[dict[str, int | str | None]]:
        completed_entries: list[dict[str, int | str | None]] = []
        for step in steps:
            completed_entries.append(
                {
                    "index": int(step.index),
                    "node_id": step.node_id,
                    "phase": step.phase,
                    "task_path": None,
                    "source_commit": step.source_commit,
                    "commit_message": step.commit_message,
                }
            )
        return completed_entries

    def _mark_rewound_paused(self, submission: Submission, *, node_id: str, phase: str, commit_oid: str) -> None:
        submission.status = SubmissionStatus.PAUSED.value
        submission.started_at = None
        submission.finished_at = None
        submission.score = None
        submission.test_pass_rate = None
        submission.passed_count = 0
        submission.failed_count = 0
        submission.run_duration_seconds = None
        submission.token_count = None
        submission.token_cost_usd = None
        submission.token_cost_currency = None
        submission.feature_implemented_count = 0
        submission.feature_total_count = 0
        submission.feature_implementation_rate = None
        submission.stdout_path = None
        submission.stderr_path = None
        submission.result_path = None
        submission.failure_reason = None
        self.db.add(submission)
        self._commit()
        self.db.refresh(submission)
        self.update_steps(
            submission,
            self.build_step_states(
                active_key="start_agent",
                completed={"deploy_agent"},
                description=f"Paused at rewound checkpoint {node_id} ({phase})",
            ),
        )
        self.append_step_event(
            submission.id,
            step_key="start_agent",
            message=f"Workspace rewound to commit {commit_oid[:7]} at {node_id} ({phase})",
            status="info",
        )

    @staticmethod
    def _stop_runner_container(submission_id: str, generation: int) -> None:
        try:
            DockerManager().remove_submission_container(submission_id, generation)
        except Exception:  # noqa: BLE001
            return

    @staticmethod
    def _remove_runner_containers_for_deletion(submission_id: str) -> None:
        """Remove local runner containers without requiring Docker on the API host.

        In the distributed deployment the container belongs to an executor,
        while DELETE is handled by an API node. Completed runs must remain
        deletable when that API node has no Docker daemon or cannot see the
        executor container. Active runs have already been marked cancelled by
        ``_cancel_for_deletion`` and the executor observes that state.
        """
        try:
            DockerManager().remove_all_submission_containers(submission_id)
        except Exception as exc:  # noqa: BLE001 - remote executor cleanup is best effort
            logger.warning("Could not remove local runner containers for deleted run %s: %s", submission_id, exc)

    def _stop_preview_container(self, submission_id: str) -> None:
        submission = self.db.get(Submission, submission_id)
        workspace = self.runtime_paths.resolve_existing_path(submission.workspace_path) if submission else None
        PreviewService(self.db).mark_stale(submission_id, workspace / "template" if workspace else None)
        self._commit()

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
            raise RuntimeError(stderr)
        return completed.stdout

    @staticmethod
    def _write_text_atomic(path: Path, content: str) -> None:
        RunService._write_atomic_bytes(path, content.encode("utf-8"))

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
        RunService._write_atomic_bytes(
            path,
            (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )

    @staticmethod
    def _read_json_table(path: Path) -> dict[str, dict]:
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): value for key, value in payload.items() if isinstance(value, dict)}

    @staticmethod
    def _write_json_table(path: Path, rows: dict[str, dict]) -> None:
        RunService._write_json_atomic(path, dict(sorted(rows.items())))

    def _write_traceability_tables_from_seed(self, traceability_dir: Path, seed: dict[str, list[dict]]) -> None:
        requirements = {
            str(item.get("req_id", "")).strip(): item
            for item in seed.get("requirements", [])
            if isinstance(item, dict) and str(item.get("req_id", "")).strip()
        }
        scenarios = {
            str(item.get("scenario_id", "")).strip(): item
            for item in seed.get("scenarios", [])
            if isinstance(item, dict) and str(item.get("scenario_id", "")).strip()
        }
        for table_name in (
            "requirements",
            "scenarios",
            "interfaces",
            "tests",
            "call_edges",
            "node_states",
            "node_contracts",
        ):
            rows = requirements if table_name == "requirements" else scenarios if table_name == "scenarios" else {}
            self._write_json_table(traceability_dir / f"{table_name}.json", rows)

    @staticmethod
    def _write_atomic_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        last_error: OSError | None = None
        for delay in (0.0, 0.05, 0.1, 0.2, 0.5, 1.0):
            if delay > 0:
                time.sleep(delay)
            tmp_path = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
            try:
                with open(tmp_path, "wb") as output:
                    output.write(content)
                os.replace(tmp_path, path)
                return
            except OSError as exc:
                last_error = exc
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
        if last_error is not None:
            raise last_error

    @staticmethod
    def _normalize_relative_path(file_path: str) -> Path:
        normalized = PurePosixPath(file_path.strip())
        if str(normalized) in {"", "."}:
            raise FileNotFoundError("Submission task asset path is required")
        if normalized.is_absolute() or ".." in normalized.parts:
            raise FileNotFoundError(f"Invalid submission task asset path: {file_path}")
        return Path(*normalized.parts)

    @staticmethod
    def build_step_states(active_key: str | None = None, completed: set[str] | None = None, description: str | None = None) -> list[StepState]:
        completed = completed or set()
        steps: list[StepState] = []
        for step in DEFAULT_STEPS:
            status = "pending"
            step_description = step.description
            if step.key in completed:
                status = "completed"
                step_description = "Done"
            elif active_key == step.key:
                status = "running"
                step_description = description or "Running"
            steps.append(StepState(key=step.key, title=step.title, status=status, description=step_description, logs=[]))
        return steps

    @staticmethod
    def build_failed_step_states(failed_key: str, reason: str, completed: set[str] | None = None) -> list[StepState]:
        completed = completed or set()
        steps: list[StepState] = []
        for step in DEFAULT_STEPS:
            if step.key in completed:
                status = "completed"
                step_description = "Done"
            elif step.key == failed_key:
                status = "failed"
                step_description = reason
            else:
                status = "pending"
                step_description = "Not reached"
            steps.append(StepState(key=step.key, title=step.title, status=status, description=step_description, logs=[]))
        return steps

    @staticmethod
    def build_cancelled_step_states(active_key: str, completed: set[str] | None = None) -> list[StepState]:
        completed = completed or set()
        steps: list[StepState] = []
        for step in DEFAULT_STEPS:
            if step.key in completed:
                status = "completed"
                description = "Done"
            elif step.key == active_key:
                status = "cancelled"
                description = "Cancelled by user"
            else:
                status = "pending"
                description = "Not reached"
            steps.append(StepState(key=step.key, title=step.title, status=status, description=description, logs=[]))
        return steps

    @staticmethod
    def attach_step_logs(steps: list[StepState], events: list[dict]) -> list[StepState]:
        enriched: list[StepState] = []
        for step in steps:
            existing_logs = list(step.logs or [])
            new_logs = [
                RunService.format_runner_event_log_line(event)
                for event in events
                if event.get("step_key") == step.key
            ]
            step_logs = existing_logs + [log for log in new_logs if log not in existing_logs]
            step_data = step.model_dump()
            # Test output is intentionally complete so every benchmark case
            # and its outcome remains visible on the run detail page. Other
            # stages retain a compact tail because agent heartbeats can be
            # extremely verbose during long generations.
            step_data["logs"] = step_logs if step.key == "run_tests" else step_logs[-5:]
            enriched.append(StepState(**step_data))
        return enriched

    def list_workspace_files(self, submission: Submission) -> list[dict]:
        workspace_path = self.runtime_paths.resolve_workspace_path(submission)
        if not workspace_path:
            snapshot_files = self.artifact_service.list_snapshot_files(submission)
            if snapshot_files is None:
                raise FileNotFoundError("Submission workspace is not available")
            tree: dict[str, dict] = {}
            for file_path in snapshot_files:
                parts = PurePosixPath(file_path).parts
                if not parts or parts[0].startswith((".arc", ".git")) or "node_modules" in parts:
                    continue
                current = tree
                for index, part in enumerate(parts):
                    entry = current.setdefault(part, {"children": {}})
                    if index == len(parts) - 1:
                        entry["is_file"] = True
                    current = entry["children"]

            def serialize(entries: dict[str, dict], prefix: PurePosixPath = PurePosixPath()) -> list[dict]:
                result = []
                for name, entry in entries.items():
                    path = prefix / name
                    is_directory = bool(entry["children"])
                    item = {"path": path.as_posix(), "name": name, "is_directory": is_directory}
                    if is_directory:
                        item["children"] = serialize(entry["children"], path)
                    result.append(item)
                return sorted(result, key=lambda item: (not item["is_directory"], item["name"]))

            return serialize(tree)
        project_dir = workspace_path / "template"
        if not project_dir.is_dir():
            raise FileNotFoundError("Project directory is not available")

        def walk_dir(path: Path, base_path: Path) -> dict:
            relative_path = path.relative_to(base_path)
            entry = {
                "path": str(relative_path.as_posix()),
                "name": path.name,
                "is_directory": path.is_dir(),
            }
            if path.is_dir():
                children = []
                for child in path.iterdir():
                    if child.name == "node_modules":
                        continue
                    if child.name.startswith(".arc"):
                        continue
                    if child.name.startswith(".git"):
                        continue
                    try:
                        children.append(walk_dir(child, base_path))
                    except Exception:
                        continue
                entry["children"] = sorted(children, key=lambda x: (not x["is_directory"], x["name"]))
            return entry

        try:
            children = []
            for child in project_dir.iterdir():
                if child.name == "node_modules":
                    continue
                if child.name.startswith(".arc"):
                    continue
                if child.name.startswith(".git"):
                    continue
                try:
                    children.append(walk_dir(child, project_dir))
                except Exception:
                    continue
            return sorted(children, key=lambda x: (not x["is_directory"], x["name"]))
        except Exception as e:
            raise RuntimeError(f"Failed to list workspace files: {e}")

    def create_project_bundle(self, submission: Submission) -> Path:
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if not workspace_path:
            bundle_dir = Path(tempfile.gettempdir()) / "arcbench-downloads"
            bundle_dir.mkdir(parents=True, exist_ok=True)
            bundle_path = bundle_dir / f"{submission.id}-template.zip"
            temporary_path = bundle_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            try:
                if not self.artifact_service.write_snapshot_zip(
                    submission,
                    temporary_path,
                    include=lambda path: not self._should_exclude_project_bundle_relative_path(Path(*path.parts)),
                ):
                    raise FileNotFoundError("Submission workspace is not available")
                os.replace(temporary_path, bundle_path)
                return bundle_path
            except FileNotFoundError:
                temporary_path.unlink(missing_ok=True)
                raise
            except Exception as exc:
                temporary_path.unlink(missing_ok=True)
                raise RuntimeError(f"Failed to package project snapshot: {exc}") from exc
        project_dir = workspace_path / "template"
        if not project_dir.is_dir():
            raise FileNotFoundError("Project directory is not available")

        bundle_dir = workspace_path / ".arcbench-downloads"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = bundle_dir / f"{submission.id}-template.zip"
        if bundle_path.exists():
            bundle_path.unlink()

        try:
            # Downloads use the same complete snapshot writer as stage
            # inheritance. Keep the archive layout stable (`template/...`)
            # while retaining every workspace entry except node_modules.
            from app.services.competition_template_service import CompetitionTemplateService

            CompetitionTemplateService._archive_directory(
                project_dir,
                bundle_path,
                prefix="template",
            )
        except Exception as exc:
            if bundle_path.exists():
                bundle_path.unlink()
            raise RuntimeError(f"Failed to package project directory: {exc}") from exc

        return bundle_path

    @staticmethod
    def _should_exclude_project_bundle_path(path: Path, project_dir: Path) -> bool:
        try:
            relative_path = path.relative_to(project_dir)
        except ValueError:
            return True
        return RunService._should_exclude_project_bundle_relative_path(relative_path)

    @staticmethod
    def _should_exclude_project_bundle_relative_path(relative_path: Path) -> bool:
        return (
            "node_modules" in relative_path.parts
            # The official runner writes this exact project-root report.
            # Nested reports and all agent-authored test/result directories
            # are part of the generated project and remain downloadable.
            or tuple(relative_path.parts) == (".arc", "playwright-report.json")
        )

    @distributed_locked(lambda self, submission, file_path, content: f"submission:{submission.id}")
    def update_workspace_file(self, submission: Submission, file_path: str, content: str) -> None:
        self.db.refresh(submission)
        if not self.can_manual_edit(submission):
            raise ValueError("Workspace edits are only allowed for paused replay submissions")
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if not workspace_path:
            raise FileNotFoundError("Submission workspace is not available")
        project_dir = workspace_path / "template"
        if not project_dir.is_dir():
            raise FileNotFoundError("Project directory is not available")

        normalized_path = self._normalize_relative_path(file_path)
        target_path = (project_dir / normalized_path).resolve()
        project_dir_resolved = project_dir.resolve()

        try:
            target_path.relative_to(project_dir_resolved)
        except ValueError:
            raise FileNotFoundError(f"File path outside project directory: {file_path}")

        target_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_text_atomic(target_path, content)
        self._update_manual_edit_session(submission, has_workspace_changes=True)
        self._append_runner_event(
            submission,
            {
                "type": "workspace_file_updated",
                "file_path": normalized_path.as_posix(),
                "message": normalized_path.as_posix(),
            },
        )
        self._append_runner_refresh_signal(
            submission,
            reason="preview_stale",
            preview=True,
        )

    @distributed_locked(lambda self, submission, *args, **kwargs: f"submission:{submission.id}")
    def create_test_file(self, submission: Submission, test_id: str, req_id: str, test_type: str, scenario_id: Optional[str] = None, file_path: Optional[str] = None) -> str:
        self.db.refresh(submission)
        if not self.can_manual_edit(submission):
            raise ValueError("Test creation is only allowed while a real ARC run is paused")
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if not workspace_path:
            raise FileNotFoundError("Submission workspace is not available")
        project_dir = workspace_path / "template"
        if not project_dir.is_dir():
            raise FileNotFoundError("Project directory is not available")

        if not file_path:
            file_path = f"tests/{test_id}.spec.ts"

        normalized_path = self._normalize_relative_path(file_path)
        target_path = (project_dir / normalized_path).resolve()
        project_dir_resolved = project_dir.resolve()

        try:
            target_path.relative_to(project_dir_resolved)
        except ValueError:
            raise FileNotFoundError(f"File path outside project directory: {file_path}")

        test_template = f"""// Test for {req_id}
// Test ID: {test_id}
// Test Type: {test_type}
"""
        if scenario_id:
            test_template += f"// Scenario ID: {scenario_id}\n"
        test_template += f"""

describe('{test_id}', () => {{
  it('should implement the test', () => {{
    // TODO: Implement the test
  }});
}});
"""

        target_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_text_atomic(target_path, test_template)

        self._register_test_in_traceability(submission, test_id, req_id, test_type, file_path, scenario_id)
        self._update_manual_edit_session(
            submission,
            has_workspace_changes=True,
            has_traceability_changes=True,
            append_test_creation=normalized_path.as_posix(),
        )
        self._append_runner_event(
            submission,
            {
                "type": "manual_test_created",
                "node_id": req_id,
                "file_path": normalized_path.as_posix(),
                "message": test_id,
            },
        )
        self._append_runner_refresh_signal(
            submission,
            reason="traceability_changed",
            traceability_selected=True,
            traceability_all=True,
            preview=True,
            message=test_id,
        )

        return file_path

    def _register_test_in_traceability(self, submission: Submission, test_id: str, req_id: str, test_type: str, file_path: str, scenario_id: Optional[str] = None) -> None:
        workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
        if not workspace_path:
            return
        arc_dir = self.runtime_paths.get_arc_dir_from_workspace(workspace_path)
        tests_path = arc_dir / "traceability" / "tests.json"
        tests = self._read_json_table(tests_path)
        tests[str(test_id or "").strip()] = {
            "test_id": str(test_id or "").strip(),
            "req_id": str(req_id or "").strip(),
            "interface_ids": [],
            "type": str(test_type or "").strip(),
            "file_path": str(file_path or "").strip(),
            "first_line": "1",
            "passed": None,
            "scenario_id": str(scenario_id or "").strip() or None,
        }
        self._write_json_table(tests_path, tests)
        self._notify_traceability_tables_changed(workspace_path)
