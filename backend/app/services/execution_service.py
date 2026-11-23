import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from threading import Event

from docker.errors import DockerException, NotFound
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.enums import AgentSourceType, SubmissionStatus
from app.db.session import SessionLocal
from app.models.requirement import Requirement
from app.models.submission import Submission
from app.models.user import User
from app.services.debug_log_service import DebugLogService
from app.services.docker_manager import DockerManager
from app.services.meter_usage_service import MeterUsageDelta, MeterUsageError, MeterUsageService
from app.services.model_proxy_service import ModelProxyService
from app.services.host_demo_execution_service import HostDemoExecutionService
from app.services.preview_service import PreviewService
from app.services.preview_source_service import PreviewSourceService
from app.services.host_demo_preview_service import HostDemoPreviewService
from app.services.notification_service import NotificationService
from app.services.personal_run_credentials import use_personal_run_credentials
from app.services.result_parser import ResultParser
from app.services.runtime_path_service import RuntimePathService
from app.services.submission_artifact_service import SubmissionArtifactService
from app.services.submission_event_stream import SubmissionEventStream
from app.services.submission_service import ExecutionOwnershipLost, RunService
from app.services.workspace_assembler import WorkspaceAssembler
from app.services.log_sync_service import LogSyncService


PAUSE_GRACE_SECONDS = 3.0
PAUSE_SIGTERM_GRACE_SECONDS = 1.5


def _build_demo_test_report(
    test_root: Path,
    report_path: Path,
    *,
    repaired_scenarios: set[str] | None = None,
    replay_index: int = 24,
) -> dict[str, object]:
    """Create deterministic Stage 3 results without executing the test suite."""
    cases: list[tuple[str, str]] = []
    pattern = re.compile(r"\b(?:test|it)\s*\(\s*(['\"])(.*?)\1", re.DOTALL)
    for path in sorted(test_root.rglob("*.spec.ts")):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in pattern.finditer(source):
            title = " ".join(match.group(2).split())
            if title:
                cases.append((title, path.relative_to(test_root).as_posix()))
    if not cases:
        raise RuntimeError(f"Demo test fixtures are missing: {test_root}")
    if replay_index >= 31:
        cases.append((
            "REQ-2.2: return from booking entry with preserved search context",
            "incremental/REQ-2.2-preserve-search-context.spec.ts",
        ))
    if replay_index >= 34:
        cases.extend([
            ("REQ-3.4: list only the current traveler's orders", "incremental/REQ-3.4-my-orders.spec.ts"),
            ("REQ-3.4: show an empty order history", "incremental/REQ-3.4-my-orders.spec.ts"),
            ("REQ-3.4: require sign in for order history", "incremental/REQ-3.4-my-orders.spec.ts"),
        ])

    # Exactly ten of the thirty ticketbooking cases form the stable teaching
    # baseline. Future repair snapshots clear individual scenario failures.
    failed_scenarios = {
        "REQ-1.1::scenario-3", "REQ-1.1::scenario-6",
        "REQ-1.2::scenario-3", "REQ-1.2::scenario-6",
        "REQ-2.1::scenario-3", "REQ-2.1::scenario-6", "REQ-2.1::scenario-7",
        "REQ-2.2::scenario-2", "REQ-3.1::scenario-3",
        "REQ-3.2::scenario-1",
        "REQ-2.2::scenario-3",
        "REQ-3.4::scenario-1", "REQ-3.4::scenario-2", "REQ-3.4::scenario-3",
    }
    repaired_scenarios = set(repaired_scenarios or set())
    specs: dict[str, list[dict[str, object]]] = {}
    req_scenario_counts: dict[str, int] = {}
    for index, (title, file_path) in enumerate(cases):
        requirement_match = re.search(r"\b(REQ-\d+(?:\.\d+)*)\b", title, re.IGNORECASE)
        req_id = requirement_match.group(1).upper() if requirement_match else None
        scenario_number = 1
        if req_id:
            scenario_number = req_scenario_counts.get(req_id, 0) + 1
            req_scenario_counts[req_id] = scenario_number
        scenario_id = f"{req_id}::scenario-{scenario_number}" if req_id else None
        passed = scenario_id not in failed_scenarios or scenario_id in repaired_scenarios
        specs.setdefault(file_path, []).append(
            {
                "title": title,
                "req_id": req_id,
                "scenario_id": scenario_id,
                "tests": [
                    {
                        "expectedStatus": "passed",
                        "results": [{"status": "passed" if passed else "failed", "duration": 180 + (index % 7) * 23}],
                    }
                ],
            }
        )
    report = {
        "suites": [
            {"file": file_path, "specs": file_specs}
            for file_path, file_specs in specs.items()
        ]
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return ResultParser().parse_playwright_report(report_path)


class ExecutionService:
    def __init__(
        self,
        db: Session,
        *,
        run_id: str,
        attempt_id: str | None,
        generation: int,
        ownership_lost: Event,
        host_demo: bool = False,
    ):
        if host_demo and attempt_id is not None:
            raise ValueError("Host demo execution must not have a distributed attempt")
        if not host_demo and not attempt_id:
            raise ValueError("Distributed execution requires an attempt id")
        self.db = db
        self.settings = get_settings()
        self.assembler = WorkspaceAssembler()
        self.result_parser = ResultParser()
        self.runtime_paths = RuntimePathService()
        self.artifact_service = SubmissionArtifactService()
        self.meter_usage = MeterUsageService(self.settings)
        self.run_id = run_id
        self.attempt_id = attempt_id
        self.generation = generation
        self.ownership_lost = ownership_lost
        self.host_demo = host_demo

    @classmethod
    def for_host_demo(cls, db: Session, *, run_id: str, generation: int) -> "ExecutionService":
        """Build an execution context for a trusted demo without a Celery lease.

        Normal submissions must still provide a claimed attempt and retain all
        distributed ownership checks. Only the dedicated host-demo runner uses
        this constructor, and the source is verified again before execution.
        """
        return cls(
            db,
            run_id=run_id,
            attempt_id=None,
            generation=generation,
            ownership_lost=Event(),
            host_demo=True,
        )

    def _read_agent_execution_duration(self, workspace_path: Path) -> float | None:
        metrics_path = self.runtime_paths.get_arc_dir_from_workspace(workspace_path) / "agent-execution.json"
        try:
            payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        value = payload.get("duration_seconds") if isinstance(payload, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            return None
        return float(value)

    @staticmethod
    def _clear_evaluator_tests(workspace_path: Path) -> None:
        """Ensure an agent always starts without evaluator tests or a stale ready signal.

        A resumed run copies the previous generation's workspace. If that
        generation stopped after evaluator injection but before final cleanup,
        both the private tests and the ready marker can still be present.
        Remove them before any runner container can access the workspace.
        """
        tests_path = workspace_path / "tests"
        if tests_path.is_symlink():
            tests_path.unlink()
        else:
            shutil.rmtree(tests_path, ignore_errors=True)
        tests_path.mkdir(parents=True, exist_ok=True)
        (workspace_path / "template" / ".arc" / "tests-ready").unlink(missing_ok=True)

    def run_submission(self, submission_id: str) -> None:
        self._run_submission_internal(submission_id, reuse_workspace=False)

    def rerun_submission(self, submission_id: str) -> None:
        self._run_submission_internal(submission_id, reuse_workspace=True)

    def _run_submission_internal(self, submission_id: str, *, reuse_workspace: bool) -> None:
        db = SessionLocal()
        try:
            submission_service = RunService(
                db,
                run_id=None if self.host_demo else self.run_id,
                attempt_id=None if self.host_demo else self.attempt_id,
                generation=None if self.host_demo else self.generation,
                ownership_lost=self.ownership_lost,
            )
            submission = submission_service.get_submission(submission_id)
            if self.host_demo and submission.agent_source != AgentSourceType.DEMO_REPLAY.value:
                raise RuntimeError("Host execution is only permitted for demo replay runs")
            agent_submission = db.get(Submission, submission.submission_id)
            user = db.get(User, submission.user_id) if submission.user_id else None
            if not user:
                raise RuntimeError(f"User '{submission.user_id}' not found")
            if agent_submission is None:
                raise RuntimeError(f"Source submission '{submission.submission_id}' not found")
            requirement = db.get(Requirement, submission.requirement_id)
            if not requirement:
                raise RuntimeError(f"Requirement '{submission.requirement_id}' not found")
            self._run(db, submission_service, submission_id, requirement, user, agent_submission, reuse_workspace=reuse_workspace)
        finally:
            db.close()

    def _run(
        self,
        db: Session,
        submission_service: RunService,
        submission_id: str,
        requirement: Requirement,
        user: User,
        agent_submission: Submission,
        *,
        reuse_workspace: bool,
    ) -> None:
        requirement_path = Path(requirement.requirements_path).resolve()
        is_competition_task = requirement_path.is_relative_to(self.settings.competition_root.resolve())
        if requirement.category not in {"web", "cli"} and not is_competition_task:
            raise RuntimeError(f"Unsupported requirement category: {requirement.category}")

        submission = submission_service.get_submission(submission_id)
        workspace_path = (
            self.runtime_paths.get_host_demo_workspace_root(submission, user)
            if self.host_demo
            else self.runtime_paths.get_workspace_root(
                submission,
                username=user.username,
                generation=self.generation,
            )
        )
        runner_kind = "python"
        if agent_submission.agent_source == AgentSourceType.BUILTIN_ARC_AGENT.value:
            start_agent_description = "Continuing built-in ARC agent" if reuse_workspace else "Running built-in ARC agent"
        elif agent_submission.agent_source == AgentSourceType.BUILTIN_OCTOS_AGENT.value:
            # Existing historical records can still be opened safely; new
            # Octos submissions are rejected at creation time above.
            start_agent_description = "Running archived legacy agent"
        else:
            start_agent_description = "Running uploaded agent"
        arc_dir = self.runtime_paths.get_arc_dir_from_workspace(workspace_path)
        shared_log_root = self.runtime_paths.get_shared_log_root(user.id, submission_id, self.generation)
        stdout_path = shared_log_root / "stdout.log"
        stderr_path = shared_log_root / "stderr.log"
        debug_log_path = shared_log_root / "execution.debug.log"
        playwright_report_path = arc_dir / "playwright-report.json"
        debug_log = DebugLogService(workspace_path, debug_log_path)
        log_sync = LogSyncService(
            self.runtime_paths.get_shared_log_root(user.id, submission_id, self.generation),
            max_bytes_per_window=self.settings.log_sync_max_bytes,
            max_bytes_per_run=self.settings.log_max_bytes_per_run,
        )
        last_log_sync_at = 0.0
        last_preview_source_sync_at = 0.0

        container = None
        manager = None
        active_step_key = "deploy_agent"
        completed_steps: set[str] = set()
        processed_runner_event_count = 0
        last_runner_error: str | None = None
        pause_notified = False
        pause_signal_sent = False
        pause_requested_at: float | None = None
        pause_signal_sent_at: float | None = None
        paused = False
        agent_execution_duration: float | None = None
        meter_start_snapshot = None
        effective_api_key = submission.openai_api_key or ""
        effective_base_url = submission.openai_base_url or ""
        model_proxy_session_id = None
        last_runner_signal_at = time.time()
        stuck_notification_sent = False
        evaluator_tests_injected = False

        def emit_event(step_key: str, message: str, status: str = "info") -> None:
            submission_service.append_step_event(submission_id, step_key=step_key, message=message, status=status)
            # Surface lifecycle and agent-stage messages in the user stdout
            # stream. Once evaluation starts, keep the stream quiet so that
            # Playwright's per-test output is never exposed.
            if step_key != "run_tests":
                try:
                    with stdout_path.open("a", encoding="utf-8") as visible_output:
                        visible_output.write(f"[arcbench] {message}\n")
                except (NameError, OSError):
                    # stdout_path is initialized immediately before normal
                    # execution; early setup failures have no visible stream.
                    pass

        def mark_paused(reason: str) -> None:
            nonlocal paused
            paused = True
            submission_service.update_status(
                submission_service.get_submission(submission_id),
                SubmissionStatus.PAUSED,
                failure_reason=reason,
            )

        def import_runner_events() -> list[dict]:
            nonlocal processed_runner_event_count, last_runner_error, last_runner_signal_at, evaluator_tests_injected
            runner_events_path = self.runtime_paths.get_arc_dir_from_workspace(workspace_path) / "runner-events.jsonl"
            if not runner_events_path.exists():
                return []
            imported_events: list[dict] = []
            refresh_flags = {
                "submission": False,
                "logs": False,
                "commit_history": False,
                "traceability_selected": False,
                "traceability_all": False,
                "preview": False,
            }
            lines = runner_events_path.read_text(encoding="utf-8").splitlines()
            new_lines = lines[processed_runner_event_count:]
            processed_runner_event_count = len(lines)
            if new_lines:
                last_runner_signal_at = time.time()
            for raw_line in new_lines:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    debug_log.append("backend", f"Failed to parse runner event line: {line}")
                    continue
                refresh_flags["logs"] = True
                event_type = str(event.get("type", "")).strip()
                if event_type == "requirement_state":
                    refresh_flags["submission"] = True
                elif event_type == "runner_state":
                    refresh_flags["submission"] = True
                elif event_type in {"interface_upsert", "interface_status", "test_upsert"}:
                    refresh_flags["traceability_selected"] = True
                    refresh_flags["traceability_all"] = True
                elif event_type == "signal":
                    refresh_payload = event.get("refresh")
                    if isinstance(refresh_payload, dict):
                        for key in refresh_flags:
                            if bool(refresh_payload.get(key)):
                                refresh_flags[key] = True
                step_key = str(event.get("step_key", "")).strip()
                message = submission_service.format_runner_event_log_line(event)
                status = str(event.get("status", "info")).strip() or "info"
                if status == "error" and message:
                    last_runner_error = message
                if step_key in {"deploy_agent", "start_agent", "run_tests"} and message:
                    imported_events.append({"step_key": step_key, "message": message, "status": status})
                    if (
                        step_key == "run_tests"
                        and status == "waiting"
                        and message == "Waiting for evaluator tests"
                        and not evaluator_tests_injected
                    ):
                        tests_source = Path(requirement.tests_path).resolve()
                        tests_target = workspace_path / "tests"
                        if not tests_source.is_dir():
                            raise RuntimeError(f"Evaluator tests directory is missing: {tests_source}")
                        shutil.rmtree(tests_target, ignore_errors=True)
                        shutil.copytree(tests_source, tests_target)
                        (workspace_path / "template" / ".arc" / "tests-ready").touch()
                        evaluator_tests_injected = True
                        debug_log.append(
                            "backend",
                            f"Injected evaluator tests after agent completion: {tests_source} -> {tests_target}",
                        )

            if refresh_flags["preview"]:
                PreviewService(db).mark_stale(submission_id, workspace_path / "template")
                db.commit()
            if refresh_flags["traceability_selected"] or refresh_flags["traceability_all"]:
                snapshot_updated = self.artifact_service.refresh_traceability_snapshot_for_workspace(
                    workspace_path,
                    force=True,
                )
                if not snapshot_updated:
                    debug_log.append(
                        "backend",
                        "Traceability refresh was requested, but .arc/traceability is not available yet",
                    )
            if any(refresh_flags.values()):
                SubmissionEventStream.publish(
                    submission_id,
                    reason="runner_events",
                    submission=refresh_flags["submission"],
                    logs=refresh_flags["logs"],
                    commit_history=refresh_flags["commit_history"],
                    traceability_selected=refresh_flags["traceability_selected"],
                    traceability_all=refresh_flags["traceability_all"],
                    preview=refresh_flags["preview"],
                )
            return imported_events

        def refresh_running_steps(latest_events: list[dict]) -> None:
            nonlocal active_step_key, completed_steps
            if not latest_events:
                return

            event_step_keys = {str(event.get("step_key", "")).strip() for event in latest_events}

            if "run_tests" in event_step_keys:
                completed_steps = {"deploy_agent", "start_agent"}
                active_step_key = "run_tests"
                description = "Preparing and running tests"
            elif "start_agent" in event_step_keys:
                completed_steps = {"deploy_agent"}
                active_step_key = "start_agent"
                description = start_agent_description
            else:
                completed_steps = set()
                active_step_key = "deploy_agent"
                description = "Preparing workspace"
            current_steps = submission_service.build_step_states(
                active_key=active_step_key,
                completed=completed_steps,
                description=description,
            )
            current_steps = submission_service.attach_step_logs(
                current_steps,
                submission_service.read_events(submission_service.get_submission(submission_id)),
            )
            submission_service.update_steps(
                submission_service.get_submission(submission_id),
                current_steps,
            )

        try:
            use_personal_run_credentials(submission, agent_submission)
            effective_api_key = submission.openai_api_key or ""
            effective_base_url = submission.openai_base_url or ""
            db.commit()
            if reuse_workspace:
                existing_workspace_path = self.runtime_paths.resolve_existing_path(submission.workspace_path)
                if existing_workspace_path is None:
                    raise RuntimeError("Submission workspace is not available for rewind resume")
                if self.host_demo:
                    # Demo continuation is a pointer move over immutable
                    # snapshots and intentionally keeps one stable workspace.
                    workspace_path = existing_workspace_path
                else:
                    if existing_workspace_path == workspace_path:
                        raise RuntimeError("Previous and current generation workspaces must be distinct")
                    self.ownership_lost.is_set() and submission_service._assert_execution_owner()
                    shutil.rmtree(workspace_path, ignore_errors=True)
                    workspace_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(existing_workspace_path, workspace_path, symlinks=True)
                    submission_service._assert_execution_owner()
                debug_log = DebugLogService(workspace_path, debug_log_path)
                debug_log.append("backend", f"Execution started for submission {submission_id}")
                debug_log.append("backend", f"Requirement category: {requirement.category}")
                if agent_submission.agent_source != AgentSourceType.DEMO_REPLAY.value:
                    manager = DockerManager()
                    manager.remove_submission_container(submission_id, self.generation)
                runner_events_path = self.runtime_paths.get_arc_dir_from_workspace(workspace_path) / "runner-events.jsonl"
                if runner_events_path.exists():
                    processed_runner_event_count = len(runner_events_path.read_text(encoding="utf-8").splitlines())
                emit_event("deploy_agent", "Reusing existing workspace")
                debug_log.append("backend", f"Reusing existing workspace at {workspace_path}")
                emit_event("deploy_agent", "Existing workspace is ready", status="success")
            else:
                emit_event("deploy_agent", "Preparing workspace")
                workspace_path = self.assembler.assemble(
                    submission,
                    agent_submission,
                    requirement,
                    user,
                    generation=self.generation,
                    workspace_root=workspace_path if self.host_demo else None,
                )
                debug_log = DebugLogService(workspace_path, debug_log_path)
                debug_log.append("backend", f"Execution started for submission {submission_id}")
                debug_log.append("backend", f"Requirement category: {requirement.category}")
                debug_log.append("backend", f"Workspace assembled at {workspace_path}")
                emit_event("deploy_agent", "Workspace assembled", status="success")
            self._clear_evaluator_tests(workspace_path)
            debug_log.append("backend", "Evaluator tests and ready marker cleared before agent execution")
            arc_dir = self.runtime_paths.get_arc_dir_from_workspace(workspace_path)
            shared_log_root = self.runtime_paths.get_shared_log_root(user.id, submission_id, self.generation)
            stdout_path = shared_log_root / "stdout.log"
            stderr_path = shared_log_root / "stderr.log"
            shared_log_root.mkdir(parents=True, exist_ok=True)
            playwright_report_path = arc_dir / "playwright-report.json"
            submission_service.mark_running(submission, workspace_path)
            if submission.cost_source == "model_proxy" and not self.settings.model_proxy_enabled:
                raise RuntimeError("Re-enable the model proxy to resume this metered Run")
            if (self.settings.model_proxy_enabled and submission.billing_mode == "self_funded"
                    and agent_submission.agent_source != AgentSourceType.DEMO_REPLAY.value):
                model_proxy_session_id, effective_api_key, effective_base_url = ModelProxyService(self.settings).issue(
                    submission.id, self.attempt_id, self.generation,
                )
                db.expire_all()
                debug_log.append("backend", "Website model proxy activated; per-request standard cost accounting enabled")
            if self.meter_usage.enabled and model_proxy_session_id is None:
                try:
                    meter_start_snapshot = self.meter_usage.capture(effective_api_key)
                    if meter_start_snapshot is not None:
                        debug_log.append(
                            "backend",
                            f"Meter baseline captured for access key {meter_start_snapshot.access_key_id}",
                        )
                        emit_event("deploy_agent", "Meter usage baseline captured", status="success")
                except MeterUsageError as exc:
                    # Metering must never discard an otherwise valid evaluation.
                    # A missing metric remains visible as unavailable on the leaderboard.
                    debug_log.append("backend", f"Meter baseline unavailable: {exc}")
                    emit_event("deploy_agent", "Meter usage is temporarily unavailable", status="info")
            PreviewService(db).mark_stale(submission.id, workspace_path / "template")
            last_preview_source_sync_at = time.monotonic()
            db.commit()
            submission_service.update_steps(
                submission,
                submission_service.build_step_states(
                    active_key="deploy_agent",
                    description="Reusing existing workspace" if reuse_workspace else "Preparing workspace",
                ),
            )

            # The deterministic demo is an animation/replay experience, not
            # an untrusted submission evaluation. Run it directly on the host
            # from this user's isolated workspace and do not create a Docker
            # runner or execute Playwright scoring for it.
            if agent_submission.agent_source == AgentSourceType.DEMO_REPLAY.value:
                emit_event("deploy_agent", "Demo workspace ready", status="success")
                submission_service.update_steps(
                    submission_service.get_submission(submission_id),
                    submission_service.build_step_states(
                        active_key="start_agent",
                        completed={"deploy_agent"},
                        description="Playing demo replay on host",
                    ),
                )
                emit_event("start_agent", "Playing demo replay on host")
                demo_root = workspace_path / "submission"
                demo_script = demo_root / "main.py"
                snapshot_dir = demo_root / "snapshots" / "ticketbooking"
                if not demo_script.is_file():
                    raise RuntimeError(f"Demo replay script is missing: {demo_script}")
                if not snapshot_dir.is_dir():
                    raise RuntimeError(f"Demo snapshot store is missing: {snapshot_dir}")
                command = [
                    sys.executable,
                    str(demo_script),
                    str(workspace_path / "template" / "requirements"),
                    "--output-dir",
                    str(workspace_path / "template"),
                    "--arc-dir",
                    str(arc_dir),
                    "--submission-dir",
                    str(demo_root),
                ]
                env = {
                    **os.environ,
                    "ARCBENCH_DEMO_SNAPSHOT_DIR": str(snapshot_dir),
                    "ARCBENCH_TEMPLATE_DIR": str(workspace_path / "template"),
                    "ARCBENCH_PROJECT_DIR": str(workspace_path / "template"),
                    "ARCBENCH_RUNNER_EVENTS_PATH": str(arc_dir / "runner-events.jsonl"),
                    "ARCBENCH_TRACEABILITY_DIR": str(arc_dir / "traceability"),
                    "ARCBENCH_CHECKPOINT_PATH": str(arc_dir / "checkpoint.json"),
                    "PYTHONPATH": os.pathsep.join(
                        item for item in (str(demo_root), os.environ.get("PYTHONPATH", "")) if item
                    ),
                }
                debug_log.append("backend", f"Starting host demo replay: {' '.join(command)}")
                with stdout_path.open("a", encoding="utf-8") as output:
                    process = subprocess.Popen(
                        command,
                        cwd=str(demo_root),
                        env=env,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                    HostDemoExecutionService.register(submission_id, process)
                    active_step_key = "start_agent"
                    completed_steps = {"deploy_agent"}
                    last_demo_checkpoint_index = int(
                        submission_service.read_checkpoint(
                            submission_service.get_submission(submission_id)
                        ).get("last_completed_index", 0)
                        or 0
                    )
                    while process.poll() is None:
                        latest_events = import_runner_events()
                        if latest_events:
                            refresh_running_steps(latest_events)
                        current_demo_checkpoint = submission_service.read_checkpoint(
                            submission_service.get_submission(submission_id)
                        )
                        current_demo_index = int(current_demo_checkpoint.get("last_completed_index", 0) or 0)
                        if current_demo_index != last_demo_checkpoint_index:
                            last_demo_checkpoint_index = current_demo_index
                            SubmissionEventStream.publish(
                                submission_id,
                                reason="demo_snapshot_advanced",
                                submission=True,
                                commit_history=True,
                                traceability_selected=True,
                                traceability_all=True,
                                preview=True,
                            )
                        if submission_service.get_submission(submission_id).status == SubmissionStatus.CANCELLED.value:
                            process.terminate()
                            process.wait(timeout=10)
                            return
                        if submission_service.get_submission(submission_id).status in {
                            SubmissionStatus.PAUSE_REQUESTED.value,
                            SubmissionStatus.PAUSED.value,
                        }:
                            HostDemoExecutionService.stop(submission_id)
                            return
                        time.sleep(0.2)
                    HostDemoExecutionService.unregister(submission_id)
                    latest_events = import_runner_events()
                    if latest_events:
                        refresh_running_steps(latest_events)
                    if process.returncode != 0:
                        raise RuntimeError(f"Host demo replay exited with code {process.returncode}")
                    replay_checkpoint = submission_service.read_checkpoint(
                        submission_service.get_submission(submission_id)
                    )
                    # A short replay (especially a TDD repair from step 24 to
                    # 25/26) can write its final checkpoint and exit between
                    # two polling iterations.  Publish the final pointer
                    # transition explicitly so clients refresh history and
                    # preview even when no in-flight poll observed it.
                    final_demo_index = int(replay_checkpoint.get("last_completed_index", 0) or 0)
                    if final_demo_index != last_demo_checkpoint_index:
                        last_demo_checkpoint_index = final_demo_index
                        SubmissionEventStream.publish(
                            submission_id,
                            reason="demo_snapshot_advanced",
                            submission=True,
                            commit_history=True,
                            traceability_selected=True,
                            traceability_all=True,
                            preview=True,
                        )
                if bool(replay_checkpoint.get("paused")):
                        paused_submission = submission_service.get_submission(submission_id)
                        submission_service.update_status(
                            paused_submission,
                            SubmissionStatus.PAUSED,
                            failure_reason="Execution paused by user request",
                        )
                        debug_log.append(
                            "backend",
                            f"Host demo replay paused at snapshot index {replay_checkpoint.get('last_completed_index', 0)}",
                        )
                        return
                submission_service.update_steps(
                    submission_service.get_submission(submission_id),
                    submission_service.build_step_states(
                        active_key="run_tests",
                        completed={"deploy_agent", "start_agent"},
                        description="Evaluating demo test results",
                    ),
                )
                emit_event("run_tests", "Evaluating demo test results...", status="running")
                debug_log.append("backend", "Demo evaluation delay started (15 seconds)")
                evaluation_deadline = time.monotonic() + 15.0
                while time.monotonic() < evaluation_deadline:
                    current_status = submission_service.get_submission(submission_id).status
                    if current_status == SubmissionStatus.CANCELLED.value:
                        return
                    time.sleep(min(0.2, max(0.0, evaluation_deadline - time.monotonic())))
                # Teaching snapshots intentionally turn on one repaired
                # external scenario at a time: step 28 demonstrates the
                # missing same-city unit coverage repair, while step 30
                # demonstrates the corrected passenger-ID contract.
                replay_index = int(replay_checkpoint.get("last_completed_index", 0) or 0)
                repaired_scenarios: set[str] = set()
                if replay_index >= 28:
                    repaired_scenarios.add("REQ-2.1::scenario-7")
                if replay_index >= 30:
                    repaired_scenarios.add("REQ-3.2::scenario-1")
                if replay_index >= 33:
                    repaired_scenarios.add("REQ-2.2::scenario-3")
                if replay_index >= 35:
                    repaired_scenarios.update({
                        "REQ-3.4::scenario-1",
                        "REQ-3.4::scenario-2",
                        "REQ-3.4::scenario-3",
                    })
                demo_results = _build_demo_test_report(
                    snapshot_dir / "tests",
                    playwright_report_path,
                    repaired_scenarios=repaired_scenarios,
                    replay_index=replay_index,
                )
                demo_score = float(demo_results["score"])
                demo_passed = int(demo_results["passed"])
                demo_failed = int(demo_results["failed"])
                demo_total = demo_passed + demo_failed
                demo_features = demo_results.get("feature_total_count", 0)
                submission_service.update_steps(
                    submission_service.get_submission(submission_id),
                    submission_service.build_step_states(
                        completed={"deploy_agent", "start_agent", "run_tests"},
                        description="Demo replay completed",
                    ),
                )
                demo_status = SubmissionStatus.PASSED if demo_failed == 0 else SubmissionStatus.FAILED
                emit_event(
                    "run_tests",
                    "Demo replay completed; evaluation is not applicable" if demo_status == SubmissionStatus.PASSED else "Demo replay completed with demonstration test failures",
                    status="success" if demo_status == SubmissionStatus.PASSED else "error",
                )
                submission_service.update_status(
                    submission_service.get_submission(submission_id),
                    demo_status,
                    failure_reason=None if demo_status == SubmissionStatus.PASSED else "Demonstration external tests include intentional failures",
                )
                completed_submission = submission_service.get_submission(submission_id)
                completed_submission.finished_at = datetime.utcnow()
                completed_submission.score = demo_score
                completed_submission.test_pass_rate = demo_score
                completed_submission.passed_count = demo_passed
                completed_submission.failed_count = demo_failed
                completed_submission.feature_total_count = int(demo_features or 0)
                completed_submission.feature_implemented_count = int(demo_results.get("feature_implemented_count", 0) or 0)
                completed_submission.feature_implementation_rate = float(demo_results.get("feature_implementation_rate", 0.0) or 0.0)
                submission_service.db.add(completed_submission)
                submission_service.db.commit()
                debug_log.append(
                    "backend",
                    f"Host demo replay completed with fixed results: passed={demo_passed}, failed={demo_failed}, total={demo_total}",
                )
                return

            if submission_service.get_submission(submission_id).status == SubmissionStatus.CANCELLED.value:
                debug_log.append("backend", "Run was cancelled before the runner container was created")
                return

            emit_event("deploy_agent", "Connecting to Docker daemon")
            debug_log.append("backend", "Connecting to Docker daemon")
            if manager is None:
                manager = DockerManager()
            debug_log.append("backend", "Docker daemon ping succeeded")
            emit_event("deploy_agent", "Docker daemon is reachable", status="success")
            submission = submission_service.get_submission(submission_id)
            submission_service.update_steps(
                submission,
                submission_service.build_step_states(active_key="deploy_agent", description="Creating runner container"),
            )
            emit_event("deploy_agent", "Preparing runner image")
            debug_log.append("backend", f"Ensuring runner image is available: {self.settings.runner_image}")
            manager.remove_submission_container(submission_id, self.generation)
            container = manager.create_container(
                submission.id,
                workspace_path,
                openai_api_key=effective_api_key,
                openai_base_url=effective_base_url,
                model=submission.model_name or "",
                visual_model=submission.visual_model,
                github_email=user.github_email,
                github_username=user.github_username,
                runner_kind=runner_kind,
                generation=self.generation,
                model_proxy=model_proxy_session_id is not None,
                log_callback=lambda line: debug_log.append("docker-build", line),
            )
            debug_log.append("backend", f"Container created: name={container.name}, id={container.id}")
            emit_event("deploy_agent", f"Runner container created ({container.name})", status="success")
            manager.start_container(container)
            debug_log.append("backend", "Container start requested")
            emit_event("deploy_agent", "Runner container started", status="success")
            if reuse_workspace:
                submission_service.clear_checkpoint_restart_flag(submission_service.get_submission(submission_id))
            completed_steps = {"deploy_agent"}
            active_step_key = "start_agent"

            submission = submission_service.get_submission(submission_id)
            submission_service.update_steps(
                submission,
                submission_service.build_step_states(
                    active_key="start_agent",
                    completed={"deploy_agent"},
                    description=start_agent_description,
                ),
            )
            emit_event("start_agent", start_agent_description)
            runner_timeout_seconds = int(self.settings.runner_timeout_seconds)
            wait_deadline = None if runner_timeout_seconds <= 0 else time.time() + runner_timeout_seconds + 30
            if wait_deadline is None:
                debug_log.append("backend", "Waiting for container to exit without timeout")
            else:
                debug_log.append("backend", f"Waiting for container to exit with timeout={runner_timeout_seconds + 30}s")
            exit_result = None
            while wait_deadline is None or time.time() < wait_deadline:
                latest_events = import_runner_events()
                if latest_events:
                    refresh_running_steps(latest_events)
                current_status = submission_service.get_submission(submission_id).status
                if time.time() - last_log_sync_at >= self.settings.log_sync_interval_seconds:
                    live_stdout, live_stderr = manager.collect_logs(container)
                    # The runner keeps user-agent/application output in the
                    # mounted workspace. Playwright output is intentionally
                    # not written there, so prefer this file when present;
                    # otherwise retain the Docker-log fallback for older
                    # runner images.
                    runner_stdout_path = arc_dir / "stdout.log"
                    if runner_stdout_path.is_file():
                        try:
                            live_stdout = runner_stdout_path.read_text(encoding="utf-8", errors="replace")
                            live_stderr = ""
                        except OSError as exc:
                            debug_log.append("backend", f"Runner stdout snapshot read failed: {exc}")
                    discarded = log_sync.sync("stdout", live_stdout) + log_sync.sync("stderr", live_stderr)
                    if discarded:
                        debug_log.append("backend", f"Log output capped; discarded_bytes={discarded}")
                    last_log_sync_at = time.time()
                if time.monotonic() - last_preview_source_sync_at >= self.settings.preview_source_sync_interval_seconds:
                    try:
                        preview_sources = PreviewSourceService()
                        try:
                            previous_snapshot, _digest, _revision = preview_sources.current(user.id, submission_id)
                        except (FileNotFoundError, RuntimeError):
                            previous_snapshot = None
                        PreviewService(db).publish_source(
                            submission_service.get_submission(submission_id),
                            workspace_path / "template",
                        )
                        db.commit()
                        current_snapshot, _digest, _revision = preview_sources.current(user.id, submission_id)
                        if current_snapshot != previous_snapshot:
                            SubmissionEventStream.publish(
                                submission_id,
                                reason="preview_source_synced",
                                traceability_selected=True,
                                traceability_all=True,
                                preview=True,
                            )
                    except Exception as exc:  # noqa: BLE001
                        db.rollback()
                        debug_log.append("backend", f"Periodic preview source sync failed: {exc}")
                    last_preview_source_sync_at = time.monotonic()
                if not stuck_notification_sent and time.time() - last_runner_signal_at >= 300:
                    NotificationService(db).create_once(
                        user_id=user.id,
                        run_id=submission_id,
                        kind="no_progress",
                        title="Run may be stuck",
                        body="No runner progress or heartbeat was recorded for five minutes. Open the run to refresh its logs or cancel it.",
                    )
                    stuck_notification_sent = True
                if current_status == SubmissionStatus.CANCELLED.value:
                    debug_log.append("backend", "Run was cancelled; stopping execution loop")
                    return
                if current_status in {SubmissionStatus.PAUSE_REQUESTED.value, SubmissionStatus.PAUSED.value}:
                    # Do not wait for ARC to flush a checkpoint. A task left
                    # RUNNING is recovered by ARC on the next --resume pass.
                    if current_status == SubmissionStatus.PAUSE_REQUESTED.value:
                        emit_event("start_agent", "Pause requested; stopping runner immediately", status="info")
                        try:
                            paused_submission = submission_service.get_submission(submission_id)
                            submission_service.set_checkpoint_restart_flag(paused_submission)
                            if manager is not None:
                                manager.remove_submission_container(submission_id, self.generation)
                                container = None
                            mark_paused("Execution paused by user request")
                            submission_service.mark_paused_for_manual_edit(
                                submission_service.get_submission(submission_id),
                                reason="Execution paused; workspace is ready for manual edits",
                            )
                            SubmissionEventStream.publish(
                                submission_id,
                                reason="pause_ready_for_manual_edit",
                                submission=True,
                                logs=True,
                                traceability_selected=True,
                                traceability_all=True,
                                preview=True,
                            )
                        except Exception as exc:  # noqa: BLE001
                            debug_log.append("backend", f"Failed to persist immediate pause state: {exc}")
                    try:
                        if manager is not None and container is not None:
                            manager.remove_submission_container(submission_id, self.generation)
                            container = None
                    except Exception as exc:  # noqa: BLE001
                        debug_log.append("backend", f"Failed to remove paused runner container: {exc}")
                    debug_log.append("backend", "Execution paused immediately; terminating runner session")
                    return
                try:
                    container.reload()
                except NotFound as exc:
                    if submission_service.checkpoint_requires_restart(submission_service.get_submission(submission_id)):
                        debug_log.append("backend", "Runner container removed for rewind restart")
                        return
                    raise RuntimeError("Runner container disappeared before completion") from exc
                if container.status == "exited":
                    exit_result = container.wait(timeout=5)
                    break
                time.sleep(1)
            if exit_result is None:
                raise TimeoutError("Runner did not finish before timeout")
            if submission_service.get_submission(submission_id).status == SubmissionStatus.CANCELLED.value:
                debug_log.append("backend", "Run was cancelled after the runner exited")
                return
            if paused or submission_service.get_submission(submission_id).status == SubmissionStatus.PAUSED.value:
                debug_log.append("backend", "Skipping test execution because submission is paused")
                return
            debug_log.append("backend", f"Container exited with result={exit_result}")
            latest_events = import_runner_events()
            if latest_events:
                refresh_running_steps(latest_events)
            checkpoint = submission_service.read_checkpoint(submission_service.get_submission(submission_id))
            if bool(checkpoint.get("resume_patch_conflict")):
                conflict_message = "Replay patch merge conflict detected. Resolve the workspace manually, then resume again."
                paused_submission = submission_service.get_submission(submission_id)
                submission_service.update_status(
                    paused_submission,
                    SubmissionStatus.PAUSED,
                    failure_reason=conflict_message,
                )
                submission_service.mark_resume_patch_conflict(paused_submission, message=conflict_message)
                SubmissionEventStream.publish(
                    submission_id,
                    reason="resume_patch_conflict",
                    submission=True,
                    logs=True,
                    commit_history=True,
                    traceability_selected=True,
                    traceability_all=True,
                    preview=True,
                )
                debug_log.append("backend", conflict_message)
                return
            emit_event("run_tests", "Collecting test artifacts")
            stdout, stderr = manager.collect_logs(container)
            runner_stdout_path = arc_dir / "stdout.log"
            if runner_stdout_path.is_file():
                try:
                    stdout = runner_stdout_path.read_text(encoding="utf-8", errors="replace")
                    stderr = ""
                except OSError as exc:
                    debug_log.append("backend", f"Runner stdout snapshot read failed: {exc}")
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            log_sync.sync("stdout", stdout)
            log_sync.sync("stderr", stderr)
            # Keep the final paths compatible with the existing API while the
            # live snapshots remain bounded on the shared filesystem.
            for path, stream in ((stdout_path, "stdout"), (stderr_path, "stderr")):
                path.write_bytes(bytes(log_sync.published[stream]))
            debug_log.append("backend", f"Collected container logs: stdout_bytes={len(stdout.encode('utf-8'))}, stderr_bytes={len(stderr.encode('utf-8'))}")
            emit_event("run_tests", "Test artifacts collected", status="success")

            submission = submission_service.get_submission(submission_id)
            submission_service.update_steps(
                submission,
                submission_service.build_step_states(
                    active_key="run_tests",
                    completed={"deploy_agent", "start_agent"},
                    description="Preparing and running tests",
                ),
            )

            agent_execution_duration = self._read_agent_execution_duration(workspace_path)
            parsed = self.result_parser.parse_playwright_report(playwright_report_path)
            total_tests = parsed["passed"] + parsed["failed"]
            if total_tests <= 0:
                if last_runner_error:
                    raise RuntimeError(last_runner_error)
                raise RuntimeError("Runner did not produce any executed Playwright tests")
            debug_log.append("backend", f"Parsed Playwright report at {playwright_report_path}: {parsed}")
            emit_event(
                "run_tests",
                f"Test results parsed: passed={parsed['passed']}, failed={parsed['failed']}, score={parsed['score']}",
                status="success",
            )
            # A valid Playwright report is a completed evaluation even when
            # individual assertions failed. Playwright intentionally exits
            # with code 1 for those assertion failures; treating that code as
            # a run failure discarded otherwise valid partial scores from the
            # leaderboard. Runtime/setup failures still fail earlier when no
            # usable report is produced (the total_tests <= 0 guard above).
            status = SubmissionStatus.PASSED
            failure_reason = None
            if parsed["failed"]:
                emit_event(
                    "run_tests",
                    f"Submission evaluated with {parsed['failed']} failed test(s)",
                    status="success",
                )
            else:
                emit_event("run_tests", "Submission finished successfully", status="success")
            submission_service.update_steps(
                submission_service.get_submission(submission_id),
                submission_service.build_step_states(completed={"deploy_agent", "start_agent", "run_tests"}),
            )
            meter_delta = self._capture_meter_delta(
                meter_start_snapshot,
                effective_api_key,
                debug_log,
            )
            try:
                PreviewService(db).publish_source(
                    submission_service.get_submission(submission_id),
                    workspace_path / "template",
                )
            except Exception as exc:  # noqa: BLE001
                debug_log.append("backend", f"Final preview source sync failed: {exc}")
            submission_service.finalize(
                submission_service.get_submission(submission_id),
                status=status,
                passed_count=parsed["passed"],
                failed_count=parsed["failed"],
                score=parsed["score"],
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                result_path=None,
                failure_reason=failure_reason,
                test_pass_rate=parsed["test_pass_rate"],
                feature_implemented_count=parsed["feature_implemented_count"],
                feature_total_count=parsed["feature_total_count"],
                feature_implementation_rate=parsed["feature_implementation_rate"],
                run_duration_seconds=agent_execution_duration,
                token_count=meter_delta.token_count if meter_delta else None,
                token_cost=float(meter_delta.cost) if meter_delta else None,
                token_cost_currency=meter_delta.currency if meter_delta else None,
            )
            debug_log.append("backend", f"Submission finalized with status={status.value}, score={parsed['score']}")
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, ExecutionOwnershipLost):
                raise
            submission = submission_service.get_submission(submission_id)
            if submission.status == SubmissionStatus.CANCELLED.value:
                debug_log.append("backend", "Skipping failure finalization for cancelled run")
                return
            if str(exc) == "Execution paused by user request":
                debug_log.append("backend", "Execution paused cleanly")
                return
            emit_event(active_step_key, str(exc), status="error")
            debug_log.append("backend", f"Execution failed during {active_step_key}: {exc}")
            try:
                PreviewService(db).publish_source(submission, workspace_path / "template")
            except Exception as snapshot_exc:  # noqa: BLE001
                debug_log.append("backend", f"Failure preview source sync failed: {snapshot_exc}")
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            log_sync.sync("stdout", str(exc))
            submission_service.update_steps(
                submission,
                submission_service.build_failed_step_states(
                    failed_key=active_step_key,
                    reason=str(exc),
                    completed=completed_steps,
                ),
            )
            meter_delta = self._capture_meter_delta(
                meter_start_snapshot,
                effective_api_key,
                debug_log,
            )
            submission_service.finalize(
                submission,
                status=SubmissionStatus.FAILED,
                passed_count=0,
                failed_count=0,
                score=0.0,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                result_path=None,
                failure_reason=str(exc),
                run_duration_seconds=agent_execution_duration or self._read_agent_execution_duration(workspace_path),
                token_count=meter_delta.token_count if meter_delta else None,
                token_cost=float(meter_delta.cost) if meter_delta else None,
                token_cost_currency=meter_delta.currency if meter_delta else None,
            )
            debug_log.append("backend", "Failure state persisted to database")
        finally:
            HostDemoExecutionService.unregister(submission_id)
            if manager is not None and container is not None:
                try:
                    debug_log.append("backend", f"Removing container {container.name}")
                    manager.remove_container(container)
                    debug_log.append("backend", "Container removed")
                except DockerException:
                    debug_log.append("backend", "Container removal failed with DockerException")
                    pass
            # The evaluator suite is transient and must never remain in the
            # user-visible run workspace or downloadable artifacts.
            try:
                shutil.rmtree(workspace_path / "tests", ignore_errors=True)
                (workspace_path / "template" / ".arc" / "tests-ready").unlink(missing_ok=True)
            except OSError as cleanup_exc:
                debug_log.append("backend", f"Evaluator test cleanup failed: {cleanup_exc}")
            if model_proxy_session_id is not None:
                try:
                    ModelProxyService(self.settings).revoke(model_proxy_session_id)
                except Exception:
                    debug_log.append("backend", "Model proxy close failed; inspect per-request usage ledger")

    def _capture_meter_delta(
        self,
        start_snapshot,
        api_key: str | None,
        debug_log: DebugLogService,
    ) -> MeterUsageDelta | None:
        if start_snapshot is None:
            return None
        try:
            end_snapshot = self.meter_usage.capture(
                api_key,
                wait_for_settlement=True,
                query_start=start_snapshot.query_start,
            )
            delta = self.meter_usage.delta(start_snapshot, end_snapshot)
            if delta is not None:
                debug_log.append(
                    "backend",
                    f"Meter usage captured: tokens={delta.token_count}, cost={delta.cost} {delta.currency}",
                )
            return delta
        except MeterUsageError as exc:
            debug_log.append("backend", f"Final meter usage unavailable: {exc}")
            return None
