import asyncio
import json
import mimetypes
from datetime import datetime
import logging
from queue import Empty
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import require_current_user
from app.api.competition_freeze import enforce_competition_freeze
from app.core.enums import AgentSourceType, RuntimeType, SubmissionStatus
from app.db.session import get_db
from app.models.user import User
from app.schemas.submission import (
    SubmissionCommitHistoryPayload,
    BulkDeleteResult,
    SubmissionCreateResponse,
    BulkDeletePayload,
    SubmissionEditableTaskPayload,
    SubmissionDetail,
    SubmissionLogs,
    SubmissionPreviewStatus,
    SubmissionRewindPayload,
    SubmissionTddRerunPayload,
    SubmissionSourcePayload,
    SubmissionSummary,
    SubmissionTraceabilityPayload,
    SubmissionManualEditCommitPreview,
    SubmissionRerunResponse,
    RunSummary,
    WorkspaceFileListPayload,
    FileUpdatePayload,
    TestCreatePayload,
    TestCreateResponse,
)
from app.services.preview_service import PreviewService
from app.services.lock_service import hold_distributed_lock
from app.services.host_demo_execution_service import HostDemoExecutionService
from app.services.host_demo_preview_service import HostDemoPreviewService
from app.services.runtime_path_service import RuntimePathService
from app.services.requirement_catalog import RequirementCatalogService
from app.services.submission_artifact_service import SubmissionArtifactService
from app.services.submission_event_stream import SubmissionEventStream
from app.services.submission_service import RunService
from app.services.agent_submission_service import AgentSubmissionService
from app.worker.outbox import (
    CompetitionRunAlreadyActive,
    GlobalRunConcurrencyLimitExceeded,
    enforce_official_competition_task_limit,
    queue_run,
)
from app.worker.celery_app import celery_app


submission_router = APIRouter(
    prefix="/submissions", tags=["submissions"],
    dependencies=[Depends(enforce_competition_freeze)],
)
run_router = APIRouter(
    prefix="/runs", tags=["runs"],
    dependencies=[Depends(enforce_competition_freeze)],
)
# Remaining detail, logs, workspace, and lifecycle endpoints are all Run
# endpoints. Keep this local alias while their function names are migrated.
router = run_router
SubmissionService = RunService
runtime_paths = RuntimePathService()
artifact_service = SubmissionArtifactService()
logger = logging.getLogger(__name__)


def _require_official_run_leader(db: Session, current_user: User, run) -> None:
    """Guard class team and official evaluation run mutations centrally."""
    try:
        RunService(db)._require_official_run_leader(current_user.id, run)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _require_official_run_member(db: Session, current_user: User, run) -> None:
    """Allow owning official members; class runs remain leader-only."""
    try:
        RunService(db)._require_official_run_member(current_user.id, run)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@submission_router.get("", response_model=list[SubmissionSummary])
def list_submissions(
    requirement_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> list[SubmissionSummary]:
    return [SubmissionSummary.model_validate(item, from_attributes=True) for item in AgentSubmissionService(db).list(current_user.id, requirement_id=requirement_id)]


@submission_router.post("", response_model=SubmissionCreateResponse)
def create_submission(
    requirement_id: str | None = Form(None),
    competition_id: str | None = Form(None),
    runtime: RuntimeType = Form(...),
    catalog: str = Form(default="playground"),
    display_name: str | None = Form(None),
    base_url: str | None = Form(None),
    api_key: str | None = Form(None),
    model: str | None = Form(None),
    visual_model: str | None = Form(None),
    # Legacy model hints are optional; new clients select models in agent code.
    model_name: str | None = Form(None),
    task_type: str | None = Form(None),
    agent_source: AgentSourceType = Form(AgentSourceType.UPLOAD),
    credential_mode: str = Form("self_funded"),
    template_selections: str | None = Form(None),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionCreateResponse:
    try:
        parsed_template_selections = json.loads(template_selections) if template_selections else None
        if parsed_template_selections is not None and not isinstance(parsed_template_selections, dict):
            raise ValueError("template_selections must be an object")
        submission = AgentSubmissionService(db).create(
            user_id=current_user.id,
            requirement_id=requirement_id,
            competition_id=competition_id,
            runtime=runtime,
            upload=file,
            catalog=catalog,
            display_name=display_name,
            base_url=base_url,
            api_key=api_key,
            model=model or model_name,
            visual_model=visual_model,
            task_type=task_type,
            agent_source=agent_source,
            credential_mode=credential_mode,
            template_selections=parsed_template_selections,
        )
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="template_selections must be valid JSON") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return SubmissionCreateResponse(submission=SubmissionSummary.model_validate(submission, from_attributes=True))


@submission_router.delete("/{submission_id}", status_code=204)
def delete_submission_snapshot(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> None:
    try:
        AgentSubmissionService(db).delete(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@submission_router.post("/batch-delete", response_model=BulkDeleteResult)
def delete_submission_snapshots_batch(
    payload: BulkDeletePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> BulkDeleteResult:
    try:
        result = AgentSubmissionService(db).delete_many(payload.ids, current_user.id)
        return BulkDeleteResult(**result)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@submission_router.get("/{submission_id}/archive")
def download_submission_snapshot_archive(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> FileResponse:
    try:
        submission = AgentSubmissionService(db).get(submission_id, current_user.id, allow_team_entry=True)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    archive_path = Path(submission.archive_path)
    if not archive_path.is_file():
        raise HTTPException(status_code=404, detail="Submission archive is not available")
    return FileResponse(archive_path, media_type="application/zip", filename=submission.original_filename or "agent.zip")


@run_router.post("", response_model=SubmissionRerunResponse)
def create_run(
    submission_id: str = Form(...),
    requirement_id: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionRerunResponse:
    try:
        run = AgentSubmissionService(db).create_run(submission_id, current_user.id, requirement_id=requirement_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return SubmissionRerunResponse(run=RunService(db).to_summary(run))


@run_router.get("", response_model=list[RunSummary])
def list_runs(
    requirement_id: str | None = None,
    submission_id: str | None = None,
    active_only: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> list[RunSummary]:
    runs = RunService(db).list_submissions(
        current_user.id,
        requirement_id=requirement_id,
        submission_id=submission_id,
        active_only=active_only,
    )
    return runs


@run_router.post("/{submission_id}/start", response_model=SubmissionDetail)
def start_submission(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionDetail:
    service = RunService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _require_official_run_leader(db, current_user, submission)
    if submission.status != SubmissionStatus.PENDING.value:
        raise HTTPException(status_code=409, detail="Submission is already running or completed")
    if submission.agent_source == AgentSourceType.DEMO_REPLAY.value:
        try:
            with hold_distributed_lock(f"submission:{submission.id}"):
                db.refresh(submission)
                if submission.status != SubmissionStatus.PENDING.value:
                    raise ValueError("Submission is already running or completed")
                enforce_official_competition_task_limit(db, submission, exclude_run_id=submission.id)
                service.update_status(submission, SubmissionStatus.RUNNING)
                service.update_steps(
                    submission,
                    service.build_step_states(active_key="deploy_agent", description="Preparing demo session"),
                )
                service.append_step_event(
                    submission_id,
                    step_key="deploy_agent",
                    message="Demo session accepted for host execution",
                    status="info",
                )
                HostDemoExecutionService.start(submission_id)
        except CompetitionRunAlreadyActive as exc:
            db.rollback()
            service.reject_pending_run(submission, str(exc))
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return service.to_detail(service.get_submission(submission_id, current_user.id))
    try:
        queue_run(db, submission_id)
        db.commit()
    except CompetitionRunAlreadyActive as exc:
        db.rollback()
        service.reject_pending_run(submission, str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GlobalRunConcurrencyLimitExceeded as exc:
        db.rollback()
        service.reject_pending_run(submission, str(exc))
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "30"}) from exc
    except (LookupError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    PreviewService(db).stop(submission.id)
    db.commit()
    service.append_step_event(submission_id, step_key="deploy_agent", message="Submission accepted and queued", status="info")
    return service.to_detail(service.get_submission(submission_id, current_user.id))


@run_router.post("/{submission_id}/rerun", response_model=SubmissionRerunResponse)
def rerun_submission(
    submission_id: str,
    requirement_id: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionRerunResponse:
    try:
        source_run = RunService(db).get_submission(submission_id, current_user.id)
        submission = AgentSubmissionService(db).create_run(
            source_run.submission_id,
            current_user.id,
            requirement_id=requirement_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        if submission.agent_source == AgentSourceType.DEMO_REPLAY.value:
            service = RunService(db)
            enforce_official_competition_task_limit(db, submission, exclude_run_id=submission.id)
            service.update_status(submission, SubmissionStatus.RUNNING)
            service.update_steps(
                submission,
                service.build_step_states(active_key="deploy_agent", description="Preparing demo session"),
            )
            HostDemoExecutionService.start(submission.id)
        else:
            queue_run(db, submission.id)
            db.commit()
    except CompetitionRunAlreadyActive as exc:
        db.rollback()
        RunService(db).reject_pending_run(submission, str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GlobalRunConcurrencyLimitExceeded as exc:
        db.rollback()
        RunService(db).reject_pending_run(submission, str(exc))
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "30"}) from exc
    except (LookupError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SubmissionRerunResponse(run=RunService(db).to_summary(submission))


@router.delete("/{submission_id}", status_code=204)
def delete_submission(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> None:
    service = SubmissionService(db)
    try:
        service.delete_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not remove the run directory: {exc}") from exc


@router.post("/batch-delete", response_model=BulkDeleteResult)
def delete_submissions_batch(
    payload: BulkDeletePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> BulkDeleteResult:
    try:
        return BulkDeleteResult(**SubmissionService(db).delete_submissions(payload.ids, current_user.id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/{submission_id}/archive")
def download_submission_archive(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> FileResponse:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    archive_path = Path(submission.agent_archive_path)
    if not archive_path.is_file():
        raise HTTPException(status_code=404, detail="The uploaded agent archive is no longer available")
    return FileResponse(archive_path, media_type="application/zip", filename=submission.original_filename or "agent.zip")


@router.post("/{submission_id}/pause", response_model=SubmissionDetail)
def pause_submission(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionDetail:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _require_official_run_leader(db, current_user, submission)
    if not service.can_pause(submission):
        raise HTTPException(status_code=409, detail="Submission is not running")
    try:
        service.request_pause(submission)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return service.to_detail(service.get_submission(submission_id, current_user.id))


@router.post("/{submission_id}/cancel", response_model=SubmissionDetail)
def cancel_submission(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionDetail:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Any member of the owning official team can release a stuck run.  Other
    # lifecycle mutations (start, pause, resume, editable task changes) retain
    # the leader-only policy.
    _require_official_run_member(db, current_user, submission)
    try:
        service.cancel_submission(submission)
        if submission.agent_source == AgentSourceType.DEMO_REPLAY.value:
            HostDemoExecutionService.stop(submission_id)
        # Docker runs execute on a separate executor host. The executor polls
        # the committed CANCELLED state and force-removes its local container.
        if submission.celery_task_id:
            celery_app.control.revoke(submission.celery_task_id, terminate=False)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return service.to_detail(service.get_submission(submission_id, current_user.id))


@router.post("/{submission_id}/resume", response_model=SubmissionDetail)
def resume_submission(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionDetail:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _require_official_run_leader(db, current_user, submission)
    if not service.can_resume(submission):
        raise HTTPException(status_code=409, detail="Submission is not paused")
    try:
        with hold_distributed_lock(f"submission:{submission.id}"):
            db.refresh(submission)
            if not service.can_resume(submission):
                raise ValueError("Submission is not paused")
            service.clear_runtime_request_files(submission)
            if submission.agent_source == AgentSourceType.DEMO_REPLAY.value:
                enforce_official_competition_task_limit(db, submission, exclude_run_id=submission.id)
                checkpoint = service.read_checkpoint(submission)
                checkpoint["paused"] = False
                service.write_checkpoint(submission, checkpoint)
                service.update_status(submission, SubmissionStatus.RUNNING)
                service.update_steps(
                    submission,
                    service.build_step_states(
                        active_key="start_agent",
                        completed={"deploy_agent"},
                        description="Resuming demo from checkpoint",
                    ),
                )
                HostDemoExecutionService.start(submission_id, reuse_workspace=True)
            else:
                resume_prep = service.prepare_resume_from_pause(submission)
                if bool(resume_prep.get("committed")):
                    SubmissionEventStream.publish(
                        submission_id,
                        reason="manual_edit_committed",
                        commit_history=True,
                        preview=True,
                    )
                submission.steps_json = json.dumps([
                    step.model_dump()
                    for step in service.build_step_states(
                        active_key="deploy_agent",
                        description="Restarting runner from checkpoint",
                    )
                ])
                db.add(submission)
                PreviewService(db).stop(submission.id)
                queue_run(db, submission_id, reuse_workspace=True)
                db.commit()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CompetitionRunAlreadyActive as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GlobalRunConcurrencyLimitExceeded as exc:
        db.rollback()
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "30"}) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    SubmissionEventStream.publish(submission_id, reason="resume_queued", submission=True, preview=True)
    return service.to_detail(submission)


@router.post("/{submission_id}/continue", response_model=SubmissionDetail)
def continue_submission(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionDetail:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _require_official_run_leader(db, current_user, submission)
    try:
        with hold_distributed_lock(f"submission:{submission.id}"):
            db.refresh(submission)
            next_demo_max: int | None = None
            if submission.agent_source == AgentSourceType.DEMO_REPLAY.value:
                enforce_official_competition_task_limit(db, submission, exclude_run_id=submission.id)
                checkpoint = service.read_checkpoint(submission)
                current_index = int(checkpoint.get("last_completed_index", 0) or 0)
                next_demo_max = {31: 32, 32: 33, 34: 35}.get(current_index)
                if next_demo_max is None:
                    raise ValueError("No next incremental teaching snapshot is prepared at the current pointer")
            service.request_continue(submission)
            if submission.agent_source == AgentSourceType.DEMO_REPLAY.value:
                # Requirement edits prepare snapshots 31 and 34. Continue then
                # advances one immutable teaching snapshot at a time.
                checkpoint = service.read_checkpoint(submission)
                checkpoint["replay_max_index"] = next_demo_max
                checkpoint["paused"] = False
                service.write_checkpoint(submission, checkpoint)
                HostDemoExecutionService.start(submission_id, reuse_workspace=True)
            else:
                PreviewService(db).stop(submission.id)
                queue_run(db, submission_id, reuse_workspace=True)
                db.commit()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CompetitionRunAlreadyActive as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GlobalRunConcurrencyLimitExceeded as exc:
        db.rollback()
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "30"}) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return service.to_detail(service.get_submission(submission_id, current_user.id))


@router.post("/{submission_id}/rewind", response_model=SubmissionDetail)
def rewind_submission(
    submission_id: str,
    payload: SubmissionRewindPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionDetail:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _require_official_run_leader(db, current_user, submission)
    if not service.can_rewind(submission):
        raise HTTPException(status_code=409, detail="Submission must be paused or completed before rewinding")
    try:
        service.rewind_to_commit(submission, payload.commit_oid)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return service.to_detail(submission)


@router.post("/{submission_id}/tdd-rerun", response_model=SubmissionDetail)
def rerun_demo_tdd(
    submission_id: str,
    payload: SubmissionTddRerunPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionDetail:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
        _require_official_run_leader(db, current_user, submission)
        if submission.agent_source != AgentSourceType.DEMO_REPLAY.value:
            raise ValueError("TDD rerun is only available for demo replay submissions")
        repair_commits = {
            "REQ-2.1::Integration::003::REQ-2.1-SEARCH-RESULTS-PAGE": ("d25f7e8b2a1c4d6e9f807162534a9bcdef012345", 25),
            "REQ-3.2::Unit::001::REQ-3.2-UNIT-BOOKING-VALIDATION": ("f26a9c7d5e3b1f2049687a6c5d4e3f2a1b0c9876", 26),
        }
        current_checkpoint = service.read_checkpoint(submission)
        if (
            payload.test_id == "REQ-3.2::Unit::001::REQ-3.2-UNIT-BOOKING-VALIDATION"
            and int(current_checkpoint.get("last_completed_index", 0) or 0) >= 29
        ):
            repair_commits[payload.test_id] = ("d30f7e8b2a1c4d6e9f807162534a9bcdef012350", 30)
        teaching_pointer_actions = {
            "__DEMO_ADD_REQ-2.1-UNIT-REJECT-SAME-CITY__": 27,
            "REQ-2.1::Unit::002::REQ-2.1-UNIT-REJECT-SAME-CITY": 28,
            "__DEMO_EDIT_REQ-3.2-UNIT-BOOKING-VALIDATION__": 29,
            "REQ-3.2::Unit::001::REQ-3.2-UNIT-BOOKING-VALIDATION:CORRECTED": 30,
            "__DEMO_EDIT_REQUIREMENT_REQ-2.2__": 31,
            "__DEMO_ADD_REQUIREMENT_REQ-3.4__": 34,
        }
        teaching_target = teaching_pointer_actions.get(payload.test_id)
        if teaching_target is not None:
            checkpoint = service.read_checkpoint(submission)
            checkpoint.update({
                # The replay worker advances from the completed cursor to the
                # requested snapshot. Keeping the cursor one step behind is
                # what makes 30 -> 31 (and the other teaching actions) an
                # actual transition instead of a no-op.
                "last_completed_index": teaching_target - 1,
                "paused": False,
                "replay_max_index": teaching_target,
            })
            service.write_checkpoint(submission, checkpoint)
            service.update_status(submission, SubmissionStatus.RUNNING)
            service.update_steps(
                submission,
                service.build_step_states(
                    active_key="start_agent",
                    completed={"deploy_agent"},
                    description=f"Replaying teaching snapshot {teaching_target}",
                ),
            )
            HostDemoExecutionService.start(submission_id, reuse_workspace=True)
            return service.to_detail(service.get_submission(submission_id, current_user.id))
        target = repair_commits.get(payload.test_id)
        if target is None:
            raise ValueError("No demo repair snapshot is registered for this test")
        commit_oid, target_index = target
        service.rewind_to_commit(submission, commit_oid)
        checkpoint = service.read_checkpoint(submission)
        checkpoint.update({"last_completed_index": target_index - 1, "paused": False, "replay_max_index": target_index})
        service.write_checkpoint(submission, checkpoint)
        service.update_status(submission, SubmissionStatus.RUNNING)
        # TDD reruns reuse the already assembled demo workspace; do not show
        # the environment-preparation step while the replay is being queued.
        service.update_steps(
            submission,
            service.build_step_states(
                active_key="start_agent",
                completed={"deploy_agent"},
                description=f"Re-running TDD for {payload.test_id}",
            ),
        )
        HostDemoExecutionService.start(submission_id, reuse_workspace=True)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GlobalRunConcurrencyLimitExceeded as exc:
        db.rollback()
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "30"}) from exc
    return service.to_detail(service.get_submission(submission_id, current_user.id))


@router.get("/{submission_id}", response_model=SubmissionDetail)
def get_submission(
    submission_id: str,
    include_step_logs: bool = Query(default=True),
    include_tests: bool = Query(default=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionDetail:
    service = SubmissionService(db)
    try:
        return service.to_detail(
            service.get_submission(submission_id, current_user.id),
            include_step_logs=include_step_logs,
            include_tests=include_tests,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{submission_id}/logs", response_model=SubmissionLogs)
def get_submission_logs(
    submission_id: str,
    log_offset: int | None = Query(default=None, ge=0),
    after_event_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionLogs:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Competition evaluators must not reveal benchmark test execution details.
    # User/agent stdout remains available; Playwright output is never written
    # to that stream by the runner.
    is_competition_run = str(submission.catalog or "").strip().lower() == "competition"
    events = "" if is_competition_run else "\n".join(service.read_event_lines(submission))
    console = ""
    stdout = ""
    next_log_offset = 0
    stdout_log_path = None
    if submission.user_id:
        stdout_log_path = runtime_paths.resolve_existing_path(
            str(runtime_paths.get_shared_log_path(
                submission.user_id,
                submission.id,
                submission.active_generation,
                "stdout",
            ))
        )
    if (stdout_log_path is None or stdout_log_path.stat().st_size == 0) and submission.workspace_path:
        workspace_path = runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is not None:
            candidate = workspace_path / "template" / ".arc" / "stdout.log"
            if candidate.is_file():
                stdout_log_path = candidate
    if stdout_log_path:
        with stdout_log_path.open("rb") as stdout_file:
            requested_offset = log_offset or 0
            if requested_offset > stdout_log_path.stat().st_size:
                requested_offset = 0
            stdout_file.seek(requested_offset)
            stdout = stdout_file.read().decode("utf-8", errors="replace")
            next_log_offset = stdout_file.tell()
    debug_log_path = None
    if submission.user_id:
        debug_log_path = runtime_paths.resolve_existing_path(
            str(runtime_paths.get_shared_log_path(
                submission.user_id,
                submission.id,
                submission.active_generation,
                "execution.debug",
            ))
        )
    if debug_log_path is None and submission.workspace_path:
        workspace_path = runtime_paths.resolve_existing_path(submission.workspace_path)
        if workspace_path is not None:
            debug_log_path = runtime_paths.resolve_existing_path(str(workspace_path / "execution.debug.log"))
    if debug_log_path:
        with debug_log_path.open("rb") as debug_file:
            # Keep detailed execution diagnostics server-side for competition
            # runs. They are intentionally not returned to the user.
            if not is_competition_run:
                console = debug_file.read().decode("utf-8", errors="replace")
    elif log_offset is not None:
        next_log_offset = log_offset
    visual_events = [] if is_competition_run else service.read_visual_events(submission)
    runner_events = [] if is_competition_run else service.read_runner_events(submission)
    if after_event_id:
        matching_index = next((index for index, event in enumerate(runner_events) if event.event_id == after_event_id), None)
        if matching_index is not None:
            runner_events = runner_events[matching_index + 1:]
    runner_event_lines = [] if is_competition_run else [event.summary for event in runner_events]
    return SubmissionLogs(
        events=events,
        stdout=stdout,
        stderr="",
        console=console,
        log_offset=next_log_offset,
        last_event_id=runner_events[-1].event_id if runner_events else after_event_id,
        visual_events=visual_events,
        runner_events=runner_events,
        runner_event_lines=runner_event_lines,
    )


@router.get("/{submission_id}/events")
def stream_submission_events(
    submission_id: str,
    request: Request,
    since_version: int = 0,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> StreamingResponse:
    service = SubmissionService(db)
    try:
        service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        if last_event_id and last_event_id.isdigit():
            since_version = max(since_version, int(last_event_id))
    except ValueError:
        pass

    async def event_generator():
        # Flush headers immediately. Redis must not block the API event loop.
        yield ": connected\n\n"
        replay, cursor = await asyncio.to_thread(
            SubmissionEventStream.replay_snapshot, submission_id, since_version,
        )
        event_queue = SubmissionEventStream.subscribe(submission_id, start_id=cursor)
        try:
            last_version = since_version
            if replay is not None:
                yield SubmissionEventStream.encode_sse(replay)
                last_version = replay.version
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.to_thread(event_queue.queue.get, True, 15)
                except Empty:
                    yield ": heartbeat\n\n"
                    continue
                if event is None:
                    break
                if event.version <= last_version:
                    continue
                yield SubmissionEventStream.encode_sse(event)
                last_version = event.version
        finally:
            SubmissionEventStream.unsubscribe(submission_id, event_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{submission_id}/editable-task", response_model=SubmissionEditableTaskPayload)
def get_submission_editable_task(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionEditableTaskPayload:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload = service.read_submission_task_documents(submission)
    return SubmissionEditableTaskPayload(**{
        "requirements_md": payload["requirements_md"],
        "requirements_yaml": payload["requirements_yaml"],
        "prerequisites_md": payload["prerequisites_md"],
    })


@router.post("/{submission_id}/editable-task")
def update_submission_editable_task(
    submission_id: str,
    payload: SubmissionEditableTaskPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> dict[str, str]:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _require_official_run_leader(db, current_user, submission)
    try:
        # Demo teaching uses immutable requirement snapshots. Saving one of
        # the two prepared incremental edits advances this user's replay
        # cursor instead of mutating the shared snapshot store.
        if submission.agent_source == AgentSourceType.DEMO_REPLAY.value and payload.edited_node_id:
            teaching_targets = {"REQ-2.2": 31, "REQ-3.4": 34}
            target_index = teaching_targets.get(payload.edited_node_id.strip())
            if target_index is None:
                raise ValueError("No incremental teaching scenario is prepared for this requirement")
            checkpoint = service.read_checkpoint(submission)
            checkpoint.update({
                "last_completed_index": target_index - 1,
                "replay_max_index": target_index,
                "paused": False,
                "runtime_state_restored": False,
            })
            service.write_checkpoint(submission, checkpoint)
            service.update_status(submission, SubmissionStatus.RUNNING)
            service.update_steps(
                submission,
                service.build_step_states(
                    active_key="start_agent",
                    completed={"deploy_agent"},
                    description=f"Replaying incremental teaching snapshot {target_index}",
                ),
            )
            HostDemoExecutionService.start(submission_id, reuse_workspace=True)
            return {"detail": f"Replaying incremental teaching snapshot {target_index}"}
        service.write_submission_task_documents(
            submission,
            requirements_md=payload.requirements_md,
            requirements_yaml=payload.requirements_yaml,
            prerequisites_md=payload.prerequisites_md,
        )
        service.write_submission_traceability_store(submission, payload.requirements_yaml)
        if payload.edited_node_id and payload.edited_node_id.strip():
            service.reset_progress_for_edited_node(submission, payload.edited_node_id)
        SubmissionEventStream.publish(
            submission.id,
            reason="task_updated",
            submission=True,
            commit_history=True,
            traceability_selected=True,
            traceability_all=True,
        )
        project_path = runtime_paths.resolve_existing_path(submission.workspace_path)
        PreviewService(db).mark_stale(submission.id, project_path / "template" if project_path else None)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"detail": "Submission workspace updated"}


@router.get("/{submission_id}/requirements-assets/{asset_kind}/{asset_path:path}")
def get_submission_requirements_asset(
    submission_id: str,
    asset_kind: str,
    asset_path: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
):
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
        try:
            return FileResponse(service.get_submission_task_asset_path(submission, asset_kind, asset_path))
        except FileNotFoundError:
            normalized_kind = "reference" if asset_kind in {"reference", "references"} else asset_kind
            if normalized_kind not in {"assets", "reference"}:
                raise FileNotFoundError(f"Unknown submission task asset kind: {asset_kind}")
            content = artifact_service.read_snapshot_bytes(submission, f"requirements/{normalized_kind}/{asset_path}")
            if content is None:
                raise
            media_type = mimetypes.guess_type(asset_path)[0] or "application/octet-stream"
            return StreamingResponse(iter([content]), media_type=media_type)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{submission_id}/traceability", response_model=SubmissionTraceabilityPayload)
def get_submission_traceability(
    submission_id: str,
    node_id: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionTraceabilityPayload:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Traceability normally resolves test rows to workspace/tests and includes
    # their complete source. Do not even read those files for an official run.
    payload = artifact_service.read_traceability(
        submission,
        node_id=node_id,
        include_tests=not service.tests_are_private(submission),
    )
    return SubmissionTraceabilityPayload(**payload)


@router.get("/{submission_id}/commit-history", response_model=SubmissionCommitHistoryPayload)
def get_submission_commit_history(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionCommitHistoryPayload:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        payload = artifact_service.read_commit_history(submission)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        # Snapshot files are swapped atomically during host demo replay. A
        # request landing in the tiny transition window should not turn the
        # whole panel into an HTTP 500; the next SSE refresh will load it.
        if submission.agent_source == AgentSourceType.DEMO_REPLAY.value:
            logger.warning("Demo commit history temporarily unavailable for %s: %s", submission_id, exc)
            payload = {"availability": "available", "commits": []}
        else:
            raise
    return SubmissionCommitHistoryPayload(**payload)


@router.get("/{submission_id}/source", response_model=SubmissionSourcePayload)
def get_submission_source(
    submission_id: str,
    file_path: str = "",
    first_line: str | None = None,
    kind: str = "file",
    commit_oid: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionSourcePayload:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if service.tests_are_private(submission):
        normalized_path = PurePosixPath(file_path.replace("\\", "/"))
        private_parts = {"tests", ".arc", "playwright-report", "test-results"}
        private_files = {"playwright-report.json", "runner-events.jsonl"}
        if (
            any(part.lower() in private_parts for part in normalized_path.parts)
            or normalized_path.name.lower() in private_files
        ):
            # read_source normally permits tests/... beside template/ for demo
            # replay. That capability must never expose an official evaluator.
            raise HTTPException(status_code=404, detail="Not found")

    try:
        payload = artifact_service.read_source(
            submission,
            file_path=file_path,
            first_line=first_line,
            kind=kind,
            commit_oid=commit_oid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SubmissionSourcePayload(**payload)


@router.get("/{submission_id}/preview/status", response_model=SubmissionPreviewStatus)
def get_submission_preview_status(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionPreviewStatus:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if submission.agent_source == AgentSourceType.DEMO_REPLAY.value:
        checkpoint = service.read_checkpoint(submission)
        step_index = int(checkpoint.get("last_completed_index", 1) or 1)
        return SubmissionPreviewStatus(**HostDemoPreviewService.demo_status(step_index))
    preview_service = PreviewService(db)
    latest = preview_service.latest(submission.id)
    if latest:
        preview_service.touch(latest)
        db.commit()
    return SubmissionPreviewStatus(**preview_service.status_payload(submission.id))


@router.post("/{submission_id}/preview/refresh", response_model=SubmissionPreviewStatus)
def refresh_submission_preview(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionPreviewStatus:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if submission.agent_source == AgentSourceType.DEMO_REPLAY.value:
        checkpoint = service.read_checkpoint(submission)
        step_index = int(checkpoint.get("last_completed_index", 1) or 1)
        return SubmissionPreviewStatus(**HostDemoPreviewService.demo_status(step_index))
    try:
        preview_service = PreviewService(db)
        preview_service.request(submission)
        db.commit()
        return SubmissionPreviewStatus(**preview_service.status_payload(submission.id))
    except (FileNotFoundError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{submission_id}/preview/stop", response_model=SubmissionPreviewStatus)
def stop_submission_preview(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionPreviewStatus:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if submission.agent_source == AgentSourceType.DEMO_REPLAY.value:
        checkpoint = service.read_checkpoint(submission)
        step_index = int(checkpoint.get("last_completed_index", 1) or 1)
        return SubmissionPreviewStatus(**HostDemoPreviewService.demo_status(step_index))
    preview_service = PreviewService(db)
    preview_service.stop(submission_id)
    db.commit()
    return SubmissionPreviewStatus(**preview_service.status_payload(submission_id))

@router.get("/{submission_id}/preview")
@router.get("/{submission_id}/preview/{file_path:path}")
def get_submission_preview_file(
    submission_id: str,
    file_path: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
):
    try:
        SubmissionService(db).get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    raise HTTPException(
        status_code=410,
        detail="Submission-scoped preview proxy is disabled. Use preview_url from the preview status response.",
    )


@router.get("/{submission_id}/workspace/files", response_model=WorkspaceFileListPayload)
def get_workspace_files(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> WorkspaceFileListPayload:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
        files = service.list_workspace_files(submission)
        return WorkspaceFileListPayload(files=files)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{submission_id}/workspace/template-bundle")
def download_workspace_template_bundle(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> FileResponse:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
        bundle_path = service.create_project_bundle(submission)
        return FileResponse(
            bundle_path,
            media_type="application/zip",
            filename=f"{submission_id}-template.zip",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{submission_id}/manual-edit/commit-preview", response_model=SubmissionManualEditCommitPreview)
def get_manual_edit_commit_preview(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> SubmissionManualEditCommitPreview:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _require_official_run_leader(db, current_user, submission)
    if not service.can_manual_edit(submission):
        raise HTTPException(status_code=409, detail="Submission is not in paused manual edit mode")
    try:
        return SubmissionManualEditCommitPreview(**service.build_manual_edit_commit_preview(submission))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{submission_id}/workspace/files")
def update_workspace_file(
    submission_id: str,
    payload: FileUpdatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> dict[str, str]:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
        _require_official_run_leader(db, current_user, submission)
        service.update_workspace_file(submission, payload.path, payload.content)
        SubmissionEventStream.publish(
            submission_id,
            reason="file_updated",
            preview=True,
        )
        return {"detail": "File updated successfully"}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{submission_id}/workspace/tests")
def create_test(
    submission_id: str,
    payload: TestCreatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> dict[str, str]:
    service = SubmissionService(db)
    try:
        submission = service.get_submission(submission_id, current_user.id)
        _require_official_run_leader(db, current_user, submission)
        file_path = service.create_test_file(
            submission,
            payload.test_id,
            payload.req_id,
            payload.test_type,
            payload.scenario_id,
            payload.file_path,
        )
        SubmissionEventStream.publish(
            submission_id,
            reason="test_created",
            traceability_selected=True,
            traceability_all=True,
            preview=True,
        )
        return {"detail": "Test created successfully", "file_path": file_path}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
